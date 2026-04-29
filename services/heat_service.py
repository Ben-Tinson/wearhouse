from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Tuple
import re

from sqlalchemy.orm import joinedload
from sqlalchemy import func

from models import Release, ReleaseSalePoint, ReleaseSalesMonthly, ReleaseSizeBid


HEAT_SCORE_BUCKETS = [
    (1.10, 10),
    (1.30, 25),
    (1.60, 45),
    (2.00, 65),
    (2.50, 80),
    (3.00, 90),
]

HEAT_PREMIUM_MIN = 0.5
HEAT_PREMIUM_MAX = 5.0
ASK_CAP_FAR = 1.30
ASK_CAP_NEAR = 1.60


def slug_tokens(text: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


def derive_model_family(text: str) -> Optional[str]:
    tokens = slug_tokens(text)
    if not tokens:
        return None
    if tokens[0] == "jordan" and len(tokens) > 1 and tokens[1].isdigit():
        return f"jordan {tokens[1]}"
    if tokens[0] == "dunk":
        for next_token in tokens[1:3]:
            if next_token in {"low", "high", "sb"}:
                return f"dunk {next_token}"
        return "dunk"
    if tokens[0] == "air" and len(tokens) > 2:
        if tokens[1] in {"max", "force"} and tokens[2].isdigit():
            return f"air {tokens[1]} {tokens[2]}"
    return " ".join(tokens[:2])


def _median(values: List[Decimal]) -> Optional[Decimal]:
    if not values:
        return None
    values = sorted(values)
    mid = len(values) // 2
    if len(values) % 2 == 1:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


def _damping(days_to_release: int) -> float:
    if days_to_release >= 60:
        return 0.35
    return 0.35 + (1 - days_to_release / 60) * 0.65


def _fetch_size_bid_values(
    db_session,
    release_id: int,
    price_type: str,
    currency: Optional[str],
) -> List[Decimal]:
    query = db_session.query(ReleaseSizeBid.highest_bid).filter(
        ReleaseSizeBid.release_id == release_id,
        ReleaseSizeBid.price_type == price_type,
    )
    if currency:
        query = query.filter(ReleaseSizeBid.currency == currency)
    return [row[0] for row in query.all() if row[0] is not None]


def _fetch_recent_sales(
    db_session,
    release_id: int,
    currency: Optional[str],
    since: datetime,
) -> List[Decimal]:
    query = db_session.query(ReleaseSalePoint.price).filter(
        ReleaseSalePoint.release_id == release_id,
        ReleaseSalePoint.sale_at >= since,
    )
    if currency:
        query = query.filter(ReleaseSalePoint.currency == currency)
    return [row[0] for row in query.all() if row[0] is not None]


def _fetch_latest_monthly(
    db_session,
    release_id: int,
    currency: Optional[str],
) -> Optional[ReleaseSalesMonthly]:
    query = db_session.query(ReleaseSalesMonthly).filter(
        ReleaseSalesMonthly.release_id == release_id,
    )
    if currency:
        query = query.filter(ReleaseSalesMonthly.currency == currency)
    return query.order_by(ReleaseSalesMonthly.month_start.desc()).first()


def get_market_snapshot(db_session, release: Release, today: Optional[date] = None) -> Dict[str, Optional[Decimal]]:
    today = today or date.today()
    currency = release.retail_currency

    ask_values = _fetch_size_bid_values(db_session, release.id, "ask", currency)
    bid_values = _fetch_size_bid_values(db_session, release.id, "bid", currency)
    since = datetime.combine(today - timedelta(days=30), datetime.min.time())
    recent_sales_values = _fetch_recent_sales(db_session, release.id, currency, since)
    latest_monthly = _fetch_latest_monthly(db_session, release.id, currency)

    return {
        "ask_count": len(ask_values),
        "bid_count": len(bid_values),
        "asks_median": _median(ask_values),
        "bids_median": _median(bid_values),
        "recent_sales_count": len(recent_sales_values),
        "recent_sales_median": _median(recent_sales_values),
        "monthly_avg": latest_monthly.avg_price if latest_monthly else None,
        "monthly_count": 1 if latest_monthly else 0,
        "has_sales": bool(latest_monthly or recent_sales_values),
    }


def get_resale_estimate(db_session, release: Release, today: Optional[date] = None) -> Tuple[Optional[Decimal], Optional[str], int]:
    snapshot = get_market_snapshot(db_session, release, today)

    if snapshot["monthly_avg"] is not None:
        return snapshot["monthly_avg"], "monthly_avg", snapshot["monthly_count"]
    if snapshot["recent_sales_median"] is not None:
        return snapshot["recent_sales_median"], "recent_sales", snapshot["recent_sales_count"]
    if snapshot["bids_median"] is not None:
        return snapshot["bids_median"], "bids_median", snapshot["bid_count"]
    if snapshot["asks_median"] is not None:
        return snapshot["asks_median"], "asks_median", snapshot["ask_count"]
    return None, None, 0


def get_comps_for_release(db_session, release: Release, max_comps: int = 200) -> List[Release]:
    if not release.brand:
        return []
    today = date.today()
    cutoff = today - timedelta(days=14)
    query = (
        db_session.query(Release)
        .options(joinedload(Release.sales_points), joinedload(Release.sales_monthly))
        .filter(
            Release.brand == release.brand,
            Release.release_date.isnot(None),
            Release.release_date <= cutoff,
        )
    )
    if release.retail_price:
        lower = float(release.retail_price) * 0.8
        upper = float(release.retail_price) * 1.2
        query = query.filter(Release.retail_price.isnot(None))
        query = query.filter(Release.retail_price >= lower, Release.retail_price <= upper)
    candidates = query.limit(max_comps * 3).all()
    target_family = derive_model_family(release.model_name or release.name)
    comps = []
    for candidate in candidates:
        if candidate.id == release.id:
            continue
        candidate_family = derive_model_family(candidate.model_name or candidate.name)
        if target_family and candidate_family and candidate_family != target_family:
            continue
        comps.append(candidate)
        if len(comps) >= max_comps:
            break
    return comps


def get_comps_ratio(db_session, release: Release) -> Tuple[Optional[float], int]:
    comps = get_comps_for_release(db_session, release)
    ratios: List[Decimal] = []
    for comp in comps:
        if not comp.retail_price or not comp.retail_currency:
            continue
        resale, _, _ = get_resale_estimate(db_session, comp)
        if resale is None:
            continue
        if comp.retail_currency != release.retail_currency:
            continue
        retail = Decimal(comp.retail_price)
        if retail <= 0:
            continue
        ratios.append(resale / retail)
    median_ratio = _median(ratios)
    return (float(median_ratio) if median_ratio is not None else None, len(ratios))


def _heat_score_from_premium(premium_ratio: float) -> int:
    if premium_ratio <= HEAT_SCORE_BUCKETS[0][0]:
        return HEAT_SCORE_BUCKETS[0][1]
    for threshold, score in HEAT_SCORE_BUCKETS[1:]:
        if premium_ratio < threshold:
            return score
    return 100


def heat_label_for_score(score: Optional[float]) -> Optional[Tuple[str, str]]:
    if score is None:
        return None
    if score < 25:
        return "Low", "🔥"
    if score < 50:
        return "Medium", "🔥🔥"
    if score < 75:
        return "High", "🔥🔥🔥"
    return "Very high", "🔥🔥🔥🔥"


def _confidence_from_basis(
    basis: str,
    comps_n: int,
    sales_count: int,
    bid_count: int,
) -> str:
    if basis.startswith("asks_"):
        return "low"
    if basis in {"sales_based", "monthly_avg", "recent_sales"}:
        if sales_count >= 10:
            return "high"
        if sales_count >= 3:
            return "medium"
        return "low"
    if basis in {"bids_based", "bids_median"}:
        if bid_count >= 15:
            return "high"
        if bid_count >= 5:
            return "medium"
        return "low"
    if comps_n >= 15:
        return "high"
    if comps_n >= 8:
        return "medium"
    return "low"


def should_recompute_heat(release: Release, now: datetime) -> bool:
    if not release.heat_updated_at:
        return True
    if release.release_date and release.release_date >= date.today():
        return release.heat_updated_at <= now - timedelta(hours=24)
    return release.heat_updated_at <= now - timedelta(days=7)


def compute_heat_for_release(db_session, release: Release, now: Optional[datetime] = None, force: bool = False) -> None:
    now = now or datetime.utcnow()
    if not force and not should_recompute_heat(release, now):
        return
    if not release.retail_price or not release.retail_currency:
        release.heat_score = None
        release.heat_confidence = "low"
        release.heat_premium_ratio = None
        release.heat_basis = "insufficient_data"
        release.heat_updated_at = now
        return

    retail = Decimal(release.retail_price)
    if retail <= 0:
        release.heat_score = None
        release.heat_confidence = "low"
        release.heat_premium_ratio = None
        release.heat_basis = "insufficient_data"
        release.heat_updated_at = now
        return

    today = date.today()
    snapshot = get_market_snapshot(db_session, release, today)
    ask_count = snapshot["ask_count"] or 0
    bid_count = snapshot["bid_count"] or 0
    has_sales = bool(snapshot["has_sales"])

    comps_ratio, comps_n = get_comps_ratio(db_session, release)

    predicted: Optional[float] = None
    basis = "insufficient_data"
    sales_count = snapshot["recent_sales_count"] or 0

    upcoming = bool(release.release_date and release.release_date > today)
    if upcoming:
        if has_sales and snapshot["recent_sales_median"] is not None:
            predicted = float(snapshot["recent_sales_median"] / retail)
            basis = "sales_based"
            sales_count = snapshot["recent_sales_count"] or 0
        elif has_sales and snapshot["monthly_avg"] is not None:
            predicted = float(snapshot["monthly_avg"] / retail)
            basis = "sales_based"
            sales_count = snapshot["monthly_count"] or 0
        elif bid_count > 0 and snapshot["bids_median"] is not None:
            predicted = float(snapshot["bids_median"] / retail)
            basis = "bids_based"
        elif ask_count > 0 and bid_count == 0 and not has_sales:
            if comps_ratio is not None and comps_n >= 8 and snapshot["asks_median"] is not None:
                raw_ask_ratio = float(snapshot["asks_median"] / retail)
                days_to_release = max(0, (release.release_date - today).days)
                damped_ask = 1 + (raw_ask_ratio - 1) * _damping(days_to_release)
                predicted = 0.9 * comps_ratio + 0.1 * damped_ask
                basis = "asks_volatile_comps"
                cap = ASK_CAP_FAR if days_to_release > 7 else ASK_CAP_NEAR
                if predicted > cap:
                    predicted = cap
                    basis = f"{basis}_cap_{cap:.2f}"
            else:
                predicted = None
                basis = "insufficient_data"
        elif comps_ratio is not None and comps_n >= 8:
            predicted = comps_ratio
            basis = "comps_only"
        else:
            predicted = None
            basis = "insufficient_data"
    else:
        resale_estimate, resale_basis, count = get_resale_estimate(db_session, release, today)
        if resale_estimate is not None:
            predicted = float(resale_estimate / retail)
            basis = resale_basis or "post_release"
            if resale_basis in {"recent_sales", "monthly_avg"}:
                sales_count = count
        elif comps_ratio is not None and comps_n >= 8:
            predicted = comps_ratio
            basis = "comps_only"
        else:
            predicted = None
            basis = "insufficient_data"

    if predicted is None:
        release.heat_score = None
        release.heat_premium_ratio = None
        release.heat_basis = basis
        release.heat_confidence = "low"
        release.heat_updated_at = now
        return

    predicted = min(HEAT_PREMIUM_MAX, max(HEAT_PREMIUM_MIN, predicted))
    release.heat_premium_ratio = float(predicted)
    release.heat_score = _heat_score_from_premium(predicted)
    release.heat_basis = basis
    release.heat_confidence = _confidence_from_basis(basis, comps_n, sales_count, bid_count)
    release.heat_updated_at = now


def _basis_label(basis: str) -> str:
    if basis.startswith("asks_volatile_comps_cap_"):
        cap = basis.split("_cap_")[-1]
        return f"Asks + comps (capped at {cap}x)"
    if basis == "asks_volatile_comps":
        return "Asks + comps (volatile)"
    if basis == "bids_based":
        return "Bid based"
    if basis == "sales_based":
        return "Sales based"
    if basis == "monthly_avg":
        return "Monthly sales average"
    if basis == "recent_sales":
        return "Recent sales"
    if basis == "bids_median":
        return "Median bids"
    if basis == "asks_median":
        return "Median asks"
    if basis == "comps_only":
        return "Comparable releases"
    if basis == "insufficient_data":
        return "Insufficient data"
    return basis.replace("_", " ").title()


def heat_tooltip(release: Release) -> Optional[str]:
    if release.heat_score is None:
        return None
    basis = release.heat_basis or "unknown"
    basis_label = _basis_label(basis)
    tooltip = (
        f"Heat score: {int(release.heat_score)}/100\n"
        f"Confidence: {release.heat_confidence or 'low'}\n"
        f"Based on: {basis_label}"
    )
    if release.release_date and release.release_date >= date.today():
        tooltip += "\nPre-release prices can be volatile; estimates adjust as release approaches."
    return tooltip
