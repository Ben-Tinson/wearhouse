# Staging Postgres Validation Checklist

This checklist is for validating Soletrak against the staging Supabase Postgres environment after:

- Alembic migrations have run cleanly to head
- SQLite source data has been imported into staging Postgres
- imported row counts have been verified
- Flask remains the main backend
- Supabase Auth is not yet in scope

Use this as a practical QA pass before treating the staging Postgres setup as production-ready.

## Test setup

- Point the app at the staging Supabase Postgres `DATABASE_URL`
- Use realistic staging user accounts with imported data
- Keep notes of:
  - route/page tested
  - user used
  - exact error message if any
  - whether the issue is data-specific or reproducible

## Auth / Session Sanity

### Login with an existing user

- What to test:
  - log in with a known imported user
- Expected outcome:
  - login succeeds
  - session persists across redirects
  - user lands on the expected post-login page
- Critical/blocking:
  - Yes

### Logout

- What to test:
  - log out from an authenticated session
- Expected outcome:
  - session is cleared
  - protected pages redirect back to login
- Critical/blocking:
  - Yes

### Invalid login handling

- What to test:
  - submit wrong password for an existing username
  - submit unknown username
- Expected outcome:
  - login fails cleanly
  - no server error
  - expected validation or flash message appears
- Critical/blocking:
  - Yes

### Password reset token flow

- What to test:
  - request password reset
  - open reset link
  - set a new password
- Expected outcome:
  - token validation works
  - password updates successfully
  - old password stops working
- Critical/blocking:
  - Yes

### Email confirmation / email-change confirmation

- What to test:
  - trigger email confirmation flow if available
  - trigger pending email confirmation flow if available
- Expected outcome:
  - token-backed flow succeeds without DB errors
  - `pending_email` and `email` transitions behave correctly
- Critical/blocking:
  - Yes

## Profile / Preferences

### View profile page

- What to test:
  - open the profile/settings page for an imported user
- Expected outcome:
  - profile renders without missing-field or query errors
  - imported values display correctly
- Critical/blocking:
  - Yes

### Update basic profile fields

- What to test:
  - update first name / last name / email if supported
- Expected outcome:
  - changes persist after refresh
  - uniqueness rules still work for email
- Critical/blocking:
  - Yes

### Update preferences

- What to test:
  - change preferred currency
  - change preferred region
  - verify timezone-related settings if exposed
- Expected outcome:
  - preferences save cleanly
  - updated values affect display logic where expected
- Critical/blocking:
  - Yes

## Collection And Sneaker Detail

### Collection list renders

- What to test:
  - open the main sneaker collection page
  - paginate/filter/search if available
- Expected outcome:
  - all imported sneakers load correctly
  - no ordering/filtering regressions
- Critical/blocking:
  - Yes

### Sneaker detail pages render

- What to test:
  - open several sneaker detail pages, including older records and records with richer history
- Expected outcome:
  - detail page loads without errors
  - image URLs, purchase data, SKU, condition, and computed fields render correctly
- Critical/blocking:
  - Yes

### Create a new sneaker

- What to test:
  - create a sneaker manually using normal UI flow
- Expected outcome:
  - insert succeeds
  - record appears in collection
  - no primary-key or sequence errors occur
- Critical/blocking:
  - Yes

### Edit an existing sneaker

- What to test:
  - update brand/model/colorway/price/image URL/condition
- Expected outcome:
  - changes persist
  - long external image URLs save correctly
- Critical/blocking:
  - Yes

### Delete a sneaker

- What to test:
  - delete a sneaker with related child data in staging-only test conditions
- Expected outcome:
  - delete behavior matches current app rules
  - expected related rows are removed or preserved according to ORM behavior
- Critical/blocking:
  - Yes

## Notes / Wears / Cleaning / Damage / Repair / Health

### Sneaker notes

- What to test:
  - create, edit, and delete a sneaker note
- Expected outcome:
  - note timeline updates correctly
  - ordering remains correct
- Critical/blocking:
  - Yes

### Wear records

- What to test:
  - add and remove wear events
  - verify recent wear displays correctly
- Expected outcome:
  - inserts and deletes work
  - derived wear-related calculations remain stable
- Critical/blocking:
  - Yes

### Cleaning events

- What to test:
  - create a cleaning event with and without optional notes
- Expected outcome:
  - cleaning event saves successfully
  - last-cleaned-related UI updates correctly
- Critical/blocking:
  - Yes

### Damage events

- What to test:
  - create a damage event with representative values
- Expected outcome:
  - event saves
  - related sneaker state/health views still load
- Critical/blocking:
  - Yes

### Repair events

- What to test:
  - create a repair event
  - resolve linked active damage if the UI supports it
- Expected outcome:
  - repair event saves successfully
  - related damage/repair linkage still behaves correctly
- Critical/blocking:
  - Yes

### Health history / snapshots

- What to test:
  - open health-related history views for sneakers with existing data
- Expected outcome:
  - existing health snapshots display
  - no orphan-related staging import issues appear
- Critical/blocking:
  - Yes

## Release Pages And Market Data

### Release list / calendar

- What to test:
  - open release list/calendar pages
  - verify only visible releases behave as expected
- Expected outcome:
  - release pages load
  - filtering/sorting by date still works
- Critical/blocking:
  - Yes

### Release detail page

- What to test:
  - open each imported release detail page
- Expected outcome:
  - release metadata, affiliate offers, sale points, and market stats render correctly
  - no missing relation errors
- Critical/blocking:
  - Yes

### Release pricing behavior

- What to test:
  - inspect any release that has regional price data
  - edit/import a release price if admin tooling is available
- Expected outcome:
  - one native retail price per region is respected
  - no duplicate-per-region behavior reappears
- Critical/blocking:
  - Yes

### Release size bid behavior

- What to test:
  - verify size market data where ask and bid rows may both exist
- Expected outcome:
  - ask and bid can coexist without overwriting each other
  - size-level market display remains stable
- Critical/blocking:
  - Yes

### Wishlist flow

- What to test:
  - add a release to wishlist
  - remove it again
- Expected outcome:
  - wishlist association is created and removed correctly
  - no duplicate-association errors
- Critical/blocking:
  - Yes

## Articles / Content

### Article list and detail pages

- What to test:
  - open article list pages and individual article pages
- Expected outcome:
  - imported content renders correctly
  - blocks appear in the expected order
  - slug lookup works
- Critical/blocking:
  - Yes

### Create and edit article content

- What to test:
  - create a draft or article
  - edit title, slug, excerpt, and body blocks
- Expected outcome:
  - content saves without DB errors
  - unique slug enforcement still works
- Critical/blocking:
  - Yes

### Site schema records

- What to test:
  - verify pages depending on `site_schema` still render correctly if those records exist
- Expected outcome:
  - missing or empty staging data does not cause crashes
- Critical/blocking:
  - Medium

## API Tokens

### View/manage API tokens

- What to test:
  - open API token management UI if available
  - inspect imported tokens
- Expected outcome:
  - existing token records load correctly
  - created/revoked state displays correctly
- Critical/blocking:
  - Yes

### Create a new API token

- What to test:
  - create a new API token for a staging user
- Expected outcome:
  - token creation succeeds
  - token value is shown once
  - DB insert succeeds without sequence/default issues
- Critical/blocking:
  - Yes

### Revoke a token

- What to test:
  - revoke an active token
- Expected outcome:
  - token can no longer be used
  - revocation timestamp persists
- Critical/blocking:
  - Yes

## Steps / Exposure / Attribution

### Step bucket views

- What to test:
  - inspect user step history pages or endpoints
- Expected outcome:
  - imported step bucket data loads correctly
  - date ordering and totals still make sense
- Critical/blocking:
  - Yes

### Step attribution views

- What to test:
  - inspect attributed steps per sneaker/day
- Expected outcome:
  - imported attribution rows render correctly
  - no missing-sneaker or missing-user issues remain
- Critical/blocking:
  - Yes

### Exposure event flows

- What to test:
  - create and edit exposure events
  - inspect derived exposure attribution output if visible
- Expected outcome:
  - exposure records save successfully
  - derived exposure attribution remains consistent
- Critical/blocking:
  - Yes

### Steps/mobile sync path

- What to test:
  - call the relevant step-write endpoint using a valid staging API token if possible
- Expected outcome:
  - authenticated write succeeds
  - new step buckets and attribution updates persist
- Critical/blocking:
  - Yes

## Admin / Import Flows

### Release CSV import

- What to test:
  - run a small known-good release CSV import in staging
- Expected outcome:
  - import completes successfully
  - releases, offers, prices, and related data follow current schema rules
- Critical/blocking:
  - Yes

### Admin release add/edit

- What to test:
  - manually add a release
  - manually edit an existing release
- Expected outcome:
  - writes succeed on Postgres
  - `source + source_product_id` uniqueness behaves correctly
  - one-price-per-region rules remain intact
- Critical/blocking:
  - Yes

### Ingestion/update flows

- What to test:
  - run any staging-safe release ingestion/update task
- Expected outcome:
  - upsert logic still works against Postgres
  - no uniqueness/sequence/cast errors occur
- Critical/blocking:
  - Yes

### Bulk/admin content updates

- What to test:
  - run any staging-safe content/admin workflows that insert related rows
- Expected outcome:
  - no hidden sequence issues remain after import
- Critical/blocking:
  - Yes

## Likely Postgres-Specific Issues To Watch For

- Sequence drift after explicit-ID imports if any table was missed by post-import sequence reset.
- Stricter varchar enforcement exposing fields that SQLite allowed to exceed declared lengths.
- Stricter boolean handling in old code paths that may still assume SQLite-style `0`/`1` semantics.
- Datetime parsing issues where SQLite tolerated looser string shapes than Postgres-backed SQLAlchemy code paths.
- Uniqueness behavior differences where SQLite test/local data previously hid collisions.
- Reserved-word SQL issues in any remaining raw SQL paths not exercised yet.
- Ordering/query behavior differences where SQLite and Postgres sort or compare text differently.
