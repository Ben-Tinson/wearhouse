# DB Migration Decisions

This document turns the unresolved database and auth questions into explicit decisions the team needs to make before or during the SQLite to PostgreSQL and Supabase migration work.

It is intentionally narrower than `docs/DATABASE_SCHEMA.md`. That file is the reference. This file is the decision log and recommendation set.

Status note:

- The `ReleasePrice` decision is now implemented.
- The `ReleaseSizeBid` decision is now implemented.
- The `Release` source identity alignment work is implemented for `source + source_product_id`; the remaining open part of that topic is whether `sku` and/or `release_slug` should ever become unique.

## 1. ReleasePrice model: one price per region vs multi-currency per region

### Decision to make

Should Soletrak support exactly one active retail price per release region, or multiple retail prices per region in different currencies?

### Current implementation

- The schema now enforces one `release_price` row per `release_id + region` via `uq_release_price_region`.
- CSV/admin upsert logic now updates one regional row rather than modeling multiple currencies per region.
- A migration was added to collapse legacy duplicate non-null regional rows by keeping the newest row per `release_id + region`.
- Current UI and display logic continue to behave like a single canonical native retail price per region.

### Options

#### Option A: one retail price per region

- Change schema and services so each release region has one native retail price.
- Region implies its native retail currency.

Pros:

- Matches current admin/import behavior.
- Matches current display logic.
- Simpler mental model for release data.
- Easier to enforce with a unique constraint on `release_id + region`.
- Lower migration risk.

Cons:

- Less flexible if the team later wants to store alternate currency-denominated official retail prices for the same region.
- Requires a schema change from the current `release_id + currency + region` uniqueness.

#### Option B: multi-currency retail prices per region

- Keep schema support for multiple currencies per region and update services/import/admin logic to support it correctly.

Pros:

- More flexible.
- Preserves the current schema shape with less DB redesign.

Cons:

- Does not match current app behavior.
- Adds complexity to import, admin edit, and display resolution logic.
- Creates ambiguity about which retail price is canonical for a region.
- Not obviously needed for Soletrak’s current use case.

### Recommended option

Option A: one retail price per region.

### Implementation status

Implemented as part of pre-migration hardening.

### Why this is best for Soletrak specifically

Soletrak is using region as the main user-facing retail-market concept. The app already separates region preference from display currency, and it intentionally avoids FX-converting retail price for presentation. That means the natural model is one native retail price per region, not multiple region-local currencies for the same release. This matches the current product behavior, reduces schema ambiguity, and lowers migration risk.

### Decision deadline

Completed before Postgres migration.

## 2. ReleaseSizeBid model: must ask and bid coexist?

### Decision to make

Should `release_size_bid` store both ask and bid rows for the same release/size combination?

### Current implementation

- The model includes `price_type`, defaulting to `bid`.
- The uniqueness constraint now includes `price_type`.
- Same-size ask and bid rows can now coexist cleanly for the same `release_id + size_label + size_type`.
- Current code in `services/heat_service.py` queries `ReleaseSizeBid` by `price_type`, which strongly implies the table is intended to support both ask and bid values.
- The relevant write paths were also updated so upsert/dedupe logic keys on `price_type` instead of overwriting one side with the other.

### Options

#### Option A: same table stores both asks and bids

- Keep one table.
- Change uniqueness to include `price_type`.

Pros:

- Matches current querying behavior.
- Keeps schema compact.
- Minimal conceptual change.

Cons:

- Requires a constraint change and careful data migration.

#### Option B: table is bid-only

- Remove ask usage from code and make the table explicitly bid-only.

Pros:

- Simpler schema.

Cons:

- Does not match current heat/market logic.
- Would reduce current feature flexibility.
- Would require code redesign later if asks are needed.

### Recommended option

Option A: same table stores both asks and bids, and uniqueness should include `price_type`.

### Implementation status

Implemented as part of pre-migration hardening.

### Why this is best for Soletrak specifically

Soletrak already uses both asks and bids conceptually in release-market logic. The current schema is the inconsistent piece, not the business need. Fixing the constraint is cleaner than redesigning the market-data model around a narrower bid-only interpretation.

### Decision deadline

Completed before Postgres migration.

## 3. Release identity strategy: source/source_product_id, SKU, release_slug

### Decision to make

What should the canonical identity rules be for `release` rows?

This breaks into three linked questions:

- Is `source + source_product_id` the canonical ingestion identity?
- Should `sku` be unique?
- Should `release_slug` be unique?

### Current implementation

- Release ingestion in `services/release_ingestion_service.py` matches first by `sku`, then by `source + source_product_id`.
- Alembic creates a unique constraint on `release(source, source_product_id)`.
- The current SQLAlchemy `Release` model now declares that same constraint again, so the earlier model-vs-migration drift is resolved.
- `sku` is indexed but not unique.
- `release_slug` is indexed but not unique.
- CSV import also uses SKU and slug-like matching behavior for upsert heuristics.

### Options

#### Option A: canonical identity is external-source identity first, SKU is lookup-only, slug is URL-only

- Preserve uniqueness on `source + source_product_id`.
- Keep `sku` non-unique.
- Keep `release_slug` non-unique.

Pros:

- Best fit for ingestion-backed shared releases.
- Avoids over-trusting SKU as a globally unique identifier.
- Keeps slug as a presentation field, not a hard identity field.

Cons:

- Requires clearer service rules for CSV-admin-created releases that may not have source IDs yet.
- Still leaves potential duplicate SKUs across releases if data is messy.

#### Option B: make SKU unique for releases

- Use SKU as the main identity.

Pros:

- Simple.
- Aligns with some current matching logic.

Cons:

- Real sneaker data can contain SKU collisions, regional differences, missing values, or bad source data.
- Risky for a shared global release table.
- Could make ingestion brittle.

#### Option C: make `release_slug` unique

- Use slug as a canonical identity in addition to or instead of source identity.

Pros:

- Clean URL semantics.

Cons:

- Slug is derived/presentation-oriented.
- Slug collisions are plausible across legacy/manual/imported data.
- Not a strong ingestion identity.

### Recommended option

Option A: keep `source + source_product_id` as the canonical external identity, keep SKU as a lookup/match key but not a hard unique constraint, and keep `release_slug` as a URL/match aid rather than a hard unique key.

### Implementation status

Partially implemented:

- `source + source_product_id` alignment between model and Alembic is restored.
- `sku` and `release_slug` remain intentionally non-unique today.
- The remaining open decision is whether either field should later be tightened for Postgres launch or kept as lookup/presentation fields only.

### Why this is best for Soletrak specifically

Soletrak’s `release` table is a shared ingestion-backed catalog, not just a manual admin list. External source identity is the cleanest stable key for shared market entities. SKU is useful and important, but too brittle to be the only canonical identity. Slug is even less suitable as a hard key because it is derived from display-oriented naming.

### Decision deadline

Partially completed before Postgres migration: `source + source_product_id` alignment is implemented, while the optional tightening of `sku` and/or `release_slug` remains open.

## 4. Timestamp strategy: UTC-naive plus timezone text vs timezone-aware Postgres types

### Decision to make

Should Soletrak preserve its current pattern of storing UTC-naive datetimes plus separate timezone text, or should it adopt timezone-aware Postgres timestamp types in selected domains?

### Current implementation

- Many timestamps are stored as plain SQLAlchemy `DateTime`.
- `step_bucket` explicitly stores UTC-naive `bucket_start` and `bucket_end` plus a separate timezone string.
- `User.timezone` and `ExposureEvent.timezone` store IANA timezone names as text.
- Services convert between UTC and local time in Python.
- Release detail pages intentionally show date-only even when time/timezone exists.

### Options

#### Option A: keep UTC-naive datetimes plus timezone text

- Preserve current semantics in Postgres.
- Continue treating stored datetimes as UTC-naive in app code.

Pros:

- Lowest application change.
- Minimizes migration churn.
- Matches current behavior and tests more closely.

Cons:

- Easier to misinterpret at the DB layer.
- Less idiomatic in Postgres.
- Requires strong team discipline.

#### Option B: move selected operational timestamps to timezone-aware Postgres types

- Store key event timestamps as timezone-aware UTC values.
- Keep separate IANA timezone text only where domain meaning requires it, such as step/exposure local-date logic.

Pros:

- More explicit and safer at the DB layer.
- Better long-term operational clarity.
- More Postgres-native.

Cons:

- Higher migration complexity.
- Requires careful audit of every timestamp usage.
- Risk of subtle regressions in local-date derivation.

### Recommended option

Hybrid leaning toward Option B:

- Keep domain-local timezone text where it matters for user-local reasoning.
- Migrate general operational timestamps to a clearer timezone-aware UTC storage strategy where feasible.
- Treat `step_bucket` and similar local-date-driven domains as special cases that need deliberate design rather than blanket conversion.

### Why this is best for Soletrak specifically

Soletrak has two different timestamp needs:

- operational/system timestamps like sync times, publish times, and token timestamps
- user-local behavioral timestamps where local date matters for steps, exposures, and wear attribution

A blanket rule in either direction is too coarse. A hybrid strategy gives better Postgres hygiene without breaking the local-date model that the health and attribution features rely on.

### Decision deadline

Must be decided before Postgres migration.

## 5. Mobile auth strategy after Supabase Auth

### Decision to make

After Supabase Auth is adopted, should mobile sync keep app-issued bearer tokens, move fully to Supabase-issued auth, or use a hybrid model?

### Current implementation

- Mobile/API step sync uses `UserApiToken`.
- Tokens are created by the Flask app, shown once, and stored hashed.
- Scope handling is narrow and currently centered on `steps:write`.
- Supabase Auth is not yet implemented.

### Options

#### Option A: keep app-issued bearer tokens for mobile sync

Pros:

- Minimal change to current mobile sync flows.
- Tokens are already scoped and hashed.
- Keeps mobile ingestion concerns decoupled from browser/session auth.

Cons:

- Two auth systems to maintain after Supabase Auth arrives.
- More long-term auth surface area.

#### Option B: move fully to Supabase-issued tokens/JWTs

Pros:

- Cleaner identity stack.
- One auth authority.
- Better fit if mobile app becomes a first-class authenticated client.

Cons:

- Bigger migration.
- Requires backend validation and mapping changes.
- Could complicate limited-scope device sync use cases.

#### Option C: hybrid model

- Keep app-issued tokens temporarily for step sync/device use.
- Move user-facing auth to Supabase.
- Revisit whether app-issued tokens are still needed later.

Pros:

- Lowest-risk transition.
- Allows incremental rollout.
- Matches the existing Soletrak pattern of narrow-scope device tokens.

Cons:

- Transitional complexity.
- Requires explicit rules to avoid auth confusion.

### Recommended option

Option C: hybrid first, with a later decision on whether app-issued device tokens should survive long-term.

### Why this is best for Soletrak specifically

Soletrak already has a narrow, device-like token pattern rather than a broad API platform. A hybrid transition lets the team adopt Supabase Auth for primary identity without breaking step sync unnecessarily. It also avoids assuming that every mobile or wearable integration should immediately become a full Supabase-authenticated client.

### Decision deadline

Must be decided before Supabase Auth migration.

## 6. Whether any RLS is needed in phase one

### Decision to make

Should Soletrak implement any Supabase Row Level Security policies in the first migration phase?

### Current implementation

- All core traffic goes through Flask.
- No Supabase integration exists yet.
- No current database-level RLS policies exist.
- User-private data is protected primarily by backend route/service checks.

### Options

#### Option A: no RLS in phase one

- Keep all core app access backend-only.
- Design RLS later if direct Supabase API access is introduced.

Pros:

- Lowest migration complexity.
- Fits the current Flask-first architecture.
- Avoids premature policy work.

Cons:

- Delays hardening for any future direct access use cases.

#### Option B: minimal RLS design and rollout for clearly user-owned tables

- Add RLS for obvious user-private tables, even if Flask remains primary.

Pros:

- Better future-readiness.
- Stronger defense-in-depth.

Cons:

- More complexity during an already risky migration.
- Little immediate benefit if all access still goes through Flask.

#### Option C: broad RLS rollout in phase one

Pros:

- Strongest DB-level access controls from day one.

Cons:

- Highest complexity.
- Poor fit for the current architecture.
- High risk of slowing or destabilizing migration work.

### Recommended option

Option A: no RLS enforcement in phase one, but define the candidate policy model during planning.

### Why this is best for Soletrak specifically

Soletrak is not migrating to a Supabase-first client architecture in phase one. The app remains Flask-mediated. That makes full RLS rollout low-value and high-complexity during the initial cutover. The right move is to keep the backend-first posture, identify future RLS candidates, and defer actual policy rollout until there is a real product need.

### Decision deadline

Can wait until later, but the high-level stance should be agreed before Supabase Auth migration.

## 7. Login identity model after Supabase Auth: keep username semantics or shift to email-first

### Decision to make

After Supabase Auth is introduced, should Soletrak keep username as a core login-facing identity concept, or shift to email-first login and treat username only as an app/profile field?

### Current implementation

- Browser login uses `username` and password.
- Email is required, but not used for login.
- Password reset and confirmation flows are email-based.
- Supabase Auth is naturally more email-centric.

### Options

#### Option A: keep username as a first-class login identity

Pros:

- Preserves current UX.
- Keeps continuity for existing users.

Cons:

- Less natural fit with Supabase Auth.
- Adds mapping complexity.

#### Option B: move to email-first auth, keep username only as app/profile data

Pros:

- Better fit with Supabase Auth.
- Cleaner identity model.
- Aligns login, verification, and password reset around the same identifier.

Cons:

- User-facing change.
- Requires transition communication and possibly UI updates.

### Recommended option

Option B: move to email-first auth and keep username as an app/profile field if still valuable.

### Why this is best for Soletrak specifically

The current system already uses email for confirmation and password reset. Supabase Auth is also email-centric by default. Keeping username as a profile/display field is fine, but keeping it as the primary auth key adds complexity without much benefit.

### Decision deadline

Must be decided before Supabase Auth migration.

## 8. Should release slugs be guaranteed unique for URLs?

### Decision to make

Does Soletrak want `release_slug` to be unique for URL identity, or should URLs continue to rely on the combined product-key-plus-slug pattern without slug uniqueness?

### Current implementation

- `release_slug` is indexed but not unique in `models.py`.
- Product URLs are built with a product key plus slug pattern rather than slug alone.
- CSV import and matching logic can use slug-like fallbacks, but slug is not the hard DB identity.

### Options

#### Option A: keep slug non-unique

Pros:

- Matches current schema and URL construction.
- Avoids forced cleanup of legacy/imported data.

Cons:

- Slug cannot be treated as a strong standalone identifier.

#### Option B: enforce unique release slugs

Pros:

- Cleaner editorial/SEO semantics.
- Simpler if the app ever wants slug-only routes.

Cons:

- Requires deduplication and slug conflict handling.
- Not necessary for the current URL shape.

### Recommended option

Option A: keep `release_slug` non-unique for now.

### Why this is best for Soletrak specifically

Soletrak already avoids making slug the sole route key. That is the right design for an ingestion-backed release catalog where naming collisions are plausible. Making slug unique now would create migration work without solving a current product problem.

### Decision deadline

Can wait until later, but should be revisited before any slug-only routing or SEO redesign.
