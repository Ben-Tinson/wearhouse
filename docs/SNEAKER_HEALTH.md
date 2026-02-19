# Sneaker Health

## Overview
Sneaker health is a single 0–100 score that combines steps wear, exposure (wet/dirty) since the last clean, and any persistent penalties that remain after cleaning. The score is designed to stay meaningful: steps impact is gentle and capped, while exposure and persistent penalties can move the score more noticeably. We also compute an internal breakdown (wear/cosmetic/structural/hygiene) and a confidence indicator for transparency.

## Data Inputs (what data is used and where it lives)
- `sneaker`
  - `last_cleaned_at`: timestamp of the last clean (used to move the exposure window forward).
  - `persistent_stain_points`: persistent penalty that remains after cleaning if a stain persists.
  - `persistent_material_damage_points`: persistent penalty for lasting material damage.
  - `persistent_structural_damage_points`: persistent penalty from active damage events.
- `sneaker_exposure_attribution`
  - `date_local`, `wet_points`, `dirty_points`: exposure points attributed to a sneaker per local day.
- `exposure_event`
  - `date_local`, `timezone`, `got_wet`, `got_dirty`, `wet_severity`, `dirty_severity`, `stain_flag`, `stain_severity`.
  - Note: stain fields are logged, but *stain exposure does not directly affect the score*. Only persistent stain penalties do.
- `step_bucket` + `step_attribution`
  - Steps are attributed to sneakers in `step_attribution` (`bucket_granularity = 'day'`, `algorithm_version = 'v1_equal_split_day'`).
  - The score uses the **total** attributed steps (all time), not just the last 30 days.
- `sneaker_clean_event`
  - `cleaned_at`, `stain_removed`, `lasting_material_impact` for clean history.
- `sneaker_health_snapshot`
  - `recorded_at`, `health_score`, `reason` to track score changes over time.
  - `wear_penalty`, `cosmetic_penalty`, `structural_penalty`, `hygiene_penalty` (breakdown).
  - `steps_total_used`, `confidence_score`, `confidence_label`.
- `sneaker_damage_event`
  - `reported_at`, `damage_type`, `severity`, `health_penalty_points`, `is_active` for damage tracking.
- `sneaker_repair_event`
  - `repaired_at`, `repair_kind`, `repair_type`, `cost_amount`, `cost_currency`, `resolved_all_active_damage`.

## Health Score Formula
Score is derived from penalties and clamped to `[0, 100]`:

```
health_score = clamp_0_100(
  100
  - steps_penalty
  - wet_penalty
  - dirty_penalty
  - persistent_stain_points
  - persistent_material_damage_points
  - persistent_structural_damage_points
  - hygiene_penalty
)
```

Where:
- `wet_penalty = wet_points_sum * wet_multiplier`
- `dirty_penalty = dirty_points_sum * dirty_multiplier`
- `wet_points_sum` and `dirty_points_sum` come from `sneaker_exposure_attribution` **since the last clean window**.

## Steps Penalty
Steps use a capped, gentle linear formula. Constants live in `services/health_service.py`:

- `RESTORE_STEPS_THRESHOLD = 750_000`
- `MAX_STEPS_PENALTY = 50.0`

Formula:

```
steps_penalty = min(
  MAX_STEPS_PENALTY,
  MAX_STEPS_PENALTY * (steps_total / RESTORE_STEPS_THRESHOLD)
)
```

Behaviour:
- Steps **do not reset** on cleaning.
- Once steps reach ~750,000, the penalty caps at 50 and **does not continue worsening**.

## Hygiene Nudge (wears-based)
Hygiene is driven by wears since last clean, not elapsed time:

Constants:
- `HYGIENE_MAX_PENALTY = 5.0`
- `HYGIENE_FULL_PENALTY_WEARS = 10`

Formula:

```
wears_since_clean = count of sneaker_wear rows where worn_at >= last_cleaned_at.date() + 1 day
hygiene_penalty = min(
  HYGIENE_MAX_PENALTY,
  HYGIENE_MAX_PENALTY * (wears_since_clean / HYGIENE_FULL_PENALTY_WEARS)
)
```

If `last_cleaned_at` is NULL, hygiene penalty is 0 and confidence is reduced.

## Exposure Penalties (wet/dirty/stain)
- Exposure points are derived from `exposure_event` and attributed per day into `sneaker_exposure_attribution`.
- Only **wet** and **dirty** exposure points affect health. Stain exposure is *logged* but does **not** directly contribute to the score.
- Exposure penalties are weighted by material multipliers:
  - Suede / Nubuck: `wet x 2.0`, `dirty x 1.6`
  - Leather: `wet x 1.4`, `dirty x 1.2`
  - Mesh / Knit / Canvas: `wet x 1.1`, `dirty x 1.3`
  - Default: `wet x 1.0`, `dirty x 1.0`

## Cleaning Behaviour
- Cleaning updates `sneaker.last_cleaned_at` and writes a `sneaker_clean_event`.
- Exposure penalties **only include** `sneaker_exposure_attribution` rows where:

```
  date_local >= cleaned_date_local
```

- The current implementation derives `cleaned_date_local` from the **UTC date** of `last_cleaned_at`, then **adds one day** to move the window forward:

```
cleaned_date_local = last_cleaned_at.date() + 1 day
```

- This means exposure from the clean day itself is *not* counted after a clean.
- Persistent penalties remain after cleaning:
  - `persistent_stain_points`
  - `persistent_material_damage_points`
- `persistent_structural_damage_points` remains until damage is repaired/restored.

## Baseline Changes From Repairs
- Full restoration (`repair_kind = restoration`) raises `starting_health` to at least 90.
- Repair with resolved damage bumps `starting_health` by `min(3, sum(severity_resolved))`, capped at 90.
- Repair with no resolved damage can still bump baseline if cost is provided or provider is not DIY, and a repair area is supplied:
  - upper / midsole / outsole: +4
  - insole / lace: +1
  - other: +2

## Sensitive Materials (suede/nubuck)
Implemented.

- Suede/nubuck increases wet/dirty penalties via multipliers above.
- When cleaning, the UI prompts for lasting impact on suede/nubuck.
- If the user confirms lasting impact, `persistent_material_damage_points` increases.

## Snapshots & History
Snapshots are written when the health score is computed during:
- Wear logging (`reason = 'wear'`).
- Cleaning (`reason = 'clean'`).
- Damage reporting (`reason = 'damage'`).
- Repair/restoration (`reason = 'repair'` or `'restoration'`).

## Confidence Indicator
Confidence is a 0–100 score with labels (High/Medium/Low), based on how complete recent data is:
- StepBucket coverage (days in last 30).
- Step attribution for the sneaker in last 30 days.
- Exposure events in last 30 days.
- Presence of `last_cleaned_at`.
- Wear logs in last 14 days.
- Recent repair/restoration events (last 180 days) add a small provider credibility bonus:
  - `Brand`, `Specialist sneaker restorer`, `Local cobbler`: `+6`
  - `Retailer service`: `+4`
  - `Self / DIY`: `+2`
  - `+1` extra if `cost_amount` is present (any provider)

Label thresholds:
- `>= 80` High
- `>= 55` Medium
- else Low

## Care Sensitivity Tags
Derived from materials and shown as guidance (no extra API calls). Current tags:
- `suede_or_nubuck`
- `knit_mesh`
- `patent_leather`
- `canvas`
- `rubber_foam`

Snapshots live in `sneaker_health_snapshot` and are displayed in the Health History page.

## Example Scores (no exposure/persistent penalties)
Using the capped steps formula:
- 10,000 steps → penalty ≈ 0.7 → score ≈ 99.3
- 100,000 steps → penalty ≈ 6.7 → score ≈ 93.3
- 250,000 steps → penalty ≈ 16.7 → score ≈ 83.3
- 750,000 steps → penalty = 50.0 → score = 50.0
- 1,000,000 steps → penalty = 50.0 → score = 50.0 (cap)

## Stain Scenarios at 750k
Stains do **not** directly reduce the score unless they persist after cleaning. Persistent stain points are computed on clean and stored in `sneaker.persistent_stain_points`.

Current configured values (in `services/health_service.py`):
- Base stain points by severity: `{1: 0.6, 2: 1.2, 3: 1.8}`
- Stain points are multiplied by the **wet material multiplier** (suede/nubuck has higher impact).

Example at 750k steps (base score = 50):
- Persistent stain (severity 2, standard material) adds ~1.2 penalty → score ≈ 48.8.
- With suede/nubuck (wet multiplier 2.0), severity 2 becomes ~2.4 penalty → score ≈ 47.6.

## Debugging / Troubleshooting
### Why a score might not rebound after cleaning
- Steps **do not reset** on cleaning; a large step total can keep scores lower.
- Exposure rows may still be included if `last_cleaned_at` is not set or if the cleaned window is not moving forward as expected.
- Persistent penalties (`persistent_stain_points`, `persistent_material_damage_points`) remain after cleaning.

### How to check last_cleaned_at and exposure windowing
Check the sneaker record:

```sql
SELECT id, last_cleaned_at, persistent_stain_points, persistent_material_damage_points
FROM sneaker
WHERE id = 69;
```

The exposure window starts **one day after** the UTC date of `last_cleaned_at`. For example, if `last_cleaned_at = 2026-02-16 10:00:00`, then `cleaned_date_local = 2026-02-17`.

### Safe SQLite queries
Steps total (all time):

```sql
SELECT COALESCE(SUM(steps_attributed), 0) AS steps_total
FROM step_attribution
WHERE sneaker_id = 69
  AND bucket_granularity = 'day'
  AND algorithm_version = 'v1_equal_split_day';
```

Exposure sums since clean window:

```sql
SELECT
  COALESCE(SUM(wet_points), 0) AS wet_sum,
  COALESCE(SUM(dirty_points), 0) AS dirty_sum
FROM sneaker_exposure_attribution
WHERE sneaker_id = 69
  AND date_local >= DATE(last_cleaned_at, '+1 day')
  AND EXISTS (SELECT 1 FROM sneaker WHERE id = 69);
```

Recent health snapshots:

```sql
SELECT recorded_at, health_score, reason
FROM sneaker_health_snapshot
WHERE sneaker_id = 69
ORDER BY recorded_at DESC
LIMIT 10;
```
