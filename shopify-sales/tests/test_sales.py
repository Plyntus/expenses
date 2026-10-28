import asyncio
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.config import TRACKED_SKUS
from app.main import create_app
from app.shopify import Shopify, ShopifyError
from app.store import Store
from app.sync import Sync

P1 = "gid://shopify/Product/1"
P2 = "gid://shopify/Product/2"
NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)


def line(line_id="line-1", product=P1, amount="18.90", quantity=2, sku=TRACKED_SKUS[0], original=None):
    return {"id": line_id, "product": {"id": product}, "title": "Flipper One Pre-order", "sku": sku,
            "quantity": quantity if original is None else original, "currentQuantity": quantity, "priceAfterAllDiscountsBeforeTaxesSet": {
                "shopMoney": {"amount": amount, "currencyCode": "USD"}}}


def order(order_id="order-1", lines=None, **kwargs):
    return {"id": order_id, "createdAt": "2026-10-19T10:00:00Z", "updatedAt": "2026-10-19T10:01:00Z",
            "cancelledAt": None, "test": False, "displayFinancialStatus": "PAID", "refunds": [],
            "lineItems": {"nodes": lines or [line("line-" + order_id)], "pageInfo": {"hasNextPage": False, "endCursor": None}}, **kwargs}


def refund(*allocations, refund_id="refund-1"):
    return {"id": refund_id, "refundLineItems": {"nodes": [
        {"quantity": quantity, "lineItem": {"id": line_id}} for line_id, quantity in allocations]}}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name + "/sales.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def dashboard(self, days=7, tz="Europe/Belgrade"):
        return self.store.dashboard(days, tz, NOW)

    def test_repeated_polls_do_not_double_count_and_orders_are_distinct(self):
        item = order(lines=[line(), line("line-2", P1, "0.10", 1, sku=TRACKED_SKUS[1])])
        self.store.upsert_orders([item])
        self.store.upsert_orders([item])
        totals = self.dashboard()["totals"]
        self.assertEqual(totals, {"revenue": 19.0, "quantity": 3, "orders": 1, "authorized_orders": 0, "refunded_quantity": 0})
        self.assertEqual(len(self.dashboard()["daily"]), 7)
        skus = self.dashboard()["skus"]
        self.assertEqual([s["sku"] for s in skus], list(TRACKED_SKUS))
        self.assertEqual([s["revenue"] for s in skus], [18.9, 0.1, 0, 0])
        self.assertEqual([s["orders"] for s in skus], [1, 1, 0, 0])
        self.assertEqual(skus[0]["daily"][-1]["orders"], 1)

    def test_partial_then_full_refund_and_cancel_replace_original(self):
        self.store.upsert_orders([order()])
        self.store.upsert_orders([order(lines=[line(amount="9.45", quantity=1)], displayFinancialStatus="PARTIALLY_REFUNDED")])
        self.assertEqual(self.dashboard()["totals"], {"revenue": 9.45, "quantity": 1, "orders": 1, "authorized_orders": 0, "refunded_quantity": 0})
        self.store.upsert_orders([order(lines=[line(amount="0", quantity=0)], displayFinancialStatus="REFUNDED")])
        self.assertEqual(self.dashboard()["totals"]["quantity"], 0)
        self.store.upsert_orders([order(cancelledAt="2026-10-19T11:00:00Z")])
        self.assertEqual(self.dashboard()["totals"]["revenue"], 0)

    def test_100_orders_10_refunds_leave_90_units_and_100_orders(self):
        orders = []
        for i in range(100):
            returned = i < 10
            orders.append(order(str(i), lines=[line(str(i), quantity=0 if returned else 1, original=1)],
                displayFinancialStatus="REFUNDED" if returned else "AUTHORIZED",
                cancelledAt="2026-10-19T11:00:00Z" if i < 5 else None,
                refunds=[refund((str(i), 1))] if returned else []))
        self.store.upsert_orders(orders)
        self.store.upsert_orders(orders)
        data = self.dashboard()
        for result in (data['totals'], data['skus'][0], data['daily'][-1], data['skus'][0]['daily'][-1]):
            self.assertEqual(result['quantity'], 90)
            self.assertEqual(result['orders'], 100)
            self.assertEqual(result['refunded_quantity'], 10)
        self.assertTrue(data['refunds_ready'])
        hours = data['last_48_hours']['hourly']
        self.assertEqual(sum(h['orders'] for h in hours), 100)
        self.assertEqual(sum(h['refunded_quantity'] for h in hours), 10)
        self.assertEqual(sum(h['quantity'] for h in hours), 90)

    def test_refunds_sum_exact_allocations_per_sku_and_do_not_infer_edits(self):
        self.store.upsert_orders([order(lines=[
            line('a', quantity=1, original=4), line('b', quantity=2, original=3),
            line('c', quantity=3, original=5, sku=TRACKED_SKUS[1])],
            displayFinancialStatus='PARTIALLY_REFUNDED', refunds=[
                refund(('a', 1), ('b', 1)), refund(('a', 2), ('untracked', 99), refund_id='refund-2'),
                refund(refund_id='shipping-only')])])
        data = self.dashboard()
        self.assertEqual(data['totals']['quantity'], 6)
        self.assertEqual(data['totals']['orders'], 1)
        self.assertEqual([s['refunded_quantity'] for s in data['skus']], [4, 0, 0, 0])
        self.assertEqual([s['orders'] for s in data['skus']], [1, 1, 0, 0])
        # Editing down to zero keeps the original order but does not invent a refund.
        self.store.upsert_orders([order(lines=[line('a', quantity=0, original=4)])])
        self.assertEqual(self.dashboard()['totals']['orders'], 1)
        self.assertEqual(self.dashboard()['totals']['refunded_quantity'], 0)

    def test_refunded_test_and_pending_orders_do_not_enter_metrics(self):
        self.store.upsert_orders([order('test', test=True, lines=[line('a', quantity=0, original=1)],
            displayFinancialStatus='REFUNDED', refunds=[refund(('a', 1))]),
            order('pending', displayFinancialStatus='PENDING', lines=[line('b', quantity=0, original=1)], refunds=[refund(('b', 1))])])
        self.assertEqual(self.dashboard()['totals']['orders'], 0)
        self.assertEqual(self.dashboard()['totals']['refunded_quantity'], 0)

    def test_unpaid_cancelled_test_orders_are_excluded(self):
        self.store.upsert_orders([order("test", test=True), order("pending", displayFinancialStatus="PENDING"),
                                  order("expired", displayFinancialStatus="EXPIRED"), order("voided", displayFinancialStatus="VOIDED"),
                                  order("cancelled", cancelledAt="2026-10-19T11:00:00Z")])
        self.assertEqual(self.dashboard()["totals"]["orders"], 0)

    def test_timezone_and_period_boundaries(self):
        self.store.upsert_orders([order(createdAt="2026-10-18T23:30:00Z")])
        self.assertEqual(self.dashboard(1)["totals"]["orders"], 1)
        self.assertEqual(self.dashboard(1, "UTC")["totals"]["orders"], 0)

    def test_hourly_window_has_48_buckets_and_exact_time_boundaries(self):
        now = NOW + timedelta(minutes=37, seconds=42)
        start = NOW - timedelta(hours=47)
        samples = [
            ("start", start, 2, TRACKED_SKUS[0]),
            ("next-hour", start + timedelta(hours=1), 3, TRACKED_SKUS[0]),
            ("recent", now - timedelta(microseconds=1), 4, TRACKED_SKUS[0]),
            ("too-old", start - timedelta(microseconds=1), 99, TRACKED_SKUS[0]),
            ("end", now, 99, TRACKED_SKUS[0]),
            ("future", now + timedelta(minutes=1), 99, TRACKED_SKUS[0]),
        ]
        self.store.upsert_orders([order(name, createdAt=stamp.isoformat(),
            lines=[line(name, quantity=quantity, sku=sku)]) for name, stamp, quantity, sku in samples])
        data = self.store.hourly("Europe/Belgrade", now)
        self.assertEqual(len(data["hourly"]), 48)
        self.assertEqual([len(s["hourly"]) for s in data["skus"]], [48, 48, 48, 48])
        self.assertEqual(data["start_at"], start.isoformat())
        self.assertEqual(data["end_at"], (NOW + timedelta(hours=1)).isoformat())
        self.assertEqual(data["observed_until"], now.isoformat())
        self.assertTrue(data["hourly"][-1]["partial"])
        self.assertTrue(all(not b["partial"] for b in data["hourly"][:-1]))
        later = self.store.hourly("Europe/Belgrade", now + timedelta(minutes=5))
        self.assertEqual([b["start_at"] for b in data["hourly"]], [b["start_at"] for b in later["hourly"]])
        for bucket in data["hourly"]:
            self.assertEqual(datetime.fromisoformat(bucket["start_at"]).minute, 0)
            self.assertEqual(datetime.fromisoformat(bucket["end_at"]).minute, 0)
        self.assertEqual(sum(b["quantity"] for b in data["hourly"]), 9)
        self.assertEqual(data["hourly"][0]["quantity"], 2)
        self.assertEqual(data["hourly"][1]["quantity"], 3)
        self.assertEqual(data["hourly"][-1]["quantity"], 4)
        self.assertEqual(sum(b["quantity"] for b in data["skus"][0]["hourly"]), 9)
        next_hour = self.store.hourly("Europe/Belgrade", NOW + timedelta(hours=1))
        self.assertEqual(next_hour["start_at"], (start + timedelta(hours=1)).isoformat())
        self.assertEqual(next_hour["hourly"][-1]["start_at"], (NOW + timedelta(hours=1)).isoformat())
        self.assertEqual(next_hour["hourly"][-1]["quantity"], 0)

    def test_hourly_uses_existing_eligibility_and_distinct_order_rules(self):
        self.store.upsert_orders([
            order("auth", displayFinancialStatus="AUTHORIZED", lines=[
                line("a", quantity=2), line("b", quantity=3), line("c", quantity=1, sku=TRACKED_SKUS[1])]),
            order("void", displayFinancialStatus="VOIDED"),
            order("cancel", cancelledAt="2026-10-19T11:00:00Z"),
            order("test", test=True),
            order("refunded", displayFinancialStatus="REFUNDED", lines=[line("refund", quantity=0)]),
        ])
        data = self.store.hourly("UTC", NOW)
        self.assertEqual(sum(b["quantity"] for b in data["hourly"]), 6)
        self.assertEqual(sum(b["orders"] for b in data["hourly"]), 1)
        self.assertEqual(sum(b["orders"] for b in data["skus"][0]["hourly"]), 1)
        self.assertEqual(sum(b["quantity"] for b in data["skus"][0]["hourly"]), 5)

    def test_last_48_hours_is_independent_of_daily_period(self):
        self.store.upsert_orders([order(createdAt=(NOW - timedelta(hours=44)).isoformat())])
        today = self.store.dashboard(1, "UTC", NOW)
        week = self.store.dashboard(7, "UTC", NOW)
        self.assertEqual(today["totals"]["quantity"], 0)
        self.assertEqual(week["totals"]["quantity"], 2)
        self.assertEqual(today["last_48_hours"], week["last_48_hours"])
        self.assertEqual(sum(b["quantity"] for b in today["last_48_hours"]["hourly"]), 2)

    def test_hourly_window_handles_daylight_saving_without_merging_hours(self):
        now = datetime(2026, 10, 25, 4, 30, tzinfo=timezone.utc)
        self.store.upsert_orders([
            order("first-0230", createdAt="2026-10-25T00:30:00Z"),
            order("second-0230", createdAt="2026-10-25T01:30:00Z"),
        ])
        data = self.store.hourly("Europe/Belgrade", now)
        nonempty = [b for b in data["hourly"] if b["quantity"]]
        self.assertEqual(len(data["hourly"]), 48)
        self.assertEqual(len(nonempty), 2)
        self.assertNotEqual(nonempty[0]["start_at"], nonempty[1]["start_at"])
        self.assertEqual([b["start_at"] for b in nonempty], ["2026-10-25T00:00:00+00:00", "2026-10-25T01:00:00+00:00"])
        self.assertEqual(sum(b["quantity"] for b in nonempty), 4)

    def test_migration_removes_untracked_history_but_keeps_tracked_data_and_checkpoint(self):
        self.store.upsert_orders([order()])
        self.store.set("checkpoint", "2026-10-19T11:00:00+00:00")
        with self.store.connect() as db:
            db.execute("CREATE TABLE products (id TEXT PRIMARY KEY, title TEXT)")
            db.execute("INSERT INTO products VALUES ('old-product', 'Untracked product')")
            db.execute("INSERT INTO orders (id,created_at,updated_at,eligible) VALUES ('untracked-order','2026-10-19T10:00:00+00:00','2026-10-19T10:01:00+00:00',1)")
            db.execute("INSERT INTO lines (id,order_id,product_id,title,sku,quantity,amount,currency) VALUES ('untracked-line','untracked-order','other','Other','OTHER-SKU',5,'5000','USD')")
            db.execute("INSERT INTO lines (id,order_id,product_id,title,sku,quantity,amount,currency) VALUES ('mixed-line','order-1','other','Other','OTHER-SKU',5,'5000','USD')")
        reopened = Store(self.store.path)
        self.assertEqual(reopened.get("checkpoint"), "2026-10-19T11:00:00+00:00")
        with reopened.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM lines").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM orders").fetchone()[0], 1)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='products'").fetchone())
        self.assertEqual(self.dashboard()["skus"][0]["revenue"], 18.9)

    def test_exact_sku_filter_drops_other_lines_and_unrelated_orders(self):
        self.store.upsert_orders([
            order(lines=[line(), line("other", sku="FLP-O-COUPON-379-EXTRA", amount="9999")]),
            order("unknown", lines=[line("unknown", sku=None)]),
            order("different", lines=[line("different", sku="FZ")]),
        ])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM lines").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM orders").fetchone()[0], 1)
        self.assertEqual(self.dashboard()["totals"]["revenue"], 18.9)

    def test_order_edit_removes_previous_sku_and_untracked_orders(self):
        self.store.upsert_orders([order()])
        self.store.upsert_orders([order(lines=[line(sku="FLP-O-COUPON-399")])])
        self.assertEqual([s["quantity"] for s in self.dashboard()["skus"]], [0, 2, 0, 0])
        self.store.upsert_orders([order(lines=[line(sku="UNTRACKED")])])
        self.assertEqual(self.dashboard()["totals"]["quantity"], 0)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM orders").fetchone()[0], 0)

    def test_only_four_requested_coupons_are_counted_separately(self):
        self.store.upsert_orders([order(lines=[line(str(i), P1, str((i + 1) * 10), i + 1, sku=sku)
                                              for i, sku in enumerate((*TRACKED_SKUS, "FLP-O-COUPON-499"))])])
        data = self.dashboard()
        self.assertEqual([s["quantity"] for s in data["skus"]], [1, 2, 3, 4])
        self.assertEqual([s["revenue"] for s in data["skus"]], [10, 20, 30, 40])
        self.assertEqual([s["daily"][-1]["quantity"] for s in data["skus"]], [1, 2, 3, 4])
        self.assertEqual(data["totals"]["orders"], 1)

    def test_multiple_lines_of_one_sku_count_one_order(self):
        self.store.upsert_orders([order(lines=[line("a"), line("b")])])
        item = self.dashboard()["skus"][0]
        self.assertEqual(item["quantity"], 4)
        self.assertEqual(item["revenue"], 37.8)
        self.assertEqual(item["orders"], 1)
        self.assertEqual(item["daily"][-1]["orders"], 1)

    def test_authorized_preorders_are_counted_separately_from_paid(self):
        self.store.upsert_orders([
            order("auth", displayFinancialStatus="AUTHORIZED", lines=[line("auth-line", amount="20.00", quantity=1)]),
            order("paid", lines=[line("paid-line", amount="20.00", quantity=1)]),
            order("partial", displayFinancialStatus="PARTIALLY_PAID", lines=[line("partial-line", amount="20.00", quantity=1)]),
        ])
        item = self.dashboard()["skus"][0]
        self.assertEqual(item["orders"], 3)
        self.assertEqual(item["quantity"], 3)
        self.assertEqual(item["revenue"], 60)
        self.assertEqual(item["authorized_orders"], 1)
        self.assertEqual(item["daily"][-1]["authorized_orders"], 1)

    def test_authorization_to_paid_and_voided_replace_existing_counts(self):
        self.store.upsert_orders([order(displayFinancialStatus="AUTHORIZED")])
        self.assertEqual(self.dashboard()["skus"][0]["authorized_orders"], 1)
        self.store.upsert_orders([order(displayFinancialStatus="PAID")])
        self.assertEqual(self.dashboard()["skus"][0]["orders"], 1)
        self.assertEqual(self.dashboard()["skus"][0]["authorized_orders"], 0)
        self.store.upsert_orders([order(displayFinancialStatus="VOIDED")])
        self.assertEqual(self.dashboard()["skus"][0]["orders"], 0)

    def test_authorized_order_with_two_tracked_skus_counts_once(self):
        self.store.upsert_orders([order(displayFinancialStatus="AUTHORIZED", lines=[
            line("a", sku=TRACKED_SKUS[0]), line("b", sku=TRACKED_SKUS[1])])])
        data = self.dashboard()
        self.assertEqual(data["totals"]["authorized_orders"], 1)
        self.assertEqual([s["authorized_orders"] for s in data["skus"]], [1, 1, 0, 0])

    def test_invalid_page_rolls_back(self):
        self.store.upsert_orders([order()])
        malformed = order(lines=[line(amount="not a decimal")])
        with self.assertRaises(Exception):
            self.store.upsert_orders([malformed])
        self.assertEqual(self.dashboard()["totals"]["revenue"], 18.9)

    def test_multiple_currencies_are_never_silently_combined(self):
        second = line("line-2", P2)
        second["priceAfterAllDiscountsBeforeTaxesSet"]["shopMoney"]["currencyCode"] = "EUR"
        self.store.upsert_orders([order(lines=[line(), second])])
        with self.assertRaises(ValueError):
            self.dashboard()

    def test_pruning_cascades_lines(self):
        self.store.upsert_orders([order()])
        self.store.prune("2026-10-20T00:00:00+00:00")
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM lines").fetchone()[0], 0)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(username="ros", password="a-unique-long-test-password", db_path=self.tmp.name + "/db.sqlite3")
        self.client = TestClient(create_app(self.settings, poll=False))
        self.auth = (self.settings.username, self.settings.password)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_health_and_favicon_are_public_but_dashboard_is_protected(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        icon = self.client.get("/favicon.png")
        self.assertEqual(icon.status_code, 200)
        self.assertEqual(icon.headers["content-type"], "image/png")
        for path in ("/", "/assets/app.js", "/assets/flipper-one.png", "/api/status", "/api/dashboard", "/api/traffic"):
            self.assertEqual(self.client.get(path).status_code, 401, path)
        self.assertEqual(self.client.get("/api/dashboard", auth=self.auth).status_code, 200)

    def test_setup_before_credentials_and_all_assets(self):
        response = self.client.get("/api/status", auth=self.auth)
        self.assertFalse(response.json()["configured"])
        traffic = self.client.get('/api/traffic?days=1', auth=self.auth)
        self.assertEqual(traffic.status_code, 200)
        self.assertIsNone(traffic.json()['sessions'])
        self.assertFalse(traffic.json()['available'])
        self.assertEqual(self.client.get('/api/traffic?days=0', auth=self.auth).status_code, 422)
        for path in ("/", "/assets/style.css", "/assets/app.js", "/assets/chart.umd.js", "/assets/flipper-one.png"):
            self.assertEqual(self.client.get(path, auth=self.auth).status_code, 200, path)
        self.assertEqual(self.client.get("/assets/unknown.png", auth=self.auth).status_code, 404)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

    def test_fixed_sku_api_does_not_allow_other_products(self):
        status = self.client.get("/api/status", auth=self.auth).json()
        self.assertEqual(status["tracked_skus"], list(TRACKED_SKUS))
        data = self.client.get("/api/dashboard", auth=self.auth).json()
        self.assertEqual([s["sku"] for s in data["skus"]], list(TRACKED_SKUS))
        self.assertTrue(all(s["quantity"] == 0 for s in data["skus"]))
        self.assertEqual(len(data["last_48_hours"]["hourly"]), 48)
        self.assertEqual(len(data["last_48_hours"]["skus"]), 4)
        self.assertEqual(self.client.get("/api/products", auth=self.auth).status_code, 404)
        self.assertEqual(self.client.put("/api/products", json={"products": []}, auth=self.auth,
                                         headers={"X-Sales-Request": "1"}).status_code, 404)

    def test_authentication_rate_limit(self):
        for _ in range(10):
            self.assertEqual(self.client.get("/api/status", auth=("ros", "wrong")).status_code, 401)
        self.assertEqual(self.client.get("/api/status", auth=("ros", "wrong")).status_code, 429)

    def test_invalid_environment_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            Settings.from_env()
        with patch.dict(os.environ, {"DASHBOARD_USERNAME": "ros", "DASHBOARD_PASSWORD": "a-long-enough-password", "SHOPIFY_SHOP": "http://localhost"}, clear=True), self.assertRaises(ValueError):
            Settings.from_env()


class ShopifyTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_coupon_scope_backfills_unchanged_orders_and_retries_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = tmp + '/db.sqlite3'
            store = Store(path)
            with store.connect() as db:
                db.execute("UPDATE state SET value=? WHERE key='tracked_skus'", (json.dumps(['FLP-O-COUPON-379']),))
                db.execute("INSERT INTO orders VALUES ('old','2026-10-19T10:00:00Z','2026-10-19T10:01:00Z',1,'PAID')")
                db.execute("""INSERT INTO lines
                    (id,order_id,title,sku,quantity,amount,currency,original_quantity,refunded_quantity)
                    VALUES ('old-line','old','Flipper One Pre-order','FLP-O-COUPON-379',1,'20','USD',1,0)""")
            store.set('checkpoint', NOW.isoformat())
            store.set('last_success', NOW.isoformat())
            store.set('import_history_days', 30)
            store.set('traffic_data', {'sessions': 123})
            store = Store(path)
            self.assertIsNone(store.get('checkpoint'))
            self.assertIsNone(store.get('last_success'))
            self.assertEqual(store.get('tracked_skus'), ['FLP-O-COUPON-349', 'FLP-O-COUPON-399', 'FLP-O-COUPON-449', 'FLP-O-COUPON-379'])
            self.assertEqual(store.get('traffic_data'), {'sessions': 123})
            with store.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM orders').fetchone()[0], 1)

            class FakeShopify:
                fail = True
                searches = []

                async def graphql(self, query):
                    return {'shop': {'currencyCode': 'USD', 'ianaTimezone': 'UTC', 'name': 'Test'}}

                async def orders(self, search):
                    self.searches.append(search)
                    yield [order()]
                    if self.fail:
                        raise ShopifyError('next page unavailable')

            fake = FakeShopify()
            settings = Settings('test', 'password', shop='test.myshopify.com', access_token='test')
            with patch('app.sync.utcnow', return_value=NOW):
                await Sync(settings, store, fake).once()
                self.assertIsNone(store.get('checkpoint'))
                self.assertIsNone(store.get('last_success'))
                fake.fail = False
                store = Store(path)
                await Sync(settings, store, fake).once()
            self.assertTrue(all(' updated_at:>=' not in search for search in fake.searches))
            self.assertTrue(all(all(f"sku:'{sku}'" in search for sku in TRACKED_SKUS) for search in fake.searches))
            self.assertEqual(store.get('checkpoint'), NOW.isoformat())
            self.assertIsNotNone(store.get('last_success'))
            self.assertEqual(store.dashboard(7, 'UTC', NOW)['totals']['orders'], 2)
            # Once imported, an unchanged scope must preserve its checkpoint.
            self.assertEqual(Store(path).get('checkpoint'), NOW.isoformat())
            with patch('app.sync.utcnow', return_value=NOW):
                await Sync(settings, store, fake).once()
            self.assertNotIn('sku:', fake.searches[-1])

    async def test_refund_migration_repairs_unchanged_cached_orders_before_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp + '/db.sqlite3')
            store.upsert_orders([order()])
            with store.connect() as db:
                db.execute('UPDATE lines SET original_quantity=NULL, refunded_quantity=NULL')
            self.assertFalse(store.refunds_ready())
            self.assertFalse(store.dashboard(7, 'UTC', NOW)['refunds_ready'])
            store.set('checkpoint', '2026-10-19T11:00:00+00:00')
            store.set('import_history_days', 30)
            updated = order(lines=[line('line-order-1', quantity=0, original=2)],
                displayFinancialStatus='REFUNDED', refunds=[refund(('line-order-1', 2))])

            class FakeShopify:
                async def orders_by_ids(self, ids):
                    self.ids = ids
                    return [updated]

                async def orders(self, search):
                    yield []

            fake = FakeShopify()
            sync = Sync(Settings('test', 'password', shop='test.myshopify.com', access_token='test'), store, fake)
            await sync.once()
            self.assertEqual(fake.ids, ['order-1'])
            self.assertTrue(store.refunds_ready())
            item = store.dashboard(7, 'UTC', NOW)['skus'][0]
            self.assertEqual((item['quantity'], item['orders'], item['refunded_quantity']), (0, 1, 2))
            self.assertIsNone(store.get('last_error'))

    async def test_refund_line_pagination_and_multiple_refunds(self):
        calls = []
        first = order(refunds=[{'id':'refund-a'}, {'id':'refund-b'}])

        def handler(request):
            body = json.loads(request.content)
            calls.append(body)
            if 'CachedOrders' in body['query']:
                return httpx.Response(200, json={'data':{'nodes':[copy.deepcopy(first)]}})
            self.assertIn('query RefundLines', body['query'])
            more = body['variables']['id'] == 'refund-a' and body['variables']['after'] is None
            return httpx.Response(200, json={'data':{'refund':{'refundLineItems':{
                'nodes':[{'quantity':1, 'lineItem':{'id':'line-order-1'}}],
                'pageInfo':{'hasNextPage':more, 'endCursor':'next-refund-line'}}}}})

        api = Shopify(Settings('test', 'password', shop='test.myshopify.com', access_token='test'),
                      httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        rows = await api.orders_by_ids(['order-1'])
        self.assertEqual([len(r['refundLineItems']['nodes']) for r in rows[0]['refunds']], [2,1])
        self.assertEqual(calls[2]['variables']['after'], 'next-refund-line')
        self.assertEqual(calls[3]['variables']['id'], 'refund-b')
        await api.close()

    async def test_missing_refund_fails_sync_instead_of_silently_reporting_zero(self):
        api = Shopify(Settings('test', 'password', shop='test.myshopify.com', access_token='test'),
                      httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
                          httpx.Response(200, json={'data':{'refund':None}}))))
        with self.assertRaises(ShopifyError):
            await api.complete_refunds(order(refunds=[{'id':'gone'}]))
        await api.close()

    async def test_status_repair_refreshes_unchanged_cached_orders_and_retries_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp + "/db.sqlite3")
            store.upsert_orders([order()])
            with store.connect() as db:
                db.execute("UPDATE orders SET financial_status='UNKNOWN', eligible=0")
            checkpoint = "2026-10-19T11:00:00+00:00"
            store.set("checkpoint", checkpoint)
            store.set("import_history_days", 30)

            class FakeShopify:
                fail = True
                requested_ids = []

                async def orders_by_ids(self, ids):
                    self.requested_ids.append(ids)
                    if self.fail:
                        raise ShopifyError("temporary repair failure")
                    return [order(displayFinancialStatus="AUTHORIZED")]

                async def orders(self, search):
                    yield []

            fake = FakeShopify()
            sync = Sync(Settings("ros", "password", shop="test.myshopify.com", access_token="test"), store, fake)
            with patch("app.sync.utcnow", return_value=NOW):
                await sync.once()
                self.assertEqual(store.get("checkpoint"), checkpoint)
                self.assertEqual(store.orders_needing_refresh(), ["order-1"])
                fake.fail = False
                await sync.once()
            self.assertEqual(fake.requested_ids, [["order-1"], ["order-1"]])
            self.assertEqual(store.orders_needing_refresh(), [])
            self.assertEqual(store.get("checkpoint"), NOW.isoformat())
            item = store.dashboard(7, "UTC", NOW)["skus"][0]
            self.assertEqual(item["orders"], 1)
            self.assertEqual(item["authorized_orders"], 1)
            self.assertIsNone(store.get("last_error"))

    async def test_refetch_by_id_paginates_lines_and_handles_deleted_orders(self):
        first = order(displayFinancialStatus="AUTHORIZED")
        first["lineItems"]["pageInfo"] = {"hasNextPage": True, "endCursor": "next-line"}

        def handler(request):
            body = json.loads(request.content)
            if "CachedOrders" in body["query"]:
                return httpx.Response(200, json={"data": {"nodes": [copy.deepcopy(first), None]}})
            return httpx.Response(200, json={"data": {"order": {"lineItems": {
                "nodes": [line("extra", sku="FLP-O-COUPON-349")],
                "pageInfo": {"hasNextPage": False, "endCursor": None}}}}})

        api = Shopify(Settings("ros", "password", shop="test.myshopify.com", access_token="test"),
                      httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        rows = await api.orders_by_ids(["order-1", "deleted"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["lineItems"]["nodes"]), 2)
        self.assertEqual(rows[0]["displayFinancialStatus"], "AUTHORIZED")
        await api.close()

    async def test_token_cached_and_refreshed_before_expiry(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"access_token": "secret-token", "expires_in": 3600})

        api = Shopify(Settings("ros", "password", shop="test.myshopify.com", client_id="client", client_secret="secret"),
                      httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        self.assertEqual(await api.access_token(), "secret-token")
        await api.access_token()
        self.assertEqual(len(calls), 1)
        api.expires_at = 0
        await api.access_token()
        self.assertEqual(len(calls), 2)
        self.assertIn(b"grant_type=client_credentials", calls[0].content)
        await api.close()

    async def test_paginates_orders_and_nested_line_items(self):
        first = order()
        first["lineItems"]["pageInfo"] = {"hasNextPage": True, "endCursor": "line-cursor"}
        calls = []

        def handler(request):
            body = json.loads(request.content)
            calls.append(body)
            if "query Lines" in body["query"]:
                return httpx.Response(200, json={"data": {"order": {"lineItems": {"nodes": [line("extra", P2)], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}})
            second = body["variables"].get("after") == "order-cursor"
            return httpx.Response(200, json={"data": {"orders": {"nodes": [order("order-2")] if second else [copy.deepcopy(first)],
                "pageInfo": {"hasNextPage": not second, "endCursor": "order-cursor"}}}})

        api = Shopify(Settings("ros", "password", shop="test.myshopify.com", access_token="test"),
                      httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        pages = [page async for page in api.orders("filter")]
        self.assertEqual(len(pages), 2)
        self.assertEqual(len(pages[0][0]["lineItems"]["nodes"]), 2)
        self.assertEqual(calls[1]["variables"]["after"], "line-cursor")
        await api.close()

    async def test_throttling_retries_but_permission_errors_do_not_leak(self):
        responses = [httpx.Response(200, json={"errors": [{"extensions": {"code": "THROTTLED"}}]}),
                     httpx.Response(200, json={"data": {"ok": True}})]
        api = Shopify(Settings("ros", "password", shop="test.myshopify.com", access_token="sensitive"),
                      httpx.AsyncClient(transport=httpx.MockTransport(lambda request: responses.pop(0))))
        with patch("app.shopify.asyncio.sleep", new_callable=AsyncMock):
            self.assertEqual(await api.graphql("query"), {"ok": True})
        await api.close()

    async def test_failed_import_does_not_advance_checkpoint_and_restart_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp + "/db.sqlite3")
            checkpoint = "2026-10-19T09:00:00+00:00"
            store.set("checkpoint", checkpoint)
            store.set("import_history_days", 30)

            class FakeShopify:
                fail = True
                searches = []

                async def orders(self, search):
                    self.searches.append(search)
                    yield [order()]
                    if self.fail:
                        raise ShopifyError("second page unavailable")

            fake = FakeShopify()
            sync = Sync(Settings("ros", "password", shop="test.myshopify.com", access_token="test"), store, fake)
            with patch("app.sync.utcnow", return_value=NOW):
                await sync.once()
                self.assertEqual(store.get("checkpoint"), checkpoint)
                self.assertEqual(store.get("last_error"), "second page unavailable")
                fake.fail = False
                await sync.once()
            self.assertEqual(store.get("checkpoint"), NOW.isoformat())
            self.assertIsNone(store.get("last_error"))
            self.assertIn("updated_at:>='2026-10-19T08:55:00+00:00'", fake.searches[1])
            with store.connect() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM orders").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
