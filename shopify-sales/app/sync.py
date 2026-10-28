import asyncio
import logging
from datetime import timedelta

from .config import TRACKED_SKUS
from .shopify import ShopifyError
from .store import parse_date, utcnow

log = logging.getLogger(__name__)


class Sync:
    def __init__(self, settings, store, shopify):
        self.settings, self.store, self.shopify = settings, store, shopify
        self.running = False

    async def once(self):
        if self.running or not self.settings.configured:
            return
        self.running = True
        started = utcnow()
        self.store.set("last_attempt", started.isoformat())
        try:
            # Repair existing cache rows after adding payment/refund tracking, even if
            # Shopify hasn't changed those orders since the persisted checkpoint.
            while ids := self.store.orders_needing_refresh():
                orders = await self.shopify.orders_by_ids(ids)
                self.store.upsert_orders(orders)
                returned = {order["id"] for order in orders}
                self.store.remove_unavailable_orders(set(ids) - returned)
            cutoff = started - timedelta(days=self.settings.history_days)
            checkpoint = self.store.get("checkpoint")
            if self.settings.history_days > self.store.get("import_history_days", 0):
                checkpoint = None
            search = f"created_at:>='{cutoff.isoformat()}' updated_at:<='{started.isoformat()}'"
            if checkpoint:
                lower = parse_date(checkpoint) - timedelta(minutes=5)
                search += f" updated_at:>='{lower.isoformat()}'"
            else:
                # Limit the initial history import to matching orders. Incremental
                # polls stay unfiltered so edits that remove the coupon are caught.
                search += " (" + " OR ".join(f"sku:'{sku}'" for sku in TRACKED_SKUS) + ")"
                info = await self.shopify.graphql("query { shop { currencyCode ianaTimezone name } }")
                self.store.set("currency", info["shop"]["currencyCode"])
                self.store.set("shop_name", info["shop"]["name"])
                self.store.set("shop_timezone", info["shop"]["ianaTimezone"])
            count = 0
            async for orders in self.shopify.orders(search):
                self.store.upsert_orders(orders)
                count += len(orders)
                self.store.set("sync_progress", count)
            # Advance only after ALL order and line-item pages have been committed.
            self.store.set("checkpoint", started.isoformat())
            self.store.set("import_history_days", self.settings.history_days)
            self.store.set("last_success", utcnow().isoformat())
            self.store.set("last_error", None)
            self.store.prune(cutoff.isoformat())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = str(exc) if isinstance(exc, ShopifyError) else "Synchronization failed; retrying next poll"
            self.store.set("last_error", message)
            log.error("Shopify sync failed (%s)", type(exc).__name__)
        finally:
            self.running = False

    async def run(self):
        while True:
            started = asyncio.get_running_loop().time()
            await self.once()
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(1, self.settings.poll_seconds - elapsed))
