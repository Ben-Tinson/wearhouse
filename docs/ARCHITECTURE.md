# Architecture Overview

This document captures the current structure of the sneaker collection app so new changes have shared context.

## Current Platform State
- Runtime database: Supabase Postgres.
- Supabase Postgres cutover is completed.
- Active Supabase project ref: `sjwdvsefjlflgavshiyy`.
- Previous Supabase project ref: `mizyioplztuzycipfdsd`, retained temporarily as fallback/reference before retirement.
- SQLite is archival fallback only and remains suitable for local dev/tests; it is not the operational source of truth.
- Operational backup/restore preference is Postgres dump/restore.
- Flask remains the active backend, auth, and session runtime.
- Flask-Login remains live.
- The app-owned `user` table remains a core application table and must not be assumed removable.
- Supabase Auth is planned as the next major platform migration, but is not implemented yet.
- Latest cutover used public-schema logical dump/restore into a fresh target, then app repoint and smoke validation.

## Entry Points
- `app.py`: Flask app factory; registers extensions and blueprints.
- `wsgi.py`: Gunicorn entry (`wsgi:app`).
- `Procfile`: Process declaration (`web: gunicorn wsgi:app`).
- CLI/scripts: `release_updater.py`, `scripts/set_fx_rate.py`, `scraper.py`, `import_data.py`, `sneaker_db_updater.py`, `make_admin.py`.

## Core Release-Related Models
- `Release`
  - base release record used by calendar, release detail, wishlist, and sneaker-detail linkage
  - stores fallback `release_date`, base `retail_price` / `retail_currency`, descriptive fields, image, KicksDB source ids/slugs, sync timestamps, and ingestion metadata
  - `release_slug` is used for matching imported releases and product URLs
  - `is_calendar_visible` controls whether the release appears on the public calendar
- `ReleaseRegion`
  - one row per release + region (`US`, `UK`, `EU`)
  - stores region-specific release date, optional release time, optional timezone
- `ReleasePrice`
  - one row per release + region + currency
  - used for region-aware retail price selection
- `AffiliateOffer`
  - reused for both aftermarket links (`stockx`, `goat`) and retailer / raffle links
  - `region=None` is treated as global; region-specific retailer rows are used by the release display service
- `ReleaseSizeBid`
  - cached size-level ask/bid chart rows for release and sneaker detail pages
- `ReleaseSalePoint`
  - cached sales history rows used for charts and market calculations
- `ReleaseMarketStats`
  - cached aggregate KicksDB market stats shared across users
  - stores `average_price_1m`, `average_price_3m`, `average_price_1y`, `volatility`, `price_range_low/high`, `sales_price_range_low/high`, `sales_volume`, and `gmv`

### Release-linked deletion / visibility behaviour
- `Release` relationships to `AffiliateOffer`, `ReleasePrice`, `ReleaseRegion`, `ReleaseSizeBid`, `ReleaseSalePoint`, `ReleaseSalesMonthly`, and `ReleaseMarketStats` use ORM cascade delete-orphan.
- The calendar “Delete All Releases” admin action does **not** delete `Release` rows. It hides currently visible releases by setting `Release.is_calendar_visible = False`.
- Individual admin delete for a release still deletes the `Release` row and cascades to release-linked child rows.

## User Preferences
- `User.preferred_currency` and `User.preferred_region` are separate.
- Supported region values are `US`, `UK`, `EU`.
- Registration (`routes/auth_routes.py`) captures `preferred_region`; profile edit (`routes/main_routes.py`) lets the authenticated user update both `preferred_currency` and `preferred_region` on their own account only.
- `preferred_region` drives release date / retailer selection; `preferred_currency` drives currency display and resale conversions where allowed.

## Database / Cutover Architecture
- SQLAlchemy models in `models.py` and Alembic migrations in `migrations/` remain the schema authority.
- Production/current runtime uses Supabase Postgres project `sjwdvsefjlflgavshiyy`.
- Future operational restores/cutovers should use full Postgres dump/restore.
- SQLite import/restore paths are historical/archival and should not be used as the normal production cutover path.
- Retain backup artefacts under `backups/postgres/` until a formal retention decision is made.
- The previous Supabase project `mizyioplztuzycipfdsd` should remain available only until rollback confidence is no longer needed.

## Authentication Architecture
- Current live auth is Flask-Login plus app-owned `User` records.
- Mobile/API step sync uses hashed bearer tokens via `UserApiToken`.
- Supabase Auth is planned but not live.
- The future Supabase Auth migration should keep an app-level user/profile table and explicitly map existing users to Supabase identities.
- Admin status, profile preferences, sneaker ownership, collection data, and app roles remain application concerns.
- Password-reset token logic was validated, but outbound reset-email delivery is deferred because Supabase Auth is expected to replace or reduce that legacy path.
- Auth assumptions are distributed across routes, decorators, profile/account forms, admin-only checks, API token helpers, templates, and tests.
- Implementation agents should perform readiness analysis before code changes, especially across `routes/auth_routes.py`, `routes/main_routes.py`, `routes/sneakers_routes.py`, `decorators.py`, `services/api_tokens.py`, `forms.py`, profile templates, and auth/profile/API tests.
- Future auth migration should be cautious and phased; do not hard-replace Flask auth in one step.

## Release Calendar Flow
- Route: `routes/main_routes.py::release_calendar()`.
- Query: upcoming `Release` rows where `Release.release_date >= today` and `Release.is_calendar_visible == True`.
- The route eagerly loads `offers`, `prices`, and `regions` and builds a `release_display_map` via `services/release_display_service.py`.
- Calendar cards use the resolved display data for:
  - release date
  - retail price
  - market note when needed
- Admin-only UI on the calendar:
  - `Add New Release`
  - `CSV Import`
  - “Delete All Releases” danger zone (actually hides visible releases)

## Admin Manual Release Add / Edit
- Routes: `/admin/add-release`, `/admin/edit-release/<id>` in `routes/main_routes.py`.
- Form: `ReleaseForm` in `forms.py`.
- Supports:
  - base release fields (`brand`, `model_name`, `name`, `sku`, `colorway`, `description`, `notes`, image, fallback release date, fallback retail price/currency, StockX URL, GOAT URL)
  - region-specific US/UK/EU date, time, timezone, price, currency, retailer links
  - date propagation checkboxes (`apply_us_date_to_uk`, etc.) to copy one entered regional date into other missing regions
- Validation currently enforced:
  - at least one regional price or fallback retail price
  - at least one regional release date or fallback release date
  - if a region includes price, retailer links, or release time, that region needs a release date
  - retail currency is required when a retail price is provided
- Manual add/edit uses the same region upsert helpers as CSV import (`_upsert_release_region`, `_upsert_release_price`, `_upsert_retailer_links`, `_upsert_affiliate_offer`).

## Release CSV Import (Admin)
- Routes: `routes/main_routes.py`
  - `GET/POST /admin/release-import`
  - `POST /admin/release-import/confirm`
  - `GET /admin/release-import/template`
- UI: `templates/admin/release_import.html`
- Service: `services/release_csv_import_service.py`

### CSV template
- Template download includes:
  - header row
  - `__FORMAT_GUIDE__` row
  - sample row
- The importer explicitly ignores any row whose `brand` column is `__FORMAT_GUIDE__`.

### CSV columns
- Base columns:
  - `brand`, `model`, `colorway`, `sku`, `image_url`, `stockx_url`, `goat_url`, `notes`, `description`
- Region columns for each of `us`, `uk`, `eu`:
  - `_release_date`, `_release_time`, `_timezone`, `_retail_price`, `_currency`, `_retailer_links`
- `colorway` is optional.

### Import flow
1. Upload CSV.
2. Service builds a dry-run preview.
3. Preview shows summary, blocking errors, warnings, and matched-vs-new counts.
4. Confirm form posts the original CSV text back as a hidden field.
5. Confirm re-validates before any write.

### CSV validation / matching / apply rules
- Required per row: `brand`, `model`, `sku`.
- At least one regional release date is required.
- Regional price requires regional currency.
- Retailer link format: `Retailer Name|URL; Retailer Name|URL`.
- Duplicate-row detection:
  - primary: normalized SKU
  - fallback: brand + model slug + region-date composite
- Match order against existing releases:
  1. normalized SKU
  2. stored `release_slug`
  3. computed slug fallback for legacy releases with no stored slug
- Upsert rules:
  - non-blank CSV values overwrite existing values
  - blank CSV values do **not** clear existing values
  - `skip_existing=True` skips matched releases entirely during apply
- Import writes:
  - `Release`
  - `ReleaseRegion`
  - `ReleasePrice`
  - `AffiliateOffer` for region retailer links plus optional global StockX / GOAT aftermarket links
- Import metadata written on `Release`:
  - `ingestion_source = "csv_admin"`
  - `ingestion_batch_id`
  - `ingested_at`
  - `ingested_by_user_id`

### Interaction with KicksDB-managed release data
- `services/release_ingestion_service.py::_should_preserve_csv_field()` protects CSV-managed releases from later KicksDB ingestion overwriting key fields such as brand, name/model, colorway, image, retail price/currency, release date, and SKU.
- Release-linked market data (offers, size bids, sale points, market stats) can still refresh independently.

## Region-Aware Release Display
- Source of truth: `services/release_display_service.py`.
- Used by:
  - release calendar cards
  - release detail page
  - wishlist release cards
  - wishlist release detail pages
  - sneaker detail release blocks when a linked `Release` is present

### Region resolution
- If logged in, preferred region comes from `User.preferred_region`.
- If logged out, there is no user-preferred region.
- If exactly one meaningful region exists across `ReleaseRegion` / `ReleasePrice`, that region becomes the canonical display region.
- If there are no explicit region rows but the base release came from KicksDB (`kicksdb_stockx` or `kicksdb_goat`) and has base date/price data, that base data is treated as US-specific for display purposes.

### Release date resolution
1. matching `ReleaseRegion` for canonical / preferred region
2. base `Release.release_date`
3. earliest available `ReleaseRegion`

### Retail price resolution
- Retail price display intentionally does **not** FX-convert.
- Selection order:
  1. exact region + currency `ReleasePrice`
  2. matching region `ReleasePrice` in its native currency
  3. base `Release.retail_price` + `Release.retail_currency`
  4. another available `ReleasePrice`
- If only USD data exists, the app shows the real USD price, not an estimated converted user-currency retail price.

### Offer resolution
1. active offers matching preferred/canonical region
2. active global offers (`region is None`)
3. any remaining active offers

### Market context labels
- Single-region fallback note:
  - `Only US release data currently available`
  - `Only UK release data currently available`
  - `Only EU release data currently available`
- The single-region note is suppressed when that single available region matches the user’s preferred region.
- `Showing {REGION} release data` only appears when both the resolved release date and resolved retail price actually come from that region.

## Release Detail Pages
- Canonical route: `routes/main_routes.py::_render_release_detail()` via `/products/<product_key>-<slug>`.
- Canonical template: `templates/release_detail.html`.
- Shared blocks:
  - `_release_about_section.html`
  - `_release_market_metrics.html`
- Detail pages render date only; stored release time/timezone are not displayed.

### Detail-page refresh / caching behaviour
- On load, if the release lacks KicksDB source ids/slugs and has a SKU, `_ensure_release_for_sku_with_resale()` attempts to hydrate them.
- If release market data is stale (`last_synced_at` older than 24h), `_refresh_resale_for_release()` runs.
- Size bids and sales history use their own 5-day staleness windows.
- Shared release data is cached at the release level, so one refresh benefits all users until stale again.
- Admin-only manual market refresh route: `/admin/releases/<id>/refresh-market`.

### Detail-page content hierarchy
- Release Overview
  - release date
  - retail price
  - market note when relevant
  - brand
  - colourway
- About this release
  - shown only if release description exists on `Release` or falls back from matching `SneakerDB.description`
- Pricing & Market
  - average resale benchmark (service currently picks one primary average, preferring 1M then 3M then 1Y; the shared metric partial currently renders that primary value under the fixed label `Average resale price (3M)`)
  - market metrics currently shown when available:
    - `Sales volume (3M)`
    - `Price premium (1Y)`
    - `Volatility (1Y)`
    - `Sales price range (1Y)`
  - values are converted into the user’s preferred currency **for resale/market metrics** when safe to do so
- Lowest Ask by Size (StockX) chart
- Retailers / Aftermarket / Raffles sections

### Admin-only detail tools
- Admin diagnostics block summarises what release-level data is currently present or missing.
- Admin “Refresh market data” button forces release-level refresh.
- Admin “Edit Release” button links to the same release-edit form used from the calendar.

## Sneaker Detail Pages (Collection / Rotation)
- Route: `routes/sneakers_routes.py::my_sneaker_detail`.
- Template: `templates/sneaker_detail.html`.
- Source context:
  - sneaker-owned data (purchase, wear, notes, health, expenses)
  - release-linked data if a `Release` can be found / backfilled from the sneaker SKU
- Current behaviour:
  - sneaker detail tries local release lookup by SKU
  - if missing or incomplete, it now backfills via `_ensure_release_for_sku_with_resale(sku)` and reloads the release
  - if a release is available, it reuses `resolve_release_display()` and `build_release_detail_extras()`
- Conditional release-linked blocks on sneaker detail:
  - About this release
  - Pricing & Market market metrics
  - release date / retail price inside the release info / finances sections
  - sales history chart
- The sneaker detail page still has its own sneaker-owned cards (`Your Sneaker's Details`, `Your Sneaker's Finances`, health, exposures, notes, etc.).

## KicksDB / Shared Market Data
- `services/kicks_client.py` is the KicksDB transport layer.
- `services/release_ingestion_service.py` and `release_updater.py` fetch and store shared release-level data.

### What is shared at the release level
- base release metadata from KicksDB lookup / ingestion (`Release` fields)
- global aftermarket offers (`AffiliateOffer` for StockX/GOAT)
- size-bid chart rows (`ReleaseSizeBid`)
- sales history rows (`ReleaseSalePoint`)
- aggregate market stats (`ReleaseMarketStats`)

### What remains user-specific
- selected display region (`preferred_region`)
- selected display currency (`preferred_currency`)
- conversion of resale/market metrics into the preferred currency when possible
- wishlist / collection / rotation membership and sneaker-owned finance / wear data

### Ingestion / updater behaviour
- `release_updater.py` drives `ingest_kicksdb_releases()` and optional enrichment/backfill.
- StockX is primary; GOAT backfill runs only when thresholds indicate it is needed.
- CSV-managed release core fields are protected from normal KicksDB ingestion overwrite.
- KicksDB-derived release market stats default to USD when the source is StockX/GOAT and the API does not provide a currency explicitly.

## Template / UI Structure
- Key release-related templates / partials in current use:
  - `templates/release_calendar.html`
  - `templates/release_detail.html`
  - `templates/sneaker_detail.html`
  - `templates/wishlist.html`
  - `templates/admin/release_import.html`
  - `templates/_release_about_section.html`
  - `templates/_release_market_metrics.html`
  - `templates/_wishlist_button.html`
- Admin-only release entry points currently present in UI:
  - add release
  - edit release
  - delete single release
  - hide all visible releases from calendar
  - CSV import + template download + confirm
  - release-detail market refresh + diagnostics

## Build, Run, Test
- Install deps: `pip install -r requirements.txt`
- Run dev server: `flask run` or `gunicorn wsgi:app`
- Migrations: `flask db upgrade`
- Tests: `python -m pytest`

## Known Ambiguities / Danger Zones
- Some release-detail and market-data tests still depend on live KicksDB-shaped payload assumptions; labels and availability can drift if upstream payloads change.
- KicksDB detail payloads use multiple inconsistent market-stat key shapes, so release ingestion / normalisation logic is intentionally defensive.
- SQLite can still be slow or fragile for heavy local KicksDB refresh flows; Postgres remains the realistic production path.
