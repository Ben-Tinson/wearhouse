# Architecture Overview

This document captures the current structure of the sneaker collection app so new changes have shared context.

## Entry Points
- `app.py`: Flask app factory; registers extensions and blueprints.
- `wsgi.py`: Gunicorn entry (`wsgi:app`).
- `Procfile`: Process declaration (`web: gunicorn wsgi:app`).
- CLI/scripts: `release_updater.py`, `scraper.py`, `import_data.py`, `sneaker_db_updater.py`, `make_admin.py` (all bootstrap the app and run one-off tasks).

## Core Modules
- `config.py`: Environment-driven configuration (DB URL, secrets, mail, API keys).
- `extensions.py`: Initialized extensions (SQLAlchemy, Migrate, LoginManager, Mail, CSRF).
- `models.py`: SQLAlchemy models (`User`, `Sneaker`, `Release`, `SneakerDB`, `wishlist_items`).
- `forms.py`: WTForms definitions for auth, profile, sneakers, releases, and utility forms.
- `routes/`: Blueprints for `auth`, `main`, and `sneakers` domains.
- `templates/`: Jinja templates (page views and partials).
- `static/`: Assets (currently `images/`; CSS/JS to be layered per standards).
- `uploads/`: User-uploaded images (UUID filenames).
- `email_utils.py`: SendGrid email helper.
- `decorators.py`: `admin_required` guard.
- `utils.py`: Shared helpers (e.g., file type validation).
- `migrations/`: Alembic environment and migration history.

## Data Flow
1) Request enters a blueprint route.  
2) Input validated via WTForms.  
3) Business logic uses SQLAlchemy models (`db.session`).  
4) Responses rendered via Jinja templates; AJAX routes return JSON.  
5) Side effects: file uploads to `uploads/`; email via SendGrid; scheduled/CLI scripts ingest external data into `Release`/`SneakerDB`.

## Build, Run, Test
- Install deps: `pip install -r requirements.txt`
- Run dev server: `flask run` (via `create_app`) or `gunicorn wsgi:app`
- Migrations: `flask db upgrade` (Alembic)
- Tests: `python -m pytest`

## Conventions (see ai-context.md)
- Naming: `lower_snake_case` files; lowercase plural dirs.
- Structure: one blueprint per domain with routes/forms/services/templates; shared extensions/config centralized.
- Error handling: wrap DB mutations, log with context, rollback on failure, user-safe flashes.
- CSS: BEM naming; organize `static/css/base|components|pages`; tokens in `variables.css`.
- Stack: Flask/Jinja2, SQLAlchemy/Alembic, WTForms, Flask-Login/Mail/CSRF; Postgres in prod, SQLite for dev/tests.

## Known Danger Zones
- Duplicate `verify_reset_password_token` in `models.py`; later definition overrides earlier one.
- `routes/sneakers_routes.py` has DB commits without rollback guards and an undefined variable in `add_to_rotation`.
- `app.py` debug prints include sensitive config; remove for prod.
- `config.py` still allows a default SECRET_KEY; enforce env in prod.
- Scripts (`scraper.py`, `release_updater.py`) bypass SSL or may import `db` incorrectly; review before running in prod.
- Two SQLite files present (`instance/site.db` and `site.db`) — ensure the intended DB is used.
