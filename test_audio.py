import tempfile
import threading
import queue
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import app
from core import Store, UTC, DEFAULT_INSTANT, DEFAULT_PRIORITY
from datetime import datetime


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.assets_patch = patch.object(app, "ASSETS", self.directory / "no-bundled-assets")
        self.assets_patch.start()
        self.addCleanup(self.assets_patch.stop)
        (self.directory / "audio").mkdir()
        for filename in ("instan.mp3", "prioritas.mp3"):
            (self.directory / "audio" / filename).write_bytes(b"dummy-mp3-for-routing-test")
        def mci(command, buffer, size, callback):
            if command.startswith("status "):
                buffer.value = "stopped"
            return 0
        self.call = Mock(side_effect=mci)
        self.library = Mock(mciSendStringW=self.call)

    def tearDown(self):
        self.temp.cleanup()

    def run_voice(self, text, cancelled=None):
        with patch.object(app, "DATA", self.directory), patch.object(app.sys, "platform", "win32"), patch.object(app.ctypes, "WinDLL", return_value=self.library, create=True):
            return app.speak(text, cancelled=cancelled)

    def test_priority_uses_female_recording_and_closes_player(self):
        self.run_voice("Ada pesanan instan prioritas baru.")
        commands = [args.args[0] for args in self.call.call_args_list]
        self.assertIn("prioritas.mp3", commands[0])
        self.assertTrue(commands[1].startswith("play "))
        self.assertTrue(commands[-1].startswith("close "))

    def test_regular_instant_uses_regular_clip(self):
        self.run_voice("Ada pesanan instan baru.")
        self.assertIn("instan.mp3", self.call.call_args_list[0].args[0])
        self.assertNotIn("prioritas.mp3", self.call.call_args_list[0].args[0])

    def test_play_failure_still_closes_player(self):
        self.call.side_effect = [0, 42, 0]
        with self.assertRaises(RuntimeError):
            self.run_voice("Pesanan prioritas")
        self.assertTrue(self.call.call_args_list[-1].args[0].startswith("close "))

    def test_missing_clip_does_not_fall_back_to_robotic_speech(self):
        (self.directory / "audio" / "prioritas.mp3").unlink()
        with self.assertRaises(RuntimeError):
            self.run_voice("Pesanan prioritas")
        self.call.assert_not_called()

    def test_packaged_voice_works_without_per_user_audio(self):
        bundled = self.directory / "bundled"
        (bundled / "audio").mkdir(parents=True)
        clip = bundled / "audio" / "prioritas.mp3"
        clip.write_bytes(b"dummy-bundled-audio")
        (self.directory / "audio" / "prioritas.mp3").unlink()
        with patch.object(app, "ASSETS", bundled):
            self.run_voice("Ada pesanan instan prioritas baru.")
        self.assertIn(str(clip), self.call.call_args_list[0].args[0])

    def test_cancelled_before_playback_does_not_open_audio(self):
        self.assertFalse(self.run_voice("Pesanan prioritas", cancelled=lambda: True))
        self.call.assert_not_called()

    def test_status_change_during_playback_stops_and_closes_audio(self):
        def playing(command, buffer, size, callback):
            if command.startswith("status "):
                buffer.value = "playing"
            return 0
        self.call.side_effect = playing
        cancelled = Mock(side_effect=[False, False, True])
        with patch.object(app.time, "sleep"):
            self.assertFalse(self.run_voice("Pesanan instan", cancelled=cancelled))
        commands = [call.args[0] for call in self.call.call_args_list]
        self.assertTrue(any(command.startswith("stop ") for command in commands))
        self.assertTrue(commands[-1].startswith("close "))

    def test_shipping_order_is_skipped_when_its_queued_audio_is_reached(self):
        store = Store(self.directory / "history.sqlite")
        sample = {"orderId": "QUEUED", "orderStatus": "PAID", "channelId": "TIKTOK_ID",
            "logisticsInfos": [{"logisticsProviderName": "Blitz-ID-Instant"}]}
        try:
            store.ingest([sample], datetime.now(UTC), DEFAULT_INSTANT, DEFAULT_PRIORITY)
            store.ingest([{**sample, "orderStatus": "SHIPPING"}], datetime.now(UTC), DEFAULT_INSTANT, DEFAULT_PRIORITY)
            view = app.App.__new__(app.App)
            view.stop_event = threading.Event()
            with patch.object(app, "DATA", self.directory), patch.object(app, "speak") as voice:
                self.assertFalse(view.play_audio("Pesanan instan", "QUEUED"))
                voice.assert_not_called()
        finally:
            store.db.close()

    def test_stale_reminder_snapshot_cannot_enqueue_after_shipping(self):
        store = Store(self.directory / "history.sqlite")
        sample = {"orderId": "REMINDER", "orderStatus": "PAID",
            "logisticsInfos": [{"logisticsProviderName": "SPX Instant Prioritas"}]}
        try:
            store.ingest([sample], datetime.now(UTC), DEFAULT_INSTANT, DEFAULT_PRIORITY)
            stale_row = store.rows()[0]
            store.ingest([{**sample, "orderStatus": "SHIPPING"}], datetime.now(UTC), DEFAULT_INSTANT, DEFAULT_PRIORITY)
            view = app.App.__new__(app.App)
            view.store = store
            view.audio = queue.Queue()
            self.assertFalse(view.alert(stale_row, reminder=True))
            self.assertTrue(view.audio.empty())
        finally:
            store.db.close()

    def test_playback_guard_observes_shipping_update_from_another_connection(self):
        store = Store(self.directory / "history.sqlite")
        sample = {"orderId": "PLAYING", "orderStatus": "PAID",
            "logisticsInfos": [{"logisticsProviderName": "SPX Instant Prioritas"}]}
        try:
            store.ingest([sample], datetime.now(UTC), DEFAULT_INSTANT, DEFAULT_PRIORITY)
            def voice(text, cancelled):
                self.assertFalse(cancelled())
                store.ingest([{**sample, "orderStatus": "SHIPPING"}], datetime.now(UTC), DEFAULT_INSTANT, DEFAULT_PRIORITY)
                self.assertTrue(cancelled())
                return False
            view = app.App.__new__(app.App)
            view.stop_event = threading.Event()
            with patch.object(app, "DATA", self.directory), patch.object(app, "speak", side_effect=voice):
                self.assertFalse(view.play_audio("Pesanan prioritas", "PLAYING"))
        finally:
            store.db.close()

    def test_ready_to_ship_skips_queued_audio_and_cancels_current_voice(self):
        store = Store(self.directory / "history.sqlite")
        sample = {"orderId": "READY", "orderStatus": "PAID",
            "logisticsInfos": [{"logisticsProviderName": "Blitz-ID-Instant"}]}
        view = app.App.__new__(app.App)
        view.stop_event = threading.Event()
        try:
            store.ingest([sample], datetime.now(UTC), DEFAULT_INSTANT, DEFAULT_PRIORITY)
            def voice(text, cancelled):
                self.assertFalse(cancelled())
                store.ingest([{**sample, "orderStatus": "READY_TO_SHIP"}], datetime.now(UTC), DEFAULT_INSTANT, DEFAULT_PRIORITY)
                self.assertTrue(cancelled())
                return False
            with patch.object(app, "DATA", self.directory), patch.object(app, "speak", side_effect=voice):
                self.assertFalse(view.play_audio("Pesanan instan", "READY"))
            with patch.object(app, "DATA", self.directory), patch.object(app, "speak") as voice:
                self.assertFalse(view.play_audio("Pesanan instan", "READY"))
                voice.assert_not_called()
        finally:
            store.db.close()


if __name__ == "__main__":
    unittest.main()
