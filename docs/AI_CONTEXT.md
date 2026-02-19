# AI Context and Coding Standards

These rules guide how we structure and extend the codebase.

## Naming
- Files and modules use `lower_snake_case` (e.g., `auth_routes.py`, `release_service.py`).
- Directories use lowercase plurals (e.g., `routes/`, `templates/`, `static/`, `tests/`, `uploads/`).
- Templates are mostly flat in `templates/` with shared partials prefixed `_` (e.g., `_single_sneaker_card.html`); email templates live in `templates/email/`.
- Static assets live under `static/brand/`, `static/images/`, and `static/js/`; uploads keep UUID filenames in `uploads/`.

## Component Structure (Flask)
- App factory lives in `app.py`; shared extensions in `extensions.py`; configuration in `config.py`.
- One blueprint per domain (`auth`, `main`, `news`, `sneakers`). Forms are centralised in `forms.py`; services live in `services/`.
- Models live in `models.py` (e.g., `Release`, `Sneaker`, `SneakerWear`, `StepBucket`, `ExposureEvent`, `Article`).
- Background jobs and utilities live in scripts (e.g., `release_updater.py`, `set_fx_rate.py`, `make_admin.py`).

## Where to Make Changes
- **External data & caching**: `services/kicks_client.py`, `services/sneaker_lookup_service.py`, `services/release_ingestion_service.py`.
- **Steps ingestion + attribution**: `routes/sneakers_routes.py` for API endpoints, `services/steps_attribution_service.py` for logic.
- **Exposure events**: `services/exposure_service.py` for attribution; UI surfaces in `templates/_single_sneaker_card.html` and `templates/sneaker_detail.html`.
- **Materials**: `services/materials_extractor.py` and `SneakerDB` fields; UI on sneaker detail.
- **Mobile tokens**: `services/api_tokens.py`, profile UI in `templates/profile.html`.
- **News/Articles**: `routes/news_routes.py` + `templates/news/` + `templates/admin/news_form.html`.
- **Sneaker health scoring**: Source of truth is `docs/SNEAKER_HEALTH.md`.

## State Management
- Authentication state is managed via Flask‑Login; avoid storing user data directly in sessions.
- API auth supports bearer tokens (`UserApiToken`) for mobile step sync.
- Database access goes through SQLAlchemy models and scoped sessions (`db.session`) only.

## Error Handling
- Wrap DB mutations in try/except; on failure log with context (operation, user id), call `db.session.rollback()`, and surface user‑safe flash messages.
- Validate inputs with WTForms plus domain‑level checks (e.g., uniqueness) before committing.
- Background scripts use structured logging and exit non‑zero on fatal errors.

## Timezone Rules
- Step buckets are stored in UTC with an IANA timezone per bucket.
- Attribution uses the bucket’s timezone to derive the local date (supports travel and DST).
- If a bucket omits timezone, fall back to `User.timezone` (default `Europe/London`).

## Cost Control for External APIs
- Prefer cached `SneakerDB` data; avoid extra KicksDB calls for materials.
- Cap pagination for large ingests; guard request counts in scripts.
- Log request counts for long‑running ingestion jobs.

## Privacy Notes
- Steps data is stored as daily buckets only; no GPS or continuous tracking.
- Exposure events store only user‑entered wet/dirty flags and severity; no location or weather data.
- Mobile API tokens are stored hashed (SHA‑256); plaintext is shown once at creation.

## News / Articles Content Rules
- Admins write in Markdown; sanitisation happens via `services/article_render.py`.
- JSON‑LD schema is hybrid: global Organisation/WebSite stored in `SiteSchema`, per‑article Product/FAQ/Video are optional.
- Article schema is generated automatically from the article data.

## How to Add a New Ingestion Source
1) Extend `services/kicks_client.py` with a minimal client method.
2) Update `services/release_ingestion_service.py` or `services/sneaker_lookup_service.py` to call it.
3) Store the response in existing models or add a migration.
4) Update docs (`docs/ARCHITECTURE.md`, `docs/MODULE_MAP.md`).

## Migrations and Tests
- Always add Alembic migrations for schema changes: `flask db upgrade`.
- Run tests with `python -m pytest` and update tests when behaviour changes.

## Debugging Tips
- Host/CSRF issues: some features behave differently on `localhost` vs `127.0.0.1`. Prefer `127.0.0.1` for local API calls.
- If you need remote access for webhooks/testing, use a tunnel (e.g. Cloudflared).

## CSS Architecture
- Prefer BEM naming (`block__element--modifier`) in templates and styles.
- CSS is currently embedded in templates; keep new styles consistent and scoped.

## Stack Expectations
- Core stack: Flask + Jinja2, SQLAlchemy + Alembic, WTForms, Flask‑Login/Mail/CSRF.
- Databases: PostgreSQL for production; SQLite only for local dev/tests.
- Deployment: Gunicorn entry via `wsgi.py`; pytest for testing; SendGrid for email.

## Context Checklist
- Update `docs/MODULE_MAP.md` when adding/removing modules, services, routes, scripts, or models.
- Update `docs/ARCHITECTURE.md` when data flow or major features change.
- Update `requirements.txt` only when new dependencies are added.
- Keep `docs/AI_CONTEXT.md` aligned with actual folder structure and conventions.
- Add or update Alembic migrations whenever schema changes are introduced.
- Document KicksDB ingestion or pricing logic changes to keep quota guidance current.
- Note new admin‑only pages or routes in `docs/ARCHITECTURE.md`.
