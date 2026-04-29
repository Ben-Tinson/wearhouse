# Supabase Auth — Phase 2 Implementation Plan

This document defines the second concrete slice of the Supabase Auth migration. It is a design/implementation plan only — no code, schema, or migrations are applied by this document.

References:
- `docs/SUPABASE_AUTH_MIGRATION_PLAN.md` — overall phased plan.
- `docs/SUPABASE_AUTH_READINESS_REVIEW.md` — pre-implementation gap analysis.
- `docs/SUPABASE_AUTH_PHASE1_IMPLEMENTATION_PLAN.md` — completed Phase 1.
- `docs/DECISIONS.md` — accepted pre-implementation decisions.

Baseline at Phase 2 start (confirmed):
- Soletrak runs on Supabase Postgres `sjwdvsefjlflgavshiyy`.
- Flask + Flask-Login + app-owned `User` is the live auth path. Login is by username.
- `user.supabase_auth_user_id` (UUID, nullable) exists with a partial unique index. Every existing row is NULL.
- `services/auth_resolver.py` is a pass-through shim with no live callers.
- `scripts/auth_audit_users.py` ran on production with **0 blocking / 0 warning**.
- `UserApiToken` (mobile/API bearer) flows are live and must keep working.
- Supabase Auth is **not** implemented; no SDK, JWT verifier, or env vars are wired in.
- Accepted decisions (per `docs/DECISIONS.md`): username login retained during dual-run; canonical link is `supabase_auth_user_id`; `UserApiToken` contract preserved; no password-hash import in first rollout; `is_email_confirmed` remains live confirmation gate; rollouts require a documented admin recovery procedure.

Phase 2 must **not** flip any user onto Supabase Auth. It introduces the server-side capability to verify a Supabase Auth identity, link existing app users to Supabase identities (admins first), and resolve a request to an app user via either Flask-Login or a Supabase JWT — all behind a feature flag that defaults off.

---

## 1. Phase 2 Objective

After Phase 2 lands:
- Soletrak's backend can **verify** a Supabase-issued JWT and resolve it to an app `User` row, but does not yet **issue** Supabase sessions or accept Supabase login for any user.
- Admins (and a small internal cohort) have `supabase_auth_user_id` populated. Every other row remains NULL.
- The resolver shim has a Supabase-JWT branch that is **off by default** and does not affect any live request unless explicitly enabled by a feature flag.
- `decorators.bearer_or_login_required` has a documented, tested precedence policy that distinguishes a `UserApiToken` from a Supabase JWT, with a regression test asserting that mobile step-sync still works against a real `UserApiToken`.
- A linkage service exists with double-link guards, admin override audit, and tests.
- `/login` and the legacy reset/email-confirmation flows are unchanged.

Why this is the safest second step:
- No user-visible auth UX change. `/login` (username + password + `is_email_confirmed` gate) is unchanged.
- No mobile/API contract change. `UserApiToken` mobile flows continue to work identically.
- A feature flag (`SUPABASE_AUTH_ENABLED`, default `false`) defends every new code path.
- The first user to actually authenticate via Supabase Auth in production is an admin who pre-linked their identity through a controlled CLI flow — not a real end-user under load.
- The full Flask-Login fallback remains intact and unchanged for the entire Phase 2 window.

---

## 2. Exact Scope of Phase 2

### In scope

1. **Supabase Auth dependency.** Add a single direct dependency, pinned: `supabase>=2.0,<3.0` (for admin-side identity creation) and use the standard `PyJWT>=2.8` (already transitively available, but pin it directly) for JWT verification on the request path. No `gotrue` direct dependency; the `supabase` SDK wraps it.
2. **Config additions.** Add `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, and `SUPABASE_AUTH_ENABLED` (boolean, default `false`) to `Config`. None of these change behaviour while `SUPABASE_AUTH_ENABLED=false`.
3. **Supabase Auth service** (`services/supabase_auth_service.py`). Pure functions:
   - `verify_access_token(token: str) -> SupabaseClaims` — verify a Supabase JWT against `SUPABASE_JWT_SECRET`, return `(supabase_user_id, email, claims_dict)` or raise a typed error.
   - `create_supabase_identity(email: str, *, email_confirmed: bool = True) -> str` — admin API call to create a Supabase Auth user; returns the new UUID. Used only by the admin pre-linking CLI.
   - `request_password_reset(email: str) -> None` — Supabase admin-side trigger; used only by the admin pre-linking flow's "send onboarding link" option.
4. **Linkage service** (`services/supabase_auth_linkage.py`). Pure ORM functions with explicit guards:
   - `link_app_user_to_supabase(app_user_id, supabase_uuid, *, by_admin=False, source: str) -> User`.
   - `find_app_user_by_supabase_id(supabase_uuid) -> Optional[User]`.
   - `find_app_user_by_email(email) -> Optional[User]` (case-normalised on `lower(trim(email))`, ignores `pending_email`).
   - Guards: never link if `app_user.supabase_auth_user_id IS NOT NULL` (unless `by_admin=True` and the override is logged); never link the same `supabase_uuid` to two different app users (the partial unique index would reject this anyway, but raise a typed error first).
5. **Resolver extension** (`services/auth_resolver.py`). Add a Supabase-JWT branch that runs only when `SUPABASE_AUTH_ENABLED=true` AND the request carries a verifiable Supabase JWT. Branch order is documented in §6. Default behaviour (flag off) is unchanged.
6. **Bearer-header collision policy** in `decorators.bearer_or_login_required`. See §6.
7. **Admin pre-linking CLI** (`scripts/link_supabase_identities.py`). Read-mostly script with explicit `--apply` flag:
   - Default: dry-run report listing each admin and what action would be taken.
   - `--apply --admins-only`: create Supabase Auth identities for admin app users that don't yet have one, link them, and (optionally, with `--send-onboarding`) trigger Supabase password-reset emails so admins can set a Supabase password.
   - Always idempotent. Logs every link to a structured audit file.
8. **Tests.** Unit tests for the JWT verifier (with fixed JWT fixtures), the linkage service (collision/double-link guards, partial-index conflict, case-insensitive email match), the resolver's Supabase branch (feature flag on/off, valid JWT, expired JWT, JWT for unlinked email, JWT for linked email). Plus a **mobile-token regression test** asserting `UserApiToken` step-sync still works under both flag states.
9. **Docs.** Update `docs/MODULE_MAP.md`, `docs/AI_CONTEXT.md`, and `docs/DECISIONS.md` (record the bearer precedence policy, JWT verifier choice, env-var names, and feature flag default).

### Out of scope (deferred to Phase 3+)

- Any change to `routes/auth_routes.py`. `/login`, `/logout`, `/register`, `/reset-password-request`, `/reset-password/<token>`, `/confirm-email/<token>`, `/confirm-new-email/<token>`, `/change-password`, `/send-change-password-link` are untouched.
- Any change to `routes/main_routes.py`'s profile/edit-profile/token-create/token-revoke routes.
- Any change to templates, forms, or test fixtures.
- `LoginForm` — still username-based.
- `is_email_confirmed` semantics — unchanged. Still a hard gate on legacy login.
- Email-change flow (`pending_email`) — unchanged. Coordination with Supabase email change is a Phase 4 concern.
- Mobile clients — no change. `UserApiToken` is the only mobile auth.
- RLS policies on `public.user` or any user-owned table.
- A user-facing Supabase login UI.
- Backfilling `supabase_auth_user_id` for non-admin users.
- `SECRET_KEY` rotation.
- Removing or deprecating `password_hash`, `pending_email`, or any itsdangerous token method.

---

## 3. Resolver / Auth Abstraction Evolution

### Phase 1 shape (current, unchanged in Phase 2 default mode)
```
get_current_app_user() -> Optional[User]
    return current_user if current_user.is_authenticated else None
```

### Phase 2 shape (active only when `SUPABASE_AUTH_ENABLED=true`)
```
get_current_app_user() -> Optional[User]:
    1. If Flask-Login session resolves to a User → return it.   # legacy primary
    2. Else if SUPABASE_AUTH_ENABLED and request carries a
       verifiable Supabase JWT:
         - verify the JWT
         - look up app user by supabase_auth_user_id
         - if found → return it
         - if not found but JWT email matches a user with NULL
           supabase_auth_user_id → return None and log a warning
           (do NOT auto-link in the resolver path; linkage is an
           explicit deliberate action, not a side-effect of a
           request).
    3. Else → return None.
```

Critical invariants:
- **Flask-Login wins when present.** During dual-run, a request that has both a Flask-Login cookie and a Supabase JWT resolves to the Flask-Login user. This protects the existing logged-in cohort and admin recovery path.
- **No auto-link in the resolver.** The resolver never writes to `user.supabase_auth_user_id`. Linkage is performed only by the explicit linkage service and the admin CLI.
- **Feature-flag gating.** When `SUPABASE_AUTH_ENABLED=false`, the resolver is byte-for-byte equivalent to its Phase 1 shape. Verified by a parity test that asserts identical behaviour with the flag off vs Phase 1 baseline.

### Why the resolver, not the decorators

Decorators (`admin_required`, `bearer_or_login_required`) keep using `flask_login.current_user` directly in Phase 2. We do **not** migrate decorator call sites to the resolver in this phase, because the resolver's Supabase branch is off in production by default and migrating decorators would create more churn than benefit. Phase 3 migrates the decorators after the resolver has run successfully behind the flag.

The resolver is wired into one new endpoint introduced in Phase 2 — the **identity probe** described in §10 — used by tests and by smoke checks in staging. It is not wired into any user-facing route.

---

## 4. Linkage / Backfill Approach for Existing Users

### Linkage rules (codified in `services/supabase_auth_linkage.py`)

- The canonical link is `user.supabase_auth_user_id` (per accepted decision in `docs/DECISIONS.md`).
- An app user with `supabase_auth_user_id IS NULL` is "unlinked"; with a UUID set, "linked".
- A Supabase identity may be linked to **at most one** app user. Enforced by the partial unique index (`uq_user_supabase_auth_user_id`).
- An app user may be linked to **at most one** Supabase identity. Enforced by single-column write semantics (`User.supabase_auth_user_id` is a scalar).
- Linkage is **explicit**. Nothing in the request path may write `supabase_auth_user_id`. Only the linkage service called from a CLI script writes it.
- Email match is performed on `lower(trim(email))`. `pending_email` is **never** used for linkage.
- Linkage of an admin requires the `by_admin=True` flag on the linkage call, and the call is recorded in a structured audit log (`backups/auth/supabase_link_audit_<timestamp>.jsonl`).

### Backfill order

Phase 2 backfills only:
1. **Internal test admins** first (1–2 users) on staging. End-to-end: log in as that admin via the staging Supabase login screen, confirm the resolver path returns the right `User`, confirm `/admin/...` is reachable.
2. **Production admins** second, one at a time. After each, confirm the admin can still log in via the legacy `/login` path (flag off path) and that the resolver returns the right `User` when hit with a synthetic Supabase JWT against the identity probe (flag on path).

Phase 2 does **not** backfill regular users. That's Phase 3.

### Forced-reset / onboarding strategy for backfilled admins

Per accepted decision, no password-hash import. Admins who get a Supabase identity created in Phase 2:
- Receive a Supabase password-reset / magic-link email (sent via the admin CLI's `--send-onboarding` option) so they can set a Supabase password independent of their existing app password.
- Continue to log in via legacy `/login` (username + app password) for routine use throughout Phase 2 — Supabase Auth is not yet enabled for any flow.
- Their first real Supabase login happens on a staging environment behind the flag, exercising the resolver path described in §10's identity probe.

---

## 5. Bearer Token Collision Handling

This is the highest-risk integration point in Phase 2 (per `docs/SUPABASE_AUTH_READINESS_REVIEW.md` H2).

### The collision

- `UserApiToken`: stored as `Authorization: Bearer <opaque>` where the opaque value is a 43-char URL-safe base64 string (`secrets.token_urlsafe(32)` → 43 chars). SHA-256 hashed in DB.
- Supabase JWT: `Authorization: Bearer <jwt>` where the JWT is three dot-separated base64url segments (header.payload.signature). Always contains two `.` characters.

### Decision (to be recorded in `docs/DECISIONS.md` at start of Phase 2)

**Format-disambiguation in `decorators.bearer_or_login_required`. No new header.**

Precedence in `bearer_or_login_required`:
1. If header `Authorization` starts with `Bearer ` and the value contains exactly two `.` characters and parses as a JWT structurally → treat as Supabase JWT.
   - If `SUPABASE_AUTH_ENABLED=false` → reject with 401 ("Supabase Auth not enabled"). This protects against accidental enablement.
   - If enabled → verify; on success, resolve the app user by `supabase_auth_user_id`. If no app user found, return 401 (do not auto-link).
2. Else if value is a non-JWT bearer string → SHA-256 hash and look up in `UserApiToken`. Behaviour is **byte-for-byte identical** to today.
3. Else → fall through to Flask-Login (`current_user`).

Rationale:
- A 43-char `UserApiToken` value contains zero `.` characters; a JWT has exactly two. Disambiguation is deterministic.
- Phase 2 keeps the JWT branch behind the feature flag, so the only possible behaviour change with the flag off is "a Supabase JWT in the header gets a clearer 401 message". `UserApiToken` requests are unaffected.
- No new header; no mobile-client change required.

### Required regression test (must ship in Phase 2)

`tests/test_bearer_or_login_required_regression.py`:
- A `UserApiToken`-authenticated request to a `@bearer_or_login_required("steps:write")` endpoint (e.g. the step ingest at `routes/sneakers_routes.py:1991`) returns 200 with the existing token shape, with `SUPABASE_AUTH_ENABLED=false`.
- Same with `SUPABASE_AUTH_ENABLED=true` (and no Supabase JWT in the header).
- A request carrying a malformed JWT in `Authorization: Bearer …` returns 401 cleanly with the flag on.
- A request carrying a malformed JWT with the flag off returns 401 with a clear "Supabase Auth not enabled" body.

The decorator's existing `last_used_at` write must continue to commit on the `UserApiToken` branch. The Supabase-JWT branch must **not** write to the database (no audit row, no `last_used_at`-style column) — pure verification only. This avoids compounding the implicit-commit footprint flagged in the readiness review (H6).

---

## 6. Admin-Safe Rollout Approach

### Pre-Phase-2 dry-run
- Re-run `scripts/auth_audit_users.py` against staging and production. Confirm exit code `0`. (Already done at end of Phase 1; confirm it has not drifted.)

### Step 1 — staging admin link (zero production impact)
- Apply the Phase 2 PRs to staging.
- On staging, set `SUPABASE_AUTH_ENABLED=true` and `SUPABASE_*` env vars.
- Run `python scripts/link_supabase_identities.py --admins-only` (dry-run). Verify the report.
- Run `python scripts/link_supabase_identities.py --admins-only --apply --send-onboarding`. Verify the audit log.
- For each linked admin: hit the identity probe endpoint with their Supabase JWT, confirm the resolver returns the right `User.id` and `is_admin=True`.
- Hit `/login` (legacy) with the same admin's username/password. Confirm Flask-Login still works.
- Hit a `UserApiToken`-protected endpoint with a fresh staging token. Confirm 200.

### Step 2 — production admin link (limited cohort)
- Apply Phase 2 PRs to production. **Keep `SUPABASE_AUTH_ENABLED=false` in production.**
- Run `python scripts/link_supabase_identities.py --admins-only --apply --send-onboarding` against production. This creates Supabase identities and emails admins the Supabase setup link. **Crucially, the resolver's Supabase branch is still off, so this has zero effect on production request handling.** The only production state change is rows in the Supabase Auth identity store and the population of `user.supabase_auth_user_id` for admins.
- Each admin sets their Supabase password via the emailed link (Supabase-side only, not Soletrak-side).
- Each admin continues to log in to Soletrak via legacy `/login` exactly as before.

### Step 3 — production identity probe behind the flag (admin-only, no UI)
- Open a temporary admin-only `/admin/auth/probe` endpoint (under `@admin_required`) that takes a Supabase JWT in the body, verifies it via the resolver, and returns `{user_id, is_admin, supabase_user_id}`. Endpoint is wrapped in `if app.config.get("SUPABASE_AUTH_ENABLED")`.
- Set `SUPABASE_AUTH_ENABLED=true` in production for **15 minutes**, exercise the probe with each linked admin's Supabase JWT, then set it back to `false`. Confirm zero impact on logs/errors during the window.

### Step 4 — leave production at `SUPABASE_AUTH_ENABLED=false` until Phase 3 begins
- The flag remains off in production for the rest of Phase 2.
- Staging stays on with the flag enabled for Phase 3 design work.

### Admin recovery path (per accepted decision)
- Legacy `/login` is the documented break-glass admin recovery path throughout Phase 2.
- If a Supabase identity is mis-linked, an admin runs `python scripts/link_supabase_identities.py --unlink --user-id <id>` (Phase 2 ships this command too). This sets `supabase_auth_user_id = NULL` for that user. Audit-logged.
- `make_admin.py` is unchanged and remains the authoritative admin-flag tool.

---

## 7. Required Env / Config Additions

To `config.py` (`Config` class), all reading from environment variables:

| Variable | Purpose | Default | Required for production? |
|---|---|---|---|
| `SUPABASE_URL` | Supabase project URL (e.g. `https://sjwdvsefjlflgavshiyy.supabase.co`) | None | Yes |
| `SUPABASE_ANON_KEY` | Public anon key for client-side + JWT verification fallback | None | Yes |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-only admin key. Used only by the admin CLI for identity creation. | None | Yes (CLI host only) |
| `SUPABASE_JWT_SECRET` | The HS256 signing secret for verifying Supabase access tokens | None | Yes |
| `SUPABASE_AUTH_ENABLED` | Master feature flag (`true` / `false`) | `false` | No (default off) |

Notes:
- `SUPABASE_SERVICE_ROLE_KEY` is only consulted by the admin CLI script. It is **not** read at app boot time; it is read only when the script imports the linkage service. This keeps web hosts free of admin credentials.
- `SUPABASE_JWT_SECRET` is read only when `SUPABASE_AUTH_ENABLED=true`. With the flag off, a missing secret does not block app boot.
- Document staging vs production redirect URLs in a deployment doc, not in code.

---

## 8. Backend Integration Points Likely to Change

These are the places code lands or is touched in Phase 2. Live behaviour is not changed unless the flag is on.

| File | Change | Live behaviour change with flag off? |
|---|---|---|
| `config.py` | Add 5 env vars to `Config`. | None — values default safely. |
| `requirements.txt` | Add `supabase>=2.0,<3.0` and pin `PyJWT>=2.8`. | None. |
| `services/supabase_auth_service.py` (new) | JWT verifier + admin SDK helpers. | None — not imported by live routes. |
| `services/supabase_auth_linkage.py` (new) | Linkage helpers + audit logging. | None — not imported by live routes. |
| `services/auth_resolver.py` | Extend with Supabase-JWT branch. | None when `SUPABASE_AUTH_ENABLED=false` (parity test). |
| `decorators.py::bearer_or_login_required` | Add format-disambiguation header inspection + JWT branch behind flag. | None — `UserApiToken` and `current_user` paths byte-for-byte preserved. |
| `routes/main_routes.py` (new admin endpoint only) | Add `/admin/auth/probe` behind flag and `@admin_required`. | None — endpoint returns 404 (or the original 403/404 behaviour) when flag is off. |
| `scripts/link_supabase_identities.py` (new) | Admin pre-linking CLI. | None — runs out-of-band. |
| `scripts/auth_audit_users.py` | No change. | None. |
| `models.py` | No change. | None. |
| Migrations | No new Alembic migration in Phase 2. | N/A. |
| Tests | New unit + regression tests. | None. |
| Templates / forms | No change. | None. |
| `routes/auth_routes.py`, profile routes, `/login`, `/logout`, password reset, email confirmation, email change | No change. | None. |

The existing Flask-Login session lifecycle is **untouched**.

---

## 9. Validation Plan

### Tests that must pass after Phase 2

- Full `pytest` suite (currently 241 tests) → still 241+ pass with the new tests added.
- `tests/test_supabase_auth_service.py` (new) — JWT verifier with fixed fixtures: valid token, expired token, wrong-secret token, malformed token.
- `tests/test_supabase_auth_linkage.py` (new) — link / find-by-id / find-by-email / case-insensitive email match / double-link guard / partial-index conflict / `pending_email` ignored.
- `tests/test_auth_resolver_phase2.py` (new) — feature-flag-off parity (must equal Phase 1 behaviour exactly), feature-flag-on with valid JWT for a linked user, with valid JWT for an unlinked email (must return None and log warning, NOT auto-link).
- `tests/test_bearer_or_login_required_regression.py` (new) — `UserApiToken` request path identical pre- and post-Phase-2 with flag off; identical with flag on but no JWT; clean 401 for malformed JWT either way.
- `scripts/auth_audit_users.py` continues to return exit code 0 against production after admin pre-linking (it now reports admin rows with non-null `supabase_auth_user_id` under C10's "rows present" output, but as **info / counted progress**, not blocking — see §11 for the C10 severity update).
- `flask db current` returns `b3c4d5e6f7a8` (the Phase 1 head; Phase 2 ships no new migration).

### Audit-script update for Phase 2

`scripts/auth_audit_users.py::check_c10_unexpected_supabase_links` must be updated to **not block** on rows with non-null `supabase_auth_user_id` once Phase 2 begins backfilling admins. Two acceptable approaches:
- **Recommended:** change C10's severity from `blocking` to `info` and report counts. The Phase 1 sentinel role is over.
- Alternative: take an explicit `--phase 2` CLI flag that downgrades C10 to info.

This is the only Phase 1 deliverable that needs to evolve in Phase 2. Documented as an explicit small change in the Phase 2 PR set.

### Smoke checks (manual or scripted) against staging after each PR merges

- `/login` (legacy) succeeds with a known admin username/password. Same response time and same flash messages.
- `/admin/...` is reachable for the admin.
- `/profile` renders unchanged. `pending_email` block still behaves correctly.
- A `UserApiToken`-authenticated step-ingest request (`routes/sneakers_routes.py:1991`) responds 200 with a valid token and 401 with a revoked one. Both with `SUPABASE_AUTH_ENABLED=false` and `=true`.
- The new `/admin/auth/probe` endpoint returns 404 (or the same as before) when the flag is off; returns the expected JSON when the flag is on and an admin's Supabase JWT is supplied.

### Evidence Phase 2 is safe to merge

- All tests green.
- No changes to `routes/auth_routes.py`, `forms.py`, `templates/`, or any user-facing route except the admin-only probe.
- `git diff` review shows `decorators.bearer_or_login_required` only adds the JWT branch behind the flag; the `UserApiToken` and Flask-Login branches are byte-for-byte unchanged (or trivially refactored with passing tests).
- Staging exercise: a linked admin's Supabase JWT resolves to the correct `User.id` via the probe; the same admin still logs in via legacy `/login`; mobile token sync continues to work.
- Production exercise (Step 2 of §6): `link_supabase_identities.py --admins-only --apply` runs cleanly against production with `SUPABASE_AUTH_ENABLED=false`; `supabase_auth_user_id` populated only for admins; legacy login unchanged.
- A 15-minute production probe window (Step 3 of §6) shows zero new errors in logs.

---

## 10. The Identity Probe (Phase 2's Only New Endpoint)

Single new route `routes/main_routes.py::admin_auth_probe`:
```
GET /admin/auth/probe
@login_required
@admin_required
- 404 if not app.config.get("SUPABASE_AUTH_ENABLED")
- accepts a Supabase access token in the Authorization header
- calls services.auth_resolver.get_current_app_user() (with the JWT
  branch active because flag is on and JWT is in the header)
- returns JSON: {"user_id": ..., "supabase_user_id": ...,
                 "is_admin": ..., "via": "supabase" | "flask_login"}
```

This is the only place where the Phase 2 resolver's Supabase branch is exercised in production. It is admin-only and gated by `@login_required` (so an admin must already be Flask-Login-authenticated to even hit it). The endpoint is **read-only**: it never mutates any row, never logs the user in via Flask-Login, never writes `supabase_auth_user_id`.

---

## 11. Rollback Plan

Phase 2 introduces no schema changes and no live behaviour change with the flag off. Rollback is therefore mostly a flag flip plus a code revert.

### Flag-only rollback (preferred for any incident with the resolver/JWT branch)
- Set `SUPABASE_AUTH_ENABLED=false` in production. Restart workers if needed.
- All resolver behaviour reverts to Phase 1 semantics. `UserApiToken` and Flask-Login paths are unaffected.
- The `supabase_auth_user_id` values on admin rows remain in place; they have no live consumers while the flag is off.

### Code revert
- Revert the Phase 2 PRs in standard reverse-merge order.
- No migration to undo.
- The admin pre-linking script's effects (Supabase identities created, `supabase_auth_user_id` populated for admins) **persist** across a code revert — they are durable data, not code state. This is intentional: rolling back code does not undo identity creation, so the next forward-roll re-uses the same identities.

### Unlink-specific recovery
- To unlink a single admin row: `python scripts/link_supabase_identities.py --unlink --user-id <id>`. Audited.
- To remove a Supabase Auth identity: do this from the Supabase dashboard or the admin SDK, separately. Soletrak's code does not delete Supabase identities.

### What we do **not** do as part of rollback
- Do not delete `password_hash`, `is_email_confirmed`, `pending_email`, or any itsdangerous token method from `User`. They remain the legacy fallback.
- Do not rotate `SECRET_KEY`. Per accepted decision, it stays stable through the migration window.
- Do not drop the `supabase_auth_user_id` column. The Phase 1 migration stays applied.

---

## 12. Exit Criteria for Moving to Phase 3

All must be true before Phase 3 work starts.

1. The Phase 2 PRs are merged to production. `SUPABASE_AUTH_ENABLED=false` is the steady-state production setting.
2. Every admin row has `supabase_auth_user_id` populated (verified by a SQL `SELECT count(*) FROM "user" WHERE is_admin=true AND supabase_auth_user_id IS NULL` returning 0).
3. Every linked admin has set their Supabase password / completed the onboarding email flow (verified by Supabase Auth dashboard or admin API). At minimum **two** admins must be confirmed onboarded so we have a recovery path that does not depend on a single individual.
4. The 15-minute production identity-probe exercise (Step 3 of §6) ran cleanly — no new errors, every linked admin's Supabase JWT resolved to the right `User.id`.
5. `auth_audit_users.py` exit code remains `0` (with C10 downgraded to `info` per §9).
6. The mobile-token regression test (`tests/test_bearer_or_login_required_regression.py`) is green and has been exercised against a real `UserApiToken` in staging.
7. Full `pytest` suite green; `flask db current` returns `b3c4d5e6f7a8`.
8. No regressions reported in mobile step-sync, admin actions, profile, or login during the 15-minute production probe window or in the 48 hours that follow.
9. Phase 3 design doc (`docs/SUPABASE_AUTH_PHASE3_IMPLEMENTATION_PLAN.md`) is drafted, covering: cohort backfill order, on-login progressive linkage rules, the bridge endpoint that turns a Supabase JWT into a Flask-Login session for browser users, password-reset / email-confirmation route deprecation strategy, and the explicit decision on whether to add `Supabase login` to the public `/login` page.
10. Decisions still open from `docs/SUPABASE_AUTH_READINESS_REVIEW.md` §10 that Phase 3 needs are recorded in `docs/DECISIONS.md`: long-term `is_email_confirmed` semantics, email-change flow ownership, mobile-token long-term strategy, rollback duration for legacy `/login`.

---

## Appendix: Phase 2 PR breakdown (recommended)

PRs are ordered to land independently. Each is reversible.

1. **PR 1 — Config + dependency**: add `SUPABASE_*` env vars to `Config`; pin `supabase` and `PyJWT` in `requirements.txt`. No new code.
2. **PR 2 — JWT verifier service**: `services/supabase_auth_service.py` with `verify_access_token`. Tests with fixed JWT fixtures. No live consumers.
3. **PR 3 — Linkage service**: `services/supabase_auth_linkage.py` + tests. No live consumers.
4. **PR 4 — Resolver extension**: `services/auth_resolver.py` Supabase branch behind flag + parity tests. No live consumers.
5. **PR 5 — Decorator bearer-collision policy**: `decorators.bearer_or_login_required` format-disambiguation + regression tests. **High-risk PR; review carefully.** Mobile tokens must be byte-for-byte preserved.
6. **PR 6 — Admin pre-linking CLI**: `scripts/link_supabase_identities.py` + tests against a stub Supabase admin client.
7. **PR 7 — Identity probe endpoint**: `/admin/auth/probe` behind flag + `@admin_required`. Tests with a flag-on test client.
8. **PR 8 — Audit script C10 severity downgrade**: small targeted change to `scripts/auth_audit_users.py`.
9. **PR 9 — Doc updates**: `docs/MODULE_MAP.md`, `docs/AI_CONTEXT.md`, `docs/DECISIONS.md` + draft of `docs/SUPABASE_AUTH_PHASE3_IMPLEMENTATION_PLAN.md`.

PRs 1–4 and 8 are low-risk and can land in any order. PR 5 is the most sensitive and should land alone with extra reviewers. PR 6 is operational. PR 7 ships last because it depends on PRs 2 and 4.
