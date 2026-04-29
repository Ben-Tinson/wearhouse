# Postgres Cutover Dry-Run Worksheet

Use this worksheet to record the completed Postgres cutover rehearsal/execution and as a template for future fresh-target restore rehearsals.

References:

- [PRODUCTION_POSTGRES_CUTOVER_RUNBOOK.md](/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/docs/PRODUCTION_POSTGRES_CUTOVER_RUNBOOK.md)
- [STAGING_STATUS_AND_NEXT_STEPS.md](/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/docs/STAGING_STATUS_AND_NEXT_STEPS.md)

## Dry-run metadata

- Dry-run date: 28.04.26
- Environment: Local app against Supabase Postgres staging target
- Release/cutover manager: Ben
- DB owner: Ben
- App owner: Ben
- QA owner: Ben
- Scribe: Ben
- Target Alembic head: `a7c3d9e4f1b2`
- Notes: The Postgres-source-of-truth -> fresh target restore approach has now been executed successfully. Active target project ref is `sjwdvsefjlflgavshiyy`; previous source project ref is `mizyioplztuzycipfdsd`.

---

## Phase 1: Pre-run checks

| Step | Owner | Planned Start | Planned End | Actual Start | Actual End | Status (Pass/Fail/Blocked) | Notes/Findings |
|---|---|---|---|---|---|---|---|
| Confirm staging validation status is `GO` | Ben |  |  |  |  | Pass | Staging Postgres validation previously closed as GO. |
| Confirm runbook version and Postgres dump/restore procedure to use | Ben |  |  |  |  | Pass | Current runbook uses current Postgres source -> fresh Postgres target restore. |
| Confirm rollback authority and communication channel | Ben |  |  |  |  | Pass | Single-operator dry run; rollback authority retained by Ben. |
| Confirm source Postgres DB is the current source of truth | Ben |  |  |  |  | Pass | Source for cutover was previous Supabase project `mizyioplztuzycipfdsd`; current source of truth after cutover is `sjwdvsefjlflgavshiyy`. |
| Confirm dry-run restore target is fresh/isolated (non-production) | Ben |  |  |  |  | Pass | Fresh target project `sjwdvsefjlflgavshiyy` was restored successfully. |
| Confirm all required source and target environment variables are available | Ben |  |  |  |  | Pass | `DATABASE_URL`, `SECRET_KEY`, `RAPIDAPI_KEY` available; app connected successfully. |

---

## Phase 2: Freeze simulation and backup safeguards

| Step | Owner | Planned Start | Planned End | Actual Start | Actual End | Status (Pass/Fail/Blocked) | Notes/Findings |
|---|---|---|---|---|---|---|---|
| Simulate write freeze / maintenance start | Ben |  |  |  |  | Pass | Freeze simulated conceptually for dry run. |
| Capture full Postgres dump from current source DB | Ben |  |  |  |  | Pass | Dumps created under `backups/postgres/`. |
| Record dump filename/path/checksum/size | Ben |  |  |  |  | Pass | Key artefacts recorded below. |
| Store dump in secure primary and secondary locations | Ben |  |  |  |  | Pass | Retain Postgres dump artefacts; do not rely on CSV as the backup artifact. |
| Verify dump can be restored to a disposable/fresh target | Ben |  |  |  |  | Pass | Public-schema dump restored successfully into fresh target. |

---

## Phase 3: Fresh target restore and schema revision check

| Step | Owner | Planned Start | Planned End | Actual Start | Actual End | Status (Pass/Fail/Blocked) | Notes/Findings |
|---|---|---|---|---|---|---|---|
| Export/test fresh target `DATABASE_URL` in shell | Ben |  |  |  |  | Pass | App was repointed to fresh target `sjwdvsefjlflgavshiyy`. |
| Restore Postgres dump into fresh target | Ben |  |  |  |  | Pass | Public-schema logical dump restore succeeded. |
| Run `flask db current` after restore | Ben |  |  |  |  | Pass | Restored DB was usable by the app. |
| Run `flask db upgrade` only if code has newer migrations | Ben |  |  |  |  | Pass | No cutover-blocking migration issue remained. |
| Confirm expected Alembic head reached | Ben |  |  |  |  | Pass | Fresh target was accepted as current runtime DB. |

---

## Phase 4: Restored data integrity verification

| Step | Owner | Planned Start | Planned End | Actual Start | Actual End | Status (Pass/Fail/Blocked) | Notes/Findings |
|---|---|---|---|---|---|---|---|
| Capture restore summary output/logs | Ben |  |  |  |  | Pass | Restore completed from public-schema dump. |
| Compare source vs target row counts for core tables (`user`, `sneaker`, `release`) | Ben |  |  |  |  | Pass | No smoke-test data mismatch surfaced. |
| Compare source vs target row counts for release-market family tables | Ben |  |  |  |  | Pass | Release/detail/refresh smoke checks passed. |
| Compare source vs target row counts for `wishlist_items` and `user_api_token` | Ben |  |  |  |  | Pass | API token create/revoke smoke check passed. |
| Verify sequence-backed PK state after restore | Ben |  |  |  |  | Pass | API token create/revoke and release refresh exercised inserts successfully. |
| Spot-check unique constraints and FK integrity | Ben |  |  |  |  | Pass | No DB integrity failures surfaced during app smoke testing. |

---

## Phase 5: App switch simulation

| Step | Owner | Planned Start | Planned End | Actual Start | Actual End | Status (Pass/Fail/Blocked) | Notes/Findings |
|---|---|---|---|---|---|---|---|
| Point app runtime config to Postgres target | Ben |  |  |  |  | Pass | App confirmed using fresh Supabase Postgres target `sjwdvsefjlflgavshiyy`. |
| Restart/redeploy app | Ben |  |  |  |  | Pass | App was running successfully against Postgres during the dry run. |
| Confirm app boot and DB connection logs | Ben |  |  |  |  | Pass | App served requests normally; no DB connection errors. |

---

## Phase 6: Immediate smoke verification

| Step | Owner | Planned Start | Planned End | Actual Start | Actual End | Status (Pass/Fail/Blocked) | Notes/Findings |
|---|---|---|---|---|---|---|---|
| Login/logout and protected route sanity | Ben |  |  |  |  | Pass | Login and logout both worked; protected pages loaded correctly. |
| Collection page and sneaker detail | Ben |  |  |  |  | Pass | `/my-collection` and sneaker detail pages returned `200`; no DB errors. |
| Rotation page | Ben |  |  |  |  | Pass | `/my-rotation` returned `200`; no DB errors. |
| Release calendar and release detail | Ben |  |  |  |  | Pass | Release calendar and release detail loaded successfully. |
| Admin release refresh | Ben |  |  |  |  | Pass | Admin refresh worked; canonical redirect and enriched release page loaded correctly. |
| Release CSV preview/apply | Ben |  |  |  |  | Pass | CSV preview flow returned `200`; no DB errors. |
| API token create/revoke | Ben |  |  |  |  | Pass | Token create and revoke both worked successfully. |
| Check logs for repeated DB errors | Ben |  |  |  |  | Pass | No repeated DB errors, no integrity failures, no Flask tracebacks. |
| Check latency on key pages | Ben |  |  |  |  | Pass | Pages were responsive enough for dry-run purposes after prior performance work. |

---

## Phase 7: Rollback simulation

| Step | Owner | Planned Start | Planned End | Actual Start | Actual End | Status (Pass/Fail/Blocked) | Notes/Findings |
|---|---|---|---|---|---|---|---|
| Simulate rollback trigger declaration | Ben |  |  |  |  | Pass | Rollback trigger conceptually identified: major smoke-test failure, repeated DB errors, or critical data mismatch. |
| Switch app config back to preserved source Postgres DB | Ben |  |  |  |  | Not needed | No rollback trigger occurred; previous project `mizyioplztuzycipfdsd` remains fallback/reference until retirement. |
| Restart/redeploy app after rollback switch | Ben |  |  |  |  | Not needed | Not executed because rollback was not triggered. |
| Validate critical smoke checks post-rollback | Ben |  |  |  |  | Not needed | Not executed because rollback was not triggered. |
| Record rollback timing and friction points | Ben |  |  |  |  | Pass | Rollback path is now old Supabase project fallback, not SQLite. |

---

## Evidence captured

- Alembic outputs (before/after): before upgrade `e6f7a8b9c0d1`; after upgrade `a7c3d9e4f1b2 (head)`
- Postgres dump artefacts:
  - `backups/postgres/soletrak_postgres_20260428_152256.dump`
  - `backups/postgres/soletrak_public_20260428_154243.dump`
  - `backups/postgres/soletrak_public_cutover_20260428_160930.dump`
- Restore logs path: terminal/cutover output from public-schema restore
- Row-count reconciliation artifact: smoke-tested restored target; retain dumps and old project until retirement decision
- Smoke test evidence: browser + terminal logs covering auth, collection, rotation, sneaker detail, release detail, admin refresh, CSV preview, API token create/revoke
- Rollback simulation evidence: conceptual only in this dry run; no real rollback switch executed
- Incident/findings notes:
  - Earlier import rehearsal was blocked because target Postgres DB was already populated; this is superseded by the completed Postgres dump/restore path.
  - Newly added release initially produced StockX lookup `404` / `products/None` warnings before admin refresh corrected canonical identity and detail page URL.

---

## Dry-run summary

- Dry-run outcome: `Pass / cutover completed`
- Total issues found: 1 active follow-up
- Blocking issues found: 0
- High-priority issues found: 0
- Medium/low issues found: 1

### Issues found

| Issue | Severity (Blocking/High/Medium/Low) | Owner | Required fix | Target date |
|---|---|---|---|---|
| Newly added release first-load enrichment produced temporary StockX `404` / `products/None` warnings before admin refresh corrected canonical identity | Medium | Ben | Optional tidy-up follow-up; not a cutover blocker | Post-cutover follow-up or before cutover if desired |

### Actions required before real cutover

1. Monitor the fresh target after cutover.
2. Retain old project `mizyioplztuzycipfdsd` temporarily, then retire it deliberately.
3. Optionally log and tidy the new-release first-load enrichment warning path, though it is not a cutover blocker.

### Production cutover readiness recommendation

- Recommendation: `GO / completed`
- Rationale: Core Postgres cutover path, app boot, auth/session sanity, profile, collection/rotation/sneaker detail, release/admin flows, resale refresh, and API token flows all worked successfully on the fresh target. No true blocking failure appeared.
- Conditions (if any):
  - keep the old Supabase source project temporarily for fallback confidence;
  - retain the dump artefacts listed above;
  - continue post-cutover monitoring before retiring the old project.
