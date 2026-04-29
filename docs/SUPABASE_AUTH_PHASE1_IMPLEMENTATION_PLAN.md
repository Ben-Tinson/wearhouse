# Supabase Auth — Phase 1 Implementation Plan

This document defines the **first concrete preparation slice** for the Supabase Auth migration. It is a design/implementation plan only — no code, schema, or migrations are being applied by this document.

References:
- `docs/SUPABASE_AUTH_MIGRATION_PLAN.md` — overall phased plan.
- `docs/SUPABASE_AUTH_READINESS_REVIEW.md` — current-state gap analysis.

Baseline (confirmed against repo state at time of writing):
- Soletrak runs on Supabase Postgres project `sjwdvsefjlflgavshiyy` (cutover complete).
- Flask remains the live backend; Flask-Login is the live browser auth path.
- App-owned `user` table is the FK target for ~14 user-owned tables and remains the account/profile anchor.
- `UserApiToken` (mobile/API bearer) flows are live and must keep working.
- Supabase Auth is not implemented and has no imports anywhere in the codebase.
- Phase 1 must **not** change live login, admin, profile, or `UserApiToken` behaviour.

---

## 1. Objective of Phase 1

Phase 1 prepares the data and code surface for Supabase Auth integration **without changing any live auth behaviour**. After Phase 1 lands:

- The `user` table contains a dormant linkage column ready for Supabase Auth identity IDs.
- The team has a deterministic, reproducible read-only report of data hazards (case-collision emails, pending-email rows, unconfirmed admins, empty identifiers) that must be resolved before backfill.
- A single internal seam exists for "who is the current app user?" so that Phase 2 can introduce a Supabase-aware code path without touching every route.

Why this is the safest first step:
- Zero impact on running Flask-Login, admin, profile, or mobile-token flows.
- Reversible: the migration is a column add with a partial unique index; no existing data is rewritten.
- No new runtime dependency, no Supabase SDK install, no environment-variable changes that affect behaviour today.
- Surfaces real data hazards *before* any backfill or identity-creation work commits us to a path.
- Provides the canonical link the migration plan recommends (`docs/SUPABASE_AUTH_MIGRATION_PLAN.md` §4 "Recommended Canonical Linkage Model"), but leaves it inert until Phase 2.
- Unblocks Phase 2 work (JWT verifier, linkage service, bridge endpoint) without committing to product decisions yet.

---

## 2. Exact Scope

### In scope (Phase 1)

1. **Read-only auth audit script.** A new CLI under `scripts/`. Reads `user` and `user_api_token` rows, reports data hazards. Writes nothing to the DB.
2. **Schema migration: linkage column.** A single Alembic migration adding `user.supabase_auth_user_id` (UUID, nullable), plus a partial unique index where the column is non-null. Optionally adds `user.created_at`. (See §3 for the recommendation; `last_login_at` is **deferred**.)
3. **Model field declaration.** Add the new column(s) to the `User` SQLAlchemy model so future code can read/write them. No call sites use the column yet.
4. **App-user resolver abstraction (no-op shim).** A small helper module — likely `services/auth_resolver.py` — exposing `get_current_app_user()` that today simply returns `current_user if current_user.is_authenticated else None`. No existing call sites are migrated to it in Phase 1; it exists so Phase 2 has a single seam to extend.
5. **Tests** for the migration round-trip, the audit script's structure, and the resolver shim's parity with `current_user`.
6. **Documentation updates.** `docs/MODULE_MAP.md`, `docs/DECISIONS.md`, and `docs/AI_CONTEXT.md` get short pointers reflecting the new column and resolver.

### Intentionally excluded from Phase 1

- No Supabase SDK / `gotrue` / JWT verification dependency.
- No new environment variables that change behaviour. (Decision on `SUPABASE_*` env vars is documented but values are not wired into `Config` in Phase 1.)
- No changes to `routes/auth_routes.py`, `routes/main_routes.py`, `routes/sneakers_routes.py`, `routes/news_routes.py`.
- No changes to `decorators.py` (`admin_required`, `bearer_or_login_required` untouched).
- No changes to `forms.py` (`LoginForm` still username-based).
- No changes to login, logout, registration, password reset, email confirmation, email change, profile, API token, or admin flows.
- No backfill of `supabase_auth_user_id` for any user. The column lands fully NULL.
- No creation of any Supabase Auth identities. No calls to the Supabase admin API.
- No changes to templates or test fixtures.
- No changes to `SECRET_KEY`, session cookie behaviour, or Flask-Login wiring.
- No RLS policies. No `public.user` access policies of any kind.
- No mobile/API token strategy change.
- No removal or rename of `password_hash`, `is_email_confirmed`, `pending_email`, or any itsdangerous token method on `User`.

---

## 3. Proposed Schema Change

### Recommended field(s)

| Column | Type | Nullable | Default | Purpose |
|---|---|---|---|---|
| `user.supabase_auth_user_id` | `UUID` (Postgres native) | Yes | `NULL` | Canonical link to a Supabase Auth identity. NULL = not yet linked. |
| `user.created_at` | `TIMESTAMP` (timezone-naive, to match other `User` columns) | Yes | `NULL` | Audit / cohort ordering for Phase 3 backfill. |

### Type recommendation

- **`supabase_auth_user_id`: native Postgres `UUID`.** Supabase Auth identity IDs are UUIDs; we are on Postgres; native UUID gives stricter validation and smaller storage than `String(36)`. Use `sqlalchemy.dialects.postgresql.UUID(as_uuid=True)` in the model.
- **`created_at`: `DateTime` without timezone**, mirroring existing columns elsewhere in `models.py` (e.g. `UserApiToken.created_at`). This keeps the new column consistent with the rest of the schema and avoids introducing a timezone discipline we don't currently enforce.

### Nullability

- `supabase_auth_user_id`: **nullable**, mandatory for Phase 1. Every existing row stays NULL until Phase 3 backfill. Tightening to `NOT NULL` is a separate decision deferred to Phase 5 (Legacy retirement).
- `created_at`: **nullable**, with a `server_default=now()` for new rows from this migration onward. Backfilling existing rows is **not required** for Phase 1; existing rows can stay NULL and a later one-shot script (or a no-op decision) handles them. Keeping it nullable avoids a long-running `UPDATE` over the whole table during the migration.

### Uniqueness / index recommendation

- **Partial unique index** on `supabase_auth_user_id` where the value is not null:

  ```
  CREATE UNIQUE INDEX uq_user_supabase_auth_user_id
    ON "user" (supabase_auth_user_id)
    WHERE supabase_auth_user_id IS NOT NULL;
  ```

  Rationale: many rows will be NULL during Phase 1-3, and Postgres treats NULLs as distinct in regular unique indexes — a non-partial unique index would technically work but a partial index is the explicit, documented form.

- **No index on `created_at`** in this phase. Add it later if/when a query actually needs it.

### `last_login_at` — defer

`last_login_at` is **not** included in this migration.

Reasons to defer:
- Phase 1 does not write to it (no auth-route changes), so the column would land permanently NULL until a future change.
- Adding it now without a writer creates an empty column that future readers might misinterpret as "user has never logged in" rather than "we don't track this yet".
- Cohort selection for Phase 3 (active vs dormant users) can be approximated from existing `Sneaker.purchase_date`, `SneakerWear.worn_at`, `StepBucket.bucket_start`, or `UserApiToken.last_used_at` if needed. We are not blocked.

Add `last_login_at` in **Phase 2**, in the same change that introduces a Supabase login bridge, so the column ships with a writer.

### Migration safety notes for existing data

- **No data rewrite.** The migration adds two columns and one index. No existing rows are updated.
- **`server_default=now()` on `created_at`** applies to new rows only; this avoids a table-wide `UPDATE` on a multi-million-row scale (Soletrak isn't there, but the discipline matters).
- **Partial index build** is fast on a column that is entirely NULL (the index covers zero rows on creation).
- **Reversibility:** the `downgrade` step drops the index then both columns — no data is lost since neither column is populated.
- **Lock footprint (Postgres 12+):** `ALTER TABLE ADD COLUMN ... DEFAULT now()` with a non-volatile default is metadata-only on recent Postgres; with `now()` (volatile) it would historically rewrite the table. To stay safe:
  - For `supabase_auth_user_id`: no default → metadata-only.
  - For `created_at`: prefer **adding the column without a server default** in this migration, then a follow-up `ALTER COLUMN ... SET DEFAULT now()` (which is metadata-only). Or skip `created_at` from this migration entirely and ship it with Phase 2. Either is fine; the readiness review noted `created_at` was a "nice to have" for cohorting, not a blocker.
- **Foreign keys:** unchanged. `user.id` integer PK is untouched. The new column is not referenced by any other table.
- **Test DB (SQLite via `TestConfig`):** SQLite does not have a native UUID type. SQLAlchemy will emit it as `CHAR(32)` under SQLite; `as_uuid=True` still round-trips. The model declaration must use the dialect-aware import to keep tests working. Verify with the existing `pytest` suite.

If the team prefers absolute minimum surface in this migration, an acceptable variant is:
- **Variant A (recommended):** add `supabase_auth_user_id` + partial unique index. Defer `created_at` to Phase 2.
- **Variant B:** add `supabase_auth_user_id` + partial unique index + `created_at` (no `server_default` in this migration; set the default in a tiny follow-up).

Either variant is consistent with the readiness review. Variant A is slightly safer because it has fewer moving parts; Variant B saves one migration later. Default to Variant A unless there is a concrete near-term consumer for `created_at`.

---

## 4. Auth Audit Script Design

### Purpose

A read-only diagnostic to surface data hazards that would corrupt linkage in Phase 2/3. The script does not mutate any state. Its output is the gating input for "are we safe to start the Supabase Auth identity backfill?"

### Location

`scripts/auth_audit_users.py`. Consistent with existing utilities (`scraper.py`, `make_admin.py`, `scripts/set_fx_rate.py`). Run as `python scripts/auth_audit_users.py [--format text|json] [--output PATH]`.

### Inputs

- Reads from the configured `SQLALCHEMY_DATABASE_URI` (i.e. whatever the running Flask app would connect to). No CLI flag for connection target — uses `create_app()` and the existing `db.session`.
- Optional CLI flags:
  - `--format {text,json}` (default `text`). `json` is used by CI gating.
  - `--output PATH` (default stdout). Writes the report to a file when provided.

### Outputs

- Stdout (or file) report. No DB writes. No network calls.
- Exit code:
  - `0` — clean (no blocking hazards detected; warnings allowed).
  - `1` — at least one **blocking** hazard present (see §4 "Severity").
  - `2` — the script itself failed (DB unreachable, etc.).

### Checks to perform

| # | Check | Severity | Reason |
|---|---|---|---|
| C1 | Total counts: `users`, `users_admin`, `users_email_confirmed`, `users_with_pending_email`, `user_api_tokens_active` | info | Baseline for sanity. |
| C2 | Duplicate emails by case-insensitive match: `lower(email)` collisions | **blocking** | Linkage by email becomes ambiguous; must be resolved before backfill. |
| C3 | Duplicate usernames by exact match (defensive — column is `unique=True` but worth verifying) | **blocking** if found | Indicates DB integrity problem unrelated to Supabase but must be resolved. |
| C4 | Users with empty / whitespace-only `email` | **blocking** | Cannot create a Supabase identity without a valid email. |
| C5 | Users with empty / whitespace-only `username` | warning | App-side display issue; not strictly blocking for Supabase Auth. |
| C6 | Users with `is_email_confirmed=False` (count + admin breakdown) | warning | Reflects pre-existing unconfirmed accounts; may need product decision before backfill. |
| C7 | Users with `is_email_confirmed=False AND is_admin=True` | **blocking** | An unconfirmed admin during cutover is a lockout risk. Resolve first. |
| C8 | Users with non-null `pending_email` | warning | Edge case for backfill; do not silently link to pending email per plan §4. |
| C9 | Users where `pending_email` collides with another user's `email` (case-insensitive) | **blocking** | Indicates an in-flight email change against an already-claimed address. |
| C10 | Users with non-null `supabase_auth_user_id` (after Phase 1 migration runs, this should be zero) | **blocking** if nonzero | Sentinel that no rogue backfill has happened. |
| C11 | `UserApiToken` rows whose `user_id` does not resolve to a `User` row | **blocking** if found | Orphaned token; would behave unpredictably under any auth path. |
| C12 | `UserApiToken` rows where `revoked_at IS NULL` per user count distribution (top 10) | info | Sanity on active-token volume. |

Computed entirely in SQL where possible. The script can use the existing SQLAlchemy session; no new dependency.

### Suggested output format

**Text (default):**
```
=== Soletrak Auth Audit — 2026-04-28T15:42:11Z ===
DB: postgresql://…sjwdvsefjlflgavshiyy…  (project ref: sjwdvsefjlflgavshiyy)

[INFO]   Users total:                     N
[INFO]   Admins:                          N
[INFO]   Email-confirmed:                 N
[INFO]   Pending email rows:              N
[INFO]   Active UserApiTokens:            N

[OK]     C2 case-collision emails:        0
[BLOCK]  C7 unconfirmed admins:           2
           - user_id=12 username=… email=…
           - user_id=18 username=… email=…
[WARN]   C8 pending_email rows:           3
           - user_id=… email=… pending_email=…
           …

Summary: 1 blocking, 1 warning, 5 info.
Exit code: 1
```

**JSON (`--format json`):** the same content as a structured object, machine-readable. Suggested top-level shape:
```
{
  "generated_at": "2026-04-28T15:42:11Z",
  "db_project_ref": "sjwdvsefjlflgavshiyy",
  "checks": [
    {"id": "C7", "severity": "blocking", "count": 2, "rows": [...]},
    ...
  ],
  "summary": {"blocking": 1, "warning": 1, "info": 5},
  "exit_code": 1
}
```

### How to interpret results before Phase 2 begins

- **Any `[BLOCK]` row → fix the data first.** Phase 2 cannot start. Specifically:
  - C2/C3 → manual reconciliation (which user is the canonical owner of the colliding email/username?).
  - C4 → assign a real email or remove the orphan row.
  - C7 → either confirm the admin's email or revoke their admin flag before cutover (admin lockout is the highest-impact risk in the readiness review).
  - C9 → resolve the pending email change manually.
  - C10 → investigate; nothing should have backfilled this column in Phase 1.
  - C11 → revoke the orphan tokens.
- **`[WARN]` rows do not block Phase 2** but should be acknowledged in the Phase 2 design (e.g. how `pending_email` rows are handled during linkage).
- **`[INFO]` rows** are baseline stats. Useful in the rollout doc.
- The audit must be re-run **immediately before** any Phase 3 backfill to confirm the picture has not drifted.

---

## 5. App-User Resolver Abstraction Plan

### Problem it solves

Today, every route, form, and template reads `current_user` directly (200+ references across `routes/`, `forms.py`, and `templates/`, per the readiness review). This is fine while Flask-Login is the only auth path. As soon as Phase 2 introduces a Supabase JWT-aware path, the questions "who is the current app user?" and "is the current app user an admin?" need a single answer that can come from either provider.

Without a seam, every new code path either:
- duplicates resolution logic (provider-specific branches in many files), or
- forces a big-bang rewrite of every `current_user` reference at the moment Supabase becomes primary.

A small resolver shim, introduced in Phase 1 with no behaviour change, lets Phase 2 add a Supabase branch in exactly one place.

### Where it sits

A new module: `services/auth_resolver.py`. Co-located with other domain services. Importable from any blueprint without circular-import risk (it depends on `models`, `extensions`, `flask_login`, `flask.g`, `flask.request` — same dependency footprint as `decorators.py`).

Public surface in Phase 1:

```python
# services/auth_resolver.py — Phase 1 shape (no Supabase logic yet)

def get_current_app_user():
    """Return the resolved app User row, or None if no auth is established.

    Phase 1: this is exactly equivalent to
        current_user if current_user.is_authenticated else None
    Phase 2 will extend this with a Supabase JWT branch behind a feature flag.
    """

def get_current_app_user_id():
    """Return the integer user.id, or None. Convenience for query filters."""

def is_current_app_user_admin():
    """Return True iff a resolved user is admin. Equivalent in Phase 1 to
    current_user.is_authenticated and current_user.is_admin."""
```

The functions return `User` objects (not bare ids) so Phase 2 can swap the resolution path without changing the calling shape.

### Codepaths it is intended to de-risk later

These do **not** change in Phase 1, but list the surfaces Phase 2 will migrate first:
- `decorators.admin_required` — will internally consult `is_current_app_user_admin()` once Phase 2 adds a Supabase branch.
- `decorators.bearer_or_login_required` — will fall through to `get_current_app_user()` for the session branch only; the bearer-token branch remains unchanged to protect mobile flows (readiness review H2).
- Routes that gate on `current_user.is_admin` (e.g. `routes/main_routes.py:312, 1949, 1990, 2037`; `routes/news_routes.py:291, 352, 394`) — will switch to `is_current_app_user_admin()` once Phase 2 needs them to.
- Profile / token routes (`routes/main_routes.py:1745-1856`) — last to migrate, since they read many fields off the user row.

### How to introduce it without behaviour change in Phase 1

- Add `services/auth_resolver.py` with the three functions above.
- Implement them as pass-throughs to `flask_login.current_user`. No `flask.request` inspection, no `flask.g` writes, no Supabase imports.
- Add unit tests that assert parity with `current_user` for both authenticated and anonymous request contexts, including a test that hitting an authenticated endpoint and calling the resolver returns the same `User.id` that `current_user.id` returns.
- **Do not migrate any existing call sites.** Touching live routes is a Phase 2 activity. The shim landing alone is a no-op for the running app.
- Add a docstring / module-level comment explaining "this is currently a shim; Phase 2 will extend it" so reviewers don't try to delete it as dead code.

---

## 6. Risks and Mitigation

Phase 1 is intentionally low-risk. The risks below are mostly second-order — things to watch as the slice lands.

### R1. Migration runs on a table with surprise data
- **Risk:** an existing row with a non-null `supabase_auth_user_id` (shouldn't happen, but…) or an existing index name collision.
- **Mitigation:** the audit script is run **before** the migration. C10 (non-null `supabase_auth_user_id` rows) catches accidental pre-existing values. A unique pre-check `SELECT COUNT(*) FROM pg_indexes WHERE indexname = 'uq_user_supabase_auth_user_id'` in the migration's pre-flight is cheap insurance.

### R2. Test suite breaks under the new column
- **Risk:** SQLite (used by `TestConfig`) does not have a native UUID type; the SQLAlchemy column declaration must work under both Postgres and SQLite.
- **Mitigation:** import `from sqlalchemy.dialects.postgresql import UUID` and declare `db.Column(UUID(as_uuid=True), nullable=True)`. SQLAlchemy emits `CHAR(32)` under SQLite. Verify by running the full `pytest` suite locally on the SQLite test DB *before* opening the migration PR.

### R3. Resolver shim diverges from `current_user` semantics
- **Risk:** a subtle edge case (e.g. when `current_user` is the anonymous user vs `None`) where the resolver returns a different shape than callers expect.
- **Mitigation:** explicit unit test for the anonymous case. Public surface is three small functions; keep them strictly thin.

### R4. Duplicate / orphaned users (downstream Phase 2/3 risk)
- **Risk in Phase 1:** none directly — Phase 1 doesn't link or create anything.
- **Mitigation:** the audit script's blocking checks (C2, C4, C9, C11) ensure that Phase 2/3 cannot start while these hazards exist. Phase 2 design must require a clean audit before identity backfill.

### R5. Admin lockout (downstream Phase 2/3 risk)
- **Risk in Phase 1:** none directly.
- **Mitigation:** C7 ("unconfirmed admins") is **blocking**; surfaced and resolved before Phase 2.

### R6. Bearer-token / Supabase-JWT header collision (downstream Phase 2 risk)
- **Risk in Phase 1:** none — `decorators.bearer_or_login_required` is untouched.
- **Mitigation:** Phase 1 deliberately does not migrate this decorator to the resolver. Phase 2's design must explicitly define header precedence (readiness review H2) before touching it.

### R7. Email mismatch / case drift (downstream Phase 3 risk)
- **Risk in Phase 1:** none directly.
- **Mitigation:** C2 (case-collision emails) is **blocking** in the audit. Phase 3's backfill design must use `lower(trim(email))` matching and skip `pending_email` for initial linkage (per plan §4).

### R8. Migration drift between staging and prod
- **Risk:** Alembic migration applied in staging but not prod, or out of order with another in-flight migration.
- **Mitigation:** the migration is small and idempotent in spirit (drop+add is reversible). Apply via the established `flask db upgrade` deployment path. Ensure no other migration sits ahead of this one in an open PR before merging.

### R9. Documentation rot
- **Risk:** the new column / resolver lands but `docs/MODULE_MAP.md` and `docs/AI_CONTEXT.md` still describe the old surface.
- **Mitigation:** the docs updates are **part of this slice**, not a follow-up.

---

## 7. Recommended Implementation Order

Within Phase 1, in the order they should land:

1. **Audit script (read-only).** `scripts/auth_audit_users.py` plus a brief README entry. No schema or model change yet.
   - PR scope: one new script file, tests for output structure, a docs pointer.
   - Validation: run against the runtime DB, confirm exit code 0 (or surface and resolve any blocking hazards before continuing).

2. **Run the audit, resolve blocking hazards.** This is operational, not a code PR. Any C2/C3/C4/C7/C9/C11 blocker must be cleaned up before step 3.

3. **Schema migration: linkage column.** Alembic migration adding `user.supabase_auth_user_id` + partial unique index. (Variant A.) Model update to declare the column. No call sites use it.
   - PR scope: one migration file, model change, migration round-trip test.
   - Validation: `flask db upgrade` then `flask db downgrade` round-trip on the test DB; full `pytest` suite passes.

4. **Resolver shim.** `services/auth_resolver.py` with `get_current_app_user`, `get_current_app_user_id`, `is_current_app_user_admin`. No call-site migration.
   - PR scope: one new module, unit tests, docs pointer.
   - Validation: parity tests pass; full `pytest` suite passes; no existing route imports change.

5. **(Optional) `created_at` on `User` — Variant B only.** If the team chose Variant B in §3, ship `created_at` either in the same migration as step 3 or as a small follow-up migration. Otherwise skip.

6. **Docs sweep.** Update `docs/MODULE_MAP.md` (note `services/auth_resolver.py`), `docs/DECISIONS.md` (record the linkage-column shape and "resolver is intentionally a shim in Phase 1"), and `docs/AI_CONTEXT.md` (point at this plan).

7. **Re-run the audit script** in staging *and* production before declaring Phase 1 complete. A clean audit is part of the exit criteria for Phase 1.

Steps 1, 3, 4 are independent code PRs and can be reviewed in sequence. Steps 2 and 7 are operational. Step 6 can go in any of the code PRs or as its own small PR.

---

## 8. Validation Plan

### Tests/checks that must pass after Phase 1

- **Existing test suite:** `python -m pytest` is green. No test fixture or test file needs to change.
- **Migration round-trip:** `flask db upgrade` and `flask db downgrade` both succeed on the SQLite test DB and (in a non-prod environment) on a Supabase Postgres DB.
- **Model load:** importing `models` in a fresh Python interpreter does not raise; `User.supabase_auth_user_id` is queryable and returns `None` for every existing row.
- **Resolver parity tests** (new):
  - Anonymous request → `get_current_app_user()` returns `None`.
  - Authenticated request → `get_current_app_user()` returns the same row Flask-Login would.
  - Admin request → `is_current_app_user_admin()` is `True`.
  - Non-admin request → `is_current_app_user_admin()` is `False`.
- **Audit script tests** (new):
  - Each check produces the expected count when seeded data is shaped to trigger it (case-collision, unconfirmed admin, orphan token, etc.).
  - Exit code is `1` when any blocking hazard is present, `0` otherwise.
  - JSON format round-trips through `json.loads`.
- **Smoke checks (manual or scripted) against staging after migration applies:**
  - `/login` (legacy) succeeds with a known username/password.
  - `/admin/...` is reachable for an admin.
  - `/profile` renders, `pending_email` block still behaves correctly.
  - A `UserApiToken`-authenticated mobile endpoint (e.g. step ingest at `routes/sneakers_routes.py:1991` or `:2093`) responds 200 with a valid token, 401 with a revoked one.
  - `make_admin.py` still elevates a user.

### Evidence Phase 1 is safe to merge

- Audit script: green run against staging and production (no `[BLOCK]` rows).
- Migration: round-trip succeeds; no application logs reference the new column at INFO+ severity (i.e. it is genuinely dormant).
- Test suite: full `pytest` is green on both default config and `TestConfig`.
- Code review: no new call sites read `User.supabase_auth_user_id`. No Supabase SDK imports anywhere. No changes to `routes/`, `decorators.py`, `forms.py`, `templates/`.
- Manual smoke: legacy login, admin gate, profile page, mobile API token endpoint all behave identically pre- and post-merge (recorded in the PR description).
- Rollback rehearsal: `flask db downgrade` of the linkage migration applies cleanly in staging and the test suite remains green afterwards.

---

## 9. Exit Criteria for Phase 1

All must be true before Phase 2 work starts:

1. The migration is applied in production. `\d "user"` shows `supabase_auth_user_id uuid` and the partial unique index exists.
2. Every existing `user` row has `supabase_auth_user_id IS NULL` (sentinel; nothing has backfilled).
3. The audit script returns exit code `0` against production. All `[BLOCK]` checks pass; warnings are documented and accepted.
4. `services/auth_resolver.py` is merged and exposes the documented public surface; **no existing route or decorator has been migrated to it yet** (deliberate — that's Phase 2 work).
5. Full `pytest` suite is green.
6. Smoke checks for legacy login, admin gate, profile, and `UserApiToken` mobile endpoints have been performed in production after migration apply, with no regressions.
7. `docs/MODULE_MAP.md`, `docs/DECISIONS.md`, and `docs/AI_CONTEXT.md` reflect the Phase 1 changes.
8. The decision list in `docs/SUPABASE_AUTH_READINESS_REVIEW.md` §10 has at least the following items resolved and recorded in `docs/DECISIONS.md`:
   - Login identifier going forward (username vs email vs either).
   - `supabase_auth_user_id` column type (UUID — already implied if Phase 1 ships as designed; record it).
   - `Authorization: Bearer` header convention for Supabase JWT vs `UserApiToken` (collision strategy).
   - Password import strategy (forced reset / magic link vs hash import).
   - `is_email_confirmed` post-cutover semantics (legacy / derived / removed).
   - Admin emergency / break-glass procedure.

   Other decisions in §10 may remain open into Phase 2 but these six are the minimum to start Phase 2 design.

---

## 10. Follow-on Recommendation

Once Phase 1 is complete, **Phase 2 should focus on the read-only Supabase Auth integration skeleton**, again deliberately not changing live login behaviour:

1. Add `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET` to `config.py`. Wire into `Config` but gate any usage behind `SUPABASE_AUTH_ENABLED=false` by default.
2. Add `services/supabase_auth_service.py` with a JWT verifier (`verify_access_token(token) -> claims`) and identity-lookup helpers (`find_app_user_by_supabase_id`, `find_app_user_by_email`). Pure functions, unit-tested with fixed JWT fixtures. **No call sites yet.**
3. Extend the `services/auth_resolver.py` shim to fall back to a Supabase JWT branch *only when the feature flag is on and a JWT is present*. Default flag off. Existing behaviour unchanged.
4. Resolve the `Authorization: Bearer` header collision policy in `decorators.bearer_or_login_required` per the locked decision (readiness review H2). Add a regression test asserting that a `UserApiToken`-authenticated mobile endpoint still responds 200 with the existing token shape after the change.
5. Build the linkage service (`link_app_user_to_supabase`) and an admin-pre-linking CLI script. Validated by tests, **not yet run** against production.

Phase 2 still does not enable Supabase login for users. Phase 3 is the limited rollout (admin-first) per `docs/SUPABASE_AUTH_MIGRATION_PLAN.md` §11.

The single guiding principle through both Phase 1 and Phase 2: **keep the legacy Flask login path live and untouched until we have evidence the Supabase path works for admins under realistic conditions.**
