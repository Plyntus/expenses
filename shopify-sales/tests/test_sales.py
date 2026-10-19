import asyncio
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
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


def line(line_id="line-1", product=P1, amount="18.90", quantity=2, sku=TRACKED_SKUS[0]):
    return {"id": line_id, "product": {"id": product}, "title": "Flipper One Pre-order", "sku": sku,
            "currentQuantity": quantity, "priceAfterAllDiscountsBeforeTaxesSet": {
                "shopMoney": {"amount": amount, "currencyCode": "USD"}}}


def order(order_id="order-1", lines=None, **kwargs):
    return {"id": order_id, "createdAt": "2026-10-19T10:00:00Z", "updatedAt": "2026-10-19T10:01:00Z",
            "cancelledAt": None, "test": False, "displayFinancialStatus": "PAID",
            "lineItems": {"nodes": lines or [line("line-" + order_id)], "pageInfo": {"hasNextPage": False, "endCursor": None}}, **kwargs}


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
        self.assertEqual(totals, {"revenue": 19.0, "quantity": 3, "orders": 1})
        self.assertEqual(len(self.dashboard()["daily"]), 7)
        skus = self.dashboard()["skus"]
        self.assertEqual([s["sku"] for s in skus], list(TRACKED_SKUS))
        self.assertEqual([s["revenue"] for s in skus], [18.9, 0.1, 0, 0])
        self.assertEqual([s["orders"] for s in skus], [1, 1, 0, 0])
        self.assertEqual(skus[0]["daily"][-1]["orders"], 1)

    def test_partial_then_full_refund_and_cancel_replace_original(self):
        self.store.upsert_orders([order()])
        self.store.upsert_orders([order(lines=[line(amount="9.45", quantity=1)], displayFinancialStatus="PARTIALLY_REFUNDED")])
        self.assertEqual(self.dashboard()["totals"], {"revenue": 9.45, "quantity": 1, "orders": 1})
        self.store.upsert_orders([order(lines=[line(amount="0", quantity=0)], displayFinancialStatus="REFUNDED")])
        self.assertEqual(self.dashboard()["totals"]["quantity"], 0)
        self.store.upsert_orders([order(cancelledAt="2026-10-19T11:00:00Z")])
        self.assertEqual(self.dashboard()["totals"]["revenue"], 0)

    def test_unpaid_cancelled_test_orders_are_excluded(self):
        self.store.upsert_orders([order("test", test=True), order("pending", displayFinancialStatus="PENDING"),
                                  order("partial", displayFinancialStatus="PARTIALLY_PAID"), order("cancelled", cancelledAt="2026-10-19T11:00:00Z")])
        self.assertEqual(self.dashboard()["totals"]["orders"], 0)

    def test_timezone_and_period_boundaries(self):
        self.store.upsert_orders([order(createdAt="2026-10-18T23:30:00Z")])
        self.assertEqual(self.dashboard(1)["totals"]["orders"], 1)
        self.assertEqual(self.dashboard(1, "UTC")["totals"]["orders"], 0)

    def test_migration_removes_untracked_history_but_keeps_tracked_data_and_checkpoint(self):
        self.store.upsert_orders([order()])
        self.store.set("checkpoint", "2026-10-19T11:00:00+00:00")
        with self.store.connect() as db:
            db.execute("CREATE TABLE products (id TEXT PRIMARY KEY, title TEXT)")
            db.execute("INSERT INTO products VALUES ('old-product', 'Untracked product')")
            db.execute("INSERT INTO orders VALUES ('untracked-order','2026-10-19T10:00:00+00:00','2026-10-19T10:01:00+00:00',1)")
            db.execute("INSERT INTO lines VALUES ('untracked-line','untracked-order','other','Other','OTHER-SKU',5,'5000','USD')")
            db.execute("INSERT INTO lines VALUES ('mixed-line','order-1','other','Other','OTHER-SKU',5,'5000','USD')")
        reopened = Store(self.store.path)
        self.assertEqual(reopened.get("checkpoint"), "2026-10-19T11:00:00+00:00")
        with reopened.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM lines").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM orders").fetchone()[0], 1)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='products'").fetchone())
        self.assertEqual(self.dashboard()["skus"][0]["revenue"], 18.9)

    def test_exact_sku_filter_drops_other_lines_and_unrelated_orders(self):
        self.store.upsert_orders([
            order(lines=[line(), line("other", sku="FLP-O-COUPON-349-EXTRA", amount="9999")]),
            order("unknown", lines=[line("unknown", sku=None)]),
            order("different", lines=[line("different", sku="FZ")]),
        ])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM lines").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM orders").fetchone()[0], 1)
        self.assertEqual(self.dashboard()["totals"]["revenue"], 18.9)

    def test_order_edit_removes_previous_sku_and_untracked_orders(self):
        self.store.upsert_orders([order()])
        self.store.upsert_orders([order(lines=[line(sku=TRACKED_SKUS[3])])])
        self.assertEqual([s["quantity"] for s in self.dashboard()["skus"]], [0, 0, 0, 2])
        self.store.upsert_orders([order(lines=[line(sku="UNTRACKED")])])
        self.assertEqual(self.dashboard()["totals"]["quantity"], 0)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM orders").fetchone()[0], 0)

    def test_all_four_variants_of_same_product_are_separate(self):
        self.store.upsert_orders([order(lines=[line(str(i), P1, str((i + 1) * 10), i + 1, sku=sku)
                                              for i, sku in enumerate(TRACKED_SKUS)])])
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

    def test_health_only_is_public(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        for path in ("/", "/assets/app.js", "/api/status", "/api/dashboard"):
            self.assertEqual(self.client.get(path).status_code, 401, path)
        self.assertEqual(self.client.get("/api/dashboard", auth=self.auth).status_code, 200)

    def test_setup_before_credentials_and_all_assets(self):
        response = self.client.get("/api/status", auth=self.auth)
        self.assertFalse(response.json()["configured"])
        for path in ("/", "/assets/style.css", "/assets/app.js", "/assets/chart.umd.js"):
            self.assertEqual(self.client.get(path, auth=self.auth).status_code, 200, path)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

    def test_fixed_sku_api_does_not_allow_other_products(self):
        status = self.client.get("/api/status", auth=self.auth).json()
        self.assertEqual(status["tracked_skus"], list(TRACKED_SKUS))
        data = self.client.get("/api/dashboard", auth=self.auth).json()
        self.assertEqual([s["sku"] for s in data["skus"]], list(TRACKED_SKUS))
        self.assertTrue(all(s["quantity"] == 0 for s in data["skus"]))
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
