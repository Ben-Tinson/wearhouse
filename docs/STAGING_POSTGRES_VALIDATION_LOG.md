# Staging Postgres Validation Log

This log captures the closeout status of the staging validation pass against Supabase Postgres.

Reference:

- [STAGING_POSTGRES_VALIDATION_CHECKLIST.md](/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/docs/STAGING_POSTGRES_VALIDATION_CHECKLIST.md)
- [STAGING_STATUS_AND_NEXT_STEPS.md](/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/docs/STAGING_STATUS_AND_NEXT_STEPS.md)

## Summary

- Total blocking issues: `0` (for Postgres migration phase)
- Total high-priority issues: `0` active
- Total medium/low issues: `2` known follow-ups
- Overall recommendation (Postgres phase): `GO / completed`
- Test date: `2026-04-14`
- Tester: `staging validation pass + code/test verification`
- Staging environment notes:
  - Supabase Postgres schema bootstrap to Alembic head: complete
  - SQLite import to staging Postgres: complete
  - Production-style Postgres-source dump/restore to fresh target: complete
  - Active Supabase target after cutover: `sjwdvsefjlflgavshiyy`
  - Previous Supabase source retained temporarily: `mizyioplztuzycipfdsd`
  - Supabase Auth: not in scope for this phase

Post-cutover update:

- The app now runs against the fresh Supabase Postgres target `sjwdvsefjlflgavshiyy`.
- A public-schema logical dump/restore into the fresh target succeeded.
- App repoint succeeded and DB credentials were rotated post-cutover.
- Smoke checks passed for home, login/logout, profile, collection, rotation, sneaker detail, release calendar/detail, release resale refresh, and API token create/revoke.
- SQLite is now archival fallback only.

## Passed

- Migration/data integrity:
  - Alembic migration chain runs cleanly on Postgres.
  - SQLite-to-Postgres import path is repeatable and validated.
  - Post-import sequence handling is in place.
- Release/admin flows:
  - Release CSV import preview/apply path works.
  - StockX-backed release refresh works.
  - Mixed-source canonical priority now prefers StockX over GOAT when valid StockX identity/data exists.
  - Wishlist crash regression (Postgres result-shape issue) is fixed.
- Auth/token behavior:
  - Password-reset token generation and verification paths are working.
  - App auth/session basics (login/logout/invalid login handling) pass.
- General product areas:
  - Core collection/detail and health/event flows are functional in staging.
  - API token create/revoke and bearer-token paths are functional.

## Deferred By Choice

- SendGrid outbound email delivery hardening (legacy Flask email flows).
  - Reason: Supabase Auth is planned; avoid deep investment in outgoing-email infra that is likely to be replaced.
  - Current state: token logic works, external email send may fail without valid provider credentials.
- GOAT-specific parity enhancements beyond current fallback behavior.
  - Reason: no longer a core Postgres blocker with StockX-first canonical behavior in place.

## Still Needs Retest

- Full end-to-end legacy email delivery with real provider credentials and base URL, if the team decides to keep investing before Supabase Auth:
  - password reset email link delivery/open/submit
  - registration/email confirmation
  - pending email-change confirmation
- Old project retirement readiness:
  - confirm retention window for `mizyioplztuzycipfdsd`
  - confirm no rollback need before retirement

## True Blocker

- None for the Postgres migration phase closeout.

## Known Non-Blocking Follow-Ups

- Sneaker edit display consistency in detail views where release data can override edited fields.
- Sold-stat/admin reporting consistency after certain delete/sold paths.

## Go / No-Go Criteria

### 1. Migration / Data Integrity

- GO when:
  - migrations apply to head on staging Postgres without manual schema edits,
  - imported table counts/integrity checks pass,
  - sequence-backed inserts work post-import.
- Current decision: `GO`

### 2. Release / Admin Flows

- GO when:
  - release CSV import create/update paths are stable,
  - admin release market refresh works without uniqueness regressions,
  - mixed-source canonical source selection is deterministic (StockX-first when available).
- Current decision: `GO`

### 3. Auth / Email

- GO for Postgres phase when:
  - auth/session and token verification are functional,
  - email delivery dependencies are explicitly tracked as deferred or configured.
- NO-GO only if:
  - immediate release scope requires validated legacy email delivery before Supabase Auth transition.
- Current decision: `GO with defer waiver` (email delivery infra deferred by choice)

### 4. Performance / Readiness

- GO when:
  - no severe page-level regressions remain,
  - a targeted performance pass and operational cutover dry-run are completed.
- Current decision: `GO` for Postgres cutover; continue normal post-cutover monitoring on the new target.

## Final Recommendation

`GO / completed` for the Postgres migration and cutover phase.

Decision caveats:

- Keep SendGrid/email delivery as a tracked deferred item unless product scope changes before Supabase Auth cutover.
- Keep the old Supabase source project temporarily for rollback confidence before deliberate retirement.
- Treat Supabase Auth planning as the next major platform workstream.

- Status: `Pass / Fail / Not tested`
- Severity: `Blocking / High / Medium / Low`
- Notes:
- Follow-up action:

### Query performance / unexpected slow pages

- Status: `Pass / Fail / Not tested`
- Severity: `Blocking / High / Medium / Low`
- Notes:
- Follow-up action:
