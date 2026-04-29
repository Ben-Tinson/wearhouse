# 1. Overview

Soletrak currently uses Supabase Postgres for the active runtime database, with SQLite retained for local development/tests and archival fallback. SQLAlchemy remains the ORM layer and Alembic remains the schema migration system. The default local database is `instance/site.db`, and the test config uses in-memory SQLite.

SQLAlchemy is the source of truth for the application data model in `models.py`, while Alembic captures schema evolution and should remain aligned with model changes. The app initializes Alembic with `render_as_batch=True`, which is useful for SQLite compatibility but also means some migration patterns reflect SQLite constraints more than an eventual PostgreSQL-first production posture.

PostgreSQL is the production database because it provides stronger concurrency characteristics, more predictable constraint enforcement, better indexing options, better timezone and numeric behavior, and a clearer path to production-grade operational tooling. Supabase is now the managed PostgreSQL host and is also the planned next platform step for managed auth.

Current implementation: active database runtime is Supabase Postgres project `sjwdvsefjlflgavshiyy`; previous Supabase project `mizyioplztuzycipfdsd` is fallback/reference only until retirement; SQLite remains the active local and test engine only; Flask backend and Flask-Login remain live; app-managed password hashes are the current identity system; Supabase Auth is planned next but not implemented.

Completed state: SQLite is local-dev/tests and archival fallback only, while PostgreSQL on Supabase is the source of truth. Current operational backup/restore preference is full Postgres dump/restore. Change timing: `Documentation only`.

# 2. Database principles

- PostgreSQL is the production source of truth; SQLite is only for local dev/tests and archival fallback.
- SQLAlchemy models and Alembic migrations must stay aligned; model-vs-migration drift should be treated as a schema risk.
- Production schema changes should only happen through reviewed Alembic migrations.
- Application logic should remain primarily in Flask routes/services unless a rule is materially safer as a database constraint, index, or trigger.
- Per-user security must never rely only on frontend logic.
- Shared global data and user-private data must remain explicitly separated.
- Delete semantics must be explicit: some flows hard-delete rows, while others intentionally soft-hide them.
- Auth identity and app-owned user/profile data should remain distinct concepts during the Supabase transition.
- The app-owned `user` table remains a core domain/account table and must not be assumed removable when Supabase Auth is introduced.
- Future auth migration should be cautious and phased, not a hard replacement.
- Auth assumptions are spread across routes, decorators, profile flows, admin checks, API token flows, templates, and tests; readiness analysis is required before auth code changes.

Recommended future state: tighten DB-enforced constraints where the app already assumes strong invariants, especially for identity, ownership, and enumerated values. Change timing: usually `Should be done during Postgres migration`.

# 3. Environment and platform model

- Local development database: SQLite via `sqlite:///instance/site.db`.
- Test database: SQLite in-memory via `sqlite:///:memory:` unless changed later.
- Staging/production database: PostgreSQL.
- Managed host: Supabase Postgres.
- Current active Supabase project ref: `sjwdvsefjlflgavshiyy`.
- Previous Supabase project ref: `mizyioplztuzycipfdsd`, retained temporarily as fallback/reference.
- Flask/SQLAlchemy reads `DATABASE_URL`; if it starts with `postgres://`, `config.py` normalizes it to `postgresql://`.
- Alembic runs through Flask-Migrate and currently initializes with `render_as_batch=True`.

Environment variables relevant to DB connectivity:

- `DATABASE_URL`
- `SECRET_KEY`

Environment variables relevant to adjacent platform behavior but not DB connectivity:

- `SENDGRID_API_KEY`
- `MAIL_DEFAULT_SENDER`
- `KICKS_API_KEY`
- `KICKS_API_BASE_URL`
- `KICKS_STOCKX_PRICES_ENABLED`
- `RETAILED_API_KEY`
- `RAPIDAPI_KEY`

Recommended operating state: keep URL normalization in place for compatibility, but standardize all deployed environments on explicit `postgresql://` URLs. Change timing: `Documentation only`.

# 4. Supabase adoption strategy

## 4.1 Initial migration approach

- Keep Flask as the primary application server.
- Keep SQLAlchemy as the ORM.
- Keep Alembic as the schema migration system.
- Database migration from SQLite to Supabase Postgres is complete.
- The completed cutover used a public-schema logical dump/restore from the previous Supabase project into fresh target `sjwdvsefjlflgavshiyy`.
- Do not rewrite the app around direct client-side database access in phase one.
- Do not broadly expose core app tables through frontend-to-database access in the initial cutover.

Completed approach: database engine migration is complete; server-side Flask domain logic remains in place. Next Supabase feature evaluation should focus on Auth. Change timing: `Documentation only`.

## 4.2 Supabase services in scope

### Adopt now

- Supabase Postgres as the managed PostgreSQL host.

### Adopt later / optional

- Supabase Auth, if the team decides to move identity off Flask-Login/password-hash storage.
- Supabase Storage, if sneaker images or future user uploads should move off local uploads.
- Row Level Security design, especially for tables that may later be exposed via Supabase APIs.
- Realtime only if a concrete use case emerges.
- Edge Functions only if server-side logic genuinely needs to run near Supabase rather than Flask.

### Not in scope yet

- Broad direct browser/mobile access to core domain tables.
- Rewriting the application into a Supabase-first client architecture.

# 5. Supabase Auth architecture

Supabase Auth is being considered because the current app stores password hashes itself, uses Flask-Login sessions, and uses `itsdangerous` tokens for password reset and email confirmation. Supabase Auth would provide managed identity, email verification flows, session issuance, and social login options, but it should not replace app-owned domain data and authorization rules.

Current implementation warning: Supabase Auth is not live. Flask-Login, app-owned `User`, `User.is_admin`, and `UserApiToken` remain active. Do not design migrations that remove the `user` table or bypass existing admin/profile/token ownership without an explicit phased transition.

## Current auth model

- Identity is fully app-managed in the `user` table.
- Passwords are stored as `password_hash` on `User`.
- Flask-Login loads users by integer `user.id`.
- Password reset and email confirmation tokens are generated with `itsdangerous`, not stored in the database.
- Mobile/API access uses `UserApiToken` bearer tokens stored as SHA-256 hashes.
- Admin authority is determined by `User.is_admin`.

Current implementation note: `User.verify_reset_password_token` now has a single implementation in `models.py`; the earlier duplicate-definition/debug-print issue has been removed as part of pre-migration hardening.

## Transitional hybrid model

- Supabase Auth becomes the identity provider for new sessions and password/email verification flows.
- Soletrak keeps its application `User` table for profile data, roles, preferences, billing/plan state, collection ownership, and other domain data.
- The `User` table gains a nullable linkage field such as `supabase_auth_user_id` (UUID text/UUID column in Postgres).
- Flask remains the primary backend and resolves app authorization against app-owned tables.
- Existing bearer-token mobile flows are reviewed rather than assumed to disappear immediately.

Recommended transition note: retain app-level authorization in Flask even if authentication moves to Supabase. Change timing: `Should be deferred until after Supabase Auth cutover`, except preparatory documentation and mapping work which is `Documentation only`.

## Target model after migration

- Supabase Auth owns core identity, password reset, email verification, social login, and primary session issuance.
- Soletrak `User` remains as the application profile/domain record.
- `User` references the Supabase auth user id.
- App roles and admin permissions remain explicit in application data, not solely in auth metadata.
- User-private domain tables continue to link to the app-level `User` row unless a later redesign intentionally changes that boundary.

## Field ownership boundary

Auth-owned in target state:

- login credential
- password lifecycle
- email verification status
- provider identities
- auth session lifecycle

App-owned in target state:

- username, if retained as an app-facing handle
- first/last name
- marketing preferences
- preferred currency
- preferred region
- timezone
- admin flag / roles
- billing or plan state
- sneaker ownership and all domain records

## Password resets, email verification, social login, sessions

Current implementation:

- password reset: `itsdangerous` token, email link, password hash updated in app
- initial email confirmation: `itsdangerous` token, updates `is_email_confirmed`
- pending email change: tokenized confirmation flow against `pending_email`
- social login: not implemented
- session handling: Flask-Login session cookie plus app-owned user identity

Recommended future state:

- password resets and email verification should move to Supabase Auth
- social login should be handled by Supabase Auth if adopted
- Flask should accept verified Supabase identity and map it to an app `User`
- bearer-token mobile flows should be reviewed for replacement, coexistence, or narrowing of scope

## Admin roles

Admin roles should remain in app tables rather than only in auth metadata. `User.is_admin` already represents this pattern. A future richer role system should still remain app-owned. Change timing: `Documentation only` for the architectural rule; `Should be deferred until after Supabase Auth cutover` for any auth-metadata integration work.

## Mobile auth and bearer tokens

Current implementation:

- mobile sync uses `UserApiToken`
- only token hashes are stored
- scopes are currently string-based, defaulting to `steps:write`
- bearer tokens can authenticate to step bucket ingestion and attribution recompute endpoints

Recommended future state:

- do not assume these tokens disappear immediately with Supabase Auth
- decide whether mobile clients will use Supabase JWTs, retained app bearer tokens, or a hybrid model

## Recommended approach

- Supabase Auth should become the identity provider.
- Soletrak should keep an application `User` table for app-specific data.
- The app `User` table should reference the Supabase auth user id.
- Authorization for roles and app features should remain explicit in the application layer and, where later useful, in database rules.
- Existing custom mobile/API token flows should be reviewed explicitly rather than removed by assumption.

Change timing:

- `supabase_auth_user_id` linkage: `Should be deferred until after Supabase Auth cutover` unless introduced earlier as a nullable preparatory column.
- replacing password/email/session flows: `Should be deferred until after Supabase Auth cutover`.
- documenting boundaries now: `Documentation only`.

# 6. Entity inventory

## Identity and users

### `user`

- Purpose: primary application user/profile record and current identity record.
- Owning domain: Identity and profile.
- Classification: user-owned record; core business data.

### `user_api_token`

- Purpose: hashed bearer tokens for mobile/API step sync.
- Owning domain: Identity / API access.
- Classification: user-owned security metadata; integration metadata.

### `user_api_usage`

- Purpose: per-user/day counters for rate-limited actions such as resale refresh.
- Owning domain: Identity / application controls.
- Classification: system-generated user-scoped metadata.

## Sneakers and ownership

### `sneaker`

- Purpose: a user’s owned sneaker record.
- Owning domain: Collection/ownership.
- Classification: user-owned; core business data.

### `wishlist_items`

- Purpose: join table from users to releases.
- Owning domain: Wishlist.
- Classification: user-owned association; core business data.

### `sneaker_note`

- Purpose: notes attached to a sneaker.
- Owning domain: Collection.
- Classification: user-owned; core business data.

### `sneaker_sale`

- Purpose: recorded sale/resale transactions tied to a sneaker and optionally a release.
- Owning domain: Collection / resale.
- Classification: user-owned transactional data, with nullable sneaker linkage.

### `sneaker_wear`

- Purpose: daily wear events for a sneaker.
- Owning domain: Collection / wear tracking.
- Classification: user-owned; core business data.

### `sneaker_clean_event`

- Purpose: cleaning events for a sneaker.
- Owning domain: Health / care.
- Classification: user-owned; core business data.

### `sneaker_damage_event`

- Purpose: reported damage events for a sneaker.
- Owning domain: Health / maintenance.
- Classification: user-owned; core business data.

### `sneaker_repair_event`

- Purpose: repair events for a sneaker.
- Owning domain: Health / maintenance.
- Classification: user-owned; core business data.

### `sneaker_repair_resolved_damage`

- Purpose: many-to-many mapping from repair events to resolved damage events.
- Owning domain: Health / maintenance.
- Classification: user-owned linkage data.

### `sneaker_expense`

- Purpose: costs attached to a sneaker.
- Owning domain: Collection finances.
- Classification: user-owned transactional data.

## Health / wear / exposure

### `sneaker_health_snapshot`

- Purpose: persisted health score snapshots and component values.
- Owning domain: Health scoring.
- Classification: system-generated derived data.

### `step_bucket`

- Purpose: imported step buckets from mobile/health sources.
- Owning domain: Steps sync.
- Classification: user-owned source-like telemetry data.

### `step_attribution`

- Purpose: attributed steps from step buckets to sneakers.
- Owning domain: Steps attribution.
- Classification: system-generated derived data.

### `exposure_event`

- Purpose: daily user-entered wet/dirty/stain exposure record.
- Owning domain: Exposure tracking.
- Classification: user-owned source data.

### `sneaker_exposure_attribution`

- Purpose: per-sneaker distribution of daily exposure effects.
- Owning domain: Exposure attribution.
- Classification: system-generated derived data.

## Releases and market data

### `release`

- Purpose: shared release record used by calendar, detail pages, wishlist, and release-linked sneaker context.
- Owning domain: Releases / shared market catalog.
- Classification: shared global data; core business data with ingestion metadata.

### `release_region`

- Purpose: region-specific release timing rows.
- Owning domain: Releases.
- Classification: shared global data.

### `release_price`

- Purpose: region/currency retail price rows for releases.
- Owning domain: Releases.
- Classification: shared global data.

### `affiliate_offer`

- Purpose: aftermarket, retailer, and raffle links for releases.
- Owning domain: Releases / market integrations.
- Classification: shared global data; integration metadata.

### `release_size_bid`

- Purpose: cached size-level ask/bid rows.
- Owning domain: Market data cache.
- Classification: shared global derived/cache data.

### `release_sale_point`

- Purpose: cached historical sale points.
- Owning domain: Market data cache.
- Classification: shared global derived/cache data.

### `release_sales_monthly`

- Purpose: cached monthly sales averages.
- Owning domain: Market data cache.
- Classification: shared global derived/cache data.

### `release_market_stats`

- Purpose: cached aggregate release market metrics.
- Owning domain: Market data cache.
- Classification: shared global derived/cache data.

### `exchange_rate`

- Purpose: currency conversion rates used by display logic.
- Owning domain: Financial utilities.
- Classification: shared reference/integration data.

## Content / articles

### `article`

- Purpose: authored news/article record with SEO fields.
- Owning domain: Content.
- Classification: admin-managed; core content data.

### `article_block`

- Purpose: ordered content blocks within an article.
- Owning domain: Content.
- Classification: admin-managed child data.

### `site_schema`

- Purpose: site-wide JSON-LD snippets.
- Owning domain: Content / SEO.
- Classification: admin-managed configuration data.

## Ingestion / sync / admin reference

### `sneaker_db`

- Purpose: shared master sneaker lookup cache imported from external data sources.
- Owning domain: Lookup / ingestion.
- Classification: shared global reference/cache data.

# 7. Table-by-table schema dictionary

Consistent format:

- Table name
- Model name
- Purpose
- Columns
- Primary key
- Foreign keys
- Unique constraints
- Indexes
- Enum/check constraints
- Business notes
- Postgres migration notes

## `wishlist_items`

- Table name: `wishlist_items`
- Model name: association table only
- Purpose: many-to-many join from `user` to `release`
- Columns:
  - `user_id` - Integer - required
  - `release_id` - Integer - required
  - `created_at` - DateTime - required - server default `now()`
- Primary key: composite `user_id`, `release_id`
- Foreign keys:
  - `user_id -> user.id`
  - `release_id -> release.id`
- Unique constraints: implicit via composite primary key
- Indexes: none declared separately
- Enum/check constraints: none
- Business notes: stores when a user added a release to wishlist; “delete all releases” intentionally does not clear this table
- Postgres migration notes: join-table shape is portable; consider adding an index on `release_id` if reverse lookups become hot in production. Change timing: `Should be done during Postgres migration` only if query plans show need.

## `user`

- Table name: `user`
- Model name: `User`
- Purpose: current identity record plus app-owned user profile
- Columns:
  - `id` - Integer - required
  - `username` - String(80) - required
  - `password_hash` - String(256) - required
  - `email` - String(120) - required
  - `first_name` - String(50) - required
  - `last_name` - String(50) - required
  - `marketing_opt_in` - Boolean - required - default `False`
  - `pending_email` - String(120) - nullable
  - `is_email_confirmed` - Boolean - required - default `False`
  - `is_admin` - Boolean - required - default `False`
  - `preferred_currency` - String(3) - required - default `GBP`
  - `preferred_region` - String(3) - required - default `UK` - server default `UK`
  - `timezone` - String(64) - required - default `Europe/London`
- Primary key: `id`
- Foreign keys: none
- Unique constraints:
  - `username`
  - `email`
  - `pending_email` is unique in migration history and intended to be unique in current DB
- Indexes: none declared on the model
- Enum/check constraints: none
- Business notes: currently acts as both identity and app profile; `is_admin` is app authorization; email confirmation and pending email change are app-managed
- Postgres migration notes: this audit added an explicit DB/server default for `preferred_region` so non-ORM inserts preserve the same non-null fallback as normal app writes. Other user-owned preferences and timestamps remain mostly app-managed by design. Email/username uniqueness is still case-sensitive unless normalized in app logic; timezone remains free text, not DB-validated; future `supabase_auth_user_id` should be added explicitly rather than implied.

## `user_api_token`

- Table name: `user_api_token`
- Model name: `UserApiToken`
- Purpose: hashed bearer tokens for API/mobile access
- Columns:
  - `id` - Integer - required
  - `user_id` - Integer - required
  - `name` - String(100) - nullable
  - `token_hash` - String(64) - required
  - `scopes` - String(200) - required - default `steps:write`
  - `last_used_at` - DateTime - nullable
  - `created_at` - DateTime - required - default `datetime.utcnow` - server default `CURRENT_TIMESTAMP`
  - `revoked_at` - DateTime - nullable
- Primary key: `id`
- Foreign keys:
  - `user_id -> user.id`
- Unique constraints:
  - `token_hash`
- Indexes:
  - `user_id`
  - `token_hash`
  - composite `user_id, revoked_at`
- Postgres migration notes: this audit added a DB/server default for `created_at` because token rows are security/audit records and should retain a non-null creation time even if inserted outside the normal ORM path. `scopes` already had a DB default; `last_used_at` and `revoked_at` remain application-managed nullable timestamps.
- Enum/check constraints: none
- Business notes: plaintext token is shown once at creation, only the SHA-256 hash is stored
- Postgres migration notes: good candidate for a partial index such as active tokens only; current string `scopes` storage is flexible but weakly structured. Partial active-token indexing is `Should be done during Postgres migration`.

## `user_api_usage`

- Table name: `user_api_usage`
- Model name: `UserApiUsage`
- Purpose: per-user daily usage counters
- Columns:
  - `id` - Integer - required
  - `user_id` - Integer - required
  - `action` - String(50) - required
  - `usage_date` - Date - required
  - `count` - Integer - required - default `0`
  - `updated_at` - DateTime - required - default/onupdate `datetime.utcnow`
- Primary key: `id`
- Foreign keys:
  - `user_id -> user.id`
- Unique constraints:
  - `user_id, action, usage_date`
- Indexes:
  - `user_id`
  - `action`
  - `usage_date`
- Enum/check constraints: none
- Business notes: currently used for request throttling around expensive refresh actions
- Postgres migration notes: straightforward migration; if action set stabilizes, an enum/check may be useful later. Change timing: `Documentation only` for now.

## `sneaker`

- Table name: `sneaker`
- Model name: `Sneaker`
- Purpose: user-owned sneaker record
- Columns:
  - `id` - Integer - required
  - `brand` - String(100) - required
  - `model` - String(100) - required
  - `sku` - String(50) - nullable
  - `colorway` - String(100) - nullable
  - `size` - String(20) - nullable
  - `size_type` - String(15) - nullable
  - `last_worn_date` - Date - nullable
  - `image_url` - String(255) - nullable
  - `purchase_price` - Numeric(10,2) - nullable
  - `purchase_currency` - String(3) - nullable
  - `price_paid_currency` - String(3) - nullable
  - `condition` - String(50) - nullable
  - `purchase_date` - Date - nullable
  - `last_cleaned_at` - DateTime - nullable
  - `starting_health` - Float - required - default `100.0`
  - `persistent_stain_points` - Float - required - default `0.0`
  - `persistent_material_damage_points` - Float - required - default `0.0`
  - `persistent_structural_damage_points` - Float - required - default `0.0`
  - `user_id` - Integer - required
  - `in_rotation` - Boolean - required - default `False`
- Primary key: `id`
- Foreign keys:
  - `user_id -> user.id`
- Unique constraints: none
- Indexes:
  - `sku`
  - `in_rotation`
- Enum/check constraints: none
- Business notes: core ownership record; SKU is optional and not unique because many users can own the same SKU
- Postgres migration notes: numeric precision is adequate for current prices; ownership should continue to be enforced by `user_id`; if condition or size_type values are stabilized, Postgres checks/enums are candidates. Change timing: `Should be done during Postgres migration` only if stronger checks are adopted.

## `sneaker_note`

- Table name: `sneaker_note`
- Model name: `SneakerNote`
- Purpose: sneaker notes
- Columns:
  - `id` - Integer - required
  - `sneaker_id` - Integer - required
  - `body` - Text - required
  - `created_at` - DateTime - required - default `datetime.utcnow`
  - `updated_at` - DateTime - nullable - default/onupdate `datetime.utcnow`
- Primary key: `id`
- Foreign keys:
  - `sneaker_id -> sneaker.id`
- Unique constraints: none
- Indexes:
  - `sneaker_id`
- Enum/check constraints: none
- Business notes: delete-orphan through `Sneaker.note_entries`
- Postgres migration notes: portable as-is.

## `sneaker_sale`

- Table name: `sneaker_sale`
- Model name: `SneakerSale`
- Purpose: sale/resale transaction records
- Columns:
  - `id` - Integer - required
  - `sneaker_id` - Integer - nullable
  - `release_id` - Integer - nullable
  - `size_label` - String(50) - nullable
  - `size_type` - String(20) - nullable
  - `sold_price` - Numeric(10,2) - required
  - `sold_currency` - String(3) - required - default `USD`
  - `purchase_price` - Numeric(10,2) - nullable
  - `purchase_currency` - String(3) - nullable
  - `sold_at` - Date - required
  - `created_at` - DateTime - required - default `datetime.utcnow`
- Primary key: `id`
- Foreign keys:
  - `sneaker_id -> sneaker.id`
  - `release_id -> release.id`
- Unique constraints: none
- Indexes:
  - `sneaker_id`
  - `release_id`
- Enum/check constraints: none
- Business notes: `sneaker_id` was introduced non-nullable, then explicitly made nullable; current model intentionally supports release-linked sales without a live sneaker row; relationship cascade is `save-update, merge`, not delete-orphan
- Postgres migration notes: nullable foreign keys need deliberate handling during data migration; decide whether orphaned sale records are allowed by design; if yes, document that clearly, if not, refactor before migration. Current flexibility is real, so any tightening is a product/schema decision. Change timing: `Documentation only` unless semantics are being changed.

## `sneaker_wear`

- Table name: `sneaker_wear`
- Model name: `SneakerWear`
- Purpose: sneaker wear events by date
- Columns:
  - `id` - Integer - required
  - `sneaker_id` - Integer - required
  - `worn_at` - Date - required
  - `created_at` - DateTime - required - default `datetime.utcnow`
- Primary key: `id`
- Foreign keys:
  - `sneaker_id -> sneaker.id`
- Unique constraints: none
- Indexes:
  - `sneaker_id`
  - `worn_at`
- Enum/check constraints: none
- Business notes: multiple wear rows per date are not blocked at the DB layer
- Postgres migration notes: if “one wear row per sneaker per date” becomes a rule, add a unique constraint during Postgres migration. Change timing: `Should be done during Postgres migration` only if enforced.

## `sneaker_clean_event`

- Table name: `sneaker_clean_event`
- Model name: `SneakerCleanEvent`
- Purpose: cleaning event history
- Columns:
  - `id` - Integer - required
  - `user_id` - Integer - required
  - `sneaker_id` - Integer - required
  - `cleaned_at` - DateTime - required - default `datetime.utcnow`
  - `stain_removed` - Boolean - nullable
  - `lasting_material_impact` - Boolean - required - default `False`
  - `notes` - String(280) - nullable
- Primary key: `id`
- Foreign keys:
  - `user_id -> user.id`
  - `sneaker_id -> sneaker.id`
- Unique constraints: none
- Indexes:
  - `user_id`
  - `sneaker_id`
  - `cleaned_at`
- Enum/check constraints: none
- Business notes: both `user_id` and `sneaker_id` are stored, but DB does not enforce that the sneaker belongs to the same user
- Postgres migration notes: ownership integrity could be strengthened only with more advanced constraints or backend discipline; keep as app-enforced unless redesigning. Change timing: `Documentation only`.

## `sneaker_health_snapshot`

- Table name: `sneaker_health_snapshot`
- Model name: `SneakerHealthSnapshot`
- Purpose: persisted health score snapshots
- Columns:
  - `id` - Integer - required
  - `sneaker_id` - Integer - required
  - `user_id` - Integer - required
  - `recorded_at` - DateTime - required - default `datetime.utcnow`
  - `health_score` - Float - required
  - `wear_penalty` - Float - nullable
  - `cosmetic_penalty` - Float - nullable
  - `structural_penalty` - Float - nullable
  - `hygiene_penalty` - Float - nullable
  - `steps_total_used` - Integer - nullable
  - `confidence_score` - Float - nullable
  - `confidence_label` - String(20) - nullable
  - `reason` - String(40) - required
- Primary key: `id`
- Foreign keys:
  - `sneaker_id -> sneaker.id`
  - `user_id -> user.id`
- Unique constraints: none
- Indexes:
  - `sneaker_id`
  - `user_id`
  - `recorded_at`
- Enum/check constraints: none
- Business notes: derived data used for history/debugging; no DB constraint on allowed `reason` values
- Postgres migration notes: consider a check/enum for `reason` only once value set is stable. Change timing: `Should be done during Postgres migration` if adopted.

## `sneaker_damage_event`

- Table name: `sneaker_damage_event`
- Model name: `SneakerDamageEvent`
- Purpose: damage reports
- Columns:
  - `id` - Integer - required
  - `user_id` - Integer - required
  - `sneaker_id` - Integer - required
  - `reported_at` - DateTime - required - default `datetime.utcnow`
  - `damage_type` - String(50) - required
  - `severity` - Integer - required
  - `notes` - String(280) - nullable
  - `photo_url` - String(1024) - nullable
  - `health_penalty_points` - Float - required - default `0.0`
  - `is_active` - Boolean - required - default `True`
  - `created_at` - DateTime - required - default `datetime.utcnow`
  - `updated_at` - DateTime - required - default/onupdate `datetime.utcnow`
- Primary key: `id`
- Foreign keys:
  - `user_id -> user.id`
  - `sneaker_id -> sneaker.id`
- Unique constraints: none
- Indexes:
  - `user_id`
  - `sneaker_id`
  - `reported_at`
- Enum/check constraints: none
- Business notes: severity is normalized in services, not constrained in DB
- Postgres migration notes: good candidate for check constraints on severity range and maybe known `damage_type` values after data audit. Change timing: `Should be done during Postgres migration`.

## `sneaker_repair_event`

- Table name: `sneaker_repair_event`
- Model name: `SneakerRepairEvent`
- Purpose: repair records
- Columns:
  - `id` - Integer - required
  - `user_id` - Integer - required
  - `sneaker_id` - Integer - required
  - `repaired_at` - DateTime - required - default `datetime.utcnow`
  - `repair_kind` - String(20) - required
  - `repair_type` - String(100) - required
  - `repair_type_other` - String(120) - nullable
  - `provider` - String(120) - nullable
  - `provider_other` - String(120) - nullable
  - `repair_area` - String(30) - nullable
  - `baseline_delta_applied` - Float - nullable
  - `cost_amount` - Numeric(10,2) - nullable
  - `cost_currency` - String(3) - nullable
  - `notes` - String(280) - nullable
  - `resolved_all_active_damage` - Boolean - required - default `True`
  - `created_at` - DateTime - required - default `datetime.utcnow`
  - `updated_at` - DateTime - required - default/onupdate `datetime.utcnow`
- Primary key: `id`
- Foreign keys:
  - `user_id -> user.id`
  - `sneaker_id -> sneaker.id`
- Unique constraints: none
- Indexes:
  - `user_id`
  - `sneaker_id`
  - `repaired_at`
- Enum/check constraints: none
- Business notes: repair/provider semantics are app-enforced
- Postgres migration notes: enums/checks are candidates once value sets stabilize; numeric precision is acceptable for current cost values.

## `sneaker_repair_resolved_damage`

- Table name: `sneaker_repair_resolved_damage`
- Model name: `SneakerRepairResolvedDamage`
- Purpose: junction between repair events and damage events
- Columns:
  - `id` - Integer - required
  - `repair_event_id` - Integer - required
  - `damage_event_id` - Integer - required
  - `created_at` - DateTime - required - default `datetime.utcnow`
- Primary key: `id`
- Foreign keys:
  - `repair_event_id -> sneaker_repair_event.id`
  - `damage_event_id -> sneaker_damage_event.id`
- Unique constraints:
  - `repair_event_id, damage_event_id`
- Indexes:
  - `repair_event_id`
  - `damage_event_id`
- Enum/check constraints: none
- Business notes: link table for resolved damage mapping
- Postgres migration notes: portable as-is.

## `sneaker_expense`

- Table name: `sneaker_expense`
- Model name: `SneakerExpense`
- Purpose: per-sneaker expense records
- Columns:
  - `id` - Integer - required
  - `user_id` - Integer - required
  - `sneaker_id` - Integer - required
  - `category` - String(30) - required
  - `amount` - Numeric(10,2) - required
  - `currency` - String(3) - required
  - `expense_date` - DateTime - required - default `datetime.utcnow`
  - `notes` - String(280) - nullable
  - `created_at` - DateTime - required - default `datetime.utcnow`
  - `updated_at` - DateTime - required - default/onupdate `datetime.utcnow`
- Primary key: `id`
- Foreign keys:
  - `user_id -> user.id`
  - `sneaker_id -> sneaker.id`
- Unique constraints: none
- Indexes:
  - `user_id`
  - `sneaker_id`
  - `expense_date`
- Enum/check constraints: none
- Business notes: category values are app-defined
- Postgres migration notes: if expense categories are stable, a check/enum is a Postgres-era candidate. Change timing: `Should be done during Postgres migration` if enforced.

## `step_bucket`

- Table name: `step_bucket`
- Model name: `StepBucket`
- Purpose: imported step buckets
- Columns:
  - `id` - Integer - required
  - `user_id` - Integer - required
  - `source` - String(50) - required
  - `granularity` - String(10) - required
  - `bucket_start` - DateTime - required
  - `bucket_end` - DateTime - required
  - `steps` - Integer - required - default `0`
  - `timezone` - String(64) - required - default `Europe/London`
  - `created_at` - DateTime - required - default `datetime.utcnow`
  - `updated_at` - DateTime - required - default/onupdate `datetime.utcnow`
- Primary key: `id`
- Foreign keys:
  - `user_id -> user.id`
- Unique constraints:
  - `user_id, source, granularity, bucket_start`
- Indexes:
  - `user_id`
  - `bucket_start`
- Enum/check constraints: none
- Business notes: route logic stores UTC-naive datetimes plus a separate IANA timezone string; granularity currently supports `day` and `hour`, but attribution supports only `day`
- Postgres migration notes: datetime handling is important here; current app uses naive UTC datetimes plus timezone text, not `TIMESTAMPTZ`; either preserve that convention consistently or migrate intentionally to timezone-aware types. Change timing: `Should be done during Postgres migration`.

## `step_attribution`

- Table name: `step_attribution`
- Model name: `StepAttribution`
- Purpose: derived step attribution per sneaker and bucket
- Columns:
  - `id` - Integer - required
  - `user_id` - Integer - required
  - `sneaker_id` - Integer - required
  - `bucket_granularity` - String(10) - required
  - `bucket_start` - DateTime - required
  - `steps_attributed` - Integer - required - default `0`
  - `algorithm_version` - String(50) - required
  - `computed_at` - DateTime - required - default `datetime.utcnow`
- Primary key: `id`
- Foreign keys:
  - `user_id -> user.id`
  - `sneaker_id -> sneaker.id`
- Unique constraints:
  - `user_id, sneaker_id, bucket_granularity, bucket_start, algorithm_version`
- Indexes:
  - `user_id`
  - `sneaker_id`
  - `bucket_start`
- Enum/check constraints: none
- Business notes: derived/recomputable table; algorithm version is part of identity
- Postgres migration notes: portable as-is; consider composite covering indexes if attribution analytics become heavier.

## `exposure_event`

- Table name: `exposure_event`
- Model name: `ExposureEvent`
- Purpose: daily exposure flags/severity entered by users
- Columns:
  - `id` - Integer - required
  - `user_id` - Integer - required
  - `date_local` - Date - required
  - `timezone` - String(64) - required - default `Europe/London`
  - `got_wet` - Boolean - required - default `False`
  - `got_dirty` - Boolean - required - default `False`
  - `stain_flag` - Boolean - required - default `False`
  - `wet_severity` - Integer - nullable
  - `dirty_severity` - Integer - nullable
  - `stain_severity` - Integer - nullable
  - `note` - String(140) - nullable
  - `created_at` - DateTime - required - default `datetime.utcnow`
  - `updated_at` - DateTime - required - default/onupdate `datetime.utcnow`
- Primary key: `id`
- Foreign keys:
  - `user_id -> user.id`
- Unique constraints:
  - `user_id, date_local`
- Indexes:
  - `user_id`
  - `date_local`
- Enum/check constraints: none
- Business notes: one daily exposure row per user; severity normalization is done in service code
- Postgres migration notes: booleans and severity checks should be explicit if stronger data quality is needed; timezone is free-text IANA string. Check constraints are a `Should be done during Postgres migration` candidate.

## `sneaker_exposure_attribution`

- Table name: `sneaker_exposure_attribution`
- Model name: `SneakerExposureAttribution`
- Purpose: distributed exposure points per sneaker/day
- Columns:
  - `id` - Integer - required
  - `user_id` - Integer - required
  - `sneaker_id` - Integer - required
  - `date_local` - Date - required
  - `wet_points` - Float - required - default `0.0`
  - `dirty_points` - Float - required - default `0.0`
  - `created_at` - DateTime - required - default `datetime.utcnow`
  - `updated_at` - DateTime - required - default/onupdate `datetime.utcnow`
- Primary key: `id`
- Foreign keys:
  - `user_id -> user.id`
  - `sneaker_id -> sneaker.id`
- Unique constraints:
  - `user_id, sneaker_id, date_local`
- Indexes:
  - `user_id`
  - `sneaker_id`
  - `date_local`
- Enum/check constraints: none
- Business notes: derived/recomputable table
- Postgres migration notes: portable as-is.

## `release`

- Table name: `release`
- Model name: `Release`
- Purpose: shared release record
- Columns:
  - `id` - Integer - required
  - `sku` - String(50) - nullable
  - `brand` - String(100) - nullable
  - `name` - String(200) - required
  - `model_name` - String(200) - nullable
  - `release_slug` - String(255) - nullable
  - `colorway` - String(200) - nullable
  - `description` - Text - nullable
  - `notes` - Text - nullable
  - `release_date` - Date - required
  - `is_calendar_visible` - Boolean - required - default `True`
  - `retail_price` - Numeric(10,2) - nullable
  - `retail_currency` - String(10) - nullable
  - `image_url` - String(500) - nullable
  - `source` - String(50) - nullable
  - `source_product_id` - String(100) - nullable
  - `source_slug` - String(255) - nullable
  - `source_updated_at` - DateTime - nullable
  - `last_synced_at` - DateTime - nullable
  - `sales_last_fetched_at` - DateTime - nullable
  - `size_bids_last_fetched_at` - DateTime - nullable
  - `heat_score` - Float - nullable
  - `heat_confidence` - String(20) - nullable
  - `heat_premium_ratio` - Float - nullable
  - `heat_basis` - String(30) - nullable
  - `heat_updated_at` - DateTime - nullable
  - `ingestion_source` - String(50) - nullable
  - `ingestion_batch_id` - String(100) - nullable
  - `ingested_at` - DateTime - nullable
  - `ingested_by_user_id` - Integer - nullable
  - `created_at` - DateTime - nullable - default `datetime.utcnow`
  - `updated_at` - DateTime - nullable - default/onupdate `datetime.utcnow`
- Primary key: `id`
- Foreign keys:
  - `ingested_by_user_id -> user.id`
- Unique constraints:
  - `source, source_product_id` via `uq_release_source_source_product_id`
- Indexes:
  - `sku`
  - `brand`
  - `release_slug`
  - `release_date`
  - `ingested_by_user_id`
  - `source_product_id`
- Enum/check constraints: none
- Business notes: central shared entity; “delete all releases” does not delete rows, it flips `is_calendar_visible = False`; individual release deletes cascade to release-linked children at ORM level
- Postgres migration notes: the model and Alembic are now aligned on `uq_release_source_source_product_id` and `source_product_id` indexing. Remaining migration questions are whether `sku` and/or `release_slug` should stay non-unique and whether an additional composite index is still useful for Postgres query patterns. Change timing: current identity alignment is implemented; any further SKU/slug constraint changes remain `Should be done during Postgres migration`.

## `release_region`

- Table name: `release_region`
- Model name: `ReleaseRegion`
- Purpose: region-specific release timing
- Columns:
  - `id` - Integer - required
  - `release_id` - Integer - required
  - `region` - String(10) - required
  - `release_date` - Date - required
  - `release_time` - Time - nullable
  - `timezone` - String(64) - nullable
  - `created_at` - DateTime - nullable - default `datetime.utcnow`
  - `updated_at` - DateTime - nullable - default/onupdate `datetime.utcnow`
- Primary key: `id`
- Foreign keys:
  - `release_id -> release.id`
- Unique constraints:
  - `release_id, region`
- Indexes:
  - `release_id`
- Enum/check constraints: none
- Business notes: supported regions in app logic are `US`, `UK`, `EU`
- Postgres migration notes: region values are free text today; a check or enum is a strong candidate once the supported set is confirmed. Change timing: `Should be done during Postgres migration`.

## `release_price`

- Table name: `release_price`
- Model name: `ReleasePrice`
- Purpose: one native retail price row per release region
- Columns:
  - `id` - Integer - required
  - `release_id` - Integer - required
  - `currency` - String(3) - required
  - `price` - Numeric(10,2) - required
  - `region` - String(10) - nullable
  - `created_at` - DateTime - nullable - default `datetime.utcnow`
  - `updated_at` - DateTime - nullable - default/onupdate `datetime.utcnow`
- Primary key: `id`
- Foreign keys:
  - `release_id -> release.id`
- Unique constraints:
  - `release_id, region` via `uq_release_price_region`
- Indexes:
  - `release_id`
- Enum/check constraints: none
- Business notes: schema and CSV/admin write paths are now aligned around one mutable native retail price row per region. The migration that introduced `uq_release_price_region` also collapses legacy duplicate non-null regional rows by keeping the newest row per `release_id + region`.
- Postgres migration notes: this table now matches current app behavior. Remaining Postgres work is mainly verification of numeric precision, nullable `region` semantics, and whether additional region-oriented indexes are needed under production query load.

## `affiliate_offer`

- Table name: `affiliate_offer`
- Model name: `AffiliateOffer`
- Purpose: retailer/aftermarket/raffle offers
- Columns:
  - `id` - Integer - required
  - `release_id` - Integer - required
  - `retailer` - String(50) - required
  - `region` - String(10) - nullable
  - `base_url` - String(1024) - required
  - `affiliate_url` - String(1024) - nullable
  - `offer_type` - String(20) - required - default `aftermarket`
  - `price` - Numeric(10,2) - nullable
  - `currency` - String(3) - nullable
  - `status` - String(50) - nullable
  - `priority` - Integer - required - default `100`
  - `is_active` - Boolean - required - default `True`
  - `last_checked_at` - DateTime - nullable
- Primary key: `id`
- Foreign keys:
  - `release_id -> release.id`
- Unique constraints:
  - `release_id, retailer, region`
- Indexes:
  - `release_id`
- Enum/check constraints: none
- Business notes: `region = NULL` is treated as global; reused for aftermarket and retailer links
- Postgres migration notes: note that unique constraints with nullable columns behave differently conceptually than app-level intent; current design still works because uniqueness is anchored by `release_id, retailer, region`, but NULL-region semantics should be tested carefully on migrated data. Change timing: `Should be done during Postgres migration` only if uniqueness semantics are changed.

## `release_size_bid`

- Table name: `release_size_bid`
- Model name: `ReleaseSizeBid`
- Purpose: cached size-level asks/bids
- Columns:
  - `id` - Integer - required
  - `release_id` - Integer - required
  - `size_label` - String(50) - required
  - `size_type` - String(20) - nullable
  - `highest_bid` - Numeric(10,2) - required
  - `currency` - String(3) - required - default `USD`
  - `price_type` - String(10) - required - default `bid`
  - `fetched_at` - DateTime - required - default `datetime.utcnow`
- Primary key: `id`
- Foreign keys:
  - `release_id -> release.id`
- Unique constraints:
  - `release_id, size_label, size_type, price_type`
- Indexes:
  - `release_id`
- Enum/check constraints: none
- Business notes: ask and bid rows can now coexist for the same release/size/size_type. Write paths were also updated so ask/bid dedupe happens per `price_type`, not per size alone.
- Postgres migration notes: the intended ask/bid coexistence rule is now implemented. Remaining Postgres work is limited to checking whether additional indexes are needed for price-type-specific heat/market queries.

## `release_sale_point`

- Table name: `release_sale_point`
- Model name: `ReleaseSalePoint`
- Purpose: cached historical sale points
- Columns:
  - `id` - Integer - required
  - `release_id` - Integer - required
  - `sale_at` - DateTime - required
  - `price` - Numeric(10,2) - required
  - `currency` - String(3) - required - default `USD`
  - `fetched_at` - DateTime - required - default `datetime.utcnow`
- Primary key: `id`
- Foreign keys:
  - `release_id -> release.id`
- Unique constraints:
  - `release_id, sale_at`
- Indexes:
  - `release_id`
  - `sale_at`
- Enum/check constraints: none
- Business notes: shared derived market cache
- Postgres migration notes: if source systems can emit multiple sale points at the same timestamp, current uniqueness may be too strong. Change timing: `Documentation only` unless source behavior proves otherwise.

## `release_sales_monthly`

- Table name: `release_sales_monthly`
- Model name: `ReleaseSalesMonthly`
- Purpose: monthly market averages
- Columns:
  - `id` - Integer - required
  - `release_id` - Integer - required
  - `month_start` - Date - required
  - `avg_price` - Numeric(10,2) - required
  - `currency` - String(3) - required - default `USD`
  - `fetched_at` - DateTime - required - default `datetime.utcnow`
- Primary key: `id`
- Foreign keys:
  - `release_id -> release.id`
- Unique constraints:
  - `release_id, month_start`
- Indexes:
  - `release_id`
  - `month_start`
- Enum/check constraints: none
- Business notes: shared derived market cache
- Postgres migration notes: if monthly rows become currency-specific, uniqueness may need to include currency. Change timing: `Documentation only` for now.

## `release_market_stats`

- Table name: `release_market_stats`
- Model name: `ReleaseMarketStats`
- Purpose: aggregate market metrics cache
- Columns:
  - `id` - Integer - required
  - `release_id` - Integer - required
  - `currency` - String(3) - nullable
  - `average_price_1m` - Numeric(10,2) - nullable
  - `average_price_3m` - Numeric(10,2) - nullable
  - `average_price_1y` - Numeric(10,2) - nullable
  - `volatility` - Float - nullable
  - `price_range_low` - Numeric(10,2) - nullable
  - `price_range_high` - Numeric(10,2) - nullable
  - `sales_price_range_low` - Numeric(10,2) - nullable
  - `sales_price_range_high` - Numeric(10,2) - nullable
  - `sales_volume` - Integer - nullable
  - `gmv` - Numeric(12,2) - nullable
  - `created_at` - DateTime - nullable - default `datetime.utcnow`
  - `updated_at` - DateTime - nullable - default/onupdate `datetime.utcnow`
- Primary key: `id`
- Foreign keys:
  - `release_id -> release.id`
- Unique constraints:
  - `release_id` effectively unique
- Indexes:
  - `release_id` unique index
- Enum/check constraints: none
- Business notes: one stats row per release; cached shared market summary
- Postgres migration notes: numeric precision is reasonable for current metrics; if multi-currency stats are ever needed, one-row-per-release shape will need redesign.

## `exchange_rate`

- Table name: `exchange_rate`
- Model name: `ExchangeRate`
- Purpose: FX conversion table
- Columns:
  - `id` - Integer - required
  - `base_currency` - String(3) - required
  - `quote_currency` - String(3) - required
  - `rate` - Numeric(18,6) - required
  - `as_of` - DateTime - required - default `datetime.utcnow`
- Primary key: `id`
- Foreign keys: none
- Unique constraints:
  - `base_currency, quote_currency`
- Indexes:
  - `base_currency`
  - `quote_currency`
- Enum/check constraints: none
- Business notes: used only for display/currency conversion, not for retail price normalization
- Postgres migration notes: precision is appropriate; if historical rates become important, uniqueness may need to include `as_of`.

## `article`

- Table name: `article`
- Model name: `Article`
- Purpose: article/news record
- Columns:
  - `id` - Integer - required
  - `title` - String(255) - required
  - `slug` - String(255) - required
  - `excerpt` - Text - nullable
  - `brand` - String(150) - nullable
  - `tags` - Text - nullable
  - `hero_image_url` - String(1024) - nullable
  - `hero_image_alt` - String(255) - nullable
  - `author_name` - String(120) - nullable
  - `author_title` - String(120) - nullable
  - `author_bio` - Text - nullable
  - `author_image_url` - String(1024) - nullable
  - `author_image_alt` - String(255) - nullable
  - `meta_title` - String(70) - nullable
  - `meta_description` - String(300) - nullable
  - `canonical_url` - String(1024) - nullable
  - `robots` - String(40) - nullable
  - `og_title` - String(255) - nullable
  - `og_description` - String(300) - nullable
  - `og_image_url` - String(1024) - nullable
  - `twitter_card` - String(40) - nullable
  - `product_schema_json` - Text - nullable
  - `faq_schema_json` - Text - nullable
  - `video_schema_json` - Text - nullable
  - `published_at` - DateTime - nullable
  - `created_by_user_id` - Integer - nullable
  - `created_at` - DateTime - nullable - default `datetime.utcnow`
  - `updated_at` - DateTime - nullable - default/onupdate `datetime.utcnow`
- Primary key: `id`
- Foreign keys:
  - `created_by_user_id -> user.id`
- Unique constraints:
  - `slug`
- Indexes:
  - `slug`
  - `brand`
  - `published_at`
- Enum/check constraints: none
- Business notes: admin-managed authored content; schema JSON fields are stored as text, not JSON
- Postgres migration notes: JSON-ish fields are stored as `Text`; consider migrating them to `JSONB` only if queryability is needed. Change timing: `Should be done during Postgres migration` only if changing field types.

## `article_block`

- Table name: `article_block`
- Model name: `ArticleBlock`
- Purpose: ordered content blocks in an article
- Columns:
  - `id` - Integer - required
  - `article_id` - Integer - required
  - `position` - Integer - required
  - `block_type` - String(50) - required
  - `heading_text` - Text - nullable
  - `heading_level` - String(4) - nullable
  - `body_text` - Text - nullable
  - `image_url` - String(1024) - nullable
  - `image_alt` - String(255) - nullable
  - `caption` - String(255) - nullable
  - `align` - String(20) - nullable
  - `carousel_images_json` - Text - nullable
  - `created_at` - DateTime - nullable - default `datetime.utcnow`
  - `updated_at` - DateTime - nullable - default/onupdate `datetime.utcnow`
- Primary key: `id`
- Foreign keys:
  - `article_id -> article.id`
- Unique constraints:
  - `article_id, position`
- Indexes:
  - `article_id`
- Enum/check constraints: none
- Business notes: ordered child rows for `Article`
- Postgres migration notes: same JSON-text note as article schema fields; if carousel data needs validation/querying, `JSONB` is a future option.

## `site_schema`

- Table name: `site_schema`
- Model name: `SiteSchema`
- Purpose: site-wide JSON-LD schema snippets
- Columns:
  - `id` - Integer - required
  - `schema_type` - String(50) - required
  - `json_text` - Text - required
  - `created_at` - DateTime - nullable - default `datetime.utcnow`
  - `updated_at` - DateTime - nullable - default/onupdate `datetime.utcnow`
- Primary key: `id`
- Foreign keys: none
- Unique constraints:
  - `schema_type`
- Indexes:
  - `schema_type`
- Enum/check constraints: none
- Business notes: stores organization/website and similar global schema payloads
- Postgres migration notes: same `Text` vs `JSONB` consideration applies.

## `sneaker_db`

- Table name: `sneaker_db`
- Model name: `SneakerDB`
- Purpose: shared sneaker reference/lookup cache
- Columns:
  - `id` - Integer - required
  - `brand` - String(150) - nullable
  - `name` - String(255) - nullable
  - `model_name` - String(255) - nullable
  - `colorway` - String(255) - nullable
  - `gender` - String(20) - nullable
  - `description` - Text - nullable
  - `release_date` - Date - nullable
  - `retail_price` - Numeric(10,2) - nullable
  - `retail_currency` - String(10) - nullable
  - `sku` - String(50) - required
  - `stockx_id` - String(100) - nullable
  - `stockx_slug` - String(255) - nullable
  - `goat_id` - String(100) - nullable
  - `goat_slug` - String(255) - nullable
  - `current_lowest_ask_stockx` - Numeric(10,2) - nullable
  - `current_lowest_ask_goat` - Numeric(10,2) - nullable
  - `primary_material` - String(100) - nullable
  - `materials_json` - Text - nullable
  - `materials_source` - String(50) - nullable
  - `materials_confidence` - Float - nullable
  - `materials_updated_at` - DateTime - nullable
  - `description_last_seen` - DateTime - nullable
  - `source_updated_at` - DateTime - nullable
  - `last_synced_at` - DateTime - nullable
  - `created_at` - DateTime - nullable - default `datetime.utcnow`
  - `updated_at` - DateTime - nullable - default/onupdate `datetime.utcnow`
  - `image_url` - String(1024) - nullable
- Primary key: `id`
- Foreign keys: none
- Unique constraints:
  - `sku`
- Indexes:
  - `brand`
  - `name`
  - `model_name`
  - `sku`
- Enum/check constraints: none
- Business notes: shared cache/reference table used for local-first lookup and release detail fallback data
- Postgres migration notes: unique SKU is important here and is already reflected in migration history; JSON text for materials is a future `JSONB` candidate only if querying is needed.

# 8. Relationships and ownership rules

One-to-many relationships:

- `User -> Sneaker`
- `User -> UserApiToken`
- `Release -> ReleaseRegion`
- `Release -> ReleasePrice`
- `Release -> AffiliateOffer`
- `Release -> ReleaseSizeBid`
- `Release -> ReleaseSalePoint`
- `Release -> ReleaseSalesMonthly`
- `Release -> ReleaseMarketStats` as one-to-one via `uselist=False`
- `Article -> ArticleBlock`

Many-to-many relationships:

- `User <-> Release` through `wishlist_items`
- `SneakerRepairEvent <-> SneakerDamageEvent` through `SneakerRepairResolvedDamage`

Ownership rules:

- `Release` and release-linked market tables are shared/global data.
- `Sneaker` and its health/expense/wear records are user-specific.
- `StepBucket`, `ExposureEvent`, and related attribution tables are private per-user data.
- `Article`, `ArticleBlock`, and `SiteSchema` are admin-managed shared content.

Cascade delete behavior:

- `Release` relationships to `AffiliateOffer`, `ReleasePrice`, `ReleaseRegion`, `ReleaseSizeBid`, `ReleaseSalePoint`, `ReleaseSalesMonthly`, and `ReleaseMarketStats` use ORM `cascade='all, delete-orphan'`.
- `Sneaker.note_entries` and `Sneaker.wears` use delete-orphan cascade.
- `Sneaker.sales` does not use delete-orphan; this aligns with `sneaker_id` being nullable.

Soft-delete/hide behavior:

- Calendar “Delete All Releases” is not a delete. It sets `Release.is_calendar_visible = False`.
- Individual release delete removes the `Release` row and release-linked children through ORM cascade.
- Wishlist rows are intentionally preserved by the hide-all action.

Important ownership caveat:

- Many user-scoped tables store both `user_id` and `sneaker_id`, but the DB does not enforce that the sneaker belongs to that same user. That is enforced in application code. Recommended future hardening: `Documentation only` unless a stronger ownership model is added later.

# 9. Constraints and validation rules

## Schema-level rules

- `user.username` unique
- `user.email` unique
- `user.pending_email` unique in current schema and migration history
- `user_api_token.token_hash` unique
- `user_api_usage(user_id, action, usage_date)` unique
- `step_bucket(user_id, source, granularity, bucket_start)` unique
- `step_attribution(user_id, sneaker_id, bucket_granularity, bucket_start, algorithm_version)` unique
- `exposure_event(user_id, date_local)` unique
- `sneaker_exposure_attribution(user_id, sneaker_id, date_local)` unique
- `release_region(release_id, region)` unique
- `release(source, source_product_id)` unique
- `release_price(release_id, region)` unique
- `affiliate_offer(release_id, retailer, region)` unique
- `release_size_bid(release_id, size_label, size_type, price_type)` unique
- `release_sale_point(release_id, sale_at)` unique
- `release_sales_monthly(release_id, month_start)` unique
- `release_market_stats.release_id` unique
- `exchange_rate(base_currency, quote_currency)` unique
- `article.slug` unique
- `article_block(article_id, position)` unique
- `site_schema.schema_type` unique
- `sneaker_db.sku` unique

## Business-level rules

- Registration lowercases email before lookup and insert.
- Login uses `username`, not `email`.
- Email confirmation is required before login succeeds.
- Password reset and email confirmation tokens are generated in app code and not persisted in DB tables.
- Region values are treated as `US`, `UK`, `EU`.
- Currencies are treated as `GBP`, `USD`, `EUR` in forms/import logic.
- CSV import requires at least one regional release date.
- Regional retail price requires regional currency.
- If region-specific price, retailer links, or release time are provided in admin forms, that region also needs a release date.
- CSV import matches releases by normalized SKU, then `release_slug`, then legacy slug fallback.
- Non-blank CSV values overwrite; blank CSV values do not clear.
- `ingestion_source="csv_admin"` protects core release fields from later KicksDB overwrite.
- Exposure severity is normalized to 1..3 in service code.
- Step ingestion enforces granularity and non-negative step counts in route code.
- Health/exposure/repair/damage categories are mostly app-enforced, not DB-enforced.

Recommended future state: add DB checks only for invariants the app already relies on heavily, such as constrained regions/currencies/severity ranges. Change timing: `Should be done during Postgres migration`.

# 10. Indexing strategy

## Current indexes observed in code/migrations

Likely hot-path current indexes:

- `user_api_token`: `user_id`, `token_hash`, `(user_id, revoked_at)`
- `user_api_usage`: `user_id`, `action`, `usage_date`
- `sneaker`: `sku`, `in_rotation`
- `sneaker_note`: `sneaker_id`
- `sneaker_sale`: `sneaker_id`, `release_id`
- `sneaker_wear`: `sneaker_id`, `worn_at`
- `sneaker_clean_event`: `user_id`, `sneaker_id`, `cleaned_at`
- `sneaker_health_snapshot`: `sneaker_id`, `user_id`, `recorded_at`
- `sneaker_damage_event`: `user_id`, `sneaker_id`, `reported_at`
- `sneaker_repair_event`: `user_id`, `sneaker_id`, `repaired_at`
- `sneaker_expense`: `user_id`, `sneaker_id`, `expense_date`
- `step_bucket`: `user_id`, `bucket_start`
- `step_attribution`: `user_id`, `sneaker_id`, `bucket_start`
- `exposure_event`: `user_id`, `date_local`
- `sneaker_exposure_attribution`: `user_id`, `sneaker_id`, `date_local`
- `release`: `sku`, `brand`, `release_slug`, `release_date`, `ingested_by_user_id`, `source_product_id`
- `release_price`: `release_id`
- `affiliate_offer`: `release_id`
- `release_size_bid`: `release_id`
- `release_sale_point`: `release_id`, `sale_at`
- `release_sales_monthly`: `release_id`, `month_start`
- `release_market_stats`: unique `release_id`
- `exchange_rate`: `base_currency`, `quote_currency`
- `article`: `slug`, `brand`, `published_at`
- `article_block`: `article_id`
- `site_schema`: `schema_type`
- `sneaker_db`: `brand`, `name`, `model_name`, `sku`

## Recommended indexes before production launch

- `release (source, source_product_id)` composite index if Postgres query plans show repeated lookup/filter pressure beyond the existing unique constraint and single-column `source_product_id` index.
  Change timing: `Should be done during Postgres migration`.
- `release (release_date, is_calendar_visible)` for calendar queries.
  Change timing: `Should be done during Postgres migration`.
- `release (sku, release_date)` or at least a re-audited SKU strategy if SKU remains a major lookup key.
  Change timing: `Should be done during Postgres migration`.
- `release_region (release_id, region)` is already unique; optionally add `region, release_date` only if region-date reporting becomes hot.
- `release_price (release_id, region)` is now the core uniqueness rule; evaluate whether a separate composite index is still beneficial beyond the unique constraint once running on Postgres.
  Change timing: `Should be done during Postgres migration`.
- `affiliate_offer (release_id, region, is_active)` for release display resolution.
  Change timing: `Should be done during Postgres migration`.
- `step_bucket (user_id, granularity, bucket_start)` composite index for recompute scans.
  Change timing: `Should be done during Postgres migration`.
- `step_attribution (user_id, sneaker_id, bucket_granularity, bucket_start)` covering the main health/attribution queries.
  Change timing: `Should be done during Postgres migration`.
- `exposure_event (user_id, date_local)` already unique; keep and verify query plans.
- `article (published_at, slug)` if article listing/detail traffic matters at launch.
  Change timing: `Should be done during Postgres migration`.

## Recommended indexes only if Supabase Auth / RLS is introduced later

- `user.supabase_auth_user_id` unique index once added.
  Change timing: `Should be deferred until after Supabase Auth cutover`.
- User-private tables with future RLS exposure should index their user ownership columns aggressively:
  `sneaker.user_id`,
  `step_bucket.user_id`,
  `step_attribution.user_id`,
  `exposure_event.user_id`,
  `sneaker_exposure_attribution.user_id`,
  `user_api_token.user_id`.
- If policies use app-user to auth-user mapping joins, index that linkage column explicitly.

# 11. Currency, region, and timezone data rules

Supported regions currently enforced in forms/import logic:

- `US`
- `UK`
- `EU`

Supported currencies currently enforced in forms/import logic:

- `GBP`
- `USD`
- `EUR`

Where preferences live:

- `User.preferred_region`
- `User.preferred_currency`
- `User.timezone`

Monetary field rules:

- retail price fields are native stored values, not converted display values
- resale/market metrics may be converted for display using `ExchangeRate`
- retail prices intentionally do not FX-convert for display if only native-region pricing is known

Timezone rules:

- step buckets store UTC-naive `bucket_start`/`bucket_end` plus an IANA timezone string
- step attribution derives local dates from the stored/fallback timezone
- user timezone defaults to `Europe/London`
- release regions may store time and timezone, but current release detail UI displays date only

Date-only vs datetime:

- `release.release_date`, `release_region.release_date`, `sneaker_wear.worn_at`, `exposure_event.date_local`, and `user_api_usage.usage_date` are date-only
- step buckets, token timestamps, sync timestamps, and content publish timestamps are datetimes

Recommended future state: move enumerated region/currency/timezone assumptions into more explicit validation and, where appropriate, DB checks. Change timing: `Should be done during Postgres migration`.

# 12. Shared cache and derived-data tables

Tables that behave as caches or derived datasets rather than primary user-authored source-of-truth records:

## Shared release cache/derived tables

- `affiliate_offer`
  - Source of truth: external sources plus admin-entered retailer links
  - Refresh/update trigger: ingestion, detail refresh, admin edits
  - Recomputable: partially
  - Retention: retain as the current shared offer cache

- `release_size_bid`
  - Source of truth: KicksDB/market source
  - Refresh/update trigger: market refresh jobs and detail refresh
  - Recomputable: yes
  - Retention: current snapshot cache

- `release_sale_point`
  - Source of truth: market history source
  - Refresh/update trigger: market refresh jobs
  - Recomputable: yes
  - Retention: cached history

- `release_sales_monthly`
  - Source of truth: market history source
  - Refresh/update trigger: market refresh jobs
  - Recomputable: yes
  - Retention: cached monthly aggregate

- `release_market_stats`
  - Source of truth: derived from fetched market data / KicksDB responses
  - Refresh/update trigger: market refresh jobs
  - Recomputable: yes
  - Retention: one current aggregate row per release

## User-derived tables

- `step_attribution`
  - Source of truth: `step_bucket` + `sneaker_wear`
  - Refresh/update trigger: step ingestion and recompute
  - Recomputable: yes
  - Retention: can be regenerated

- `sneaker_exposure_attribution`
  - Source of truth: `exposure_event` + `sneaker_wear`
  - Refresh/update trigger: exposure updates and recompute
  - Recomputable: yes
  - Retention: can be regenerated

- `sneaker_health_snapshot`
  - Source of truth: computed from ownership, wear, step attribution, exposure, and maintenance data
  - Refresh/update trigger: relevant sneaker lifecycle actions
  - Recomputable: largely yes, but historical snapshots may still be operationally useful
  - Retention: keep as historical trace unless policy changes

## Integration/reference cache

- `sneaker_db`
  - Source of truth: external sneaker data provider responses
  - Refresh/update trigger: lookup and sync services
  - Recomputable: yes
  - Retention: cache/reference table

Recommended future state: keep derived tables clearly labeled as derived in docs and avoid treating them as sole source of truth during migration. Change timing: `Documentation only`.

# 13. Security model

## Authentication

Current implementation:

- Flask-Login session auth for browser flows
- app-owned password hashes in `user.password_hash`
- `itsdangerous` token flows for password reset and email confirmation
- hashed bearer tokens in `user_api_token` for mobile/API step sync

Future direction:

- Supabase Auth likely becomes identity provider

## Authorisation

Current implementation:

- `User.is_admin` controls admin-only actions
- route decorators enforce login/admin/bearer scope
- bearer token scope enforcement is string-based and narrow today
- no DB-level role model or RLS policy currently exists

## Data ownership

- `user`, `sneaker`, wishlist, steps, exposures, health, repairs, expenses, notes, and tokens are private/user-owned domains
- `release` and release-linked market data are shared/global
- `article`, `article_block`, and `site_schema` are admin-managed shared content
- ownership is primarily enforced in Flask routes/services, not by DB relational cross-checks

## Private vs shared data domains

Private or sensitive:

- user identity/profile fields
- API token hashes
- step buckets
- exposure events
- sneaker ownership and finances
- health snapshots
- damage/repair history

Shared/global:

- release catalog
- release market metrics
- sneaker reference cache
- articles and site schema
- exchange rates

Security principle:

- user-private tables must never be publicly queryable by default
- shared/global release tables are different from private user-owned tables and should not be governed by the same exposure model

Recommended future state: keep auth identity, authorization, and data ownership as separate concerns in both docs and implementation. Change timing: `Documentation only`.

# 14. Supabase Row Level Security plan

Current implementation:

- no RLS
- backend mediates access
- Supabase APIs are not currently in the request path

Planning recommendation:

- Keep core app traffic going through Flask first.
- Design RLS intentionally for any tables that may later be exposed through Supabase APIs.
- User-private tables are natural RLS candidates because ownership maps cleanly to a user identity.
- Shared/global release tables need different policy treatment than user-private tables.

Likely RLS candidates later:

- `sneaker`
- `wishlist_items`
- `step_bucket`
- `step_attribution`
- `exposure_event`
- `sneaker_exposure_attribution`
- `sneaker_note`
- `sneaker_expense`
- `sneaker_damage_event`
- `sneaker_repair_event`
- `sneaker_health_snapshot`
- `user_api_token` if ever exposed through Supabase-managed APIs, which is not currently recommended

Backend-only initially:

- `user`
- `user_api_token`
- `user_api_usage`
- shared ingestion/cache tables
- admin content tables

Admin access model:

- continue to route admin access through Flask first
- if RLS is later added, admin bypass should be explicit and auditable, not inferred from client metadata alone

Change timing:

- RLS design documentation: `Documentation only`
- actual policy rollout: `Should be deferred until after Supabase Auth cutover` unless direct Supabase API exposure is introduced earlier

# 15. Migration from SQLite to PostgreSQL

SQLite-specific behaviors to check:

- boolean storage and server defaults may differ in practice
- naive datetime handling can mask timezone assumptions
- unique constraints and NULL behavior need retesting
- string comparison/collation/case behavior may differ
- batch-mode migration patterns reflect SQLite constraints
- some historical migrations created constraints that are not clearly reflected in current model declarations

Column/type differences to review:

- booleans: ensure explicit Postgres defaults for flags such as `is_admin`, `is_email_confirmed`, `is_calendar_visible`, `got_wet`, `got_dirty`, `in_rotation`
- datetimes: decide whether to keep UTC-naive timestamps plus separate timezone fields, or intentionally adopt timezone-aware Postgres columns in selected domains
- text/varchar: current app often stores constrained values as free-text strings
- numeric precision: current money columns are mostly `Numeric(10,2)` and FX is `Numeric(18,6)`
- JSON fields: article schema and materials payloads are stored as `Text`, not JSON
- case sensitivity: unique text columns like username/email/slug should be audited for normalization expectations
- foreign key enforcement: SQLite and SQLAlchemy patterns may have hidden assumptions; verify all FK behavior on migrated data
- cascade behavior: ORM cascades do not automatically equal DB-level `ON DELETE` behavior

Migration phases:

1. Audit current schema
   - compare live DB, `models.py`, and Alembic history
   - confirm previously corrected alignment such as `release(source, source_product_id)` still matches live environments
2. Generate/fix migrations
   - make model declarations reflect intended constraints
   - add missing indexes and checks chosen for launch
3. Stand up Supabase project
   - provision Postgres
   - configure environment separation and secrets
4. Apply schema to Postgres
   - run Alembic against clean Postgres
   - validate created indexes/constraints explicitly
5. Migrate data
   - export SQLite data
   - transform/normalize values where needed
   - import into Postgres in dependency-safe order
6. Verify integrity
   - row counts
   - uniqueness assumptions
   - FK consistency
   - sampled application flows
7. Switch environment config
   - point staging then production to Postgres
   - verify connection handling
8. Test production-critical flows
   - auth
   - profile/preferences
   - collection CRUD
   - wishlist
   - release calendar and detail
   - ingestion/sync

Recommended schema changes to align during this phase:

- decide whether to strengthen region/currency/severity checks

The earlier pre-migration hardening items for auth cleanup, `Release` identity alignment, `ReleasePrice`, and `ReleaseSizeBid` are now implemented. Remaining schema work here is mostly Postgres-specific constraint/index/check decisions.

# 16. Auth migration plan

Current auth model summary:

- browser auth: Flask-Login using app session
- identity store: `user`
- credentials: `password_hash`
- reset/verification flows: `itsdangerous`
- API/mobile access: `user_api_token`

Target Supabase Auth model summary:

- identity provider: Supabase Auth
- app profile/domain user: retained `user` table
- identity linkage: future `supabase_auth_user_id`
- authorization: remains app-owned

Identity mapping strategy:

- create or reconcile one Soletrak `User` row per Supabase auth user
- add linkage field on `User`
- preserve app-owned fields and domain relationships unchanged

Existing users migration options:

- staged migration with forced password reset into Supabase
- staged migration with email invite / magic-link onboarding
- one-cutover identity import if technically feasible and secure

Password handling:

- current password hashes are app-managed
- do not assume they can be ported directly into Supabase Auth without a deliberate compatibility plan
- forced reset or staged migration is the safer default assumption

Sessions during cutover:

- existing Flask sessions should be considered invalid once auth authority changes
- cutover plan should include explicit session expiry / re-authentication behavior

Admin roles and profile data:

- keep `is_admin` and profile data in app tables
- do not rely solely on Supabase metadata for authorization

Rollback considerations:

- preserve app `user` rows throughout transition
- keep migration reversible where possible
- avoid deleting app auth logic until the Supabase path is proven in staging/production

Recommended approach:

- move to Supabase Auth in a phased manner
- retain application `User`
- plan for forced reset or staged password migration rather than assuming hash portability

Change timing:

- auth bug cleanup in current code: `Safe pre-migration refactor`
- app/user linkage column and auth cutover work: `Should be deferred until after Supabase Auth cutover`
- documenting the target model now: `Documentation only`

# 17. Operational concerns

- Backups: Supabase/Postgres backup and restore policies must be defined before production launch.
- Restore testing: backup existence is not enough; restore drills should be tested.
- Connection pooling: production Postgres will need connection pooling awareness that SQLite does not.
- Migration discipline: run Alembic in all deployed environments; no direct production schema edits.
- Environment separation: local, test, staging, and production databases must be distinct.
- Secrets handling: `DATABASE_URL`, `SECRET_KEY`, email keys, and API keys should live in environment config only.
- Monitoring: add query and connection monitoring once on Postgres; SQLite does not surface the same operational signals.
- Export/import safety: use deterministic export order and verify encoding, datetime handling, and numeric fidelity during migration.

Recommended future state: formalize backup/restore and connection observability before launch on Postgres. Change timing: `Should be done during Postgres migration`.

# 18. Testing and verification checklist

Run and verify at minimum:

- registration
- login
- logout
- password reset flow
- email confirmation flow
- pending email change flow
- profile updates
- preferred region/currency behavior
- mobile token creation/revocation
- collection CRUD
- sneaker notes
- sneaker wear logging
- sneaker sales
- expenses
- damage/repair flows
- wishlist add/remove
- release calendar
- release detail pages
- sneaker detail release linkage
- release CSV import preview/confirm
- KicksDB ingestion/update flows
- manual/admin release edit flows
- steps/mobile sync
- attribution recompute
- exposure calculations
- health calculations
- news/article admin tooling

Pytest coverage to run:

- full `python -m pytest`
- specifically release, wishlist, auth, sneakers, steps, exposure, news, money utility tests

Manual integrity spot checks:

- user counts
- release counts
- wishlist row counts
- step bucket / attribution totals for sample users
- exposure row uniqueness by user/date
- release identity duplicates by SKU and by `(source, source_product_id)`
- article slug uniqueness

# 19. Open questions / decision log

- Should `Release.sku` remain non-unique in the shared release table, or is uniqueness expected after cleanup?
- Should `Release.release_slug` remain non-unique, or should product URL identity be tightened?
- Should selected string fields move to Postgres checks/enums at migration time?
- Will mobile clients continue using app bearer tokens after Supabase Auth arrives?
- Will any direct mobile/browser access to Supabase APIs be allowed in phase one or later?
- Should images/uploads move to Supabase Storage later?
- Which user-private tables, if any, need RLS in the first Supabase-integrated phase?

# 20. Maintenance rules

- Update this file whenever models, relationships, constraints, indexes, or auth architecture changes.
- Update this file alongside Alembic migrations that change schema behavior.
- Keep this file aligned with `docs/ARCHITECTURE.md`, `docs/MODULE_MAP.md`, `docs/AI_CONTEXT.md`, and `docs/DECISIONS.md`.
- Add a dated note when major database or auth decisions change.
- If model declarations and migration history diverge, document the drift explicitly and resolve it quickly.

# Appendix: Pre-migration action list

| Issue | Severity | Recommended action | Change timing | Required before Postgres migration? (Yes/No) | Required before Supabase Auth migration? (Yes/No) |
|---|---|---|---|---|---|
| `User.verify_reset_password_token` duplicate/debug-print issue | Resolved | Implemented: single method kept in `models.py`; remove from active migration risk list | Safe pre-migration refactor | No | No |
| `app.logger` used in `confirm_new_email_with_token` | Resolved | Implemented: error-path logging now uses `current_app.logger` | Safe pre-migration refactor | No | No |
| `ReleaseSizeBid` uniqueness omitted `price_type` | Resolved | Implemented: uniqueness now includes `price_type`, and write-path dedupe/upsert logic preserves ask/bid coexistence | Safe pre-migration refactor | No | No |
| `ReleasePrice` schema/service mismatch | Resolved | Implemented: one native retail price per `release_id + region`, with Alembic/data-handling alignment | Safe pre-migration refactor | No | No |
| `Release` model/Alembic drift around `(source, source_product_id)` | Resolved | Implemented: model now declares the same unique constraint/index expectation as Alembic | Safe pre-migration refactor | No | No |
| `Release` launch identity for `sku` and `release_slug` is ambiguous | Medium | Audit duplicates and decide whether either field should become unique or remain lookup-only | Should be done during Postgres migration | Yes | No |
| Region/currency/severity values are validated in app code but mostly not constrained in DB | Medium | Add targeted Postgres checks/enums only for invariants the app truly relies on | Should be done during Postgres migration | Yes | No |
| Ownership of user-scoped child rows is enforced in Flask, not DB | Medium | Document clearly now; only add deeper DB enforcement if the model is redesigned | Documentation only | No | No |
| Step bucket datetime/timezone convention is UTC-naive plus timezone text | Medium | Decide whether to preserve this convention or move selected fields to timezone-aware Postgres types | Should be done during Postgres migration | Yes | No |
| Article/schema JSON payloads are stored as `Text` | Low | Keep as text unless queryability is required; migrate to `JSONB` only if needed | Documentation only | No | No |
| `User` currently combines identity and app profile responsibilities | Medium | Preserve for now, but define the auth/profile boundary explicitly in migration docs | Documentation only | No | Yes |
| Add `supabase_auth_user_id` linkage to `User` | High | Add explicit nullable linkage column when the Supabase Auth transition design is finalized | Should be deferred until after Supabase Auth cutover | No | Yes |
| Password reset, email verification, and session handling are still app-managed | High | Replace with Supabase-managed flows during auth cutover, with explicit rollback and re-auth plan | Should be deferred until after Supabase Auth cutover | No | Yes |
| Mobile/API token strategy after Supabase Auth is unresolved | Medium | Decide whether to retain app bearer tokens, replace with Supabase JWTs, or run hybrid auth | Should be deferred until after Supabase Auth cutover | No | Yes |
| RLS policy design for user-private tables is not defined | Medium | Keep backend-only access first; design RLS intentionally before any Supabase API exposure | Should be deferred until after Supabase Auth cutover | No | Yes |
