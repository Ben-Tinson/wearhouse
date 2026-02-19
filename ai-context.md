# AI Context and Coding Standards

These rules guide how we structure and extend the codebase.

## Naming
- Files and modules use `lower_snake_case` (e.g., `auth_routes.py`, `release_service.py`).
- Directories use lowercase plurals (e.g., `routes/`, `templates/`, `static/`, `tests/`, `uploads/`).
- Templates are grouped by feature (`templates/auth/`, `templates/sneakers/`, `templates/profile/`) with shared partials in `templates/_partials/`.
- Static assets live under `static/css/`, `static/js/`, `static/images/`; uploads keep UUID filenames in `uploads/`.

## Component Structure (Flask)
- App factory lives in `app.py` (or `app/__init__.py` if packaged); shared extensions in `extensions.py`; configuration in `config.py`.
- One blueprint per domain (e.g., `auth`, `main`, `sneakers`, `admin`). Each blueprint has its own `routes.py`, `forms.py`, `services.py` (business logic), and `templates/<blueprint>/`.
- Models stay in `models.py` or split by domain (`models/user.py`, `models/sneaker.py`, etc.) with an import shim for convenience.
- Background jobs and utilities live in `jobs/` or `scripts/` (e.g., `release_updater.py`, `scraper.py`, `make_admin.py`).

## State Management
- Authentication state is managed via Flask-Login; avoid storing user data directly in sessions.
- Request state flows through WTForms and server-rendered templates; avoid global mutable state.
- Database access goes through SQLAlchemy models and scoped sessions (`db.session`) only.

## Error Handling
- Wrap database mutations in try/except; on failure log with context (operation, user id), call `db.session.rollback()`, and surface user-safe flash messages.
- Validate inputs with WTForms plus domain-level checks (e.g., uniqueness) before committing.
- Centralize error pages for 400/403/404/500 in `templates/errors/` with handlers registered in the app factory.
- Background scripts use structured logging and exit non-zero on fatal errors.

## CSS Architecture
- Prefer BEM naming (`block__element--modifier`) in templates and styles.
- Organize styles by feature and layer: `static/css/base/` (reset, variables, typography), `static/css/components/` (buttons, cards, forms), `static/css/pages/` (page-specific overrides).
- Define design tokens once in `variables.css` (colors, spacing, typography) and import where needed.
- Avoid inline styles; use utility classes sparingly for layout helpers only.

## Stack Expectations
- Core stack: Flask + Jinja2, SQLAlchemy + Alembic, WTForms, Flask-Login/Mail/CSRF.
- Databases: PostgreSQL for production; SQLite only for local dev/tests. Normalize `DATABASE_URL` to `postgresql://…`; do not rely on hardcoded secrets in production.
- Deployment: Gunicorn entry via `wsgi.py`; pytest for testing; SendGrid for email.
- Background work runs as standalone CLI utilities; add a worker/queue only when needed.

## Collaboration Rules
- Before proposing code, locate the relevant call sites and list the files you will touch.
- Prefer existing utilities over introducing new dependencies.
