# Expense Tracker

Self-hosted expense tracker built as a modular monolith.

Flow:

```text
Telegram -> Google Sheets -> manual correction -> Sync -> Postgres -> Web Dashboard
```

Google Sheets is the source of truth. Postgres is a read-optimized replica for the web dashboard and can be rebuilt from Google Sheets at any time. The Telegram bot never writes to Postgres.

## Current Structure

```text
backend/
  app/
    main.py
    api/
    core/
    db/
    expenses/
    sheets/
    telegram_bot/
    llm/
  Dockerfile
  requirements.txt
frontend/
  static/
docker-compose.yml
.env.example
```

## Local Run

1. Create env file:

```bash
cp .env.example .env
```

2. Fill at least:

```text
POSTGRES_PASSWORD=
GOOGLE_SHEETS_ID=
GOOGLE_WORKSHEET_NAME=Trans
GOOGLE_SERVICE_ACCOUNT_JSON=
APP_USERNAME=
APP_PASSWORD=
```

For the Telegram bot also fill:

```text
TELEGRAM_BOT_TOKEN=
OPENAI_API_KEY=
```

3. Start backend and Postgres:

```bash
docker compose up --build
```

Backend runs migrations automatically before starting.

4. Open dashboard:

```text
http://localhost:8000
```

5. Start the Telegram bot when needed:

```bash
docker compose --profile bot up bot
```

## Google Sheets Credentials

Recommended for Coolify/VPS:

```text
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}
```

Alternative:

```text
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service_account.json
```

Share the spreadsheet with the service account email. Set `GOOGLE_SHEETS_URL` if you want the dashboard to show an "Open Google Sheets" link.

## Migrations

Run manually if needed:

```bash
docker compose run --rm backend alembic upgrade head
```

Initial migration creates:

- `expenses`
- `sync_runs`

## Endpoints

- `GET /api/health`
- `GET /api/expenses?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD&category=...`
- `GET /api/expenses?expenses_only=false&limit=50000` for all account movements, including income
- `GET /api/dashboard/summary`
- `POST /api/sync/google-sheets`
- `GET /api/sync/last`

If `APP_USERNAME` and `APP_PASSWORD` are set, the dashboard entrypoint and API endpoints use Basic Auth.

## Sync Behavior

`POST /api/sync/google-sheets`:

1. Reads all non-empty Google Sheets rows.
2. Maps rows to normalized expenses.
3. Skips invalid individual rows and records `rows_failed`.
4. Rebuilds `expenses` inside one database transaction.
5. Writes a `sync_runs` record.

If Google Sheets cannot be read, or all rows fail validation, sync is marked `failed` and the previous `expenses` table contents remain available.

## Verification

Healthcheck:

```bash
curl http://localhost:8000/api/health
```

Frontend:

```text
http://localhost:8000
```

Run sync:

```bash
curl -u "$APP_USERNAME:$APP_PASSWORD" -X POST http://localhost:8000/api/sync/google-sheets
```

Check latest sync:

```bash
curl -u "$APP_USERNAME:$APP_PASSWORD" http://localhost:8000/api/sync/last
```

Check Postgres tables:

```bash
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\\dt"
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select count(*) from expenses;"
```

Check Telegram bot:

```bash
docker compose --profile bot up bot
```

Send a text or voice message. After confirmation, the bot should answer that rows were appended to Google Sheets.

## Coolify / VPS

Use the repository as a Docker Compose app. Configure env variables from `.env.example` in Coolify. Keep the `postgres_data` volume persistent. Expose port `8000` for the web app.

Run the bot as an additional compose service with the `bot` profile, or create a second Coolify service using the same image and command:

```bash
python -m app.telegram_bot.bot
```

## Known Limitations / TODO

- The frontend is a lightweight static dashboard, not a full port of all old Dash filters.
- Sync uses transactional `DELETE` + `INSERT`, not a dedicated staging table.
- Static assets are public; the dashboard entrypoint and API data are protected with Basic Auth when credentials are configured.
- Product-sheet visualization from the old visualizer was not migrated in this first pass.
