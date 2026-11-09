import base64
import hashlib
import hmac
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from core import ApiError, Client, Store, UTC, category, parse_time, stamp, DEFAULT_INSTANT, DEFAULT_PRIORITY, list_records


NOW = datetime(2026, 11, 9, 5, 0, tzinfo=UTC)


def order(identifier="O1", provider="Instant Prioritas", status="PAID"):
    return {"orderId": identifier, "externalOrderSn": "ABC123", "shopId": "S1", "channelId": "SHOPEE_ID",
        "orderStatus": status, "externalCreateAt": stamp(NOW - timedelta(minutes=5)),
        "payAt": stamp(NOW - timedelta(minutes=5)),
        "logisticsInfos": [{"logisticsProviderName": provider, "shippingType": ""}]}


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "test.sqlite"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.db.close()
        self.directory.cleanup()

    def ingest(self, orders):
        self.store.ingest(orders, NOW, "instant, instan", "prioritas")

    def test_dedupe_persists_across_restart(self):
        self.ingest([order()])
        self.store.mark_delivered("O1")
        self.store.db.close()
        self.store = Store(self.path)
        self.ingest([order()])
        self.assertEqual(len(self.store.rows()), 1)
        self.assertEqual(self.store.pending(), [])

    def test_priority_uses_payment_time(self):
        self.ingest([order()])
        deadline = parse_time(self.store.rows()[0]["deadline"])
        self.assertEqual(deadline, NOW + timedelta(minutes=10))

    def test_pending_payment_and_regular_excluded(self):
        self.ingest([order(status="PENDING_PAYMENT"), order("O2", "Grab Same Day")])
        self.assertEqual(self.store.rows(), [])

    def test_cancelled_stops_reminder(self):
        self.ingest([order()])
        self.ingest([order(status="CANCELLED")])
        self.assertTrue(self.store.rows()[0]["acknowledged"])

    def test_priority_upgrade_announced_again(self):
        self.ingest([order(provider="Instant")])
        self.store.mark_delivered("O1")
        self.ingest([order()])
        self.assertEqual(self.store.pending()[0]["kind"], "PRIORITAS")

    def test_transaction_rolls_back_on_invalid_order(self):
        with self.assertRaises(KeyError):
            self.ingest([order(), {}])
        self.assertEqual(self.store.rows(), [])
        self.assertEqual(self.store.since(NOW), NOW - timedelta(minutes=15))

    def test_overlap_window(self):
        self.ingest([])
        self.assertEqual(self.store.since(NOW), NOW - timedelta(minutes=15))

    def test_late_paid_tiktok_update_is_rechecked_without_duplicate(self):
        self.ingest([])
        update_time = NOW - timedelta(minutes=5)
        self.assertLessEqual(self.store.since(NOW), update_time)
        data = order(provider="Blitz-ID-Instant", status="PAID")
        data.update(channelId="TIKTOK_ID", payAt=None, lastUpdateAt=stamp(update_time))
        self.ingest([data])
        self.assertEqual(self.store.pending()[0]["kind"], "INSTAN")
        self.store.mark_delivered(data["orderId"])
        self.ingest([data])
        self.assertEqual(self.store.pending(), [])

    def test_priority_needs_validated_pattern(self):
        self.assertEqual(category(order(), "instant", ""), "INSTAN")
        self.assertEqual(category(order(provider="Grab Same Day"), "instant", "prioritas"), "LAINNYA")

    def test_missing_time_does_not_invent_deadline(self):
        data = order()
        data.pop("payAt")
        self.ingest([data])
        self.assertEqual(self.store.rows()[0]["deadline"], "")

    def test_payment_packing_deadline_is_separate_from_ship_deadline(self):
        sample = order(provider="SPX Instant Prioritas")
        sample.update(externalCreateAt="2026-11-09T04:07:00Z",
            payAt="2026-11-09T04:11:10Z", promisedToShipBefore="2026-11-09T04:56:11Z")
        self.ingest([sample])
        self.assertEqual(self.store.rows()[0]["deadline"], "2026-11-09T04:26:10Z")

    def test_observed_standard_service_is_not_instant(self):
        data = order(provider="J&T Express", status="SHIPPING")
        data["logisticsInfos"][0]["shippingType"] = "Standard shipping"
        self.assertEqual(category(data, "instant, instan", "prioritas"), "LAINNYA")
        self.ingest([data])
        self.assertEqual(self.store.rows(), [])

    def test_confirmed_spx_priority_marker(self):
        sample = order(provider="SPX Instant Prioritas")
        sample["logisticsInfos"][0]["shippingType"] = None
        self.assertEqual(category(sample, DEFAULT_INSTANT, DEFAULT_PRIORITY), "PRIORITAS")
        sample["logisticsInfos"][0]["logisticsProviderName"] = "SPX Instant"
        self.assertEqual(category(sample, DEFAULT_INSTANT, DEFAULT_PRIORITY), "INSTAN")

    def test_confirmed_shipping_priority_is_not_new_packing_alert(self):
        self.ingest([order(provider="SPX Instant Prioritas", status="SHIPPING")])
        self.assertEqual(self.store.pending(), [])

    def test_tiktok_blitz_instant_announced_without_shopee_timer(self):
        sample = order(provider="Blitz-ID-Instant")
        sample["channelId"] = "TIKTOK_ID"
        self.ingest([sample])
        alert = self.store.pending()[0]
        self.assertEqual(alert["kind"], "INSTAN")
        self.assertEqual(alert["deadline"], "")

    def test_priority_service_name_alone_cannot_apply_shopee_timer(self):
        sample = order(provider="SPX Instant Prioritas")
        sample["channelId"] = "TIKTOK_ID"
        self.ingest([sample])
        self.assertEqual(self.store.pending()[0]["deadline"], "")

    def test_unknown_channel_does_not_inherit_shopee_timer(self):
        sample = order()
        sample.pop("channelId")
        self.ingest([sample])
        self.assertEqual(self.store.pending()[0]["deadline"], "")

    def test_instant_detection_is_independent_of_marketplace(self):
        for channel in ("SHOPEE_ID", "TIKTOK_ID", "TOKOPEDIA_ID", "OTHER_CHANNEL"):
            for provider, expected in (("Blitz-ID-Instant", "INSTAN"),
                ("GoSend Instant", "INSTAN"), ("GrabExpress Instant", "INSTAN"),
                ("SPX Instant Prioritas", "PRIORITAS"),
                ("Instant Priority", "PRIORITAS"), ("Regular Priority", "LAINNYA")):
                with self.subTest(channel=channel, provider=provider):
                    sample = order(provider=provider)
                    sample["channelId"] = channel
                    self.assertEqual(category(sample, DEFAULT_INSTANT, DEFAULT_PRIORITY), expected)


class ApiTests(unittest.TestCase):
    def test_v3_list_with_string_cursor(self):
        self.assertEqual(list_records({"nextCursor": "123#ORDER", "orders": [{"orderId": "A"}]}), [{"orderId": "A"}])
        with self.assertRaises(ApiError):
            list_records({"orders": [], "unrelated": []})

    def test_signature_and_country(self):
        class Opener:
            def open(self, req, timeout):
                self.request = req
                return io.BytesIO(json.dumps({"code": "SUCCESS", "data": []}).encode())
        opener = Opener()
        path = "/openapi/v3/oms/order/item/batch-get"
        with patch("urllib.request.build_opener", return_value=opener):
            self.assertEqual(Client("dummy-key", "dummy-secret").call(path, {}), [])
        expected = base64.b64encode(hmac.new(b"dummy-secret", f"POST${path}$".encode(), hashlib.sha256).digest()).decode()
        self.assertEqual(opener.request.get_header("Authorization"), "dummy-key:" + expected)
        self.assertEqual(opener.request.get_header("X-advai-country"), "ID")

    def test_pagination_and_details(self):
        client = Client("dummy", "dummy")
        client.call = lambda path, body: (
            {"content": [{"orderId": "A", "orderStatus": "PAID"}], "more": True, "nextCursor": "1794096039000#SAMPLE"}
            if path == "/openapi/v3/oms/order/list" and "nextCursor" not in body else
            {"content": [{"orderId": "B", "orderStatus": "PAID"}], "more": False} if path == "/openapi/v3/oms/order/list" else
            [order("A"), order("B")])
        self.assertEqual(len(client.orders(NOW - timedelta(minutes=1), NOW)), 2)

    def test_incomplete_details_are_retried(self):
        client = Client("dummy", "dummy")
        client.call = lambda path, body: {"content": [{"orderId": "A", "orderStatus": "PAID"}]} if path == "/openapi/v3/oms/order/list" else []
        with self.assertRaises(ApiError):
            client.orders(NOW - timedelta(minutes=1), NOW)

    def test_repeating_cursor_raises(self):
        client = Client("dummy", "dummy")
        client.call = lambda path, body: {"content": [{"orderId": "A"}], "more": True, "nextCursor": ["same"]}
        with self.assertRaises(ApiError):
            client.orders(NOW - timedelta(minutes=1), NOW)

    def test_terminal_orders_do_not_trigger_detail_downloads(self):
        client = Client("dummy", "dummy")
        calls = []
        def call(path, body):
            calls.append(path)
            return {"content": [{"orderId": "DONE", "orderStatus": "SHIPPING"}]}
        client.call = call
        self.assertEqual(client.orders(NOW - timedelta(minutes=1), NOW)[0]["orderStatus"], "SHIPPING")
        self.assertEqual(calls, ["/openapi/v3/oms/order/list"])


if __name__ == "__main__":
    unittest.main()
