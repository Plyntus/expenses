import json
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import PRODUCT_TITLE, TRACKED_SKUS

COUNTED_STATUSES = {"AUTHORIZED", "PAID", "PARTIALLY_PAID", "PARTIALLY_REFUNDED", "REFUNDED"}


def utcnow():
    return datetime.now(timezone.utc)


def parse_date(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Store:
    def __init__(self, path):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    eligible INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lines (
                    id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                    product_id TEXT, title TEXT NOT NULL, sku TEXT NOT NULL,
                    quantity INTEGER NOT NULL, amount TEXT NOT NULL, currency TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS lines_order ON lines(order_id);
                CREATE INDEX IF NOT EXISTS orders_created ON orders(created_at);
                CREATE INDEX IF NOT EXISTS lines_sku ON lines(sku);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(orders)")}
            if "financial_status" not in columns:
                db.execute("ALTER TABLE orders ADD COLUMN financial_status TEXT NOT NULL DEFAULT 'UNKNOWN'")
            line_columns = {row[1] for row in db.execute("PRAGMA table_info(lines)")}
            for column in ("original_quantity", "refunded_quantity"):
                if column not in line_columns:
                    # NULL means not imported yet; never present it as zero refunds.
                    db.execute(f"ALTER TABLE lines ADD COLUMN {column} INTEGER")
            # Migrate the earlier product-wide cache without losing tracked history.
            placeholders = ",".join("?" for _ in TRACKED_SKUS)
            db.execute(f"DELETE FROM lines WHERE sku NOT IN ({placeholders})", TRACKED_SKUS)
            db.execute("DELETE FROM orders WHERE NOT EXISTS (SELECT 1 FROM lines WHERE order_id=orders.id)")
            db.execute("DROP TABLE IF EXISTS products")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, json.dumps(value)))

    def upsert_orders(self, orders):
        with self.connect() as db:
            for order in orders:
                lines = [line for line in order["lineItems"]["nodes"] if line.get("sku") in TRACKED_SKUS]
                if not lines:
                    # An edited order can stop containing every tracked SKU.
                    db.execute("DELETE FROM orders WHERE id=?", (order["id"],))
                    continue
                status = order["displayFinancialStatus"]
                refunds = defaultdict(int)
                for refund in order["refunds"]:
                    for item in refund["refundLineItems"]["nodes"]:
                        refunds[item["lineItem"]["id"]] += item["quantity"]
                # A cancelled, refunded sale stays in order/refund history.
                has_product_refund = any(refunds[line["id"]] > 0 for line in lines)
                eligible = not order["test"] and status in COUNTED_STATUSES and (not order["cancelledAt"] or has_product_refund)
                db.execute("""INSERT INTO orders (id, created_at, updated_at, eligible, financial_status) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET created_at=excluded.created_at,
                    updated_at=excluded.updated_at, eligible=excluded.eligible, financial_status=excluded.financial_status""",
                    (order["id"], parse_date(order["createdAt"]).isoformat(),
                     parse_date(order["updatedAt"]).isoformat(), int(bool(eligible)), status))
                db.execute("DELETE FROM lines WHERE order_id=?", (order["id"],))
                for line in lines:
                    money = line["priceAfterAllDiscountsBeforeTaxesSet"]["shopMoney"]
                    # Parse here so malformed upstream data rolls back this entire page.
                    amount = str(Decimal(money["amount"]))
                    db.execute("""INSERT INTO lines
                        (id, order_id, product_id, title, sku, quantity, amount, currency, original_quantity, refunded_quantity)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                        line["id"], order["id"], (line.get("product") or {}).get("id"),
                        line["title"], line.get("sku") or "", line["currentQuantity"], amount, money["currencyCode"],
                        line["quantity"], refunds[line["id"]],
                    ))

    def orders_needing_refresh(self, limit=20):
        with self.connect() as db:
            return [row[0] for row in db.execute("""SELECT id FROM orders WHERE financial_status='UNKNOWN'
                OR EXISTS (SELECT 1 FROM lines WHERE order_id=orders.id
                           AND (original_quantity IS NULL OR refunded_quantity IS NULL)) LIMIT ?""", (limit,))]

    def refunds_ready(self):
        return not self.orders_needing_refresh(limit=1)

    def remove_unavailable_orders(self, ids):
        with self.connect() as db:
            db.executemany("DELETE FROM orders WHERE id=?", [(value,) for value in ids])

    def prune(self, cutoff):
        with self.connect() as db:
            db.execute("DELETE FROM orders WHERE created_at < ?", (cutoff,))

    def hourly(self, tz_name, now=None):
        now = (now or utcnow()).astimezone(timezone.utc)
        start = now - timedelta(hours=24)
        intervals = [{"start_at": (start + timedelta(hours=i)).isoformat(),
                      "end_at": (start + timedelta(hours=i + 1)).isoformat()} for i in range(24)]
        buckets = {sku: [{"quantity": 0, "refunded_quantity": 0, "orders": set()} for _ in intervals] for sku in TRACKED_SKUS}
        overall = [set() for _ in intervals]
        with self.connect() as db:
            rows = db.execute("""SELECT l.sku, l.quantity, l.refunded_quantity, l.order_id, o.created_at
                FROM lines l JOIN orders o ON o.id=l.order_id
                WHERE o.eligible=1 AND o.created_at >= ? AND o.created_at < ? AND COALESCE(l.original_quantity,l.quantity) > 0""",
                (start.isoformat(), now.isoformat())).fetchall()
        for row in rows:
            if row["sku"] not in buckets:
                continue
            index = int((parse_date(row["created_at"]) - start).total_seconds() // 3600)
            if not 0 <= index < 24:
                continue
            bucket = buckets[row["sku"]][index]
            bucket["quantity"] += row["quantity"]
            bucket["refunded_quantity"] += row["refunded_quantity"] or 0
            bucket["orders"].add(row["order_id"])
            overall[index].add(row["order_id"])
        series = [{"sku": sku,
                   "hourly": [{**intervals[i], "quantity": value["quantity"], "refunded_quantity": value["refunded_quantity"], "orders": len(value["orders"])}
                              for i, value in enumerate(values)]} for sku, values in buckets.items()]
        return {"timezone": tz_name, "start_at": start.isoformat(), "end_at": now.isoformat(),
                "hourly": [{**intervals[i], "quantity": sum(buckets[sku][i]["quantity"] for sku in TRACKED_SKUS),
                            "refunded_quantity": sum(buckets[sku][i]["refunded_quantity"] for sku in TRACKED_SKUS),
                            "orders": len(overall[i])} for i in range(24)], "skus": series}

    def dashboard(self, days, tz_name, now=None):
        now = now or utcnow()
        tz = ZoneInfo(tz_name)
        today = now.astimezone(tz).date()
        first = today - timedelta(days=days - 1)
        labels = [(first + timedelta(days=i)).isoformat() for i in range(days)]
        start = datetime.combine(first, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
        buckets = {sku: {"sku": sku, "title": PRODUCT_TITLE, "quantity": 0, "refunded_quantity": 0,
                   "revenue": Decimal(0), "orders": set(), "authorized_orders": set(),
                   "daily": {day: {"quantity": 0, "refunded_quantity": 0, "revenue": Decimal(0), "orders": set(), "authorized_orders": set()} for day in labels}} for sku in TRACKED_SKUS}
        currencies = set()
        with self.connect() as db:
            rows = db.execute("""SELECT l.*, o.created_at, o.financial_status FROM lines l JOIN orders o ON o.id=l.order_id
                WHERE o.eligible=1 AND o.created_at >= ? AND COALESCE(l.original_quantity,l.quantity) > 0""", (start.isoformat(),)).fetchall()
        all_orders = set()
        all_authorized_orders = set()
        daily_orders = defaultdict(set)
        for row in rows:
            if row["sku"] not in buckets:
                continue
            day = parse_date(row["created_at"]).astimezone(tz).date().isoformat()
            if day not in labels or parse_date(row["created_at"]) > now:
                continue
            currencies.add(row["currency"])
            item = buckets[row["sku"]]
            amount = Decimal(row["amount"])
            item["quantity"] += row["quantity"]
            item["refunded_quantity"] += row["refunded_quantity"] or 0
            item["revenue"] += amount
            item["orders"].add(row["order_id"])
            item["daily"][day]["quantity"] += row["quantity"]
            item["daily"][day]["refunded_quantity"] += row["refunded_quantity"] or 0
            item["daily"][day]["revenue"] += amount
            item["daily"][day]["orders"].add(row["order_id"])
            if row["financial_status"] == "AUTHORIZED":
                item["authorized_orders"].add(row["order_id"])
                item["daily"][day]["authorized_orders"].add(row["order_id"])
                all_authorized_orders.add(row["order_id"])
            all_orders.add(row["order_id"])
            daily_orders[day].add(row["order_id"])
        if len(currencies) > 1:
            raise ValueError("Multiple shop currencies in the selected period; totals cannot be combined")
        currency = next(iter(currencies), self.get("currency", "USD"))
        daily = [{"date": day, "revenue": float(sum((p["daily"][day]["revenue"] for p in buckets.values()), Decimal(0))),
                  "quantity": sum(p["daily"][day]["quantity"] for p in buckets.values()),
                  "refunded_quantity": sum(p["daily"][day]["refunded_quantity"] for p in buckets.values()),
                  "orders": len(daily_orders[day])} for day in labels]
        serialized = []
        for item in buckets.values():
            serialized.append({**item, "revenue": float(item["revenue"]), "orders": len(item["orders"]),
                               "authorized_orders": len(item["authorized_orders"]),
                               "daily": [{"date": day, "quantity": value["quantity"], "revenue": float(value["revenue"]),
                                          "refunded_quantity": value["refunded_quantity"],
                                          "orders": len(value["orders"]), "authorized_orders": len(value["authorized_orders"])} for day, value in item["daily"].items()]})
        return {"currency": currency, "timezone": tz_name, "days": days, "refunds_ready": self.refunds_ready(),
                "totals": {"revenue": float(sum((p["revenue"] for p in buckets.values()), Decimal(0))),
                           "quantity": sum(p["quantity"] for p in buckets.values()), "orders": len(all_orders),
                           "refunded_quantity": sum(p["refunded_quantity"] for p in buckets.values()),
                           "authorized_orders": len(all_authorized_orders)},
                "daily": daily, "skus": serialized, "last_24_hours": self.hourly(tz_name, now)}
