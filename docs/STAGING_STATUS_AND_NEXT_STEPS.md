# Staging Status And Next Steps

## Current platform status

Soletrak has completed the Postgres cutover to a fresh Supabase Postgres target. The current/latest source of truth is now the new Supabase project `sjwdvsefjlflgavshiyy`.

Current active state:

- Postgres phase status: `completed / GO`
- Active Supabase project ref: `sjwdvsefjlflgavshiyy`
- Previous Supabase project ref: `mizyioplztuzycipfdsd`, retained temporarily as fallback/reference before retirement
- SQLite: archival fallback only, not the operational source of truth
- Flask backend/auth: still live
- Supabase Auth: planned next, not implemented yet
- Decision basis: staging validation plus completed production cutover smoke checks

## Effectively passing

- SQLite -> Supabase Postgres migration chain runs cleanly to head.
- SQLite -> Postgres data import process is working with sequence-reset handling and integrity checks.
- Public-schema Postgres dump/restore to the fresh target succeeded.
- Flask app repoint to the new Supabase target succeeded.
- Current Postgres target is now the operational source of truth.
- StockX-backed release market refresh is working end-to-end.
- Mixed-source canonical source priority now prefers StockX over GOAT when valid StockX identity/data is available.
- Release CSV import flow is functioning (preview + apply/update behavior).
- Auth token generation/verification paths are functioning (password-reset token generation/verification and app-level auth token behavior).
- Smoke checks passed on the new target: home, login/logout, profile, collection, rotation, sneaker detail, release calendar/detail, release resale refresh, and API token create/revoke.

Backup artefacts retained:

- `backups/postgres/soletrak_postgres_20260428_152256.dump`
- `backups/postgres/soletrak_public_20260428_154243.dump`
- `backups/postgres/soletrak_public_cutover_20260428_160930.dump`

## Current blockers

- No hard product blocker remains in the Postgres migration slice that prevents staging app validation from continuing.
- Remaining auth-email testing is blocked by external mail provider configuration, not by token logic.

## Deferred items (intentional)

- SendGrid/email delivery hardening is deferred as a launch-critical investment because Supabase Auth is planned as the future identity provider.
  - Reason: avoid over-investing in legacy email/password infrastructure that is expected to be replaced or reduced in scope.
- GOAT-specific enrichment parity is deferred where StockX-backed flow already satisfies canonical release refresh needs.
  - Reason: not a core blocker for current staging readiness; prioritize launch-critical stability and migration completion first.

## Recommended next workstream

Proceed with post-cutover monitoring and Supabase Auth planning for the Flask + Supabase Postgres architecture.

Focus areas:

- Monitor the new Supabase target for errors, slow queries, connection usage, and data anomalies.
- Keep old source project `mizyioplztuzycipfdsd` temporarily for rollback confidence, then retire it deliberately.
- Plan Supabase Auth migration without breaking current Flask auth, admin, profile/account, and API token flows.

Important cutover outcome:

- Earlier planning assumed the final cutover would replay SQLite -> Postgres import into production.
- The completed cutover used Postgres-source-of-truth -> fresh Postgres target restore.
- Future operational restores/cutovers should use full Postgres dump/restore.
- CSV and SQLite are not the normal operational backup/restore path.

Operational runbook:

- [PRODUCTION_POSTGRES_CUTOVER_RUNBOOK.md](/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/docs/PRODUCTION_POSTGRES_CUTOVER_RUNBOOK.md)

## Recommended next 3 development tasks (priority order)

1. Plan and design the Supabase Auth migration, including app-user linkage, session transition, email confirmation/password reset replacement, and rollback.
2. Complete post-cutover monitoring and define the retirement plan for old project `mizyioplztuzycipfdsd`.
3. Tidy remaining low-priority release enrichment gaps, especially GOAT-specific parity, only after platform/auth priorities are clear.
