# Architecture Overview

This document captures the current structure of the sneaker collection app so new changes have shared context.

## Entry Points
- `app.py`: Flask app factory; registers extensions and blueprints.
- `wsgi.py`: Gunicorn entry (`wsgi:app`).
- `Procfile`: Process declaration (`web: gunicorn wsgi:app`).
- CLI/scripts: `release_updater.py`, `scripts/set_fx_rate.py`, `scraper.py`, `import_data.py`, `sneaker_db_updater.py`, `make_admin.py` (all bootstrap the app and run one‑off tasks).

## Core Modules
- `config.py`: Environment‑driven configuration (DB URL, secrets, mail, API keys).
- `extensions.py`: Initialised extensions (SQLAlchemy, Migrate, LoginManager, Mail, CSRF).
- `models.py`: SQLAlchemy models (`User`, `UserApiToken`, `Sneaker`, `SneakerDB`, `Release`, `Article`, `ArticleBlock`, `SiteSchema`, `AffiliateOffer`, `ReleasePrice`, `ExchangeRate`, `SneakerNote`, `SneakerSale`, `SneakerWear`, `StepBucket`, `StepAttribution`, `ExposureEvent`, `SneakerExposureAttribution`, `ReleaseSizeBid`, `ReleaseSalePoint`, `wishlist_items`).
- `forms.py`: WTForms for auth, profile, sneakers, releases, FX rates, news/SEO, and utility forms.
- `routes/`: Blueprints for `auth`, `main`, `news`, and `sneakers` domains.
- `templates/`: Jinja templates (page views and partials, email templates under `templates/email/`).
- `static/`: Assets under `static/brand/`, `static/images/`, `static/js/`.
- `uploads/`: User‑uploaded images (UUID filenames).
- `email_utils.py`: SendGrid email helper.
- `decorators.py`: `admin_required` guard and bearer/session auth helper.
- `utils/money.py`: Currency formatting + conversion using cached `ExchangeRate`.
- `utils/sku.py`: SKU normalisation helpers (space/hyphen/case variants).
- `services/kicks_client.py`: KicksDB API client (StockX/GOAT list + detail, prices, sales history).
- `services/sneaker_lookup_service.py`: Local‑first lookup, scoring, staleness checks, and SneakerDB upserts.
- `services/materials_extractor.py`: Keyword‑based materials extraction from cached descriptions.
- `services/news_service.py`: Article slug + tags helpers.
- `services/article_render.py`: Safe Markdown rendering (Markdown → HTML + Bleach sanitisation).
- `services/api_tokens.py`: Mobile bearer token generation and hashing.
- `services/steps_attribution_service.py`: Step bucket attribution (v1 equal split per day).
- `services/steps_seed_service.py`: Dev‑only helpers for seeding/verifying step buckets/attribution.
- `services/exposure_service.py`: Daily exposure attribution (wet/dirty) + material weighting.
- `services/release_ingestion_service.py`: Low‑cost release ingestion, GOAT backfill, resale refresh helpers.
- `migrations/`: Alembic environment and migration history.

## Data Flows

### Sneaker lookup and caching
1) `/api/sneaker-lookup` queries local `SneakerDB` first.
2) On cache miss/stale, calls KicksDB (StockX/GOAT) and upserts `SneakerDB`.
3) Materials extraction runs on cached descriptions only; no extra API calls.

### Release ingestion
- `release_updater.py` + `services/release_ingestion_service.py` fetch StockX/GOAT listings and update `Release`, `AffiliateOffer`, `ReleasePrice`, plus resale/sales history caches.
- Request caps and backfill thresholds are enforced to protect KicksDB quotas.

### Steps ingestion + attribution
- `POST /api/steps/buckets` upserts `StepBucket` (day/hour) with timezone per bucket.
- `POST /api/attribution/recompute` rebuilds `StepAttribution` for a date range.
- Attribution uses `SneakerWear` dates and equal split per day (`v1_equal_split_day`).
- Results are surfaced on sneaker detail pages and collection cards.
- Dev verification tooling is documented in `docs/steps_debug.md`.
- Mobile payload formats and timezone rules are in `docs/MOBILE_SYNC.md`.

### Exposure events (wet/dirty)
- Exposure is captured when a user updates a sneaker’s last worn date (card or detail view).
- Daily exposure is stored in `ExposureEvent` and split across sneakers worn that day into `SneakerExposureAttribution`.
- Health score combines steps + exposure penalties (material‑weighted) since `Sneaker.last_cleaned_at`.

### News / Articles
- Public feed: `GET /news` (filters: brand, tag, sort; pagination).
- Detail: `GET /news/<slug>` with SEO meta tags, JSON‑LD schemas, related articles, reading time, and share buttons.
- Admin authoring: `/admin/news/new`, `/admin/news/<id>/edit`, `/admin/news/<id>/delete` (admin‑only).
- Content blocks: heading/body/side image/full image/carousel with Markdown body text.
- JSON‑LD: global `SiteSchema` (organisation + website), auto‑generated `Article` schema, and optional per‑article Product/FAQ/Video schemas.

## Data & Privacy
- Steps data: daily buckets only (no GPS or activity traces), stored with timezone for travel/DST correctness.
- Exposure events: user‑entered wet/dirty flags and severity only; no location or weather data.
- Mobile API tokens: bearer tokens are stored hashed (SHA‑256); plaintext is shown once at creation.

## Build, Run, Test
- Install deps: `pip install -r requirements.txt`
- Run dev server: `flask run` (via `create_app`) or `gunicorn wsgi:app`
- Migrations: `flask db upgrade` (Alembic)
- Tests: `python -m pytest`

## Conventions (see `docs/AI_CONTEXT.md`)
- Naming: `lower_snake_case` files; lowercase plural dirs.
- Structure: one blueprint per domain with routes/forms/services/templates; shared extensions/config centralised.
- Error handling: wrap DB mutations, log with context, rollback on failure, user‑safe flashes.
- CSS: currently inline/embedded styles; prefer existing patterns and component classes.
- Stack: Flask/Jinja2, SQLAlchemy/Alembic, WTForms, Flask‑Login/Mail/CSRF; Postgres in prod, SQLite for dev/tests.

## Known Danger Zones
- Alembic has had multiple heads; merge before running `flask db upgrade` when branches diverge.
- Large KicksDB ingests on SQLite can hit `disk I/O error`; use smaller page caps or run against Postgres.
- GOAT detail lookups require a GOAT product id/slug; using StockX ids will 404.
- StockX sales history pagination may return very recent sales only; guard request caps to avoid excessive API usage.

## Debugging Tips
- Host/CSRF issues: some features behave differently on `localhost` vs `127.0.0.1`. Prefer `127.0.0.1` for local API calls.
- If you need remote access for webhooks/testing, use a tunnel (e.g. Cloudflared) and update `SERVER_NAME`/allowed hosts.
