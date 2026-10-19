import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from .shopify import ShopifyError
from .store import parse_date, utcnow
from .traffic import LANDING_PAGE, QUERY, ROW_LIMIT, dates

log = logging.getLogger(__name__)
ATTRIBUTION_QUERY = '''query TrackedOrderSources($ids: [ID!]!) {
    nodes(ids: $ids) { ... on Order {
        id customerJourneySummary {
            ready lastVisit { occurredAt landingPage utmParameters { medium } }
        }
    } }
}'''


def midnight(day, tz_name):
    return datetime.combine(datetime.fromisoformat(day).date(), time.min, ZoneInfo(tz_name)).astimezone(timezone.utc)


def hour_floor(stamp):
    return stamp.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def hour_starts(start, end):
    values = []
    cursor = hour_floor(start)
    while cursor <= end:
        values.append(cursor)
        cursor += timedelta(hours=1)
    return values


def parse_sessions(result, start, end):
    if not result or result.get('parseErrors'):
        raise ShopifyError('Cannot prepare hourly sessions report')
    table = result.get('tableData')
    if not table or not {'hour', 'utm_medium', 'sessions'}.issubset({c['name'] for c in table['columns']}):
        raise ValueError('Incomplete hourly sessions response')
    if len(table['rows']) >= ROW_LIMIT:
        raise ValueError('Hourly report may be truncated')
    expected = {stamp.isoformat() for stamp in hour_starts(start, end)}
    buckets, seen = {}, set()
    for row in table['rows']:
        stamp = parse_date(row['hour'])
        if stamp.tzinfo is None or stamp != hour_floor(stamp):
            raise ValueError('Invalid hour timestamp')
        key = stamp.astimezone(timezone.utc).isoformat()
        medium = row['utm_medium']
        value = int(row['sessions'])
        if key not in expected or (key, medium) in seen or value < 0:
            raise ValueError('Invalid hourly group')
        seen.add((key, medium))
        bucket = buckets.setdefault(key, {'start_at': key, 'email_sessions': 0, 'other_sessions': 0})
        bucket['email_sessions' if medium == 'email' else 'other_sessions'] += value
    if set(buckets) != expected:
        raise ValueError('Missing hourly buckets')
    return [buckets[key] for key in sorted(buckets)]


class AcquisitionSync:
    def __init__(self, settings, store, shopify):
        self.settings, self.store, self.shopify = settings, store, shopify
        self.running = False

    async def refresh_sessions(self):
        now = utcnow().replace(microsecond=0)
        start = midnight(dates(self.settings.history_days, self.settings.timezone, now)[0], self.settings.timezone)
        query = (
            'FROM sessions SHOW sessions '
            "WHERE landing_page_path IN ('/pages/flipper-one', '/pages/flipper-one/') "
            "GROUP BY utm_medium TIMESERIES hour WITH TIMEZONE 'UTC' "
            f"SINCE {start.strftime('%Y-%m-%dT%H:%M:%S')} UNTIL {now.strftime('%Y-%m-%dT%H:%M:%S')} "
            f'ORDER BY hour ASC LIMIT {ROW_LIMIT}'
        )
        result = (await self.shopify.graphql(QUERY, {'query': query}))['shopifyqlQuery']
        hourly = parse_sessions(result, start, now)
        self.store.set('acquisition_sessions', {'timezone': self.settings.timezone, 'path': LANDING_PAGE,
            'start_at': start.isoformat(), 'end_at': now.isoformat(), 'hourly': hourly,
            'last_success': utcnow().isoformat()})

    async def refresh_attribution(self):
        # Retry unprepared Shopify journeys every five minutes, independently of
        # updated_at: becoming ready does not necessarily update the order itself.
        cycle = utcnow()
        retry_before = (cycle - timedelta(minutes=5)).isoformat()
        while True:
            with self.store.connect() as db:
                pending = db.execute('''SELECT o.id, o.updated_at FROM orders o
                    LEFT JOIN order_attribution a ON a.order_id=o.id
                    WHERE o.eligible=1 AND (a.order_id IS NULL OR a.order_updated_at!=o.updated_at
                        OR (a.status IN ('PENDING','UNATTRIBUTED') AND a.checked_at <= ?))
                    ORDER BY CASE WHEN a.order_id IS NULL THEN 0 ELSE 1 END, a.checked_at LIMIT 50''',
                    (retry_before,)).fetchall()
            if not pending:
                break
            versions = {row['id']: row['updated_at'] for row in pending}
            result = await self.shopify.graphql(ATTRIBUTION_QUERY, {'ids': list(versions)})
            nodes = {node['id']: node for node in result['nodes'] if node is not None}
            with self.store.connect() as db:
                for order_id, version in versions.items():
                    node = nodes.get(order_id)
                    summary = (node or {}).get('customerJourneySummary') or {}
                    visit = summary.get('lastVisit')
                    status, visit_at, matches, group = 'PENDING', None, 0, None
                    if node is None:
                        status = 'UNAVAILABLE'
                    elif summary.get('ready'):
                        status = 'UNATTRIBUTED'
                        if visit and visit.get('landingPage') and visit.get('occurredAt'):
                            stamp = parse_date(visit['occurredAt'])
                            if stamp.tzinfo is None:
                                raise ValueError('Visit has no timezone')
                            visit_at = stamp.astimezone(timezone.utc).isoformat()
                            matches = int(urlsplit(visit['landingPage']).path.rstrip('/') == LANDING_PAGE.rstrip('/'))
                            medium = (visit.get('utmParameters') or {}).get('medium')
                            if medium is None:
                                medium = parse_qs(urlsplit(visit['landingPage']).query, keep_blank_values=True).get('utm_medium', [None])[0]
                            group = 'email' if medium == 'email' else 'other'
                            status = 'READY'
                    # A sales poll may prune the order while the API request runs.
                    if db.execute('SELECT 1 FROM orders WHERE id=?', (order_id,)).fetchone():
                        db.execute('''INSERT INTO order_attribution
                            (order_id,status,visit_at,landing_match,medium_group,checked_at,order_updated_at)
                            VALUES (?,?,?,?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET
                            status=excluded.status, visit_at=excluded.visit_at, landing_match=excluded.landing_match,
                            medium_group=excluded.medium_group, checked_at=excluded.checked_at,
                            order_updated_at=excluded.order_updated_at''',
                            (order_id, status, visit_at, matches, group, cycle.isoformat(), version))
        self.store.set('attribution_last_success', utcnow().isoformat())

    async def once(self):
        if self.running or not self.settings.configured:
            return
        self.running = True
        try:
            for name, refresh in (('acquisition_sessions', self.refresh_sessions), ('attribution', self.refresh_attribution)):
                try:
                    await refresh()
                    self.store.set(name + '_error', None)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.store.set(name + '_error', 'Shopify source data could not be refreshed; retrying next minute.')
                    log.error('%s sync failed (%s)', name, type(exc).__name__)
        finally:
            self.running = False

    async def run(self):
        while True:
            started = asyncio.get_running_loop().time()
            await self.once()
            await asyncio.sleep(max(1, self.settings.poll_seconds - (asyncio.get_running_loop().time() - started)))


def dashboard(store, settings, days, now=None):
    now = now or utcnow()
    labels = dates(days, settings.timezone, now)
    start = midnight(labels[0], settings.timezone)
    daily = {day: {'date': day, 'email_orders': 0, 'other_orders': 0} for day in labels}
    cache = store.get('acquisition_sessions', {})
    cached_end = parse_date(cache['end_at']) if cache else None
    sessions_available = bool(cache and cache.get('path') == LANDING_PAGE
        and cache.get('timezone') == settings.timezone and parse_date(cache['start_at']) <= start
        and cached_end.astimezone(ZoneInfo(settings.timezone)).date().isoformat() == labels[-1])
    hourly = {}
    if sessions_available:
        for item in cache['hourly']:
            stamp = parse_date(item['start_at'])
            if stamp >= hour_floor(start) and stamp <= cached_end:
                hourly[item['start_at']] = {**item, 'end_at': min(stamp + timedelta(hours=1), cached_end).isoformat(),
                    'email_orders': 0, 'other_orders': 0}
    pending, unlinked, other_page, matched, awaiting_sessions = 0, 0, 0, 0, 0
    with store.connect() as db:
        rows = db.execute('''SELECT o.id,o.created_at,a.status,a.visit_at,a.landing_match,a.medium_group
            FROM orders o LEFT JOIN order_attribution a ON a.order_id=o.id
            WHERE o.eligible=1 AND EXISTS (SELECT 1 FROM lines l WHERE l.order_id=o.id
                AND COALESCE(l.original_quantity,l.quantity)>0)''').fetchall()
    for row in rows:
        created = parse_date(row['created_at'])
        if created > now:
            continue
        day = created.astimezone(ZoneInfo(settings.timezone)).date().isoformat()
        in_period = day in daily
        if row['status'] in (None, 'PENDING'):
            pending += int(in_period)
            continue
        if row['status'] != 'READY':
            unlinked += int(in_period)
            continue
        if not row['landing_match']:
            other_page += int(in_period)
            continue
        group = row['medium_group']
        if in_period:
            daily[day][group + '_orders'] += 1
            matched += 1
        visit = parse_date(row['visit_at'])
        key = hour_floor(visit).isoformat()
        if sessions_available and start <= visit <= cached_end and key in hourly:
            hourly[key][group + '_orders'] += 1
        elif in_period and visit >= start:
            awaiting_sessions += 1
    output = []
    for bucket in hourly.values():
        for group in ('email', 'other'):
            count = bucket[group + '_sessions']
            if not count:
                awaiting_sessions += bucket[group + '_orders']
            bucket[group + '_conversion'] = bucket[group + '_orders'] / count * 100 if count else None
        output.append(bucket)
    known_orders = sum(row['email_orders'] + row['other_orders'] for row in output)
    last_success = cache.get('last_success')
    stale = not last_success or (now - parse_date(last_success)).total_seconds() > settings.poll_seconds * 3
    return {'path': LANDING_PAGE, 'timezone': settings.timezone, 'days': days,
        'daily': list(daily.values()), 'hourly': output,
        'purchases_available': bool(store.get('last_success')) and (matched > 0 or pending == 0),
        'conversion_available': bool(store.get('last_success')) and sessions_available and (known_orders > 0 or pending == 0),
        'pending_orders': pending, 'unlinked_orders': unlinked, 'other_landing_page_orders': other_page,
        'matched_orders': matched, 'awaiting_sessions_orders': awaiting_sessions,
        'last_success': last_success, 'attribution_last_success': store.get('attribution_last_success'), 'stale': stale,
        'sessions_error': store.get('acquisition_sessions_error'), 'attribution_error': store.get('attribution_error')}
