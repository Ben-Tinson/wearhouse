# Decisions Log

A concise record of non‑obvious decisions that shape the codebase.

## External API usage and caching
- **Caching‑first KicksDB**: `services/sneaker_lookup_service.py` always checks `SneakerDB` before calling KicksDB. Materials are extracted from cached descriptions only (no extra API calls).
- **Quota protection**: release ingestion uses request caps and GOAT backfill thresholds in `services/release_ingestion_service.py` to minimise paid calls.

## Materials extraction
- **Keyword‑based extraction**: `services/materials_extractor.py` uses a lightweight rule‑set on cached descriptions (no paid endpoints).
- **Caching**: materials are stored in `SneakerDB` with `materials_updated_at` and TTL logic.

## Steps syncing & attribution
- **Day‑level v1 attribution**: steps are split evenly per day across worn sneakers (`v1_equal_split_day`).
- **Timezone‑correct buckets**: attribution uses the bucket’s timezone (not the user’s current timezone) to determine local dates.
- **Backend‑only logic**: mobile clients upload buckets, attribution is fully server‑side.

## Sneaker health scoring
- **Single health score**: combines steps + exposure penalties since `Sneaker.last_cleaned_at`.
- **Exposure capture**: wet/dirty exposure is prompted when updating last‑worn; stored in `ExposureEvent` and split into `SneakerExposureAttribution`.

## News / Articles
- **Markdown authoring**: admins write Markdown; rendering uses `services/article_render.py` with Bleach sanitisation.
- **SEO strategy**: hybrid JSON‑LD approach — global Organisation/WebSite schemas in `SiteSchema`, auto Article schema from article data, optional per‑article Product/FAQ/Video schemas.
- **Blocks model**: flexible `ArticleBlock` types (heading/body/side image/full image/carousel), ordered by `position`.
