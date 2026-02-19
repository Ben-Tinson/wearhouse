from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Iterable, Optional
import logging

from sqlalchemy import func

from extensions import db
from models import (
    StepAttribution,
    StepBucket,
    Sneaker,
    SneakerWear,
    ExposureEvent,
    SneakerRepairEvent,
    SneakerDamageEvent,
)
from services.exposure_service import exposure_sums_for_sneaker, material_sensitivity_multipliers
from services.steps_attribution_service import ALGORITHM_V1


STAIN_BASE_POINTS = {
    1: 0.6,
    2: 1.2,
    3: 1.8,
}
MATERIAL_DAMAGE_BASE_POINTS = 2.0
RESTORE_STEPS_THRESHOLD = 750_000
MAX_STEPS_PENALTY = 50.0
HYGIENE_MAX_PENALTY = 5.0
HYGIENE_FULL_PENALTY_WEARS = 10

# Structural damage has higher penalties than cosmetic.
DAMAGE_PENALTY_POINTS = {
    "tear_upper": {1: 8.0, 2: 15.0, 3: 25.0},
    "sole_separation": {1: 10.0, 2: 20.0, 3: 30.0},
    "midsole_crumble": {1: 15.0, 2: 30.0, 3: 45.0},
    "outsole_wear": {1: 10.0, 2: 20.0, 3: 30.0},
    "upper_scuff": {1: 4.0, 2: 8.0, 3: 12.0},
    "upper_paint_chip": {1: 5.0, 2: 10.0, 3: 15.0},
    "midsole_scuff": {1: 3.0, 2: 6.0, 3: 10.0},
    "midsole_paint_chip": {1: 4.0, 2: 8.0, 3: 12.0},
    "other": {1: 5.0, 2: 10.0, 3: 15.0},
}

LEGACY_DAMAGE_TYPE_MAP = {
    "scuff": "midsole_scuff",
    "tear (knit/upper)": "tear_upper",
    "tear_knit": "tear_upper",
    "midsole crumble": "midsole_crumble",
    "sole separation": "sole_separation",
    "other": "other",
}


def normalize_damage_type(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return "other"
    raw = raw.replace("-", "_")
    if raw in LEGACY_DAMAGE_TYPE_MAP:
        return LEGACY_DAMAGE_TYPE_MAP[raw]
    if raw in DAMAGE_PENALTY_POINTS:
        return raw
    return "other"

CARE_TAGS = {
    "suede_or_nubuck": ["suede", "nubuck"],
    "knit_mesh": ["knit", "mesh", "flyknit", "primeknit"],
    "patent_leather": ["patent leather"],
    "canvas": ["canvas", "denim"],
    "rubber_foam": ["rubber", "foam", "eva foam", "tpu"],
}

CARE_TAG_LABELS = {
    "suede_or_nubuck": "Suede/Nubuck",
    "knit_mesh": "Knit/Mesh",
    "patent_leather": "Patent leather",
    "canvas": "Canvas",
    "rubber_foam": "Rubber/Foam",
}


def derive_care_tags(materials: Iterable[str]) -> list:
    lowered = {m.lower() for m in materials if m}
    tags = []
    for tag, keywords in CARE_TAGS.items():
        if any(keyword in lowered for keyword in keywords):
            tags.append(tag)
    return tags


def _confidence_label(score: float) -> str:
    if score >= 80:
        return "High"
    if score >= 55:
        return "Medium"
    return "Low"


def _compute_confidence_score(user_id: int, sneaker_id: int, last_cleaned_at: Optional[datetime]) -> Dict[str, object]:
    score = 40.0
    since_30 = datetime.utcnow() - timedelta(days=30)
    since_14 = datetime.utcnow().date() - timedelta(days=14)

    bucket_days = (
        db.session.query(func.count(func.distinct(StepBucket.bucket_start)))
        .filter(
            StepBucket.user_id == user_id,
            StepBucket.granularity == "day",
            StepBucket.bucket_start >= since_30,
        )
        .scalar()
    ) or 0
    if bucket_days >= 20:
        score += 20

    step_attr_exists = (
        db.session.query(StepAttribution.id)
        .filter(
            StepAttribution.user_id == user_id,
            StepAttribution.sneaker_id == sneaker_id,
            StepAttribution.bucket_granularity == "day",
            StepAttribution.algorithm_version == ALGORITHM_V1,
            StepAttribution.bucket_start >= since_30,
        )
        .first()
        is not None
    )
    if step_attr_exists:
        score += 15

    exposure_exists = (
        db.session.query(ExposureEvent.id)
        .filter(
            ExposureEvent.user_id == user_id,
            ExposureEvent.date_local >= since_30.date(),
        )
        .first()
        is not None
    )
    if exposure_exists:
        score += 10

    if last_cleaned_at:
        score += 10

    wear_recent = (
        db.session.query(SneakerWear.id)
        .filter(
            SneakerWear.sneaker_id == sneaker_id,
            SneakerWear.worn_at >= since_14,
        )
        .first()
        is not None
    )
    if wear_recent:
        score += 5

    provider_bonus = 0
    since_180 = datetime.utcnow() - timedelta(days=180)
    recent_repairs = (
        db.session.query(SneakerRepairEvent)
        .filter(
            SneakerRepairEvent.user_id == user_id,
            SneakerRepairEvent.sneaker_id == sneaker_id,
            SneakerRepairEvent.repaired_at >= since_180,
        )
        .order_by(SneakerRepairEvent.repaired_at.desc())
        .all()
    )
    if recent_repairs:
        provider_bonus_map = {
            "brand": 6,
            "specialist_restorer": 6,
            "local_cobbler": 6,
            "retailer": 4,
            "self": 2,
        }
        for event in recent_repairs:
            provider_bonus = max(provider_bonus, provider_bonus_map.get(event.provider or "", 0))
        if any(event.cost_amount and float(event.cost_amount) > 0 for event in recent_repairs):
            provider_bonus += 1
    score += provider_bonus

    score = max(0.0, min(100.0, score))
    return {"score": score, "label": _confidence_label(score)}

logger = logging.getLogger(__name__)


def exposure_since_date(last_cleaned_at: Optional[datetime]) -> Optional[datetime.date]:
    if not last_cleaned_at:
        return None
    return last_cleaned_at.date() + timedelta(days=1)


def has_sensitive_suede_materials(materials: Iterable[str]) -> bool:
    lowered = {m.lower() for m in materials if m}
    return any(m in lowered for m in ["suede", "nubuck"])


def compute_persistent_stain_points(stain_severity: Optional[int], materials: Iterable[str]) -> float:
    if not stain_severity:
        return 0.0
    base = STAIN_BASE_POINTS.get(int(stain_severity), STAIN_BASE_POINTS[2])
    wet_multiplier, _ = material_sensitivity_multipliers(materials)
    return round(base * wet_multiplier, 2)


def compute_material_damage_points(materials: Iterable[str]) -> float:
    wet_multiplier, _ = material_sensitivity_multipliers(materials)
    return round(MATERIAL_DAMAGE_BASE_POINTS * wet_multiplier, 2)


def compute_damage_penalty_points(damage_type: str, severity: int) -> float:
    safe_type = normalize_damage_type(damage_type)
    safe_severity = int(severity) if severity in {1, 2, 3} else 2
    return float(DAMAGE_PENALTY_POINTS.get(safe_type, DAMAGE_PENALTY_POINTS["other"]).get(safe_severity, 10.0))


def compute_health_components(
    sneaker: Sneaker,
    user_id: int,
    materials: Iterable[str],
    steps_total: Optional[int] = None,
) -> Dict[str, float]:
    if steps_total is None:
        steps_total = (
            db.session.query(func.sum(StepAttribution.steps_attributed))
            .filter(
                StepAttribution.user_id == user_id,
                StepAttribution.sneaker_id == sneaker.id,
                StepAttribution.bucket_granularity == "day",
                StepAttribution.algorithm_version == ALGORITHM_V1,
            )
            .scalar()
        )
    steps_total_value = float(steps_total or 0)
    steps_penalty = 0.0
    if steps_total_value:
        steps_penalty = min(
            MAX_STEPS_PENALTY,
            MAX_STEPS_PENALTY * (steps_total_value / RESTORE_STEPS_THRESHOLD),
        )

    wears_since_clean = 0
    hygiene_penalty = 0.0
    if sneaker.last_cleaned_at:
        cleaned_date = sneaker.last_cleaned_at.date() + timedelta(days=1)
        wears_since_clean = (
            db.session.query(func.count(SneakerWear.id))
            .filter(
                SneakerWear.sneaker_id == sneaker.id,
                SneakerWear.worn_at >= cleaned_date,
            )
            .scalar()
        ) or 0
        hygiene_penalty = min(
            HYGIENE_MAX_PENALTY,
            HYGIENE_MAX_PENALTY * (wears_since_clean / HYGIENE_FULL_PENALTY_WEARS),
        )
    active_damage_events = (
        db.session.query(SneakerDamageEvent.damage_type, SneakerDamageEvent.severity)
        .filter(
            SneakerDamageEvent.sneaker_id == sneaker.id,
            SneakerDamageEvent.user_id == user_id,
            SneakerDamageEvent.is_active.is_(True),
        )
        .all()
    )
    active_damage_count = len(active_damage_events)

    since_cleaned_date = exposure_since_date(sneaker.last_cleaned_at)
    wet_points_sum, dirty_points_sum = exposure_sums_for_sneaker(
        sneaker.id, user_id, since_date=since_cleaned_date
    )
    wet_multiplier, dirty_multiplier = material_sensitivity_multipliers(materials)
    wet_penalty = wet_points_sum * wet_multiplier
    dirty_penalty = dirty_points_sum * dirty_multiplier

    persistent_stain_points = float(sneaker.persistent_stain_points or 0.0)
    persistent_material_damage_points = float(sneaker.persistent_material_damage_points or 0.0)
    persistent_structural_damage_points = float(sneaker.persistent_structural_damage_points or 0.0)

    wear_penalty = steps_penalty
    cosmetic_penalty = wet_penalty + dirty_penalty + persistent_stain_points
    structural_penalty = persistent_material_damage_points + persistent_structural_damage_points

    total_penalty = (
        wear_penalty
        + cosmetic_penalty
        + structural_penalty
        + hygiene_penalty
    )
    starting_health = float(getattr(sneaker, "starting_health", 100.0) or 100.0)
    health_score = max(0.0, min(100.0, round(starting_health - total_penalty, 1)))

    recommendation_state = "none"
    recommendation_label = None
    recommendation_reason = None

    normalized_active = [
        (normalize_damage_type(damage_type), severity or 1)
        for damage_type, severity in active_damage_events
    ]
    active_types = {dtype for dtype, _ in normalized_active}
    only_outsole = bool(active_types) and active_types.issubset({"outsole_wear"})
    has_sole_separation = any(dtype == "sole_separation" for dtype, _ in normalized_active)
    has_midsole_crumble = any(dtype == "midsole_crumble" for dtype, _ in normalized_active)
    has_tear_upper_severe = any(
        dtype == "tear_upper" and severity >= 2 for dtype, severity in normalized_active
    )
    has_tear_upper_light = any(
        dtype == "tear_upper" and severity < 2 for dtype, severity in normalized_active
    )
    outsole_severity_max = max(
        (severity for dtype, severity in normalized_active if dtype == "outsole_wear"),
        default=0,
    )

    clean_recommended = (
        wears_since_clean > 0
        and (hygiene_penalty >= 2.0 or cosmetic_penalty >= 5.0)
    )
    restore_recommended = (
        steps_penalty >= 45.0 or steps_total_value >= RESTORE_STEPS_THRESHOLD
    )
    outsole_restore = (
        outsole_severity_max >= 2
        and (steps_penalty >= 35.0 or steps_total_value >= 500_000)
    )
    if restore_recommended or outsole_restore:
        recommendation_state = "restore"
        recommendation_label = "Restore"
        if outsole_restore and steps_total_value < RESTORE_STEPS_THRESHOLD:
            recommendation_reason = "Severe outsole wear plus high mileage suggests restoration."
        elif steps_total_value >= RESTORE_STEPS_THRESHOLD:
            recommendation_reason = "High step total is pushing the wear penalty to the restore zone."
        else:
            recommendation_reason = "Wear penalty is near the cap, signalling restoration territory."
    elif has_sole_separation or has_midsole_crumble or has_tear_upper_severe:
        recommendation_state = "repair"
        recommendation_label = "Repair"
        if has_sole_separation:
            recommendation_reason = "Sole separation needs attention."
        elif has_midsole_crumble:
            recommendation_reason = "Midsole crumbling is a structural issue."
        else:
            recommendation_reason = "Tears in the upper are significant."
    elif structural_penalty >= 12.0 and not only_outsole:
        recommendation_state = "repair"
        recommendation_label = "Repair"
        recommendation_reason = "Structural penalties are significant."
    elif 6.0 <= structural_penalty < 12.0 and not only_outsole:
        recommendation_state = "watch"
        recommendation_label = "Minor wear logged"
        recommendation_reason = "Structural wear is present but not urgent."
    elif has_tear_upper_light:
        recommendation_state = "watch"
        recommendation_label = "Minor wear logged"
        recommendation_reason = "Light upper wear is logged."
    elif only_outsole and outsole_severity_max >= 1:
        recommendation_state = "watch"
        recommendation_label = "Minor wear logged"
        recommendation_reason = "Outsole wear is logged."
    elif clean_recommended:
        recommendation_state = "clean"
        recommendation_label = "Clean"
        if hygiene_penalty >= 2.0:
            recommendation_reason = "Several wears since the last clean are affecting the score."
        else:
            recommendation_reason = "Cosmetic exposure penalties are elevated since the last clean."

    status_label = None
    if has_sole_separation or has_midsole_crumble or has_tear_upper_severe:
        status_label = "Needs attention"
    else:
        if health_score >= 90:
            status_label = "Healthy"
        elif health_score >= 80:
            status_label = "OK"
        elif health_score >= 65:
            status_label = "Monitor"
        else:
            status_label = "Needs attention"

    confidence = _compute_confidence_score(user_id, sneaker.id, sneaker.last_cleaned_at)

    logger.info(
        "health_score sneaker_id=%s starting_health=%.1f steps_total=%s wear=%.2f cosmetic=%.2f structural=%.2f hygiene=%.2f "
        "wet_points=%.2f dirty_points=%.2f persistent_stain=%.2f persistent_material_damage=%.2f "
        "persistent_structural_damage=%.2f health_score=%.1f confidence=%.1f",
        sneaker.id,
        starting_health,
        int(steps_total_value),
        wear_penalty,
        cosmetic_penalty,
        structural_penalty,
        hygiene_penalty,
        wet_points_sum,
        dirty_points_sum,
        persistent_stain_points,
        persistent_material_damage_points,
        persistent_structural_damage_points,
        health_score,
        confidence["score"],
    )

    return {
        "steps_penalty": steps_penalty,
        "steps_total": steps_total_value,
        "wet_points_sum": wet_points_sum,
        "dirty_points_sum": dirty_points_sum,
        "wet_penalty": wet_penalty,
        "dirty_penalty": dirty_penalty,
        "persistent_stain_points": persistent_stain_points,
        "persistent_material_damage_points": persistent_material_damage_points,
        "persistent_structural_damage_points": persistent_structural_damage_points,
        "wear_penalty": wear_penalty,
        "cosmetic_penalty": cosmetic_penalty,
        "structural_penalty": structural_penalty,
        "hygiene_penalty": hygiene_penalty,
        "wears_since_clean": wears_since_clean,
        "active_damage_count": int(active_damage_count),
        "confidence_score": confidence["score"],
        "confidence_label": confidence["label"],
        "health_score": health_score,
        "recommendation_state": recommendation_state,
        "recommendation_label": recommendation_label,
        "recommendation_reason": recommendation_reason,
        "status_label": status_label,
    }
