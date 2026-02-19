from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None

from extensions import db
from models import ExposureEvent, Sneaker, SneakerExposureAttribution, SneakerWear


DEFAULT_TZ = "Europe/London"


def resolve_timezone(tz_name: Optional[str]) -> str:
    tz_name = (tz_name or "").strip() or DEFAULT_TZ
    if ZoneInfo is None:
        return DEFAULT_TZ
    try:
        ZoneInfo(tz_name)
        return tz_name
    except Exception:
        return DEFAULT_TZ


def local_today(tz_name: Optional[str]) -> date:
    tz = resolve_timezone(tz_name)
    if ZoneInfo is None:
        return datetime.utcnow().date()
    return datetime.now(ZoneInfo(tz)).date()


def _normalize_severity(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    try:
        val = int(value)
    except (TypeError, ValueError):
        return None
    if val < 1:
        return 1
    if val > 3:
        return 3
    return val


def upsert_daily_exposure(
    user_id: int,
    date_local: date,
    timezone: Optional[str],
    got_wet: bool,
    got_dirty: bool,
    wet_severity: Optional[int],
    dirty_severity: Optional[int],
    stain_flag: bool = False,
    stain_severity: Optional[int] = None,
    note: Optional[str] = None,
) -> ExposureEvent:
    tz_name = resolve_timezone(timezone)
    wet_level = _normalize_severity(wet_severity) if got_wet else None
    dirty_level = _normalize_severity(dirty_severity) if got_dirty else None
    if got_wet and wet_level is None:
        wet_level = 2
    if got_dirty and dirty_level is None:
        dirty_level = 2
    stain_level = _normalize_severity(stain_severity) if stain_flag else None
    if stain_flag and stain_level is None:
        stain_level = 2
    cleaned_note = (note or "").strip()
    if cleaned_note:
        cleaned_note = cleaned_note[:140]
    else:
        cleaned_note = None

    exposure = (
        db.session.query(ExposureEvent)
        .filter_by(user_id=user_id, date_local=date_local)
        .first()
    )
    if exposure is None:
        exposure = ExposureEvent(
            user_id=user_id,
            date_local=date_local,
        )
        db.session.add(exposure)

    exposure.timezone = tz_name
    exposure.got_wet = bool(got_wet)
    exposure.got_dirty = bool(got_dirty)
    exposure.stain_flag = bool(stain_flag)
    exposure.wet_severity = wet_level
    exposure.dirty_severity = dirty_level
    exposure.stain_severity = stain_level
    exposure.note = cleaned_note
    exposure.updated_at = datetime.utcnow()
    db.session.flush()
    return exposure


def _sneakers_worn_on_date(user_id: int, date_local: date) -> List[int]:
    rows = (
        db.session.query(SneakerWear.sneaker_id)
        .join(Sneaker, Sneaker.id == SneakerWear.sneaker_id)
        .filter(Sneaker.user_id == user_id, SneakerWear.worn_at == date_local)
        .distinct()
        .all()
    )
    return [row.sneaker_id for row in rows]


def recompute_exposure_attributions(
    user_id: int,
    start_date: date,
    end_date: date,
) -> dict:
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    db.session.query(SneakerExposureAttribution).filter(
        SneakerExposureAttribution.user_id == user_id,
        SneakerExposureAttribution.date_local >= start_date,
        SneakerExposureAttribution.date_local <= end_date,
    ).delete(synchronize_session=False)

    exposures = (
        db.session.query(ExposureEvent)
        .filter(
            ExposureEvent.user_id == user_id,
            ExposureEvent.date_local >= start_date,
            ExposureEvent.date_local <= end_date,
        )
        .all()
    )

    written = 0
    for exposure in exposures:
        sneaker_ids = _sneakers_worn_on_date(user_id, exposure.date_local)
        if not sneaker_ids:
            continue

        total_wet = exposure.wet_severity if exposure.got_wet else 0
        total_dirty = exposure.dirty_severity if exposure.got_dirty else 0
        if not total_wet and not total_dirty:
            continue

        count = len(sneaker_ids)
        wet_split = float(total_wet) / count if total_wet else 0.0
        dirty_split = float(total_dirty) / count if total_dirty else 0.0

        for sneaker_id in sneaker_ids:
            db.session.add(
                SneakerExposureAttribution(
                    user_id=user_id,
                    sneaker_id=sneaker_id,
                    date_local=exposure.date_local,
                    wet_points=wet_split,
                    dirty_points=dirty_split,
                )
            )
            written += 1

    db.session.commit()
    return {"exposures_processed": len(exposures), "attributions_written": written}


def exposure_history(
    user_id: int,
    end_date: date,
    days: int = 7,
) -> List[dict]:
    start_date = end_date - timedelta(days=days - 1)
    rows = (
        db.session.query(ExposureEvent)
        .filter(
            ExposureEvent.user_id == user_id,
            ExposureEvent.date_local >= start_date,
            ExposureEvent.date_local <= end_date,
        )
        .all()
    )
    by_date = {row.date_local: row for row in rows}
    history = []
    for offset in range(days):
        current = start_date + timedelta(days=offset)
        event = by_date.get(current)
        history.append(
            {
                "date": current,
                "got_wet": bool(event.got_wet) if event else False,
                "got_dirty": bool(event.got_dirty) if event else False,
                "stain_flag": bool(event.stain_flag) if event else False,
                "wet_severity": event.wet_severity if event else None,
                "dirty_severity": event.dirty_severity if event else None,
                "stain_severity": event.stain_severity if event else None,
                "note": event.note if event else None,
            }
        )
    return history


def exposure_sums_for_sneaker(
    sneaker_id: int,
    user_id: int,
    since_date: Optional[date] = None,
) -> Tuple[float, float]:
    query = db.session.query(
        db.func.coalesce(db.func.sum(SneakerExposureAttribution.wet_points), 0.0),
        db.func.coalesce(db.func.sum(SneakerExposureAttribution.dirty_points), 0.0),
    ).filter(
        SneakerExposureAttribution.user_id == user_id,
        SneakerExposureAttribution.sneaker_id == sneaker_id,
    )
    if since_date:
        query = query.filter(SneakerExposureAttribution.date_local >= since_date)
    wet_sum, dirty_sum = query.first() or (0.0, 0.0)
    return float(wet_sum or 0.0), float(dirty_sum or 0.0)


def material_sensitivity_multipliers(materials: Iterable[str]) -> Tuple[float, float]:
    lowered = {m.lower() for m in materials if m}
    if any(m in lowered for m in ["suede", "nubuck"]):
        return 2.0, 1.6
    if any("leather" in m for m in lowered):
        return 1.4, 1.2
    if any(m in lowered for m in ["mesh", "knit", "canvas"]):
        return 1.1, 1.3
    return 1.0, 1.0
