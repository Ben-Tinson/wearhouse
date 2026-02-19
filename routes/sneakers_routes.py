# routes/sneakers_routes.py
import os
import uuid
import json
import requests
import math
import re
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app, abort, g # <-- ADD current_app
from flask_login import login_required, current_user
from sqlalchemy import or_, asc, desc, func
from werkzeug.utils import secure_filename
from utils import allowed_file
from utils.sku import normalize_sku, sku_variants
from utils.money import format_money, convert_money
from utils.slugs import build_my_sneaker_slug
from extensions import db, csrf
from decorators import bearer_or_login_required
from models import (
    User,
    Sneaker,
    SneakerDB,
    Release,
    SneakerNote,
    ReleaseSizeBid,
    ReleaseSalePoint,
    SneakerSale,
    SneakerWear,
    SneakerCleanEvent,
    SneakerDamageEvent,
    SneakerRepairEvent,
    SneakerRepairResolvedDamage,
    SneakerExpense,
    SneakerHealthSnapshot,
    StepBucket,
    StepAttribution,
    ExposureEvent,
)
from forms import SneakerForm, EmptyForm, DamageReportForm, RepairEventForm
import uuid
from services.kicks_client import KicksClient, KicksAPIError
from services.materials_extractor import extract_materials
from services.steps_attribution_service import recompute_attribution, ALGORITHM_V1
from services.exposure_service import (
    exposure_sums_for_sneaker,
    material_sensitivity_multipliers,
    recompute_exposure_attributions,
    upsert_daily_exposure,
)
from services.health_service import (
    compute_health_components,
    compute_damage_penalty_points,
    compute_material_damage_points,
    compute_persistent_stain_points,
    derive_care_tags,
    CARE_TAG_LABELS,
    exposure_since_date,
    has_sensitive_suede_materials,
    normalize_damage_type,
)
from services.steps_seed_service import seed_fake_steps

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

MATERIAL_DISPLAY = {
    "patent leather": "Patent Leather",
    "tumbled leather": "Tumbled Leather",
    "leather": "Leather",
    "suede": "Suede",
    "nubuck": "Nubuck",
    "knit": "Knit",
    "flyknit": "Knit",
    "primeknit": "Knit",
    "mesh": "Mesh",
    "canvas": "Canvas",
    "denim": "Denim",
    "nylon": "Nylon",
    "polyester": "Polyester",
    "satin": "Satin",
    "silk": "Silk",
    "corduroy": "Corduroy",
    "gore tex": "Gore-Tex",
    "gore-tex": "Gore-Tex",
    "neoprene": "Neoprene",
    "rubber": "Rubber",
    "foam": "Foam",
    "cork": "Cork",
    "synthetic": "Synthetic",
    "plastic": "Plastic",
    "tpu": "TPU",
    "eva foam": "EVA Foam",
}


def _normalize_material_label(value: str) -> str:
    cleaned = " ".join((value or "").strip().split())
    if not cleaned:
        return ""
    key = cleaned.lower()
    return MATERIAL_DISPLAY.get(key, cleaned.title())


def _load_materials_list(record: SneakerDB) -> List[str]:
    if record.materials_json:
        try:
            materials = json.loads(record.materials_json)
            if isinstance(materials, list):
                normalized = []
                for item in materials:
                    if isinstance(item, str) and item.strip():
                        label = _normalize_material_label(item)
                        if label:
                            normalized.append(label)
                return normalized
        except (TypeError, ValueError):
            pass
    if record.primary_material:
        label = _normalize_material_label(record.primary_material)
        return [label] if label else []
    return []


def _stain_stats_since_clean(sneaker_id: int, user_id: int, since_date: Optional[date]) -> dict:
    query = (
        db.session.query(ExposureEvent.stain_severity)
        .join(
            SneakerWear,
            (SneakerWear.sneaker_id == sneaker_id)
            & (SneakerWear.worn_at == ExposureEvent.date_local),
        )
        .filter(
            ExposureEvent.user_id == user_id,
            ExposureEvent.stain_flag.is_(True),
        )
    )
    if since_date:
        query = query.filter(ExposureEvent.date_local >= since_date)
    severities = [
        int(row.stain_severity) if row.stain_severity else 2 for row in query.all()
    ]
    return {
        "count": len(severities),
        "max_severity": max(severities) if severities else None,
    }


def _starting_health_for_condition(value: Optional[str]) -> float:
    if not value:
        return 100.0
    normalized = value.strip().lower()
    mapping = {
        "deadstock": 100.0,
        "near new": 98.0,
        "nearly new": 98.0,
        "lightly worn": 95.0,
        "heavily worn": 85.0,
        "beater": 70.0,
    }
    return mapping.get(normalized, 100.0)


def _sum_expenses_for_sneaker(
    sneaker_id: int,
    preferred_currency: str,
) -> Decimal:
    expenses = SneakerExpense.query.filter_by(sneaker_id=sneaker_id).all()
    total_value = Decimal("0")
    for expense in expenses:
        currency = expense.currency
        amount = Decimal(str(expense.amount))
        if currency and currency != preferred_currency:
            converted = convert_money(db.session, amount, currency, preferred_currency)
            if converted is None:
                continue
            amount = Decimal(str(converted))
        total_value += amount
    return total_value


def _total_invested_for_sneaker(sneaker: Sneaker, preferred_currency: str) -> Decimal:
    total_value = Decimal("0")
    if sneaker.purchase_price is not None:
        purchase_currency = sneaker.price_paid_currency or sneaker.purchase_currency or preferred_currency
        purchase_amount = Decimal(str(sneaker.purchase_price))
        if purchase_currency != preferred_currency:
            converted = convert_money(db.session, purchase_amount, purchase_currency, preferred_currency)
            if converted is not None:
                purchase_amount = Decimal(str(converted))
            else:
                purchase_amount = Decimal("0")
        total_value += purchase_amount
    total_value += _sum_expenses_for_sneaker(sneaker.id, preferred_currency)
    return total_value


def _recompute_structural_damage_points(sneaker_id: int) -> float:
    total = (
        db.session.query(func.coalesce(func.sum(SneakerDamageEvent.health_penalty_points), 0.0))
        .filter(
            SneakerDamageEvent.sneaker_id == sneaker_id,
            SneakerDamageEvent.is_active.is_(True),
        )
        .scalar()
    )
    return float(total or 0.0)
from services.sneaker_lookup_service import lookup_or_fetch_sneaker
from sqlalchemy.orm import joinedload

sneakers_bp = Blueprint('sneakers', __name__)

def _matches_search_tokens(text: str, tokens) -> bool:
    if not tokens:
        return True
    haystack = text or ""
    collapsed = re.sub(r"[\s\-_]+", "", haystack).lower()

    def matches_single_token(single_token: str) -> bool:
        if single_token.isdigit():
            pattern = re.compile(rf"(?<!\d){re.escape(single_token)}(?!\d)")
        else:
            pattern = re.compile(rf"\b{re.escape(single_token)}\b", re.IGNORECASE)
        return bool(pattern.search(haystack))

    for token in tokens:
        if not token:
            continue
        if "-" in token or "_" in token:
            parts = [part for part in re.split(r"[-_]+", token) if part]
            if not parts:
                continue
            if not all(matches_single_token(part) for part in parts):
                return False
            continue
        if not matches_single_token(token):
            if not token.isdigit() and token.lower() in collapsed:
                continue
            return False
    return True


def _normalize_search_tokens(term: str):
    if not term:
        return []
    return [token for token in re.split(r"[\s\-_]+", term.strip()) if token]


def _is_dev_environment() -> bool:
    return bool(
        current_app.env == "development"
        or current_app.config.get("ENV") == "development"
        or current_app.config.get("DEBUG")
    )


MAX_STEP_BUCKETS = 400
DEFAULT_TIMEZONE = "Europe/London"


def _parse_iso_datetime(value: str) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _parse_date_or_datetime(value: str) -> Optional[datetime]:
    parsed = _parse_iso_datetime(value)
    if parsed:
        return parsed
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _is_valid_timezone(name: str) -> bool:
    if not name or ZoneInfo is None:
        return False
    try:
        ZoneInfo(name)
    except Exception:
        return False
    return True


def _resolve_timezone_name(value: Optional[str], fallback: Optional[str]) -> Optional[str]:
    candidate = (value or "").strip()
    if candidate:
        return candidate if _is_valid_timezone(candidate) else None
    fallback_value = (fallback or "").strip()
    if fallback_value and _is_valid_timezone(fallback_value):
        return fallback_value
    return DEFAULT_TIMEZONE


def _average_resale_from_offers(offers, preferred_currency: str):
    aftermarket = [offer for offer in offers if offer.offer_type == "aftermarket" and offer.price is not None]
    if not aftermarket:
        return None, None
    matching = [offer for offer in aftermarket if offer.currency == preferred_currency]
    selected = matching if matching else [offer for offer in aftermarket if offer.currency]
    if not selected:
        return None, None
    currency = selected[0].currency
    prices = [offer.price for offer in selected if offer.currency == currency]
    if not prices:
        return None, None
    avg = sum(prices) / len(prices)
    return avg, currency


def _avg_resale_entry_for_sneaker(sneaker, release, preferred_currency: str):
    if release:
        avg_price, avg_currency = _average_resale_from_offers(release.offers, preferred_currency)
        if avg_price is not None and avg_currency:
            return {"amount": avg_price, "currency": avg_currency, "is_estimate": False}

    if sneaker.purchase_price is None:
        return None
    fallback_currency = sneaker.price_paid_currency or sneaker.purchase_currency or preferred_currency
    return {"amount": sneaker.purchase_price, "currency": fallback_currency, "is_estimate": True}


def _normalize_sku_value(value: str) -> str:
    return normalize_sku(value) or ""


def _sku_query_values(values):
    expanded = set()
    for value in values:
        expanded.update(sku_variants(value))
    return [value for value in expanded if value]


def _resale_sort_value(sneaker, release, preferred_currency: str):
    avg_entry = _avg_resale_entry_for_sneaker(sneaker, release, preferred_currency)
    if not avg_entry:
        return None
    amount = avg_entry["amount"]
    currency = avg_entry["currency"]
    if not amount or not currency:
        return None
    if currency == preferred_currency:
        return Decimal(str(amount))
    return convert_money(db.session, amount, currency, preferred_currency)


def _sum_resale_value_for_sneakers(sneakers, release_by_sku, preferred_currency: str):
    total = Decimal("0")
    counted = 0
    is_estimate = False
    for sneaker in sneakers:
        sku_key = _normalize_sku_value(sneaker.sku)
        release = release_by_sku.get(sku_key) if sku_key else None
        avg_entry = _avg_resale_entry_for_sneaker(sneaker, release, preferred_currency)
        if not avg_entry:
            continue
        avg_price = avg_entry["amount"]
        avg_currency = avg_entry["currency"]
        if avg_entry.get("is_estimate"):
            is_estimate = True
        if avg_currency == preferred_currency:
            total += avg_price
            counted += 1
            continue
        converted = convert_money(db.session, avg_price, avg_currency, preferred_currency)
        if converted is None:
            continue
        total += converted
        counted += 1
    return (total, counted, is_estimate) if counted else (None, 0, False)


def _extract_stockx_size_bids(detail):
    if not isinstance(detail, dict):
        return []
    data = detail.get("data") if isinstance(detail.get("data"), dict) else detail
    variants = data.get("variants") if isinstance(data, dict) else None
    if not variants:
        variants = (
            data.get("productVariants")
            or data.get("product_variants")
            or data.get("variantsList")
            or data.get("sizeVariants")
            or data.get("sizes")
            or []
        ) if isinstance(data, dict) else []
    if isinstance(variants, dict):
        variants = (
            variants.get("results")
            or variants.get("items")
            or variants.get("data")
            or variants.get("variants")
            or variants.get("sizes")
            or []
        )
    bids = []
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        market = variant.get("market")
        market_dict = market if isinstance(market, dict) else {}
        prices = variant.get("prices")
        prices_dict = prices if isinstance(prices, dict) else {}
        size_label = (
            variant.get("size")
            or variant.get("size_us")
            or variant.get("size_us_men")
            or variant.get("size_us_women")
            or variant.get("size_uk")
            or variant.get("size_eu")
            or variant.get("sizeEU")
            or variant.get("sizeUS")
            or variant.get("size_title")
            or variant.get("sizeTitle")
            or variant.get("displaySize")
            or variant.get("variant")
            or variant.get("variantValue")
        )
        size_type = (
            variant.get("size_type")
            or variant.get("sizeType")
            or variant.get("sizes")
            or "US"
        )
        if not size_label:
            continue
        bid_value = (
            variant.get("highestBid")
            or variant.get("highest_bid")
            or variant.get("bid")
            or market_dict.get("highestBid")
            or market_dict.get("highest_bid")
            or market_dict.get("bid")
            or market_dict.get("bid_price")
            or prices_dict.get("bid")
            or prices_dict.get("highest_bid")
            or prices_dict.get("highestBid")
        )
        if bid_value is None:
            continue
        price_type = "bid"
        try:
            bid_decimal = Decimal(str(bid_value))
        except (ValueError, TypeError):
            continue
        bids.append((str(size_label), str(size_type) if size_type else "US", bid_decimal, price_type))
    return bids


def _extract_stockx_size_prices(payload):
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") or []
    if not data or not isinstance(data, list):
        return []
    entry = data[0] if isinstance(data[0], dict) else {}
    variants = entry.get("variants") or []
    prices = []
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        size_label = variant.get("size") or variant.get("size_title") or variant.get("sizeTitle")
        size_type = variant.get("size_type") or variant.get("sizeType") or "US"
        price_value = variant.get("price") or variant.get("asks")
        if not size_label or price_value is None:
            continue
        try:
            price_decimal = Decimal(str(price_value))
        except (ValueError, TypeError):
            continue
        prices.append((str(size_label), str(size_type) if size_type else "US", price_decimal, "ask"))
    return prices


def _get_release_size_bids(release):
    if not release:
        return [], None
    now = datetime.utcnow()
    if release.size_bids_last_fetched_at and release.size_bids_last_fetched_at >= now - timedelta(days=5):
        bids = (
            db.session.query(ReleaseSizeBid)
            .filter(ReleaseSizeBid.release_id == release.id)
            .order_by(ReleaseSizeBid.size_label.asc())
            .all()
        )
        return bids, release.size_bids_last_fetched_at

    source_id = release.source_product_id or release.source_slug
    if not source_id:
        bids = (
            db.session.query(ReleaseSizeBid)
            .filter(ReleaseSizeBid.release_id == release.id)
            .order_by(ReleaseSizeBid.size_label.asc())
            .all()
        )
        return bids, release.size_bids_last_fetched_at

    api_key = current_app.config.get('KICKS_API_KEY')
    if not api_key:
        bids = (
            db.session.query(ReleaseSizeBid)
            .filter(ReleaseSizeBid.release_id == release.id)
            .order_by(ReleaseSizeBid.size_label.asc())
            .all()
        )
        return bids, release.size_bids_last_fetched_at

    client = KicksClient(
        api_key=api_key,
        base_url=current_app.config.get('KICKS_API_BASE_URL', 'https://api.kicks.dev'),
        logger=current_app.logger,
    )
    parsed_bids = []
    use_price_endpoint = current_app.config.get("KICKS_STOCKX_PRICES_ENABLED", False)
    if use_price_endpoint:
        try:
            price_payload = client.stockx_prices(
                market="US",
                skus=[release.sku] if release.sku else None,
                product_ids=[release.source_product_id] if release.source_product_id else None,
            )
            parsed_bids = _extract_stockx_size_prices(price_payload)
        except KicksAPIError as exc:
            current_app.logger.warning("Size price fetch failed for release %s: %s", release.id, exc)
    if not parsed_bids:
        try:
            detail = client.get_stockx_product(
                source_id,
                include_variants=True,
                include_traits=False,
                include_market=True,
                include_statistics=False,
            )
            parsed_bids = _extract_stockx_size_bids(detail)
        except KicksAPIError as exc:
            current_app.logger.warning("Size bid fetch failed for release %s: %s", release.id, exc)
            bids = (
                db.session.query(ReleaseSizeBid)
                .filter(ReleaseSizeBid.release_id == release.id)
                .order_by(ReleaseSizeBid.size_label.asc())
                .all()
            )
            return bids, release.size_bids_last_fetched_at

    db.session.query(ReleaseSizeBid).filter(ReleaseSizeBid.release_id == release.id).delete()
    if parsed_bids:
        deduped = {}
        for size_label, size_type, bid_value, price_type in parsed_bids:
            key = (str(size_label), str(size_type) if size_type else None)
            existing = deduped.get(key)
            if not existing or bid_value > existing[0]:
                deduped[key] = (bid_value, price_type)
        for (size_label, size_type), (bid_value, price_type) in deduped.items():
            db.session.add(
                ReleaseSizeBid(
                    release_id=release.id,
                    size_label=size_label,
                    size_type=size_type,
                    highest_bid=bid_value,
                    currency="USD",
                    price_type=price_type,
                    fetched_at=now,
                )
            )
    else:
        detail_data = detail.get("data") if isinstance(detail.get("data"), dict) else {}
        data_keys = list(detail_data.keys()) if isinstance(detail_data, dict) else []
        sample_variants = detail_data.get("variants") or detail_data.get("productVariants") or detail_data.get("sizes") or []
        sample_item = sample_variants[0] if isinstance(sample_variants, list) and sample_variants else {}
        sample_market = sample_item.get("market") if isinstance(sample_item, dict) else None
        sample_prices = sample_item.get("prices") if isinstance(sample_item, dict) else None
        current_app.logger.info(
            "Size bid debug: release_id=%s source_id=%s detail_keys=%s data_keys=%s variants_type=%s sample_keys=%s market_keys=%s prices_keys=%s",
            release.id,
            source_id,
            list(detail.keys()),
            data_keys,
            type(sample_variants).__name__,
            list(sample_item.keys()) if isinstance(sample_item, dict) else None,
            list(sample_market.keys()) if isinstance(sample_market, dict) else None,
            list(sample_prices.keys()) if isinstance(sample_prices, dict) else None,
        )
        current_app.logger.info(
            "Size bid fetch returned no variants for release %s (source_id=%s).",
            release.id,
            source_id,
        )
    release.size_bids_last_fetched_at = now
    db.session.commit()

    bids = (
        db.session.query(ReleaseSizeBid)
        .filter(ReleaseSizeBid.release_id == release.id)
        .order_by(ReleaseSizeBid.size_label.asc())
        .all()
    )
    return bids, release.size_bids_last_fetched_at


def _parse_stockx_sale_timestamp(value: str):
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1]
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _get_release_sales_series(release, max_points: int = 15):
    if not release:
        return [], None
    now = datetime.utcnow()
    if release.sales_last_fetched_at and release.sales_last_fetched_at >= now - timedelta(days=5):
        rows = (
            db.session.query(ReleaseSalePoint)
            .filter(ReleaseSalePoint.release_id == release.id)
            .order_by(ReleaseSalePoint.sale_at.asc())
            .all()
        )
        if max_points and len(rows) >= max_points:
            return rows[-max_points:], release.sales_last_fetched_at

    source_id = release.source_product_id or release.source_slug
    if not source_id:
        rows = (
            db.session.query(ReleaseSalePoint)
            .filter(ReleaseSalePoint.release_id == release.id)
            .order_by(ReleaseSalePoint.sale_at.asc())
            .all()
        )
        return rows, release.sales_last_fetched_at

    api_key = current_app.config.get('KICKS_API_KEY')
    if not api_key:
        rows = (
            db.session.query(ReleaseSalePoint)
            .filter(ReleaseSalePoint.release_id == release.id)
            .order_by(ReleaseSalePoint.sale_at.asc())
            .all()
        )
        return rows, release.sales_last_fetched_at

    client = KicksClient(
        api_key=api_key,
        base_url=current_app.config.get('KICKS_API_BASE_URL', 'https://api.kicks.dev'),
        logger=current_app.logger,
    )
    try:
        payload = client.get_stockx_sales_history(source_id, limit=max_points, page=1)
    except KicksAPIError as exc:
        current_app.logger.warning("Sales history fetch failed for release %s: %s", release.id, exc)
        rows = (
            db.session.query(ReleaseSalePoint)
            .filter(ReleaseSalePoint.release_id == release.id)
            .order_by(ReleaseSalePoint.sale_at.asc())
            .all()
        )
        return rows, release.sales_last_fetched_at

    sales = payload.get("data") if isinstance(payload, dict) else []
    points = []
    for sale in sales or []:
        if not isinstance(sale, dict):
            continue
        created_at = _parse_stockx_sale_timestamp(sale.get("created_at"))
        amount = sale.get("amount")
        if created_at is None or amount is None:
            continue
        try:
            amount_decimal = Decimal(str(amount))
        except (ValueError, TypeError):
            continue
        points.append((created_at, amount_decimal))

    if points:
        db.session.query(ReleaseSalePoint).filter(ReleaseSalePoint.release_id == release.id).delete()
        for created_at, amount in points:
            db.session.add(
                ReleaseSalePoint(
                    release_id=release.id,
                    sale_at=created_at,
                    price=amount,
                    currency="USD",
                    fetched_at=now,
                )
            )
        release.sales_last_fetched_at = now
        db.session.commit()
    else:
        release.sales_last_fetched_at = now
        db.session.commit()

    rows = (
        db.session.query(ReleaseSalePoint)
        .filter(ReleaseSalePoint.release_id == release.id)
        .order_by(ReleaseSalePoint.sale_at.asc())
        .all()
    )
    if max_points and len(rows) > max_points:
        rows = rows[-max_points:]
    return rows, release.sales_last_fetched_at

def get_sort_order(sort_by, order):
    # This helper can contain your if/elif block to determine the sort criteria
    # For now, we'll keep the logic inside the main function.
    pass

# --- Sneaker Collection Routes ---

# My Collection Route (Formerly Dashboard)

# In routes/sneakers_routes.py

@sneakers_bp.route('/my-collection')
@login_required
def dashboard():
    is_ajax = "X-Requested-With" in request.headers and request.headers['X-Requested-With'] == 'XMLHttpRequest'
    
    # --- 1. Get Parameters & Define State ---
    page = request.args.get('page', default=1, type=int)
    per_page = 40
    sort_by_param = request.args.get('sort_by')
    order_param = request.args.get('order')
    filter_brand_param = request.args.get('filter_brand')
    search_term_param = request.args.get('search_term')

    sort_active_in_url = bool(sort_by_param)
    
    sortable_columns = ['id', 'brand', 'model', 'purchase_date', 'last_worn_date', 'purchase_price', 'resale_value']
    effective_sort_by = sort_by_param if sort_by_param in sortable_columns else 'purchase_date'
    default_order = 'asc' if effective_sort_by in ['brand', 'model'] else 'desc'
    effective_order = order_param if order_param in ['asc', 'desc'] else default_order

    current_filter_brand = filter_brand_param.strip() if (filter_brand_param and filter_brand_param.lower() != 'all') else None
    current_search_term = search_term_param.strip() if (search_term_param and search_term_param.strip()) else None
    preferred_currency = current_user.preferred_currency or "GBP"

    # --- 2. Build the Main Query with Filters ---
    query = Sneaker.query.filter_by(user_id=current_user.id)

    if current_filter_brand:
        query = query.filter(Sneaker.brand == current_filter_brand)

    if current_search_term:
        keywords = _normalize_search_tokens(current_search_term)
        search_conditions = [or_(Sneaker.brand.ilike(f"%{k}%"), Sneaker.model.ilike(f"%{k}%"), Sneaker.colorway.ilike(f"%{k}%")) for k in keywords if k]
        if search_conditions:
            query = query.filter(*search_conditions)
    
    # --- 3. Apply Sorting ---
    if effective_sort_by != 'resale_value':
        sort_column = getattr(Sneaker, effective_sort_by, Sneaker.id)
        if effective_sort_by in ['brand', 'model', 'colorway']:
            sort_expression = sort_column.collate('NOCASE')
        else:
            sort_expression = sort_column

        if effective_order == 'desc':
            query = query.order_by(sort_expression.desc().nullslast(), Sneaker.id.desc())
        else:
            query = query.order_by(sort_expression.asc().nullsfirst(), Sneaker.id.desc())
        
    user_sneakers_list = query.all()

    if sort_active_in_url and effective_sort_by != 'resale_value':
        # Use a lambda function for robust, case-insensitive sorting that handles None
        is_reverse = (effective_order == 'desc')
        def sort_key(sneaker):
            val = getattr(sneaker, effective_sort_by)
            if val is None:
                return (1, None) # Group None values together
            if isinstance(val, str):
                return (0, val.lower()) # Sort strings case-insensitively
            return (0, val) # Sort other types normally

        user_sneakers_list.sort(key=sort_key, reverse=is_reverse)
    
    if current_search_term:
        keywords = _normalize_search_tokens(current_search_term)
        def matches_tokens(sneaker):
            text = " ".join(filter(None, [sneaker.brand, sneaker.model, sneaker.colorway]))
            return _matches_search_tokens(text, keywords)
        user_sneakers_list = [sneaker for sneaker in user_sneakers_list if matches_tokens(sneaker)]

    if effective_sort_by == 'resale_value':
        skus_for_sort = [_normalize_sku_value(sneaker.sku) for sneaker in user_sneakers_list if sneaker.sku]
        release_by_sku = {}
        if skus_for_sort:
            skus_for_query = _sku_query_values(skus_for_sort)
            releases = (
                db.session.query(Release)
                .options(joinedload(Release.offers))
                .filter(func.upper(Release.sku).in_(skus_for_query))
                .all()
            )
            release_by_sku = {
                _normalize_sku_value(release.sku): release for release in releases if release.sku
            }
        def resale_key(sneaker):
            release = release_by_sku.get(_normalize_sku_value(sneaker.sku))
            value = _resale_sort_value(sneaker, release, preferred_currency)
            return (1, Decimal("0")) if value is None else (0, value)
        user_sneakers_list.sort(key=resale_key, reverse=(effective_order == 'desc'))

    status_map = {}
    for sneaker in user_sneakers_list:
        health_components = compute_health_components(
            sneaker=sneaker,
            user_id=current_user.id,
            materials=[],
        )
        status_map[sneaker.id] = health_components["status_label"]

    total_count = len(user_sneakers_list)
    total_pages = max(1, math.ceil(total_count / per_page)) if total_count else 1
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    end = start + per_page
    user_sneakers_list = user_sneakers_list[start:end]
    displayed_count = len(user_sneakers_list)

    skus = [_normalize_sku_value(sneaker.sku) for sneaker in user_sneakers_list if sneaker.sku]
    avg_resale_map = {}
    if skus:
        skus_for_query = _sku_query_values(skus)
        releases = (
            db.session.query(Release)
            .options(joinedload(Release.offers))
            .filter(func.upper(Release.sku).in_(skus_for_query))
            .all()
        )
        release_by_sku = {
            _normalize_sku_value(release.sku): release for release in releases if release.sku
        }
        for sneaker in user_sneakers_list:
            release = release_by_sku.get(_normalize_sku_value(sneaker.sku))
            avg_resale = _avg_resale_entry_for_sneaker(sneaker, release, preferred_currency)
            if avg_resale:
                avg_resale_map[sneaker.id] = avg_resale

    # status_map already computed for user_sneakers_list above

    steps_map = {}
    sneaker_ids = [sneaker.id for sneaker in user_sneakers_list]
    if sneaker_ids:
        step_rows = (
            db.session.query(
                StepAttribution.sneaker_id,
                func.sum(StepAttribution.steps_attributed).label("steps_total"),
            )
            .filter(
                StepAttribution.user_id == current_user.id,
                StepAttribution.sneaker_id.in_(sneaker_ids),
                StepAttribution.bucket_granularity == "day",
                StepAttribution.algorithm_version == ALGORITHM_V1,
                StepAttribution.bucket_start >= datetime.utcnow() - timedelta(days=30),
            )
            .group_by(StepAttribution.sneaker_id)
            .all()
        )
        steps_map = {
            row.sneaker_id: int(row.steps_total or 0) for row in step_rows
        }

    # --- 4. Calculate All Stats & Dropdown Data ---
    base_query = Sneaker.query.filter_by(user_id=current_user.id)
    overall_total_count = base_query.count()
    total_value = float(base_query.with_entities(func.sum(Sneaker.purchase_price)).scalar() or 0.0)
    total_value_display = format_money(total_value, preferred_currency)
    collection_sneakers = base_query.all()
    collection_skus = [_normalize_sku_value(sneaker.sku) for sneaker in collection_sneakers if sneaker.sku]
    release_by_sku = {}
    if collection_skus:
        skus_for_query = _sku_query_values(collection_skus)
        releases = (
            db.session.query(Release)
            .options(joinedload(Release.offers))
            .filter(func.upper(Release.sku).in_(skus_for_query))
            .all()
        )
        release_by_sku = {
            _normalize_sku_value(release.sku): release for release in releases if release.sku
        }
    total_resale_value, _, total_resale_is_estimate = _sum_resale_value_for_sneakers(
        collection_sneakers, release_by_sku, preferred_currency
    )
    total_resale_value_display = format_money(total_resale_value, preferred_currency) if total_resale_value else None
    if total_resale_value_display and total_resale_is_estimate:
        total_resale_value_display = f"Est. {total_resale_value_display}"
    total_resale_delta = None
    if total_resale_value is not None:
        total_resale_delta = total_resale_value - Decimal(str(total_value))
    total_resale_delta_display = (
        format_money(total_resale_delta, preferred_currency) if total_resale_delta is not None else None
    )
    if total_resale_delta_display and total_resale_is_estimate:
        total_resale_delta_display = f"Est. {total_resale_delta_display}"
    brand_distribution = base_query.with_entities(Sneaker.brand, func.count(Sneaker.brand)).filter(Sneaker.brand.isnot(None)).group_by(Sneaker.brand).order_by(func.count(Sneaker.brand).desc()).all()
    total_brands = len(brand_distribution)
    most_owned_brand = brand_distribution[0][0] if brand_distribution else "N/A"
    brand_labels = [item[0] for item in brand_distribution]
    brand_data = [item[1] for item in brand_distribution]
    brands_for_filter = [b[0] for b in base_query.with_entities(Sneaker.brand).distinct().order_by(Sneaker.brand).all() if b[0]]
    brand_specific_count = base_query.filter(Sneaker.brand == current_filter_brand).count() if current_filter_brand else None
    in_rotation_count = base_query.filter_by(in_rotation=True).count()

    # --- 5. Prepare Final Context Dictionary ---
    modal_form = SneakerForm()
    modal_form.purchase_currency.data = current_user.preferred_currency or "GBP"
    context = {
        "sneakers": user_sneakers_list, "displayed_count": len(user_sneakers_list),
        "overall_total_count": overall_total_count, "brand_specific_count": brand_specific_count,
        "total_value": total_value, "total_value_display": total_value_display, "preferred_currency": preferred_currency,
        "total_resale_value": total_resale_value, "total_resale_value_display": total_resale_value_display,
        "total_resale_delta": total_resale_delta, "total_resale_delta_display": total_resale_delta_display,
        "total_resale_is_estimate": total_resale_is_estimate,
        "total_brands": total_brands,
        "most_owned_brand": most_owned_brand, "in_rotation_count": in_rotation_count,
        "brand_labels": brand_labels, "brand_data": brand_data,
        "brands_for_filter": brands_for_filter, "months_for_filter": [],
        "current_sort_by": effective_sort_by, "current_order": effective_order,
        "sort_active_in_url": sort_active_in_url, "current_filter_brand": current_filter_brand,
        "current_filter_month": None, "current_search_term": current_search_term,
        "show_sort_controls": True,
        "allowed_sort_fields": [
            {"key": "id", "label": "Added", "default_order": "desc"},
            {"key": "brand", "label": "Brand", "default_order": "asc"},
            {"key": "model", "label": "Model", "default_order": "asc"},
            {"key": "purchase_date", "label": "Purchase Date", "default_order": "desc"},
            {"key": "last_worn_date", "label": "Last Worn", "default_order": "desc"},
            {"key": "purchase_price", "label": "Purchase Price", "default_order": "desc"},
            {"key": "resale_value", "label": "Resale Value", "default_order": "desc"},
        ],
        "form_for_modal": modal_form,
        "avg_resale_map": avg_resale_map,
        "steps_map": steps_map,
        "status_map": status_map,
        "page": page,
        "total_pages": total_pages,
        "pagination_params": {k: v for k, v in request.args.to_dict(flat=True).items() if k != 'page'},
        "pagination_endpoint": "sneakers.dashboard",
    }

    # --- 6. Respond ---
    if is_ajax:
        context['sneaker_grid_html'] = render_template('_sneaker_grid.html', **context)
        context['controls_bar_html'] = render_template('_controls_bar.html', target_endpoint='sneakers.dashboard', **context)
        context['summary_message_html'] = render_template('_collection_summary_message.html', **context)
        context.pop('sneakers', None)
        context.pop('form_for_modal', None)
        return jsonify(context)

    return render_template('dashboard.html', **context)

# My Rotation Route

@sneakers_bp.route('/my-rotation') # NEW URL
@login_required
def rotation():
    # Get parameters from request arguments (this part is the same)
    sort_by_param = request.args.get('sort_by')
    order_param = request.args.get('order')
    filter_brand_param = request.args.get('filter_brand')
    search_term_param = request.args.get('search_term')
    page = request.args.get('page', default=1, type=int)
    per_page = 40

    # --- Base query is the KEY DIFFERENCE ---
    # Instead of all sneakers, we only get those where in_rotation is True
    query = Sneaker.query.filter_by(user_id=current_user.id, in_rotation=True)

    # --- Calculate Counts Specific to Rotation ---
    # The total count of sneakers just in the rotation
    rotation_total_count = query.count()
    # The total count of ALL sneakers in the user's collection for context
    overall_collection_count = Sneaker.query.filter_by(user_id=current_user.id).count()


    # --- Determine if sorting was explicitly set via URL (for highlighting) ---
    sort_active_in_url = bool(sort_by_param)

    # --- Determine effective sort criteria for the query ---
    effective_sort_by = 'purchase_date'  # Default sort field
    effective_order = 'desc'           # Default order for purchase_date (newest first)

    if sort_by_param: # Only override defaults if sort_by_param actually exists
        if sort_by_param == 'brand':
            effective_sort_by = 'brand'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'asc'
        elif sort_by_param == 'model':
            effective_sort_by = 'model'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'asc'
        elif sort_by_param == 'purchase_date': 
            effective_sort_by = 'purchase_date'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'desc'
        elif sort_by_param == 'last_worn_date':
            effective_sort_by = 'last_worn_date'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'desc'
        elif sort_by_param == 'purchase_price':
            effective_sort_by = 'purchase_price'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'desc'
        elif sort_by_param == 'resale_value':
            effective_sort_by = 'resale_value'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'desc'
        elif sort_by_param == 'id': 
            effective_sort_by = 'id' 
            effective_order = order_param if order_param in ['asc', 'desc'] else 'desc'
        # If sort_by_param is an unrecognized value, defaults for effective_sort_by/order remain.
    
    # --- Apply brand filter ---
    is_brand_filter_active = bool(filter_brand_param and filter_brand_param.lower() != 'all')
    current_filter_brand = filter_brand_param.strip() if is_brand_filter_active else None
    if current_filter_brand:
        query = query.filter(Sneaker.brand == current_filter_brand)
    
    # --- Calculate brand_specific_count (after brand filter, before search) ---
    brand_specific_count = None
    if current_filter_brand:
         brand_query_for_count = Sneaker.query.filter_by(user_id=current_user.id, brand=current_filter_brand)
         brand_specific_count = brand_query_for_count.count()

    # --- Apply search term filter ---
    is_search_active = bool(search_term_param and search_term_param.strip())
    current_search_term = search_term_param.strip() if is_search_active else None
    if current_search_term:
        keywords = _normalize_search_tokens(current_search_term)
        search_conditions = []
        for keyword in keywords:
            if keyword: 
                keyword_pattern = f"%{keyword}%"
                keyword_condition = or_(
                    Sneaker.brand.ilike(keyword_pattern),
                    Sneaker.model.ilike(keyword_pattern),
                    Sneaker.colorway.ilike(keyword_pattern)
                )
                search_conditions.append(keyword_condition)
        if search_conditions:
            query = query.filter(*search_conditions)

    if current_filter_brand and current_filter_brand.lower() != 'all':
         brand_query_for_count = query.filter(Sneaker.brand == current_filter_brand) # Apply to rotation query
         brand_specific_count = brand_query_for_count.count()

    # --- Apply sorting to the query ---
    if effective_sort_by != 'resale_value':
        if effective_sort_by == 'brand':
            order_obj = Sneaker.brand.desc() if effective_order == 'desc' else Sneaker.brand.asc()
        elif effective_sort_by == 'model':
            order_obj = Sneaker.model.desc() if effective_order == 'desc' else Sneaker.model.asc()
        elif effective_sort_by == 'purchase_date':
            order_obj = Sneaker.purchase_date.desc().nullslast() if effective_order == 'desc' else Sneaker.purchase_date.asc().nullsfirst()
        elif effective_sort_by == 'last_worn_date':
            order_obj = Sneaker.last_worn_date.desc().nullslast() if effective_order == 'desc' else Sneaker.last_worn_date.asc().nullsfirst()
        elif effective_sort_by == 'purchase_price':
            order_obj = Sneaker.purchase_price.desc().nullslast() if effective_order == 'desc' else Sneaker.purchase_price.asc().nullsfirst()
        elif effective_sort_by == 'id': # "Added" sort
            order_obj = Sneaker.id.desc() if effective_order == 'desc' else Sneaker.id.asc()
        else: # Default case, should match initialized effective_sort_by ('purchase_date')
            order_obj = Sneaker.purchase_date.desc().nullslast() 
            # Re-affirm defaults if sort_by_param was invalid, though effective_sort_by should already be set
            effective_sort_by = 'purchase_date' 
            effective_order = 'desc'

        query = query.order_by(order_obj)
    user_sneakers = query.all()
    if current_search_term:
        keywords = _normalize_search_tokens(current_search_term)
        def matches_tokens(sneaker):
            text = " ".join(filter(None, [sneaker.brand, sneaker.model, sneaker.colorway]))
            return _matches_search_tokens(text, keywords)
        user_sneakers = [sneaker for sneaker in user_sneakers if matches_tokens(sneaker)]
    if effective_sort_by == 'resale_value':
        preferred_currency = current_user.preferred_currency or "GBP"
        skus_for_sort = [_normalize_sku_value(sneaker.sku) for sneaker in user_sneakers if sneaker.sku]
        release_by_sku = {}
        if skus_for_sort:
            skus_for_query = _sku_query_values(skus_for_sort)
            releases = (
                db.session.query(Release)
                .options(joinedload(Release.offers))
                .filter(func.upper(Release.sku).in_(skus_for_query))
                .all()
            )
            release_by_sku = {
                _normalize_sku_value(release.sku): release for release in releases if release.sku
            }
        def resale_key(sneaker):
            release = release_by_sku.get(_normalize_sku_value(sneaker.sku))
            value = _resale_sort_value(sneaker, release, preferred_currency)
            return (1, Decimal("0")) if value is None else (0, value)
        user_sneakers.sort(key=resale_key, reverse=(effective_order == 'desc'))
    total_count = len(user_sneakers)
    total_pages = max(1, math.ceil(total_count / per_page)) if total_count else 1
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    end = start + per_page
    user_sneakers = user_sneakers[start:end]

    # --- Calculate counts ---
    overall_total_count = Sneaker.query.filter_by(user_id=current_user.id).count()
    displayed_count = len(user_sneakers)

    preferred_currency = current_user.preferred_currency or "GBP"
    skus = [_normalize_sku_value(sneaker.sku) for sneaker in user_sneakers if sneaker.sku]
    avg_resale_map = {}
    if skus:
        skus_for_query = _sku_query_values(skus)
        releases = (
            db.session.query(Release)
            .options(joinedload(Release.offers))
            .filter(func.upper(Release.sku).in_(skus_for_query))
            .all()
        )
        release_by_sku = {
            _normalize_sku_value(release.sku): release for release in releases if release.sku
        }
        for sneaker in user_sneakers:
            release = release_by_sku.get(_normalize_sku_value(sneaker.sku))
            avg_resale = _avg_resale_entry_for_sneaker(sneaker, release, preferred_currency)
            if avg_resale:
                avg_resale_map[sneaker.id] = avg_resale

    # --- Get distinct brands for the filter dropdown ---
    distinct_brands_tuples = db.session.query(Sneaker.brand).filter(Sneaker.user_id == current_user.id).distinct().order_by(Sneaker.brand).all()
    brands_for_filter = [brand[0] for brand in distinct_brands_tuples if brand[0]]

    # --- Form for the "Add/Edit Sneaker" Modal ---
    modal_form = SneakerForm()
    modal_form.purchase_currency.data = current_user.preferred_currency or "GBP"

    status_map = {}
    for sneaker in user_sneakers:
        health_components = compute_health_components(
            sneaker=sneaker,
            user_id=current_user.id,
            materials=[],
        )
        status_map[sneaker.id] = health_components["status_label"]

    return render_template('rotation.html', 
                           show_sort_controls=True,
                           on_rotation_page=True,
                           name=current_user.first_name or current_user.username,
                           sneakers=user_sneakers,
                           rotation_total_count=rotation_total_count,
                           overall_collection_count=overall_collection_count,
                           brand_specific_count=brand_specific_count,
                           displayed_count=displayed_count,
                           current_sort_by=effective_sort_by,
                           current_order=effective_order,
                           sort_active_in_url=sort_active_in_url, # Flag for template highlighting
                           brands_for_filter=brands_for_filter,
                           current_filter_brand=current_filter_brand,
                           current_search_term=current_search_term,
                           allowed_sort_fields=[
                               {"key": "id", "label": "Added", "default_order": "desc"},
                               {"key": "brand", "label": "Brand", "default_order": "asc"},
                               {"key": "model", "label": "Model", "default_order": "asc"},
                               {"key": "purchase_date", "label": "Purchase Date", "default_order": "desc"},
                               {"key": "last_worn_date", "label": "Last Worn", "default_order": "desc"},
                               {"key": "purchase_price", "label": "Purchase Price", "default_order": "desc"},
                               {"key": "resale_value", "label": "Resale Value", "default_order": "desc"},
                           ],
                           form_for_modal=modal_form, # Pass modal form as form_for_modal
                           avg_resale_map=avg_resale_map,
                           status_map=status_map,
                           page=page,
                           total_pages=total_pages,
                           pagination_params={k: v for k, v in request.args.to_dict(flat=True).items() if k != 'page'}
                           )


@sneakers_bp.route('/my/sneakers/<int:sneaker_id>')
@login_required
def my_sneaker_detail_redirect(sneaker_id):
    sneaker = Sneaker.query.filter_by(id=sneaker_id, user_id=current_user.id).first_or_404()
    canonical_slug = build_my_sneaker_slug(sneaker)
    return redirect(
        url_for('sneakers.my_sneaker_detail', sneaker_id=sneaker.id, slug=canonical_slug),
        code=302 if current_app.debug else 301,
    )


@sneakers_bp.route('/my/sneakers/<int:sneaker_id>-<slug>')
@login_required
def my_sneaker_detail(sneaker_id, slug):
    sneaker = Sneaker.query.filter_by(id=sneaker_id, user_id=current_user.id).first_or_404()
    canonical_slug = build_my_sneaker_slug(sneaker)
    if slug != canonical_slug:
        return redirect(
            url_for('sneakers.my_sneaker_detail', sneaker_id=sneaker.id, slug=canonical_slug),
            code=302 if current_app.debug else 301,
        )

    source = request.args.get("source")
    if source == "rotation":
        back_url = url_for("sneakers.rotation")
        back_label = "Back to Rotation"
    else:
        back_url = url_for("sneakers.dashboard")
        back_label = "Back to Collection"

    preferred_currency = current_user.preferred_currency or "GBP"
    release = None
    if sneaker.sku:
        sku_values = _sku_query_values([sneaker.sku])
        release = (
            db.session.query(Release)
            .options(joinedload(Release.offers), joinedload(Release.size_bids))
            .filter(func.upper(Release.sku).in_(sku_values))
            .first()
        )
    sneaker_materials = []
    if sneaker.sku:
        sku_filters = [SneakerDB.sku.ilike(value) for value in sku_variants(sneaker.sku)]
        sneaker_record = SneakerDB.query.filter(or_(*sku_filters)).first()
        if sneaker_record:
            sneaker_materials = _load_materials_list(sneaker_record)
    care_tags = derive_care_tags(sneaker_materials)

    avg_resale = _avg_resale_entry_for_sneaker(sneaker, release, preferred_currency)
    modal_form = SneakerForm()
    damage_form = DamageReportForm()
    repair_form = RepairEventForm()
    modal_form.purchase_currency.data = current_user.preferred_currency or "GBP"
    finance_delta_display = None
    finance_delta_is_estimate = False
    total_invested = _total_invested_for_sneaker(sneaker, preferred_currency)
    total_invested_display = format_money(total_invested, preferred_currency) if total_invested else None
    if avg_resale and total_invested is not None and total_invested != Decimal("0"):
        avg_amount = avg_resale.get("amount")
        avg_currency = avg_resale.get("currency")
        finance_delta_is_estimate = bool(avg_resale.get("is_estimate"))
        if avg_amount is not None and avg_currency:
            avg_value = avg_amount
            if avg_currency != preferred_currency:
                avg_value = convert_money(db.session, avg_amount, avg_currency, preferred_currency)
            if avg_value is not None:
                delta_value = Decimal(str(avg_value)) - Decimal(str(total_invested))
                finance_delta_display = format_money(delta_value, preferred_currency)
                if finance_delta_display and finance_delta_is_estimate:
                    finance_delta_display = f"Est. {finance_delta_display}"

    wear_count = (
        db.session.query(func.count(SneakerWear.id))
        .filter(SneakerWear.sneaker_id == sneaker.id)
        .scalar()
    ) or 0
    first_wear_date = (
        db.session.query(func.min(SneakerWear.worn_at))
        .filter(SneakerWear.sneaker_id == sneaker.id)
        .scalar()
    )
    cpw_display = None
    if wear_count:
        total_cost_value = _total_invested_for_sneaker(sneaker, preferred_currency)
        if total_cost_value:
            cpw_value = Decimal(str(total_cost_value)) / Decimal(str(wear_count))
            cpw_display = format_money(cpw_value, preferred_currency)

    sales_series = []
    sales_series_currency = None
    sales_series_fetched_at = None
    if release:
        recent_sales, fetched_at = _get_release_sales_series(release, max_points=30)
        sales_series_fetched_at = fetched_at
        if recent_sales:
            display_currency = preferred_currency
            converted = []
            for row in recent_sales:
                value = row.price
                currency = row.currency
                if currency != preferred_currency:
                    mapped = convert_money(db.session, value, currency, preferred_currency)
                    if mapped is None:
                        display_currency = currency
                        converted = []
                        break
                    converted.append(mapped)
                else:
                    converted.append(value)
            if converted:
                sales_series = [
                    {"label": row.sale_at.strftime('%b %d'), "value": float(converted[index])}
                    for index, row in enumerate(recent_sales)
                ]
                sales_series_currency = display_currency
            else:
                sales_series = [
                    {"label": row.sale_at.strftime('%b %d'), "value": float(row.price)}
                    for row in recent_sales
                ]
                sales_series_currency = recent_sales[0].currency if recent_sales else None

    steps_total = (
        db.session.query(func.sum(StepAttribution.steps_attributed))
        .filter(
            StepAttribution.user_id == current_user.id,
            StepAttribution.sneaker_id == sneaker.id,
            StepAttribution.bucket_granularity == "day",
            StepAttribution.algorithm_version == ALGORITHM_V1,
        )
        .scalar()
    )
    steps_last_30 = (
        db.session.query(func.sum(StepAttribution.steps_attributed))
        .filter(
            StepAttribution.user_id == current_user.id,
            StepAttribution.sneaker_id == sneaker.id,
            StepAttribution.bucket_granularity == "day",
            StepAttribution.algorithm_version == ALGORITHM_V1,
            StepAttribution.bucket_start >= datetime.utcnow() - timedelta(days=30),
        )
        .scalar()
    )
    steps_last_7 = (
        db.session.query(func.sum(StepAttribution.steps_attributed))
        .filter(
            StepAttribution.user_id == current_user.id,
            StepAttribution.sneaker_id == sneaker.id,
            StepAttribution.bucket_granularity == "day",
            StepAttribution.algorithm_version == ALGORITHM_V1,
            StepAttribution.bucket_start >= datetime.utcnow() - timedelta(days=7),
        )
        .scalar()
    )

    exposure_notes = (
        db.session.query(ExposureEvent)
        .join(
            SneakerWear,
            (SneakerWear.sneaker_id == sneaker.id)
            & (SneakerWear.worn_at == ExposureEvent.date_local),
        )
        .filter(
            ExposureEvent.user_id == current_user.id,
            ExposureEvent.note.isnot(None),
        )
        .order_by(ExposureEvent.date_local.desc())
        .all()
    )

    since_cleaned_date = exposure_since_date(sneaker.last_cleaned_at)
    health_components = compute_health_components(
        sneaker=sneaker,
        user_id=current_user.id,
        materials=sneaker_materials,
    )
    wet_points_sum = health_components["wet_points_sum"]
    dirty_points_sum = health_components["dirty_points_sum"]
    wet_penalty = health_components["wet_penalty"]
    dirty_penalty = health_components["dirty_penalty"]
    steps_penalty = health_components["steps_penalty"]
    health_score = health_components["health_score"]
    recommendation_state = health_components["recommendation_state"]
    recommendation_label = health_components["recommendation_label"]
    recommendation_reason = health_components["recommendation_reason"]
    status_label = health_components["status_label"]
    wear_penalty = health_components["wear_penalty"]
    cosmetic_penalty = health_components["cosmetic_penalty"]
    structural_penalty = health_components["structural_penalty"]
    hygiene_penalty = health_components["hygiene_penalty"]
    wears_since_clean = health_components["wears_since_clean"]
    active_damage_count = health_components["active_damage_count"]
    confidence_score = health_components["confidence_score"]
    confidence_label = health_components["confidence_label"]
    latest_snapshot = (
        db.session.query(SneakerHealthSnapshot)
        .filter(
            SneakerHealthSnapshot.sneaker_id == sneaker.id,
            SneakerHealthSnapshot.user_id == current_user.id,
        )
        .order_by(SneakerHealthSnapshot.recorded_at.desc())
        .first()
    )
    breakdown_wear = (
        latest_snapshot.wear_penalty
        if latest_snapshot and latest_snapshot.wear_penalty is not None
        else wear_penalty
    )
    breakdown_cosmetic = (
        latest_snapshot.cosmetic_penalty
        if latest_snapshot and latest_snapshot.cosmetic_penalty is not None
        else cosmetic_penalty
    )
    breakdown_structural = (
        latest_snapshot.structural_penalty
        if latest_snapshot and latest_snapshot.structural_penalty is not None
        else structural_penalty
    )
    breakdown_hygiene = (
        latest_snapshot.hygiene_penalty
        if latest_snapshot and latest_snapshot.hygiene_penalty is not None
        else hygiene_penalty
    )
    breakdown_steps_total = (
        latest_snapshot.steps_total_used
        if latest_snapshot and latest_snapshot.steps_total_used is not None
        else steps_total
    )
    breakdown_health_score = (
        latest_snapshot.health_score if latest_snapshot else health_score
    )
    starting_health = float(getattr(sneaker, "starting_health", 100.0) or 100.0)
    breakdown_total_penalty = (
        (breakdown_wear or 0.0)
        + (breakdown_cosmetic or 0.0)
        + (breakdown_structural or 0.0)
        + (breakdown_hygiene or 0.0)
    )
    purchase_condition_label = sneaker.condition or "Unknown"
    wet_multiplier, dirty_multiplier = material_sensitivity_multipliers(sneaker_materials)
    stain_stats = _stain_stats_since_clean(sneaker.id, current_user.id, since_cleaned_date)
    has_stains_since_clean = stain_stats["count"] > 0
    has_sensitive_materials = has_sensitive_suede_materials(sneaker_materials)
    damage_type_labels = {
        "tear_upper": "Tear (Upper/Knit)",
        "upper_scuff": "Upper scuff / abrasion (leather/suede)",
        "upper_paint_chip": "Upper paint chip / colour loss",
        "sole_separation": "Sole separation",
        "midsole_crumble": "Midsole crumbling",
        "midsole_scuff": "Midsole scuff / marks",
        "midsole_paint_chip": "Midsole paint chip / peeling",
        "outsole_wear": "Outsole wear (balding)",
        "other": "Other",
    }
    active_damage_events = (
        db.session.query(SneakerDamageEvent)
        .filter(
            SneakerDamageEvent.sneaker_id == sneaker.id,
            SneakerDamageEvent.user_id == current_user.id,
            SneakerDamageEvent.is_active.is_(True),
        )
        .order_by(SneakerDamageEvent.reported_at.desc())
        .all()
    )

    return render_template(
        'sneaker_detail.html',
        sneaker=sneaker,
        release=release,
        avg_resale=avg_resale,
        preferred_currency=preferred_currency,
        form_for_modal=modal_form,
        back_url=back_url,
        back_label=back_label,
        source=source,
        finance_delta_display=finance_delta_display,
        wear_count=wear_count,
        cpw_display=cpw_display,
        first_wear_date=first_wear_date,
        sales_series=sales_series,
        sales_series_currency=sales_series_currency,
        sales_series_fetched_at=sales_series_fetched_at,
        sneaker_materials=sneaker_materials,
        steps_total=int(steps_total) if steps_total is not None else None,
        steps_last_30=int(steps_last_30) if steps_last_30 is not None else None,
        steps_last_7=int(steps_last_7) if steps_last_7 is not None else None,
        health_score=health_score,
        health_breakdown={
            "starting_health": starting_health,
            "purchase_condition_label": purchase_condition_label,
            "wear_penalty": breakdown_wear,
            "cosmetic_penalty": breakdown_cosmetic,
            "structural_penalty": breakdown_structural,
            "hygiene_penalty": breakdown_hygiene,
            "steps_total": breakdown_steps_total,
            "health_score": breakdown_health_score,
            "total_penalty": breakdown_total_penalty,
        },
        recommendation_label=recommendation_label,
        recommendation_reason=recommendation_reason,
        recommendation_state=recommendation_state,
        status_label=status_label,
        wear_penalty=wear_penalty,
        cosmetic_penalty=cosmetic_penalty,
        structural_penalty=structural_penalty,
        hygiene_penalty=hygiene_penalty,
        wears_since_clean=wears_since_clean,
        active_damage_count=active_damage_count,
        confidence_score=confidence_score,
        confidence_label=confidence_label,
        steps_penalty=steps_penalty,
        wet_points_sum=wet_points_sum,
        dirty_points_sum=dirty_points_sum,
        wet_penalty=wet_penalty,
        dirty_penalty=dirty_penalty,
        persistent_stain_points=float(sneaker.persistent_stain_points or 0.0),
        persistent_material_damage_points=float(sneaker.persistent_material_damage_points or 0.0),
        persistent_structural_damage_points=float(sneaker.persistent_structural_damage_points or 0.0),
        total_invested_display=total_invested_display,
        damage_form=damage_form,
        repair_form=repair_form,
        wet_multiplier=wet_multiplier,
        dirty_multiplier=dirty_multiplier,
        last_cleaned_at=sneaker.last_cleaned_at,
        exposure_notes=exposure_notes,
        has_stains_since_clean=has_stains_since_clean,
        has_sensitive_materials=has_sensitive_materials,
        active_damage_events=active_damage_events,
        damage_type_labels=damage_type_labels,
        care_tags=care_tags,
        care_tag_labels=CARE_TAG_LABELS,
    )


@sneakers_bp.route('/sneakers/<int:sneaker_id>')
@login_required
def sneaker_detail(sneaker_id):
    sneaker = Sneaker.query.filter_by(id=sneaker_id, user_id=current_user.id).first_or_404()
    canonical_slug = build_my_sneaker_slug(sneaker)
    return redirect(
        url_for('sneakers.my_sneaker_detail', sneaker_id=sneaker.id, slug=canonical_slug),
        code=302 if current_app.debug else 301,
    )


@sneakers_bp.route('/sneakers/<int:sneaker_id>/health-history', methods=['GET'])
@login_required
def sneaker_health_history(sneaker_id):
    sneaker = db.session.get(Sneaker, sneaker_id)
    if not sneaker or sneaker.owner != current_user:
        abort(404)

    wears = (
        db.session.query(SneakerWear)
        .filter(SneakerWear.sneaker_id == sneaker.id)
        .order_by(SneakerWear.worn_at.desc())
        .all()
    )
    wear_dates = [wear.worn_at for wear in wears]

    exposure_by_date = {}
    if wear_dates:
        exposures = (
            db.session.query(ExposureEvent)
            .filter(
                ExposureEvent.user_id == current_user.id,
                ExposureEvent.date_local.in_(wear_dates),
            )
            .all()
        )
        exposure_by_date = {event.date_local: event for event in exposures}

    steps_by_date = {}
    if wear_dates:
        min_date = min(wear_dates)
        max_date = max(wear_dates)
        step_rows = (
            db.session.query(
                StepAttribution.bucket_start,
                func.coalesce(func.sum(StepAttribution.steps_attributed), 0),
            )
            .filter(
                StepAttribution.user_id == current_user.id,
                StepAttribution.sneaker_id == sneaker.id,
                StepAttribution.bucket_granularity == "day",
                StepAttribution.algorithm_version == ALGORITHM_V1,
                StepAttribution.bucket_start >= datetime.combine(min_date, time.min),
                StepAttribution.bucket_start <= datetime.combine(max_date, time.max),
            )
            .group_by(StepAttribution.bucket_start)
            .all()
        )
        steps_by_date = {row[0].date(): int(row[1] or 0) for row in step_rows}

    wear_history = []
    for wear in wears:
        exposure = exposure_by_date.get(wear.worn_at)
        wear_history.append(
            {
                "date": wear.worn_at,
                "steps": steps_by_date.get(wear.worn_at),
                "got_wet": bool(exposure.got_wet) if exposure else False,
                "got_dirty": bool(exposure.got_dirty) if exposure else False,
                "stain_flag": bool(exposure.stain_flag) if exposure else False,
                "wet_severity": exposure.wet_severity if exposure else None,
                "dirty_severity": exposure.dirty_severity if exposure else None,
                "stain_severity": exposure.stain_severity if exposure else None,
            }
        )

    clean_events = (
        db.session.query(SneakerCleanEvent)
        .filter(
            SneakerCleanEvent.sneaker_id == sneaker.id,
            SneakerCleanEvent.user_id == current_user.id,
        )
        .order_by(SneakerCleanEvent.cleaned_at.desc())
        .all()
    )

    snapshots = (
        db.session.query(SneakerHealthSnapshot)
        .filter(
            SneakerHealthSnapshot.sneaker_id == sneaker.id,
            SneakerHealthSnapshot.user_id == current_user.id,
        )
        .order_by(SneakerHealthSnapshot.recorded_at.desc())
        .all()
    )

    return render_template(
        "health_history.html",
        sneaker=sneaker,
        wear_history=wear_history,
        clean_events=clean_events,
        damage_events=(
            db.session.query(SneakerDamageEvent)
            .filter(
                SneakerDamageEvent.sneaker_id == sneaker.id,
                SneakerDamageEvent.user_id == current_user.id,
            )
            .order_by(SneakerDamageEvent.reported_at.desc())
            .all()
        ),
        repair_events=(
            db.session.query(SneakerRepairEvent)
            .filter(
                SneakerRepairEvent.sneaker_id == sneaker.id,
                SneakerRepairEvent.user_id == current_user.id,
            )
            .order_by(SneakerRepairEvent.repaired_at.desc())
            .all()
        ),
        snapshots=snapshots,
    )

@sneakers_bp.route('/sneakers/<int:sneaker_id>/materials/add', methods=['POST'])
@login_required
def add_sneaker_material(sneaker_id):
    sneaker = db.session.get(Sneaker, sneaker_id)
    if not sneaker or sneaker.owner != current_user:
        abort(404)

    raw_material = (request.form.get("material") or "").strip()
    material = _normalize_material_label(raw_material)
    if not material:
        flash("Please enter a material.", "warning")
        return redirect(
            url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id, source=request.args.get("source"))
        )

    if not sneaker.sku:
        flash("SKU is required to save materials.", "warning")
        return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id, source=request.args.get("source")))

    sku_filters = [SneakerDB.sku.ilike(value) for value in sku_variants(sneaker.sku)]
    sneaker_record = SneakerDB.query.filter(or_(*sku_filters)).first()
    if not sneaker_record:
        flash("Could not find cached sneaker data for this SKU.", "warning")
        return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id, source=request.args.get("source")))

    materials = _load_materials_list(sneaker_record)
    if material not in materials:
        materials.append(material)

    sneaker_record.primary_material = materials[0] if materials else None
    sneaker_record.materials_json = json.dumps(materials)
    sneaker_record.materials_source = "manual"
    sneaker_record.materials_confidence = 1.0
    sneaker_record.materials_updated_at = datetime.utcnow()

    try:
        db.session.commit()
        flash("Material added.", "success")
    except Exception:
        db.session.rollback()
        flash("Unable to save material.", "danger")

    return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id, source=request.args.get("source")))


@sneakers_bp.route('/sneakers/<int:sneaker_id>/materials/delete', methods=['POST'])
@login_required
def delete_sneaker_material(sneaker_id):
    sneaker = db.session.get(Sneaker, sneaker_id)
    if not sneaker or sneaker.owner != current_user:
        abort(404)

    raw_material = (request.form.get("material") or "").strip()
    material = _normalize_material_label(raw_material)
    if not material:
        flash("Material not found.", "warning")
        return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id, source=request.args.get("source")))

    if not sneaker.sku:
        flash("SKU is required to save materials.", "warning")
        return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id, source=request.args.get("source")))

    sku_filters = [SneakerDB.sku.ilike(value) for value in sku_variants(sneaker.sku)]
    sneaker_record = SneakerDB.query.filter(or_(*sku_filters)).first()
    if not sneaker_record:
        flash("Could not find cached sneaker data for this SKU.", "warning")
        return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id, source=request.args.get("source")))

    materials = _load_materials_list(sneaker_record)
    materials = [item for item in materials if item.lower() != material.lower()]

    sneaker_record.primary_material = materials[0] if materials else None
    sneaker_record.materials_json = json.dumps(materials)
    sneaker_record.materials_source = "manual"
    sneaker_record.materials_confidence = 1.0
    sneaker_record.materials_updated_at = datetime.utcnow()

    try:
        db.session.commit()
        flash("Material removed.", "success")
    except Exception:
        db.session.rollback()
        flash("Unable to remove material.", "danger")

    return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id, source=request.args.get("source")))

# Add Sneaker Route

@sneakers_bp.route('/api/steps/buckets', methods=['POST'])
@bearer_or_login_required(scope="steps:write")
@csrf.exempt
def ingest_step_buckets():
    auth_user = g.api_user
    payload = request.get_json(silent=True) or {}
    source = (payload.get("source") or "").strip() or "unknown"
    timezone_name = (payload.get("timezone") or "").strip() or None
    granularity = (payload.get("granularity") or "").strip()
    buckets = payload.get("buckets") or []

    if granularity not in {"day", "hour"}:
        return jsonify({"status": "error", "message": "Invalid granularity."}), 400
    if not isinstance(buckets, list):
        return jsonify({"status": "error", "message": "Buckets must be a list."}), 400
    if len(buckets) > MAX_STEP_BUCKETS:
        return jsonify({"status": "error", "message": "Too many buckets."}), 400

    upserted = 0
    updated = 0
    bucket_starts = []
    fallback_timezone = auth_user.timezone or DEFAULT_TIMEZONE
    effective_timezone = _resolve_timezone_name(timezone_name, fallback_timezone)
    if effective_timezone is None:
        return jsonify({"status": "error", "message": "Invalid timezone."}), 400
    tzinfo = ZoneInfo(effective_timezone) if ZoneInfo is not None else None

    for bucket in buckets:
        date_value = bucket.get("date")
        start = _parse_iso_datetime(bucket.get("start"))
        end = _parse_iso_datetime(bucket.get("end"))
        steps = bucket.get("steps")
        if date_value and granularity == "day":
            try:
                local_date = datetime.strptime(str(date_value), "%Y-%m-%d").date()
            except (TypeError, ValueError):
                return jsonify({"status": "error", "message": "Invalid bucket date."}), 400
            if tzinfo is None:
                return jsonify({"status": "error", "message": "Timezone required."}), 400
            local_start = datetime.combine(local_date, time.min).replace(tzinfo=tzinfo)
            local_end = local_start + timedelta(days=1)
            start = local_start.astimezone(timezone.utc).replace(tzinfo=None)
            end = local_end.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            if start is None or end is None:
                return jsonify({"status": "error", "message": "Invalid bucket datetime."}), 400
            if end <= start:
                return jsonify({"status": "error", "message": "Bucket end must be after start."}), 400
        if not isinstance(steps, int) or steps < 0:
            return jsonify({"status": "error", "message": "Invalid steps value."}), 400

        existing = StepBucket.query.filter_by(
            user_id=auth_user.id,
            source=source,
            granularity=granularity,
            bucket_start=start,
        ).first()

        if existing:
            existing.bucket_end = end
            existing.steps = steps
            existing.timezone = effective_timezone
            updated += 1
        else:
            db.session.add(
                StepBucket(
                    user_id=auth_user.id,
                    source=source,
                    granularity=granularity,
                    bucket_start=start,
                    bucket_end=end,
                    steps=steps,
                    timezone=effective_timezone,
                )
            )
            upserted += 1
        bucket_starts.append(start)

    db.session.commit()

    recompute_stats = None
    if granularity == "day" and bucket_starts:
        start_dt = min(bucket_starts)
        end_dt = max(bucket_starts) + timedelta(days=1)
        recompute_stats = recompute_attribution(
            user_id=auth_user.id,
            granularity="day",
            start=start_dt,
            end=end_dt,
            algorithm_version=ALGORITHM_V1,
        )

    return jsonify(
        {
            "status": "success",
            "upserted_count": upserted,
            "updated_count": updated,
            "recompute": recompute_stats,
        }
    )


@sneakers_bp.route('/api/attribution/recompute', methods=['POST'])
@bearer_or_login_required(scope="steps:write")
@csrf.exempt
def recompute_steps_attribution():
    auth_user = g.api_user
    payload = request.get_json(silent=True) or {}
    granularity = (payload.get("granularity") or "").strip()
    start_raw = payload.get("start")
    end_raw = payload.get("end")

    if granularity not in {"day", "hour"}:
        return jsonify({"status": "error", "message": "Invalid granularity."}), 400
    start_dt = _parse_date_or_datetime(start_raw)
    end_dt = _parse_date_or_datetime(end_raw)
    if not start_dt or not end_dt:
        return jsonify({"status": "error", "message": "Invalid start/end date."}), 400

    try:
        stats = recompute_attribution(
        user_id=auth_user.id,
        granularity=granularity,
        start=start_dt,
        end=end_dt,
        algorithm_version=ALGORITHM_V1,
    )
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    return jsonify(
        {
            "status": "success",
            "buckets_processed": stats["buckets_processed"],
            "attributions_written": stats["attributions_written"],
        }
    )


@sneakers_bp.route('/dev/steps/seed', methods=['POST'])
@login_required
@csrf.exempt
def seed_fake_steps_endpoint():
    if not _is_dev_environment() or not getattr(current_user, "is_admin", False):
        return jsonify({"status": "error", "message": "Not available."}), 403

    payload = request.get_json(silent=True) or {}
    days = int(payload.get("days", 14))
    steps_min = int(payload.get("steps_min", 6000))
    steps_max = int(payload.get("steps_max", 12000))
    source = (payload.get("source") or "apple_health").strip() or "apple_health"
    granularity = (payload.get("granularity") or "day").strip()
    timezone_name = (payload.get("timezone") or "Europe/London").strip() or "Europe/London"

    if days <= 0 or days > 90:
        return jsonify({"status": "error", "message": "Invalid days value."}), 400

    try:
        stats = seed_fake_steps(
            user_id=current_user.id,
            days=days,
            steps_min=steps_min,
            steps_max=steps_max,
            source=source,
            granularity=granularity,
            timezone_name=timezone_name,
            seed=str(current_user.id),
        )
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    return jsonify({"status": "success", "stats": stats})

@sneakers_bp.route('/sneakers/<int:sneaker_id>/notes', methods=['POST'])
@login_required
def add_sneaker_note(sneaker_id):
    sneaker = db.session.get(Sneaker, sneaker_id)
    if not sneaker or sneaker.owner != current_user:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'status': 'error', 'message': 'Sneaker not found.'}), 404
        abort(404)

    body = (request.form.get('note') or '').strip()
    if not body:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'status': 'error', 'message': 'Note cannot be empty.'}), 400
        flash('Note cannot be empty.', 'warning')
        return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id))

    note = SneakerNote(sneaker_id=sneaker.id, body=body)
    db.session.add(note)
    db.session.commit()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            'status': 'success',
            'message': 'Note added.',
            'note': {
                'id': note.id,
                'body': note.body,
                'created_at': note.created_at.strftime('%b %d, %Y') if note.created_at else None,
                'delete_url': url_for('sneakers.delete_sneaker_note', sneaker_id=sneaker.id, note_id=note.id),
            }
        })

    flash('Note added.', 'success')
    return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id))


@sneakers_bp.route('/sneakers/<int:sneaker_id>/notes/<int:note_id>/delete', methods=['POST'])
@login_required
def delete_sneaker_note(sneaker_id, note_id):
    sneaker = db.session.get(Sneaker, sneaker_id)
    if not sneaker or sneaker.owner != current_user:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'status': 'error', 'message': 'Sneaker not found.'}), 404
        abort(404)

    note = SneakerNote.query.filter_by(id=note_id, sneaker_id=sneaker.id).first()
    if not note:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'status': 'error', 'message': 'Note not found.'}), 404
        abort(404)

    db.session.delete(note)
    db.session.commit()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'status': 'success', 'message': 'Note removed.', 'note_id': note_id})

    flash('Note removed.', 'success')
    return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id))

@sneakers_bp.route('/add-sneaker', methods=['POST'])
@login_required
def add_sneaker():
    form = SneakerForm()
    if not form.purchase_currency.data:
        form.purchase_currency.data = current_user.preferred_currency or "GBP"
    if form.validate_on_submit():
        final_image_location = None
        # Handle Image URL or Upload
        if form.image_option.data == 'upload' and form.sneaker_image_file.data:
            image_file = form.sneaker_image_file.data
            if allowed_file(image_file.filename):
                filename = secure_filename(image_file.filename)
                unique_filename = str(uuid.uuid4()) + os.path.splitext(filename)[1]
                image_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
                image_file.save(image_path)
                final_image_location = unique_filename
        elif form.image_option.data == 'url' and form.sneaker_image_url.data:
            final_image_location = form.sneaker_image_url.data

        # Create New Sneaker Object
        new_sneaker = Sneaker(
            brand=form.brand.data,
            model=form.model.data,
            sku=normalize_sku(form.sku.data) if form.sku.data else None,
            colorway=form.colorway.data.strip() if form.colorway.data else None,
            size_type=form.size_type.data,
            size=form.size.data,
            purchase_date=form.purchase_date.data,
            purchase_price=form.purchase_price.data,
            purchase_currency=form.purchase_currency.data or current_user.preferred_currency or "GBP",
            price_paid_currency=form.purchase_currency.data or current_user.preferred_currency or "GBP",
            condition=form.condition.data,
            starting_health=_starting_health_for_condition(form.condition.data),
            last_worn_date=form.last_worn_date.data,
            image_url=final_image_location,
            owner=current_user
        )
        db.session.add(new_sneaker)
        db.session.commit()
        if new_sneaker.sku:
            from routes.main_routes import _ensure_release_for_sku_with_resale
            _ensure_release_for_sku_with_resale(new_sneaker.sku)
            sku_filters = [SneakerDB.sku.ilike(value) for value in sku_variants(new_sneaker.sku)]
            sneaker_record = SneakerDB.query.filter(or_(*sku_filters)).first()
            if sneaker_record and sneaker_record.description and not sneaker_record.primary_material:
                materials = extract_materials(sneaker_record.description, "cached_description")
                materials_list = materials.get("materials") or []
                sneaker_record.primary_material = materials.get("primary_material")
                sneaker_record.materials_json = json.dumps(materials_list)
                sneaker_record.materials_source = materials.get("source") or "cached_description"
                sneaker_record.materials_confidence = materials.get("confidence")
                sneaker_record.materials_updated_at = datetime.utcnow()
                db.session.commit()
            elif not sneaker_record or not sneaker_record.primary_material:
                api_key = current_app.config.get('KICKS_API_KEY')
                if api_key:
                    client = KicksClient(
                        api_key=api_key,
                        base_url=current_app.config.get('KICKS_API_BASE_URL', 'https://api.kicks.dev'),
                        logger=current_app.logger,
                    )
                    try:
                        lookup_or_fetch_sneaker(
                            query=new_sneaker.sku,
                            db_session=db.session,
                            client=client,
                            max_age_hours=24,
                            force_best=True,
                            return_candidates=False,
                            mode="lite",
                        )
                    except Exception as exc:
                        current_app.logger.warning("Material backfill failed for '%s': %s", new_sneaker.sku, exc)

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'status': 'success', 'message': 'Sneaker added successfully!'})
        
        flash('Sneaker added successfully!', 'success')
        return redirect(url_for('sneakers.dashboard'))

    # Handle validation errors
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'status': 'error', 'errors': form.errors}), 400
    
    flash('There were errors with your submission.', 'danger')
    return redirect(url_for('sneakers.dashboard'))

# Edit Sneaker Route

@sneakers_bp.route('/edit-sneaker/<int:sneaker_id>', methods=['POST'])
@login_required
def edit_sneaker(sneaker_id):
    sneaker_to_edit = db.session.get(Sneaker, sneaker_id)
    if not sneaker_to_edit or sneaker_to_edit.owner != current_user:
        # For AJAX, return a JSON error; otherwise, abort
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'status': 'error', 'message': 'Permission denied.'}), 403
        abort(403)
    
    form = SneakerForm()
    if not form.purchase_currency.data:
        form.purchase_currency.data = current_user.preferred_currency or "GBP"
    if form.validate_on_submit():
        # Update text-based fields
        sneaker_to_edit.brand = form.brand.data
        sneaker_to_edit.model = form.model.data
        sneaker_to_edit.sku = normalize_sku(form.sku.data) if form.sku.data else None
        sneaker_to_edit.colorway = form.colorway.data.strip() if form.colorway.data else None
        sneaker_to_edit.size_type = form.size_type.data
        sneaker_to_edit.size = form.size.data
        sneaker_to_edit.purchase_date = form.purchase_date.data
        sneaker_to_edit.purchase_price = form.purchase_price.data
        sneaker_to_edit.purchase_currency = form.purchase_currency.data or current_user.preferred_currency or "GBP"
        sneaker_to_edit.price_paid_currency = form.purchase_currency.data or current_user.preferred_currency or "GBP"
        previous_condition = sneaker_to_edit.condition
        sneaker_to_edit.condition = form.condition.data
        if previous_condition != sneaker_to_edit.condition:
            sneaker_to_edit.starting_health = _starting_health_for_condition(form.condition.data)
        sneaker_to_edit.last_worn_date = form.last_worn_date.data

        # If a SKU was added and details are missing, backfill from cached lookup (KicksDB fallback).
        if sneaker_to_edit.sku and (
            not sneaker_to_edit.brand
            or not sneaker_to_edit.model
            or not sneaker_to_edit.colorway
            or not sneaker_to_edit.image_url
        ):
            api_key = current_app.config.get('KICKS_API_KEY')
            if api_key:
                client = KicksClient(
                    api_key=api_key,
                    base_url=current_app.config.get('KICKS_API_BASE_URL', 'https://api.kicks.dev'),
                    logger=current_app.logger,
                )
                try:
                    result = lookup_or_fetch_sneaker(
                        query=sneaker_to_edit.sku,
                        db_session=db.session,
                        client=client,
                        max_age_hours=24,
                        force_best=True,
                        return_candidates=False,
                        mode="lite",
                    )
                    data = result.get('sneaker') if result.get('status') == 'ok' else None
                    if data:
                        if not sneaker_to_edit.brand:
                            sneaker_to_edit.brand = data.get('brand') or sneaker_to_edit.brand
                        if not sneaker_to_edit.model:
                            sneaker_to_edit.model = data.get('model_name') or data.get('name') or sneaker_to_edit.model
                        if not sneaker_to_edit.colorway:
                            sneaker_to_edit.colorway = data.get('colorway') or sneaker_to_edit.colorway
                        if not sneaker_to_edit.image_url:
                            sneaker_to_edit.image_url = data.get('image_url') or sneaker_to_edit.image_url
                except Exception as exc:
                    current_app.logger.warning("Edit sneaker lookup failed for '%s': %s", sneaker_to_edit.sku, exc)
        
        # Handle new image (URL or Upload)
        if form.image_option.data == 'upload' and form.sneaker_image_file.data:
            image_file = form.sneaker_image_file.data
            if allowed_file(image_file.filename):
                filename = secure_filename(image_file.filename)
                unique_filename = str(uuid.uuid4()) + os.path.splitext(filename)[1]
                image_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
                image_file.save(image_path)
                sneaker_to_edit.image_url = unique_filename
        elif form.image_option.data == 'url' and form.sneaker_image_url.data:
            sneaker_to_edit.image_url = form.sneaker_image_url.data
        
        if previous_condition != sneaker_to_edit.condition:
            sneaker_materials = []
            if sneaker_to_edit.sku:
                sku_filters = [SneakerDB.sku.ilike(value) for value in sku_variants(sneaker_to_edit.sku)]
                sneaker_record = SneakerDB.query.filter(or_(*sku_filters)).first()
                if sneaker_record:
                    sneaker_materials = _load_materials_list(sneaker_record)
            health_components = compute_health_components(
                sneaker=sneaker_to_edit,
                user_id=current_user.id,
                materials=sneaker_materials,
            )
            db.session.add(
                SneakerHealthSnapshot(
                    sneaker_id=sneaker_to_edit.id,
                    user_id=current_user.id,
                    health_score=health_components["health_score"],
                    wear_penalty=health_components["wear_penalty"],
                    cosmetic_penalty=health_components["cosmetic_penalty"],
                    structural_penalty=health_components["structural_penalty"],
                    hygiene_penalty=health_components["hygiene_penalty"],
                    steps_total_used=int(health_components["steps_total"] or 0),
                    confidence_score=health_components["confidence_score"],
                    confidence_label=health_components["confidence_label"],
                    reason="purchase_condition_update",
                )
            )

        db.session.commit()
        if sneaker_to_edit.sku:
            from routes.main_routes import _ensure_release_for_sku_with_resale
            _ensure_release_for_sku_with_resale(sneaker_to_edit.sku)

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'status': 'success', 'message': 'Sneaker updated successfully!'})
            
        flash('Sneaker updated successfully!', 'success')
        return redirect(url_for('sneakers.dashboard'))

    # Handle validation errors
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'status': 'error', 'errors': form.errors}), 400
        
    flash('There were errors with your submission.', 'danger')
    return redirect(url_for('sneakers.dashboard'))

# Delete Sneaker Route

@sneakers_bp.route('/delete-sneaker/<int:sneaker_id>', methods=['POST'])
@login_required
def delete_sneaker(sneaker_id):
    sneaker_to_delete = db.session.get(Sneaker, sneaker_id)
    if not sneaker_to_delete:
        abort(404)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
              (request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html)

    if sneaker_to_delete.owner != current_user:
        if is_ajax:
            return jsonify({'status': 'error', 'message': 'You do not have permission.'}), 403
        else:
            flash('You do not have permission to delete this sneaker.', 'danger')
            return redirect(url_for('sneakers.dashboard'))

    try:
        sold_flag = request.form.get("sold") == "1"
        sold_price = request.form.get("sold_price")
        sold_currency = request.form.get("sold_currency") or (current_user.preferred_currency or "GBP")
        sold_date_raw = request.form.get("sold_date")
        sold_date = None
        if sold_flag:
            if not sold_price:
                if is_ajax:
                    return jsonify({'status': 'error', 'message': 'Sale price is required.'}), 400
                flash('Sale price is required.', 'danger')
                return redirect(url_for('sneakers.dashboard'))
            try:
                sold_price_value = Decimal(str(sold_price))
            except (InvalidOperation, TypeError):
                if is_ajax:
                    return jsonify({'status': 'error', 'message': 'Invalid sale price.'}), 400
                flash('Invalid sale price.', 'danger')
                return redirect(url_for('sneakers.dashboard'))
            if sold_date_raw:
                try:
                    sold_date = datetime.strptime(sold_date_raw, "%Y-%m-%d").date()
                except ValueError:
                    sold_date = None
            if not sold_date:
                sold_date = date.today()
            release_id = None
            if sneaker_to_delete.sku:
                sku_values = _sku_query_values([sneaker_to_delete.sku])
                release = (
                    db.session.query(Release)
                    .filter(func.upper(Release.sku).in_(sku_values))
                    .first()
                )
                if release:
                    release_id = release.id
            purchase_currency = (
                sneaker_to_delete.price_paid_currency
                or sneaker_to_delete.purchase_currency
                or (current_user.preferred_currency or "GBP")
            )
            db.session.add(
                SneakerSale(
                    sneaker_id=sneaker_to_delete.id,
                    release_id=release_id,
                    size_label=sneaker_to_delete.size,
                    size_type=sneaker_to_delete.size_type,
                    sold_price=sold_price_value,
                    sold_currency=sold_currency,
                    purchase_price=sneaker_to_delete.purchase_price,
                    purchase_currency=purchase_currency,
                    sold_at=sold_date,
                )
            )

        # If it's an uploaded image, delete the file from the server
        if sneaker_to_delete.image_url and not (sneaker_to_delete.image_url.startswith('http://') or sneaker_to_delete.image_url.startswith('https://')):
            old_file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], sneaker_to_delete.image_url)
            if os.path.exists(old_file_path):
                try:
                    os.remove(old_file_path)
                    current_app.logger.info(f"Deleted image file during sneaker delete: {old_file_path}")
                except Exception as e:
                    current_app.logger.error(f"Error deleting image file {old_file_path}: {e}")

        # --- Get data needed for count updates BEFORE deleting ---
        deleted_sneaker_brand = sneaker_to_delete.brand

        db.session.delete(sneaker_to_delete)
        db.session.commit()

        if is_ajax:
            # --- Get updated counts AFTER deleting ---
            overall_total_count = Sneaker.query.filter_by(user_id=current_user.id).count()

            # Get count for the brand of the deleted sneaker
            brand_specific_count = Sneaker.query.filter_by(user_id=current_user.id, brand=deleted_sneaker_brand).count()

            return jsonify({
                'status': 'success', 
                'message': 'Sneaker removed.',
                'overall_total_count': overall_total_count,
                'deleted_sneaker_brand': deleted_sneaker_brand,
                'brand_specific_count_for_deleted_brand': brand_specific_count
            })
        else:
            flash('Sneaker removed from your collection.', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting sneaker {sneaker_id}: {str(e)}")
        if is_ajax: return jsonify({'status': 'error', 'message': f'Error deleting sneaker: {str(e)}'}), 500
        else: flash(f'Error deleting sneaker: {str(e)}', 'danger')

    return redirect(url_for('sneakers.dashboard'))

# Update Last Worn Route

@sneakers_bp.route('/update-last-worn/<int:sneaker_id>', methods=['POST'])
@login_required
def update_last_worn(sneaker_id):
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    sneaker = db.session.get(Sneaker, sneaker_id)

    if not sneaker:
        if is_ajax:
            return jsonify({'status': 'error', 'message': 'Sneaker not found.'}), 404
        abort(404)

    # --- THIS IS THE CRUCIAL SECURITY CHECK ---
    if sneaker.owner != current_user:
        if is_ajax:
            # For an AJAX request, return a JSON error with a 403 status
            return jsonify({'status': 'error', 'message': 'Permission denied.'}), 403
        else:
            # For a normal form post, flash and redirect
            flash('You do not have permission to update this sneaker.', 'danger')
            return redirect(url_for('sneakers.dashboard'))

    # --- Rest of the function logic ---
    new_date_str = request.form.get('new_last_worn')
    if not new_date_str:
        return jsonify({'status': 'error', 'message': 'No date provided.'}), 400

    try:
        sneaker.last_worn_date = date.fromisoformat(new_date_str)
        db.session.add(SneakerWear(sneaker_id=sneaker.id, worn_at=sneaker.last_worn_date))
        if request.form.get("exposure_update"):
            got_wet = bool(request.form.get("got_wet"))
            got_dirty = bool(request.form.get("got_dirty"))
            wet_severity = request.form.get("wet_severity")
            dirty_severity = request.form.get("dirty_severity")
            stain_flag = bool(request.form.get("stain_flag")) and (got_wet or got_dirty)
            stain_severity = request.form.get("stain_severity")
            note = request.form.get("note")
            upsert_daily_exposure(
                user_id=current_user.id,
                date_local=sneaker.last_worn_date,
                timezone=current_user.timezone or "Europe/London",
                got_wet=got_wet,
                got_dirty=got_dirty,
                wet_severity=wet_severity,
                dirty_severity=dirty_severity,
                stain_flag=stain_flag,
                stain_severity=stain_severity,
                note=note,
            )
            recompute_exposure_attributions(
                current_user.id, sneaker.last_worn_date, sneaker.last_worn_date
            )
        sneaker_materials = []
        if sneaker.sku:
            sku_filters = [SneakerDB.sku.ilike(value) for value in sku_variants(sneaker.sku)]
            sneaker_record = SneakerDB.query.filter(or_(*sku_filters)).first()
            if sneaker_record:
                sneaker_materials = _load_materials_list(sneaker_record)
        health_components = compute_health_components(
            sneaker=sneaker,
            user_id=current_user.id,
            materials=sneaker_materials,
        )
        db.session.add(
            SneakerHealthSnapshot(
                sneaker_id=sneaker.id,
                user_id=current_user.id,
                health_score=health_components["health_score"],
                wear_penalty=health_components["wear_penalty"],
                cosmetic_penalty=health_components["cosmetic_penalty"],
                structural_penalty=health_components["structural_penalty"],
                hygiene_penalty=health_components["hygiene_penalty"],
                steps_total_used=int(health_components["steps_total"] or 0),
                confidence_score=health_components["confidence_score"],
                confidence_label=health_components["confidence_label"],
                reason="wear",
            )
        )
        db.session.commit()
        wear_count = (
            db.session.query(func.count(SneakerWear.id))
            .filter(SneakerWear.sneaker_id == sneaker.id)
            .scalar()
        ) or 0
        first_wear_date = (
            db.session.query(func.min(SneakerWear.worn_at))
            .filter(SneakerWear.sneaker_id == sneaker.id)
            .scalar()
        )
        cpw_display = None
        if wear_count:
            preferred_currency = current_user.preferred_currency or "GBP"
            total_cost_value = _total_invested_for_sneaker(sneaker, preferred_currency)
            if total_cost_value:
                cpw_value = Decimal(str(total_cost_value)) / Decimal(str(wear_count))
                cpw_display = format_money(cpw_value, preferred_currency)
        return jsonify({
            'status': 'success',
            'message': 'Date updated!',
            'new_date_display': sneaker.last_worn_date.strftime('%b %d, %Y'),
            'wear_count': wear_count,
            'first_wear_date_display': (
                first_wear_date.strftime('%b %d, %Y') if first_wear_date else None
            ),
            'cpw_display': cpw_display
        })
    except (ValueError, TypeError):
        return jsonify({'status': 'error', 'message': 'Invalid date format.'}), 400
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating last_worn_date for sneaker {sneaker_id}: {e}")
        return jsonify({'status': 'error', 'message': 'A database error occurred.'}), 500


@sneakers_bp.route('/sneakers/<int:sneaker_id>/mark-cleaned', methods=['POST'])
@login_required
def mark_sneaker_cleaned(sneaker_id):
    form = EmptyForm()
    if not form.validate_on_submit():
        abort(400)

    sneaker = db.session.get(Sneaker, sneaker_id)
    if not sneaker or sneaker.owner != current_user:
        abort(404)

    notes_action = request.form.get("notes_action", "keep")
    selected_ids = request.form.getlist("note_ids")
    notes_query = (
        db.session.query(ExposureEvent)
        .join(
            SneakerWear,
            (SneakerWear.sneaker_id == sneaker.id)
            & (SneakerWear.worn_at == ExposureEvent.date_local),
        )
        .filter(
            ExposureEvent.user_id == current_user.id,
            ExposureEvent.note.isnot(None),
        )
    )
    if notes_action == "delete_all":
        for event in notes_query.all():
            event.note = None
            event.updated_at = datetime.utcnow()
    elif notes_action == "delete_selected" and selected_ids:
        for event in notes_query.filter(ExposureEvent.id.in_(selected_ids)).all():
            event.note = None
            event.updated_at = datetime.utcnow()

    since_cleaned_date = exposure_since_date(sneaker.last_cleaned_at)
    stain_stats = _stain_stats_since_clean(sneaker.id, current_user.id, since_cleaned_date)
    has_stains_since_clean = stain_stats["count"] > 0
    stain_removed_raw = request.form.get("stain_removed")
    stain_removed = None
    if has_stains_since_clean and stain_removed_raw in {"yes", "no"}:
        stain_removed = stain_removed_raw == "yes"

    sneaker_materials = []
    if sneaker.sku:
        sku_filters = [SneakerDB.sku.ilike(value) for value in sku_variants(sneaker.sku)]
        sneaker_record = SneakerDB.query.filter(or_(*sku_filters)).first()
        if sneaker_record:
            sneaker_materials = _load_materials_list(sneaker_record)

    has_sensitive_materials = has_sensitive_suede_materials(sneaker_materials)
    lasting_material_impact = bool(request.form.get("lasting_material_impact")) if has_sensitive_materials else False

    if stain_removed is True:
        sneaker.persistent_stain_points = 0.0
    elif stain_removed is False and stain_stats["max_severity"]:
        computed_stain_points = compute_persistent_stain_points(
            stain_stats["max_severity"], sneaker_materials
        )
        sneaker.persistent_stain_points = max(
            float(sneaker.persistent_stain_points or 0.0),
            computed_stain_points,
        )

    if lasting_material_impact and has_sensitive_materials:
        sneaker.persistent_material_damage_points = float(
            sneaker.persistent_material_damage_points or 0.0
        ) + compute_material_damage_points(sneaker_materials)

    clean_notes = (request.form.get("clean_notes") or "").strip() or None
    if clean_notes:
        clean_notes = clean_notes[:280]

    sneaker.last_cleaned_at = datetime.utcnow()
    db.session.add(
        SneakerCleanEvent(
            sneaker_id=sneaker.id,
            user_id=current_user.id,
            cleaned_at=sneaker.last_cleaned_at,
            stain_removed=stain_removed,
            lasting_material_impact=lasting_material_impact,
            notes=clean_notes,
        )
    )

    health_components = compute_health_components(
        sneaker=sneaker,
        user_id=current_user.id,
        materials=sneaker_materials,
    )
    db.session.add(
        SneakerHealthSnapshot(
            sneaker_id=sneaker.id,
            user_id=current_user.id,
            health_score=health_components["health_score"],
            wear_penalty=health_components["wear_penalty"],
            cosmetic_penalty=health_components["cosmetic_penalty"],
            structural_penalty=health_components["structural_penalty"],
            hygiene_penalty=health_components["hygiene_penalty"],
            steps_total_used=int(health_components["steps_total"] or 0),
            confidence_score=health_components["confidence_score"],
            confidence_label=health_components["confidence_label"],
            reason="clean",
        )
    )

    db.session.commit()
    flash("Sneaker marked as cleaned.", "success")
    return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id))


@sneakers_bp.route('/sneakers/<int:sneaker_id>/exposure-notes/add', methods=['POST'])
@login_required
def add_exposure_note(sneaker_id):
    form = EmptyForm()
    if not form.validate_on_submit():
        abort(400)

    sneaker = db.session.get(Sneaker, sneaker_id)
    if not sneaker or sneaker.owner != current_user:
        abort(404)

    note_text = (request.form.get("note") or "").strip()
    date_str = (request.form.get("date_local") or "").strip()
    if not note_text:
        flash("Please enter a note.", "warning")
        return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id))

    if date_str:
        date_local = date.fromisoformat(date_str)
    elif sneaker.last_worn_date:
        date_local = sneaker.last_worn_date
    else:
        flash("Please set a last worn date before adding a note.", "warning")
        return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id))
    worn = (
        db.session.query(SneakerWear.id)
        .filter(SneakerWear.sneaker_id == sneaker.id, SneakerWear.worn_at == date_local)
        .first()
    )
    if not worn:
        flash("That date isn't logged as worn for this sneaker.", "warning")
        return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id))

    event = (
        db.session.query(ExposureEvent)
        .filter_by(user_id=current_user.id, date_local=date_local)
        .first()
    )
    if event:
        event.note = note_text[:140]
        event.updated_at = datetime.utcnow()
    else:
        upsert_daily_exposure(
            user_id=current_user.id,
            date_local=date_local,
            timezone=current_user.timezone or "Europe/London",
            got_wet=False,
            got_dirty=False,
            wet_severity=None,
            dirty_severity=None,
            note=note_text,
        )
    db.session.commit()
    flash("Exposure note saved.", "success")
    return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id))


@sneakers_bp.route('/sneakers/<int:sneaker_id>/exposure-notes/<int:event_id>/update', methods=['POST'])
@login_required
def update_exposure_note(sneaker_id, event_id):
    form = EmptyForm()
    if not form.validate_on_submit():
        abort(400)

    sneaker = db.session.get(Sneaker, sneaker_id)
    if not sneaker or sneaker.owner != current_user:
        abort(404)

    event = db.session.get(ExposureEvent, event_id)
    if not event or event.user_id != current_user.id:
        abort(404)

    worn = (
        db.session.query(SneakerWear.id)
        .filter(SneakerWear.sneaker_id == sneaker.id, SneakerWear.worn_at == event.date_local)
        .first()
    )
    if not worn:
        abort(404)

    note_text = (request.form.get("note") or "").strip()
    event.note = note_text[:140] if note_text else None
    event.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Exposure note updated.", "success")
    return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id))


@sneakers_bp.route('/sneakers/<int:sneaker_id>/exposure-notes/<int:event_id>/delete', methods=['POST'])
@login_required
def delete_exposure_note(sneaker_id, event_id):
    form = EmptyForm()
    if not form.validate_on_submit():
        abort(400)

    sneaker = db.session.get(Sneaker, sneaker_id)
    if not sneaker or sneaker.owner != current_user:
        abort(404)

    event = db.session.get(ExposureEvent, event_id)
    if not event or event.user_id != current_user.id:
        abort(404)

    worn = (
        db.session.query(SneakerWear.id)
        .filter(SneakerWear.sneaker_id == sneaker.id, SneakerWear.worn_at == event.date_local)
        .first()
    )
    if not worn:
        abort(404)

    event.note = None
    event.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Exposure note removed.", "success")
    return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id))


@sneakers_bp.route('/sneakers/<int:sneaker_id>/damage', methods=['POST'])
@login_required
def report_sneaker_damage(sneaker_id):
    form = DamageReportForm()
    if not form.validate_on_submit():
        flash("Please check the damage details and try again.", "danger")
        return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker_id))

    sneaker = db.session.get(Sneaker, sneaker_id)
    if not sneaker or sneaker.owner != current_user:
        abort(404)

    if form.damage_type.data == "other":
        raw_notes = (form.notes.data or "").strip()
        if not raw_notes:
            flash("Please provide details for \"Other\" damage.", "danger")
            return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id))

    severity = int(form.severity.data)
    normalized_type = normalize_damage_type(form.damage_type.data)
    penalty_points = compute_damage_penalty_points(normalized_type, severity)
    notes = (form.notes.data or "").strip() or None
    if notes:
        notes = notes[:280]

    damage_event = SneakerDamageEvent(
        sneaker_id=sneaker.id,
        user_id=current_user.id,
        damage_type=normalized_type,
        severity=severity,
        notes=notes,
        health_penalty_points=penalty_points,
        is_active=True,
    )
    db.session.add(damage_event)
    sneaker.persistent_structural_damage_points = _recompute_structural_damage_points(sneaker.id)

    sneaker_materials = []
    if sneaker.sku:
        sku_filters = [SneakerDB.sku.ilike(value) for value in sku_variants(sneaker.sku)]
        sneaker_record = SneakerDB.query.filter(or_(*sku_filters)).first()
        if sneaker_record:
            sneaker_materials = _load_materials_list(sneaker_record)

    health_components = compute_health_components(
        sneaker=sneaker,
        user_id=current_user.id,
        materials=sneaker_materials,
    )
    db.session.add(
        SneakerHealthSnapshot(
            sneaker_id=sneaker.id,
            user_id=current_user.id,
            health_score=health_components["health_score"],
            wear_penalty=health_components["wear_penalty"],
            cosmetic_penalty=health_components["cosmetic_penalty"],
            structural_penalty=health_components["structural_penalty"],
            hygiene_penalty=health_components["hygiene_penalty"],
            steps_total_used=int(health_components["steps_total"] or 0),
            confidence_score=health_components["confidence_score"],
            confidence_label=health_components["confidence_label"],
            reason="damage",
        )
    )

    db.session.commit()
    flash("Damage recorded.", "success")
    return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id))


@sneakers_bp.route('/sneakers/<int:sneaker_id>/repair', methods=['POST'])
@login_required
def repair_sneaker(sneaker_id):
    form = RepairEventForm()
    legacy_repair_type_map = {
        "glue": "glue_sole",
        "stitch": "stitching",
    }
    legacy_provider_map = {
        "local cobbler": "local_cobbler",
        "Local cobbler": "local_cobbler",
        "Local Cobbler": "local_cobbler",
    }
    if request.method == "POST":
        raw_type = request.form.get("repair_type")
        raw_provider = request.form.get("provider")
        raw_kind = request.form.get("repair_kind")
        if raw_type in legacy_repair_type_map:
            form.repair_type.choices.append((raw_type, raw_type))
        if raw_provider in legacy_provider_map:
            form.provider.choices.append((raw_provider, raw_provider))
        if raw_kind == "restoration":
            form.repair_type.data = "full_restoration"
    if not form.validate_on_submit():
        flash("Please check the repair details and try again.", "danger")
        return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker_id))

    sneaker = db.session.get(Sneaker, sneaker_id)
    if not sneaker or sneaker.owner != current_user:
        abort(404)

    repair_kind = form.repair_kind.data
    raw_repair_type = (form.repair_type.data or "").strip()
    repair_type = legacy_repair_type_map.get(raw_repair_type, raw_repair_type)
    repair_type_other = (form.repair_type_other.data or "").strip() or None
    raw_provider = (form.provider.data or "").strip() or None
    provider = legacy_provider_map.get(raw_provider, raw_provider)
    provider_other = (form.provider_other.data or "").strip() or None
    repair_area = (form.repair_area.data or "").strip() or None
    if repair_type == "other" and not repair_type_other:
        flash("Please specify the repair type.", "danger")
        return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id))
    if provider == "other" and not provider_other:
        flash("Please specify the provider.", "danger")
        return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id))
    notes = (form.notes.data or "").strip() or None
    if notes:
        notes = notes[:280]

    resolved_raw = request.form.get("resolved_all_active_damage")
    resolved_all_active_damage = str(resolved_raw).lower() in {"1", "true", "on", "y", "yes"}
    selected_damage_ids = request.form.getlist("resolved_damage_ids")

    sneaker.persistent_structural_damage_points = _recompute_structural_damage_points(sneaker.id)

    cost_amount = form.cost_amount.data
    cost_currency = form.cost_currency.data or current_user.preferred_currency or "GBP"
    if cost_amount is not None:
        db.session.add(
            SneakerExpense(
                sneaker_id=sneaker.id,
                user_id=current_user.id,
                category=repair_kind,
                amount=cost_amount,
                currency=cost_currency,
                expense_date=datetime.utcnow(),
                notes=notes,
            )
        )

    repair_event = SneakerRepairEvent(
        sneaker_id=sneaker.id,
        user_id=current_user.id,
        repair_kind=repair_kind,
        repair_type=repair_type,
        repair_type_other=repair_type_other if repair_type == "other" else None,
        provider=provider,
        provider_other=provider_other if provider == "other" else None,
        repair_area=repair_area if repair_kind == "repair" else None,
        cost_amount=cost_amount,
        cost_currency=cost_currency if cost_amount is not None else None,
        notes=notes,
        resolved_all_active_damage=resolved_all_active_damage,
    )
    db.session.add(repair_event)
    db.session.flush()

    resolved_rows = []
    if resolved_all_active_damage:
        active_rows = SneakerDamageEvent.query.filter_by(
            sneaker_id=sneaker.id,
            is_active=True,
        ).all()
        for row in active_rows:
            row.is_active = False
            row.updated_at = datetime.utcnow()
            resolved_rows.append(row)
            db.session.add(
                SneakerRepairResolvedDamage(
                    repair_event_id=repair_event.id,
                    damage_event_id=row.id,
                )
            )
    else:
        if not selected_damage_ids:
            selected_damage_ids = []
        active_rows = (
            SneakerDamageEvent.query.filter(
                SneakerDamageEvent.sneaker_id == sneaker.id,
                SneakerDamageEvent.user_id == current_user.id,
                SneakerDamageEvent.is_active.is_(True),
                SneakerDamageEvent.id.in_(selected_damage_ids),
            )
            .all()
        )
        if selected_damage_ids and (not active_rows or len(active_rows) != len(set(selected_damage_ids))):
            db.session.rollback()
            flash("Selected damage items are invalid.", "danger")
            return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id))
        for row in active_rows:
            row.is_active = False
            row.updated_at = datetime.utcnow()
            resolved_rows.append(row)
            db.session.add(
                SneakerRepairResolvedDamage(
                    repair_event_id=repair_event.id,
                    damage_event_id=row.id,
                )
            )

    baseline_delta = 0.0
    if repair_kind == "restoration":
        original = float(sneaker.starting_health or 100.0)
        sneaker.starting_health = max(sneaker.starting_health or 100.0, 90.0)
        baseline_delta = max(0.0, sneaker.starting_health - original)
    else:
        if resolved_rows:
            severity_sum = sum(int(row.severity or 0) for row in resolved_rows)
            bump = min(3, severity_sum)
            original = float(sneaker.starting_health or 100.0)
            sneaker.starting_health = min(90.0, original + bump)
            baseline_delta = max(0.0, sneaker.starting_health - original)
        else:
            if not repair_area:
                db.session.rollback()
                flash("Please select the repair area.", "danger")
                return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id))
            area_bump_map = {
                "upper": 4.0,
                "midsole": 4.0,
                "outsole": 4.0,
                "insole": 1.0,
                "lace": 1.0,
                "other": 2.0,
            }
            bump = area_bump_map.get(repair_area, 0.0)
            original = float(sneaker.starting_health or 100.0)
            sneaker.starting_health = min(90.0, original + bump)
            baseline_delta = max(0.0, sneaker.starting_health - original)

    repair_event.baseline_delta_applied = baseline_delta
    sneaker.persistent_structural_damage_points = _recompute_structural_damage_points(sneaker.id)

    sneaker_materials = []
    if sneaker.sku:
        sku_filters = [SneakerDB.sku.ilike(value) for value in sku_variants(sneaker.sku)]
        sneaker_record = SneakerDB.query.filter(or_(*sku_filters)).first()
        if sneaker_record:
            sneaker_materials = _load_materials_list(sneaker_record)

    health_components = compute_health_components(
        sneaker=sneaker,
        user_id=current_user.id,
        materials=sneaker_materials,
    )
    db.session.add(
        SneakerHealthSnapshot(
            sneaker_id=sneaker.id,
            user_id=current_user.id,
            health_score=health_components["health_score"],
            wear_penalty=health_components["wear_penalty"],
            cosmetic_penalty=health_components["cosmetic_penalty"],
            structural_penalty=health_components["structural_penalty"],
            hygiene_penalty=health_components["hygiene_penalty"],
            steps_total_used=int(health_components["steps_total"] or 0),
            confidence_score=health_components["confidence_score"],
            confidence_label=health_components["confidence_label"],
            reason=repair_kind,
        )
    )

    db.session.commit()
    flash("Repair/restoration saved.", "success")
    return redirect(url_for('sneakers.sneaker_detail', sneaker_id=sneaker.id))

# Fetch Sneaker Data Route

@sneakers_bp.route('/sneaker-data/<int:sneaker_id>', methods=['GET'])
@login_required
def get_sneaker_data(sneaker_id):
    sneaker = db.session.get(Sneaker, sneaker_id)
    if not sneaker or sneaker.owner != current_user:
        return jsonify({'status': 'error', 'message': 'Sneaker not found or permission denied.'}), 404
    if sneaker.sku:
        from routes.main_routes import _ensure_release_for_sku_with_resale
        _ensure_release_for_sku_with_resale(sneaker.sku)

    # Determine the correct URL for the preview image
    image_display_url = None
    if sneaker.image_url:
        if sneaker.image_url.startswith('http'):
            image_display_url = sneaker.image_url
        else:
            # Use _external=True to generate a full URL for AJAX
            image_display_url = url_for('main.uploaded_file', filename=sneaker.image_url, _external=True)

    sneaker_data = {
        'brand': sneaker.brand,
        'model': sneaker.model,
        'sku': sneaker.sku,
        'colorway': sneaker.colorway,
        'size': sneaker.size,
        'size_type': sneaker.size_type,
        'last_worn_date': sneaker.last_worn_date.strftime('%Y-%m-%d') if sneaker.last_worn_date else '',
        'purchase_price': str(sneaker.purchase_price) if sneaker.purchase_price is not None else '',
        'purchase_currency': sneaker.purchase_currency,
        'condition': sneaker.condition,
        'purchase_date': sneaker.purchase_date.strftime('%Y-%m-%d') if sneaker.purchase_date else '',
        'sneaker_image_url': sneaker.image_url if sneaker.image_url and sneaker.image_url.startswith('http') else '',
        'current_image_display_url': image_display_url
    }
    return jsonify({'status': 'success', 'sneaker': sneaker_data})

# Add to Rotation Route

@sneakers_bp.route('/add-to-rotation/<int:sneaker_id>', methods=['POST'])
@login_required
def add_to_rotation(sneaker_id):
    sneaker = db.session.get(Sneaker, sneaker_id)
    if not sneaker:
        abort(404)
    if sneaker.owner != current_user:
        return jsonify({'status': 'error', 'message': 'Permission denied.'}), 403

    sneaker.in_rotation = True
    db.session.commit()

    # Return the re-rendered button HTML so the UI can update
    new_button_html = render_template('_rotation_button.html', sneaker=sneaker)
    return jsonify({
        'status': 'success', 
        'message': f"Added '{sneaker.brand} {sneaker.model}' to your rotation.",
        'new_button_html': new_button_html
    })

# Remove from Rotation Route

@sneakers_bp.route('/remove-from-rotation/<int:sneaker_id>', methods=['POST'])
@login_required
def remove_from_rotation(sneaker_id):
    sneaker = db.session.get(Sneaker, sneaker_id)

    # Check if sneaker exists
    if not sneaker:
        abort(404) # Or return a JSON error for AJAX

    # Check ownership
    if sneaker.owner != current_user:
        return jsonify({'status': 'error', 'message': 'Permission denied.'}), 403

    sneaker.in_rotation = False
    db.session.commit()

    # Re-render the button HTML so the UI can update
    new_button_html = render_template('_rotation_button.html', sneaker=sneaker)
    return jsonify({
        'status': 'success', 
        'message': f"Removed '{sneaker.brand} {sneaker.model}' from your rotation.",
        'in_rotation': False, # So JS knows the sneaker was removed from rotation
        'new_button_html': new_button_html
    })

# Select for Rotation Route

@sneakers_bp.route('/select-for-rotation', methods=['GET', 'POST'])
@login_required
def select_for_rotation():
    # --- POST request logic: Handles the form submission ---
    if request.method == 'POST':
        sneaker_ids_to_add_str = request.form.getlist('sneaker_ids')
        if not sneaker_ids_to_add_str:
            flash('You did not select any sneakers to add.', 'warning')
            return redirect(url_for('sneakers.select_for_rotation'))
        try:
            sneaker_ids_to_add = [int(id_str) for id_str in sneaker_ids_to_add_str]
            sneakers_to_update = Sneaker.query.filter(
                Sneaker.id.in_(sneaker_ids_to_add), 
                Sneaker.user_id == current_user.id # This security check is key
            ).all()
            
            updated_count = 0
            for sneaker in sneakers_to_update:
                sneaker.in_rotation = True
                updated_count += 1
            
            db.session.commit()
            
            if updated_count > 0:
                flash(f'{updated_count} sneaker{"s" if updated_count != 1 else ""} {"have" if updated_count != 1 else "has"} been added to your rotation.', 'success')

            else:
                # This is the message the test is looking for
                flash('No sneakers were added. Please check your selection.', 'warning') 

            return redirect(url_for('sneakers.rotation'))
        
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error adding sneakers to rotation: {e}")
            flash('An error occurred while updating your rotation.', 'danger')
            return redirect(url_for('sneakers.select_for_rotation'))

    # --- GET request logic: Displays the page with sorting/filtering/searching ---
    sort_by_param = request.args.get('sort_by')
    order_param = request.args.get('order')
    filter_brand_param = request.args.get('filter_brand')
    search_term_param = request.args.get('search_term')

    sort_active_in_url = bool(sort_by_param)
    effective_sort_by = 'purchase_date'
    effective_order = 'desc'

    if sort_by_param:
        if sort_by_param == 'brand':
            effective_sort_by = 'brand'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'asc'
        elif sort_by_param == 'model':
            effective_sort_by = 'model'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'asc'
        elif sort_by_param == 'purchase_date': 
            effective_sort_by = 'purchase_date'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'desc'
        elif sort_by_param == 'last_worn_date':
            effective_sort_by = 'last_worn_date'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'desc'
        elif sort_by_param == 'purchase_price':
            effective_sort_by = 'purchase_price'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'desc'
        elif sort_by_param == 'resale_value':
            effective_sort_by = 'resale_value'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'desc'
        elif sort_by_param == 'id': 
            effective_sort_by = 'id'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'desc'
    
    # Base query: all sneakers for the user that are NOT in rotation
    query = Sneaker.query.filter_by(user_id=current_user.id, in_rotation=False)

    is_brand_filter_active = bool(filter_brand_param and filter_brand_param.lower() != 'all')
    is_search_active = bool(search_term_param and search_term_param.strip())
    current_filter_brand = filter_brand_param.strip() if is_brand_filter_active else None
    current_search_term = search_term_param.strip() if is_search_active else None

    if current_filter_brand:
        query = query.filter(Sneaker.brand == current_filter_brand)
    if current_search_term:
        keywords = _normalize_search_tokens(current_search_term)
        search_conditions = []
        for keyword in keywords:
            if not keyword:
                continue
            keyword_condition = or_(
                Sneaker.brand.ilike(f"%{keyword}%"),
                Sneaker.model.ilike(f"%{keyword}%"),
                Sneaker.colorway.ilike(f"%{keyword}%"),
            )
            search_conditions.append(keyword_condition)
        if search_conditions:
            query = query.filter(*search_conditions)

    # Apply sorting
    if effective_sort_by != 'resale_value':
        if effective_sort_by == 'brand':
            order_obj = Sneaker.brand.desc() if effective_order == 'desc' else Sneaker.brand.asc()
        elif effective_sort_by == 'model':
            order_obj = Sneaker.model.desc() if effective_order == 'desc' else Sneaker.model.asc()
        elif effective_sort_by == 'purchase_date':
            order_obj = Sneaker.purchase_date.desc().nullslast() if effective_order == 'desc' else Sneaker.purchase_date.asc().nullsfirst()
        elif effective_sort_by == 'last_worn_date':
            order_obj = Sneaker.last_worn_date.desc().nullslast() if effective_order == 'desc' else Sneaker.last_worn_date.asc().nullsfirst()
        elif effective_sort_by == 'purchase_price':
            order_obj = Sneaker.purchase_price.desc().nullslast() if effective_order == 'desc' else Sneaker.purchase_price.asc().nullsfirst()
        elif effective_sort_by == 'id':
            order_obj = Sneaker.id.desc() if effective_order == 'desc' else Sneaker.id.asc()
        else: # Default case
            order_obj = Sneaker.purchase_date.desc().nullslast()

        query = query.order_by(order_obj)
    
    available_sneakers = query.order_by(Sneaker.brand, Sneaker.model).all() # Using a simple sort for this example
    if current_search_term:
        tokens = _normalize_search_tokens(current_search_term)
        available_sneakers = [
            sneaker for sneaker in available_sneakers
            if _matches_search_tokens(" ".join([sneaker.brand or "", sneaker.model or "", sneaker.colorway or ""]), tokens)
        ]
    if effective_sort_by == 'resale_value':
        preferred_currency = current_user.preferred_currency or "GBP"
        skus_for_sort = [_normalize_sku_value(sneaker.sku) for sneaker in available_sneakers if sneaker.sku]
        release_by_sku = {}
        if skus_for_sort:
            skus_for_query = _sku_query_values(skus_for_sort)
            releases = (
                db.session.query(Release)
                .options(joinedload(Release.offers))
                .filter(func.upper(Release.sku).in_(skus_for_query))
                .all()
            )
            release_by_sku = {
                _normalize_sku_value(release.sku): release for release in releases if release.sku
            }
        def resale_key(sneaker):
            release = release_by_sku.get(_normalize_sku_value(sneaker.sku))
            value = _resale_sort_value(sneaker, release, preferred_currency)
            return (1, Decimal("0")) if value is None else (0, value)
        available_sneakers.sort(key=resale_key, reverse=(effective_order == 'desc'))

    # Get distinct brands for the filter dropdown
    base_available_query = Sneaker.query.filter_by(user_id=current_user.id, in_rotation=False)
    distinct_brands_tuples = base_available_query.with_entities(Sneaker.brand).distinct().order_by(Sneaker.brand).all()
    brands_for_filter = [brand[0] for brand in distinct_brands_tuples if brand[0]]

    form = EmptyForm() # For CSRF protection

    return render_template('select_for_rotation.html', 
                           title='Add Sneakers to Rotation', 
                           available_sneakers=available_sneakers,
                           form=form,
                           brands_for_filter=brands_for_filter,
                           current_sort_by=effective_sort_by,
                           current_order=effective_order,
                           sort_active_in_url=sort_active_in_url,
                           current_filter_brand=current_filter_brand,
                           current_search_term=current_search_term,
                           allowed_sort_fields=[
                               {"key": "id", "label": "Added", "default_order": "desc"},
                               {"key": "brand", "label": "Brand", "default_order": "asc"},
                               {"key": "model", "label": "Model", "default_order": "asc"},
                               {"key": "purchase_date", "label": "Purchase Date", "default_order": "desc"},
                               {"key": "last_worn_date", "label": "Last Worn", "default_order": "desc"},
                               {"key": "purchase_price", "label": "Purchase Price", "default_order": "desc"},
                               {"key": "resale_value", "label": "Resale Value", "default_order": "desc"},
                           ])

# --- V2 FEATURE: API ENDPOINT FOR SNEAKER SEARCH ---
@sneakers_bp.route('/api/search-sneakers')
@login_required
def search_sneakers():
    """
    Searches the local SneakerDB table and returns results as JSON.
    """
    search_query = request.args.get('q', '')
    if not search_query:
        return jsonify({'results': []}) # Return empty list if no query

    # Build a search pattern for a LIKE query
    search_pattern = f"%{search_query}%"

    # Query our local SneakerDB table, searching across multiple fields
    sneakers_found = SneakerDB.query.filter(
        or_(
            SneakerDB.model_name.ilike(search_pattern),
            SneakerDB.name.ilike(search_pattern),
            SneakerDB.brand.ilike(search_pattern),
            SneakerDB.sku.ilike(search_pattern)
        )
    ).limit(20).all() # Limit to 20 results for performance

    # Convert the sneaker objects into a list of dictionaries to be sent as JSON
    results = []
    for sneaker in sneakers_found:
        results.append({
            'name': sneaker.model_name or sneaker.name,
            'brand': sneaker.brand,
            'sku': sneaker.sku,
            'releaseDate': sneaker.release_date.strftime('%Y-%m-%d') if sneaker.release_date else None,
            'retailPrice': float(sneaker.retail_price) if sneaker.retail_price else None,
            'image': {
                'original': sneaker.image_url
            }
        })

    return jsonify({'results': results})


@sneakers_bp.route('/sneakers/db/search')
@login_required
def search_sneaker_db():
    query = request.args.get('q', '').strip()
    mode = request.args.get('mode', '').strip().lower()
    force_best = request.args.get('force_best', 'false').strip().lower() in ('1', 'true', 'yes')
    force_refresh = request.args.get('force_refresh', 'false').strip().lower() in ('1', 'true', 'yes')

    if not query:
        return jsonify({'status': 'error', 'message': 'Query is required.'}), 400

    api_key = current_app.config.get('KICKS_API_KEY')
    if not api_key:
        return jsonify({'status': 'error', 'message': 'KICKS_API_KEY is not configured.'}), 500

    client = KicksClient(
        api_key=api_key,
        base_url=current_app.config.get('KICKS_API_BASE_URL', 'https://api.kicks.dev'),
        logger=current_app.logger,
    )

    try:
        result = lookup_or_fetch_sneaker(
            query=query,
            db_session=db.session,
            client=client,
            max_age_hours=24,
            force_best=force_best,
            return_candidates=(mode == 'pick'),
            force_refresh=force_refresh,
        )
    except Exception as e:
        current_app.logger.error("KicksDB lookup failed for query '%s': %s", query, e)
        return jsonify({'status': 'error', 'message': 'External lookup failed.'}), 502

    status = result.get('status')
    if status in ('ok', 'pick'):
        return jsonify(result)
    if status == 'not_found':
        return jsonify(result), 404
    return jsonify(result), 400


@sneakers_bp.route('/api/sneaker-lookup')
@login_required
def sneaker_lookup():
    query = request.args.get('q', '').strip()
    limit = request.args.get('limit', '5').strip()
    force_best = request.args.get('force_best', 'false').strip().lower() in ('1', 'true', 'yes')
    force_refresh = request.args.get('force_refresh', 'false').strip().lower() in ('1', 'true', 'yes')
    mode = request.args.get('mode', 'lite').strip().lower()

    if not query:
        return jsonify({'message': 'Query is required.'}), 400

    try:
        limit_value = max(1, min(int(limit), 10))
    except ValueError:
        limit_value = 5

    api_key = current_app.config.get('KICKS_API_KEY')
    if not api_key:
        return jsonify({'message': 'KICKS_API_KEY is not configured.'}), 500

    request_id = uuid.uuid4().hex
    client = KicksClient(
        api_key=api_key,
        base_url=current_app.config.get('KICKS_API_BASE_URL', 'https://api.kicks.dev'),
        logger=current_app.logger,
    )

    try:
        result = lookup_or_fetch_sneaker(
            query=query,
            db_session=db.session,
            client=client,
            max_age_hours=24,
            force_best=force_best,
            return_candidates=True,
            mode=mode,
            force_refresh=force_refresh,
        )
    except KicksAPIError as e:
        current_app.logger.error("Sneaker lookup failed for '%s': %s", query, e)
        current_app.logger.info(
            "Sneaker lookup request_id=%s query=%s cache_status=error kicks_requests_used=%s endpoints=%s",
            request_id,
            query,
            client.request_count,
            client.endpoints_hit,
        )
        if e.status_code in (401, 403, 429):
            return jsonify({'message': 'KicksDB access denied or rate limited.'}), e.status_code
        return jsonify({'message': 'KicksDB error.'}), 503
    except Exception as e:
        current_app.logger.error("Sneaker lookup failed for '%s': %s", query, e)
        current_app.logger.info(
            "Sneaker lookup request_id=%s query=%s cache_status=error kicks_requests_used=%s endpoints=%s",
            request_id,
            query,
            client.request_count,
            client.endpoints_hit,
        )
        return jsonify({'message': 'External lookup failed.'}), 503

    current_app.logger.info(
        "Sneaker lookup request_id=%s query=%s cache_status=%s kicks_requests_used=%s endpoints=%s",
        request_id,
        query,
        result.get('cache_status', 'unknown'),
        client.request_count,
        client.endpoints_hit,
    )

    if result.get('status') == 'ok':
        return jsonify({
            'mode': 'single',
            'sneaker': result.get('sneaker'),
            'source': result.get('source'),
            'kicks_requests_used': client.request_count,
        })
    if result.get('status') == 'pick':
        candidates = result.get('candidates') or []
        return jsonify({
            'mode': 'pick',
            'candidates': candidates[:limit_value],
            'source': result.get('source'),
            'kicks_requests_used': client.request_count,
        })
    if result.get('status') == 'not_found':
        return jsonify({
            'mode': 'none',
            'message': result.get('message', 'No results found.'),
            'kicks_requests_used': client.request_count,
        }), 404
    return jsonify({'mode': 'error', 'message': result.get('message', 'Lookup failed.')}), 400
