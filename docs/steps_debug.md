# Fake Steps Verification

This guide provides a dev-only, end-to-end verification path for the steps pipeline:

StepBucket ingest -> StepAttribution compute -> UI reflects totals.

## Prereqs
- Create a user and add at least 1 sneaker in their collection.
- Ensure you have at least one wear date logged, or use the helper below.

## Seed wear dates (dev only)

```bash
flask wear:seed-fake --user-email you@example.com --days 14 --sneaker-ids 1,2
```

This alternates the sneaker ids by day.

## Seed fake step buckets (dev only)

```bash
flask steps:seed-fake --user-email you@example.com --days 14 --steps-min 6000 --steps-max 12000 --source apple_health
```

This upserts daily buckets and recomputes attribution for the range.

## Verify attribution totals

```bash
flask steps:verify --user-email you@example.com --days 14
```

Output includes per-day buckets, per-sneaker totals, and any days without wear data.

## Dev-only HTTP shortcut (admin only)

```bash
curl -X POST http://localhost:5000/dev/steps/seed \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"days":14,"steps_min":6000,"steps_max":12000,"source":"apple_health"}'
```

## UI checklist
- Collection cards show "Steps (Last 30 Days)".
- Sneaker detail page shows "Estimated Steps (Last 7 Days)" and "(Last 30 Days)".
