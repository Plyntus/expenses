import asyncio
import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

from .shopify import ShopifyError
from .store import parse_date, utcnow

log = logging.getLogger(__name__)
LANDING_PAGE = '/pages/flipper-one/'
SEGMENTATION = 'utm_medium_email_vs_other'
ROW_LIMIT = 10000
QUERY = '''query LandingPageSessions($query: String!) {
    shopifyqlQuery(query: $query) {
        tableData { columns { name } rows } parseErrors
    }
}'''


def dates(days, timezone, now):
    today = now.astimezone(ZoneInfo(timezone)).date()
    return [(today - timedelta(days=i)).isoformat() for i in reversed(range(days))]


class TrafficSync:
    """Independent analytics polling preserves sales syncing and the last good report."""

    def __init__(self, settings, store, shopify):
        self.settings, self.store, self.shopify = settings, store, shopify
        self.running = False

    async def once(self):
        if self.running or not self.settings.configured:
            return
        self.running = True
        try:
            labels = dates(self.settings.history_days, self.settings.timezone, utcnow())
            query = (
                'FROM sessions SHOW sessions '
                "WHERE landing_page_path IN ('/pages/flipper-one', '/pages/flipper-one/') "
                f"GROUP BY utm_medium TIMESERIES day WITH TIMEZONE '{self.settings.timezone}' "
                f'SINCE {labels[0]} UNTIL {labels[-1]} ORDER BY day ASC LIMIT {ROW_LIMIT}'
            )
            result = (await self.shopify.graphql(QUERY, {'query': query}))['shopifyqlQuery']
            if not result or result.get('parseErrors'):
                raise ShopifyError('Shopify could not prepare the landing-page sessions report')
            table = result.get('tableData')
            if not table or not {'day', 'sessions', 'utm_medium'}.issubset({c['name'] for c in table['columns']}):
                raise ValueError('Incomplete analytics response')
            if len(table['rows']) >= ROW_LIMIT:
                raise ValueError('Analytics report may be truncated')
            daily = {}
            seen = set()
            for row in table['rows']:
                day = row['day']
                medium = row['utm_medium']
                count = int(row['sessions'])
                if day not in labels or (day, medium) in seen or count < 0:
                    raise ValueError('Invalid analytics row')
                seen.add((day, medium))
                values = daily.setdefault(day, {'sessions': 0, 'email_sessions': 0, 'other_sessions': 0})
                values['sessions'] += count
                # Missing/empty tags and every value other than exactly "email"
                # belong to Other. Do not infer email from a marketing channel.
                values['email_sessions' if medium == 'email' else 'other_sessions'] += count
            # TIMESERIES supplies zero days. Missing days must not look like zero traffic.
            if set(daily) != set(labels):
                raise ValueError('Incomplete analytics date range')
            self.store.set('traffic_data', {'path': LANDING_PAGE, 'timezone': self.settings.timezone,
                'segmentation': SEGMENTATION,
                'daily': [{'date': day, **daily[day]} for day in labels],
                'last_success': utcnow().isoformat()})
            self.store.set('traffic_error', None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = ('Shopify analytics is unavailable. Check read_reports and analytics access.'
                       if isinstance(exc, ShopifyError) else 'Sessions report could not be refreshed; retrying next minute.')
            self.store.set('traffic_error', message)
            log.error('Shopify analytics sync failed (%s)', type(exc).__name__)
        finally:
            self.running = False

    async def run(self):
        while True:
            started = asyncio.get_running_loop().time()
            await self.once()
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(1, self.settings.poll_seconds - elapsed))


def dashboard(store, settings, days, now=None):
    now = now or utcnow()
    labels = dates(days, settings.timezone, now)
    cached = store.get('traffic_data', {})
    daily = {item['date']: item for item in cached.get('daily', [])}
    available = (cached.get('path') == LANDING_PAGE and cached.get('timezone') == settings.timezone
                 and all(day in daily for day in labels))
    updated = cached.get('last_success')
    breakdown_available = (available and cached.get('segmentation') == SEGMENTATION
                           and all('email_sessions' in daily[day] and 'other_sessions' in daily[day] for day in labels))
    stale = not updated or (now - parse_date(updated)).total_seconds() > settings.poll_seconds * 3
    return {'path': LANDING_PAGE, 'metric': 'landing_page_sessions', 'timezone': settings.timezone,
            'days': days, 'available': available, 'stale': stale, 'last_success': updated,
            'breakdown_available': breakdown_available,
            'email_sessions': sum(daily[day]['email_sessions'] for day in labels) if breakdown_available else None,
            'other_sessions': sum(daily[day]['other_sessions'] for day in labels) if breakdown_available else None,
            'last_error': store.get('traffic_error'),
            'sessions': sum(daily[day]['sessions'] for day in labels) if available else None,
            'daily': [daily[day] for day in labels] if available else []}
