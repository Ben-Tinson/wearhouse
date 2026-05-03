# Supabase Auth — Phase 3a Execution Plan

This document is the **implementation sequencing** plan for Phase 3a of the Supabase Auth migration. Phase 3a launches Supabase-first sign-up and sign-in for **brand-new users only**. Existing users, legacy login, mobile/API tokens, admin gating, and every existing route remain operationally unchanged.

References:
- `docs/SUPABASE_AUTH_PHASE3_IMPLEMENTATION_PLAN.md` — overall Phase 3 design.
- `docs/DECISIONS.md` — accepted Phase 3 decisions (the ten dated 2026-05-02).
- `docs/SUPABASE_AUTH_PHASE2_PROBE_REHEARSAL_OUTCOME_2026-04-30.md` — proven Phase 2 baseline.

Baseline at Phase 3a start (confirmed):
- Phase 2 capability shipped end-to-end. `services/supabase_auth_service.py` verifies HS256 + ES256 + RS256 (JWKS). `services/supabase_auth_linkage.py` is the only sanctioned writer of `user.supabase_auth_user_id`. `services/auth_resolver.py` and `decorators.bearer_or_login_required` consult the verifier behind `SUPABASE_AUTH_ENABLED`.
- `/admin/auth/probe` validated end-to-end against staging.
- `routes/auth_routes.py`, `forms.py`, all `templates/*.html` are unchanged. `LoginForm` is still username + password.
- `UserApiToken` mobile/API contract is unchanged.
- Production steady state: `SUPABASE_AUTH_ENABLED=false` (flipped on only in time-boxed probe windows). **Phase 3a transitions this flag to "durable on" in production.**

---

## 1. Exact Phase 3a Scope

### What Phase 3a delivers
- **Brand-new users** can sign up and sign in via Supabase Auth using:
  - Email + password (Supabase-managed verification), AND
  - Google SSO.
- A new `/signup` page replaces `/register` for the launch UX. Legacy `/register` redirects to `/signup` when the Phase 3a feature flag is on.
- A bridge endpoint `/auth/supabase/bridge` accepts a verified Supabase access token, creates the app `User` row (with `password_hash IS NULL`, `is_email_confirmed=True` mirrored from Supabase, `auth_provider` set, `supabase_auth_user_id` populated), and issues a normal Flask-Login session via `login_user(app_user)`.
- Onboarding step (`/auth/supabase/onboarding`) collects the Soletrak profile fields the Supabase JWT does not carry: `username`, `first_name`, `last_name`, `preferred_region`, `marketing_opt_in`. Username remains required (per accepted decision).
- Schema additions to support the above:
  - `User.password_hash` becomes nullable.
  - `User.last_login_at` (DateTime, nullable) — written by the bridge and by legacy login.
  - `User.auth_provider` (String(40), nullable) — values `legacy`, `supabase_email`, `supabase_oauth_google`.
- A defensive guard in `User.check_password` returning `False` when `password_hash IS NULL`.
- Production environment configured for `SUPABASE_AUTH_ENABLED=true` as the durable steady state (flipped on once Phase 3a code lands; not flipped off afterwards).

### Phase 3a is complete when
- A real new user can sign up with email + password and land authenticated on `/dashboard`.
- A real new user can sign up with Google SSO and land authenticated on `/dashboard`.
- The same user can log out and back in via either method.
- All existing users continue to sign in via legacy `/login` with no observable change in behaviour.
- Mobile `UserApiToken` step sync continues to work with no change.
- `make_admin.py` continues to elevate users; admin pages continue to work for legacy admins.
- Audit JSONL files under `backups/auth/` capture each Supabase-first signup as `action="link"` with `source="bridge_signup"`.

---

## 2. Out of Scope (Phase 3a)

Explicitly **not** in Phase 3a; deferred to later sub-phases or later phases:

- **Existing-user linking.** A logged-out existing user landing on `/login` sees no Supabase entry points. The bridge refuses to operate on an existing legacy user row (returns a typed error that the front-end translates into "you already have a Soletrak account; please sign in with your existing username", with no follow-on linkage UI yet). Linkage is Phase 3b.
- **Apple SSO** and any non-Google provider. Phase 3a launch surface is `SUPABASE_SSO_PROVIDERS=google`. Apple is a follow-up.
- **Magic link** sign-in. Considered for Phase 3b+ if the design calls for it.
- **`/login` UI changes.** The legacy login form is untouched. No "Sign in with Google" button on `/login` in 3a.
- **Legacy `/reset-password-request` or `/confirm-new-email/<token>` guards.** Those are needed for linked users (Phase 3b). In 3a, no user is in the linked-existing state, so the guards are not required.
- **Soft `/dashboard` migration nudge** for legacy users.
- **Cohort backfill CLI extensions.** The Phase 2 CLI is unchanged.
- **Removal of `password_hash`, `is_email_confirmed`, `pending_email`, or itsdangerous tokens.**
- **Mobile / `UserApiToken` migration to Supabase tokens.**
- **`LoginForm` changes** — `forms.py::LoginForm` stays username + password.
- **RLS on `public.user`** or any user-owned table.
- **Sunset of legacy `/login`.** `LEGACY_LOGIN_ENABLED` defaults to `true` and is not flipped off in Phase 3a.

---

## 3. Recommended PR Breakdown (Implementation Order)

Six PRs, ordered to land independently. Each is rollback-able by feature flag (PRs 3–5) or by code revert (PRs 1, 2, 6). PR 1 is the only one with a one-way-door consideration (see §8).

| # | PR | Risk | Visible to end users? |
|---|---|---|---|
| 1 | Schema migration + model updates | medium (one-way after first Supabase-first signup) | no |
| 2 | Session bridge service (`services/supabase_session_bridge.py`) | low | no |
| 3 | Supabase Auth blueprint + bridge endpoint | low (behind flag, default off) | no while flag off |
| 4 | Signup UI + Supabase JS client glue + onboarding template | low (behind flag, default off) | no while flag off |
| 5 | `/register` redirect + Supabase Auth durable on-state | medium (flag flip moment) | yes once flag on |
| 6 | Doc updates (`MODULE_MAP.md`, `AI_CONTEXT.md`, `DECISIONS.md`) | none | no |

**PRs 1–4 can land in any order relative to each other** because they are inert without PR 5's flag flip. PR 5 must land last.

---

## 4. Exact Files Likely to Change per PR

### PR 1 — Schema migration + model updates
**New:**
- `migrations/versions/<rev>_phase3a_user_columns.py` — Alembic migration:
  - `ALTER TABLE "user" ALTER COLUMN password_hash DROP NOT NULL` (Postgres metadata-only on 12+; SQLite via `batch_alter_table`).
  - `ADD COLUMN last_login_at TIMESTAMP NULL`.
  - `ADD COLUMN auth_provider VARCHAR(40) NULL`.
  - `downgrade()` reverses all three (with a guard / comment that the `password_hash` re-tightening will fail if any row has NULL).
- `tests/test_supabase_auth_phase3a_migration.py` — round-trip test against a disposable SQLite DB (mirrors `tests/test_supabase_auth_migration.py` from Phase 1).

**Modified:**
- `models.py`:
  - `password_hash = db.Column(db.String(256), nullable=True)` — was `nullable=False`.
  - Two new columns on `User`: `last_login_at = db.Column(db.DateTime, nullable=True)` and `auth_provider = db.Column(db.String(40), nullable=True)`.
  - Defensive guard in `User.check_password`: return `False` immediately when `self.password_hash is None`. Single line addition.
- `tests/conftest.py` — no change required (existing fixtures all set `password_hash` via `set_password()`).

### PR 2 — Session bridge service
**New:**
- `services/supabase_session_bridge.py`:
  - `bridge_supabase_identity(access_token, profile_payload, *, login_callback=login_user)` — returns `BridgeOutcome` dataclass `{app_user, was_created: bool, source: str}`.
  - Typed errors: `BridgeError` (base), `BridgeFlagDisabled`, `BridgeTokenInvalid`, `ExistingLegacyUserConflict(app_user_id)`, `BridgeProfileInvalid(field_errors)`.
  - All linkage writes go through `services.supabase_auth_linkage.link_app_user_to_supabase` with `source="bridge_signup"`.
  - All audit-log writes go to the same `backups/auth/supabase_link_audit_<timestamp>.jsonl` shape used by the linkage CLI.
  - Wraps the verify → find → create → link → login sequence in a single DB transaction.
- `tests/test_supabase_session_bridge.py` — unit tests with monkey-patched `verify_access_token` and a fake `login_callback`. Covers: happy path new user, returning user (already linked), email-match-existing-user (raises `ExistingLegacyUserConflict`), invalid token (raises `BridgeTokenInvalid`), missing required profile field (raises `BridgeProfileInvalid`), atomic rollback on failure between create and link.

**Modified:**
- None.

### PR 3 — Supabase Auth blueprint + bridge endpoint
**New:**
- `routes/supabase_auth_routes.py`:
  - Blueprint `supabase_auth_bp = Blueprint('supabase_auth', __name__, url_prefix='/auth/supabase')`.
  - `POST /bridge` — calls the bridge service; returns JSON response codes (200 / 400 / 401 / 409 for `ExistingLegacyUserConflict`).
  - All routes return 404 when `SUPABASE_NEW_USER_SIGNUP_ENABLED` is False.
  - No DB writes outside the bridge service call.
- `tests/test_supabase_auth_routes_bridge.py` — covers: 404 when flag off; 200 happy path; 400 on profile validation failure; 401 on invalid token; 409 on email match against an existing legacy user.

**Modified:**
- `app.py` — register the new blueprint (one line, alongside the existing `app.register_blueprint(...)` calls).
- `config.py` — add Phase 3a flags:
  - `SUPABASE_NEW_USER_SIGNUP_ENABLED` — bool, default `False`.
  - `SUPABASE_SSO_PROVIDERS` — string, default `"google"`.
  - `SUPABASE_BRIDGE_REDIRECT_URL` — string, default `None` (required when flag on; the route raises a clear startup error if missing).

### PR 4 — Signup UI + Supabase JS client glue + onboarding
**New:**
- `templates/auth/supabase_signup.html` — the `/signup` page. Loads Supabase JS SDK from CDN (`https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/dist/umd/supabase.min.js`); renders email/password form + Google button (provider list driven by `SUPABASE_SSO_PROVIDERS`). On successful Supabase signup, calls the bridge POST.
- `templates/auth/supabase_oauth_onboarding.html` — post-OAuth profile-completion form (username, first/last name, region, marketing opt-in). Email is pre-filled from the JWT and read-only.
- `templates/auth/supabase_confirm.html` — the destination of the email confirmation link; thin client that extracts the access token from the URL fragment and POSTs to the bridge.
- `static/js/supabase_auth_client.js` — small browser-side glue: extract token from URL fragment, POST to `/auth/supabase/bridge` with profile payload, redirect to `/dashboard` on 200, show error on 4xx.
- `tests/test_supabase_auth_routes_ui.py` — covers: `/signup` returns 404 when flag off, 200 when flag on; renders the configured providers; `/auth/supabase/onboarding` returns 200 when flag on with the expected form fields.

**Modified:**
- `routes/supabase_auth_routes.py` — extend with the HTML routes:
  - `GET /signup` — renders `supabase_signup.html`.
  - `GET /oauth-callback` — renders the JS handler that decides whether to skip onboarding (returning user) or redirect to onboarding (new user).
  - `GET /confirm` — renders `supabase_confirm.html` for the email-confirmation landing.
  - `GET /onboarding` — renders `supabase_oauth_onboarding.html`.
  - All HTML routes 404 when flag off.

### PR 5 — `/register` redirect + production flag flip
**Modified:**
- `routes/auth_routes.py::register` — add a one-line guard at the top:
  ```python
  if current_app.config.get("SUPABASE_NEW_USER_SIGNUP_ENABLED"):
      return redirect(url_for("supabase_auth.supabase_signup"))
  ```
- `tests/test_auth.py` — extend with a redirect-when-flag-on test (existing tests continue to pass with flag off).

**Operational** (not a code change, but part of this PR's deploy):
- Production environment variables:
  - `SUPABASE_AUTH_ENABLED=true` (transitions from time-boxed-on to durable-on).
  - `SUPABASE_NEW_USER_SIGNUP_ENABLED=true` (the PR 3a launch flag flip).
  - `SUPABASE_SSO_PROVIDERS=google`.
  - `SUPABASE_BRIDGE_REDIRECT_URL=<APP_BASE_URL>/auth/supabase/oauth-callback`.
- Supabase dashboard configuration:
  - Email auth provider enabled with email-confirmation required.
  - Google OAuth provider configured with the production redirect URL.
  - Email templates (confirmation, password reset) styled and tested.
  - Site URL set to `APP_BASE_URL`.

### PR 6 — Doc updates
**Modified:**
- `docs/MODULE_MAP.md` — pointers for `services/supabase_session_bridge.py`, `routes/supabase_auth_routes.py`, the new templates, and the JS client glue.
- `docs/AI_CONTEXT.md` — Phase 3a status note.
- `docs/DECISIONS.md` — new dated status block recording Phase 3a launch.

---

## 5. Schema Changes Required

**One Alembic migration, three changes, in PR 1.**

| Change | Postgres impact | SQLite impact |
|---|---|---|
| `user.password_hash` → nullable | metadata-only on 12+ | `batch_alter_table` rebuild (already supported by `render_as_batch=True` in `app.py`) |
| `user.last_login_at` (TIMESTAMP NULL) | column add — fast | column add via batch |
| `user.auth_provider` (VARCHAR(40) NULL) | column add — fast | column add via batch |

No data is rewritten. All three changes land NULL for existing rows.

`downgrade()` is best-effort: dropping the columns is straightforward; re-tightening `password_hash` to NOT NULL will fail if any row has NULL. The migration's `downgrade()` function includes a comment that this is a one-way change once any Supabase-first user exists. Operators should treat the schema migration as a one-way door 24h after `SUPABASE_NEW_USER_SIGNUP_ENABLED` is flipped on in production (see §8 rollback).

---

## 6. Flags Required and Default Values

Phase 3a introduces three new flags and changes the operational role of one existing flag.

| Flag | Type | Default in code | Production value at end of Phase 3a |
|---|---|---|---|
| `SUPABASE_AUTH_ENABLED` | bool | `false` | **`true` (durable on)** — transitions from "kill switch behind probe windows" to "always on" |
| `SUPABASE_NEW_USER_SIGNUP_ENABLED` | bool | `false` | `true` after PR 5 deploys |
| `SUPABASE_SSO_PROVIDERS` | string (csv) | `"google"` | `"google"` |
| `SUPABASE_BRIDGE_REDIRECT_URL` | string | `None` | `<APP_BASE_URL>/auth/supabase/oauth-callback` |

Flags **not** introduced in Phase 3a (deferred to 3b/3c/3d):
- `SUPABASE_EXISTING_USER_LINK_ENABLED` (Phase 3b).
- `LEGACY_LOGIN_ENABLED` / `LEGACY_LOGIN_DEPRECATED` (Phase 3d).

The operational rule from `docs/DECISIONS.md` for `SUPABASE_AUTH_ENABLED` is updated by Phase 3a: starting at PR 5 deploy, the flag is **expected** to be `true` in production (no longer a flag kept off-by-default). Other tracked-config rules still hold — the value is set in the deployment platform's secret store, not in any version-controlled file.

---

## 7. Acceptance Criteria per PR

### PR 1 — Schema
- `flask db upgrade` then `flask db downgrade -1` succeed cleanly on the SQLite test DB. Migration round-trip test passes.
- `models.User` exposes `last_login_at` and `auth_provider`. `User.password_hash` is nullable; `User.check_password` returns `False` for a row whose `password_hash IS NULL`.
- Full `pytest` is green. No existing test broken; one new test in `tests/test_supabase_auth_phase3a_migration.py`.
- `flask db current` returns the new revision.

### PR 2 — Bridge service
- `bridge_supabase_identity` happy path: verifies a token, creates a `User` row with the expected field values, calls the supplied `login_callback` with the new app user, returns `BridgeOutcome(was_created=True, source="bridge_signup")`.
- Returning-user path: if `find_app_user_by_supabase_id` finds a row, the bridge does not re-create or re-link; updates `last_login_at`; returns `BridgeOutcome(was_created=False)`.
- Email-match-existing-user path: raises `ExistingLegacyUserConflict(app_user_id)` without writing anything.
- Token failure: raises `BridgeTokenInvalid`; nothing committed.
- Profile validation failure: raises `BridgeProfileInvalid(field_errors)`; nothing committed.
- Atomicity: when `link_app_user_to_supabase` raises mid-transaction, the partial `User` row is rolled back and not visible after the exception.
- Audit JSONL is written for `link` actions; suppressed on errors that occur before the link write.
- Full `pytest` green; new tests in `tests/test_supabase_session_bridge.py`.

### PR 3 — Bridge endpoint
- `POST /auth/supabase/bridge` returns 404 when `SUPABASE_NEW_USER_SIGNUP_ENABLED=false`.
- 200 with body `{ ok: true, user_id, was_created, redirect: "/dashboard" }` on happy path.
- 400 with `{ ok: false, error: "<message>", field_errors: {...} }` on profile validation failure.
- 401 with `{ ok: false, error: "<verifier message>" }` on invalid token.
- 409 with `{ ok: false, error: "existing_legacy_account", existing_user_id_hint: <id> }` on email match.
- A successful 200 leaves a Flask-Login session cookie on the response.
- A 4xx response leaves no session cookie and no DB row.
- Full `pytest` green.

### PR 4 — Signup UI
- `GET /auth/supabase/signup` returns 404 with flag off, 200 with flag on.
- The signup page renders an email/password form and a Google button when `SUPABASE_SSO_PROVIDERS=google`.
- The signup page renders ONLY email/password (no SSO buttons) when `SUPABASE_SSO_PROVIDERS` is empty.
- `GET /auth/supabase/onboarding`, `GET /auth/supabase/confirm`, `GET /auth/supabase/oauth-callback` all 404 with flag off and 200 with flag on.
- The onboarding form requires `username`, `first_name`, `last_name`, `preferred_region`, `marketing_opt_in`. Username uniqueness is validated against `User.query.filter_by(username=...)`.
- Full `pytest` green.

### PR 5 — `/register` redirect + flag flip
- With `SUPABASE_NEW_USER_SIGNUP_ENABLED=false`: `GET /register` renders the legacy `register.html` (no behaviour change).
- With `SUPABASE_NEW_USER_SIGNUP_ENABLED=true`: `GET /register` redirects (302) to `/auth/supabase/signup`.
- Legacy `/login` continues to render the existing `LoginForm` regardless of flag state.
- Full `pytest` green.
- After production flag flip: a real new email/password signup completes end-to-end into a Flask-Login session.
- After production flag flip: a real new Google SSO signup completes end-to-end into a Flask-Login session.
- After production flag flip: an existing user signing in via legacy `/login` sees no behaviour change (response time, flash messages, redirect target all identical).
- Mobile `UserApiToken`-authenticated step ingest continues to respond 200 with a valid token.

### PR 6 — Docs
- `docs/MODULE_MAP.md`, `docs/AI_CONTEXT.md`, `docs/DECISIONS.md` reflect the Phase 3a state.
- `docs/DECISIONS.md` carries a new dated status block recording Phase 3a launch.

---

## 8. Rollback Plan per PR

### PR 1 — Schema
- **Pre-flag-flip** (no Supabase-first user exists yet): `flask db downgrade` cleanly reverses the migration.
- **Post-flag-flip** (one or more Supabase-first user exists with `password_hash IS NULL`): the migration is **one-way**. Re-tightening `password_hash` to NOT NULL would fail. Recovery options:
  - Roll forward: keep the schema, fix the issue in code.
  - Hard rollback: backfill `password_hash` for Supabase-first users with a sentinel value (or delete those rows) before downgrading. Requires manual operator action and is treated as an incident, not a routine rollback.
- **Mitigation:** keep at least 24h between PR 1 deploy and PR 5 flag flip in production. During that window the schema is downgrade-safe.

### PR 2 — Bridge service
- Pure code module; no live consumers until PR 3 calls it. `git revert` is safe at any time.

### PR 3 — Bridge endpoint
- The endpoint returns 404 with `SUPABASE_NEW_USER_SIGNUP_ENABLED=false`. In any incident, set the flag to `false` and the endpoint disappears. Then `git revert` if needed. No DB damage to undo.

### PR 4 — Signup UI
- All UI routes 404 with the flag off. Same rollback pattern as PR 3.

### PR 5 — `/register` redirect + flag flip
- **Code rollback**: revert the one-line guard in `auth_routes.register`. `/register` reverts to legacy.
- **Operational rollback** (preferred for any incident): set `SUPABASE_NEW_USER_SIGNUP_ENABLED=false`. Effects:
  - `/register` stops redirecting (legacy form renders).
  - `/signup`, bridge endpoint, onboarding all 404.
  - Existing Supabase-first user accounts continue to exist; their next sign-in via legacy `/login` will fail (no `password_hash`), but they could be sent through Supabase password reset to set a Soletrak password if desperately needed. Realistically: the rollback recovery path is "flag back on" rather than "force Supabase-first users into legacy".
  - Existing legacy users are unaffected.
- **Worst case** (bridge endpoint genuinely broken): also `git revert` PR 3, leaving PR 1 and PR 2 in place. The schema and bridge service remain installed but unreachable.

### PR 6 — Docs
- `git revert` is always safe for doc-only changes.

---

## 9. Staging Test Plan

### Stage 1 — pre-flight
- Confirm `auth_audit_users.py` returns exit code 0 against the staging DB (Phase 2 baseline).
- Confirm staging admins are linked (Phase 2 outcome doc).
- Confirm staging Supabase project has email auth + Google OAuth enabled in the dashboard, with the staging redirect URL configured.

### Stage 2 — apply schema (PR 1 in staging)
- `flask db upgrade` against staging Postgres.
- Verify columns exist (`\d "user"` shows `password_hash` as nullable, `last_login_at`, `auth_provider`).
- Run `pytest` against the test DB to confirm migration round-trip is clean.
- Re-run `auth_audit_users.py` — expect exit 0 with the new columns visible in C1 baseline counts (no other behaviour change).

### Stage 3 — deploy code (PRs 2–4 in staging)
- Set staging env vars: `SUPABASE_AUTH_ENABLED=true`, `SUPABASE_NEW_USER_SIGNUP_ENABLED=false` (still off), `SUPABASE_SSO_PROVIDERS=google`, `SUPABASE_BRIDGE_REDIRECT_URL=<staging>/auth/supabase/oauth-callback`.
- Restart workers.
- Confirm `/auth/supabase/signup` returns 404 (flag still off).
- Confirm `/login` and `/register` continue to work for existing users.

### Stage 4 — flip the flag in staging (PR 5)
- Set `SUPABASE_NEW_USER_SIGNUP_ENABLED=true`. Restart workers.
- Confirm `/register` redirects to `/auth/supabase/signup`.
- Confirm `/auth/supabase/signup` renders the new page with email/password + Google button.

### Stage 5 — internal end-to-end signup tests
Five real signups by the internal team:

| # | Method | Expected result |
|---|---|---|
| 1 | Email + password | Confirmation email arrives; clicking the link lands at `/auth/supabase/confirm`; bridge POST succeeds; user lands on `/dashboard` authenticated. New `User` row has `password_hash IS NULL`, `is_email_confirmed=True`, `auth_provider="supabase_email"`, `supabase_auth_user_id` set. |
| 2 | Email + password (different email, same flow) | Same as #1. |
| 3 | Google SSO | OAuth completes; lands at `/auth/supabase/onboarding`; user fills profile; bridge succeeds; user lands on `/dashboard`. `auth_provider="supabase_oauth_google"`. |
| 4 | Email + password using an email already on a legacy account | Bridge returns 409; UI shows "you already have a Soletrak account; please sign in with your existing username". No `User` row mutated. |
| 5 | Email + password, then logout, then log back in | Logout clears Flask-Login cookie. Re-signup attempt with same email returns to the bridge as a returning user (resolved by `supabase_auth_user_id`); `was_created=false`; new Flask-Login session. |

### Stage 6 — regression checks
- Existing legacy user signs in via `/login` — must succeed identically to before.
- Mobile `UserApiToken` step-ingest endpoint (`/api/steps/buckets`) responds 200 with a valid token, 401 with a revoked token. Both with `SUPABASE_AUTH_ENABLED=true`.
- Admin signs in via legacy `/login`, hits `/admin/...` pages — works as before.
- Admin's previously-linked Supabase JWT continues to work via `/admin/auth/probe`.

### Stage 7 — failure-mode rehearsal
- Set `SUPABASE_NEW_USER_SIGNUP_ENABLED=false` mid-day. Confirm `/signup` becomes 404, `/register` stops redirecting, existing Supabase-first users created in Stage 5 are still in the DB (and would still be reachable if the flag were flipped back on).
- Set the flag back to `true`. Confirm a fresh signup still works.

Stage 7 is the dress rehearsal for production rollback.

---

## 10. Production Rollout Recommendation

### Day 0 — schema
- Deploy PR 1 to production.
- Apply migration: `flask db upgrade`.
- Confirm columns exist; run `auth_audit_users.py` for a sanity check (exit 0 expected).
- **Wait at least 24h** before flipping the Phase 3a flag. During this window the migration remains downgrade-safe.

### Day 1 — code
- Deploy PRs 2, 3, 4. All endpoints return 404 (flag off).
- Confirm legacy login and mobile token sync continue to work (smoke).

### Day 2 — flag flip (PR 5 deploy)
- Deploy PR 5 (the `/register` redirect).
- Set production env vars:
  - `SUPABASE_AUTH_ENABLED=true` (durable on; transitions from probe-window-only to always-on).
  - `SUPABASE_NEW_USER_SIGNUP_ENABLED=true`.
  - `SUPABASE_SSO_PROVIDERS=google`.
  - `SUPABASE_BRIDGE_REDIRECT_URL=<APP_BASE_URL>/auth/supabase/oauth-callback`.
- Restart workers.
- Confirm `/register` redirects to `/auth/supabase/signup`.
- One internal-team end-to-end signup (email + password) and one Google signup against production.
- Begin **24-hour observation window**:
  - Watch bridge endpoint 5xx rate (target: zero).
  - Watch JWKS lookup error rate (target: zero outside the cache-warming first call).
  - Watch mobile token-authenticated `/api/steps/buckets` error rate (target: unchanged from baseline).
  - Watch legacy `/login` success rate (target: unchanged from baseline).
  - Watch Supabase confirmation-email delivery (operator confirms in Supabase dashboard).

### Day 3 — broader announcement
- Once the 24h observation window is clean, announce the Supabase-first signup in the next user-facing release note.
- Continue monitoring bridge 5xx, audit JSONL volume, and mobile/API regressions for a further 7 days.

### Day 30 — decide on Phase 3b
- If Phase 3a has been operationally stable for ~30 days, begin design execution for Phase 3b (existing-user opt-in linking). Otherwise, hold and address Phase 3a issues first.

### Rollback decision tree (production)
- **Bridge 5xx spike, signups failing**: set `SUPABASE_NEW_USER_SIGNUP_ENABLED=false`. `/register` reverts to legacy. New signups go through the legacy form. Investigate.
- **JWKS / verifier failures**: same flag flip. Bridge endpoint 404s. Investigate and roll forward with a fix.
- **Mobile token regression detected**: this would be unexpected (Phase 3a does not touch the decorator), but if observed: same flag flip, then `git revert` PRs 3–5, then investigate.
- **Schema problem**: see PR 1 rollback notes (one-way after first Supabase-first user). Most schema issues are caught by the migration round-trip test before deploy.
- **Admin lockout** (most-feared scenario): impossible by design — `is_admin` is read off the existing `User` row in `decorators.admin_required`, and admins continue to use legacy `/login`. The Phase 2 admin recovery procedure (legacy `/login` as break-glass) remains in force throughout Phase 3a.

The single guiding principle through Phase 3a, inherited from Phase 1 / Phase 2: **roll forward by flag flips, not by destructive changes. Keep the legacy path working until we have evidence the Supabase path works under realistic load.**
