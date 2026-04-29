# AI Context and Coding Standards

These rules guide how we structure and extend the codebase.

## State Of The World
- Supabase Postgres cutover is completed; Soletrak now runs against the fresh Supabase Postgres target project `sjwdvsefjlflgavshiyy`.
- The previous Supabase project `mizyioplztuzycipfdsd` is fallback/reference only until deliberate retirement.
- SQLite is archival fallback only; it is no longer the operational source of truth or normal cutover source.
- Flask remains the live backend and auth/session runtime.
- Flask-Login remains live.
- The app-owned `user` table remains important and must not be assumed removable.
- Supabase Auth is planned next, but is not live and has not replaced Flask-Login or app-owned `User` logic.
- Current operational backup/restore preference is full Postgres dump/restore.
- Postgres backup artefacts exist under `backups/postgres/`:
  - `soletrak_postgres_20260428_152256.dump`
  - `soletrak_public_20260428_154243.dump`
  - `soletrak_public_cutover_20260428_160930.dump`

## Current Platform Direction
- Postgres is the production database source of truth.
- Future operational restores/cutovers should use full Postgres dump/restore, not SQLite re-import or CSV.
- Keep the old Supabase source project temporarily for rollback confidence before retirement.
- SendGrid/password-reset email delivery is deferred because Supabase Auth is planned.
- Next major platform work is Supabase Auth migration planning and implementation design.
- Future auth work should use cautious phased migration, not hard replacement.

## Naming
- Files and modules use `lower_snake_case` (e.g. `auth_routes.py`, `release_display_service.py`).
- Directories use lowercase plurals (e.g. `routes/`, `templates/`, `static/`, `tests/`, `uploads/`).
- Templates are mostly flat in `templates/` with shared partials prefixed `_` (e.g. `_release_about_section.html`, `_release_market_metrics.html`, `_single_sneaker_card.html`); admin pages live under `templates/admin/`; email templates live in `templates/email/`.
- Static assets live under `static/brand/`, `static/images/`, and `static/js/`; uploads keep UUID filenames in `uploads/`.

## Component Structure (Flask)
- App factory lives in `app.py`; shared extensions in `extensions.py`; configuration in `config.py`.
- One blueprint per domain (`auth`, `main`, `news`, `sneakers`). Forms are centralised in `forms.py`; services live in `services/`.
- Models live in `models.py` (notably `Release`, `ReleaseRegion`, `ReleasePrice`, `AffiliateOffer`, `ReleaseMarketStats`, `Sneaker`, `SneakerDB`, `StepBucket`, `ExposureEvent`, `Article`).
- Background jobs and utilities live in scripts (e.g. `release_updater.py`, `set_fx_rate.py`, `make_admin.py`).

## Where to Make Changes
- **Release calendar + release admin flows**: `routes/main_routes.py`, `forms.py`, `templates/release_calendar.html`, `templates/add_release.html`, `templates/edit_release.html`, `templates/admin/release_import.html`.
- **Release CSV import**: `services/release_csv_import_service.py` for parsing/validation/upsert, `routes/main_routes.py` for preview/confirm/template endpoints.
- **Release ingestion + KicksDB backfill**: `services/release_ingestion_service.py`, `services/kicks_client.py`, `release_updater.py`.
- **Region-aware release display**: `services/release_display_service.py` is the source of truth for date/price/offers/market-context selection.
- **Release detail + shared market metrics**: `routes/main_routes.py`, `services/release_detail_service.py`, `templates/release_detail.html`, `_release_about_section.html`, `_release_market_metrics.html`.
- **Collection/rotation sneaker detail release linkage**: `routes/sneakers_routes.py`, `templates/sneaker_detail.html`.
- **Profile / registration preferences**: `routes/auth_routes.py`, `routes/main_routes.py`, `forms.py`, `templates/register.html`, `templates/profile.html`, `templates/edit_profile.html`.
- **Steps ingestion + attribution**: `routes/sneakers_routes.py` for API endpoints, `services/steps_attribution_service.py` for logic.
- **Exposure events**: `services/exposure_service.py` for attribution; UI surfaces in `templates/_single_sneaker_card.html` and `templates/sneaker_detail.html`.
- **Materials**: `services/materials_extractor.py` and `SneakerDB` fields; UI on sneaker detail.
- **Mobile tokens**: `services/api_tokens.py`, profile UI in `templates/profile.html`.
- **News/Articles**: `routes/news_routes.py` + `templates/news/` + `templates/admin/news_form.html`.
- **Sneaker health scoring**: Source of truth is `docs/SNEAKER_HEALTH.md`.

## State Management
- Authentication state is managed via Flask-Login; avoid storing user data directly in sessions.
- API auth supports bearer tokens (`UserApiToken`) for mobile step sync.
- Database access goes through SQLAlchemy models and `db.session` only.
- Release detail market data caches live on `Release` plus release-linked tables (`AffiliateOffer`, `ReleasePrice`, `ReleaseSizeBid`, `ReleaseSalePoint`, `ReleaseMarketStats`); these are shared across users.
- Per-user display differences come from `User.preferred_region` + `User.preferred_currency`, resolved at render time by `services/release_display_service.py`.

## Future Supabase Auth Context
- Current Flask auth, Flask-Login sessions, app-owned `User`, admin checks, and `UserApiToken` flows still exist and are live.
- **Phase 1 of the Supabase Auth migration has landed (preparation only, no behaviour change):** the `user` table has a dormant `supabase_auth_user_id` UUID column with a partial unique index where non-null; `services/auth_resolver.py` exposes a pass-through resolver shim (`get_current_app_user`, `get_current_app_user_id`, `is_current_app_user_admin`); `scripts/auth_audit_users.py` provides a read-only audit.
- **Phase 2 foundation has landed (still no end-user behaviour change, default flag-off):** `SUPABASE_*` env vars are wired in `Config` with `SUPABASE_AUTH_ENABLED` defaulting to `false`; `services/supabase_auth_service.py` exposes a JWT verifier and a `SupabaseAdminClient` (used only by the linkage CLI); `services/supabase_auth_linkage.py` exposes the only sanctioned writers of `user.supabase_auth_user_id`; `services/auth_resolver.py` has a flag-gated Supabase JWT branch (Flask-Login still wins, never auto-links); `scripts/link_supabase_identities.py` is the admin linkage CLI (dry-run default, `--apply` required, audit-logged, reversible via `--unlink`). `decorators.bearer_or_login_required`, `routes/auth_routes.py`, `forms.py`, and templates are unchanged. No `/admin/auth/probe` yet.
- Current auth assumptions are spread across routes, decorators, profile flows, admin checks, templates, and API token flows. Before changing auth code, perform readiness analysis across `routes/auth_routes.py`, `routes/main_routes.py`, `routes/sneakers_routes.py`, `decorators.py`, `services/api_tokens.py`, `forms.py`, profile templates, and tests.
- Password-reset token generation/verification was validated earlier, but email delivery was not prioritised because Supabase Auth is expected to replace or reduce that path.
- Supabase Auth should be treated as a separate migration, not as part of the completed Postgres cutover.
- Do not remove or bypass the app-owned `user` table; it owns profile data, admin status, preferences, collection ownership, wishlist relationships, and API token ownership.
- Prefer a phased dual-run migration with explicit `user` to Supabase Auth identity linkage.
- Future auth work must plan:
  - linkage from existing `user` rows to Supabase Auth identities
  - browser session transition from Flask auth to Supabase Auth
  - replacement of email confirmation and password reset flows
  - mobile/API token coexistence or replacement strategy
  - rollback/transition plan that preserves access
  - protection of current admin, profile, account, and collection flows

## Error Handling
- Wrap database mutations in try/except; on failure log with context (operation, user id), call `db.session.rollback()`, and surface user-safe flash messages.
- Validate inputs with WTForms plus domain-level checks before committing.
- CSV import preview must stay dry-run only; confirm re-validates the CSV before applying.
- Centralise error pages for 400/403/404/500 in `templates/errors/` with handlers registered in the app factory.
- Background scripts use structured logging and exit non-zero on fatal errors.

## Timezone Rules
- Step buckets are stored in UTC with an IANA timezone per bucket.
- Attribution uses the bucket’s timezone to derive the local date (supports travel and DST).
- If a bucket omits timezone, fall back to `User.timezone` (default `Europe/London`).
- Release detail pages intentionally display release dates as date-only; region times/timezones may be stored in `ReleaseRegion` but are not surfaced in the current release-detail UI.

## Cost Control for External APIs
- Prefer cached `SneakerDB` data; avoid extra KicksDB calls for materials.
- Prefer cached release-level data (`Release`, `AffiliateOffer`, `ReleaseSalePoint`, `ReleaseMarketStats`, `ReleaseSizeBid`) and refresh on staleness windows rather than per-view user-specific fetches.
- Release detail pages auto-refresh KicksDB-backed market data when stale, but should avoid duplicate StockX/GOAT calls and unnecessary GOAT requests when StockX data is sufficient.
- `release_updater.py` and `services/release_ingestion_service.py` enforce request caps and backfill thresholds to protect quota.

## Feature Flags / Hidden UI
- Heat Factor UI is currently hidden while the backend logic is parked; do not surface Heat badges until re-enabled.
- Admin diagnostics and manual market refresh are admin-only on release detail pages.

## Privacy Notes
- Steps data is stored as daily buckets only; no GPS or continuous tracking.
- Exposure events store only user-entered wet/dirty flags and severity; no location or weather data.
- Mobile API tokens are stored hashed (SHA-256); plaintext is shown once at creation.

## Release Calendar / CSV Import Rules
- Admins can add/edit releases manually or via CSV import.
- CSV template download includes a `__FORMAT_GUIDE__` row; importer must ignore that row.
- Release CSV import supports US/UK/EU date/time/timezone, per-region prices/currencies, and per-region retailer links.
- CSV preview is authoritative for validation; import confirm must not bypass preview validation.
- Non-blank CSV values overwrite; blank CSV values do not clear existing release fields.
- `skip_existing` skips matched releases during apply; it does not delete or clear existing region data.
- `delete all releases` on the calendar currently hides visible releases by setting `Release.is_calendar_visible = False`; it is not a destructive delete.

## News / Articles Content Rules
- Admins write in Markdown; sanitisation happens via `services/article_render.py`.
- JSON-LD schema is hybrid: global Organisation/WebSite stored in `SiteSchema`, per-article Product/FAQ/Video are optional.
- Article schema is generated automatically from the article data.

## How to Add a New Ingestion Source
1. Extend `services/kicks_client.py` with a minimal client method.
2. Update `services/release_ingestion_service.py` and/or `release_updater.py` to call it.
3. Store the response in existing release-linked models or add a migration.
4. Update docs (`docs/ARCHITECTURE.md`, `docs/MODULE_MAP.md`, `docs/DECISIONS.md`).

## Migrations and Tests
- Always add Alembic migrations for schema changes: `flask db upgrade`.
- Run tests with `python -m pytest` and update tests when behaviour changes.
- For release behaviour changes, check at least release calendar, release detail, wishlist, sneaker detail, and CSV import tests.

## Debugging Tips
- Host/CSRF issues: some features behave differently on `localhost` vs `127.0.0.1`. Prefer `127.0.0.1` for local API calls.
- If you need remote access for webhooks/testing, use a tunnel (e.g. Cloudflared).
- If release detail market data is missing, inspect the release’s `source`, `source_product_id`, `source_slug`, and fetch timestamps before changing template logic.

## CSS Architecture
- Prefer BEM naming (`block__element--modifier`) in templates and styles.
- CSS is embedded in templates in several places; keep new styles consistent and avoid adding another styling system.
- Soletrak styling is token-driven via `static/brand/soletrak-tokens.css`.
- Theme source of truth is `data-theme` on `<html>` (`light`/`dark`); legacy hooks and `--wearhouse-*`/`.wearhouse-*` are compatibility only.

## Stack Expectations
- Core stack: Flask + Jinja2, SQLAlchemy + Alembic, WTForms, Flask-Login/Mail/CSRF.
- Databases: Supabase Postgres for production/current runtime; SQLite only for local dev/tests and archival fallback. Normalise `DATABASE_URL` to `postgresql://…`; do not rely on hardcoded secrets in production.
- Deployment: Gunicorn entry via `wsgi.py`; pytest for testing; SendGrid for email.
- Background work runs as standalone CLI utilities; add a worker/queue only when needed.

## Collaboration Rules
- Before proposing code, locate the relevant call sites and list the files you will touch.
- Prefer existing utilities over introducing new dependencies.
- Keep release-region/currency selection logic centralised; do not reimplement it in templates or individual routes.

## Context Checklist
- Update `docs/MODULE_MAP.md` when adding/removing modules, services, routes, scripts, templates, or models.
- Update `docs/ARCHITECTURE.md` when release ingestion, CSV import, market-data refresh, or region-aware display changes.
- Update `docs/DECISIONS.md` when a non-obvious product/architecture rule becomes enforced in code.
- Keep `docs/AI_CONTEXT.md` aligned with actual folder structure and conventions.
- Add or update Alembic migrations whenever schema changes are introduced.
- Document KicksDB ingestion, refresh windows, and quota-control changes.
- Note new admin-only pages or routes in `docs/ARCHITECTURE.md`.
- Keep shared release partials aligned with the real templates in use.
