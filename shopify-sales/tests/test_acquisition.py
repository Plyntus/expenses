import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.acquisition import AcquisitionSync, ATTRIBUTION_QUERY, dashboard, hour_floor, hour_starts, midnight, parse_sessions
from app.config import Settings
from app.shopify import ShopifyError
from app.store import Store
from app.traffic import LANDING_PAGE, dates
from test_sales import order, line

NOW = datetime(2026,10,19,12,30,tzinfo=timezone.utc)


def journey(medium='email', path='/pages/flipper-one/', ready=True, stamp='2026-10-19T10:15:00Z'):
    return {'ready':ready, 'lastVisit':{'occurredAt':stamp,
        'landingPage':'https://flipper.net'+path+'?utm_medium='+str(medium)+'&private=do-not-store',
        'utmParameters': {'medium':medium}} if ready else None}


class AcquisitionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(self.tmp.name+'/db.sqlite3')
        self.store.set('last_success', NOW.isoformat())
        self.settings=Settings('test','password',shop='test.myshopify.com',access_token='test')
        self.nodes={}
        self.calls=[]
        owner=self

        class API:
            async def graphql(self, query, variables):
                owner.calls.append((query,variables))
                if query==ATTRIBUTION_QUERY:
                    return {'nodes':[{'id':i,'customerJourneySummary':owner.nodes.get(i)} for i in variables['ids']]}
                if isinstance(owner.report,Exception):
                    raise owner.report
                return copy.deepcopy(owner.report)

        self.api=API()
        self.worker=AcquisitionSync(self.settings,self.store,self.api)
        self.cache(days=7)

    def tearDown(self):
        self.tmp.cleanup()

    def cache(self,days=1,now=NOW):
        start=midnight(dates(days,self.settings.timezone,now)[0],self.settings.timezone)
        hourly=[{'start_at':h.isoformat(),'email_sessions':0,'other_sessions':0} for h in hour_starts(start,now)]
        self.store.set('acquisition_sessions',{'path':LANDING_PAGE,'timezone':self.settings.timezone,
            'start_at':start.isoformat(),'end_at':now.isoformat(),'last_success':now.isoformat(),'hourly':hourly})
        rows=[{'hour':h['start_at'],'utm_medium':None,'sessions':'0'} for h in hourly]
        self.report={'shopifyqlQuery':{'parseErrors':[],'tableData':{'columns':[{'name':x} for x in ('hour','utm_medium','sessions')],'rows':rows}}}
        return hourly

    def sessions(self,at,email,other):
        cache=self.store.get('acquisition_sessions')
        for h in cache['hourly']:
            if h['start_at']==hour_floor(at).isoformat():
                h.update(email_sessions=email,other_sessions=other)
        self.store.set('acquisition_sessions',cache)

    async def sources(self,now=NOW):
        with patch('app.acquisition.utcnow',return_value=now):
            await self.worker.refresh_attribution()

    async def test_tracks_skus_once_per_order_and_uses_exact_medium_and_page(self):
        self.store.upsert_orders([
            order('email',lines=[line('a'),line('b',sku='FLP-O-COUPON-399')]),
            order('other'),order('elsewhere'),order('pending'),order('unlinked'),
            order('irrelevant',lines=[line('ignore',sku='UNTRACKED')])])
        self.nodes={'email':journey(),'other':journey(medium=None),'elsewhere':journey(path='/products/another'),
            'pending':journey(ready=False),'unlinked':{'ready':True,'lastVisit':None}}
        self.sessions(datetime(2026,10,19,10,tzinfo=timezone.utc),2,3)
        await self.sources()
        data=dashboard(self.store,self.settings,1,NOW)
        self.assertEqual((data['daily'][0]['email_orders'],data['daily'][0]['other_orders']),(1,1))
        self.assertEqual((data['pending_orders'],data['unlinked_orders'],data['other_landing_page_orders']),(1,1,1))
        hour=next(h for h in data['hourly'] if h['start_at']=='2026-10-19T10:00:00+00:00')
        self.assertEqual(hour['email_conversion'],50)
        self.assertAlmostEqual(hour['other_conversion'],100/3)
        with self.store.connect() as db:
            saved=[dict(r) for r in db.execute('SELECT * FROM order_attribution')]
        self.assertNotIn('private',json.dumps(saved))
        self.assertNotIn('utm_medium',json.dumps(saved))
        self.assertNotIn('irrelevant',[r['order_id'] for r in saved])

    async def test_pending_is_retried_without_an_order_update_and_never_becomes_other(self):
        self.store.upsert_orders([order()])
        self.nodes={'order-1':journey(ready=False)}
        await self.sources()
        first=dashboard(self.store,self.settings,1,NOW)
        self.assertEqual(first['pending_orders'],1)
        self.assertFalse(first['purchases_available'])
        self.assertFalse(first['conversion_available'])
        before=len(self.calls)
        await self.sources(NOW+timedelta(minutes=1))
        self.assertEqual(len(self.calls),before)
        self.nodes['order-1']=journey()
        await self.sources(NOW+timedelta(minutes=6))
        self.assertEqual(len(self.calls),before+1)
        result=dashboard(self.store,self.settings,1,NOW+timedelta(minutes=6))
        self.assertEqual(result['pending_orders'],0)
        self.assertEqual(result['daily'][0]['email_orders'],1)
        self.assertEqual(result['daily'][0]['other_orders'],0)

    async def test_order_date_and_session_hour_are_kept_distinct_across_midnight(self):
        self.store.upsert_orders([order(createdAt='2026-10-18T22:10:00Z')])
        self.nodes={'order-1':journey(stamp='2026-10-18T21:50:00Z')}
        self.sessions(datetime(2026,10,18,21,tzinfo=timezone.utc),4,0)
        await self.sources()
        today=dashboard(self.store,self.settings,1,NOW)
        self.assertEqual(today['daily'][0]['email_orders'],1)
        self.assertEqual(sum(h['email_orders'] for h in today['hourly']),0)
        week=dashboard(self.store,self.settings,7,NOW)
        self.assertEqual(sum(h['email_orders'] for h in week['hourly']),1)
        converted=next(h for h in week['hourly'] if h['email_orders'])
        self.assertEqual(converted['email_conversion'],25)

    async def test_zero_sessions_is_null_and_orders_per_session_is_not_capped(self):
        self.store.upsert_orders([order(str(i)) for i in range(3)])
        self.nodes={str(i):journey() for i in range(3)}
        await self.sources()
        result=dashboard(self.store,self.settings,1,NOW)
        h=next(h for h in result['hourly'] if h['email_orders'])
        self.assertIsNone(h['email_conversion'])
        self.sessions(datetime(2026,10,19,10,tzinfo=timezone.utc),1,0)
        result=dashboard(self.store,self.settings,1,NOW)
        self.assertEqual(next(h for h in result['hourly'] if h['email_orders'])['email_conversion'],300)

    async def test_refunds_remain_orders_and_cancelled_test_voided_stay_excluded(self):
        self.store.upsert_orders([order('refund',displayFinancialStatus='REFUNDED',lines=[line('refund',quantity=0,original=1)]),
            order('void',displayFinancialStatus='VOIDED'),order('test',test=True),
            order('cancel',cancelledAt='2026-10-19T11:00:00Z')])
        self.nodes={'refund':journey(medium='Email')}
        await self.sources()
        result=dashboard(self.store,self.settings,1,NOW)
        self.assertEqual(result['matched_orders'],1)
        self.assertEqual(result['daily'][0]['other_orders'],1)
        self.assertEqual(result['daily'][0]['email_orders'],0)

    async def test_analytics_failure_preserves_cache_but_order_sources_still_refresh(self):
        self.store.upsert_orders([order()]);self.nodes={'order-1':journey()}
        before=self.store.get('acquisition_sessions');self.store.set('checkpoint','sales-checkpoint')
        self.report=ShopifyError('ACCESS_DENIED')
        with patch('app.acquisition.utcnow',return_value=NOW):
            await self.worker.once()
        self.assertEqual(self.store.get('acquisition_sessions'),before)
        self.assertEqual(self.store.get('checkpoint'),'sales-checkpoint')
        self.assertIsNotNone(self.store.get('acquisition_sessions_error'))
        self.assertIsNone(self.store.get('attribution_error'))
        self.assertEqual(dashboard(self.store,self.settings,1,NOW)['matched_orders'],1)

    async def test_hourly_report_validation_and_utc_boundaries(self):
        self.cache(days=30)
        with patch('app.acquisition.utcnow',return_value=NOW):
            await self.worker.refresh_sessions()
        query=self.calls[-1][1]['query']
        self.assertIn("WITH TIMEZONE 'UTC'",query)
        self.assertIn('SINCE 2026-09-19T22:00:00 UNTIL 2026-10-19T12:30:00',query)
        result=self.report['shopifyqlQuery'];start=midnight('2026-09-20','Europe/Belgrade')
        self.assertGreater(len(parse_sessions(result,start,NOW)),700)
        result['tableData']['rows'].pop()
        with self.assertRaises(ValueError):parse_sessions(result,start,NOW)

    async def test_fall_back_hours_remain_separate(self):
        now=datetime(2026,10,25,22,30,tzinfo=timezone.utc)
        self.cache(days=1,now=now)
        start=midnight('2026-10-25','Europe/Belgrade')
        rows=self.report['shopifyqlQuery']['tableData']['rows']
        for row in rows:
            if row['hour'] in ('2026-10-25T00:00:00+00:00','2026-10-25T01:00:00+00:00'):
                row['sessions']='2'
        parsed=parse_sessions(self.report['shopifyqlQuery'],start,now)
        self.assertEqual(len(parsed),25)
        self.assertEqual(len([h for h in parsed if h['other_sessions']]),2)

    async def test_updated_orders_refresh_and_pruning_cascades_source_rows(self):
        self.store.upsert_orders([order()]);self.nodes={'order-1':journey()}
        await self.sources();calls=len(self.calls)
        self.store.upsert_orders([order(updatedAt='2026-10-19T12:01:00Z')])
        self.nodes['order-1']=journey(medium=None)
        await self.sources()
        self.assertEqual(len(self.calls),calls+1)
        self.assertEqual(dashboard(self.store,self.settings,1,NOW)['daily'][0]['other_orders'],1)
        self.store.prune('2026-10-20T00:00:00+00:00')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM order_attribution').fetchone()[0],0)

    async def test_medium_can_be_read_directly_from_last_visit_url(self):
        self.store.upsert_orders([order()])
        visit=journey()['lastVisit']
        visit['utmParameters']=None
        self.nodes={'order-1':{'ready':True,'lastVisit':visit}}
        await self.sources()
        self.assertEqual(dashboard(self.store,self.settings,1,NOW)['daily'][0]['email_orders'],1)

    async def test_charts_do_not_show_zero_before_the_first_order_import(self):
        self.store.set('last_success', None)
        result=dashboard(self.store,self.settings,1,NOW)
        self.assertFalse(result['purchases_available'])
        self.assertFalse(result['conversion_available'])
