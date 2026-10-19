# Sales Pulse — Shopify sales dashboard

Separate service in the expenses repository. It has its own image, password, database and Coolify resource. The root compose file and existing expenses application are unchanged.

Configured Coolify resource: `shopify-sales` (`bsx2yfwc7vai3076g1uixf1p`). External address: `https://flipper1.78.46.135.213.sslip.io`. This address resolves to the Hetzner server without buying a domain. HTTPS still depends on successful certificate issuance and renewal from the shared DNS service. Dashboard credentials are kept in Coolify runtime variables, not in this repository.

One Python/FastAPI process polls Shopify GraphQL Admin API every 60 seconds, even with no browser open. SQLite persists order lines; the page reads the local data and refreshes every minute. Chart.js is bundled locally. No customer names, emails, addresses or payment details are requested or stored.

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

Open the HTTPS URL. The browser asks for the dashboard login/password. Click **Выбрать товары**, search the Shopify catalog and save up to 100 products. All variants of a selected product are combined. The selection survives redeploys in SQLite.

The first import may take several minutes. The page shows sync status, the last successful update and errors. Old data remains available during Shopify outages. No sample data is presented as live sales.

## Calculation rules

- Include paid and partially/fully refunded orders; exclude test, cancelled and unpaid/partially paid orders. Fully refunded product lines naturally contribute zero units.
- Units use Shopify `currentQuantity`. Revenue uses `priceAfterAllDiscountsBeforeTaxesSet.shopMoney`: current line total after discounts and product refunds, excluding tax and delivery. Monetary calculations use Python Decimal before serialization.
- This is product revenue, not settlement/payout accounting. Refunds not allocated to a product line, chargebacks, shipping/tax refunds and payment fees are not included. Historical buckets change when the original order changes; refunds are attributed to the original order date.
- Days use the order's `createdAt`, grouped in `DASHBOARD_TIMEZONE` (default Europe/Belgrade). The API also reports the shop timezone for comparison. Revenue is in shop currency. Mixed historical shop currencies produce an explicit error instead of a misleading total.
- Orders are counted once in the overview even when they contain several selected products. Each product's own count includes orders containing that product.
- Initial history is 30 days (`HISTORY_DAYS`, 1–60). After that, only new/updated orders are fetched using a persisted checkpoint, a fixed upper time bound and a five-minute overlap. Both orders and nested line items are paginated. The checkpoint advances only after a complete sync; retries are idempotent. Increasing HISTORY_DAYS triggers a new backfill.
- Orders older than the configured history are pruned. Shopify's standard `read_orders` scope exposes the last 60 days; no `read_all_orders` access is required.
- This service handles one store. It does not automatically reconcile hard-deleted orders. To reconcile them, stop the application, back up the database, clear the `orders` table and the `checkpoint` state key, then restart for a full import. Keep the saved product selection and credentials.

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

- `/healthz` is public and only returns process health; it does not claim that Shopify is connected. The page, assets and all sales/configuration APIs require authentication. `/api/status` exposes last successful sync to authenticated clients.
- The service retries transient errors and rate limits with bounded backoff, automatically renews client-credentials tokens, serializes API requests and respects the GraphQL cost budget. No Shopify secrets are sent to the browser or written to logs.
- Back up the `/data` volume using SQLite's online backup API or after stopping the application. Restore the database and keep the same environment variables to recover the dashboard.
- Changing credentials, store or API version requires a redeploy. **Use a fresh volume if changing stores**, to avoid mixing different shops. Changing the dashboard password does not affect Shopify credentials.
- A push to the Git repository alone does not create a Coolify resource. The separate resource must be created once; subsequent pushes can auto deploy it.
