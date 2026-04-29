# 1. Purpose

This document is the execution roadmap for moving Soletrak from the current SQLite-based setup to PostgreSQL on Supabase, using the repository state documented in `docs/DATABASE_SCHEMA.md` and the decision framing in `docs/DB_MIGRATION_DECISIONS.md`.

Post-cutover status update:

- The Supabase Postgres cutover is complete.
- Active Supabase Postgres project ref: `sjwdvsefjlflgavshiyy`.
- Previous Supabase source project ref: `mizyioplztuzycipfdsd`, retained temporarily as fallback/reference before retirement.
- The current/latest source of truth is now the fresh Supabase Postgres target.
- The successful final path was public-schema logical dump/restore into the fresh target, then Flask app repoint and smoke validation.
- SQLite is archival fallback only.
- Supabase Auth is planned next, but is not implemented yet.

This is now both a historical implementation plan and a reference for future Postgres restore/cutover work. It assumes:

- Flask remains the main backend
- SQLAlchemy remains the ORM
- Alembic remains the migration system
- Supabase Postgres migration happens before any Supabase Auth cutover
- Supabase Auth is a related but separate later phase
- the app is not redesigned into direct client-to-database access

# 2. Locked decisions

This plan assumes the following decisions from `docs/DB_MIGRATION_DECISIONS.md`:

- `ReleasePrice` is now implemented as one native retail price per release region, not a multi-currency-per-region model.
- `ReleaseSizeBid` now supports both ask and bid rows in the same table, with uniqueness including `price_type`.
- `Release` external identity is currently aligned around `source + source_product_id`, with `sku` retained as a lookup/match key rather than a hard unique identifier.
- `release_slug` should remain non-unique for now.
- Postgres migration should not be blocked on immediate RLS rollout.
- Supabase Auth should be introduced after the Postgres migration, not as part of the first database cutover.
- Mobile token behavior should follow a hybrid period during auth transition rather than immediate removal.
- Soletrak should move toward email-first auth when Supabase Auth is introduced, while keeping `username` as application/profile data if still needed.

Important note:

- The timestamp strategy is not fully “one-way locked” yet. The working assumption in this plan is a pragmatic hybrid: preserve current local-date-sensitive designs where needed, but use the Postgres migration to make timestamp handling more explicit and less SQLite-dependent.

# 3. Migration principles

- Use small, reversible steps.
- Keep schema, models, and migrations aligned for ongoing Postgres operation.
- Prefer production safety over speed.
- Migrate to Postgres before attempting Supabase Auth cutover.
- Keep a backend-first security posture.
- Preserve current Flask route/service ownership of business logic during the migration.
- Treat live DB behavior, Alembic history, and SQLAlchemy models as a three-way consistency problem, not a models-only problem.
- Avoid introducing optional redesign work into the first migration wave.

# 4. Phase overview

## Phase 0: pre-migration hardening

- Goal: clean up the most obvious schema/auth risks and model-vs-migration drift before touching Postgres.
- Why it exists: the current repository has a few correctness and migration-discipline issues that should not be carried into a production database move.
- Dependencies: none beyond current repo state.
- Go/no-go checkpoint: model definitions, migrations, and intended schema rules are aligned enough that a clean Postgres schema can be generated intentionally.
- Status: Tasks 1 through 5 are complete.

## Phase 1: schema alignment before Postgres

- Goal: finalize the schema shape that will be migrated to Postgres.
- Why it exists: the team should not move SQLite ambiguity directly into a production Postgres database.
- Dependencies: Phase 0 hardening completed.
- Go/no-go checkpoint: all “must decide before Postgres migration” schema decisions are implemented or explicitly deferred with no ambiguity in the target Postgres schema.

## Phase 2: SQLite -> Supabase Postgres migration

- Goal: provision Supabase Postgres, apply schema cleanly, migrate data, and switch environment configuration.
- Why it exists: this is the actual engine migration.
- Dependencies: Phase 1 schema alignment complete.
- Go/no-go checkpoint: staging Postgres environment passes integrity validation and critical flows.
- Current status: completed. The final cutover used Postgres-source-of-truth -> fresh Postgres target restore; do not replay the SQLite import path unless an explicit recovery decision is made.

## Phase 3: Postgres stabilization

- Goal: operate on Postgres safely, watch query behavior, and fix migration fallout before adding auth complexity.
- Why it exists: database cutovers often reveal real data or query issues that are better solved before identity changes.
- Dependencies: Phase 2 cutover complete.
- Go/no-go checkpoint: production-critical flows are stable, monitoring is acceptable, and no major schema regressions remain.
- Current status: active post-cutover monitoring on project `sjwdvsefjlflgavshiyy`.

## Phase 4: Supabase Auth preparation

- Goal: prepare the app and schema for a later identity-provider transition without cutting over sessions yet.
- Why it exists: auth migration has different risks than DB migration and should be staged.
- Dependencies: stable Postgres operation.
- Go/no-go checkpoint: app can represent both current users and future Supabase-linked identities without ambiguity.

## Phase 5: Supabase Auth cutover

- Goal: switch primary identity handling from app-managed auth to Supabase Auth.
- Why it exists: this is the point where managed identity becomes live.
- Dependencies: Phase 4 prep complete, staging auth flows validated.
- Go/no-go checkpoint: login, password reset, email verification, session handling, and app-user mapping all work in staging with rollback ready.

## Phase 6: post-auth hardening / optional RLS

- Goal: strengthen the post-cutover posture with optional RLS and any remaining auth cleanup.
- Why it exists: RLS and direct-Supabase exposure should follow real product needs, not precede them.
- Dependencies: successful Supabase Auth cutover.
- Go/no-go checkpoint: there is a concrete need for RLS or direct Supabase API exposure.

# 5. Detailed task breakdown by phase

## Phase 0: pre-migration hardening

Likely code/doc/config areas:

- [models.py](/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/models.py)
- [routes/auth_routes.py](/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/routes/auth_routes.py)
- [services/release_csv_import_service.py](/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/services/release_ingestion_service.py)
- [services/release_ingestion_service.py](/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/services/release_ingestion_service.py)
- `migrations/versions/*`
- docs already created in `docs/`

Migrations likely needed:

- completed for `ReleasePrice`
- completed for `ReleaseSizeBid`
- no additional migration was needed for `Release` identity alignment because Alembic already represented the intended rule
- possibly yes for server-default alignment

Data migration tasks:

- low-volume cleanup or dedupe checks for releases before any constraint changes
- audit existing `release_price` rows for per-region multiplicity before applying the one-price-per-region migration in each environment
- audit existing `release_size_bid` rows for unexpected same-size duplication before applying the broadened uniqueness rule in each environment

Testing tasks:

- auth route tests
- release CSV import tests
- release display/detail tests
- heat/market-data tests if present
- migration smoke tests on a clean DB

Rollback considerations:

- keep each hardening change separate
- do not bundle unrelated schema refactors into one migration
- preserve reversible Alembic steps

Risks:

- codifying the wrong release identity rule
- changing `release_price` semantics without updating import/display behavior together

Current status:

- The auth cleanup items are done.
- `Release` model/Alembic identity alignment is done.
- `ReleasePrice` schema, migration, and write-path alignment are done.
- `ReleaseSizeBid` uniqueness and write-path alignment are done.
- The ORM-defaults vs DB/server-defaults audit is done, with explicit DB defaults added only for `user.preferred_region` and `user_api_token.created_at`.
- Remaining Phase 0 work is primarily documentation alignment verification and any separate migration-hygiene cleanup such as `pending_email` downgrade handling.

## Phase 1: schema alignment before Postgres

Likely code/doc/config areas:

- `models.py`
- relevant release services
- Alembic migration set

Migrations likely needed:

- explicit schema-shape migrations for launch target
- possible unique/index adjustments
- possible server-default normalization

Data migration tasks:

- resolve duplicates that would violate chosen launch constraints
- verify live data conforms to selected one-price-per-region rule

Testing tasks:

- regenerate/test clean schema from migrations
- run full pytest
- verify no unexpected autogenerate drift remains

Rollback considerations:

- schema alignment must remain reversible on SQLite and staging Postgres

Risks:

- hidden live SQLite data inconsistencies
- model vs migration mismatch persisting into Postgres

## Phase 2: SQLite -> Supabase Postgres migration

Likely code/doc/config areas:

- [config.py](/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/config.py)
- deployment env config
- Alembic env and migration scripts
- operational scripts for the initial SQLite export/import and the current Postgres dump/restore cutover path

Migrations likely needed:

- none beyond the already-aligned target schema if Phase 1 is done correctly

Data migration tasks:

- completed initial path: export SQLite data, transform if required, import into Postgres in dependency-safe order, validate row counts and key constraints
- current final-cutover path: dump current Postgres source, restore into a fresh Postgres target, validate row counts and key constraints

Testing tasks:

- run schema on clean Supabase Postgres
- validate the completed SQLite -> Postgres import path in staging
- run a fresh-target Postgres dump/restore rehearsal before real cutover
- run application test suite against Postgres if feasible
- manual staging verification

Rollback considerations:

- keep the current Postgres source database intact until go/no-go is complete
- retain earlier SQLite backups as archival fallback only
- retain ability to point app back to the preserved source Postgres database during the cutover window

Risks:

- dump/restore or connection-string mistakes
- uniqueness collisions
- timestamp interpretation bugs
- runtime query differences between SQLite and Postgres

## Phase 3: Postgres stabilization

Likely code/doc/config areas:

- query-heavy routes/services
- exchange-rate and release display logic
- release ingestion and sync scripts

Migrations likely needed:

- only targeted fix migrations if production/staging findings justify them

Data migration tasks:

- cleanup of bad imported rows if discovered

Testing tasks:

- regression tests on production-critical flows
- query-plan review for hot paths
- monitor connection usage and slow queries

Rollback considerations:

- if stabilization reveals severe correctness issues, rollback should still be possible before auth migration begins

Risks:

- latent query inefficiency
- constraints that were technically valid but operationally awkward

## Phase 4: Supabase Auth preparation

Likely code/doc/config areas:

- `models.py`
- auth routes
- Flask-Login integration
- session/loading logic
- any future Supabase integration layer

Migrations likely needed:

- likely addition of `supabase_auth_user_id` on `User`
- possibly supporting fields/indexes for email-first mapping

Data migration tasks:

- map existing `User` records to future Supabase identities
- decide staged onboarding/password migration approach

Testing tasks:

- auth flow staging tests
- email-first login transition tests
- app user mapping tests

Rollback considerations:

- keep current auth paths alive until cutover is proven

Risks:

- identity duplication
- session invalidation edge cases
- user confusion during email-first transition

## Phase 5: Supabase Auth cutover

Likely code/doc/config areas:

- auth routes
- session handling
- password reset/email confirmation flows
- login UI/forms
- mobile auth validation layer

Migrations likely needed:

- maybe none if Phase 4 prepared linkage already

Data migration tasks:

- staged or cutover mapping of existing users
- re-authentication/session invalidation strategy

Testing tasks:

- browser login/logout
- password reset
- email verification
- profile/account continuity
- mobile token coexistence

Rollback considerations:

- preserve old auth path long enough to recover
- do not delete app-user data or current auth-related fields prematurely

Risks:

- broken logins
- mismatch between Supabase identity and app `User`
- mobile sync disruption

## Phase 6: post-auth hardening / optional RLS

Likely code/doc/config areas:

- Supabase policy definitions if adopted
- any future direct API exposure layer

Migrations likely needed:

- maybe indexes to support RLS-heavy access paths

Data migration tasks:

- none likely, unless ownership cleanup is needed

Testing tasks:

- policy verification
- access-boundary tests

Rollback considerations:

- apply RLS gradually and table-by-table

Risks:

- overcomplicating backend-first architecture
- policy mistakes locking out valid app access

# 6. Pre-migration hardening task list

## Remove duplicate `verify_reset_password_token` implementation

- Status: completed.
- Why it matters: this removed a real auth-code correctness smell before Postgres work.
- Should happen before Postgres migration: Yes.
- Requires an Alembic migration: No.

## Fix `app.logger` to `current_app.logger` in auth route

- Status: completed.
- Why it matters: the error path in `confirm_new_email_with_token` now uses the correct Flask logger context.
- Should happen before Postgres migration: Yes.
- Requires an Alembic migration: No.

## Align `Release` model with actual intended `source/source_product_id` identity rule

- Status: completed.
- Why it matters: the `Release` model now again declares the same `source + source_product_id` identity rule that Alembic already represented.
- Should happen before Postgres migration: Yes.
- Requires an Alembic migration: No for the completed alignment work. A future migration is only needed if the rule itself changes.

## Decide and implement the `ReleasePrice` one-price-per-region model

- Status: completed.
- Why it matters: the schema, Alembic history, and CSV/admin write paths now all represent one native retail price per release region.
- Should happen before Postgres migration: Yes.
- Requires an Alembic migration: Yes. Completed via the `release_price` uniqueness migration and duplicate-handling step.

## Adjust `ReleaseSizeBid` uniqueness to include `price_type`

- Status: completed.
- Why it matters: ask and bid rows can now coexist for the same release/size combination, and write-path dedupe logic preserves that behavior.
- Should happen before Postgres migration: Yes.
- Requires an Alembic migration: Yes. Completed.

## Review ORM defaults vs DB/server defaults where relevant

- Status: completed.
- Why it matters: SQLite and ORM-managed writes can hide missing server defaults that become more important in Postgres and in operational scripts/imports.
- Should happen before Postgres migration: Yes.
- Requires an Alembic migration: Yes. Completed with a narrow follow-up migration.
- Outcome:
  - added DB/server default for `user.preferred_region = 'UK'`
  - added DB/server default for `user_api_token.created_at = CURRENT_TIMESTAMP`
  - intentionally left most other timestamps and app-owned fields as ORM-only defaults because they are service-owned event metadata rather than infrastructure defaults

## Audit `pending_email` uniqueness and migration hygiene

- Why it matters: `pending_email` is unique in both model and migration history, but the downgrade migration for that constraint is faulty.
- Should happen before Postgres migration: Yes.
- Requires an Alembic migration: Not necessarily for the live schema, but migration hygiene may require a corrective migration or at least a documented fix path.

## Reword and align schema documentation after each hardening change

- Why it matters: the team now has repo-critical DB docs, and they must stay ahead of implementation drift.
- Should happen before Postgres migration: Yes.
- Requires an Alembic migration: No.

# 7. PostgreSQL migration design

## Provisioning Supabase

- Create a Supabase project for staging first, then production.
- Use Supabase Postgres only; do not expose app tables directly to clients in this phase.
- Record connection strings, SSL requirements, and pooled vs direct connection endpoints.
- For migrations and other Postgres-native commands, use the staging project's direct connection string if the environment supports IPv6. If the local environment does not support IPv6, use the Supavisor session-mode connection string instead.
- Keep Supabase Auth disabled for this milestone; this phase is database bootstrap only.

## Environment configuration

- Keep `DATABASE_URL` as the app input.
- Use the existing normalization in `config.py` for any legacy `postgres://` values.
- Add environment-specific `DATABASE_URL` values for staging and production Supabase instances.
- Keep local dev and tests on SQLite unless/until Postgres-backed tests are introduced.
- Do not overwrite the normal local `.env` just to run staging bootstrap; prefer shell-scoped `DATABASE_URL` export so the default local SQLite setup still works when the shell session ends.
- Include `sslmode=require` in the staging Postgres connection string unless the exact Supabase connection string already includes equivalent SSL requirements.

## Running Alembic cleanly against Postgres

- Finish Phase 0 and Phase 1 before attempting a clean Postgres schema.
- Apply migrations to a fresh Supabase Postgres instance.
- Inspect resulting constraints and indexes manually, especially:
  - `release(source, source_product_id)`
  - `release_price`
  - `release_size_bid`
  - `pending_email`
  - unique/index-backed auth fields
- Treat any model/autogenerate drift after this point as a stop sign.

Bootstrap commands:

1. Export the staging DB URL for this shell only:
   `export DATABASE_URL='postgresql://postgres:<PASSWORD>@db.<PROJECT-REF>.supabase.co:5432/postgres?sslmode=require'`

   If direct IPv6 connectivity is not available locally, use the Supavisor session-mode connection string from the Supabase dashboard instead of the direct string.

2. Confirm the app sees the staging DB:
   `./venv/bin/flask --app app:create_app db current`

3. Apply the full migration chain:
   `./venv/bin/flask --app app:create_app db upgrade`

4. Confirm the database is at head:
   `./venv/bin/flask --app app:create_app db current`

Expected current head after bootstrap:

- `c1d2e3f4a5b6`

Schema verification commands:

```bash
./venv/bin/python - <<'PY'
from app import create_app
from extensions import db
from sqlalchemy import inspect

app = create_app()
with app.app_context():
    insp = inspect(db.engine)

    required_tables = {
        "user",
        "release",
        "release_region",
        "release_price",
        "release_size_bid",
        "affiliate_offer",
        "user_api_token",
    }
    tables = set(insp.get_table_names())
    missing = sorted(required_tables - tables)
    print("missing_tables", missing)

    print("release_uniques", insp.get_unique_constraints("release"))
    print("release_indexes", insp.get_indexes("release"))
    print("release_price_uniques", insp.get_unique_constraints("release_price"))
    print("release_size_bid_uniques", insp.get_unique_constraints("release_size_bid"))

    user_columns = {col["name"]: col for col in insp.get_columns("user")}
    token_columns = {col["name"]: col for col in insp.get_columns("user_api_token")}
    print("user.preferred_region.default", user_columns["preferred_region"].get("default"))
    print("user_api_token.created_at.default", token_columns["created_at"].get("default"))
PY
```

Bootstrap is correct if all of the following are true:

- `missing_tables` is empty
- `release` includes `uq_release_source_source_product_id`
- `release` includes `ix_release_source_product_id`
- `release_price` includes `uq_release_price_region`
- `release_size_bid` includes `uq_release_size_bid` over `release_id, size_label, size_type, price_type`
- `user.preferred_region` has a DB default of `UK`
- `user_api_token.created_at` has a DB default equivalent to `CURRENT_TIMESTAMP`

## Export/import strategy from SQLite

- Export from SQLite after schema hardening is complete.
- Prefer a controlled application/scripted export rather than ad hoc dumps if transforms are required.
- Normalize data before import where constraints changed.

Status/update:

- This SQLite -> Postgres import strategy was the initial migration path and has been validated.
- For final production cutover preparation, the source of truth has moved forward to the current Postgres database.
- Do not use CSV or a fresh SQLite export as the final production backup/import mechanism unless the team deliberately rolls back to the earlier migration phase.

## Current Postgres source-of-truth cutover strategy

- Treat the current Postgres database as the latest operational data source.
- Freeze writes before the final dump to avoid split-brain changes.
- Take a full logical Postgres dump from the source database.
- Recommended backup format: `pg_dump -Fc --no-owner --no-acl`.
- Restore that dump into a fresh Supabase Postgres target.
- Use `pg_restore --no-owner --no-acl --single-transaction` against the fresh target where possible.
- Verify `alembic_version` with `flask db current` after restore.
- Run `flask db upgrade` only if the restored database is behind the deployed code's migration head.
- Compare source and restored target row counts and spot-check key flows before switching `DATABASE_URL`.

Completed cutover details:

- Previous source project: `mizyioplztuzycipfdsd`.
- Active target project: `sjwdvsefjlflgavshiyy`.
- Public-schema restore succeeded.
- App repoint succeeded.
- DB credentials were rotated after cutover.
- Smoke checks passed for home, login/logout, profile, collection, rotation, sneaker detail, release calendar/detail, release resale refresh, and API token create/revoke.
- Backup artefacts:
  - `backups/postgres/soletrak_postgres_20260428_152256.dump`
  - `backups/postgres/soletrak_public_20260428_154243.dump`
  - `backups/postgres/soletrak_public_cutover_20260428_160930.dump`

What this changes from the earlier SQLite-first plan:

- SQLite remains an archival fallback, not the final source for production cutover.
- The production backup artifact is a full Postgres dump, not CSV and not a SQLite file.
- The final target should be built by Postgres restore, not by re-running the SQLite importer.
- Rollback during the cutover window means pointing the app back to the preserved source Postgres database.

## Dependency-safe import order

This order applies only to the completed SQLite -> Postgres importer path. It is retained for historical/reference use and for disaster recovery from SQLite-era backups. It is not the recommended final cutover path now that Postgres is the source of truth.

Suggested order:

1. `user`
2. `exchange_rate`
3. `site_schema`
4. `sneaker_db`
5. `release`
6. `article`
7. `sneaker`
8. `release_region`
9. `release_price`
10. `affiliate_offer`
11. `release_market_stats`
12. `release_size_bid`
13. `release_sale_point`
14. `release_sales_monthly`
15. `article_block`
16. `wishlist_items`
17. `user_api_token`
18. `user_api_usage`
19. `sneaker_note`
20. `sneaker_wear`
21. `sneaker_sale`
22. `sneaker_clean_event`
23. `sneaker_damage_event`
24. `sneaker_repair_event`
25. `sneaker_repair_resolved_damage`
26. `sneaker_expense`
27. `step_bucket`
28. `step_attribution`
29. `exposure_event`
30. `sneaker_exposure_attribution`
31. `sneaker_health_snapshot`

## Integrity verification after import or restore

- For the historical SQLite importer path, compare row counts table-by-table after import.
- For the current final cutover path, compare source Postgres and restored target row counts table-by-table after restore.
- Validate unique constraints explicitly.
- Check for release identity duplicates by:
  - `source + source_product_id`
  - SKU
  - slug
- Spot-check user-private ownership domains.
- Verify release-linked child tables still resolve correctly.
- Verify steps, exposures, and health snapshots for sample users.

## Type and constraint handling

- Booleans: verify defaults and existing values survive import correctly.
- Numerics: verify `Numeric(10,2)` and `Numeric(18,6)` precision round-trips cleanly.
- Text/case sensitivity: check any uniqueness assumptions tied to email, username, slug, and source identifiers.
- Timestamps: audit naive datetime interpretation carefully; do not rely on implicit timezone behavior.
- Unique constraints: pre-audit data before applying stricter target constraints.

# 8. Auth migration design

## Hybrid auth period

- Keep Flask as the primary backend.
- Add Supabase Auth as a new identity provider while preserving the app-owned `User` table.
- Continue app-side authorization based on app-owned roles and domain records.

## Introducing `supabase_auth_user_id`

- Add an explicit nullable linkage field on `User`.
- Backfill it during auth migration prep or onboarding.
- Index it uniquely once the mapping is authoritative.

## Email-first login transition

- Move the login model toward email-first auth when Supabase Auth is introduced.
- Keep `username` as application/profile data if still useful for display or personalization.
- Do not force `username` to remain the primary auth identifier after Supabase Auth.

## What happens to username

- Keep it as app-owned data.
- Preserve existing usernames for continuity.
- Do not treat it as the canonical auth identity once Supabase Auth is live.

## Password reset and email verification replacement

- Replace app-managed `itsdangerous` reset and confirmation flows with Supabase-managed identity flows.
- Keep transition logic explicit and reversible during rollout.

## Browser session cutover

- Old Flask-authenticated sessions should be considered invalid once the auth authority changes.
- Cutover should include a controlled re-authentication plan.

## Mobile token coexistence

- Keep existing `UserApiToken` behavior during the first auth transition period unless there is a strong reason to replace it immediately.
- Revisit later whether mobile should move fully to Supabase-issued auth.

## Rollback strategy

- Keep app-level `User` data intact throughout.
- Do not remove old auth-related code paths until Supabase Auth has been proven in staging and the production cutover is stable.
- Preserve the ability to restore app-managed login behavior during the transition window if needed.

# 9. Testing strategy

## Unit/integration tests

- Run `python -m pytest`.
- Prioritize:
  - auth tests
  - profile tests
  - release import/display/detail tests
  - sneakers tests
  - steps/exposure tests
  - wishlist tests
  - money utility tests
  - news/content tests

## Migration verification tests

- Apply Alembic to fresh Postgres.
- Import representative SQLite data into staging Postgres.
- Rehearse current-Postgres-source dump and restore into a fresh Postgres target before any future target move or major restore.
- Verify schema diff stability after import.
- Run smoke tests against Postgres-backed app configuration.

## Manual QA flows

- register
- confirm email
- login/logout
- request password reset
- change pending email
- update profile
- create/revoke mobile token
- collection CRUD
- wishlist add/remove
- release calendar and release detail
- CSV release import preview/confirm
- KicksDB ingestion/update
- steps ingestion
- attribution recompute
- exposure and health flows
- article admin and public rendering

## Completed staging checks for Postgres cutover

- staging uses Supabase Postgres
- all env vars are correct
- database connectivity is stable
- migrations run cleanly
- row counts and sample integrity checks pass
- no blocking query/performance problems are visible

Current result:

- These checks have passed for the completed Postgres cutover.
- Continue monitoring the active target `sjwdvsefjlflgavshiyy`.
- Keep previous project `mizyioplztuzycipfdsd` temporarily before retirement.

# 10. Production cutover checklist

Canonical execution runbook:

- [PRODUCTION_POSTGRES_CUTOVER_RUNBOOK.md](/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/docs/PRODUCTION_POSTGRES_CUTOVER_RUNBOOK.md)

## Pre-cutover checks

- all Phase 0 and Phase 1 tasks complete
- staging migration validated
- current Postgres source database confirmed as latest source of truth
- production DB backup plan confirmed as full Postgres dump/restore, not CSV
- fresh target restore plan confirmed
- rollback path confirmed
- release identity and release-price decisions implemented

## Migration steps

1. freeze writes or define maintenance window
2. take a full Postgres dump from the current source database
3. record dump checksum, size, source revision, and secure storage locations
4. provision/verify a fresh production Supabase Postgres target
5. restore the Postgres dump into the fresh target
6. run `flask db current`; run `flask db upgrade` only if the restored target is behind the deployed code
7. verify row counts, sequences, key constraints, and smoke flows
8. switch production `DATABASE_URL` to the restored target
9. restart app using Postgres config

Completed result:

- The production-style cutover is complete using the public-schema logical dump/restore approach.
- Active DB target is `sjwdvsefjlflgavshiyy`.
- Previous DB target `mizyioplztuzycipfdsd` is fallback/reference only.

## Validation steps

- smoke-test login
- smoke-test homepage/profile
- validate collection and wishlist
- validate release calendar/detail
- validate admin release edit/import
- validate steps endpoints
- inspect logs for DB errors

## Rollback triggers

- failed auth/profile flows
- large row-count mismatch
- broken release detail or collection flows
- severe restore integrity failure
- repeated DB errors immediately after cutover

Rollback path:

- keep the source Postgres database untouched during cutover until the go/no-go decision is complete
- if rollback is needed, switch `DATABASE_URL` back to the preserved source Postgres database
- treat any writes accepted on the fresh target before rollback as non-authoritative unless separately reconciled
- retain earlier SQLite backups only as historical fallback, not as the normal rollback mechanism

## Post-cutover monitoring

- connection usage
- slow queries
- error rates
- release-page behavior
- step-sync success rate

# 11. Deferred items

These are intentionally not part of the first migration wave:

- broad RLS rollout
- direct client-side Supabase data access
- storage migration for uploads/images unless separately chosen
- JSONB refactors for article/schema/materials fields unless needed for launch
- replacing mobile bearer tokens immediately
- full auth cutover during the initial Postgres migration
- broad role-system redesign beyond current `is_admin`

Current deferred status:

- SendGrid/password-reset email delivery hardening remains deferred because Supabase Auth is planned.
- GOAT-specific enrichment parity remains lower priority than core platform/auth work.
- Supabase Auth is now the next major platform workstream.

# 12. Recommended next Codex tasks

1. Tasks 1 through 5 are complete.
2. The ORM-defaults vs DB/server-defaults audit is complete, with only `user.preferred_region` and `user_api_token.created_at` promoted to DB-level defaults.
3. Staging Supabase Postgres bootstrap and initial SQLite -> Postgres import validation are complete.
4. Postgres-source-of-truth -> fresh-target restore cutover is complete; current source of truth is `sjwdvsefjlflgavshiyy`.
5. Monitor the new target and retire old project `mizyioplztuzycipfdsd` only after rollback confidence is no longer needed.
6. Plan Supabase Auth migration: app-user linkage, session transition, email confirmation/password reset replacement, mobile token coexistence, and rollback.
7. Tidy remaining low-priority release enrichment gaps, especially GOAT-specific parity, after platform/auth priorities are clear.
