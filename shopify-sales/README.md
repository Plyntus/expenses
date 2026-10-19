# Sales Pulse — Shopify sales dashboard

Separate service in the expenses repository. It has its own image, password, database and Coolify resource. The root compose file and existing expenses application are unchanged.

Configured Coolify resource: `shopify-sales` (`bsx2yfwc7vai3076g1uixf1p`). External address: `https://flipper1.78.46.135.213.sslip.io`. This address resolves to the Hetzner server without buying a domain. HTTPS still depends on successful certificate issuance and renewal from the shared DNS service. Dashboard credentials are kept in Coolify runtime variables, not in this repository.

One Python/FastAPI process polls Shopify GraphQL Admin API every 60 seconds, even with no browser open. SQLite persists only lines matching the four fixed SKUs plus aggregate landing-page session counts; the page reads the local data and refreshes every minute. Chart.js is bundled locally. No customer names, emails, addresses or payment details are requested or stored.

## 1. Shopify connection

Store: `flipper-zero.myshopify.com`.

1. Open [Shopify Dev Dashboard](https://dev.shopify.com/dashboard), create an app in the **same organization** as the store.
2. Create/release an app version with Admin API scopes `read_orders` and `read_products`. This dashboard does not require write scopes or a Storefront API token.
3. Install the app on `flipper-zero.myshopify.com`. If the dashboard asks for an App URL, use your final dashboard HTTPS URL. This is a standalone server integration and does not need an embedded UI.
4. Copy **Client ID** and **Client Secret** to Coolify's runtime environment variables `SHOPIFY_CLIENT_ID` and `SHOPIFY_CLIENT_SECRET`. Set `SHOPIFY_SHOP=flipper-zero.myshopify.com`.
5. The service obtains a token via client credentials and renews it before its expiry. This flow requires the app and shop to belong to the same organization. For other organizations, an OAuth authorization flow is needed and is outside this implementation.

An existing non-expiring Admin API token can instead be provided as `SHOPIFY_ACCESS_TOKEN`. Do not use a manually copied 24-hour token as a permanent credential.

References: [client credentials grant](https://shopify.dev/docs/apps/build/authentication-authorization/client-credentials-grant), [access tokens](https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens), [order scopes and 60-day limit](https://shopify.dev/docs/api/admin-graphql/latest/objects/Order).

## 2. Coolify on Hetzner — recommended Dockerfile setup

Create a **new application resource** using the existing GitHub connection:

| Setting | Value |
| --- | --- |
| Repository | `Plyntus/expenses` |
| Branch | `main` |
| Build pack | Dockerfile |
| Base directory | `/shopify-sales` |
| Dockerfile location | `/Dockerfile` |
| Container port / Ports Exposes | `8000` |
| Health check | `GET /healthz`, port `8000` |
| Persistent storage | Named volume `shopify-sales-data` mounted at `/data` |
| Domain | Your controlled HTTPS hostname, e.g. `https://flipper1.sales.com` |

Runtime variables: copy keys from `.env.example`. Required for any startup: `DASHBOARD_USERNAME` and a unique `DASHBOARD_PASSWORD` of at least 16 characters. Without them the process fails closed. Shopify credentials can be added later: the authenticated dashboard will show setup instructions until configured. Enable auto deploy for the new resource; if using path filters, include `shopify-sales/**`.

Use **one replica and one Uvicorn worker**. SQLite and the in-process poller are intended for a single instance. During deployment, stop the old instance before the replacement writes to the shared volume (disable rolling updates if needed). The volume must be writable by UID/GID `10001`; a new Docker named volume inherits the image's `/data` ownership. For an existing bind mount, set that directory's owner to `10001:10001`.

For a custom hostname such as `flipper1.sales.com`, you need control of its DNS, but no separate hosting plan. Set an A record pointing to `78.46.135.213` (verify the server IP), and remove or correct AAAA if IPv6 is not configured. Without a custom domain, the configured `flipper1.78.46.135.213.sslip.io` uses public DNS from sslip.io. Let Coolify issue and renew the HTTPS certificate; shared-domain certificate limits can affect issuance. Verify a trusted certificate before entering a password. See [the DNS service's TLS documentation](https://nip.io/#tls) for its certificate support. Basic Auth credentials must be used over HTTPS.

References: [Coolify Dockerfile build](https://coolify.io/docs/applications/builds/dockerfile), [persistent storage](https://coolify.io/docs/knowledge-base/persistent-storage).

## 3. Open the dashboard

Open the HTTPS URL. The browser asks for the dashboard login/password. The dashboard is fixed to **Flipper One Pre-order** and these exact, case-sensitive SKU values:

- `FLP-O-COUPON-349`
- `FLP-O-COUPON-399`
- `FLP-O-COUPON-449`
- `FLP-O-COUPON-499`

The interface is in English and shows net units, original order counts, refunded-unit counts and authorized-order counts for each SKU, with a daily units chart. Monetary totals, revenue charts and monetary table columns are not displayed. The daily breakdown table starts with today and lists earlier dates below it. Beneath the daily units chart, **Last 24 hours** shows a separate hourly series for each SKU, independently of the selected daily date range. It covers exactly the rolling last 24 hours in 24 consecutive 60-minute buckets, including zero buckets; labels and tooltips use the dashboard timezone, with offsets in tooltips to distinguish repeated hours at daylight-saving changes. Both charts read the same local cache and refresh every minute. No product selection is needed or exposed. All four SKUs remain visible even when one has no sales. Before the first successful import, amounts appear as dashes rather than misleading zeroes. Order amount comes from actual Shopify amounts, not the numeric suffix in the SKU. The API retains its legacy `revenue` key for this amount; it is not a captured-payment or cash-received metric.

Shopify still returns complete order pages, which can include other products. The service discards non-matching line items before persistence and never stores order headers for orders with no matching SKU. Matching uses SKU, not a mutable product title. On startup, earlier caches are migrated: unrelated lines and empty order headers are removed, while tracked history and sync checkpoints are preserved. The previous product-selection table is retired. A later payment-status migration adds `financial_status` to cached orders; rows without a known status are refreshed by ID before the next incremental sync. This repair does not require rebuilding the entire store history or advancing the checkpoint prematurely.

The first import may take several minutes. The page shows sync status, the last successful update and errors. Old data remains available during Shopify outages. No sample data is presented as live sales.

## Landing-page sessions

The **Landing page sessions** panel reads Shopify Analytics via ShopifyQL for `/pages/flipper-one/`, matching the path with and without its trailing slash. This measures sessions **starting** on that page, not every session that later visits it. It does not filter traffic by SKU or calculate a purchase conversion rate. The panel displays Total, Email (`utm_medium` exactly `email`) and Other (every other value, including empty or missing tags). No additional human/bot filter is applied. Total and both groups come from one ShopifyQL result, so Email + Other equals Total for each day and selected period. Raw UTM-medium values are aggregated into these two groups before persistence.

The same Today / Last 7 days / Last 30 days control selects the inclusive calendar-day range. ShopifyQL uses `TIMESERIES day`, explicit dates and `WITH TIMEZONE` set to the dashboard timezone. The server refreshes the full configured history once a minute, including corrected historical counts. Only aggregate date/session counts are stored; query strings, customer details and individual session identifiers are not fetched or stored.

The existing Shopify app needs `read_reports` and ShopifyQL eligibility (including Level 2 protected-customer-data access). Update the app version and approve new scopes for the installed app as needed. No new credentials or environment variables are required. See [ShopifyQL API requirements](https://shopify.dev/docs/api/admin-graphql/latest/queries/shopifyqlQuery) and [session fields](https://shopify.dev/docs/api/shopifyql/latest/schemas/sessions_and_behavior/sessions).

Analytics polling is independent of order sync; an analytics failure preserves the last good report and does not move the sales checkpoint or prevent new sales imports. Missing/incomplete reports display a dash rather than zero. The panel shows its own update timestamp, timezone, stale status and errors. `/api/traffic?days=7` is authenticated and returns the cached daily series and its sum. Shopify processing may lag the dashboard polling time. The report groups by `utm_medium` and rejects duplicate groups or a response that reaches its explicit row limit, preserving the last complete report instead of showing truncated totals. When upgrading an older total-only cache, Total remains available and Email/Other display dashes until the first grouped report succeeds.

## Tracked-SKU purchase sources and conversion

Two bottom charts use only eligible orders containing the four configured SKUs, once per order even if it contains multiple matching lines or variants. **Purchases by UTM medium** groups those orders by order creation date. **Hourly purchase conversion** uses `tracked orders attributed to the visit hour / landing-page sessions in that hour × 100` for Email and Other separately. Both use the selected Today / 7 / 30 day range and the dashboard timezone.

The order source is `customerJourneySummary.lastVisit`: the last session before purchase. The visit must start on `/pages/flipper-one/` (with either trailing-slash form). Its UTM medium comes from Shopify's `utmParameters.medium`, falling back to the literal `utm_medium` in its landing-page URL when the field is absent. Only the Email/Other group, visit time, page-match flag, processing status and timestamps are persisted; the full URL, query string, customer identifiers and other UTM values are not stored. Orders from other landing pages and orders with no linked visit are reported separately and excluded from the page's charts.

New orders may have `ready=false`. They remain pending, do not become Other, and are retried every five minutes even if the order's `updatedAt` does not change. Known counts are labelled provisional while any selected-period orders await source data. If every relevant order is still pending, the charts show a waiting message. Updated eligible orders are refreshed by ID; source records cascade when orders are pruned. No write scopes or new credentials are needed.

A separate poller refreshes hourly ShopifyQL sessions and order sources. Session buckets are queried in UTC using explicit timestamp boundaries, then rendered in the dashboard timezone. Repeated hours at a daylight-saving rollback remain separate. Hours without sessions have an undefined rate and display gaps rather than false zero percentages. Rates are calculated from summed counts, not averaged from percentages; they can exceed 100% if one visit produces multiple orders. The current hour is partial. Historical ratios can change as Shopify prepares sources and as order eligibility changes. Refunded orders remain purchases under the existing Orders rules; this is not net units or payment-capture conversion.

`/api/acquisition?days=7` returns daily purchase counts, hourly session/order/rate pairs, pending/unlinked/excluded-order counts and independent source/report errors. Last good hourly reports are preserved on errors, and sales synchronization continues independently. See [Shopify CustomerJourneySummary](https://shopify.dev/docs/api/admin-graphql/latest/objects/CustomerJourneySummary).

## Calculation rules

- Include authorized, paid, partially paid and partially/fully refunded orders; exclude test, pending, expired and voided orders. Cancelled orders remain excluded unless a tracked product line has an actual Shopify refund allocation; those refunded sales stay in order/refund history. Fully refunded product lines contribute zero net units but still count in Orders. Authorized orders are preorders with a payment hold, not captured funds; the UI labels the monetary metric as order amount and reports authorized-order counts separately.
- Units use Shopify `currentQuantity`. Orders count distinct eligible orders with a positive original line `quantity`, including fully refunded lines. Refunded is a count of units, summed from Shopify `refunds → refundLineItems.quantity` for each matching line ID, not a count of refund events or refunded money. Line removal during editing and shipping-only/unallocated refunds do not increase Refunded. Refund records describe product refunds, not payment settlement; transaction success is not audited here. For 100 single-unit orders with 10 fully refunded units: Units 90, Orders 100, Refunded 10. Multiple units in an order can make Units and Orders differ. Refunds and net units use the original order date, so the selected period is an order cohort rather than a list of refund processing dates. See [Shopify Refund](https://shopify.dev/docs/api/admin-graphql/latest/objects/Refund). Order amount uses `priceAfterAllDiscountsBeforeTaxesSet.shopMoney`: current line total after discounts and product refunds, excluding tax and delivery. Monetary calculations use Python Decimal before serialization.
- This is product order value, not captured revenue or settlement/payout accounting. Refunds not allocated to a product line, chargebacks, shipping/tax refunds and payment fees are not included. Historical buckets change when the original order changes; refunds are attributed to the original order date.
- Days use the order's `createdAt`, grouped in `DASHBOARD_TIMEZONE` (default Europe/Belgrade). The API also reports the shop timezone for comparison. Order amount is in shop currency. Mixed historical shop currencies produce an explicit error instead of a misleading total.
- Each SKU counts distinct orders containing that SKU. Several lines of the same SKU in one order count as one order. An order containing two tracked SKUs appears once in each SKU. The API retains a distinct overall order count, but the UI shows only the SKU breakdown.
- Initial history is 30 days (`HISTORY_DAYS`, 1–60). After that, only new/updated orders are fetched using a persisted checkpoint, a fixed upper time bound and a five-minute overlap. Both orders and nested line items are paginated. All refund records are read without list truncation, and each refund line-item connection is paginated before committing an order. Existing cached orders with unknown original/refund quantities are refreshed by ID, without discarding the cache or resetting the checkpoint. Refunded is displayed as a dash while that backfill is incomplete. The checkpoint advances only after a complete sync; retries are idempotent. Increasing HISTORY_DAYS triggers a new backfill.
- Orders older than the configured history are pruned. Shopify's standard `read_orders` scope exposes the last 60 days; no `read_all_orders` access is required.
- This service handles one store. It does not automatically reconcile hard-deleted orders. To reconcile them, stop the application, back up the database, clear the `orders` table and the `checkpoint` state key, then restart for a full import. Keep the same credentials and fixed SKU configuration.

## Local run / Docker Compose alternative

```sh
cd shopify-sales
cp .env.example .env
# Fill DASHBOARD_USERNAME / DASHBOARD_PASSWORD, then Shopify credentials.
docker compose up --build -d
```

Open `http://127.0.0.1:8010` locally, or tunnel it over SSH. The local compose file deliberately binds the port to loopback. Coolify's Dockerfile setup routes HTTPS directly to port 8000; it does not use this local port mapping.

Without Docker (Python 3.12):

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# Export environment variables securely; set DATABASE_PATH to a writable local path.
.venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8010
```

Checks:

```sh
python -m unittest discover -s tests -v
```

## Operations

- `/healthz` is public and only returns process health; it does not claim that Shopify is connected. The page, product image, scripts, styles and all sales/status APIs require authentication. The non-sensitive `/favicon.png` is public so browsers can load the tab icon before sign-in. The former product-catalog and selection endpoints are removed; the tracked SKUs cannot be expanded from the UI. `/api/status` exposes last successful sync to authenticated clients.
- The service retries transient errors and rate limits with bounded backoff, automatically renews client-credentials tokens, serializes API requests and respects the GraphQL cost budget. No Shopify secrets are sent to the browser or written to logs.
- Back up the `/data` volume using SQLite's online backup API or after stopping the application. Restore the database and keep the same environment variables to recover the dashboard.
- Changing credentials, store or API version requires a redeploy. **Use a fresh volume if changing stores**, to avoid mixing different shops. Changing the dashboard password does not affect Shopify credentials.
- A push to the Git repository alone does not create a Coolify resource. The separate resource must be created once; subsequent pushes can auto deploy it.
