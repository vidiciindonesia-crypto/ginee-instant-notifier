"""Ginee read-only polling and local alert history. Python 3.11+."""
import base64
import hashlib
import hmac
import json
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
DEFAULT_INSTANT = "instant, instan"
DEFAULT_PRIORITY = "instant prioritas, instan prioritas, instant priority, instan priority"
STOP_STATUSES = {"READY_TO_SHIP", "PACKED", "CANCELLED", "CANCELED", "SHIPPED", "SHIPPING", "DELIVERED", "CLOSED", "RETURNED"}


def list_records(data):
    if not isinstance(data, dict):
        raise ApiError("Format daftar pesanan tidak sesuai respons V3.")
    arrays = [value for value in data.values() if isinstance(value, list)]
    if len(arrays) != 1:
        raise ApiError("Daftar pesanan V3 tidak dapat ditentukan dengan aman.")
    return arrays[0]


def stamp(value):
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


class ApiError(Exception):
    pass


class Client:
    def __init__(self, key, secret):
        self.key, self.secret = key, secret
        self.next_request = 0

    def call(self, path, payload):
        # Keep below the lowest observed endpoint limit (ListShop: 3 QPS).
        time.sleep(max(0, self.next_request - time.monotonic()))
        self.next_request = time.monotonic() + 0.35
        signature = base64.b64encode(hmac.new(
            self.secret.encode(), f"POST${path}$".encode(), hashlib.sha256
        ).digest()).decode()
        request = urllib.request.Request("https://api.ginee.com" + path,
            data=json.dumps(payload).encode(), method="POST", headers={
                "Content-Type": "application/json", "X-Advai-Country": "ID",
                "Authorization": self.key + ":" + signature})
        # Do not follow redirects with the Authorization header.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=25) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise ApiError(f"HTTP {exc.code} pada {path}. Periksa izin API / koneksi.") from None
        except (OSError, ValueError):
            raise ApiError("Tidak dapat membaca respons Ginee. Periksa internet dan coba lagi.") from None
        if not isinstance(result, dict) or result.get("code") != "SUCCESS":
            code = str(result.get("code", "INVALID_RESPONSE"))[:60] if isinstance(result, dict) else "INVALID_RESPONSE"
            raise ApiError(f"Ginee: {code}. Periksa key, izin API, atau batas permintaan.")
        return result.get("data")

    def orders(self, since, until, watch_ids=()):
        cursor = None
        visited = set()
        orders = {}
        while True:
            body = {"size": 100, "lastUpdateSince": stamp(since), "lastUpdateTo": stamp(until)}
            if cursor is not None:
                body["nextCursor"] = cursor
            data = self.call("/openapi/v3/oms/order/list", body)
            records = list_records(data)
            for item in records:
                if not isinstance(item, dict) or not item.get("orderId"):
                    raise ApiError("Pesanan tanpa ID pada respons Ginee.")
                orders[item["orderId"]] = item
            cursor = data.get("nextCursor")
            if data.get("more") is False or not cursor or not records:
                break
            marker = json.dumps(cursor, sort_keys=True)
            if marker in visited:
                raise ApiError("Cursor Ginee berulang. Akan dicoba ulang tanpa memajukan waktu sinkronisasi.")
            visited.add(marker)
        eligible = {"PAID"}
        # Refresh existing open alerts even when their market update timestamp
        # falls outside the incremental list window.
        watched = set(watch_ids)
        ids = [identifier for identifier, value in orders.items() if (
            value.get("orderStatus") in eligible or
            (identifier in watched and value.get("orderStatus") not in STOP_STATUSES))]
        for identifier in watched:
            if identifier not in orders:
                orders[identifier] = {"orderId": identifier}
                ids.append(identifier)
        detailed = set(ids)
        result = [value for identifier, value in orders.items() if identifier not in detailed]
        for offset in range(0, len(ids), 100):
            batch_ids = ids[offset:offset + 100]
            details = self.call("/openapi/v3/oms/order/item/batch-get", {"orderIds": batch_ids})
            if not isinstance(details, list):
                raise ApiError("Format detail pesanan tidak sesuai dokumentasi.")
            mapped = {d.get("orderId"): d for d in details if isinstance(d, dict)}
            if not all(identifier in mapped for identifier in batch_ids):
                raise ApiError("Detail pesanan belum lengkap; aplikasi akan mencoba lagi.")
            result.extend({**orders[identifier], **mapped[identifier]} for identifier in batch_ids)
        return result


def service(order):
    return " | ".join(" / ".join(str(row.get(k) or "") for k in
        ("logisticsProviderName", "shippingType", "logisticsProviderId"))
        for row in (order.get("logisticsInfos") or []) if isinstance(row, dict))


def category(order, instant, priority):
    # Exact service naming must be validated against the user's real orders.
    text = service(order).casefold()
    if any(term.strip().casefold() in text for term in priority.split(",") if term.strip()):
        return "PRIORITAS"
    if any(term.strip().casefold() in text for term in instant.split(",") if term.strip()):
        return "INSTAN"
    return "LAINNYA"


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)")
        self.db.execute("""CREATE TABLE IF NOT EXISTS alerts (
            id TEXT PRIMARY KEY, number TEXT, shop TEXT, service TEXT, kind TEXT,
            deadline TEXT, acknowledged INTEGER DEFAULT 0, delivered INTEGER DEFAULT 0,
            created TEXT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS stopped_orders (
            id TEXT PRIMARY KEY, status TEXT NOT NULL, seen_at TEXT NOT NULL)""")
        self.db.commit()

    def since(self, now):
        row = self.db.execute("SELECT value FROM state WHERE key='cursor'").fetchone()
        # Ginee may expose a channel update after its timestamp has passed.
        # Re-read a wider window; the alert history prevents repeated announcements.
        return (parse_time(row[0]) - timedelta(minutes=15)) if row else now - timedelta(minutes=15)

    def ingest(self, orders, until, instant, priority):
        # One transaction: retrying a failed pass never skips orders.
        with self.db:
            for order in orders:
                kind = category(order, instant, priority)
                identifier = order["orderId"]
                status = str(order.get("orderStatus", "")).upper()
                if status in STOP_STATUSES:
                    # Persist even if a processed status arrives before the paid order.
                    # A delayed PAID response must never restart its announcements.
                    self.db.execute("INSERT OR REPLACE INTO stopped_orders VALUES (?,?,?)",
                        (identifier, status, stamp(until)))
                    self.db.execute("UPDATE alerts SET acknowledged=1 WHERE id=?", (identifier,))
                    continue
                if self.db.execute("SELECT 1 FROM stopped_orders WHERE id=?", (identifier,)).fetchone():
                    continue
                # Only Lunas/PAID orders are eligible for announcements.
                if kind == "LAINNYA" or status != "PAID":
                    continue
                # User-confirmed rule applies only to Shopee priority orders.
                # TikTok instant orders do not inherit this packing deadline.
                # promisedToShipBefore is a different shipping deadline.
                start = parse_time(order.get("payAt"))
                channel = str(order.get("channelId") or order.get("channel") or "").upper()
                deadline = stamp(start + timedelta(minutes=15)) if (
                    channel == "SHOPEE_ID" and kind == "PRIORITAS" and start
                ) else ""
                number = str(order.get("externalOrderSn") or identifier)
                self.db.execute("""INSERT INTO alerts
                    (id,number,shop,service,kind,deadline,created) VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET service=excluded.service,
                    deadline=excluded.deadline, delivered=CASE WHEN alerts.kind != excluded.kind
                    THEN 0 ELSE alerts.delivered END, kind=excluded.kind""",
                    (identifier, number, str(order.get("shopId") or ""), service(order), kind, deadline, stamp(until)))
            self.db.execute("INSERT OR REPLACE INTO state VALUES ('cursor',?)", (stamp(until),))

    def rows(self):
        self.db.row_factory = sqlite3.Row
        return [dict(r) for r in self.db.execute("""SELECT a.*, s.status AS stopped_status
            FROM alerts a LEFT JOIN stopped_orders s ON s.id=a.id
            ORDER BY a.acknowledged, a.kind DESC, a.created DESC LIMIT 500""")]

    def can_announce(self, identifier):
        return self.db.execute("""SELECT 1 FROM alerts a WHERE a.id=?
            AND a.acknowledged=0
            AND NOT EXISTS(SELECT 1 FROM stopped_orders s WHERE s.id=a.id)""",
            (identifier,)).fetchone() is not None

    def active_ids(self):
        return [row[0] for row in self.db.execute("""SELECT a.id FROM alerts a
            WHERE a.acknowledged=0
            AND NOT EXISTS(SELECT 1 FROM stopped_orders s WHERE s.id=a.id)""")]

    def pending(self):
        self.db.row_factory = sqlite3.Row
        return [dict(r) for r in self.db.execute("SELECT * FROM alerts WHERE delivered=0 AND acknowledged=0")]

    def mark_delivered(self, identifier):
        with self.db:
            self.db.execute("UPDATE alerts SET delivered=1 WHERE id=?", (identifier,))

    def acknowledge(self, identifier):
        with self.db:
            self.db.execute("UPDATE alerts SET acknowledged=1 WHERE id=?", (identifier,))
