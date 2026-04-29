# Soletrak Production Postgres Cutover Runbook

This runbook records the completed production cutover of Soletrak onto the fresh Supabase Postgres target and remains the reference for future Postgres restore/cutover exercises.

Scope:

- Flask remains the backend.
- SQLAlchemy remains the ORM.
- Alembic remains the migration mechanism.
- Supabase Auth was out of scope for this cutover and remains a future phase.

Current operational state:

- Cutover status: `completed / GO`.
- Active Supabase Postgres project ref: `sjwdvsefjlflgavshiyy`.
- Previous Supabase Postgres source project ref: `mizyioplztuzycipfdsd` is fallback/reference only until retirement.
- The current/latest source of truth is the new Supabase Postgres target project.
- SQLite is archival fallback only, not the normal operational cutover source.
- Future restores/cutovers should use Postgres dump/restore, not SQLite re-import.

Completed cutover outcome:

- Public-schema logical dump/restore to the fresh target succeeded.
- Flask app repoint to the new target succeeded.
- DB credentials were rotated post-cutover.
- Smoke checks passed for home, login/logout, profile, collection, rotation, sneaker detail, release calendar/detail, release resale refresh, and API token create/revoke.
- Backup artefacts to retain:
  - `backups/postgres/soletrak_postgres_20260428_152256.dump`
  - `backups/postgres/soletrak_public_20260428_154243.dump`
  - `backups/postgres/soletrak_public_cutover_20260428_160930.dump`

Primary references:

- [SUPABASE_POSTGRES_MIGRATION_PLAN.md](/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/docs/SUPABASE_POSTGRES_MIGRATION_PLAN.md)
- [STAGING_POSTGRES_VALIDATION_LOG.md](/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/docs/STAGING_POSTGRES_VALIDATION_LOG.md)

## 1. Roles and ownership

- `Release Manager`: owns cutover window, go/no-go calls.
- `DB Owner`: owns Postgres backup, fresh-target restore, Alembic revision check, and DB validation.
- `App Owner`: owns env switch, deployment restart, smoke tests.
- `QA Owner`: owns post-cutover functional verification.
- `Observer/Scribe`: records timestamps, outcomes, and incidents.

## 2. Completed pre-cutover checks

All were green before cutover:

- Staging is closed as `GO`.
- Latest migration head is known and committed.
- Current Postgres source database is identified and confirmed as the latest data source.
- Fresh target Postgres database/project is provisioned or ready to provision.
- Production maintenance window is scheduled and communicated.
- Rollback decision authority is confirmed.

Environment/config checks:

- Production `DATABASE_URL` for the fresh Supabase target is available and tested.
- SSL mode and connectivity requirements are confirmed.
- `SECRET_KEY` and all required app secrets are present.
- Email-related settings are explicitly marked in/out of cutover scope.

Backup/export safeguards:

- Take a full logical dump of the current Postgres source database.
- Recommended format: `pg_dump -Fc --no-owner --no-acl` using the direct/session Supabase connection string with SSL enabled.
- Store the dump in at least two secure locations.
- Record dump filename, checksum, timestamp, source connection/project, Alembic revision, and owner.
- Verify the dump by restoring it into a disposable/fresh Postgres target before using it for production cutover.
- Do not rely on CSV export as the production backup format. CSV can support spot checks, but it is not the rollback artifact.

## 3. Completed cutover execution checklist

### Phase A: Freeze and backup

1. Announce maintenance start.
2. Disable writes (maintenance mode or temporary write freeze).
3. Confirm the current Postgres source is the latest source of truth.
4. Take a final full Postgres dump from the current source database.
5. Verify dump checksum and capture row counts on key tables.

### Phase B: Fresh target restore

1. Confirm the fresh production Supabase target is reachable and isolated.
2. Restore the final Postgres dump into the fresh target.
3. Run `flask db current` against the restored target.
4. Run `flask db upgrade` only if the deployed code contains migrations newer than the dumped database revision.
5. Record Alembic revision after restore/upgrade.

### Phase C: Data verification

1. Compare source Postgres and restored target row counts for critical tables:
   - `user`, `sneaker`, `release`, `affiliate_offer`, `release_price`, `release_size_bid`, `wishlist_items`, `user_api_token`
2. Verify sequence-backed tables can accept a test insert in a transaction that is rolled back, or inspect sequence state versus `MAX(id)`.
3. Spot-check critical constraints and indexes on the restored target.
4. Capture restore and verification logs.

### Phase D: App switch

1. Set production runtime `DATABASE_URL` to Supabase Postgres.
2. Restart/redeploy app processes.
3. Confirm app boot logs show Postgres connection.

Completed result:

- Source project `mizyioplztuzycipfdsd` was dumped.
- Fresh target project `sjwdvsefjlflgavshiyy` was restored from the public-schema cutover dump.
- App runtime was repointed to `sjwdvsefjlflgavshiyy`.

## 4. Immediate post-cutover verification

Critical smoke checks:

- Home page: passed.
- Login/logout and protected-route access: passed.
- Profile page: passed.
- Collection page load and sneaker detail load: passed.
- Rotation page load: passed.
- Release calendar and release detail load: passed.
- Release resale/admin refresh endpoint behavior: passed.
- API token create/revoke path: passed.

Data integrity spot checks:

- Random sample of users: sneaker counts match pre-cutover.
- Random sample of releases: canonical source fields and offers look valid.
- Random sample of release market tables: no obvious null/duplicate anomalies.

Operational checks:

- No sustained DB exceptions in logs.
- No repeated unique/foreign-key failure loops.
- Connection count and response latency are acceptable.

## 5. Go/no-go result

Cutover proceeded because:

- Full Postgres dump completes and checksum is recorded.
- Restore to the fresh target completes without critical integrity errors.
- Alembic is at the expected head or has been upgraded cleanly after restore.
- Critical smoke checks pass.

Cutover status:

- `GO / completed`
- No rollback trigger was hit during smoke validation.

## 6. Rollback triggers

Trigger rollback if any of these occur and cannot be resolved quickly:

- Auth/session flows fail broadly.
- Repeated 5xx due to DB/query errors.
- Critical data mismatch in core entities (`user`, `sneaker`, `release`).
- Release/admin critical flows fail in a way that blocks operation.
- Severe performance degradation that makes core pages unusable.
- Restored target has row-count or constraint mismatches that cannot be explained quickly.

## 7. Rollback plan

1. Announce rollback start.
2. Re-enable maintenance/write freeze if needed.
3. Point production app back to the preserved current Postgres source database.
4. Restart/redeploy app.
5. Validate critical smoke checks on rollback environment.
6. Keep the failed fresh target untouched for forensic analysis.
7. Record incident summary, root-cause hypothesis, and next-attempt prerequisites.

Current rollback implications:

- Because the previous source project remains temporarily preserved, rollback confidence comes from the old Supabase Postgres project, not SQLite.
- Any writes accepted on the fresh target before rollback must be treated as potentially lost unless separately reconciled.
- Keep the old source project temporarily for rollback confidence before retirement.
- SQLite backups from the earlier migration remain useful for historical disaster recovery only; they are not the normal rollback path for this cutover.

## 8. Future dry-run checklist

For any future target move or major restore, rehearse in a production-like environment:

- Use the current Postgres database as the source.
- Take a full Postgres dump with the exact intended command/options.
- Restore the dump into a fresh Postgres target.
- Run `flask db current` and only run `flask db upgrade` if the restored database is behind the target code revision.
- Execute the same smoke test list as production.
- Simulate rollback by switching app config back to the preserved source Postgres database and verifying recovery.

Dry-run success criteria:

- End-to-end dump/restore rehearsal completes without manual DB patching.
- Timings are captured for each phase (freeze, dump, restore, verify, rollback simulation).
- Any required runbook edits are made before production window.

## 9. Evidence to capture

- Alembic current/head output before and after.
- Postgres dump command/options, checksum, size, and storage locations.
- Restore logs and row-count reconciliation report.
- Smoke test outcomes with timestamps and owners.
- Decision timestamps (`GO`, `NO-GO`, or `ROLLBACK`).

## 10. Post-cutover follow-up (next 24-72h)

- Monitor error rates, slow queries, and connection usage.
- Re-run targeted high-value user journeys daily.
- Document any query/index follow-up discovered under live load.
- Keep Supabase Auth work separate from this stabilization window.
- Plan retirement timing for previous project `mizyioplztuzycipfdsd`.
- Keep backup artefacts under `backups/postgres/` until a formal retention decision is made.
