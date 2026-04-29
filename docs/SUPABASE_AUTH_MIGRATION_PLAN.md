# Supabase Auth Migration Plan

This document is the planning reference for moving Soletrak from the current Flask-managed authentication model to Supabase Auth.

Current platform baseline:

- Soletrak now runs on Supabase Postgres project `sjwdvsefjlflgavshiyy`.
- Flask remains the live backend and auth/session path.
- Flask-Login, app-owned `user` records, admin checks, and `user_api_token` flows are still active.
- Supabase Auth is planned next, but is not implemented yet.
- SendGrid/reset-email delivery remains deferred because Supabase Auth is expected to replace that path.

## 1. Executive Summary

Soletrak should move to Supabase Auth to reduce custom identity risk and shift core authentication concerns to a managed provider: password handling, email verification, password reset, session/token issuance, and future social login or mobile auth support.

What remains live today:

- Flask-Login browser sessions.
- App-managed password hashes on `user.password_hash`.
- App-managed email confirmation and password reset tokens using `itsdangerous`.
- App-owned `user` table as the account/profile/domain anchor.
- Flask-enforced admin checks through `User.is_admin`.
- Hashed bearer tokens in `user_api_token` for mobile/API step sync.

This migration should replace core identity and credential flows, not the application user/profile model. The `user` table should remain as Soletrak’s app-owned profile/account table for roles, admin status, preferences, collection ownership, wishlist ownership, API tokens during transition, and other domain relationships.

Recommended direction: phased dual-run with explicit identity linkage, followed by controlled cutover. Avoid a big-bang auth replacement.

## 2. Current Auth/Account Architecture

### Flask-Login Session Model

- Login is handled in `routes/auth_routes.py`.
- `login_user(user)` creates the browser session.
- `logout_user()` ends the browser session.
- Protected routes use `@login_required` and `current_user`.
- Flask-Login loads users by integer `user.id`.

### App-Owned `user` Table

The `user` table is currently the account source of truth.

Important fields:

- `id`: integer primary key used by relationships and Flask-Login.
- `username`: unique, required.
- `email`: unique, required.
- `password_hash`: required, app-managed password hash.
- `first_name`, `last_name`: required profile fields.
- `marketing_opt_in`: app-owned preference.
- `pending_email`: unique nullable field used during email-change confirmation.
- `is_email_confirmed`: current app-managed email confirmation flag.
- `is_admin`: app-owned admin flag.
- `preferred_currency`, `preferred_region`, `timezone`: app-owned preferences.

Important relationships:

- `User.sneakers`: collection ownership.
- `User.wishlist`: many-to-many release wishlist ownership.
- `User.api_tokens`: hashed mobile/API token ownership.
- Release ingestion metadata can reference `ingested_by_user_id`.

### Password Reset / Email Confirmation

Current app flows use signed `itsdangerous` tokens, not database-stored reset tokens.

- Password reset token: `User.get_reset_password_token()` and `User.verify_reset_password_token()`.
- Registration email confirmation token: `User.get_email_confirmation_token()` and `User.verify_email_confirmation_token()`.
- Pending email-change token: `User.get_confirm_new_email_token()` and `User.verify_confirm_new_email_token()`.

Password-reset token generation/verification has already been validated. Full SendGrid/reset-email delivery was deferred because Supabase Auth is expected to replace or reduce this path.

### Admin/Auth Checks

- `decorators.admin_required` checks `current_user.is_authenticated` and `current_user.is_admin`.
- Admin release, CSV import, market refresh, news, and destructive controls depend on Flask-side admin checks.
- These checks are app authorization, not core identity, and should remain app-owned after Supabase Auth.

### `user_api_token` Behaviour

- Tokens are generated in `services/api_tokens.py`.
- Plaintext token is shown once.
- SHA-256 hash is stored in `UserApiToken.token_hash`.
- Tokens belong to `user.id`.
- Revocation uses `revoked_at`.
- `decorators.api_token_or_login_required` accepts either bearer-token auth or logged-in browser session and sets `g.api_user`.

These mobile/API tokens are not Supabase Auth tokens today. They should coexist during the first Supabase Auth rollout unless a separate mobile auth migration is explicitly designed.

### Profile/Account Flows Depending On `user`

Current profile/account behaviour depends heavily on app-owned `User` fields:

- username update
- first/last name update
- preferred region/currency update
- marketing opt-in update
- email-change flow via `pending_email`
- API token create/revoke
- admin access
- collection, rotation, wishlist, steps, exposure, and health ownership

## 3. Desired Target State

Supabase Auth should become the primary authentication provider.

Target responsibilities:

- Supabase Auth owns identity, credentials, password reset, email verification, and primary session/token issuance.
- Soletrak keeps `public.user` as an app-owned profile/account table.
- Each app `user` row links to exactly one Supabase Auth identity once migration is complete.
- Flask remains the main backend and continues to enforce app authorization.
- Admin status remains in app data (`user.is_admin`) unless a later role-system redesign is intentionally chosen.
- Browser requests are authenticated by Supabase-issued session/JWT material that the Flask backend verifies.
- Mobile/API auth is reviewed separately; existing `user_api_token` can remain during transition.

Expected target flow:

- Signup: Supabase Auth creates the auth identity; Flask creates or links the app `user` row.
- Login: Supabase Auth authenticates; Flask resolves the linked app user and establishes backend request context.
- Logout: Supabase Auth session is cleared and any Flask compatibility session is cleared.
- Password reset: Supabase Auth email/reset flow replaces app-managed reset tokens.
- Email verification: Supabase Auth email verification replaces app-managed confirmation tokens for identity email.
- Profile pages: continue to update app-owned fields, with email updates coordinated through Supabase Auth.

## 4. Identity Linkage Strategy

### Existing Rows

Existing `user` rows must be preserved. They own collection data, wishlist data, API tokens, preferences, admin state, and app history.

The migration must link existing app users to Supabase Auth users without changing `user.id` or breaking foreign keys.

### Possible Linkage Keys

Option: email-only linkage

- Pros: simple and already present.
- Cons: fragile if emails change, case sensitivity differs, or duplicate/pending email states exist.
- Verdict: useful for backfill matching, not sufficient as the canonical long-term link.

Option: add `supabase_auth_user_id` to `user`

- Pros: stable UUID relationship to Supabase Auth identity; clear lookup path; supports email changes.
- Cons: requires schema migration and careful backfill.
- Verdict: recommended canonical linkage.

Option: separate migration/linkage table

- Pros: reversible, can track migration status/history.
- Cons: more moving parts; app still needs a direct efficient lookup.
- Verdict: useful as a temporary audit table if needed, but not the only long-term link.

### Recommended Canonical Linkage Model

Add a nullable `supabase_auth_user_id` column to the app-owned `user` table.

Recommended shape:

- Type: Postgres UUID where practical, or string if implementation compatibility is simpler.
- Nullable during transition.
- Unique index once populated for migrated users.
- Eventually required only after legacy Flask-auth-only users are fully retired.
- Keep `user.email` as app/profile data, but treat Supabase Auth email as the auth-owned identity email after cutover.

### Avoiding Duplicate Or Orphaned Accounts

Rules:

- Do not create a new app `user` row if an existing `user.email` matches the authenticated Supabase identity and has no `supabase_auth_user_id`.
- Never link one Supabase Auth identity to multiple app users.
- Never link one app user to multiple Supabase identities.
- Normalize email case for matching and audit existing data before migration.
- Treat `pending_email` rows as migration edge cases; do not silently link to pending emails.
- Admin users should be linked and tested before broad rollout to avoid lockout.

### Existing Users Before Rollout

Recommended handling:

- Pre-audit existing users by email, pending email, confirmation status, and admin status.
- Create Supabase Auth users for existing app users where possible.
- Link `user.supabase_auth_user_id` after successful creation/match.
- Force password reset / magic-link onboarding if existing password hashes cannot or should not be imported.
- Keep old Flask login available during the transitional period.

## 5. Migration Options

### Option A: Big-Bang Replacement

Replace Flask auth with Supabase Auth in one release.

Pros:

- Shortest period of dual-auth complexity.
- Clear target state quickly.

Cons:

- High lockout risk.
- Harder rollback.
- Existing sessions break immediately.
- More dangerous for admin access and mobile/API flows.

Operational risk: high.

Recommendation: do not use unless the user base is tiny, downtime is acceptable, and rollback has been fully rehearsed.

### Option B: Phased Dual-Run

Introduce Supabase Auth while keeping Flask auth paths available during transition.

Pros:

- Lower lockout risk.
- Allows admin-first and limited rollout.
- Supports rollback to known Flask auth.
- Allows `user_api_token` to continue while browser auth changes.

Cons:

- Requires careful request/session handling.
- Temporary complexity while both systems exist.
- Needs clear precedence rules when both auth states are present.

Operational risk: medium.

Recommendation: preferred approach for Soletrak.

### Option C: Progressive Linkage On Next Login

Users link to Supabase Auth progressively as they next authenticate.

Pros:

- Avoids one-time migration pressure.
- Handles inactive users later.
- Can reduce forced reset friction.

Cons:

- Long tail of unlinked users.
- More complex support/debugging.
- Old Flask auth stays alive longer.

Operational risk: medium.

Recommendation: useful as part of the phased approach, but not sufficient alone for admin and early critical users.

## 6. Recommended Migration Approach

Use a phased dual-run migration with explicit pre-linking for admins and active users, then progressive linkage for the long tail.

Why this is best for Soletrak:

- The app-owned `user` table is deeply connected to collection, wishlist, admin, API token, preference, and ingestion data.
- Flask auth is currently working and should remain as fallback during transition.
- Supabase Auth is the next platform step, but not urgent enough to justify a hard switch.
- Legacy email delivery is intentionally deferred, so Supabase Auth can replace that investment cleanly.

Recommended staged plan:

1. Preparation
   - Audit current users, emails, pending emails, admin users, and API tokens.
   - Decide email normalization rules.
   - Decide whether existing users get forced password reset, magic link, or imported identities.

2. Schema/app readiness
   - Add nullable `supabase_auth_user_id` to `user`.
   - Add a unique index for non-null Supabase auth IDs.
   - Add backend helper/service for resolving Supabase identity to app user.

3. Identity backfill/linking
   - Link admin users first.
   - Link test/internal users next.
   - Link active users using email match or controlled invite/reset.

4. Limited rollout
   - Enable Supabase Auth login for internal/admin users.
   - Keep Flask login fallback.
   - Verify profile, admin, collection, wishlist, API token, and release/admin flows.

5. Full cutover
   - Make Supabase Auth the primary login/signup/reset path.
   - Preserve app `User` resolution and authorization.
   - Keep old Flask auth available behind a rollback flag or controlled fallback until stable.

6. Legacy retirement
   - Retire Flask password login only after stability is proven.
   - Remove or disable legacy reset/email confirmation routes only after Supabase Auth equivalents are confirmed.
   - Keep app-owned `User` and app authorization.

## 7. Backend And Schema Implications

Likely implementation needs:

- Add `User.supabase_auth_user_id`.
- Add a unique index/constraint for `supabase_auth_user_id` once non-null values are authoritative.
- Add Supabase Auth configuration environment variables.
- Add backend JWT/session verification.
- Add a service such as `services/supabase_auth_service.py` or similar to avoid spreading provider logic across routes.
- Update login/register/logout/reset/email-confirmation routes to support the target flow.
- Update tests for hybrid and target auth states.

Admin checks:

- Keep `User.is_admin` as the app-owned admin source.
- Do not rely only on Supabase Auth metadata for admin authorization.
- Ensure admin users are linked and tested before rollout.

API tokens:

- Keep `UserApiToken` during the first Supabase Auth transition.
- Continue mapping tokens to app `user.id`.
- Decide later whether mobile clients should move to Supabase-issued tokens.
- Avoid breaking step sync while browser auth changes.

Profile/account data:

- Keep preferences, names, marketing opt-in, admin status, collection ownership, wishlist ownership, and API token management in the app DB.
- Coordinate email changes with Supabase Auth once Supabase owns identity email.

## 8. User-Facing Flow Impacts

Signup:

- Target: Supabase Auth creates the identity; Flask creates/links app `user`.
- App still needs first name, last name, username if retained, region/currency, and marketing preference.

Login:

- Target: Supabase Auth authenticates; Flask resolves linked app `user`.
- During transition, old Flask login may remain available as fallback.

Logout:

- Must clear Supabase session and any Flask compatibility session.

Password reset:

- Target: Supabase Auth reset flow.
- Current Flask token logic can be retired after cutover stability.

Email verification:

- Target: Supabase Auth verification.
- `User.is_email_confirmed` may become legacy, derived, or app-specific depending on final design.

Profile/account pages:

- Continue to edit app-owned fields.
- Email updates must go through Supabase Auth or a coordinated app/Supabase flow.

Admin access:

- Must continue to depend on `User.is_admin`.
- Admins should be migrated first and tested before any public rollout.

Existing users:

- Should not lose collection, wishlist, token, preference, or admin data.
- May need a password reset/magic-link onboarding step if passwords are not migrated.

## 9. Risk Register

| Risk | Impact | Mitigation |
|---|---|---|
| Duplicate users | Collection/wishlist/account split | Use `supabase_auth_user_id` unique linkage; pre-audit emails; block app-user creation when an unlinked email match exists. |
| Orphaned auth users | Login succeeds but no app data | Require app-user resolution after Supabase login; show safe error and log if no linked app user exists. |
| Broken sessions | Users cannot stay logged in | Test browser session lifecycle; keep Flask fallback during rollout. |
| Admin lockout | Admin cannot manage releases/users | Migrate/admin-test admins first; keep emergency Flask admin fallback until cutover is proven. |
| Token incompatibility | Mobile step sync breaks | Keep `UserApiToken` during phase one; do not force mobile clients onto Supabase tokens immediately. |
| Email/provider behaviour differs | Reset/verification links fail | Validate Supabase email templates, redirect URLs, and staging/prod domains before rollout. |
| Rollback complexity | Users linked in Supabase but app reverts | Keep app `User` data authoritative; do not delete password hashes until legacy fallback is retired. |
| Email change conflicts | User links to wrong identity | Treat `pending_email` as an edge case; use current `email`, not pending email, for initial matching unless manually reviewed. |
| RLS/policy mistakes | Data access failures or leakage | Do not introduce broad RLS/direct client DB access in the auth cutover unless separately designed and tested. |

## 10. Rollout And Rollback Plan

Before rollout, test:

- Supabase Auth signup/login/logout in staging.
- Existing-user linking.
- Admin login and admin-only pages.
- Profile update flows.
- Password reset and email verification links.
- API token create/revoke.
- Mobile/API token-authenticated step sync.
- Collection, wishlist, release admin, and CSV import flows after Supabase login.

Limited rollout:

- Start with one or more internal non-admin users.
- Then migrate/admin-test admin users.
- Then expand to a small active-user cohort.
- Keep Flask auth fallback available until the full cutover is stable.

Full cutover conditions:

- Admin users can log in and perform admin actions.
- Existing users can link/login without duplicate accounts.
- Password reset/email verification works through Supabase.
- Profile/account flows work.
- API token flows still work.
- No unexplained auth/session errors in logs.

Rollback if Supabase Auth launch fails:

- Disable Supabase Auth login/signup path in the app.
- Re-enable Flask login as primary.
- Keep `supabase_auth_user_id` values in place for later retry.
- Do not delete app `password_hash` or legacy token methods until rollback is no longer needed.
- Preserve any Supabase Auth users created during the failed rollout for reconciliation.

## 11. Recommended Implementation Phases

### Phase 1: Auth Design And Schema Linkage Prep

- Finalize identity linkage rules.
- Add nullable `supabase_auth_user_id` to `user`.
- Add indexes/uniqueness rules.
- Add migration and tests.

### Phase 2: Backend Integration Skeleton

- Add Supabase Auth config.
- Add backend token/session verification helper.
- Add app-user resolution by Supabase auth ID.
- Keep current Flask auth routes untouched as fallback.

### Phase 3: User Migration/Linking

- Audit current users.
- Link internal/admin users first.
- Add management or script path for linking existing users safely.
- Decide forced-reset/magic-link behaviour for existing users.

### Phase 4: Rollout And Cutover

- Enable Supabase Auth for limited cohort.
- Validate admin/profile/collection/API-token flows.
- Promote Supabase Auth to primary login/signup/reset path.
- Keep controlled Flask fallback during stabilization.

### Phase 5: Legacy Auth Cleanup

- Retire old Flask password login only after stability.
- Retire app-managed reset/email confirmation flows after Supabase equivalents are proven.
- Decide whether `password_hash`, `is_email_confirmed`, and `pending_email` become legacy fields, are retained for fallback, or are removed in a later migration.

## 12. Open Questions / Decisions Needed

- Should existing users be pre-created in Supabase Auth, or linked progressively on next login?
- Should existing users be forced through Supabase password reset/magic link, or should passwords be imported if Supabase supports the required hash path safely?
- Should `supabase_auth_user_id` be a native UUID column or string for compatibility?
- What is the canonical source for display/email after cutover: Supabase Auth email, app `user.email`, or synchronized copies?
- What should happen to `is_email_confirmed` after Supabase Auth owns email verification?
- How long should Flask password login remain available as fallback?
- Should mobile/API clients continue using `UserApiToken` indefinitely, or move to Supabase-issued tokens later?
- Which environment variables and redirect URLs are required for staging and production Supabase Auth?
- Is RLS still deferred after auth cutover, or should any user-owned tables get policies in the same phase?
- What is the emergency admin access procedure if Supabase Auth has an outage or misconfiguration?
