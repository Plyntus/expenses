import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.config import Settings
from app.shopify import ShopifyError
from app.store import Store
from app.traffic import TrafficSync, dashboard, dates

NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)


class TrafficTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name + '/db.sqlite3')
        self.settings = Settings('test', 'password', shop='test.myshopify.com', access_token='test')
        labels = dates(30, self.settings.timezone, NOW)
        self.result = {'shopifyqlQuery': {'parseErrors': [], 'tableData': {
            'columns': [{'name': 'day'}, {'name': 'sessions'}],
            'rows': [{'day': day, 'sessions': str(index)} for index, day in enumerate(labels)]}}}
        owner = self

        class FakeShopify:
            async def graphql(self, query, variables):
                self.query, self.variables = query, variables
                if isinstance(owner.result, Exception):
                    raise owner.result
                return copy.deepcopy(owner.result)

        self.api = FakeShopify()
        self.worker = TrafficSync(self.settings, self.store, self.api)

    def tearDown(self):
        self.tmp.cleanup()

    async def run_sync(self):
        with patch('app.traffic.utcnow', return_value=NOW):
            await self.worker.once()

    async def test_fixed_page_zero_days_timezone_and_period_totals(self):
        await self.run_sync()
        query = self.api.variables['query']
        self.assertIn("('/pages/flipper-one', '/pages/flipper-one/')", query)
        self.assertIn("WITH TIMEZONE 'Europe/Belgrade'", query)
        self.assertIn('SINCE 2026-09-20 UNTIL 2026-10-19', query)
        for count in (1, 7, 30):
            result = dashboard(self.store, self.settings, count, NOW)
            self.assertTrue(result['available'])
            self.assertFalse(result['stale'])
            self.assertEqual(result['sessions'], sum(range(30-count, 30)))
            self.assertEqual(len(result['daily']), count)
        self.assertEqual(dashboard(self.store, self.settings, 30, NOW)['daily'][0]['sessions'], 0)

    async def test_errors_keep_last_report_and_do_not_change_sales_checkpoint(self):
        await self.run_sync()
        previous = self.store.get('traffic_data')
        self.store.set('checkpoint', 'sales-checkpoint')
        for result in (ShopifyError('ACCESS_DENIED'),
                       {'shopifyqlQuery': {'parseErrors': ['invalid query'], 'tableData': None}}):
            self.result = result
            await self.run_sync()
            self.assertEqual(self.store.get('traffic_data'), previous)
            self.assertEqual(self.store.get('checkpoint'), 'sales-checkpoint')
            self.assertIsNotNone(dashboard(self.store, self.settings, 7, NOW)['last_error'])
            self.assertTrue(dashboard(self.store, self.settings, 7, NOW)['available'])

    async def test_incomplete_or_invalid_rows_never_replace_a_good_report(self):
        await self.run_sync()
        original = copy.deepcopy(self.result)
        before = self.store.get('traffic_data')
        invalid = [[], original['shopifyqlQuery']['tableData']['rows'][:-1],
                   [{'day': '2099-01-01', 'sessions': '1'}], [{'day': '2026-10-19', 'sessions': '-1'}],
                   [{'day': '2026-10-19', 'sessions': 'not-a-count'}]]
        for rows in invalid:
            self.result = copy.deepcopy(original)
            self.result['shopifyqlQuery']['tableData']['rows'] = rows
            await self.run_sync()
            self.assertEqual(self.store.get('traffic_data'), before)
        self.result = original
        await self.run_sync()
        self.assertIsNone(self.store.get('traffic_error'))

    async def test_midnight_and_changed_timezone_do_not_report_partial_totals(self):
        await self.run_sync()
        next_day = datetime(2026, 10, 19, 23, 30, tzinfo=timezone.utc)
        self.assertEqual(dates(1, 'Europe/Belgrade', next_day), ['2026-10-20'])
        result = dashboard(self.store, self.settings, 7, next_day)
        self.assertFalse(result['available'])
        self.assertIsNone(result['sessions'])
        self.assertEqual(result['daily'], [])
        cached = self.store.get('traffic_data')
        cached['timezone'] = 'UTC'
        self.store.set('traffic_data', cached)
        self.assertFalse(dashboard(self.store, self.settings, 7, NOW)['available'])

    async def test_unconfigured_and_uncached_analytics_is_not_zero_traffic(self):
        await TrafficSync(Settings('test', 'password'), self.store, self.api).once()
        self.assertFalse(hasattr(self.api, 'query'))
        result = dashboard(self.store, self.settings, 1, NOW)
        self.assertFalse(result['available'])
        self.assertIsNone(result['sessions'])

    async def test_stale_cache_remains_visible_with_timestamp_and_stale_flag(self):
        await self.run_sync()
        result = dashboard(self.store, self.settings, 7, NOW + timedelta(minutes=4))
        self.assertTrue(result['available'])
        self.assertTrue(result['stale'])
        self.assertEqual(result['last_success'], NOW.isoformat())
