import asyncio
import time

import httpx


class ShopifyError(Exception):
    pass


LINE_FIELDS = """id title sku currentQuantity product { id }
    priceAfterAllDiscountsBeforeTaxesSet { shopMoney { amount currencyCode } }"""
ORDER_FIELDS = """id createdAt updatedAt cancelledAt test displayFinancialStatus
    lineItems(first: 20) { nodes { %s } pageInfo { hasNextPage endCursor } }""" % LINE_FIELDS
ORDERS_QUERY = """query Orders($query: String!, $after: String) {
    orders(first: 20, after: $after, query: $query, sortKey: UPDATED_AT) {
        nodes { %s } pageInfo { hasNextPage endCursor }
    }
}""" % ORDER_FIELDS
LINES_QUERY = """query Lines($id: ID!, $after: String!) {
    order(id: $id) { lineItems(first: 100, after: $after) {
        nodes { %s } pageInfo { hasNextPage endCursor }
    } }
}""" % LINE_FIELDS


class Shopify:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=45)
        self.token = settings.access_token
        self.expires_at = float("inf") if self.token else 0
        self.lock = asyncio.Lock()
        self.token_lock = asyncio.Lock()

    async def close(self):
        await self.client.aclose()

    async def access_token(self):
        async with self.token_lock:
            if self.token and time.monotonic() < self.expires_at:
                return self.token
            try:
                response = await self.client.post(f"https://{self.settings.shop}/admin/oauth/access_token", data={
                    "grant_type": "client_credentials", "client_id": self.settings.client_id,
                    "client_secret": self.settings.client_secret,
                })
                if response.status_code != 200:
                    raise ShopifyError("Shopify authentication failed; check the installed app and client credentials")
                data = response.json()
                self.token = data["access_token"]
                self.expires_at = time.monotonic() + max(0, int(data["expires_in"]) - 120)
                return self.token
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                raise ShopifyError("Cannot obtain a Shopify access token") from exc

    async def graphql(self, query, variables=None):
        # Serialize catalog requests and polling so both respect the same cost budget.
        async with self.lock:
            for attempt in range(5):
                try:
                    response = await self.client.post(
                        f"https://{self.settings.shop}/admin/api/{self.settings.api_version}/graphql.json",
                        headers={"X-Shopify-Access-Token": await self.access_token()},
                        json={"query": query, "variables": variables or {}},
                    )
                    if response.status_code == 401 and self.settings.client_id and attempt == 0:
                        self.expires_at = 0
                        continue
                    if response.status_code == 429 or response.status_code >= 500:
                        await asyncio.sleep(min(30, max(2 ** attempt, float(response.headers.get("Retry-After", "1")))))
                        continue
                    if response.status_code >= 400:
                        raise ShopifyError(f"Shopify HTTP {response.status_code}; check app permissions and credentials")
                    body = response.json()
                    errors = body.get("errors", [])
                    if errors:
                        if all(e.get("extensions", {}).get("code") == "THROTTLED" for e in errors):
                            await asyncio.sleep(min(30, 2 ** (attempt + 1)))
                            continue
                        # Never forward raw upstream payloads or tokens to the browser/logs.
                        codes = {e.get("extensions", {}).get("code", "QUERY_ERROR") for e in errors}
                        raise ShopifyError("Shopify GraphQL error: " + ", ".join(sorted(codes)))
                    cost = body.get("extensions", {}).get("cost", {})
                    throttle = cost.get("throttleStatus", {})
                    if throttle and throttle.get("restoreRate", 0):
                        wait = max(0, (cost.get("requestedQueryCost", 0) - throttle["currentlyAvailable"]) / throttle["restoreRate"])
                        if wait:
                            await asyncio.sleep(min(wait, 30))
                    return body["data"]
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    if attempt == 4:
                        raise ShopifyError("Cannot read Shopify; check connectivity and API configuration") from exc
                    await asyncio.sleep(2 ** attempt)
            raise ShopifyError("Shopify temporarily unavailable or rate limited; retrying next poll")

    async def orders(self, search):
        after = None
        while True:
            connection = (await self.graphql(ORDERS_QUERY, {"query": search, "after": after}))["orders"]
            for order in connection["nodes"]:
                lines = order["lineItems"]
                page = lines["pageInfo"]
                while page["hasNextPage"]:
                    data = await self.graphql(LINES_QUERY, {"id": order["id"], "after": page["endCursor"]})
                    if data.get("order") is None:
                        raise ShopifyError("An order became unavailable during sync; retrying next poll")
                    extra = data["order"]["lineItems"]
                    lines["nodes"].extend(extra["nodes"])
                    page = extra["pageInfo"]
            yield connection["nodes"]
            if not connection["pageInfo"]["hasNextPage"]:
                break
            after = connection["pageInfo"]["endCursor"]
