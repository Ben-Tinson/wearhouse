# Supabase Auth Readiness Review

This is a pre-implementation gap analysis for the planned phased migration from Flask-Login + app-managed credentials to Supabase Auth on Soletrak.

Scope: read-only audit of the live codebase against `docs/SUPABASE_AUTH_MIGRATION_PLAN.md`. No application code, schema, or migrations are changed by this document.

Baseline assumptions (confirmed against repo and platform docs):
- Supabase Postgres cutover is complete; runtime project ref is `sjwdvsefjlflgavshiyy`. Previous project `mizyioplztuzycipfdsd` is fallback/reference only.
- Flask remains the live backend; Flask-Login is the live browser auth path.
- The app-owned `user` table is the current account/profile anchor and the FK target for ~14 user-owned tables.
- `user_api_token` (mobile/API bearer) flows are live and must keep working.
- Supabase Auth is **not** implemented yet. There are no `supabase`, `gotrue`, `JWT`, or similar imports in `app.py`, `config.py`, `extensions.py`, `decorators.py`, `models.py`, `forms.py`, or any current `routes/*.py` / `services/*.py` (verified by grep).

**Known vs inferred** is called out throughout. "Known" = confirmed by reading the file referenced. "Inferred" = best judgement from current code; should be verified before acting on it.

---

## 1. Executive Readiness Summary

**Overall readiness level: ready to begin Phase 1 (schema linkage prep + auth abstraction), not ready to begin Phase 2 (backend integration) without a small number of pre-implementation decisions being locked.**

Strengths:
- Auth surface is reasonably small and centralised. Nearly all live auth lives in `routes/auth_routes.py`, `decorators.py`, `models.py`, `forms.py`, and a thin extension wiring in `app.py`.
- The user/profile model already separates identity-ish fields (`email`, `password_hash`, `is_email_confirmed`, `pending_email`) from app-domain fields (`preferred_currency`, `preferred_region`, `is_admin`, `marketing_opt_in`, `timezone`). The plan's "keep `user` as the app-owned profile table" already matches the data shape.
- `UserApiToken` is independent of password/credential logic — it can keep working unchanged through Phase 1 and Phase 2.
- Database is now Postgres, so a native `UUID` column for `supabase_auth_user_id` is feasible.

Gaps (highest impact first):
1. **Login identifier mismatch.** `LoginForm` and `routes/auth_routes.py::login` authenticate by **username**, not email. Supabase Auth is email-first. This requires an explicit product decision before implementation.
2. **No abstraction for "current app user".** Routes, forms, and templates call `current_user` (Flask-Login) directly. Adding a second auth path (Supabase JWT/session) without a thin resolver will spread provider-conditional logic everywhere.
3. **No `supabase_auth_user_id` linkage column** on `user`. This is the canonical link the plan recommends and nothing in schema currently supports it.
4. **`is_email_confirmed` is a hard login gate** (auth_routes.py:119). Backfilling users into Supabase Auth without setting this correctly risks lockouts.
5. **Email-change flow** uses `pending_email` + itsdangerous token. After Supabase Auth owns identity email, this conflicts with Supabase's own change-email flow.

Main risks (highest impact first):
- **Admin lockout** if admin users are not pre-linked and tested before any rollout. `make_admin.py` and `decorators.admin_required` work purely off `User.is_admin` + Flask-Login session.
- **Mobile/API step sync breakage** if `bearer_or_login_required` is refactored as part of the auth swap. Token resolution sets `g.api_user = token.user`, which assumes the token always points at an app `User` row.
- **Email-case / duplicate / `pending_email` data drift** that breaks deterministic linkage when Supabase identities are created/imported.

Recommendation: proceed with Phase 1 in a non-disruptive way (linkage column + read-only audit script) and resolve the locked decisions list (Section 10) before touching any auth route.

---

## 2. Current Auth/Account Inventory

| Concern | File / Symbol | Role |
|---|---|---|
| Login (browser) | `routes/auth_routes.py::login` (lines 106-133); `forms.py::LoginForm` (lines 52-58) | Authenticates by **username + password**. Calls `User.check_password()` and gates on `User.is_email_confirmed` before `login_user(user)`. |
| Logout (browser) | `routes/auth_routes.py::logout` (lines 136-141) | Calls `logout_user()`; flashes message; redirects to `main.home`. |
| Flask-Login session setup | `app.py::create_app` (lines 47-49) initialises `login_manager`, sets `login_view='auth.login'`. | Standard Flask-Login wiring; uses cookie session backed by `SECRET_KEY`. |
| `current_user` loading | `app.py::load_user` (lines 54-56) — `db.session.get(User, int(user_id))` | Flask-Login encodes the integer `User.id` in the session and reloads via integer PK. **Hard-coded integer cast.** |
| Auth decorator (login required) | `flask_login.login_required` used throughout `routes/*.py` | Standard Flask-Login decorator. |
| Auth decorator (admin) | `decorators.py::admin_required` (lines 10-22) | Checks `current_user.is_authenticated and current_user.is_admin`. |
| Auth decorator (bearer or session) | `decorators.py::bearer_or_login_required` (lines 25-58) | Looks up `Authorization: Bearer <token>` against `UserApiToken.token_hash` (SHA-256). Falls back to `current_user`. Sets `g.api_user`. **Commits `last_used_at` inside the auth check**. |
| Admin checks | `decorators.admin_required`; inline `current_user.is_admin` checks in `main_routes.py:312`, `:1949`, `:1990`, `:2037`; `news_routes.py:291`, `:352`, `:394`. | `is_admin` is read straight off the `User` row everywhere. |
| Password reset request | `routes/auth_routes.py::reset_password_request` (lines 145-160) + `send_password_reset_email` (lines 21-34) | Looks up by email. Sends `User.get_reset_password_token()` link. Always flashes the same message (anti-enumeration). |
| Password reset (apply) | `routes/auth_routes.py::reset_password_with_token` (lines 164-199) | Verifies itsdangerous token → `User`. Calls `set_password`. Logs the user out if they were logged in as the same identity. |
| Send change-password link (logged-in) | `routes/auth_routes.py::send_change_password_link_route` (lines 264-271); UI in `request_password_change_link.html` | Reuses the password-reset email path for logged-in users. |
| Email confirmation (registration) | `routes/auth_routes.py::confirm_email_from_token` (lines 291-313); token via `User.get_email_confirmation_token` / `verify_email_confirmation_token` | Sets `is_email_confirmed=True`. |
| Email-change flow | `routes/auth_routes.py::confirm_new_email_with_token` (lines 213-260); `User.get_confirm_new_email_token` / `verify_confirm_new_email_token`; `pending_email` field on `User` | Two-step: profile sets `pending_email`, email link confirms and rewrites `email`. |
| Profile/account pages | `routes/main_routes.py::profile` (~lines 1745-1773) and `edit_profile` (~1809-1856); `templates/profile.html`, `templates/edit_profile.html` | Heavy direct reads/writes of `current_user` fields including `username`, `email`, `pending_email`, `marketing_opt_in`, `preferred_currency`, `preferred_region`. |
| API token create | `routes/main_routes.py:1775-1786` → `services/api_tokens.create_token_for_user(current_user, ...)` | Generates plaintext, stores SHA-256 hash, plaintext shown once. |
| API token revoke | `routes/main_routes.py:1789-1806` | Sets `revoked_at`; scoped by `user_id=current_user.id`. |
| Templates referencing `current_user` | `base.html`, `profile.html`, `home.html`, `dashboard.html`, `release_calendar.html`, `release_detail.html`, `sneaker_detail.html`, `wishlist.html`, `_single_sneaker_card.html`, `_wishlist_button.html`, `request_password_change_link.html` | All use `current_user.is_authenticated` / `current_user.is_admin` / `current_user.pending_email` etc. None call Supabase APIs. |

**Known**: list is sourced directly from grep across `routes/*`, `services/*`, `decorators.py`, `forms.py`, `templates/*`, and `tests/*`.

---

## 3. Current Model / Schema Readiness

Reference: `models.py` (User at lines 15-100, UserApiToken at 102-117).

### What already supports a future Supabase Auth linkage model
- `User.email` exists, is `unique=True`, `nullable=False`, length 120.
- Email is already lowercased before insert in `auth_routes.py::register` (line 61) and `reset_password_request` (line 152), so case-insensitive matching is **mostly** consistent — but case normalisation is application-side, not enforced at the DB level.
- `User.is_email_confirmed` exists and can be reused as a Supabase-mirrored flag if desired.
- `User.id` is an integer PK that 14+ tables depend on. Keeping it as the app-user PK matches the plan's recommendation. We do not need to migrate to a UUID PK to add Supabase Auth.
- All user-owned tables already FK `user.id` (collection, wishlist, API tokens, steps, attribution, exposure, damage/repair, expenses, articles via `created_by_user_id`, releases via `ingested_by_user_id`). No domain table FK's `email` or `username` — linkage will not require touching child tables.

### What likely needs to be added later (for Supabase Auth)
- `User.supabase_auth_user_id` (nullable, UUID where practical, unique partial index where not null). Recommended canonical link.
- `User.created_at` / `User.updated_at` — currently **absent** on `User` (see lines 15-32). Useful for migration audit. Optional but cheap to add in the same migration.
- `User.last_login_at` — optional, helps inform "active vs dormant" linkage strategy.
- A unique index on `lower(email)` (or a `citext` column / functional index) to make duplicate detection deterministic before linkage. Currently uniqueness is on raw `email`, with case normalisation done in code, which is best-effort only.
- Possibly a temporary `auth_migration_state` enum/string column on `User` if granular per-user migration tracking is wanted (alternative to a separate audit table).

### Likely future field/constraint/index/linkage patterns
- Canonical link: `user.supabase_auth_user_id UUID UNIQUE WHERE NOT NULL`.
- Lookup helper: `get_app_user_by_supabase_id(supabase_uuid) -> User`.
- Backfill helper: `link_user_to_supabase(app_user_id, supabase_uuid)` with safety checks (no double-linking; no overwrite without admin override).
- Email matching during backfill should normalise both sides on `lower(trim(email))` and explicitly skip `pending_email` collisions.

### Schema assumptions that may complicate migration
- **Integer `User.id` is encoded everywhere** — Flask-Login session, FK indexes, JSON payloads in mobile-token-protected endpoints, and admin scripts. **Do not change this.** Add Supabase UUID alongside.
- **`pending_email` is `unique=True, nullable=True`.** During migration, if we attempt to import Supabase Auth's email-change flow, we may end up with two systems each holding a "pending" email for the same user. Pick one owner before flipping over.
- **No `created_at` on User** means we cannot order users by signup recency for the active-cohort backfill without using `min(child_table.created_at)` heuristics.
- **`username` is `unique=True, nullable=False`** and is the login identifier today. Supabase Auth will not own `username`. Keeping `username` as an app-only field is fine; the question is whether login still accepts it (see Section 4).

---

## 4. Embedded Flask-Auth Assumptions

The codebase carries a number of assumptions that will not survive a naïve auth swap. Highest-risk first.

### A. Login is by username, not email (HIGH risk)
- `forms.py::LoginForm` (lines 52-58) asks for `username` only.
- `routes/auth_routes.py::login` (line 116) does `User.query.filter_by(username=form.username.data).first()`.
- **Implication:** Supabase Auth identifies by email (or phone). Users who currently log in by username will have a different identifier path. This is a UX *and* implementation decision: keep username-as-login (Supabase signs in by email under the hood; the form maps username → email), switch to email-only, or accept either. Must be decided before code is written.

### B. Integer `User.id` baked into Flask-Login (MEDIUM risk)
- `app.py::load_user` casts to `int(user_id)`. If a Supabase-issued session ever wires through Flask-Login directly, the loader must handle the resolution path properly. Better solution: do **not** stuff Supabase identity into Flask-Login's session; use a separate resolver and keep Flask-Login for the legacy fallback.

### C. Auth assumption: "the request has exactly one identity, and it's `current_user`" (HIGH risk)
- Routes, forms, and templates read `current_user.*` directly (200+ references across `routes/`, `forms.py`, and `templates/`).
- Under dual-run, requests may arrive with a Supabase JWT cookie/header **and** a stale Flask-Login session, or a partially linked user. Without a single resolver function, every route needs ad-hoc handling.
- **Mitigation:** add `get_current_app_user()` returning a resolved `User` from whichever auth path produced it. Routes that already use `current_user` keep working through Flask-Login during transition; new Supabase paths route through the resolver.

### D. Admin checks read directly off the `User` row (LOW-MEDIUM risk, but high blast radius if mishandled)
- `decorators.admin_required` and inline `current_user.is_admin` checks (see Section 2 inventory).
- This is fine to keep — admin authorization stays app-owned per the plan. The risk is operational: an admin must be **linked to a Supabase identity before** the Supabase login path is enabled for them, otherwise their first Supabase-authenticated request resolves to no app user and they cannot reach admin pages. Pre-link admins first.

### E. `is_email_confirmed` blocks login (MEDIUM risk)
- `routes/auth_routes.py::login` line 119 refuses login for unconfirmed users.
- After Supabase Auth owns email verification, this column either becomes a mirror of Supabase's `email_confirmed_at` or is retired. If we backfill users into Supabase Auth without setting confirmation correctly, **and** we keep this gate on the legacy login path, users may land in an "Supabase says confirmed; Flask says not" mismatch on rollback.

### F. Email-change flow assumes app owns identity email (HIGH risk for cutover)
- `routes/auth_routes.py::confirm_new_email_with_token` (lines 213-260) and `User.pending_email` mechanics are entirely app-managed.
- Once Supabase Auth owns identity email, the only safe single-source flow is to drive email change through Supabase Auth's API and reflect the result in `User.email`. Leaving the legacy flow live during dual-run is acceptable provided one rule: **Flask cannot mutate `User.email` without coordinating with Supabase Auth's identity record** (otherwise the Supabase-side email and the app-side email diverge).

### G. itsdangerous tokens depend on `SECRET_KEY` (LOW risk if untouched)
- `User.get_reset_password_token`, `get_confirm_new_email_token`, `get_email_confirmation_token` all sign with `current_app.config['SECRET_KEY']`.
- **Don't rotate `SECRET_KEY`** during migration; doing so invalidates active reset/confirm links and Flask-Login sessions simultaneously, which would amplify any auth incident. Tag this as a "do not touch" during the migration window.

### H. `bearer_or_login_required` mutates DB inside the auth path (LOW risk, watch for it)
- `decorators.py:44-45` does `token.last_used_at = db.func.now(); db.session.commit()`.
- Adding a Supabase JWT branch to the same decorator must avoid implicit commits on auth state mutation, or must wrap the existing commit so it does not partially complete a rolled-back business transaction. Worth flagging when this decorator is touched.

---

## 5. API Token and Admin Compatibility Review

### `user_api_token`
- **What must remain compatible during phased dual-run:**
  - SHA-256 hash storage and the `Authorization: Bearer <plaintext>` header contract (mobile clients are already shipped against this).
  - `g.api_user` being a fully-loaded app `User` row (used by step/wear endpoints in `routes/sneakers_routes.py`, e.g. `:1991` and `:2093`).
  - `UserApiToken.user_id` as integer FK to `user.id`.
- **What could break under naïve Supabase Auth introduction:**
  - If Supabase Auth replaces `decorators.bearer_or_login_required` instead of running alongside it, mobile clients lose access overnight. The plan calls for keeping `UserApiToken` during transition; the implementation must respect that.
  - If Supabase JWT headers and `Authorization: Bearer <opaque>` headers both appear, the decorator must distinguish them. Today's branch is "starts with `bearer ` → look up in `UserApiToken`". Supabase JWTs also live in `Authorization: Bearer <jwt>` headers. **A naïve refactor that does JWT verification first will silently break mobile tokens.** Recommend explicit precedence: try `UserApiToken` hash first (fast O(1) lookup on a 64-char hex), fall through to JWT verification, fall through to Flask-Login session. Or split the header conventions so they don't collide.
- **What needs special care:**
  - The token's owning `User` may, mid-migration, be unlinked to Supabase. That's fine for the mobile path because mobile auth still resolves by `UserApiToken.user_id`.
  - Token issuance is currently triggered from the profile UI which itself is `@login_required`. If/when profile auth flips to Supabase, token create/revoke routes must still resolve to the right app user.

### Admin
- **Must remain compatible:** `User.is_admin` as the source of truth; admin checks via `decorators.admin_required` and inline `current_user.is_admin`; `make_admin.py` script keyed off integer `User.id`.
- **Could break naïvely:** if Supabase Auth metadata (`app_metadata.role`) is consulted instead of `User.is_admin`, we end up with two sources of truth that drift. Don't.
- **Special care:** admins must be pre-linked to a Supabase identity *before* the Supabase login path is enabled for them. Otherwise their first Supabase login resolves to no `User`, and they get an unhelpful 401/403. Recommend a pre-rollout sanity test: log a known admin in via Supabase in staging, hit `/admin/...`, confirm 200. Add it to the rollout checklist.

---

## 6. Migration Gap Analysis (vs `SUPABASE_AUTH_MIGRATION_PLAN.md`)

### Already compatible with the plan
- App-owned `user` table is the profile/account anchor (Plan §3, §7).
- Domain tables FK only `user.id` — no `email` or `username` FKs to break (Plan §4 linkage strategy).
- `UserApiToken` is decoupled from credential logic — survives Phase 1-3 unchanged (Plan §7 "API tokens").
- `is_admin` lives on the app `User` row, ready to remain authoritative (Plan §7 admin checks).
- Postgres runtime supports native UUID for the linkage column (Plan §4 "Type: Postgres UUID where practical").
- Email is already lowercased on insert/lookup in registration and reset, so most rows are case-clean (Plan §4 "Normalize email case").

### Clearly needs refactoring
- `LoginForm` and `routes/auth_routes.py::login` username-based authentication will not align with Supabase's email-first identifier without an adapter (Plan §8 "Login").
- `routes/auth_routes.py::reset_password_request` / `reset_password_with_token` and `send_password_reset_email` should eventually be retired or proxied (Plan §8 "Password reset").
- `routes/auth_routes.py::confirm_email_from_token` and the registration→token email path should be replaced or made conditional after Supabase verification is live (Plan §8 "Email verification").
- `routes/auth_routes.py::confirm_new_email_with_token` + `pending_email` semantics need to be coordinated with Supabase Auth's email-change flow once it owns identity email (Plan §7 profile/account data).

### Likely needs an abstraction layer
- A single `get_current_app_user()` resolver that knows about Flask-Login *and* Supabase JWT/session. All routes that today read `current_user` keep doing so during Phase 1-2; new Supabase paths use the resolver. This avoids "two `current_user`s" anti-pattern.
- A `services/supabase_auth_service.py` (or similar) containing: JWT verification, identity-by-supabase-id lookup, "find or link" helper, "create app user from Supabase identity" helper. **Do not** spread provider calls across routes (Plan §7 "service to avoid spreading provider logic").
- A linkage service: `link_app_user_to_supabase(app_user_id, supabase_uuid, *, by_admin=False)`. Used by backfill scripts and (later) by the on-login progressive linkage path.
- An auth-decorator family: `@app_user_required` and `@app_admin_required` that internally call the resolver. Migrate routes to these only when their auth path actually flips.

### What should not be touched first
- `models.User.id` (integer PK) — keep as-is forever.
- The 14+ FK relationships keyed off `user.id`.
- `UserApiToken` schema and `decorators.bearer_or_login_required` semantics for the bearer-token branch (mobile contract is shipped).
- `User.is_admin`, `make_admin.py`, and any `admin_required` checks.
- `SECRET_KEY`. Do not rotate during the migration window.
- Templates that read `current_user.*` — they keep working as long as the resolver returns a `User` row. Touch them only after the resolver lands.

---

## 7. Risk Hotspots

Listed in approximate order of severity × likelihood.

### H1. Username-based login (`forms.py::LoginForm`, `auth_routes.login`)
- **Why risky:** Supabase Auth is email-first. Existing user mental model is "log in with my username". A migration that flips this without a decision will surprise users and break automated tests (the test suite uses `auth.login(username=..., password=...)` exclusively — see `tests/conftest.py:38`).
- **Failure mode:** Users locked out after Supabase Auth becomes primary because they don't know their email-password combination is the live one; tests fail wholesale.
- **Mitigation:** lock the decision before code (Section 10). If keeping username, add a server-side mapping `username → email → Supabase signIn` that is used only on the bridge endpoint. If switching to email, communicate it in advance and update test fixtures and login template/form copy.

### H2. `Authorization: Bearer …` header collision (`decorators.bearer_or_login_required`)
- **Why risky:** Today a 64-char hex `UserApiToken` and a future Supabase JWT both arrive in `Authorization: Bearer <X>`.
- **Failure mode:** A naïve "verify JWT first" refactor 401s every mobile step-sync request; users see step data stop syncing.
- **Mitigation:** preserve the existing branch. Either (a) cheap-path on `UserApiToken` first using SHA-256 hash equality, (b) detect JWT shape (3 dot-separated base64url segments), or (c) introduce a separate header / scheme for Supabase JWT. Decide explicitly. Add a regression test that hits a `:1991`/`:2093` style endpoint with a `UserApiToken` header *while* a Supabase JWT verifier is wired up.

### H3. Admin lockout
- **Why risky:** Admin checks rely on `User.is_admin`. If a Supabase login resolves to a not-yet-linked user, the user reaches authenticated routes but `is_admin` is `False` (or worse, the resolver returns `None` and decorators 403/401).
- **Failure mode:** Admin cannot manage releases or run CSV imports at the moment Supabase Auth becomes primary.
- **Mitigation:** pre-link every admin row to a Supabase identity before the cutover; add a rollout gate that verifies "every admin has a non-null `supabase_auth_user_id`". Keep Flask-Login fallback live for emergency admin access until cutover is proven.

### H4. Email-change drift between app and Supabase
- **Why risky:** Two systems can each store a "pending email" or change it independently.
- **Failure mode:** A user updates email in the profile (legacy path), confirms via app token, but Supabase Auth's identity email is unchanged → next Supabase login still uses the old email → user sees "you don't have an account" or links wrong app row.
- **Mitigation:** during dual-run, freeze the legacy email-change UI for users who have a non-null `supabase_auth_user_id`; route their email change through Supabase Auth and mirror back to `User.email` on success.

### H5. `is_email_confirmed` gate vs Supabase verification state
- **Why risky:** Login refuses unconfirmed users. Backfilling Supabase Auth identities for already-confirmed app users requires setting Supabase's `email_confirmed_at` accurately; otherwise rollback to legacy login still works but the Supabase path may bounce them (or vice versa).
- **Failure mode:** Confirmed app users locked out of the Supabase path on first attempt, or unconfirmed app users accidentally bypass the confirmation gate when arriving via Supabase.
- **Mitigation:** during backfill, set Supabase `email_confirmed_at` from `User.is_email_confirmed`. Decide explicitly whether `is_email_confirmed` becomes legacy/derived after cutover (Section 10 / 11).

### H6. `bearer_or_login_required` commits inside the auth path
- **Why risky:** `db.session.commit()` for `last_used_at` happens before the route runs. Adding a JWT verification branch that also writes (e.g. an audit row) compounds the implicit-commit footprint.
- **Failure mode:** A failed business-logic transaction in the route can no longer roll back the auth-side write atomically.
- **Mitigation:** when this decorator is touched, move the `last_used_at` update to a deferred path (after route success, or via a separate session). Don't bundle both auth paths' side-effects in one decorator.

### H7. Test fixtures depend on legacy path
- **Why risky:** `tests/conftest.py` creates users with `set_password()` + `is_email_confirmed=True` and the auth helper logs in via username/password. Test count: 27 test files, with `tests/test_auth.py`, `test_api_tokens.py`, `test_profile.py` directly exercising the legacy path.
- **Failure mode:** If Supabase becomes the only path, the entire test suite needs reworking simultaneously, which encourages risky big-bang changes.
- **Mitigation:** keep the legacy login path live throughout Phase 1-3. Introduce Supabase-specific tests alongside, not replacing.

### H8. Empty/lowercase email not enforced at DB level
- **Why risky:** Uniqueness is on raw `email`. Code mostly lowercases on insert, but historic data may contain mixed-case emails. Linkage by email becomes ambiguous.
- **Failure mode:** Two app users with `Foo@Example.com` and `foo@example.com` exist; Supabase has only the lowercase identity; the app may link the wrong row.
- **Mitigation:** the Phase 1 audit script should surface case-collisions and require a manual fix before any backfill.

---

## 8. Recommended Implementation Order (concrete)

This is concrete enough to drive PRs. Each step is intended to land in isolation and be reversible. Mapping to the plan: Steps 1-3 = Plan Phase 1; Steps 4-6 = Plan Phase 2; Steps 7-8 = Plan Phase 3; Steps 9-10 = Plan Phase 4; Steps 11-12 = Plan Phase 5.

1. **Read-only audit script** (`scripts/auth_audit_users.py` or similar). No schema change. Reports: case-collision emails, `pending_email` collisions, unconfirmed admins, `NULL`/empty email users, and admin count. Output to stdout. Used to gate Step 2.
2. **Schema migration: linkage column.** Add `User.supabase_auth_user_id` (UUID, nullable) and a partial unique index `WHERE supabase_auth_user_id IS NOT NULL`. Optionally add `User.created_at` / `User.last_login_at` in the same migration. Backfill = NULL. No code change yet.
3. **Auth resolver abstraction.** Add `get_current_app_user(request)` returning a `User` (or `None`) using *only* Flask-Login today. Pure refactor — every existing call site keeps using `current_user`; new code uses the resolver. No behaviour change. Tests prove parity.
4. **Supabase Auth config.** Add `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` (server-only), `SUPABASE_JWT_SECRET` to `config.py`. Document staging vs prod redirect URLs in a deployment doc, not in code.
5. **Supabase client + JWT verifier service.** `services/supabase_auth_service.py`. Verify a Supabase access token, return `(supabase_user_id, email, claims)`. Pure function, unit-tested with fixed JWT fixtures.
6. **App-user resolution by Supabase id.** Add `find_app_user_by_supabase_id(supabase_uuid)` and `find_app_user_by_email(email)` (case-normalised). Wire into the resolver from Step 3 *behind a feature flag* (e.g. `SUPABASE_AUTH_ENABLED=false`). Default off. No live behaviour change.
7. **Linkage service** for backfill. `link_app_user_to_supabase(app_user_id, supabase_uuid)` with collision and double-link guards. Backed by tests.
8. **Admin pre-linking script** (CLI). Iterates admin users, creates Supabase Auth identities via Supabase admin API where missing, sets `supabase_auth_user_id`. Safe to re-run.
9. **Bridge login endpoint.** A new auth route, e.g. `/auth/supabase/callback`, that accepts a Supabase access token, verifies it, resolves the app user, and either calls `login_user(app_user)` (preserves Flask-Login session for the rest of the app) or sets a separate session cookie. Hidden behind feature flag, exercised first by admins.
10. **Limited cohort rollout.** Enable the Supabase login UI for admins + an internal cohort. Keep `/login` (username/password) as fallback. Verify profile, admin, collection, wishlist, API token, and step-sync flows.
11. **Promote Supabase Auth to primary.** Move `/login` to the Supabase flow; keep legacy login under `/login/legacy` behind a flag. Promote password reset and email verification to Supabase Auth's flows.
12. **Legacy retirement.** Once stable: remove legacy login route, retire `User.password_hash`, `is_email_confirmed`, `pending_email`, and the itsdangerous token methods. Decide whether to drop or keep them for rollback safety. Mobile token strategy revisited separately.

---

## 9. Suggested First Implementation Slice

The smallest concrete next step that materially de-risks the migration without touching live auth:

**Slice: "Linkage column + audit, no behaviour change."**

Two PRs landing together (or in close sequence):

1. **Read-only audit script.**
   - File: `scripts/auth_audit_users.py`.
   - Connects via current SQLAlchemy session.
   - Reports (stdout, no DB writes):
     - Total user count, admin count, `is_email_confirmed` count.
     - Case-collision emails (`SELECT lower(email), count(*) ... GROUP BY 1 HAVING count(*) > 1`).
     - Users with non-null `pending_email`.
     - Users with `is_admin=true` and `is_email_confirmed=false`.
     - Users with empty/whitespace `email` or `username`.
   - No app code changes. Run locally against the live DB to surface data we'll need to clean before linkage.

2. **Alembic migration: add `supabase_auth_user_id` to `user`.**
   - Column type: `UUID` (Postgres native), nullable.
   - Partial unique index: `WHERE supabase_auth_user_id IS NOT NULL`.
   - Optionally include `created_at` (with `server_default=now()` and a backfill of `NULL` or `now()` — decide explicitly), and `last_login_at` (nullable).
   - No model/code consumers yet — column is dormant until Phase 2.
   - Test: `flask db upgrade` then `flask db downgrade` round-trip on the test DB.

Why this is the right first slice:
- Zero impact on running auth, mobile tokens, or admin flows.
- Surfaces real data hazards before any backfill code is written.
- Adds the canonical link the plan says we need, in a reversible way.
- Unblocks Phase 2 work (resolver, JWT verifier, linkage service) without committing to product decisions yet.

Explicitly **not** in this slice: any new dependency, any Supabase SDK install, any change to login/forms/decorators/templates, any Supabase Auth identity creation. That work waits until Section 10 decisions are locked.

---

## 10. Pre-Implementation Decisions to Lock

Decisions that should be agreed and recorded (in `docs/DECISIONS.md`) before code changes begin.

1. **Login identifier going forward.** Username, email, or either? This drives `LoginForm`, `auth_routes.login`, the bridge endpoint, and the test suite. (See H1.)
2. **`supabase_auth_user_id` column type.** Native Postgres `UUID` (recommended; we are on Postgres) or `String(36)`? UUID has stricter validation and smaller storage; String is simpler for portability.
3. **Password import strategy.** Force reset / magic-link for all existing users on first Supabase login? Or attempt to import werkzeug `pbkdf2:sha256` hashes into Supabase? (Default expectation: forced reset; werkzeug's salt format may not import cleanly into GoTrue.)
4. **Fate of `is_email_confirmed`.** After cutover: deprecated, derived from Supabase `email_confirmed_at`, or retained as legacy flag for rollback?
5. **Fate of `pending_email` and the legacy email-change flow.** Frozen per-user once `supabase_auth_user_id` is set? Removed entirely after cutover?
6. **Mobile / API token strategy.** Keep `UserApiToken` indefinitely, or move mobile clients to Supabase-issued tokens in a later, separately-planned phase?
7. **Header convention for Supabase JWT.** Keep `Authorization: Bearer <jwt>` (with format-disambiguation in the decorator) or use a different header to avoid collision with `UserApiToken`?
8. **Where Supabase Auth state lives in the request context.** Bridge to Flask-Login `login_user(app_user)` (simplest, reuses templates) or maintain a separate cookie/session and route everything through the resolver?
9. **Admin emergency procedure.** What is the break-glass path if Supabase Auth is down or misconfigured? Keep `/login/legacy` indefinitely, or document a CLI-driven admin-impersonation procedure?
10. **`SECRET_KEY` rotation policy.** Confirmed do-not-rotate during the migration window? When and how is it allowed to rotate after legacy retirement?
11. **Environment variables and redirect URLs.** Staging vs prod values for `SUPABASE_URL`, anon key, service role key, JWT secret, and OAuth/email redirect URLs.
12. **Rollback policy.** How long is `/login/legacy` kept alive after Supabase becomes primary? Days? Weeks? Conditional on metric thresholds?
13. **Backfill audience and order.** Admins → internal users → active users → long tail (progressive on-login)? Or admins → all users at once?
14. **Test strategy under dual-run.** Will tests continue to use the legacy login fixture, with Supabase paths covered by separate tests using a stubbed JWT verifier? (Recommended.)

---

## 11. Open Questions

Where the codebase or docs do not make the answer clearly determinable:

- Are there any Supabase Auth identities **already pre-provisioned** in the runtime project `sjwdvsefjlflgavshiyy`? The codebase has no Supabase imports, but the Supabase project itself may already have records from earlier experiments. Needs a Supabase Studio / admin-API check before any backfill plan is finalised.
- Is the live `user` table free of case-collision emails? The audit script in §9 is the way to confirm. Today, code lowercases on insert in `register` and `reset_password_request`, but historic data predating those code paths could still contain mixed-case rows.
- Does the runtime DB still hold any orphan rows where `pending_email` was set but never confirmed? These will be edge cases during linkage.
- Does the password-reset email flow currently work end-to-end in production, or is delivery still deferred (`docs/AI_CONTEXT.md` says deferred because Supabase Auth is expected to replace it)? If users have been told to "request a reset and wait", they may behave differently during cutover than typical users.
- Are there any in-flight `itsdangerous` tokens (reset / email-confirmation / change-email) that we should treat as "must remain valid through migration day", or is it acceptable to invalidate them?
- Does the mobile app use `Authorization: Bearer <UserApiToken>` exclusively, or is there a code path that ever sends a session cookie? (Affects whether dual-run can simply ignore the bridge endpoint for mobile.)
- Will RLS be enabled on `public.user` and user-owned tables as part of this phase, or strictly later? The plan defers this; confirm before any Supabase Auth-aware policies are written.
- Should social login (Google/Apple/etc.) be enabled day 1, or is it strictly password + magic-link first? Affects what we wire in Step 5/9 above.
- Is there a marketing / comms requirement to email users when their account is migrated to Supabase Auth? (`marketing_opt_in` on `User` already exists if so.)
- For backfill scripts that act "as a system user" (e.g. `release_updater.py` writing `ingested_by_user_id`), is there a chosen system-user identity, and should that identity have a Supabase row too, or only an app row?

---

## Appendix: file/line index

For quick navigation during implementation:

- App factory + Flask-Login wiring: `app.py:30-56`.
- User model: `models.py:15-100`.
- UserApiToken model: `models.py:102-117`.
- All FKs to `user.id`: `models.py:9, 104, 139, 216, 230, 248, 270, 307, 323, 343, 376, 399, 445, 634, 687`.
- Auth routes: `routes/auth_routes.py:53-313`.
- Decorators: `decorators.py:10-58`.
- Forms (auth-relevant): `forms.py:52-141`.
- API tokens: `services/api_tokens.py:1-37`.
- Profile/account routes: `routes/main_routes.py:1745-1856` (profile, edit, token list/create/revoke).
- Admin checks (sample): `routes/main_routes.py:2275, 2312, 2351, 2458, 2496, 2599, 2708, 2733, 2763`; `routes/news_routes.py:482, 579, 706`.
- `bearer_or_login_required` consumers (sample): `routes/sneakers_routes.py:1991, 2093`.
- Test auth fixtures: `tests/conftest.py:32-152`.
