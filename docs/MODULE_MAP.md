# Module Map

Quick reference for where things live and how they connect.

- `app.py` — Creates Flask app, configures extensions, registers blueprints (`routes/auth_routes.py`, `routes/main_routes.py`, `routes/sneakers_routes.py`).
- `config.py` — Configuration source (secrets, DB URI, mail, API keys); uses environment variables.
- `extensions.py` — Shared extension instances (`db`, `migrate`, `login_manager`, `mail`, `csrf`).
- `models.py` — SQLAlchemy models: `User`, `Sneaker`, `Release`, `SneakerDB`, and `wishlist_items` join table.
- `forms.py` — WTForms for auth, profile, sneakers, releases, and an `EmptyForm` for CSRF-only cases.
- `routes/auth_routes.py` — Auth flows (register/login/logout, password reset, email confirmation) and email helpers.
- `routes/main_routes.py` — Home/profile pages, release calendar, admin add-release.
- `routes/sneakers_routes.py` — Collection/rotation CRUD, wishlist/rotation toggles, AJAX helpers, SneakerDB search.
- `templates/` — Jinja templates and partials (email templates under `templates/email/`).
- `static/` — Assets (currently `images/`; CSS/JS to be organized per standards).
- `uploads/` — User-uploaded sneaker/release images (stored by UUID filename).
- `email_utils.py` — SendGrid email sender.
- `decorators.py` — `admin_required` decorator.
- `utils.py` — Shared helpers (file type validation).
- `services/kicks_client.py` — KicksDB API client (StockX/GOAT search + detail).
- `services/sneaker_lookup_service.py` — Local-first lookup, scoring, staleness checks, and SneakerDB upserts.
- `migrations/` — Alembic env and migration scripts.
- `tests/` — Pytest suite covering auth, profile, sneakers, releases, wishlist, and smoke tests.
- Scripts: `release_updater.py` (API ingest), `scraper.py` (scrape drop dates), `sneaker_db_updater.py` (SneakerDB sync), `import_data.py` (data import), `make_admin.py` (elevate a user).
