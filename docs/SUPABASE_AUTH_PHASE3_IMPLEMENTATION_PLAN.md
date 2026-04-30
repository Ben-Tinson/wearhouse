# Supabase Auth — Phase 3 Implementation Plan

This document plans the transition from Flask-Login-first authentication to **Supabase-first end-user authentication** for Soletrak. Phase 3 is the first phase that intentionally changes end-user UX: brand-new users will sign up and log in through Supabase Auth (email + password and SSO providers), and existing users get a dual-path window to migrate on their own terms.

It is a design/implementation plan only — no code, schema, or migrations are applied by this document.

References:
- `docs/SUPABASE_AUTH_MIGRATION_PLAN.md` — overall phased strategy.
- `docs/SUPABASE_AUTH_READINESS_REVIEW.md` — original gap analysis.
- `docs/SUPABASE_AUTH_PHASE1_IMPLEMENTATION_PLAN.md` — completed.
- `docs/SUPABASE_AUTH_PHASE2_IMPLEMENTATION_PLAN.md` — completed.
- `docs/SUPABASE_AUTH_PHASE2_PROBE_REHEARSAL_OUTCOME_2026-04-30.md` — staging probe rehearsal record.
- `docs/DECISIONS.md` — accepted decisions that constrain this plan.

Baseline at Phase 3 start (confirmed):
- `user.supabase_auth_user_id` UUID column exists with a partial unique index. Admin rows are linked. Other rows are NULL.
- `services/supabase_auth_service.py` verifies HS256 + ES256 + RS256 against `SUPABASE_JWT_SECRET` (legacy) or the project JWKS at `<SUPABASE_URL>/auth/v1/.well-known/jwks.json`. Algorithm allowlist guards alg-confusion.
- `services/supabase_auth_linkage.py` is the only sanctioned writer of `user.supabase_auth_user_id`. The resolver and decorator never auto-link.
- `services/auth_resolver.py` consults Flask-Login first, then a Supabase JWT branch behind `SUPABASE_AUTH_ENABLED`.
- `decorators.bearer_or_login_required` applies format-disambiguation: JWT-shaped values route to the Supabase branch when the flag is on; opaque bearers continue through `UserApiToken` byte-for-byte.
- `/admin/auth/probe` is the only live Supabase-aware end-user-reachable HTTP route. Admin-only, flag-gated, read-only. Validated end-to-end against staging including ES256/JWKS.
- `routes/auth_routes.py`, `forms.py`, and templates are unchanged. `LoginForm` remains username + password.
- `UserApiToken` mobile/API contract is unchanged.
- Production steady state: `SUPABASE_AUTH_ENABLED=false`. Flipped on only in time-boxed probe windows.

Phase 3 begins with the question Phase 1 / Phase 2 deliberately deferred: **how do real end users actually authenticate via Supabase?**

---

## 1. Objective and Non-Objectives

### Objective

After Phase 3, Soletrak supports two coexisting authentication paths for end users, with Supabase Auth as the primary path for new users:

- **Brand-new users** sign up and sign in exclusively through Supabase Auth — email + password (with Supabase-managed verification and reset) or SSO (Google, Apple, etc.).
- **Existing users** keep the legacy username + password Flask-Login path AND can opt into Supabase Auth at their own pace, either by linking on next Supabase sign-in or via the linkage CLI.
- The existing app — admin checks, profile pages, sneaker data, mobile `UserApiToken` step sync, and every `current_user`-using route — keeps working unchanged because the bridge layer turns a verified Supabase identity into a normal Flask-Login session.

### Non-objectives (deferred to Phase 4 or later)

- **Forced migration of all existing users to Supabase.** Phase 3 ends with a documented sunset *plan* for legacy login, not the sunset itself.
- **Removal of `password_hash`, `is_email_confirmed`, `pending_email`, or itsdangerous tokens.** These remain as the legacy fallback throughout Phase 3.
- **Mobile/API client migration to Supabase tokens.** `UserApiToken` is the only mobile auth path through Phase 3.
- **Row-Level Security on `public.user` or any user-owned table.** Still deferred per the accepted plan.
- **Removing `username` from the user model.** Username may transition to "display handle" but stays for backwards compatibility.
- **Email-change UX redesign.** The dual coordination problem (Supabase email vs app email) is in scope for design but not for full UI replacement in Phase 3.

---

## 2. Target End-State for New-User Auth

A new user lands on Soletrak and chooses an authentication method:

```
┌────────────────────────────────────────────────────────────────────┐
│ Sign up                                                            │
│                                                                    │
│  [ Continue with Google  ]                                         │
│  [ Continue with Apple   ]                                         │
│  ───────────── or ─────────────                                    │
│  Email     [_________________]                                     │
│  Password  [_________________]                                     │
│  First name [____]   Last name [____]                              │
│  Region    [▼ UK ]                                                 │
│  [ Create account ]                                                │
└────────────────────────────────────────────────────────────────────┘
```

### Email + password
1. Browser captures email, password, and the Soletrak-side profile fields (first name, last name, region, marketing opt-in).
2. Browser calls Supabase JS SDK `supabase.auth.signUp({email, password, options: {emailRedirectTo: <bridge confirm URL>}})`.
3. Supabase emails a confirmation link.
4. User clicks the link; Supabase confirms the identity and returns to `/auth/supabase/confirm` with an access token.
5. Front-end calls `POST /auth/supabase/bridge` with the access token + the Soletrak profile fields.
6. Bridge verifies the token, creates the app `User` row (with `password_hash IS NULL`, `is_email_confirmed=true` mirrored from Supabase), links `supabase_auth_user_id`, and calls `login_user(app_user)` to issue a Flask-Login session.
7. User lands on `/dashboard` already authenticated. The rest of the app sees `current_user` as if they had logged in via the legacy path.

### SSO (Google / Apple / etc.)
1. Browser captures Soletrak profile fields (or these are collected in a mandatory post-OAuth step — a "tell us about you" screen).
2. Browser calls Supabase JS SDK `supabase.auth.signInWithOAuth({provider, options: {redirectTo: <bridge URL>}})`.
3. User completes the provider's flow (Google / Apple / etc.).
4. Supabase redirects to `/auth/supabase/oauth-callback` with an access token.
5. Bridge verifies the token. If first-time, the app shows the missing profile fields in a single follow-up form (post-OAuth onboarding step), then calls `POST /auth/supabase/bridge` with the access token + collected fields.
6. Bridge creates the app `User` row, links, issues Flask-Login session.

### Login (returning new user)
1. User goes to `/login` — Supabase entry point on the same page (see §6).
2. Either email/password through Supabase, or one-click SSO.
3. Supabase returns an access token; browser POSTs to bridge.
4. Bridge verifies, resolves the existing app `User` by `supabase_auth_user_id` (fast path) or email (linkage fallback), updates `last_login_at`, calls `login_user`.

### What new users **never** see
- A "username" field (username is auto-generated or not collected for Supabase-first users; see §9).
- The legacy Flask-managed password reset flow.
- The legacy email confirmation token machinery.
- Any reference to a separate Soletrak password — Supabase owns the credential.

---

## 3. Recommended Architecture

The smallest viable architecture that delivers the target without rewriting the app:

```
   Browser                                        Soletrak (Flask)
   ─────────                                      ────────────────
   Supabase JS SDK ──── access token (JWT) ────►  /auth/supabase/bridge
                                                     │
                                                     │  verify_access_token(token)
                                                     │  ↓
                                                     │  find_app_user_by_supabase_id(sub)
                                                     │  ↓
                                                     │  if None: find_app_user_by_email(...)
                                                     │  ↓
                                                     │  if None: create app User row
                                                     │  ↓
                                                     │  link_app_user_to_supabase(...)
                                                     │  ↓
                                                     │  login_user(app_user) ── Flask-Login session ──► Browser
   Subsequent requests ───── session cookie ─────► every current_user-using route
```

### Why a bridge to Flask-Login (not a JWT-everywhere model)
- The app has 200+ direct `current_user` references (per `docs/SUPABASE_AUTH_READINESS_REVIEW.md`). Migrating all of them to a JWT-on-every-request model would be a massive surface-area change with no functional benefit during the transition.
- The bridge converts **once** per session, then the app behaves identically to today. Templates, routes, decorators, profile pages, admin checks, and `UserApiToken`-protected mobile endpoints all work without modification.
- Flask-Login's session cookie is HTTP-only, signed by `SECRET_KEY`, already battle-tested in this codebase, and the source of truth for browser auth state.
- Rolling back is a flag flip: the bridge endpoint stops issuing sessions; the legacy login path continues serving the existing user base.

### Why Supabase JS SDK in the browser (not pure server-side OAuth)
- The browser already needs to talk to Supabase for password reset / email update flows once cutover begins. Using the JS SDK consistently is simpler than mixing server-side and client-side auth.
- For SSO, the JS SDK handles the OAuth dance with Supabase as the broker — Soletrak never sees provider tokens or implements OAuth state management.
- For email/password, the JS SDK does Supabase password hashing client-side; Soletrak never sees the password.
- The bridge endpoint receives only a verified Supabase JWT. No raw passwords ever cross Soletrak's network boundary on the new path.

### Module additions (final shape)
- `routes/supabase_auth_routes.py` (new blueprint): `/auth/supabase/bridge`, `/auth/supabase/oauth-callback`, `/auth/supabase/confirm`, `/auth/supabase/signup` (UI), `/auth/supabase/onboarding` (post-OAuth profile capture).
- `services/supabase_session_bridge.py`: orchestration helper that runs the verify → find/create → link → `login_user` sequence atomically, with explicit error types for each failure mode. Pure orchestration; the existing service modules continue to handle verification and linkage.
- `templates/auth/supabase_signup.html` (new): the new signup page. Loads Supabase JS SDK and the signup widget.
- `templates/auth/supabase_oauth_onboarding.html` (new): the "tell us about you" form shown post-OAuth.
- `templates/login.html` (existing): adds Supabase entry points alongside the legacy form, behind a feature flag.
- `static/js/supabase_auth_client.js` (new): the small browser-side glue between Supabase JS SDK callbacks and the bridge POST.
- `migrations/versions/<rev>_phase3_user_columns.py` (new): nullable `password_hash`, new `last_login_at`, new `auth_provider` (see §9).

### Module changes (minimised)
- `routes/auth_routes.py`: receives **one** addition — a new `/login/legacy` alias (so the existing `/login` page can continue carrying the legacy form when JS-disabled). The existing handlers are not modified.
- `forms.py`: optional adjustments for the signup page (no removals; existing forms stay as-is).
- `decorators.py`: unchanged.
- `services/auth_resolver.py`: unchanged.
- `models.py`: three column additions to `User`; no relationship or other changes.

---

## 4. Signup Flow (brand-new users)

### 4.1 Email + password sign-up

1. **Browser visits `/signup`** (new route in the Supabase Auth blueprint). When `SUPABASE_NEW_USER_SIGNUP_ENABLED=true`, this serves the new template; else falls back to legacy `/register`.
2. **User fills the form**: email, password, first name, last name, region, marketing opt-in.
3. **Form posts client-side**: the JS handler calls `supabase.auth.signUp({email, password, options: {data: {first_name, last_name, region, marketing_opt_in}, emailRedirectTo: <site>/auth/supabase/confirm}})`. The Soletrak profile fields are passed in `options.data` so they're attached to the Supabase user as `user_metadata` — useful as a fallback if the bridge POST is interrupted.
4. **Supabase emails a confirmation link**. The page shows "check your inbox".
5. **User clicks the email link**. Supabase confirms the identity and redirects to `/auth/supabase/confirm` with an access token in the URL fragment.
6. **`/auth/supabase/confirm` page** is a thin client that extracts the token, then calls `POST /auth/supabase/bridge` with `{access_token, profile: {first_name, last_name, region, marketing_opt_in}}`.
7. **Bridge endpoint**:
   - `verify_access_token(token)` → `claims`. Reject 401 on failure.
   - `find_app_user_by_supabase_id(claims.sub)` → if found, this is a returning login. Update `last_login_at`. `login_user(app_user)`. 200.
   - `find_app_user_by_email(claims.email)` → if found, this is an existing legacy user signing up "again" with the same email via Supabase. Bridge **does not silently link** — it returns a typed error so the front-end can show "you already have a Soletrak account; please sign in with your existing username, or click here to link this Supabase identity". Linkage requires explicit user consent (see §10).
   - Else → new user. Validate the profile payload (server-side; the WTForms validator runs here). Create `User` row with `password_hash=NULL`, `email=claims.email`, profile fields from the payload, `is_email_confirmed=True` (mirroring Supabase's verification), `auth_provider='supabase_email'`. `link_app_user_to_supabase(...)`. `login_user(app_user)`. 200.
8. **Browser redirects to `/dashboard`**. End user is authenticated; `current_user` returns the new app User row.

### 4.2 SSO sign-up

1. **Browser visits `/signup`**, clicks "Continue with Google".
2. **JS calls `supabase.auth.signInWithOAuth({provider: 'google', options: {redirectTo: <site>/auth/supabase/oauth-callback}})`**.
3. **User completes Google's flow**. Supabase brokers the OAuth and redirects to `/auth/supabase/oauth-callback` with an access token.
4. **`/auth/supabase/oauth-callback`** verifies the access token via the bridge in "lookup-only" mode: if a linked app user exists, log them in immediately. Otherwise, the page redirects the user to **`/auth/supabase/onboarding`** to capture the missing profile fields (first name, last name, region, marketing opt-in). Email is pre-filled from the JWT and read-only.
5. **Onboarding form posts to `/auth/supabase/bridge`** with the access token + collected fields. Bridge creates the app User row with `auth_provider='supabase_oauth_<provider>'`, `is_email_confirmed=True`, and `password_hash=NULL`. Issues Flask-Login session.

The two-step OAuth flow (callback → onboarding → bridge) is mandatory because Supabase's JWT does not carry first/last name in a reliable form across providers (Google sometimes does; Apple intentionally does not). Capturing them on the Soletrak side keeps the user table consistent.

### 4.3 What the bridge endpoint must do atomically

The bridge runs inside one DB transaction:

```python
with db.session.begin():
    claims = verify_access_token(token)        # raises → 401
    app_user = find_app_user_by_supabase_id(claims.sub)
    if app_user is None:
        existing_by_email = find_app_user_by_email(claims.email)
        if existing_by_email is not None:
            raise ExistingLegacyUserConflict(existing_by_email.id)
        app_user = User(...)                   # validated payload
        db.session.add(app_user)
        db.session.flush()                     # get app_user.id
        link_app_user_to_supabase(app_user.id, claims.sub, source="bridge_signup")
    app_user.last_login_at = datetime.utcnow()
login_user(app_user)                           # outside the transaction
```

Any exception rolls back the partial state. `login_user` is called only after commit succeeds.

---

## 5. Login Flow (existing users)

### 5.1 Existing user using legacy path (no change)

`/login` continues to render the existing `LoginForm` (username + password). `auth_routes.login` is untouched. Submit → `User.check_password` → `login_user`. Identical to today.

### 5.2 Existing user signing in via Supabase email/password (linkage)

1. User goes to `/login`, sees Supabase entry points alongside the legacy form.
2. User clicks "Sign in with email" → Supabase JS SDK `signInWithPassword({email, password})`. The user enters their email (their Soletrak email) and **a Supabase password they have set via Supabase's password-reset flow**, OR — if they have not set one — they click "I don't have a Supabase password yet" which sends them through Supabase's "set password" link flow.
3. Supabase returns an access token.
4. Browser POSTs to `/auth/supabase/bridge`. Bridge resolves by `supabase_auth_user_id` (if previously linked) or by email (if not yet linked).
5. **First-time linkage path**: bridge finds an existing app User by email but with `supabase_auth_user_id IS NULL`. Bridge presents an explicit confirmation: "We found a Soletrak account for `<email>`. Link it to this Supabase identity?" The user confirms. Bridge then calls `link_app_user_to_supabase(...)` and proceeds.
   - This is a deliberate, explicit, one-time per user action. **The bridge never silently auto-links** — that violates the accepted resolver write-safety rule (which the bridge inherits in spirit even though it is a sanctioned writer).
   - On confirm, the link is logged to a JSONL audit file under `backups/auth/` (same shape as the linkage CLI), `source="bridge_user_consent"`.
6. Subsequent sign-ins for that user via Supabase resolve by `supabase_auth_user_id` directly — no email match, no consent step.

### 5.3 Existing user signing in via SSO

Same as 5.2 except the Supabase access token comes from an OAuth provider rather than email/password. Email match + consent remains the linkage rule.

### 5.4 Existing user prompted to migrate (optional)

After Phase 3 stabilises, a soft prompt may appear on `/dashboard` for legacy-only users encouraging them to add a Supabase password or SSO provider. This is a UX nudge, not a forced migration. Implementation is straightforward — the prompt links to `/profile/security` which exposes "Set a Supabase password" or "Connect Google" buttons that go through Supabase JS SDK.

This nudge is in scope for Phase 3b (existing-user migration) but disabled by default until the rollout flag is flipped.

---

## 6. Email/Password Flow

### 6.1 Sign-up
Covered in §4.1. Supabase owns the password and the verification email.

### 6.2 Sign-in
- New users: Supabase JS SDK + bridge.
- Existing users: legacy form OR Supabase JS SDK (after one-time linkage).

### 6.3 Password reset
Two flows, separated by user type:

| User type | Reset flow |
|---|---|
| Supabase-first (new) | Supabase's `auth.resetPasswordForEmail` + Supabase email + Supabase reset page. Soletrak is uninvolved. |
| Legacy-only existing | Existing `/reset-password-request` → itsdangerous token → `/reset-password/<token>`. Unchanged. |
| Linked existing | After linkage, the user **always** resets through Supabase. The legacy reset endpoint refuses for users with `supabase_auth_user_id IS NOT NULL` (returns a friendly "your account has been migrated; please use the Supabase reset link" message). |

The third row is a small, additive change to `routes/auth_routes.reset_password_request` and `reset_password_with_token`: a single guard at the top of each handler that checks `user.supabase_auth_user_id` and short-circuits with a flash message. No other changes to those handlers.

### 6.4 Email change
Two flows, similarly separated:

| User type | Email change flow |
|---|---|
| Supabase-first (new) | Supabase `auth.updateUser({email})` triggers Supabase's confirmation. On success, the bridge listens (or the user is re-bridged) and writes the new `email` to the app `User` row. |
| Legacy-only existing | Existing `pending_email` flow + `/confirm-new-email/<token>`. Unchanged. |
| Linked existing | Email change goes through Supabase. The legacy email-change form refuses for users with `supabase_auth_user_id IS NOT NULL`. |

Coordinating both Supabase email and app `User.email` is the only place where genuine bi-directional sync is unavoidable. The simplest model: **Supabase owns the identity email; app `User.email` is a mirror written by the bridge after each Supabase sign-in.** If the user changes their Supabase email, their next sign-in updates the app email automatically.

### 6.5 Email confirmation gate
Per the accepted decision, `User.is_email_confirmed` remains the live confirmation gate during dual-run.

- For Supabase-first users: the bridge sets `is_email_confirmed=True` from Supabase's `email_confirmed_at`. They never see the legacy confirmation flow.
- For legacy users: the existing flow remains.
- For linked existing users: their `is_email_confirmed` was already True (legacy login required it).

After full cutover (Phase 4+), `is_email_confirmed` is either retired or repurposed as a derived view of Supabase's confirmation state. Phase 3 does not retire it.

---

## 7. SSO Flow

### 7.1 Provider list

Phase 3 launches with the following providers, configured in the Supabase Auth dashboard (not in Soletrak code):

| Provider | Status | Configuration owner |
|---|---|---|
| Google | Launch | Supabase dashboard |
| Apple | Launch | Supabase dashboard |
| Email/password | Launch | Supabase dashboard |
| GitHub | Optional, post-launch | Supabase dashboard |
| Magic link | Considered, deferred | Supabase dashboard |

Providers are added/removed in Supabase Auth without code changes. Soletrak's signup/login pages render whichever providers are listed in the `SUPABASE_SSO_PROVIDERS` config (a comma-separated env var, defaulting to `google,apple`).

### 7.2 Flow (consolidated — see §4.2 for sign-up, §5.3 for sign-in)

```
Browser                Supabase            Provider          Bridge
   │                       │                  │                │
   │── signInWithOAuth ────►│                 │                │
   │                       │── redirect ─────►│                │
   │                       │                  │── consent ─────►
   │                       │◄── callback ─────│                │
   │◄── access token ──────│                  │                │
   │                                          │                │
   │── POST /auth/supabase/bridge ────────────────────────────►│
   │                                          │                │── verify_access_token
   │                                          │                │── resolve / create User
   │                                          │                │── login_user
   │◄── 200 + Flask-Login session cookie ─────────────────────────
```

### 7.3 First-time SSO requires onboarding step

The `/auth/supabase/onboarding` page (§4.2) is mandatory for first-time SSO sign-up because:
- Apple does not provide name fields after the user's first sign-in to a given app.
- Google sometimes provides them but they're not always reliable across locales.
- Soletrak needs `first_name`, `last_name`, `preferred_region`, and `marketing_opt_in` to be present on the User row.

Subsequent SSO sign-ins skip onboarding and go straight to bridge.

### 7.4 SSO for existing users (linkage)

Existing users clicking SSO go through the same bridge → email-match → explicit-consent linkage flow described in §5.3. Once linked, future SSO sign-ins are identical to the new-user path.

---

## 8. Session Handling Strategy

### 8.1 Browser sessions

- **Source of truth: Flask-Login session cookie**, signed by `SECRET_KEY`. Same as today.
- **How it gets issued for Supabase users**: the bridge endpoint calls `login_user(app_user)` after successful verification + resolution.
- **How it gets cleared**: `logout_user()` from `/logout` clears the Flask-Login cookie. Supabase JS SDK `auth.signOut()` separately clears the Supabase session in browser local storage. The Soletrak `/logout` route calls a small JS hook that does both, so a single user-initiated logout cleans both layers.
- **Session length**: unchanged from current Flask-Login defaults. Supabase access tokens expire (~1 hour) but that's irrelevant to Flask-Login session length — the JWT is only used at bridge time.
- **Session refresh**: Supabase JS SDK refreshes Supabase access tokens silently in the browser. The bridge re-verification is a one-shot at sign-in; subsequent requests use the Flask-Login cookie.

### 8.2 Mobile / API clients

`UserApiToken` continues to be the only mobile auth path. No change. `decorators.bearer_or_login_required` continues to honour both `UserApiToken` and (when `SUPABASE_AUTH_ENABLED=true`) Supabase JWTs. Phase 3 does not introduce a Supabase-issued mobile token.

### 8.3 The probe endpoint

`/admin/auth/probe` from Phase 2 stays. It remains admin-only, flag-gated, read-only — used for staging dry-runs and post-rollout health checks. No interaction with the new bridge endpoint.

### 8.4 What happens when Flask-Login + Supabase JWT are both presented

Same as Phase 2: Flask-Login session wins. The resolver's order is `current_user` first, then JWT branch. Existing logged-in users are unaffected by anything Supabase does in their tab.

---

## 9. User Table / Linkage Model Rules

### 9.1 Schema additions (Phase 3 migration)

| Column | Type | Nullable | Default | Purpose |
|---|---|---|---|---|
| `User.password_hash` | `String(256)` | **Change to nullable** | None | Supabase-first users have no app password. Existing rows retain their hash. |
| `User.last_login_at` | `DateTime` | Yes | None | Written by the bridge and by legacy login. Useful for cohort backfill and Phase 4 sunset planning. |
| `User.auth_provider` | `String(40)` | Yes | None | Tracks how each user authenticates. Values: `legacy`, `supabase_email`, `supabase_oauth_google`, `supabase_oauth_apple`, etc. Set on first bridge link. |

Single Alembic migration. No data is rewritten — `password_hash` keeps existing values; `last_login_at` and `auth_provider` land NULL for existing rows. The `password_hash` nullability change is the only constraint relaxation; widening a NOT NULL constraint is metadata-only on Postgres 12+ and a batch-rebuild on SQLite (already supported by the existing `render_as_batch=True` Flask-Migrate setup).

### 9.2 Linkage rules (extension of Phase 2)

- `supabase_auth_user_id` remains the canonical link (per accepted decision). Email is backfill-match input only.
- Sanctioned writers in Phase 3:
  1. `scripts/link_supabase_identities.py` (Phase 2 CLI; admin pre-linking).
  2. `services/supabase_session_bridge.py` (new in Phase 3; user-consent linkage and new-user creation).
- The resolver and decorator continue to be **read-only** with respect to `supabase_auth_user_id`.
- The bridge writes only on:
  - **New user creation**: `app_user = User(...); link_app_user_to_supabase(...)`.
  - **Existing user explicit consent** (§5.2 step 5): `link_app_user_to_supabase(existing.id, claims.sub, source="bridge_user_consent")`.
- The bridge **never** silently links a user found by email match. Email match returns a typed error (`ExistingLegacyUserConflict`); the front-end shows a consent prompt; only after user click does the bridge link.

### 9.3 What new vs existing user rows look like after Phase 3

| Field | New user (Supabase-first) | Existing user (legacy-only) | Existing user (linked) |
|---|---|---|---|
| `password_hash` | `NULL` | hashed | hashed (preserved as fallback) |
| `is_email_confirmed` | `True` (mirrored from Supabase) | `True` (legacy gate) | `True` |
| `pending_email` | `NULL` | possibly set | `NULL` (email change goes through Supabase) |
| `supabase_auth_user_id` | UUID | `NULL` | UUID |
| `auth_provider` | `supabase_email` or `supabase_oauth_*` | `legacy` or `NULL` | `supabase_email` or `supabase_oauth_*` (overwritten on link) |
| `username` | auto-generated (e.g. `user_<id>`) or user-chosen on signup | the existing username | unchanged |
| `last_login_at` | written by bridge on each sign-in | written by legacy login | written by whichever path was used |

`username`'s role for Supabase-first users is the most important open decision — see §15.

### 9.4 Constraint on `password_hash` nullability

The schema relaxation is irreversible-feeling but safe: existing rows keep their hashes; new Supabase-first rows land NULL. No code path reads `password_hash` for a Supabase-first user (legacy login looks up by username, which Supabase-first users may not have, and `check_password` is only called by legacy login). A defensive guard is added to `User.check_password` — if `password_hash` is NULL, return `False` regardless of input. This prevents a Supabase-first user accidentally being authenticated through legacy login if they ever try.

---

## 10. Migration Strategy from Flask-Login to Supabase-First

Phase 3 itself decomposes into four sub-phases. Production may pause between any two.

### Phase 3a — New users only (Supabase-first launch)
- Schema migration lands.
- Bridge endpoint, signup page, OAuth callback, onboarding page, JS glue all land behind `SUPABASE_NEW_USER_SIGNUP_ENABLED=false`.
- Existing `/register` still serves legacy signup. Existing `/login` still serves legacy login.
- Flip `SUPABASE_NEW_USER_SIGNUP_ENABLED=true` in production: `/signup` becomes the new user signup; `/register` redirects there. **Existing users see no change.**
- Acceptance criterion: brand-new users can sign up via email/password and via at least one SSO provider, end-to-end, into a working session.

### Phase 3b — Existing users may opt-in
- Add Supabase entry points to `/login` (alongside the legacy form) behind `SUPABASE_EXISTING_USER_LINK_ENABLED=false`.
- Add the explicit-consent linkage flow (§5.2 step 5).
- Flip `SUPABASE_EXISTING_USER_LINK_ENABLED=true`: existing users can link voluntarily.
- Add the soft prompt on `/dashboard` (also behind a flag, off by default) encouraging legacy users to add Supabase auth.
- Acceptance criterion: an existing legacy user can choose Supabase email/password or SSO at `/login`, complete the consent linkage, and from then on sign in either way.

### Phase 3c — Cohort migration of inactive users
- Use the Phase 2 linkage CLI (`scripts/link_supabase_identities.py`) extended to support `--cohort=inactive` (offline backfill).
- For users who haven't logged in for 90+ days: pre-create Supabase identities with `--send-onboarding` so they receive an email setting up Supabase access.
- This is operator-driven, not user-driven. No UI change.
- Acceptance criterion: 80%+ of all `User` rows have `supabase_auth_user_id` set.

### Phase 3d — Sunset planning
- Decide a sunset date for legacy `/login` (e.g. 6 months after Phase 3a).
- Add a deprecation banner to `/login`'s legacy form behind `LEGACY_LOGIN_DEPRECATED=false`.
- After the announced date: flip `LEGACY_LOGIN_ENABLED=false`. The legacy form still renders but submitting flashes "legacy login has been retired; please sign in with Supabase email/password or SSO". Users still in the system are forced through password reset via Supabase.
- This sub-phase is the boundary between Phase 3 and Phase 4. Phase 3 ends with the deprecation banner live and the sunset plan documented; Phase 4 is the actual code-level removal of legacy auth.

### Dual-path during transition

Throughout Phase 3a–3c, both paths serve traffic:

- **Always-on**: legacy login, `UserApiToken` mobile auth, admin-required gating, `/profile`, `/logout`.
- **Flag-on**: Supabase signup (3a+), Supabase login for existing users (3b+).
- **Mutually exclusive cases per user**: a user can have `password_hash IS NOT NULL` AND `supabase_auth_user_id IS NOT NULL` simultaneously (linked existing user). Both paths work for them. The legacy reset/email-change refuses if Supabase is linked (§6.3, §6.4).

---

## 11. Rollback and Safety Plan

### Per-flag rollback

Each sub-phase is rollback-able by flipping its feature flag:

- `SUPABASE_NEW_USER_SIGNUP_ENABLED=false` — `/signup` redirects back to legacy `/register`. Existing Supabase-first user accounts continue to work for sign-in (their rows persist; their next bridge sign-in still succeeds).
- `SUPABASE_EXISTING_USER_LINK_ENABLED=false` — `/login` no longer offers Supabase entry points. Already-linked existing users can still sign in via legacy username/password (their `password_hash` was preserved).
- `LEGACY_LOGIN_ENABLED=false` (Phase 3d only) — flipping back to `true` re-enables legacy login. No data loss.

### Per-incident rollback

If the bridge endpoint mishandles a class of user (e.g. wrong linkage logic):

1. Set `SUPABASE_NEW_USER_SIGNUP_ENABLED=false` immediately. New signups revert to legacy.
2. Set `SUPABASE_EXISTING_USER_LINK_ENABLED=false` immediately. Existing-user Supabase login is hidden.
3. Investigate. The audit JSONL files under `backups/auth/` capture every linkage write performed by the bridge.
4. Use `scripts/link_supabase_identities.py --unlink --user-id <id> --apply` to undo any incorrectly-applied linkages.

### Hard-rollback (worst case)

If the schema migration itself proves problematic:

- The migration is downgrade-safe: `password_hash` returns to NOT NULL (would fail if any row has NULL — would need to backfill from Supabase first, or hard-rollback before any Supabase-first user signs up). `last_login_at` and `auth_provider` are dropped.
- Realistically: once Phase 3a has shipped to production for >24h with at least one Supabase-first signup, the schema migration is **not** rollback-friendly. Treat the Phase 3a schema migration as a one-way door after the first Supabase-first user is created.

### Admin emergency

The Phase 2 admin recovery procedure remains: legacy `/login` is the documented break-glass admin path throughout Phase 3a–3c. The `make_admin.py` CLI script is unchanged.

### Mobile / API safety

`UserApiToken` and `decorators.bearer_or_login_required` are not modified in Phase 3. The format-disambiguation policy from Phase 2 stays. Mobile step-sync is untouched.

---

## 12. Environment / Secret Requirements

In addition to the Phase 2 vars (`SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `SUPABASE_AUTH_ENABLED`):

| Variable | Purpose | Default | Required for production? |
|---|---|---|---|
| `SUPABASE_NEW_USER_SIGNUP_ENABLED` | Flag for Phase 3a | `false` | No (default off until rollout) |
| `SUPABASE_EXISTING_USER_LINK_ENABLED` | Flag for Phase 3b | `false` | No (default off until rollout) |
| `SUPABASE_SSO_PROVIDERS` | Comma-separated list (e.g. `google,apple`) | `google,apple` | Yes (drives the signup/login UI) |
| `SUPABASE_BRIDGE_REDIRECT_URL` | Where Supabase redirects browsers post-confirmation/OAuth (must match Supabase dashboard config) | `<APP_BASE_URL>/auth/supabase/oauth-callback` | Yes |
| `LEGACY_LOGIN_ENABLED` | Master kill switch for the legacy username/password form | `true` | Yes (default on; flipped off only at Phase 3d sunset) |
| `LEGACY_LOGIN_DEPRECATED` | Renders deprecation banner on `/login` | `false` | No |

Notes:
- All three new feature flags default to **safe**: signup/link off until explicitly enabled; legacy login always on.
- `SUPABASE_SSO_PROVIDERS` is read at request time so adding/removing providers is a config-only deploy.
- The Supabase dashboard side requires:
  - Email auth provider enabled (with email confirmation required).
  - Google OAuth provider configured with the production redirect URL.
  - Apple OAuth provider configured with the production redirect URL.
  - The asymmetric signing key set is published at the JWKS endpoint (already validated in Phase 2 rehearsal).
  - Email templates (confirmation, password reset, magic link) styled and tested.

The operational rule from `docs/DECISIONS.md` continues to apply: **`SUPABASE_AUTH_ENABLED` must remain `true` for the Phase 3 rollout** (the bridge calls `verify_access_token`, which requires the flag). This is the **only** change to the Phase 2 operational rule: Phase 3 turns the flag on permanently in production. The flag's role transitions from "kill switch hidden behind a probe window" to "always on, gates only the JWT branch of the decorator and the bridge".

---

## 13. Staged Rollout Plan

### Stage 0 — pre-flight (all environments)
- Phase 2 probe rehearsal complete in production (separate exercise from staging).
- All admins linked in production via the Phase 2 CLI.
- All Phase 3 PRs merged to `main` with feature flags **off**.

### Stage 1 — schema migration
- Land the Phase 3 migration (`password_hash` nullable, `last_login_at`, `auth_provider`) to staging then production. No code change is observable.
- Run `auth_audit_users.py` after the migration applies; expected: clean (Phase 2 C10 is `info`-only; new columns don't affect any check).

### Stage 2 — staging Phase 3a
- Set `SUPABASE_AUTH_ENABLED=true` in staging.
- Set `SUPABASE_NEW_USER_SIGNUP_ENABLED=true` in staging.
- Configure Supabase Auth dashboard for staging: email + Google + Apple.
- Internal team runs through 5+ end-to-end signups: 2× email/password, 2× Google, 1× Apple. Verify:
  - User row created with correct fields, `password_hash IS NULL`, `is_email_confirmed=true`, `auth_provider` set, `supabase_auth_user_id` set.
  - Flask-Login session works; `current_user` returns the new row; `/profile`, `/dashboard`, `/my/sneakers/...` reachable.
  - Sign-out clears both Flask-Login cookie and Supabase JS session.
  - Mobile token sync still works for existing users.
  - Legacy `/login` still works for existing users.

### Stage 3 — production Phase 3a
- Set `SUPABASE_AUTH_ENABLED=true` in production (this is the durable on-state, replacing the time-boxed probe windows).
- Set `SUPABASE_NEW_USER_SIGNUP_ENABLED=true` in production.
- Communicate the change in the next user-facing release note.
- Monitor:
  - First 24 hours: signup error rate, bridge endpoint 5xx rate, JWKS lookup failures.
  - Audit JSONL volume under `backups/auth/`.
  - Mobile step-sync error rate (must be unchanged).

### Stage 4 — staging Phase 3b
- Set `SUPABASE_EXISTING_USER_LINK_ENABLED=true` in staging.
- Internal team runs the linkage flow as existing users: 2 admins, 2 non-admin internal accounts. Verify the explicit-consent prompt, the audit log entry, that the legacy reset endpoint refuses for now-linked users.

### Stage 5 — production Phase 3b
- Set `SUPABASE_EXISTING_USER_LINK_ENABLED=true` in production.
- Add the optional `/dashboard` migration nudge (off by default; flip on after a week of clean Phase 3b traffic).

### Stage 6 — Phase 3c (cohort backfill)
- Operator-driven; multi-week. Run the linkage CLI in cohorts:
  - Active users (logged in within 30 days): pre-link with onboarding email.
  - Semi-active (30–90 days): pre-link with onboarding email + reminder.
  - Inactive (>90 days): pre-link without onboarding (they can recover via Supabase password reset whenever they return).

### Stage 7 — Phase 3d (sunset planning)
- Add deprecation banner; announce the sunset date publicly.
- This stage's exit is Phase 4 — out of scope here.

---

## 14. Acceptance Criteria

### Phase 3a (new users)
- [ ] Schema migration applied to production; `auth_audit_users.py` returns exit code 0.
- [ ] `/signup` page renders email/password form + at least Google + Apple SSO buttons.
- [ ] End-to-end email/password signup: Supabase confirmation email → bridge → Flask-Login session → `/dashboard` reachable.
- [ ] End-to-end Google signup: OAuth → onboarding → bridge → Flask-Login session → `/dashboard` reachable.
- [ ] End-to-end Apple signup: same.
- [ ] New `User` row has `password_hash IS NULL`, `is_email_confirmed=True`, `auth_provider` set, `supabase_auth_user_id` set, `last_login_at` written.
- [ ] Existing `/register` redirects to `/signup` when the flag is on (no orphan codepath).
- [ ] Legacy `/login` still works for existing users (no regression).
- [ ] Mobile `UserApiToken` step sync still works (regression test passes).
- [ ] Admin login + admin pages still reachable for pre-linked admins via both legacy and Supabase paths.
- [ ] Audit JSONL files under `backups/auth/` show one row per bridge link.
- [ ] No Flask-Login cookie issued when bridge verification fails.
- [ ] Bridge endpoint refuses email-match for an existing legacy user without explicit consent (returns typed error; UI shows confirmation prompt).
- [ ] Supabase password reset works for new users (Supabase-side; no Soletrak involvement).
- [ ] Logout clears both Flask-Login cookie and Supabase JS session.

### Phase 3b (existing users opt-in)
- [ ] `/login` shows Supabase entry points alongside the legacy form.
- [ ] Existing legacy user can complete the email/password Supabase linkage flow including the consent prompt.
- [ ] Existing legacy user can complete the SSO linkage flow.
- [ ] After linkage, `user.supabase_auth_user_id` is set; `user.password_hash` is preserved; both legacy and Supabase login paths work.
- [ ] Legacy `/reset-password-request` refuses for users with `supabase_auth_user_id IS NOT NULL` (flashes the documented message; does not 500).
- [ ] Legacy email-change form refuses for the same.

### Phase 3c (cohort backfill)
- [ ] ≥80% of `User` rows have `supabase_auth_user_id` set.
- [ ] No admin lockouts reported.
- [ ] Audit JSONL covers every backfill link.

### Phase 3d (sunset planning)
- [ ] Deprecation banner live on `/login`.
- [ ] Sunset date documented in `docs/DECISIONS.md`.
- [ ] Phase 4 implementation plan drafted.

---

## 15. Open Decisions Still Needing Resolution

These decisions must be locked before each respective sub-phase begins. They are not blockers for drafting Phase 3 PRs but are blockers for shipping them.

### Must lock before Phase 3a

1. **`username` for Supabase-first users.** Three options:
   - (a) Auto-generate `user_<id>` and treat `username` as a hidden internal handle. Profile page allows changing it.
   - (b) Collect `username` on signup as a required field.
   - (c) Drop `username` from the User model entirely (deeper change; not recommended in Phase 3).
   - **Recommendation:** (a) for simplicity. Username becomes a display handle that defaults to a generated value.
2. **Email-as-identity uniqueness.** Today `email` is `unique=True` but case-sensitivity is enforced only in code. Phase 3 should add a case-insensitive uniqueness guarantee at the DB level — likely a functional unique index on `lower(email)`. Decide whether to land this in the Phase 3 schema migration or as a follow-up.
3. **Supabase JWT issuer and audience claim hardening.** Phase 2 deliberately deferred this. Phase 3's bridge accepts JWTs from real end users, not just admin probes — claim hardening is appropriate. Decide:
   - Required `iss`: `<SUPABASE_URL>/auth/v1`.
   - Required `aud`: `authenticated`.
   - Both in `verify_access_token` options.
4. **Onboarding profile fields for SSO.** Are first/last name still required? Region? Or are these soft prompts the user can fill in later from `/profile`? Affects the `/auth/supabase/onboarding` UX.
5. **Marketing opt-in language and placement** for the new signup page. Compliance review owner.

### Must lock before Phase 3b

6. **Existing-user consent prompt copy.** What does "we found a Soletrak account; link it?" actually say. Affects user trust during the linkage moment.
7. **Soft-prompt UX on `/dashboard` for legacy-only users.** Wording, placement, dismissibility.
8. **Refusal copy for legacy reset / email-change after link.** Users who linked and then click the legacy reset link need clear next-step guidance.

### Must lock before Phase 3c

9. **Cohort definitions** — how active is "active"? 30/60/90/365 day thresholds for the CLI.
10. **Onboarding email template** for backfilled users. Currently the linkage CLI's `--send-onboarding` triggers Supabase's password-reset email; for backfill we may want a Soletrak-branded "we've moved your account to a new login system" email instead. Decide whether to use Supabase's default or build a Soletrak-side opt-in alongside.

### Must lock before Phase 3d (sunset)

11. **Sunset date** for legacy `/login`.
12. **What happens to legacy-only users at sunset.** Are they force-migrated via a one-time email, or do they hit a "your account is now Supabase-only; please reset your password" page on next login? Pick one and document the user-visible flow.
13. **Long-term mobile/`UserApiToken` strategy.** Phase 3 keeps it as-is, but Phase 4 will need a decision: keep indefinitely, migrate to Supabase-issued tokens, or sunset in favour of native session bridges.
14. **Removal of `password_hash`, `is_email_confirmed`, `pending_email`, and itsdangerous tokens.** A separate Phase 4 schema cleanup migration, or kept indefinitely as legacy?

### Lower-impact but recurring

15. **JWKS cache lifespan.** Phase 2 picked 3600 s. Revisit if Supabase rotates signing keys more aggressively.
16. **Bridge endpoint rate limiting.** A new HTTP attack surface; consider a per-IP throttle to defend against credential-stuffing replays.
17. **Logging volume.** The audit JSONL files grow at signup rate. Decide rotation policy (size-based / time-based / log-only-on-error).

---

## Appendix: Phase 3 PR breakdown (recommended)

PRs are ordered to land independently. Each is reversible by feature flag.

1. **PR 1 — Phase 3 schema migration**: Alembic migration adding `last_login_at`, `auth_provider`, and relaxing `password_hash` to nullable; model updates. No live consumers yet.
2. **PR 2 — Session bridge service**: `services/supabase_session_bridge.py` plus typed errors. Pure function, unit-tested with monkey-patched verifier.
3. **PR 3 — Bridge endpoint + Supabase Auth blueprint**: `routes/supabase_auth_routes.py` with `/auth/supabase/bridge`, `/auth/supabase/oauth-callback`, `/auth/supabase/confirm`, `/auth/supabase/onboarding`. Behind `SUPABASE_NEW_USER_SIGNUP_ENABLED`.
4. **PR 4 — Signup UI**: `templates/auth/supabase_signup.html` + Supabase JS client glue. Page returns 404 when flag off.
5. **PR 5 — `/register` redirect**: when `SUPABASE_NEW_USER_SIGNUP_ENABLED=true`, the legacy `/register` redirects to `/signup`. One-line change to `auth_routes.register`.
6. **PR 6 — Linked-user reset/email-change guards**: small additive change to `auth_routes.reset_password_request`, `reset_password_with_token`, profile email-change endpoints. Guards in front of existing handlers; no changes to handler bodies.
7. **PR 7 — Existing-user Supabase login UI**: extends `templates/login.html` with Supabase entry points behind `SUPABASE_EXISTING_USER_LINK_ENABLED`.
8. **PR 8 — Linkage consent flow**: bridge handling for `ExistingLegacyUserConflict` + the consent confirmation page.
9. **PR 9 — Cohort backfill CLI extension**: extend `scripts/link_supabase_identities.py` with `--cohort` filtering.
10. **PR 10 — Deprecation banner + sunset announcement**: cosmetic Phase 3d.
11. **PR 11 — Doc updates**: `docs/MODULE_MAP.md`, `docs/AI_CONTEXT.md`, `docs/DECISIONS.md` recording the locked decisions from §15 as they're made.

PRs 1, 2, 6 are low-risk and can land in any order. PRs 3, 4, 5 are the user-visible Phase 3a slice. PRs 7, 8 are Phase 3b. PR 9 is Phase 3c. PRs 10, 11 are continuous through Phase 3.

The single guiding principle through Phase 3, inherited from Phase 1 and Phase 2: **keep the legacy path working until we have evidence the Supabase path works under realistic load. Roll forward by flag flips, not by destructive changes.**
