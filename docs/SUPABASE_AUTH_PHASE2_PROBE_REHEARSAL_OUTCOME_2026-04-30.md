# Supabase Auth Phase 2 probe rehearsal outcome — 2026-04-30

A point-in-time record of the Phase 2 admin probe rehearsal, executed end-to-end against a Supabase Postgres + Supabase Auth staging target. This document captures what was validated, what failed and was fixed, what remains in scope only for Phase 3, and the security follow-ups that must close out the session.

References:
- `docs/SUPABASE_AUTH_PHASE2_IMPLEMENTATION_PLAN.md` — overall Phase 2 plan and §6 admin-safe rollout sequence.
- `docs/SUPABASE_AUTH_READINESS_REVIEW.md` — pre-implementation gap analysis.
- `docs/DECISIONS.md` — accepted Phase 2 decisions (`SUPABASE_AUTH_ENABLED=false` operational rule, bearer-token collision policy, write-safety rule, admin-only rollout, etc.).

## Environment

- Local Flask app was pointed at the **staging Supabase Postgres project `mizyioplztuzycipfdsd`** for the rehearsal.
- The corresponding **Supabase Auth project used for identity verification was `mizyioplztuzycipfdsd`** (same project ref). The JWKS endpoint consulted by the verifier was `https://<project>.supabase.co/auth/v1/.well-known/jwks.json`.
- Baseline operational posture: `SUPABASE_AUTH_ENABLED=false`. This is the steady-state value per the accepted decision in `docs/DECISIONS.md` and is enforced by the default in `Config`.
- The flag was **temporarily set to `true`** only for the duration of the controlled probe window, then **returned to `false`** before the rehearsal closed. No version-controlled file was changed.
- Production runtime project (`sjwdvsefjlflgavshiyy`) was **not touched**; this rehearsal exercised the staging target only.

## Pre-flight checks

Performed in a staging-aligned shell (no values printed in full; presence checks only):

- `DATABASE_URL` confirmed pointing at the staging Postgres project.
- `SUPABASE_URL` — set.
- `SUPABASE_SERVICE_ROLE_KEY` — set.
- `SUPABASE_JWT_SECRET` — set.
- `SUPABASE_AUTH_ENABLED` — `false` (baseline).
- `user.supabase_auth_user_id` column present in staging Postgres (Phase 1 migration `b3c4d5e6f7a8` applied).
- Admin users present in staging:
  - `BenTinson`
  - `BenTin`

Step 1 C/D/E gate: **PASS** in the staging shell.

## Admin linkage

Linkage performed via `scripts/link_supabase_identities.py` per the accepted operational rule (dry-run by default, `--apply` required, `--admins-only` for Phase 2 scope, audit-logged JSONL).

- **Dry-run.** `python scripts/link_supabase_identities.py --admins-only` produced a clean report:
  - candidates: **2**
  - blockers: **0**
- **Apply.** `python scripts/link_supabase_identities.py --admins-only --apply` linked both admins successfully:
  - linked: **2**
  - errors: **0**
- **Audit trail.** A JSONL audit row was appended under `backups/auth/` for each link action (`action: "link"`, `app_user_id`, `email`, `supabase_uuid`, `dry_run: false`, `source: "cli"`). The file path was reported in the CLI output.

Both admin app users now have `user.supabase_auth_user_id` populated. Linkage was performed with `SUPABASE_AUTH_ENABLED=false` in effect, so request handling was unaffected by the linkage step itself.

## Flag-off baseline

With `SUPABASE_AUTH_ENABLED=false`:

- `GET /admin/auth/probe` returned **HTTP 404** as expected.
- The endpoint is hidden from any environment without the flag deliberately set, defending against accidental enablement.

## Probe validation

The flag was set to `true` for a controlled probe window, then exercised against the staging app:

- **Flask-Login session, no bearer JWT.** A logged-in admin hitting `/admin/auth/probe` without an `Authorization` header returned **HTTP 200** with body `{ ok: true, via: "flask_login", user_id: <admin.id>, is_admin: true, supabase_user_id: null }`. Confirms admin gating and the no-JWT default path.
- **Linked admin Supabase JWT — initial failure.** Presenting a real Supabase-issued access token for a linked admin returned **HTTP 401** with the error string `"The specified alg value is not allowed"`. Investigation showed the token header contained `alg=ES256` (Supabase's current asymmetric default), but the verifier still pinned `algorithms=["HS256"]` and consulted only `SUPABASE_JWT_SECRET`. The legacy symmetric path was the only one wired in at that point.
- **Root cause.** The Phase 2 verifier shipped with HS256-only support. Supabase Auth's current standard is asymmetric signing (ES256/RS256) keyed by the project's published JWKS at `<SUPABASE_URL>/auth/v1/.well-known/jwks.json`. Real staging tokens are ES256 and could never have verified through the HS256-only path.
- **ES256/JWKS verifier follow-up.** A small, scoped fix was implemented and merged: `services/supabase_auth_service.verify_access_token` now dispatches on the token's `alg` header. ES256/RS256 routes to `PyJWKClient` (cached per Supabase URL) against the JWKS endpoint; HS256 continues to use `SUPABASE_JWT_SECRET`. Algorithm allowlist `{HS256, ES256, RS256}` defends against alg-confusion. `cryptography==47.0.0` was pinned in `requirements.txt`. No request-path changes; the resolver, decorator, and probe consult `verify_access_token` unchanged. New unit tests exercise the ES256 path with a generated key pair and a monkey-patched JWKS client; an end-to-end ES256 test was added to the probe suite. Full pytest stayed green (345 passed).
- **Linked admin Supabase JWT — after fix.** A fresh ES256 access token for a linked admin returned **HTTP 200** with body:
  - `ok: true`
  - `via: "supabase"`
  - correct linked `user_id` (the staging admin's app `User.id`)
  - correct `supabase_user_id` (the JWT `sub` claim, matching `user.supabase_auth_user_id`)
  - `is_admin: true`
- **Malformed non-JWT bearer.** `Authorization: Bearer opaque-no-dots-here` returned **HTTP 400** with body `{ ok: false, via: "supabase", error: "bearer value is not a JWT", supabase_user_id: null }`. Confirms the format-disambiguation path inside the probe.
- **Expired JWT.** A deliberately-expired token returned **HTTP 401** with body containing `"Signature has expired"` (the message surfaced from PyJWT through `SupabaseTokenInvalid`). This is the most informative single-line confirmation that signature verification is genuinely active — an unverified token would never reach the expiry check.

No DB rows were created or modified during the probe window. No Flask-Login session was issued by the probe path. No `user.supabase_auth_user_id` was written by request handling (linkage continues to be the linkage CLI's exclusive responsibility).

## Flag-off restoration

After the probe scenarios completed:

- `SUPABASE_AUTH_ENABLED` was returned to `false`.
- The app was restarted to pick up the new config.
- `GET /admin/auth/probe` again returned **HTTP 404**, confirming the endpoint is once more hidden and the kill switch behaved correctly.

The `supabase_auth_user_id` values written during linkage remained in place (durable data); admins also keep their staging Supabase identities. These persist intentionally so future probe windows reuse the same identities rather than churning the Supabase Auth user list.

## Outcome

**Phase 2 probe rehearsal: PASSED.**

Validated end-to-end:

- **Flag-off safety.** With `SUPABASE_AUTH_ENABLED=false`, `/admin/auth/probe` is 404 and no Supabase code path runs for any request.
- **Flag-on probe path.** With the flag temporarily on, an authenticated admin reaches the probe and receives a structured JSON result.
- **Linked admin JWT resolves correctly through Supabase.** A real Supabase-issued ES256 access token for a linked admin verifies, resolves to the matching app `User`, and is reported as `via=supabase`.
- **ES256 / JWKS verification.** The verifier reads the token header, fetches the project JWKS, validates the signature against the matching `kid`, and rejects expired or wrong-key tokens.
- **Safe return to hidden/off state.** Setting the flag back to `false` and restarting restored 404 immediately.

## Important boundary

This rehearsal validates the **Phase 2 probe path only**. It does **not** enable end-user Supabase-only sign-in.

- `routes/auth_routes.py`, `forms.py`, and templates remain unchanged.
- `LoginForm` is still username + password.
- `/login`, `/logout`, `/register`, `/reset-password-request`, `/reset-password/<token>`, `/confirm-email/<token>`, `/confirm-new-email/<token>`, `/change-password`, `/send-change-password-link` continue to operate against Flask-Login + the app-managed credential model.
- Mobile / API clients continue to authenticate via `UserApiToken` exclusively.
- End-user **login, signup, password reset, and SSO cutover remain Phase 3 work** and are not in scope for this rehearsal or for any subsequent Phase 2 step.

## Follow-up actions

- Production may continue with `SUPABASE_AUTH_ENABLED=false` as steady state until a deliberate production probe window is scheduled. The same staging procedure (admin pre-linking → 15-minute probe → flag returned to false) should be followed for production.
- A separate **Phase 3 implementation plan** is required for Supabase-only end-user auth (email/password and SSO). This must cover: cohort backfill order, on-login progressive linkage rules, the bridge endpoint that turns a Supabase JWT into a Flask-Login session for browser users, password-reset / email-confirmation route deprecation, and the explicit decision on whether to expose a "Sign in with Supabase" entry point on `/login`.
- Two open decisions from `docs/SUPABASE_AUTH_READINESS_REVIEW.md` §10 should be recorded in `docs/DECISIONS.md` before Phase 3 begins: long-term `is_email_confirmed` semantics; long-term mobile-token strategy.
- Issuer / audience claim hardening (`verify_iss`, strict `verify_aud`) was deliberately deferred from Phase 2; Phase 3 should land it together with end-user JWT acceptance.

## Post-session security actions required

Because sensitive material was exposed in shells, terminals, and ad-hoc test commands during setup and rehearsal, the following must be **rotated or burned** after the session closes:

- **Supabase service role / secret keys** — rotate via the Supabase dashboard. The previous value is considered exposed.
- **Supabase publishable / anon keys** — rotate if exposure is deemed material (operator judgement). Lower-impact than the service role key but worth a fresh value if uncertain.
- **Supabase JWT-related secrets** — rotate `SUPABASE_JWT_SECRET` (legacy HS256 secret) and any project-level signing-key material that was handled outside of standard Supabase rotation.
- **Temporary Supabase passwords** used during the rehearsal (e.g. for admin onboarding via `--send-onboarding`) — burn / reset; treat any value typed or copied during the session as compromised.
- **Exposed Supabase access tokens** captured during testing (the ES256 admin tokens used to exercise the probe) — these are short-lived but should not be reused. Allow them to expire naturally; do not paste them anywhere persistent.
- **Exposed database credentials** — if `DATABASE_URL` (including the embedded Postgres password) was visible in a terminal, history file, or screenshot, rotate the staging Postgres password via Supabase and update the staging deployment configuration accordingly.

After rotation, the post-rotation values should be installed only via the deployment platform's secret store; do not commit them to any version-controlled file (`.env` remains gitignored, but the operational rule holds).

A simple checklist for the rotation pass:

- [ ] Supabase service role key rotated
- [ ] Supabase anon / publishable key rotated (if deemed necessary)
- [ ] `SUPABASE_JWT_SECRET` rotated
- [ ] Temporary admin passwords reset
- [ ] Captured ES256 access tokens treated as expired / not reused
- [ ] Staging Postgres password rotated (if exposed)
- [ ] Deployment platform secret store updated
- [ ] No rotated values committed to any repo file
