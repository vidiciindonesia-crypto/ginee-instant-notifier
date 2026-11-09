import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import app


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
        self.call = Mock(return_value=0)
        self.library = Mock(mciSendStringW=self.call)

    def tearDown(self):
        self.temp.cleanup()

    def run_voice(self, text):
        with patch.object(app, "DATA", self.directory), patch.object(app.sys, "platform", "win32"), patch.object(app.ctypes, "WinDLL", return_value=self.library, create=True):
            app.speak(text)

    def test_priority_uses_female_recording_and_closes_player(self):
        self.run_voice("Ada pesanan instan prioritas baru.")
        commands = [args.args[0] for args in self.call.call_args_list]
        self.assertIn("prioritas.mp3", commands[0])
        self.assertIn(" wait", commands[1])
        self.assertTrue(commands[2].startswith("close "))

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


if __name__ == "__main__":
    unittest.main()
