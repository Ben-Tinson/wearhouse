from datetime import datetime, date, time, timezone
from typing import Dict, Iterable, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from sqlalchemy import and_

from extensions import db
from models import User, Sneaker, SneakerWear, StepBucket, StepAttribution


ALGORITHM_V1 = "v1_equal_split_day"
DEFAULT_TIMEZONE = "Europe/London"


def _to_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_datetime(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return _to_utc_naive(value)
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    return None


def _local_date(bucket_start: datetime, timezone_name: Optional[str]) -> date:
    if timezone_name and ZoneInfo is not None:
        try:
            local_tz = ZoneInfo(timezone_name)
            return bucket_start.replace(tzinfo=timezone.utc).astimezone(local_tz).date()
        except Exception:
            pass
    return bucket_start.date()


def _sneakers_worn_on_date(user_id: int, local_date: date) -> List[int]:
    rows = (
        db.session.query(SneakerWear.sneaker_id)
        .join(Sneaker, Sneaker.id == SneakerWear.sneaker_id)
        .filter(
            Sneaker.user_id == user_id,
            SneakerWear.worn_at == local_date,
        )
        .distinct()
        .order_by(SneakerWear.sneaker_id.asc())
        .all()
    )
    return [row[0] for row in rows]


def recompute_attribution(
    user_id: int,
    granularity: str,
    start: datetime,
    end: datetime,
    algorithm_version: str = ALGORITHM_V1,
) -> Dict[str, int]:
    start_dt = _parse_datetime(start)
    end_dt = _parse_datetime(end)
    if not start_dt or not end_dt:
        raise ValueError("Invalid start/end range for attribution.")

    if granularity != "day":
        raise ValueError("Unsupported granularity for v1 attribution.")

    bucket_query = (
        StepBucket.query.filter(
            StepBucket.user_id == user_id,
            StepBucket.granularity == granularity,
            StepBucket.bucket_start >= start_dt,
            StepBucket.bucket_start < end_dt,
        )
        .order_by(StepBucket.bucket_start.asc())
    )

    StepAttribution.query.filter(
        StepAttribution.user_id == user_id,
        StepAttribution.bucket_granularity == granularity,
        StepAttribution.algorithm_version == algorithm_version,
        StepAttribution.bucket_start >= start_dt,
        StepAttribution.bucket_start < end_dt,
    ).delete(synchronize_session=False)

    buckets_processed = 0
    attributions_written = 0
    user = db.session.get(User, user_id)
    fallback_timezone = (user.timezone if user else None) or DEFAULT_TIMEZONE

    for bucket in bucket_query:
        buckets_processed += 1
        timezone_name = bucket.timezone or fallback_timezone
        local_date = _local_date(bucket.bucket_start, timezone_name)
        sneaker_ids = _sneakers_worn_on_date(user_id, local_date)
        if not sneaker_ids:
            continue

        count = len(sneaker_ids)
        base_steps = bucket.steps // count
        remainder = bucket.steps % count

        for idx, sneaker_id in enumerate(sneaker_ids):
            steps = base_steps + (1 if idx < remainder else 0)
            attribution = StepAttribution(
                user_id=user_id,
                sneaker_id=sneaker_id,
                bucket_granularity=granularity,
                bucket_start=bucket.bucket_start,
                steps_attributed=steps,
                algorithm_version=algorithm_version,
                computed_at=datetime.utcnow(),
            )
            db.session.add(attribution)
            attributions_written += 1

    db.session.commit()
    return {
        "buckets_processed": buckets_processed,
        "attributions_written": attributions_written,
    }
