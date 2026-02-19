# AI Context and Coding Standards

These rules guide how we structure and extend the codebase.

## Naming
- Files and modules use `lower_snake_case` (e.g., `auth_routes.py`, `release_service.py`).
- Directories use lowercase plurals (e.g., `routes/`, `templates/`, `static/`, `tests/`, `uploads/`).
- Templates are mostly flat in `templates/` with shared partials prefixed `_` (e.g., `_single_sneaker_card.html`); email templates live in `templates/email/`.
- Static assets live under `static/brand/`, `static/images/`, and `static/js/`; uploads keep UUID filenames in `uploads/`.

## Component Structure (Flask)
- App factory lives in `app.py`; shared extensions in `extensions.py`; configuration in `config.py`.
- One blueprint per domain (e.g., `auth`, `main`, `sneakers`). Forms are centralised in `forms.py`; services live in `services/`.
- Models live in `models.py` (e.g., `Release`, `Sneaker`, `SneakerWear`, `StepBucket`, `ExposureEvent`).
- Background jobs and utilities live in scripts (e.g., `release_updater.py`, `set_fx_rate.py`, `make_admin.py`).

## Where to Make Changes
- **External data & caching**: `services/kicks_client.py`, `services/sneaker_lookup_service.py`, `services/release_ingestion_service.py`.
- **Steps ingestion + attribution**: `routes/sneakers_routes.py` for API endpoints, `services/steps_attribution_service.py` for logic.
- **Exposure events**: `services/exposure_service.py` for attribution; UI surfaces in `templates/_single_sneaker_card.html` and `templates/sneaker_detail.html`.
- **Materials**: `services/materials_extractor.py` and `SneakerDB` fields; UI on sneaker detail.
- **Mobile tokens**: `services/api_tokens.py`, profile UI in `templates/profile.html`.

## State Management
- Authentication state is managed via Flask-Login; avoid storing user data directly in sessions.
- API auth supports bearer tokens (`UserApiToken`) for mobile step sync.
- Database access goes through SQLAlchemy models and scoped sessions (`db.session`) only.

## Error Handling
- Wrap database mutations in try/except; on failure log with context (operation, user id), call `db.session.rollback()`, and surface user‑safe flash messages.
- Validate inputs with WTForms plus domain‑level checks (e.g., uniqueness) before committing.
- Centralise error pages for 400/403/404/500 in `templates/errors/` with handlers registered in the app factory.
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

## CSS Architecture
- Prefer BEM naming (`block__element--modifier`) in templates and styles.
- CSS is currently minimal and embedded in existing templates; keep new styles consistent with current layout and avoid inline styles when possible.

## Stack Expectations
- Core stack: Flask + Jinja2, SQLAlchemy + Alembic, WTForms, Flask‑Login/Mail/CSRF.
- Databases: PostgreSQL for production; SQLite only for local dev/tests. Normalise `DATABASE_URL` to `postgresql://…`; do not rely on hardcoded secrets in production.
- Deployment: Gunicorn entry via `wsgi.py`; pytest for testing; SendGrid for email.
- Background work runs as standalone CLI utilities; add a worker/queue only when needed.

## Collaboration Rules
- Before proposing code, locate the relevant call sites and list the files you will touch.
- Prefer existing utilities over introducing new dependencies.

## Context Checklist
- Update `docs/MODULE_MAP.md` when adding/removing modules, services, routes, scripts, or models.
- Update `docs/ARCHITECTURE.md` when data flow or major features change.
- Update `requirements.txt` only when new dependencies are added.
- Keep `ai-context.md` aligned with actual folder structure and conventions.
- Add or update Alembic migrations whenever schema changes are introduced.
- Document KicksDB ingestion or pricing logic changes (e.g., `release_updater.py`, `services/kicks_client.py`) to keep quota guidance current.
- Note new admin‑only pages or routes in `docs/ARCHITECTURE.md`.
