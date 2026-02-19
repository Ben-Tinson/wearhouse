from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional
import random

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from sqlalchemy import func

from extensions import db
from models import Sneaker, SneakerWear, StepBucket, StepAttribution
from services.steps_attribution_service import recompute_attribution, ALGORITHM_V1


DEFAULT_TZ = "Europe/London"


def _resolve_timezone(name: Optional[str]):
    if name and ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return timezone.utc


def _to_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _bucket_bounds(local_date: date, tz) -> (datetime, datetime):
    local_start = datetime.combine(local_date, time.min).replace(tzinfo=tz)
    local_end = local_start + timedelta(days=1)
    return _to_utc_naive(local_start), _to_utc_naive(local_end)


def _deterministic_steps(local_date: date, steps_min: int, steps_max: int, seed_base: str) -> int:
    rng = random.Random(f"{seed_base}-{local_date.isoformat()}")
    return rng.randint(steps_min, steps_max)


def seed_fake_steps(
    user_id: int,
    days: int = 14,
    steps_min: int = 6000,
    steps_max: int = 12000,
    source: str = "apple_health",
    granularity: str = "day",
    timezone_name: Optional[str] = None,
    seed: Optional[str] = None,
) -> Dict[str, int]:
    if days <= 0:
        raise ValueError("Days must be greater than 0.")
    if steps_min < 0 or steps_max < 0 or steps_min > steps_max:
        raise ValueError("Invalid steps_min/steps_max values.")
    if granularity not in {"day", "hour"}:
        raise ValueError("Invalid granularity value.")

    tz_name = timezone_name or DEFAULT_TZ
    tz = _resolve_timezone(tz_name)
    local_today = datetime.now(tz).date()
    start_date = local_today - timedelta(days=days - 1)
    seed_base = seed or str(user_id)

    upserted = 0
    updated = 0
    bucket_starts: List[datetime] = []

    for offset in range(days):
        local_date = start_date + timedelta(days=offset)
        bucket_start, bucket_end = _bucket_bounds(local_date, tz)
        steps = _deterministic_steps(local_date, steps_min, steps_max, seed_base)

        existing = StepBucket.query.filter_by(
            user_id=user_id,
            source=source,
            granularity=granularity,
            bucket_start=bucket_start,
        ).first()

        if existing:
            existing.bucket_end = bucket_end
            existing.steps = steps
            existing.timezone = tz_name
            updated += 1
        else:
            db.session.add(
                StepBucket(
                    user_id=user_id,
                    source=source,
                    granularity=granularity,
                    bucket_start=bucket_start,
                    bucket_end=bucket_end,
                    steps=steps,
                    timezone=tz_name,
                )
            )
            upserted += 1
        bucket_starts.append(bucket_start)

    db.session.commit()

    recompute_stats = None
    if granularity == "day" and bucket_starts:
        recompute_stats = recompute_attribution(
            user_id=user_id,
            granularity="day",
            start=min(bucket_starts),
            end=max(bucket_starts) + timedelta(days=1),
            algorithm_version=ALGORITHM_V1,
        )

    return {
        "buckets_upserted": upserted,
        "buckets_updated": updated,
        "buckets_processed": recompute_stats["buckets_processed"] if recompute_stats else 0,
        "attributions_written": recompute_stats["attributions_written"] if recompute_stats else 0,
        "start_date": start_date.isoformat(),
        "end_date": local_today.isoformat(),
    }


def seed_fake_wear(
    user_id: int,
    days: int,
    sneaker_ids: List[int],
    timezone_name: Optional[str] = None,
) -> Dict[str, int]:
    if days <= 0:
        raise ValueError("Days must be greater than 0.")
    if not sneaker_ids:
        raise ValueError("At least one sneaker_id is required.")

    tz = _resolve_timezone(timezone_name or DEFAULT_TZ)
    local_today = datetime.now(tz).date()
    start_date = local_today - timedelta(days=days - 1)
    end_date = local_today

    existing_rows = (
        db.session.query(SneakerWear.sneaker_id, SneakerWear.worn_at)
        .filter(
            SneakerWear.sneaker_id.in_(sneaker_ids),
            SneakerWear.worn_at >= start_date,
            SneakerWear.worn_at <= end_date,
        )
        .all()
    )
    existing = {(row[0], row[1]) for row in existing_rows}

    created = 0
    for offset in range(days):
        local_date = start_date + timedelta(days=offset)
        sneaker_id = sneaker_ids[offset % len(sneaker_ids)]
        if (sneaker_id, local_date) in existing:
            continue
        db.session.add(SneakerWear(sneaker_id=sneaker_id, worn_at=local_date))
        created += 1

    db.session.commit()
    return {
        "wears_created": created,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }


def verify_steps_attribution(
    user_id: int,
    days: int,
    granularity: str = "day",
    algorithm_version: str = ALGORITHM_V1,
) -> Dict[str, object]:
    if granularity not in {"day", "hour"}:
        raise ValueError("Invalid granularity value.")

    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days - 1)
    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(end_date + timedelta(days=1), time.min)

    buckets = (
        StepBucket.query.filter(
            StepBucket.user_id == user_id,
            StepBucket.granularity == granularity,
            StepBucket.bucket_start >= start_dt,
            StepBucket.bucket_start < end_dt,
        )
        .order_by(StepBucket.bucket_start.asc())
        .all()
    )

    attributions = (
        db.session.query(
            StepAttribution.sneaker_id,
            func.sum(StepAttribution.steps_attributed).label("steps_total"),
        )
        .filter(
            StepAttribution.user_id == user_id,
            StepAttribution.bucket_granularity == granularity,
            StepAttribution.algorithm_version == algorithm_version,
            StepAttribution.bucket_start >= start_dt,
            StepAttribution.bucket_start < end_dt,
        )
        .group_by(StepAttribution.sneaker_id)
        .all()
    )

    sneaker_names = {
        row.id: f"{row.brand or ''} {row.model or ''}".strip()
        for row in db.session.query(Sneaker.id, Sneaker.brand, Sneaker.model)
        .filter(Sneaker.user_id == user_id)
        .all()
    }

    bucket_lines = [
        {"date": bucket.bucket_start.date().isoformat(), "steps": bucket.steps}
        for bucket in buckets
    ]
    attribution_lines = [
        {
            "sneaker_id": row.sneaker_id,
            "sneaker_name": sneaker_names.get(row.sneaker_id, "Unknown"),
            "steps": int(row.steps_total or 0),
        }
        for row in attributions
    ]

    total_bucket_steps = sum(bucket.steps for bucket in buckets)
    total_attributed_steps = sum(line["steps"] for line in attribution_lines)

    missing_wear_days = []
    for bucket in buckets:
        local_date = bucket.bucket_start.date()
        worn = (
            db.session.query(SneakerWear.id)
            .join(Sneaker, Sneaker.id == SneakerWear.sneaker_id)
            .filter(
                Sneaker.user_id == user_id,
                SneakerWear.worn_at == local_date,
            )
            .first()
        )
        if worn is None:
            missing_wear_days.append(local_date.isoformat())

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "bucket_lines": bucket_lines,
        "attribution_lines": attribution_lines,
        "total_bucket_steps": total_bucket_steps,
        "total_attributed_steps": total_attributed_steps,
        "missing_wear_days": missing_wear_days,
    }
