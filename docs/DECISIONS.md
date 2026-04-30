# Decisions Log

A concise record of non-obvious decisions that shape the codebase.

## Platform / database cutover
- **Supabase Postgres is now the source of truth**: Soletrak has completed cutover to fresh Supabase project `sjwdvsefjlflgavshiyy`.
- **Previous project retained temporarily**: Supabase project `mizyioplztuzycipfdsd` is fallback/reference only until deliberate retirement.
- **Postgres dump/restore is the operational cutover path**: future restores/cutovers should use full Postgres logical backups and fresh-target restores, not SQLite re-import or CSV.
- **SQLite is archival/local only**: SQLite remains useful for local dev/tests and historical fallback, but it is no longer the normal operational source.
- **Backup artefacts are retained**: keep `backups/postgres/soletrak_postgres_20260428_152256.dump`, `backups/postgres/soletrak_public_20260428_154243.dump`, and `backups/postgres/soletrak_public_cutover_20260428_160930.dump` until retention is decided.
- **Supabase Auth is separate from the DB cutover**: the Postgres cutover is complete; Supabase Auth remains the next major platform phase and must not be assumed live.

## Supabase Auth direction
- **Flask auth remains live for now**: Flask-Login, app-owned `User`, existing admin checks, and `UserApiToken` flows are still active.
- **Managed identity is the next target**: Supabase Auth is planned to handle core identity, password reset, email verification, and future auth hardening.
- **App user/profile data stays app-owned**: roles, admin status, preferences, sneaker ownership, billing/plan state if added, and domain data should remain explicit application concerns.
- **The app-owned `user` table must not be assumed removable**: it anchors domain ownership and authorization even if Supabase Auth owns identity.
- **Transition must be phased**: future auth work needs user linkage, session cutover, email/reset replacement, mobile token strategy, and rollback planning.
- **No hard auth replacement**: implementation should use cautious phased migration, not a big-bang switch from Flask auth to Supabase Auth.
- **Auth readiness analysis is required before code changes**: auth assumptions are spread across routes, decorators, profile flows, admin checks, API token flows, templates, and tests.
- **Legacy email delivery is deferred by choice**: password-reset token logic was validated, but SendGrid/reset-email delivery is not prioritised because Supabase Auth is expected to replace that path.

## Supabase Auth migration — accepted pre-implementation decisions

Framing: the Supabase Auth migration is a **phased dual-run**, not a hard replacement. The active strategy prioritises **continuity and compatibility** over early behavioural change. Each decision below is recorded as accepted before Phase 1 implementation begins. Reference: `docs/SUPABASE_AUTH_READINESS_REVIEW.md`, `docs/SUPABASE_AUTH_PHASE1_IMPLEMENTATION_PLAN.md`.

- **Login identifier remains username during dual-run** (Accepted, 2026-04-28).
  - Decision: keep username + password as the live user-facing login during the dual-run migration period; do not switch to email-first login in Phase 1 or initial Phase 2 work.
  - Why: the current app, templates, and full test suite are built around username login (`forms.py::LoginForm`, `routes/auth_routes.py::login`, `tests/conftest.py::auth.login`). Changing the login identifier too early stacks product risk on top of migration risk.
  - Implication: Supabase Auth is introduced behind the scenes while the visible login UX stays stable. Username removal, if ever wanted, is a separate later product decision and not part of the initial migration.

- **Canonical identity link is `user.supabase_auth_user_id`, not email** (Accepted, 2026-04-28).
  - Decision: the long-term canonical relationship between an app user and a Supabase Auth identity is the `user.supabase_auth_user_id` column. Email is a backfill/match input only.
  - Why: email is mutable and case-sensitive in places; using it as the canonical link would silently break on email changes or duplicates. The app-owned `user` table remains the profile/account anchor and needs a stable foreign reference.
  - Implication: once a row is linked, `supabase_auth_user_id` is authoritative; email matching is used for backfill matching only. Resolution helpers and decorators should resolve by `supabase_auth_user_id` once a JWT branch is added.

- **`Authorization: Bearer` token contract for `UserApiToken` is preserved** (Accepted, 2026-04-28).
  - Decision: the existing `UserApiToken` bearer-token behaviour must remain unchanged throughout dual-run. Any Supabase JWT handling added later must not regress current `Authorization: Bearer ...` API token flows.
  - Why: mobile/API clients are already shipped against this contract via `decorators.bearer_or_login_required`; a naïve "verify JWT first" change would 401 every step-sync request.
  - Implication: when a Supabase JWT branch is introduced, an explicit token-resolution policy must be defined (e.g. `UserApiToken` SHA-256 hash lookup first, JWT verification second, Flask-Login session third — or a separate header). No rollout ships if it risks breaking existing API token consumers; a regression test covering a `UserApiToken`-authenticated endpoint is required before that decorator is touched.

- **No password-hash import in the first Supabase Auth rollout** (Accepted, 2026-04-28).
  - Decision: do not attempt to import or migrate `password_hash` values into Supabase Auth in the first rollout. Prefer a safer staged credential transition (linked activation, magic link, or controlled reset/setup flow).
  - Why: hash-format compatibility is uncertain and importing credentials couples identity linkage with credential migration, increasing failure surface. Flask auth remains available as fallback throughout phased rollout, so users are not locked out.
  - Implication: password import is not a Phase 1 requirement and not assumed for Phase 2/3. The rollout phase that flips Supabase Auth to primary must explicitly document the chosen credential transition path before it ships.

- **`is_email_confirmed` remains the live confirmation gate until cutover** (Accepted, 2026-04-28).
  - Decision: `User.is_email_confirmed` continues to be the app-side login confirmation gate until Supabase Auth is the explicit source of verification truth.
  - Why: the current login path depends on this flag (`routes/auth_routes.py::login` refuses unconfirmed users). Repurposing it silently in early phases creates login and account-state risk.
  - Implication: later migration phases must explicitly define how app confirmation state maps to Supabase Auth `email_confirmed_at` (legacy, derived, or removed). The field is not silently retired or repurposed in Phase 1 or Phase 2.

- **No Supabase Auth rollout without a documented admin recovery procedure** (Accepted, 2026-04-28).
  - Decision: no Supabase Auth rollout phase proceeds without a documented emergency admin recovery procedure and at least one admin account validated end-to-end on the new path.
  - Why: admin lockout is one of the highest-impact migration failure modes. Current admin checks depend on `User.is_admin` and Flask-Login session state; a Supabase login that resolves to no app user gives no admin access at all.
  - Implication: each rollout phase that introduces Supabase Auth-issued sessions must (a) document a break-glass path (e.g. retain Flask-Login `/login/legacy` or a CLI-driven elevation flow) and (b) verify a known admin can log in via the new path and reach `/admin/...` before broader rollout.

- **Phase 1 preparatory slice landed; Phase 2 not started** (Status, 2026-04-28).
  - Decision: Phase 1 has been implemented exactly as scoped in `docs/SUPABASE_AUTH_PHASE1_IMPLEMENTATION_PLAN.md`. Variant A was followed: only `user.supabase_auth_user_id` (UUID, nullable) plus a partial unique index were added; `created_at` and `last_login_at` were intentionally **not** added.
  - Why: the slice was kept strictly non-behaviour-changing. Live login, logout, profile, admin checks, `UserApiToken`, templates, forms, and decorators were not modified.
  - Implication: `services/auth_resolver.py` is a pass-through shim and is **not yet referenced by any live code path** — leave it untouched until Phase 2 work begins. The new column is dormant (every row is NULL). Phase 2 must not start until the audit script has been run against staging and production with exit code `0` (no `[BLOCK]` rows).

## Supabase Auth Phase 2 — accepted pre-implementation decisions

Framing: Phase 2 introduces server-side Supabase Auth **capability** (JWT verification, identity linkage, admin-only probe) without enabling Supabase Auth for any end-user request path. Each decision below is recorded as accepted before Phase 2 code work begins. Reference: `docs/SUPABASE_AUTH_PHASE2_IMPLEMENTATION_PLAN.md`.

- **Phase 2 ships with `SUPABASE_AUTH_ENABLED=false` as production steady state** (Accepted, 2026-04-29).
  - Decision: Supabase Auth request handling is disabled by default behind a single `SUPABASE_AUTH_ENABLED` boolean env var. Production runs with the flag `false` for the entire Phase 2 window except for an explicit, time-boxed admin probe exercise.
  - Why: a feature-flag default of `false` removes the risk of "ship-then-flip" mistakes and guarantees that merging Phase 2 PRs cannot, on its own, change request handling for any user.
  - Implication: no Phase 2 rollout is enabled by configuration drift. The flag is only set to `true` in production for the documented 15-minute admin probe window in `docs/SUPABASE_AUTH_PHASE2_IMPLEMENTATION_PLAN.md` §6 Step 3, then returned to `false`. Staging may run with the flag enabled for ongoing Phase 3 design work.

- **The resolver/auth request path must never auto-link or mutate `user.supabase_auth_user_id`** (Accepted, 2026-04-29).
  - Decision: `services/auth_resolver.py` and any decorator that consults it must treat `user.supabase_auth_user_id` as read-only. Auto-linking on email match, on first Supabase login, or on any other request-path heuristic is forbidden in Phase 2.
  - Why: a single misconfigured or attacker-supplied Supabase identity that auto-binds to the wrong app user could attach to collection, wishlist, admin, or token data with no audit trail.
  - Implication: linkage is performed **only** by the explicit linkage tooling (`scripts/link_supabase_identities.py` calling `services/supabase_auth_linkage.py`). Every link/unlink is audited. A request that presents a valid Supabase JWT for an email whose app user is unlinked must resolve to `None` (logged warning), not silently link.

- **Bearer-token collision is resolved by format disambiguation** (Accepted, 2026-04-29).
  - Decision: in `decorators.bearer_or_login_required`, an `Authorization: Bearer <value>` header is treated as a candidate Supabase JWT only when (a) `SUPABASE_AUTH_ENABLED=true`, (b) the value contains exactly two `.` separators, and (c) it parses as a structurally valid JWT. Any other opaque bearer value continues through the existing `UserApiToken` SHA-256 lookup. No new header is introduced.
  - Why: existing `UserApiToken` values are 43-char URL-safe base64 strings (`secrets.token_urlsafe(32)`) with zero `.` characters; Supabase JWTs always contain exactly two. Disambiguation is deterministic and does not require a mobile-client release.
  - Implication: no Phase 2 change may regress current `UserApiToken` mobile/API behaviour. A regression test exercising a real `UserApiToken` against a `@bearer_or_login_required` endpoint must ship with the decorator change and pass with the flag both off and on. The Supabase JWT branch must be pure verification only — no DB writes — so the existing `last_used_at` commit footprint is not compounded.

- **Admins are the only cohort eligible for the first live Supabase probe** (Accepted, 2026-04-29).
  - Decision: in Phase 2, only admin app users may be linked to Supabase identities, and only admins may exercise the probe endpoint. No regular-user backfill, no public Supabase login UI, no progressive on-login linkage in Phase 2.
  - Why: admin lockout is the highest-impact migration failure mode. Limiting Phase 2 to admins keeps the blast radius small and the recovery path well understood.
  - Implication: `/login` (legacy username + password + `is_email_confirmed` gate) remains the required break-glass fallback throughout Phase 2. Phase 2 cannot be considered complete, and Phase 3 cannot start, until **at least two admins** are confirmed end-to-end onboarded on the new path so recovery does not depend on a single individual.

- **No user-facing UX changes in Phase 2** (Accepted, 2026-04-29).
  - Decision: Phase 2 makes no changes to the login form, profile form, password-reset UX, email-confirmation UX, email-change UX, navigation, or any user-visible template. `LoginForm` remains username-based. `routes/auth_routes.py` is not modified.
  - Why: introducing capability and changing UX in the same phase couples user-visible risk to backend risk. Decoupling lets us roll back UI-free in production by a single env-var flip if anything in the resolver / JWT verifier path misbehaves.
  - Implication: any UX work for Supabase Auth (a "Sign in with Supabase" button, redesigned reset flow, email-change coordination) is deferred to Phase 3 or later and must be designed against the verified Phase 2 backend, not in parallel with it.

- **No schema expansion in Phase 2** (Accepted, 2026-04-29).
  - Decision: Phase 2 ships **no** new Alembic migration. The Phase 1 migration `b3c4d5e6f7a8` (adding `user.supabase_auth_user_id` and its partial unique index) remains the auth-related schema head.
  - Why: `supabase_auth_user_id` is the only schema surface needed for verification, linkage, and the admin probe. Adding more columns now (e.g. `last_login_at`, `auth_migration_state`) without an active writer creates ambiguous columns and migration noise.
  - Implication: `flask db current` and `flask db heads` should both report `b3c4d5e6f7a8` at the end of Phase 2. Any column needed by Phase 3 (likely `last_login_at` once Supabase login bridges to a Flask session) ships in Phase 3 with its writer.

- **Linkage CLI is dry-run by default and audit-logged when applied** (Accepted, 2026-04-29).
  - Decision: `scripts/link_supabase_identities.py` performs **no** state changes unless `--apply` is passed explicitly. Production linkage requires `--apply`, is restricted to `--admins-only` in Phase 2, writes a structured audit row per link/unlink to `backups/auth/supabase_link_audit_<timestamp>.jsonl`, and is reversible via `--unlink --user-id <id>`.
  - Why: linking creates durable side effects (Supabase Auth identity rows) that persist across code rollbacks. Making the safe default the read-only one prevents accidents; auditability and reversibility ensure mistakes are recoverable without manual DB surgery.
  - Implication: any production linking action is traceable to a single CLI invocation and is undoable in code, not just in the database. The CLI must be idempotent: re-running it for an already-linked admin must be a no-op rather than an error or a duplicate-link attempt.

- **First live Supabase capability is an admin-only, flag-gated, read-only probe endpoint** (Accepted, 2026-04-29).
  - Decision: the only new HTTP route in Phase 2 is `/admin/auth/probe`, gated by `@login_required` + `@admin_required`, returning 404 when `SUPABASE_AUTH_ENABLED=false`, and performing pure verification (no row writes, no Flask-Login session creation, no `supabase_auth_user_id` writes).
  - Why: it is the smallest production surface that proves the resolver's Supabase branch works against real Supabase JWTs and real production data without touching a single end-user request.
  - Implication: no end-user request path in Phase 2 depends on Supabase Auth. Mobile/API contracts and browser session lifecycle are observably unchanged for end users. Phase 3 is the first phase where any end-user-facing path may consult Supabase Auth.

- **Phase 2 foundation slice landed; admin linkage CLI landed; decorator/probe slices not started** (Status, 2026-04-29).
  - Decision: the Phase 2 capability foundation has been implemented per `docs/SUPABASE_AUTH_PHASE2_IMPLEMENTATION_PLAN.md` (config + env + JWT verifier service + linkage service + flag-gated resolver branch + bearer-collision regression tests), and the admin linkage CLI `scripts/link_supabase_identities.py` has landed alongside its tests. `decorators.bearer_or_login_required` and `/admin/auth/probe` are deliberately deferred to subsequent slices.
  - Why: these slices were kept strictly capability-only and operational-only. No decorator changes; no end-user UX changes; `SUPABASE_AUTH_ENABLED` defaults to false; no schema changes.
  - Implication: production state remains observably unchanged for end users. The CLI can pre-link admins (with the flag still false) so that, when the decorator and probe slices ship, real production data is ready. No admin pre-linking against production should happen until at least one staging dry-run + apply cycle has been audited.

- **Phase 2 probe rehearsal completed successfully against staging** (Status, 2026-04-30).
  - Decision: the Phase 2 admin probe rehearsal has been executed end-to-end against the staging Supabase Postgres + Supabase Auth target and is recorded as **passed**. Full record: `docs/SUPABASE_AUTH_PHASE2_PROBE_REHEARSAL_OUTCOME_2026-04-30.md`.
  - Why: validated the safety contract under real conditions — flag-off → flag-on → flag-off cycle behaved correctly, a linked admin's Supabase JWT resolved through `/admin/auth/probe`, and a verifier follow-up to add ES256/JWKS support (Supabase's current asymmetric default) was scoped, merged, and re-validated in the same window without changing any end-user code path.
  - Implication: the Phase 2 probe path is considered ready. Production may continue with `SUPABASE_AUTH_ENABLED=false` as steady state until a deliberate production probe window is scheduled. End-user Supabase-only sign-in (login, signup, password reset, SSO cutover) is **not** validated by this rehearsal and remains Phase 3 work.

## Release CSV import
- **Admin-only preview/confirm flow**: CSV import always previews before apply, and confirm re-validates the submitted CSV text instead of trusting preview state.
- **Guidance-row compatibility**: the template includes a `__FORMAT_GUIDE__` row and the importer ignores it explicitly.
- **Non-destructive overwrite semantics**: non-blank CSV values overwrite existing values; blank CSV values do not clear existing fields.
- **Skip-existing means skip apply**: `skip_existing` leaves matched releases untouched; it does not partially update or clear matched rows.
- **CSV field precedence over KicksDB ingestion**: releases marked `ingestion_source="csv_admin"` preserve core descriptive/calendar fields against later KicksDB ingestion.

## Region-aware release display
- **Region and currency are separate user preferences**: `preferred_region` selects which market data to prefer; `preferred_currency` selects how convertible resale/market numbers are displayed.
- **Centralised display selection**: release date, retail price, offers, and region messaging are resolved in `services/release_display_service.py`, not duplicated across routes/templates.
- **Single-region canonical fallback**: when a release only has one meaningful region across region rows / prices, that region becomes the canonical display region for all users.
- **Native retail currency is preserved**: retail price is never FX-converted for display. If the app only has a real USD retail price, it shows USD, not an estimated GBP/EUR retail price.
- **KicksDB base-only release data is treated as US-specific**: when no explicit region rows exist but the base release came from KicksDB and only base date/price exist, that data is presented as US-specific rather than region-neutral.
- **Region notes are contextual, not constant**: single-region warnings are hidden when the available region matches the user’s own region; `Showing UK release data` only appears on a true UK date+price match.

## Release detail pages
- **Date-only release display**: release detail pages intentionally show dates without time/timezone, even though region times/timezones may be stored.
- **Shared detail blocks**: “About this release” and market metric layout are shared partials reused across release detail and sneaker detail pages.
- **Conditional content only**: description, market metrics, and chart-adjacent release market content render only when data is present.
- **Admin diagnostics stay admin-only**: missing/present data diagnostics and manual market refresh are exposed only to admins.

## Shared KicksDB market data
- **Release-level cache shared across users**: size bids, sales history, offers, and aggregate market stats are stored on release-linked tables and reused by all users until stale.
- **Per-user display, shared source data**: users do not get their own copies of market data; only the selection and currency presentation differ per user.
- **Quota protection over completeness**: StockX is primary, GOAT backfill is conditional, and refresh windows/caps are enforced to reduce request burn.

## Calendar / admin release management
- **Delete-all hides, does not delete**: the calendar “Delete All Releases” action now removes releases from the calendar by setting `is_calendar_visible = False`, so user wishlists are not affected.
- **Manual add/edit matches region-aware import model**: admin add/edit forms use the same region-aware release structures (`ReleaseRegion`, `ReleasePrice`, `AffiliateOffer`) as CSV import.
- **Regional date propagation is explicit**: admin add/edit supports checkboxes to copy one entered regional date into other regions rather than inferring that automatically.

## Collection / rotation detail linkage
- **Sneaker detail backfills missing release context**: collection/rotation sneaker detail pages are allowed to backfill `Release` data from SKU when local linkage is missing or incomplete so release-linked market context can still render.
