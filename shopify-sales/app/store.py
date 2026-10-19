import json
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo


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
                CREATE TABLE IF NOT EXISTS products (id TEXT PRIMARY KEY, title TEXT NOT NULL);
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
            """)

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

    def products(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM products ORDER BY title")]

    def select_products(self, products):
        with self.connect() as db:
            db.execute("DELETE FROM products")
            db.executemany("INSERT INTO products VALUES (?, ?)", [(p["id"], p["title"]) for p in products])

    def upsert_orders(self, orders):
        with self.connect() as db:
            for order in orders:
                eligible = not order["test"] and not order["cancelledAt"] and order["displayFinancialStatus"] in (
                    "PAID", "PARTIALLY_REFUNDED", "REFUNDED"
                )
                db.execute("""INSERT INTO orders VALUES (?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET created_at=excluded.created_at,
                    updated_at=excluded.updated_at, eligible=excluded.eligible""",
                    (order["id"], parse_date(order["createdAt"]).isoformat(),
                     parse_date(order["updatedAt"]).isoformat(), int(bool(eligible))))
                db.execute("DELETE FROM lines WHERE order_id=?", (order["id"],))
                for line in order["lineItems"]["nodes"]:
                    money = line["priceAfterAllDiscountsBeforeTaxesSet"]["shopMoney"]
                    # Parse here so malformed upstream data rolls back this entire page.
                    amount = str(Decimal(money["amount"]))
                    db.execute("INSERT INTO lines VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
                        line["id"], order["id"], (line.get("product") or {}).get("id"),
                        line["title"], line.get("sku") or "", line["currentQuantity"], amount, money["currencyCode"],
                    ))

    def prune(self, cutoff):
        with self.connect() as db:
            db.execute("DELETE FROM orders WHERE created_at < ?", (cutoff,))

    def dashboard(self, days, tz_name, now=None):
        now = now or utcnow()
        tz = ZoneInfo(tz_name)
        today = now.astimezone(tz).date()
        first = today - timedelta(days=days - 1)
        labels = [(first + timedelta(days=i)).isoformat() for i in range(days)]
        start = datetime.combine(first, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
        products = self.products()
        ids = {p["id"] for p in products}
        buckets = {p["id"]: {"id": p["id"], "title": p["title"], "quantity": 0,
                   "revenue": Decimal(0), "orders": set(),
                   "daily": {day: {"quantity": 0, "revenue": Decimal(0)} for day in labels}} for p in products}
        currencies = set()
        with self.connect() as db:
            rows = db.execute("""SELECT l.*, o.created_at FROM lines l JOIN orders o ON o.id=l.order_id
                WHERE o.eligible=1 AND o.created_at >= ? AND l.quantity > 0""", (start.isoformat(),)).fetchall()
        all_orders = set()
        daily_orders = defaultdict(set)
        for row in rows:
            if row["product_id"] not in ids:
                continue
            day = parse_date(row["created_at"]).astimezone(tz).date().isoformat()
            if day not in labels or parse_date(row["created_at"]) > now:
                continue
            currencies.add(row["currency"])
            item = buckets[row["product_id"]]
            amount = Decimal(row["amount"])
            item["quantity"] += row["quantity"]
            item["revenue"] += amount
            item["orders"].add(row["order_id"])
            item["daily"][day]["quantity"] += row["quantity"]
            item["daily"][day]["revenue"] += amount
            all_orders.add(row["order_id"])
            daily_orders[day].add(row["order_id"])
        if len(currencies) > 1:
            raise ValueError("Multiple shop currencies in the selected period; totals cannot be combined")
        currency = next(iter(currencies), self.get("currency", "USD"))
        daily = [{"date": day, "revenue": float(sum((p["daily"][day]["revenue"] for p in buckets.values()), Decimal(0))),
                  "quantity": sum(p["daily"][day]["quantity"] for p in buckets.values()), "orders": len(daily_orders[day])} for day in labels]
        serialized = []
        for item in buckets.values():
            serialized.append({**item, "revenue": float(item["revenue"]), "orders": len(item["orders"]),
                               "daily": [{"date": day, "quantity": value["quantity"], "revenue": float(value["revenue"])} for day, value in item["daily"].items()]})
        return {"currency": currency, "timezone": tz_name, "days": days,
                "totals": {"revenue": float(sum((p["revenue"] for p in buckets.values()), Decimal(0))),
                           "quantity": sum(p["quantity"] for p in buckets.values()), "orders": len(all_orders)},
                "daily": daily, "products": sorted(serialized, key=lambda p: p["revenue"], reverse=True)}
