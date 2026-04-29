# routes/main_routes.py
import requests
import re
import os
import uuid
from urllib.parse import urlparse
from collections import OrderedDict
from datetime import datetime, date, timedelta
from typing import Optional
from decimal import Decimal, InvalidOperation
import math
from flask import Blueprint, render_template, redirect, url_for, flash, request, send_from_directory, current_app, jsonify, abort, has_request_context, session
import io
from flask_login import login_required, current_user
from extensions import db
from models import Article, User, Sneaker, Release, SneakerDB, AffiliateOffer, ExchangeRate, ReleasePrice, ReleaseSalePoint, ReleaseMarketStats, ReleaseSizeBid, UserApiUsage, SneakerSale, wishlist_items, UserApiToken
from forms import EditProfileForm, ReleaseForm, ReleaseCsvImportForm, DeleteAllReleasesForm, EmptyForm, SneakerForm, FXRateForm, MobileTokenForm
from werkzeug.utils import secure_filename
from decorators import admin_required
from sqlalchemy import or_, asc, desc, extract, func
from sqlalchemy.orm import joinedload
from utils import allowed_file
from utils.sku import normalize_sku, sku_variants
from utils.money import convert_money, display_money, format_money
from utils.slugs import build_product_key, build_product_slug
from services.kicks_client import KicksClient, KicksAPIError
from services.sneaker_lookup_service import lookup_or_fetch_sneaker
from services.health_service import compute_health_components
from services.heat_service import compute_heat_for_release, should_recompute_heat
from services.api_tokens import create_token_for_user
from routes.sneakers_routes import _get_release_size_bids, _get_release_sales_series
from services.release_csv_import_service import (
    build_release_import_preview,
    apply_release_csv_import,
    RELEASE_CSV_HEADERS,
    _parse_time_value,
    _parse_retailer_links,
    _earliest_region_date,
    _earliest_region_date_from_row,
    _upsert_release_region,
    _upsert_release_price,
    _upsert_retailer_links,
    _upsert_affiliate_offer,
)
from services.release_display_service import build_release_display_map, resolve_release_display
from services.release_detail_service import build_release_detail_extras, find_matching_sneaker_record
from services.supabase_auth_linkage import find_app_user_by_supabase_id
from services.supabase_auth_service import (
    SupabaseAuthError,
    looks_like_jwt,
    verify_access_token,
)
import csv


main_bp = Blueprint('main', __name__)


@main_bp.route('/admin/auth/probe', methods=['GET'])
@login_required
@admin_required
def admin_auth_probe():
    """Phase 2 admin-only probe for the Supabase Auth verification path.

    Read-only sanity check that the resolver's Supabase JWT branch works
    against real production data. Returns 404 unless
    SUPABASE_AUTH_ENABLED is True. Never writes a row, never creates a
    Flask-Login session, never auto-links.

    Behaviour:
        - 404 when SUPABASE_AUTH_ENABLED is False (defends against the
          accidental probe of an unconfigured environment).
        - 401 from @login_required when the caller is not Flask-Login
          authenticated.
        - 403 from @admin_required when the caller is not an admin.
        - 200 with via=flask_login when no Authorization bearer is sent.
        - 200 with via=supabase + ok=true when a valid JWT for a linked
          app user is presented.
        - 200 with via=supabase + ok=false when the JWT is valid but its
          identity is not linked to any app user.
        - 401 when the JWT itself fails verification.

    The endpoint is wrapped in @login_required so an admin must already
    be Flask-Login authenticated to reach it. The Supabase JWT branch is
    exercised independently of that session, so the probe truly tests
    the JWT path and not the Flask-Login session lookup.
    """
    if not current_app.config.get('SUPABASE_AUTH_ENABLED'):
        abort(404)

    auth_header = request.headers.get('Authorization', '')
    if not auth_header.lower().startswith('bearer '):
        return jsonify({
            'ok': True,
            'via': 'flask_login',
            'user_id': current_user.id,
            'is_admin': bool(current_user.is_admin),
            'supabase_user_id': None,
        })

    token = auth_header.split(None, 1)[1].strip()
    if not looks_like_jwt(token):
        return jsonify({
            'ok': False,
            'via': 'supabase',
            'error': 'bearer value is not a JWT',
            'supabase_user_id': None,
        }), 400

    try:
        claims = verify_access_token(token)
    except SupabaseAuthError as exc:
        return jsonify({
            'ok': False,
            'via': 'supabase',
            'error': str(exc),
            'supabase_user_id': None,
        }), 401

    linked = find_app_user_by_supabase_id(claims.supabase_user_id)
    if linked is None:
        return jsonify({
            'ok': False,
            'via': 'supabase',
            'error': 'JWT identity is not linked to any app user',
            'supabase_user_id': claims.supabase_user_id,
        })

    return jsonify({
        'ok': True,
        'via': 'supabase',
        'user_id': linked.id,
        'is_admin': bool(linked.is_admin),
        'supabase_user_id': claims.supabase_user_id,
    })


def _format_month_filter_choices(distinct_months_tuples):
    months_for_filter = []
    for year, month in distinct_months_tuples:
        year = int(year)
        month = int(month)
        date_obj = datetime(year, month, 1)
        months_for_filter.append((f"{year}-{month:02d}", date_obj.strftime('%B %Y')))
    return months_for_filter


def _ensure_heat_for_releases(releases):
    if not releases:
        return
    now = datetime.utcnow()
    needs_commit = False
    for release in releases:
        if release.heat_score is None or should_recompute_heat(release, now):
            compute_heat_for_release(db.session, release, now=now)
            needs_commit = True
    if needs_commit:
        db.session.commit()


def _average_resale(offers, preferred_currency: str):
    aftermarket = [offer for offer in offers if _is_aftermarket_offer(offer) and offer.price is not None]
    if not aftermarket:
        return None, None

    normalized = []
    for offer in aftermarket:
        currency = offer.currency or "USD"
        normalized.append((offer.price, currency))
    matching = [item for item in normalized if item[1] == preferred_currency]
    selected = matching if matching else normalized
    if not selected:
        return None, None
    currency = selected[0][1]
    prices = [price for price, item_currency in selected if item_currency == currency]
    if not prices:
        return None, None
    avg = sum(prices) / len(prices)
    return avg, currency


def _normalize_sku_value(value: str) -> str:
    return normalize_sku(value) or ""


def _parse_sale_timestamp(value: str) -> Optional[datetime]:
    if not value:
        return None
    raw = value.rstrip("Z")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _average_sale_price_from_sales(sales, max_days: int = 30) -> Optional[Decimal]:
    if not sales:
        return None
    cutoff = datetime.utcnow() - timedelta(days=max_days)
    recent_amounts = []
    all_amounts = []
    for sale in sales:
        if not isinstance(sale, dict):
            continue
        amount = _to_decimal(sale.get("amount"))
        if amount is None:
            continue
        all_amounts.append(amount)
        created_at = _parse_sale_timestamp(sale.get("created_at"))
        if created_at and created_at >= cutoff:
            recent_amounts.append(amount)
    amounts = recent_amounts or all_amounts
    if not amounts:
        return None
    return sum(amounts) / len(amounts)


def _extract_goat_id_or_slug(offer: AffiliateOffer, release: Release) -> Optional[str]:
    if release.source == "kicksdb_goat":
        return release.source_product_id or release.source_slug
    if offer and offer.base_url and "goat.com" in offer.base_url:
        try:
            path = urlparse(offer.base_url).path.strip("/")
            if path:
                return path.split("/")[-1]
        except ValueError:
            pass
    return None


def _extract_stockx_id_or_slug(offer: AffiliateOffer, release: Release) -> Optional[str]:
    if release.source == "kicksdb_stockx":
        return release.source_product_id or release.source_slug
    if offer and offer.base_url and "stockx.com" in offer.base_url:
        try:
            path = urlparse(offer.base_url).path.strip("/")
            if path:
                return path.split("/")[-1]
        except ValueError:
            pass
    return None


def _normalized_retailer(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _is_aftermarket_offer(offer: AffiliateOffer) -> bool:
    offer_type = (getattr(offer, "offer_type", None) or "").strip().lower()
    retailer = _normalized_retailer(getattr(offer, "retailer", None))
    if offer_type == "aftermarket":
        return True
    # Backward compatibility for legacy/imported rows where offer_type is blank.
    return not offer_type and retailer in {"stockx", "goat"}


def _resolve_goat_id_by_sku(client: KicksClient, sku: str) -> Optional[dict]:
    if not sku:
        return None
    data = client.search_goat(sku)
    items = data.get("results") or data.get("data") or []
    normalized = normalize_sku(sku)
    for item in items:
        if not isinstance(item, dict):
            continue
        item_sku = item.get("sku") or item.get("style_code")
        if normalized and normalize_sku(item_sku) == normalized:
            return item
    return items[0] if items else None


def _resolve_stockx_id_by_sku(client: KicksClient, sku: str) -> Optional[dict]:
    if not sku:
        return None
    data = client.search_stockx(sku)
    items = data.get("results") or data.get("data") or []
    normalized = normalize_sku(sku)
    for item in items:
        if not isinstance(item, dict):
            continue
        item_sku = item.get("sku") or item.get("style_code")
        if normalized and normalize_sku(item_sku) == normalized:
            return item
    return items[0] if items else None


def _sum_resale_value_for_sneakers(sneakers, release_by_sku, preferred_currency: str):
    total = Decimal("0")
    counted = 0
    is_estimate = False
    for sneaker in sneakers:
        sku_key = _normalize_sku_value(sneaker.sku)
        release = release_by_sku.get(sku_key) if sku_key else None
        avg_price = None
        avg_currency = None
        if release:
            avg_price, avg_currency = _average_resale(release.offers, preferred_currency)
        if avg_price is None or not avg_currency:
            if sneaker.purchase_price is None:
                continue
            avg_price = sneaker.purchase_price
            avg_currency = sneaker.price_paid_currency or sneaker.purchase_currency or preferred_currency
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
    return (total, is_estimate) if counted else (None, False)


def _needs_resale_refresh(offers) -> bool:
    if not offers:
        return True
    current_month = datetime.utcnow().strftime("%Y-%m")
    for offer in offers:
        if not _is_aftermarket_offer(offer):
            continue
        if offer.price is None:
            return True
        if not offer.last_checked_at or offer.last_checked_at.strftime("%Y-%m") != current_month:
            return True
    return False


def _needs_size_bid_refresh(release) -> bool:
    if not release:
        return False
    if not current_app.config.get("KICKS_API_KEY"):
        return False
    if not (release.source_product_id or release.source_slug or release.sku):
        return False

    now = datetime.utcnow()
    bids_exist = (
        db.session.query(ReleaseSizeBid.id)
        .filter(ReleaseSizeBid.release_id == release.id)
        .first()
        is not None
    )
    if release.size_bids_last_fetched_at and release.size_bids_last_fetched_at >= now - timedelta(days=5):
        return not bids_exist
    return True


def _serialize_size_bid_series(size_bids, preferred_currency: str):
    size_bid_series = []
    for bid in size_bids:
        value = bid.highest_bid
        currency = bid.currency
        if preferred_currency and currency and currency != preferred_currency:
            converted = convert_money(db.session, value, currency, preferred_currency)
            if converted is not None:
                value = converted
                currency = preferred_currency
        size_bid_series.append(
            {
                "label": bid.size_label,
                "size_type": bid.size_type,
                "value": float(value),
                "currency": currency,
                "price_type": bid.price_type,
            }
        )
    size_type_options = sorted(
        {item["size_type"] for item in size_bid_series if item.get("size_type")}
    )
    size_type_default = size_type_options[0] if size_type_options else None
    return size_bid_series, size_type_options, size_type_default


def _check_and_increment_usage(user_id: int, action: str, max_per_day: int) -> bool:
    today = date.today()
    usage = (
        db.session.query(UserApiUsage)
        .filter_by(user_id=user_id, action=action, usage_date=today)
        .first()
    )
    if usage and usage.count >= max_per_day:
        return False
    if not usage:
        usage = UserApiUsage(user_id=user_id, action=action, usage_date=today, count=0)
        db.session.add(usage)
    usage.count += 1
    db.session.commit()
    return True


def _refresh_resale_for_release(release: Release, max_per_day: int = 3, force_refresh: bool = False) -> bool:
    if not force_refresh and not _needs_resale_refresh(release.offers):
        return False
    if has_request_context():
        if current_user.is_authenticated and not getattr(current_user, "is_admin", False):
            if not _check_and_increment_usage(current_user.id, "resale_refresh", max_per_day):
                return False

    if not release.offers:
        data = {}
        if release.source == "kicksdb_goat":
            data = {"goat_slug": release.source_slug, "goat_id": release.source_product_id}
        elif release.source == "kicksdb_stockx":
            data = {"stockx_slug": release.source_slug, "stockx_id": release.source_product_id}
        _ensure_offers_from_lookup(release, data)
        db.session.commit()

    api_key = current_app.config.get("KICKS_API_KEY")
    if not api_key:
        return False

    client = KicksClient(
        api_key=api_key,
        base_url=current_app.config.get("KICKS_API_BASE_URL", "https://api.kicks.dev"),
        logger=current_app.logger,
    )

    updated = False
    is_recent_release = False
    if release.release_date:
        is_recent_release = release.release_date >= (date.today() - timedelta(days=90))
    aftermarket_offers = [o for o in release.offers if _is_aftermarket_offer(o)]
    for offer in aftermarket_offers:
        if not (offer.offer_type or "").strip():
            offer.offer_type = "aftermarket"
            updated = True
    source_name = getattr(release, "source", None)
    preferred_market = "goat" if source_name == "kicksdb_goat" else "stockx" if source_name == "kicksdb_stockx" else None
    current_month = datetime.utcnow().strftime("%Y-%m")
    stockx_offer = next((o for o in aftermarket_offers if _normalized_retailer(o.retailer) == "stockx"), None)
    stockx_ready = (
        stockx_offer
        and stockx_offer.price is not None
        and stockx_offer.last_checked_at
        and stockx_offer.last_checked_at.strftime("%Y-%m") == current_month
    )
    stockx_fetched_this_run = False
    for offer in aftermarket_offers:
        retailer = _normalized_retailer(offer.retailer)
        try:
            if retailer == "stockx":
                id_or_slug = _extract_stockx_id_or_slug(offer, release)
                if not id_or_slug and release.sku:
                    match = _resolve_stockx_id_by_sku(client, release.sku)
                    if isinstance(match, dict):
                        id_or_slug = match.get("id") or match.get("slug")
                        matched_link = match.get("link")
                        if matched_link and (
                            not offer.base_url or "stockx.com" not in offer.base_url.lower()
                        ):
                            offer.base_url = matched_link
                if not id_or_slug:
                    continue
                detail = client.get_stockx_product(
                    id_or_slug,
                    include_variants=False,
                    include_traits=True,
                    include_market=True,
                    include_statistics=True,
                )
                normalized_detail = _normalize_kicks_detail(detail or {})
                updated = _update_release_from_detail(
                    release,
                    normalized_detail,
                    source_hint="stockx",
                ) or updated
                updated = _upsert_release_market_stats(
                    release,
                    normalized_detail,
                    raw_detail=detail,
                    source_label="stockx",
                ) or updated
                stockx_fetched_this_run = True
                price = _extract_stockx_resale_price(normalized_detail, prefer_short_window=is_recent_release)
                if price is None and is_recent_release:
                    try:
                        sales_payload = client.get_stockx_sales_history(
                            id_or_slug,
                            limit=100,
                            page=1,
                        )
                        sales = sales_payload.get("data") or []
                        price = _average_sale_price_from_sales(sales, max_days=30)
                    except KicksAPIError as exc:
                        current_app.logger.warning(
                            "Resale sales history fetch failed for release %s: %s",
                            release.id,
                            exc,
                        )
                if price is not None:
                    offer.price = price
                    offer.currency = offer.currency or "USD"
                    offer.last_checked_at = datetime.utcnow()
                    updated = True
            elif retailer == "goat":
                if preferred_market != "goat" and (stockx_ready or stockx_fetched_this_run):
                    continue
                id_or_slug = _extract_goat_id_or_slug(offer, release)
                if not id_or_slug and release.sku:
                    match = _resolve_goat_id_by_sku(client, release.sku)
                    if isinstance(match, dict):
                        id_or_slug = match.get("id") or match.get("slug")
                        offer.base_url = offer.base_url or match.get("link")
                        release.source_slug = release.source_slug or match.get("slug")
                        release.source_product_id = release.source_product_id or match.get("id")
                if not id_or_slug:
                    continue
                detail = client.get_goat_product(
                    id_or_slug,
                    include_variants=True,
                    include_traits=True,
                    include_statistics=True,
                    include_market=True,
                )
                normalized_detail = _normalize_kicks_detail(detail or {})
                updated = _update_release_from_detail(
                    release,
                    normalized_detail,
                    source_hint="goat",
                ) or updated
                updated = _upsert_release_market_stats(
                    release,
                    normalized_detail,
                    raw_detail=detail,
                    source_label="goat",
                ) or updated
                price = _extract_goat_resale_price(normalized_detail)
                if price is not None:
                    offer.price = price
                    offer.currency = offer.currency or "USD"
                    offer.last_checked_at = datetime.utcnow()
                    updated = True
        except KicksAPIError as exc:
            current_app.logger.warning("Resale refresh failed for release %s: %s", release.id, exc)
            continue
        except Exception as exc:
            current_app.logger.warning("Resale refresh failed for release %s: %s", release.id, exc)
            continue

    if updated:
        db.session.commit()
    return updated


def _update_release_from_detail(release: Release, detail: dict, source_hint: Optional[str] = None) -> bool:
    if not detail:
        return False
    changed = False
    source_hint_normalized = (source_hint or "").strip().lower()
    retail_price_value, retail_currency_value = _extract_retail_price_info(detail)
    for key, attr in (
        ("brand", "brand"),
        ("model_name", "model_name"),
        ("name", "name"),
        ("colorway", "colorway"),
        ("image_url", "image_url"),
        ("description", "description"),
    ):
        value = detail.get(key) or detail.get(attr)
        if getattr(release, attr, None) is None and value is not None:
            setattr(release, attr, value)
            changed = True

    if release.retail_price is None and retail_price_value is not None:
        release.retail_price = retail_price_value
        changed = True
        if retail_currency_value and release.retail_currency != retail_currency_value:
            release.retail_currency = retail_currency_value
            changed = True
    elif release.retail_currency is None and retail_currency_value:
        release.retail_currency = retail_currency_value
        changed = True

    stockx_product_id = detail.get("stockx_id")
    stockx_slug = detail.get("stockx_slug")
    goat_product_id = detail.get("goat_id")
    goat_slug = detail.get("goat_slug")

    # Generic id/slug belong to whichever detail endpoint returned this payload.
    if source_hint_normalized in {"stockx", "kicksdb_stockx"}:
        stockx_product_id = stockx_product_id or detail.get("id")
        stockx_slug = stockx_slug or detail.get("slug")
    elif source_hint_normalized in {"goat", "kicksdb_goat"}:
        goat_product_id = goat_product_id or detail.get("id")
        goat_slug = goat_slug or detail.get("slug")
    else:
        # Legacy fallback when source hint is not provided.
        if not stockx_product_id and not goat_product_id:
            goat_product_id = detail.get("id")
        if not stockx_slug and not goat_slug:
            goat_slug = detail.get("slug")

    stockx_has_identity = bool(stockx_product_id or stockx_slug)

    # Canonical source priority: StockX wins whenever a valid StockX identity is present.
    if stockx_has_identity and release.source != "kicksdb_stockx":
        can_promote = True
        if stockx_product_id:
            conflict = _find_release_identity_conflict(release, "kicksdb_stockx", str(stockx_product_id))
            can_promote = conflict is None
        if can_promote:
            release.source = "kicksdb_stockx"
            changed = True

    if release.source == "kicksdb_stockx":
        if stockx_product_id:
            normalized_stockx_id = str(stockx_product_id)
            if release.source_product_id is None:
                release.source_product_id = normalized_stockx_id
                changed = True
            elif str(release.source_product_id) != normalized_stockx_id:
                conflicting_release = _find_release_identity_conflict(release, "kicksdb_stockx", normalized_stockx_id)
                if conflicting_release is None:
                    release.source_product_id = normalized_stockx_id
                    changed = True
        if stockx_slug and release.source_slug != stockx_slug:
            release.source_slug = stockx_slug
            changed = True
    elif release.source in (None, "kicksdb_goat"):
        if release.source is None and (goat_product_id or goat_slug):
            release.source = "kicksdb_goat"
            changed = True
        if release.source == "kicksdb_goat":
            if goat_product_id:
                normalized_goat_id = str(goat_product_id)
                if release.source_product_id is None:
                    release.source_product_id = normalized_goat_id
                    changed = True
                elif str(release.source_product_id) != normalized_goat_id:
                    conflicting_release = _find_release_identity_conflict(release, "kicksdb_goat", normalized_goat_id)
                    if conflicting_release is None:
                        release.source_product_id = normalized_goat_id
                        changed = True
            if goat_slug and release.source_slug != goat_slug:
                release.source_slug = goat_slug
                changed = True

    traits = detail.get("traits") or detail.get("productTraits") or []
    release_date_value = detail.get("release_date") or detail.get("releaseDate")
    if not release_date_value:
        release_date_value = _extract_trait_value(traits, "Release Date")
    parsed_release_date = _parse_release_date_from_lookup(release_date_value)
    if parsed_release_date and (release.release_date is None or release.is_calendar_visible is False):
        release.release_date = parsed_release_date
        release.is_calendar_visible = parsed_release_date >= date.today()
        changed = True
    return changed


def _parse_release_date_from_lookup(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    if raw.isdigit() and len(raw) == 8:
        try:
            return datetime.strptime(raw, "%Y%m%d").date()
        except ValueError:
            return None
    if "-" in raw:
        parts = raw.split("-")
        if len(parts) == 3 and len(parts[0]) == 2 and len(parts[2]) == 4:
            try:
                return datetime.strptime(raw, "%d-%m-%Y").date()
            except ValueError:
                return None
    if "T" in raw:
        raw = raw.split("T", 1)[0]
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _to_decimal(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _normalize_currency_code(value) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip().upper()
    return cleaned if re.fullmatch(r"[A-Z]{3}", cleaned) else None


def _extract_numeric_value(value):
    parsed = _to_decimal(value)
    if parsed is not None:
        return parsed
    if isinstance(value, str):
        cleaned = re.sub(r"[^\d.\-]", "", value)
        if cleaned:
            return _to_decimal(cleaned)
    if isinstance(value, dict):
        for key in (
            "amount",
            "price",
            "value",
            "retail_price",
            "retailPrice",
            "lowest_ask",
            "lowestAsk",
            "ask",
            "min_price",
            "minPrice",
            "max_price",
            "maxPrice",
            "avg_price",
            "avgPrice",
            "average_price",
            "averagePrice",
            "count",
            "orders",
            "sales_count",
            "quantity",
            "qty",
            "total",
            "volume",
        ):
            if key in value:
                nested = _extract_numeric_value(value.get(key))
                if nested is not None:
                    return nested
        for nested_value in value.values():
            nested = _extract_numeric_value(nested_value)
            if nested is not None:
                return nested
    if isinstance(value, (list, tuple)):
        for item in value:
            nested = _extract_numeric_value(item)
            if nested is not None:
                return nested
    return None


def _coerce_variant_list(variants):
    if isinstance(variants, dict):
        return (
            variants.get("results")
            or variants.get("items")
            or variants.get("data")
            or variants.get("variants")
            or variants.get("sizes")
            or []
        )
    return variants if isinstance(variants, list) else []


def _iter_stat_sources(stats: dict) -> list:
    sources = []
    if isinstance(stats, dict):
        sources.append(stats)
        for key in (
            "market_stats",
            "marketStats",
            "marketStatistics",
            "market_statistics",
            "statistics",
            "stats",
        ):
            nested = stats.get(key)
            if isinstance(nested, dict):
                sources.append(nested)
        for key in (
            "3m",
            "3M",
            "last_90_days",
            "last_90_days_stats",
            "last_90_days_statistics",
            "90d",
            "1y",
            "1Y",
            "last_1y",
            "last_12_months",
            "annual",
            "year",
            "12m",
            "12M",
        ):
            nested = stats.get(key)
            if isinstance(nested, dict):
                sources.append(nested)
    return sources


def _extract_stat_value(stats: dict, market: dict, *keys):
    sources = _iter_stat_sources(stats)
    if isinstance(market, dict):
        sources.append(market)
    for source in sources:
        for key in keys:
            if key in source and source.get(key) is not None:
                return source.get(key)
    return None


def _extract_decimal_range(value):
    if value is None:
        return None, None
    if isinstance(value, dict):
        low = value.get("low") or value.get("min") or value.get("from")
        high = value.get("high") or value.get("max") or value.get("to")
        return _to_decimal(low), _to_decimal(high)
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return _to_decimal(value[0]), _to_decimal(value[1])
    if isinstance(value, (int, float, Decimal)):
        decimal_value = _to_decimal(value)
        return decimal_value, decimal_value
    if isinstance(value, str):
        matches = re.findall(r"[-+]?[0-9]*\\.?[0-9]+", value.replace(",", ""))
        if not matches:
            return None, None
        if len(matches) == 1:
            decimal_value = _to_decimal(matches[0])
            return decimal_value, decimal_value
        return _to_decimal(matches[0]), _to_decimal(matches[1])
    return None, None


def _extract_retail_price_info(detail: dict):
    price = _extract_numeric_value(
        detail.get("retail_price")
        or detail.get("retailPrice")
    )
    currency = (
        _normalize_currency_code(detail.get("retail_currency"))
        or _normalize_currency_code(detail.get("retailCurrency"))
        or _normalize_currency_code(detail.get("currency"))
    )
    if price is not None:
        return price, currency

    retail_prices = detail.get("retail_prices") or detail.get("retailPrices")
    if isinstance(retail_prices, dict):
        currency = (
            currency
            or _normalize_currency_code(retail_prices.get("currency"))
            or _normalize_currency_code(retail_prices.get("code"))
            or _normalize_currency_code(retail_prices.get("currency_code"))
        )
        price = _extract_numeric_value(
            retail_prices.get("amount")
            or retail_prices.get("price")
            or retail_prices.get("value")
            or retail_prices.get("retail_price")
        )
        if price is not None:
            return price, currency
        for key, value in retail_prices.items():
            numeric_value = _extract_numeric_value(value)
            if numeric_value is None:
                continue
            nested_currency = None
            if isinstance(value, dict):
                nested_currency = (
                    _normalize_currency_code(value.get("currency"))
                    or _normalize_currency_code(value.get("code"))
                )
            inferred_currency = (
                _normalize_currency_code(key)
                or nested_currency
                or currency
            )
            return numeric_value, inferred_currency
    elif isinstance(retail_prices, list):
        for entry in retail_prices:
            if not isinstance(entry, dict):
                continue
            entry_currency = (
                _normalize_currency_code(entry.get("currency"))
                or _normalize_currency_code(entry.get("code"))
                or _normalize_currency_code(entry.get("currency_code"))
            )
            price = _extract_numeric_value(
                entry.get("amount")
                or entry.get("price")
                or entry.get("value")
                or entry
            )
            if price is not None:
                return price, entry_currency or currency

    traits = detail.get("traits") or detail.get("productTraits") or []
    trait_price = (
        _extract_trait_value(traits, "Retail Price")
        or _extract_trait_value(traits, "Retail")
        or _extract_trait_value(traits, "MSRP")
    )
    if trait_price:
        parsed_price = _to_decimal(re.sub(r"[^\d.]", "", str(trait_price)))
        if parsed_price is not None:
            parsed_currency = currency
            raw_trait = str(trait_price)
            if "$" in raw_trait and not parsed_currency:
                parsed_currency = "USD"
            elif "£" in raw_trait and not parsed_currency:
                parsed_currency = "GBP"
            elif "€" in raw_trait and not parsed_currency:
                parsed_currency = "EUR"
            return parsed_price, parsed_currency
    return None, currency


def _extract_goat_sales_volume(detail: dict) -> Optional[int]:
    def _coerce_count(value):
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        if isinstance(value, str):
            cleaned = re.sub(r"[^\d-]", "", value)
            if not cleaned:
                return None
            try:
                return int(cleaned)
            except (TypeError, ValueError):
                return None
        return None

    def _sum_nested_counts(value):
        if isinstance(value, dict):
            keyed_total = 0
            keyed_found = False
            for key in (
                "orders",
                "order_count",
                "sales_count",
                "count",
                "quantity",
                "qty",
                "total",
                "volume",
                "weekly_orders",
                "weeklyOrders",
                "value",
            ):
                if key not in value:
                    continue
                nested_total, nested_found = _sum_nested_counts(value.get(key))
                if nested_found:
                    keyed_total += nested_total
                    keyed_found = True
            if keyed_found:
                return keyed_total, True
            total = 0
            found = False
            for nested in value.values():
                nested_total, nested_found = _sum_nested_counts(nested)
                if nested_found:
                    total += nested_total
                    found = True
            return total, found
        if isinstance(value, list):
            total = 0
            found = False
            for nested in value:
                nested_total, nested_found = _sum_nested_counts(nested)
                if nested_found:
                    total += nested_total
                    found = True
            return total, found
        parsed = _coerce_count(value)
        if parsed is None:
            return 0, False
        return parsed, True

    weekly_orders = detail.get("weekly_orders") or detail.get("weeklyOrders")
    if weekly_orders is None:
        return None
    total, found = _sum_nested_counts(weekly_orders)
    return total if found else None


def _extract_goat_variant_price_bounds(detail: dict):
    variants = detail.get("variants") or detail.get("sizes") or []
    prices = []
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        value = (
            variant.get("lowest_ask")
            or variant.get("lowestAsk")
            or variant.get("ask")
            or variant.get("price")
        )
        if value is None and isinstance(variant.get("prices"), dict):
            price_dict = variant.get("prices")
            value = (
                price_dict.get("ask")
                or price_dict.get("lowest_ask")
                or price_dict.get("lowestAsk")
                or price_dict.get("price")
            )
        parsed = _to_decimal(value)
        if parsed is not None:
            prices.append(parsed)
    if not prices:
        return None, None
    return min(prices), max(prices)


def _pick_dict(detail: dict, keys: tuple) -> dict:
    for key in keys:
        value = detail.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _merge_kicks_detail_container(primary: dict, container: dict) -> dict:
    merged = dict(primary)
    for key in (
        "market",
        "marketData",
        "market_data",
        "marketStats",
        "marketStatistics",
        "market_stats",
        "market_statistics",
        "statistics",
        "stats",
        "traits",
        "productTraits",
        "release_date",
        "releaseDate",
        "lowest_ask",
        "lowestAsk",
        "average_sale_price",
        "averageSalePrice",
        "avg_sale_price",
        "avgSalePrice",
        "average_price",
        "averagePrice",
        "variants",
        "productVariants",
        "sizes",
    ):
        if key in container and key not in merged:
            merged[key] = container[key]
    return merged


def _describe_kicks_detail_shape(detail: dict) -> dict:
    if not isinstance(detail, dict):
        return {"type": type(detail).__name__}

    shape = {"keys": sorted(detail.keys())}
    for key in (
        "data",
        "result",
        "product",
        "attributes",
        "market",
        "marketData",
        "market_data",
        "marketStats",
        "marketStatistics",
        "market_stats",
        "market_statistics",
        "statistics",
        "stats",
    ):
        value = detail.get(key)
        if isinstance(value, dict):
            shape[f"{key}_keys"] = sorted(value.keys())
    results = detail.get("results")
    if isinstance(results, list) and results:
        first = results[0]
        if isinstance(first, dict):
            shape["results_first_keys"] = sorted(first.keys())
            if isinstance(first.get("attributes"), dict):
                shape["results_first_attributes_keys"] = sorted(first["attributes"].keys())
            if isinstance(first.get("product"), dict):
                shape["results_first_product_keys"] = sorted(first["product"].keys())
    return shape


def _upsert_release_market_stats(
    release: Release,
    detail: dict,
    raw_detail: Optional[dict] = None,
    source_label: Optional[str] = None,
) -> bool:
    if not release or not isinstance(detail, dict):
        return False
    stats = _pick_dict(
        detail,
        (
            "statistics",
            "market_stats",
            "marketStats",
            "marketStatistics",
            "market_statistics",
            "stats",
        ),
    )
    market = _pick_dict(detail, ("market", "market_data", "marketData"))

    average_price_1m = _to_decimal(
        _extract_stat_value(
            stats,
            market,
            "last_30_days_average_price",
            "last_30_days_avg_price",
            "last_30_days_average_sale_price",
            "last_30_days_avg_sale_price",
            "last_30_days_average",
        )
    )
    average_price_3m = _to_decimal(
        _extract_stat_value(
            stats,
            market,
            "last_90_days_average_price",
            "last_90_days_avg_price",
            "average_sale_price",
            "averageSalePrice",
            "avg_sale_price",
            "avgSalePrice",
            "average_price",
            "averagePrice",
        )
    )
    average_price_1y = _to_decimal(
        _extract_stat_value(
            stats,
            market,
            "annual_average_price",
            "annualAveragePrice",
        )
    )
    if average_price_3m is None:
        average_price_3m = _to_decimal(
            detail.get("avg_price")
            or detail.get("avgPrice")
            or detail.get("average_price")
            or detail.get("averagePrice")
        )
    if average_price_1y is None:
        average_price_1y = _to_decimal(
            detail.get("avg_price")
            or detail.get("avgPrice")
            or detail.get("average_price")
            or detail.get("averagePrice")
        )

    volatility_raw = _extract_stat_value(
        stats,
        market,
        "volatility",
        "volatility_1y",
        "volatility_3m",
        "volatility_90d",
        "volatility_percent",
        "volatility_percentage",
        "volatilityPct",
        "annual_volatility",
        "annualVolatility",
    )
    volatility = None
    if volatility_raw is not None:
        try:
            volatility = float(volatility_raw)
        except (ValueError, TypeError):
            volatility = None

    price_range_value = _extract_stat_value(
        stats,
        market,
        "price_range",
        "priceRange",
        "price_range_usd",
        "priceRangeUsd",
    )
    price_range_low, price_range_high = _extract_decimal_range(price_range_value)
    if price_range_low is None and price_range_high is None:
        price_range_low = _to_decimal(
            _extract_stat_value(
                stats,
                market,
                "price_range_low",
                "priceRangeLow",
                "last_90_days_range_low",
            )
        )
        price_range_high = _to_decimal(
            _extract_stat_value(
                stats,
                market,
                "price_range_high",
                "priceRangeHigh",
                "last_90_days_range_high",
            )
        )
    if price_range_low is None and price_range_high is None:
        fallback_low = _to_decimal(detail.get("min_price") or detail.get("minPrice"))
        fallback_high = _to_decimal(detail.get("max_price") or detail.get("maxPrice"))
        if fallback_low is not None or fallback_high is not None:
            price_range_low = fallback_low
            price_range_high = fallback_high
    if price_range_low is None and price_range_high is None:
        price_range_low, price_range_high = _extract_goat_variant_price_bounds(detail)

    sales_price_range_value = _extract_stat_value(
        stats,
        market,
        "sales_price_range",
        "salesPriceRange",
        "sales_price_range_1y",
        "sales_price_range_3m",
        "sales_price_range_usd",
        "salesPriceRangeUsd",
    )
    sales_price_range_low, sales_price_range_high = _extract_decimal_range(sales_price_range_value)
    if sales_price_range_low is None and sales_price_range_high is None:
        sales_price_range_low = _to_decimal(
            _extract_stat_value(
                stats,
                market,
                "sales_price_range_low",
                "salesPriceRangeLow",
                "annual_range_low",
            )
        )
        sales_price_range_high = _to_decimal(
            _extract_stat_value(
                stats,
                market,
                "sales_price_range_high",
                "salesPriceRangeHigh",
                "annual_range_high",
            )
        )
    if sales_price_range_low is None and sales_price_range_high is None:
        fallback_low = _to_decimal(detail.get("min_price") or detail.get("minPrice"))
        fallback_high = _to_decimal(detail.get("max_price") or detail.get("maxPrice"))
        if fallback_low is not None or fallback_high is not None:
            sales_price_range_low = fallback_low
            sales_price_range_high = fallback_high
    if sales_price_range_low is None and sales_price_range_high is None:
        sales_price_range_low, sales_price_range_high = _extract_goat_variant_price_bounds(detail)

    sales_volume_value = _extract_stat_value(
        stats,
        market,
        "sales_volume",
        "salesVolume",
        "sales_count",
        "salesCount",
        "annual_sales_count",
        "last_90_days_sales_count",
    )
    sales_volume = None
    if sales_volume_value is not None:
        try:
            sales_volume = int(sales_volume_value)
        except (ValueError, TypeError):
            sales_volume = None
    if sales_volume is None and (source_label or getattr(release, "source", None)) in {"goat", "kicksdb_goat"}:
        sales_volume = _extract_goat_sales_volume(detail)

    gmv_value = _extract_stat_value(stats, market, "gmv", "GMV", "annual_total_dollars")
    gmv = _to_decimal(gmv_value) if gmv_value is not None else None

    source_default_currency = None
    if getattr(release, "source", None) in {"kicksdb_stockx", "kicksdb_goat"}:
        source_default_currency = "USD"

    currency = (
        _extract_stat_value(stats, market, "currency")
        or detail.get("currency")
        or market.get("currency")
        or source_default_currency
        or getattr(release, "retail_currency", None)
    )

    has_any = any(
        [
            average_price_1m is not None,
            average_price_3m is not None,
            average_price_1y is not None,
            volatility is not None,
            price_range_low is not None,
            price_range_high is not None,
            sales_price_range_low is not None,
            sales_price_range_high is not None,
            sales_volume is not None,
            gmv is not None,
        ]
    )
    if not has_any:
        stats_keys = list(stats.keys()) if isinstance(stats, dict) else []
        market_keys = list(market.keys()) if isinstance(market, dict) else []
        current_app.logger.info(
            "Market stats missing for release %s (source=%s). stats_keys=%s market_keys=%s raw_shape=%s normalized_shape=%s",
            release.id,
            source_label or getattr(release, "source", None),
            stats_keys,
            market_keys,
            _describe_kicks_detail_shape(raw_detail or {}),
            _describe_kicks_detail_shape(detail),
        )
        return False

    record = ReleaseMarketStats.query.filter_by(release_id=release.id).first()
    if not record:
        record = ReleaseMarketStats(release_id=release.id)
        db.session.add(record)

    changed = False
    for attr, value in (
        ("currency", currency),
        ("average_price_1m", average_price_1m),
        ("average_price_3m", average_price_3m),
        ("average_price_1y", average_price_1y),
        ("volatility", volatility),
        ("price_range_low", price_range_low),
        ("price_range_high", price_range_high),
        ("sales_price_range_low", sales_price_range_low),
        ("sales_price_range_high", sales_price_range_high),
        ("sales_volume", sales_volume),
        ("gmv", gmv),
    ):
        if value is not None and getattr(record, attr) != value:
            setattr(record, attr, value)
            changed = True
    return changed


def _extract_stockx_resale_price(detail: dict, prefer_short_window: bool = False):
    if not detail:
        return None
    market = detail.get("market") if isinstance(detail.get("market"), dict) else {}
    stats = detail.get("statistics") if isinstance(detail.get("statistics"), dict) else {}
    short_window = (
        stats.get("last_30_days_average_price")
        or stats.get("last_30_days_avg_price")
        or stats.get("last_30_days_average_sale_price")
        or stats.get("last_30_days_avg_sale_price")
        or stats.get("last_30_days_average")
        or market.get("last_30_days_average_price")
        or market.get("last_30_days_avg_price")
        or market.get("last_30_days_average_sale_price")
        or market.get("last_30_days_avg_sale_price")
        or market.get("last_30_days_average")
    )
    if prefer_short_window and short_window is not None:
        return _to_decimal(short_window)
    price = (
        stats.get("last_90_days_average_price")
        or stats.get("average_sale_price")
        or stats.get("averageSalePrice")
        or stats.get("avg_sale_price")
        or stats.get("avgSalePrice")
        or stats.get("average_price")
        or stats.get("averagePrice")
        or stats.get("annual_average_price")
        or detail.get("average_sale_price")
        or detail.get("averageSalePrice")
        or detail.get("avg_sale_price")
        or detail.get("avgSalePrice")
        or detail.get("average_price")
        or detail.get("averagePrice")
        or market.get("average_sale_price")
        or market.get("averageSalePrice")
        or market.get("avg_sale_price")
        or market.get("avgSalePrice")
        or market.get("average_price")
        or market.get("averagePrice")
    )
    return _to_decimal(price)


def _extract_goat_resale_price(detail: dict):
    if not detail:
        return None
    price = _extract_numeric_value(
        detail.get("min_price")
        or detail.get("minPrice")
        or detail.get("lowest_price")
        or detail.get("lowestPrice")
        or detail.get("average_sale_price")
        or detail.get("averageSalePrice")
        or detail.get("avg_sale_price")
        or detail.get("avgSalePrice")
        or detail.get("avg_price")
        or detail.get("avgPrice")
        or detail.get("average_price")
        or detail.get("averagePrice")
        or detail.get("lowest_ask")
        or detail.get("lowestAsk")
    )
    if price is None:
        variants = _coerce_variant_list(detail.get("variants") or detail.get("sizes") or [])
        candidate_prices = []
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            variant_price = _extract_numeric_value(
                variant.get("lowest_ask")
                or variant.get("lowestAsk")
                or variant.get("ask")
                or variant.get("price")
            )
            if variant_price is None and isinstance(variant.get("prices"), dict):
                prices = variant.get("prices")
                variant_price = _extract_numeric_value(
                    prices.get("ask")
                    or prices.get("lowest_ask")
                    or prices.get("lowestAsk")
                    or prices.get("price")
                )
            parsed = _extract_numeric_value(variant_price)
            if parsed is not None:
                candidate_prices.append(parsed)
        if candidate_prices:
            return min(candidate_prices)
    return _extract_numeric_value(price)


def _extract_trait_value(traits, trait_name: str):
    if isinstance(traits, dict):
        return traits.get(trait_name)
    if isinstance(traits, list):
        for trait in traits:
            name = trait.get("name") or trait.get("trait")
            if name and name.lower() == trait_name.lower():
                return trait.get("value") or trait.get("displayValue")
    return None


def _normalize_kicks_detail(detail: dict) -> dict:
    if not isinstance(detail, dict):
        return {}
    if isinstance(detail.get("data"), dict):
        data = detail["data"]
        if isinstance(data.get("product"), dict):
            return _merge_kicks_detail_container(data["product"], data)
        if isinstance(data.get("attributes"), dict):
            return _merge_kicks_detail_container(data["attributes"], data)
        detail = data
    if isinstance(detail.get("product"), dict):
        return _merge_kicks_detail_container(detail["product"], detail)
    if isinstance(detail.get("attributes"), dict):
        return _merge_kicks_detail_container(detail["attributes"], detail)
    if isinstance(detail.get("result"), dict):
        detail = detail["result"]
        if isinstance(detail.get("product"), dict):
            return _merge_kicks_detail_container(detail["product"], detail)
        if isinstance(detail.get("attributes"), dict):
            return _merge_kicks_detail_container(detail["attributes"], detail)
    if isinstance(detail.get("results"), list) and detail["results"]:
        first = detail["results"][0]
        if isinstance(first, dict):
            if isinstance(first.get("product"), dict):
                return _merge_kicks_detail_container(first["product"], first)
            if isinstance(first.get("attributes"), dict):
                return _merge_kicks_detail_container(first["attributes"], first)
            detail = first
    return detail


def _ensure_offers_from_lookup(release: Release, data: dict) -> None:
    if not release:
        return
    stockx_slug = data.get("stockx_slug")
    stockx_id = data.get("stockx_id")
    goat_slug = data.get("goat_slug")
    goat_id = data.get("goat_id")
    stockx_price = data.get("current_lowest_ask_stockx")
    goat_price = data.get("current_lowest_ask_goat")

    if stockx_slug or stockx_id:
        base_url = f"https://stockx.com/{stockx_slug or stockx_id}"
        offer = (
            db.session.query(AffiliateOffer)
            .filter_by(release_id=release.id, retailer="stockx", region=None)
            .first()
        )
        if not offer:
            offer = AffiliateOffer(
                release_id=release.id,
                retailer="stockx",
                region=None,
                base_url=base_url,
                offer_type="aftermarket",
                priority=50,
                is_active=True,
            )
            db.session.add(offer)
        else:
            offer.base_url = base_url
        if offer.price is None and stockx_price is not None:
            offer.price = _to_decimal(stockx_price)
            offer.currency = offer.currency or "USD"

    if goat_slug or goat_id:
        base_url = f"https://www.goat.com/sneakers/{goat_slug or goat_id}"
        offer = (
            db.session.query(AffiliateOffer)
            .filter_by(release_id=release.id, retailer="goat", region=None)
            .first()
        )
        if not offer:
            offer = AffiliateOffer(
                release_id=release.id,
                retailer="goat",
                region=None,
                base_url=base_url,
                offer_type="aftermarket",
                priority=60,
                is_active=True,
            )
            db.session.add(offer)
        else:
            offer.base_url = base_url
        if offer.price is None and goat_price is not None:
            offer.price = _to_decimal(goat_price)
            offer.currency = offer.currency or "USD"

def _is_auction_slug(slug: Optional[str]) -> bool:
    if not slug:
        return False
    return "-auction" in slug.lower()


def _release_from_lookup_identity(data: dict) -> tuple[Optional[str], Optional[str], Optional[str]]:
    source = None
    source_product_id = None
    source_slug = None
    if data.get("stockx_id") or data.get("stockx_slug"):
        source = "kicksdb_stockx"
        source_product_id = data.get("stockx_id")
        source_slug = data.get("stockx_slug")
    elif data.get("goat_id") or data.get("goat_slug"):
        source = "kicksdb_goat"
        source_product_id = data.get("goat_id")
        source_slug = data.get("goat_slug")
    return source, source_product_id, source_slug


def _find_release_identity_conflict(
    current_release: Optional[Release],
    source: Optional[str],
    source_product_id: Optional[str],
) -> Optional[Release]:
    if not source or not source_product_id:
        return None
    conflict_query = Release.query.filter_by(source=source, source_product_id=source_product_id)
    if current_release is not None and current_release.id is not None:
        conflict_query = conflict_query.filter(Release.id != current_release.id)
    return conflict_query.first()


def _apply_lookup_data_to_release(release: Release, data: dict) -> Release:
    source, source_product_id, source_slug = _release_from_lookup_identity(data)
    conflicting_release = _find_release_identity_conflict(release, source, source_product_id)
    if conflicting_release:
        _ensure_offers_from_lookup(conflicting_release, data)
        db.session.commit()
        return conflicting_release

    release.name = data.get('model_name') or data.get('name') or release.name
    release.model_name = data.get('model_name') or release.model_name
    release.brand = data.get('brand') or release.brand
    release.colorway = data.get('colorway') or release.colorway
    release.image_url = data.get('image_url') or release.image_url
    release.retail_price = data.get('retail_price') or release.retail_price
    release.retail_currency = data.get('retail_currency') or release.retail_currency
    if source:
        release.source = source
    release.source_product_id = source_product_id or release.source_product_id
    release.source_slug = source_slug or release.source_slug
    db.session.commit()
    return release

def _ensure_release_for_sku_with_resale(query: str) -> Optional[Release]:
    if not query:
        return None
    sku_filters = [Release.sku.ilike(value) for value in sku_variants(query)]
    if not sku_filters:
        sku_filters = [Release.sku.ilike(query)]
    release = Release.query.filter(or_(*sku_filters)).first()
    if not release:
        api_key = current_app.config.get('KICKS_API_KEY')
        if not api_key:
            return None

        client = KicksClient(
            api_key=api_key,
            base_url=current_app.config.get('KICKS_API_BASE_URL', 'https://api.kicks.dev'),
            logger=current_app.logger,
        )
        try:
            lookup_query = normalize_sku(query) or query
            result = lookup_or_fetch_sneaker(
                query=lookup_query,
                db_session=db.session,
                client=client,
                max_age_hours=24,
                force_best=True,
                return_candidates=False,
                mode="lite",
            )
        except Exception as exc:
            current_app.logger.warning("Resale lookup failed for '%s': %s", query, exc)
            return None

        if result.get('status') != 'ok' or not result.get('sneaker'):
            return None

        data = result.get('sneaker')
        source, source_product_id, _source_slug = _release_from_lookup_identity(data)
        existing_release = _find_release_identity_conflict(None, source, source_product_id)
        if existing_release:
            _ensure_offers_from_lookup(existing_release, data)
            db.session.commit()
            _refresh_resale_for_release(existing_release)
            return existing_release
        sku = data.get('sku')
        release_date = _parse_release_date_from_lookup(data.get('release_date'))
        release_visible = True
        if not release_date:
            release_date = date.today()
            release_visible = False
        if isinstance(release_date, date) and release_date < date.today():
            release_visible = False

        release = Release(
            sku=sku,
            name=data.get('model_name') or data.get('name') or sku or 'Unknown',
            model_name=data.get('model_name'),
            brand=data.get('brand'),
            colorway=data.get('colorway'),
            image_url=data.get('image_url'),
            retail_price=data.get('retail_price'),
            retail_currency=data.get('retail_currency') or "USD",
            release_date=release_date,
            source=source or "lookup",
            source_product_id=source_product_id,
            source_slug=data.get('stockx_slug') or data.get('goat_slug'),
            is_calendar_visible=release_visible,
        )
        db.session.add(release)
        db.session.commit()
        _ensure_offers_from_lookup(release, data)
        db.session.commit()
        _refresh_resale_for_release(release)
        return release

    data = {}
    if not release.source or not release.source_slug or not release.source_product_id:
        api_key = current_app.config.get('KICKS_API_KEY')
        if api_key:
            client = KicksClient(
                api_key=api_key,
                base_url=current_app.config.get('KICKS_API_BASE_URL', 'https://api.kicks.dev'),
                logger=current_app.logger,
            )
            try:
                result = lookup_or_fetch_sneaker(
                    query=normalize_sku(query) or query,
                    db_session=db.session,
                    client=client,
                    max_age_hours=24,
                    force_best=True,
                    return_candidates=False,
                    mode="lite",
                )
            except Exception as exc:
                current_app.logger.warning("Resale lookup failed for '%s': %s", query, exc)
                result = None

            if result and result.get('status') == 'ok' and result.get('sneaker'):
                data = result.get('sneaker')
                release = _apply_lookup_data_to_release(release, data)

    if _is_auction_slug(release.source_slug):
        api_key = current_app.config.get('KICKS_API_KEY')
        if api_key:
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
                    force_best=True,
                    return_candidates=False,
                    mode="lite",
                )
            except Exception as exc:
                current_app.logger.warning("Resale lookup failed for '%s': %s", query, exc)
                result = None

            if result and result.get('status') == 'ok' and result.get('sneaker'):
                data = result.get('sneaker')
                release = _apply_lookup_data_to_release(release, data)

    if not data:
        if release.source == "kicksdb_goat":
            data = {"goat_slug": release.source_slug, "goat_id": release.source_product_id}
        elif release.source == "kicksdb_stockx":
            data = {"stockx_slug": release.source_slug, "stockx_id": release.source_product_id}
    _ensure_offers_from_lookup(release, data)
    db.session.commit()
    _refresh_resale_for_release(release)
    return release

# Home Route

@main_bp.route('/')
def home():
    # Initialize all lists and default stats
    recent_sneakers, upcoming_wishlist, rotation_sneakers = [], [], []
    stats = {
        "overall_total_count": 0,
        "total_brands": 0,
        "total_value": 0.0,
        "total_resale_value": None,
        "total_resale_is_estimate": False,
        "total_resale_delta": None,
        "most_owned_brand": "N/A",
        "in_rotation_count": 0,
    }

    # --- THIS QUERY NOW RUNS FOR ALL VISITORS ---
    today = date.today()
    general_releases = Release.query.filter(Release.release_date >= today, Release.is_calendar_visible.is_(True)) \
                            .order_by(Release.release_date.asc()) \
                            .limit(4).all()
    latest_articles = (
        Article.query.filter(Article.published_at.isnot(None))
        .filter(Article.published_at <= datetime.utcnow())
        .order_by(Article.published_at.desc())
        .limit(3)
        .all()
    )

    if current_user.is_authenticated:
        # --- These queries ONLY run for logged-in users ---
        base_query = Sneaker.query.filter_by(user_id=current_user.id)

        # Content for homepage sections
        recent_sneakers = base_query.order_by(Sneaker.id.desc()).limit(4).all()
        upcoming_wishlist = Release.query.join(wishlist_items).filter(
            wishlist_items.c.user_id == current_user.id,
            Release.release_date >= today,
            Release.is_calendar_visible.is_(True)
        ).order_by(Release.release_date.asc()).limit(4).all()
        rotation_sneakers = base_query.filter_by(in_rotation=True).order_by(Sneaker.last_worn_date.asc().nullsfirst()).limit(4).all()
        status_map = {}
        for sneaker in recent_sneakers + rotation_sneakers:
            health_components = compute_health_components(
                sneaker=sneaker,
                user_id=current_user.id,
                materials=[],
                include_confidence=False,
            )
            status_map[sneaker.id] = health_components["status_label"]

        # Stats for stat cards
        stats["overall_total_count"] = base_query.count()
        stats["in_rotation_count"] = base_query.filter_by(in_rotation=True).count()
        stats["total_value"] = float(base_query.with_entities(func.sum(Sneaker.purchase_price)).scalar() or 0.0)
        stats["total_brands"] = (
            base_query.with_entities(func.count(func.distinct(Sneaker.brand)))
            .filter(Sneaker.brand.isnot(None))
            .scalar()
            or 0
        )
        preferred_currency = current_user.preferred_currency or "GBP"
        sneakers_list = base_query.all()
        skus = [_normalize_sku_value(sneaker.sku) for sneaker in sneakers_list if sneaker.sku]
        release_by_sku = {}
        if skus:
            skus_for_query = []
            for sku in skus:
                skus_for_query.extend(list(sku_variants(sku)))
            releases = (
                db.session.query(Release)
                .options(joinedload(Release.offers))
                .filter(func.upper(Release.sku).in_(skus_for_query))
                .all()
            )
            release_by_sku = {
                _normalize_sku_value(release.sku): release for release in releases if release.sku
            }
        stats["total_resale_value"], stats["total_resale_is_estimate"] = _sum_resale_value_for_sneakers(
            sneakers_list, release_by_sku, preferred_currency
        )
        if stats["total_resale_value"] is not None:
            stats["total_resale_delta"] = stats["total_resale_value"] - Decimal(str(stats["total_value"]))
        brand_dist = base_query.with_entities(Sneaker.brand, func.count(Sneaker.brand)).filter(Sneaker.brand.isnot(None)).group_by(Sneaker.brand).order_by(func.count(Sneaker.brand).desc()).first()
        if brand_dist:
            stats["most_owned_brand"] = brand_dist[0]

    form = EmptyForm()

    return render_template('home.html', 
                        recent_sneakers=recent_sneakers,
                        upcoming_wishlist=upcoming_wishlist,
                        rotation_sneakers=rotation_sneakers,
                        general_releases=general_releases,
                        latest_articles=latest_articles,
                        stats=stats,
                        status_map=status_map if current_user.is_authenticated else {},
                        form_for_modal=form)

# Profile Route

@main_bp.route('/profile')
@login_required
def profile():
    form = EditProfileForm()
    form.username.data = current_user.username
    form.first_name.data = current_user.first_name
    form.last_name.data = current_user.last_name
    form.email.data = current_user.pending_email or current_user.email
    form.marketing_opt_in.data = current_user.marketing_opt_in
    form.preferred_currency.data = current_user.preferred_currency or "GBP"
    form.preferred_region.data = current_user.preferred_region or "UK"
    token_form = MobileTokenForm()
    revoke_form = EmptyForm()
    tokens = (
        UserApiToken.query.filter_by(user_id=current_user.id)
        .order_by(UserApiToken.revoked_at.asc().nullsfirst(), UserApiToken.created_at.desc())
        .all()
    )
    plaintext_token = session.pop("mobile_token_plaintext", None)
    return render_template(
        'profile.html',
        title='Your Profile',
        form=form,
        token_form=token_form,
        revoke_form=revoke_form,
        api_tokens=tokens,
        plaintext_token=plaintext_token,
    )


@main_bp.route('/profile/tokens/create', methods=['POST'])
@login_required
def create_mobile_token():
    form = MobileTokenForm()
    if not form.validate_on_submit():
        flash('Unable to create token. Please try again.', 'danger')
        return redirect(url_for('main.profile'))

    token, plaintext = create_token_for_user(current_user, name=form.name.data)
    session["mobile_token_plaintext"] = plaintext
    flash('Mobile token created. Copy it now — you will not see it again.', 'success')
    return redirect(url_for('main.profile'))


@main_bp.route('/profile/tokens/<int:token_id>/revoke', methods=['POST'])
@login_required
def revoke_mobile_token(token_id):
    form = EmptyForm()
    if not form.validate_on_submit():
        flash('Invalid request.', 'danger')
        return redirect(url_for('main.profile'))

    token = UserApiToken.query.filter_by(id=token_id, user_id=current_user.id).first_or_404()
    if token.revoked_at:
        flash('Token already revoked.', 'info')
        return redirect(url_for('main.profile'))

    token.revoked_at = datetime.utcnow()
    db.session.commit()
    flash('Token revoked.', 'success')
    return redirect(url_for('main.profile'))

# Edit Profile Route

@main_bp.route('/edit-profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    form = EditProfileForm()
    if form.validate_on_submit():
        new_email = form.email.data.lower()
        email_changed = (new_email != current_user.email.lower())
        can_proceed_with_update = True
        email_update_pending = False

        if email_changed:
            existing_user_by_email = User.query.filter(User.email == new_email, User.id != current_user.id).first()
            other_user_pending_this_email = User.query.filter(User.pending_email == new_email, User.id != current_user.id).first()
            if existing_user_by_email or other_user_pending_this_email:
                form.email.errors.append('That email address is already in use or pending confirmation by another account.')
                can_proceed_with_update = False
            else:
                from .auth_routes import send_confirm_new_email_address_email
                current_user.pending_email = new_email
                send_confirm_new_email_address_email(current_user, new_email)
                email_update_pending = True
        
        if can_proceed_with_update:
            current_user.username = form.username.data.strip()
            current_user.first_name = form.first_name.data.strip()
            current_user.last_name = form.last_name.data.strip()
            current_user.marketing_opt_in = form.marketing_opt_in.data
            current_user.preferred_currency = form.preferred_currency.data or current_user.preferred_currency or "GBP"
            current_user.preferred_region = form.preferred_region.data or current_user.preferred_region or "UK"
            try:
                db.session.commit()
                if email_update_pending:
                    flash('Your profile details have been updated. A confirmation link has been sent to your new email address to complete the change.', 'info')
                else:
                    flash('Your profile has been updated successfully!', 'success')
                return redirect(url_for('main.profile'))
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Error updating profile for user {current_user.id}: {e}")
                flash('Error updating profile. Please try again.', 'danger')
    
    elif request.method == 'GET':
        form.username.data = current_user.username
        form.first_name.data = current_user.first_name
        form.last_name.data = current_user.last_name
        form.email.data = current_user.pending_email or current_user.email
        form.marketing_opt_in.data = current_user.marketing_opt_in
        form.preferred_currency.data = current_user.preferred_currency or "GBP"
        form.preferred_region.data = current_user.preferred_region or "UK"
    
    return render_template('edit_profile.html', title='Edit Your Profile', form=form)

# Upload Image Route

@main_bp.route('/uploads/<path:filename>')
def uploaded_file(filename):
    # Use current_app to access config['UPLOAD_FOLDER'] safely from within blueprint
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)

# Release Calendar Route

@main_bp.route('/release-calendar')
def release_calendar():
    """Displays upcoming sneaker releases from our own database, with filtering and searching."""
    form = EmptyForm()
    delete_all_form = DeleteAllReleasesForm()
    today = date.today()
    page = request.args.get('page', default=1, type=int)
    per_page = 40

    # Get filter/search parameters from the URL
    filter_brand_param = request.args.get('filter_brand')
    filter_month_param = request.args.get('filter_month')
    search_term_param = request.args.get('search_term')

    # Base query: all releases from today onwards
    query = Release.query.options(
        joinedload(Release.offers),
        joinedload(Release.prices),
        joinedload(Release.regions),
        joinedload(Release.market_stats),
    ).filter(
        Release.release_date >= today,
        Release.is_calendar_visible.is_(True)
    )

    # Get distinct brands and months for the filter dropdowns BEFORE filtering the main query
    distinct_brands_tuples = query.with_entities(Release.brand).distinct().order_by(Release.brand).all()
    brands_for_filter = [brand[0] for brand in distinct_brands_tuples if brand[0]]

    distinct_months_tuples = db.session.query(
        extract('year', Release.release_date), 
        extract('month', Release.release_date)
    ).filter(Release.release_date >= today, Release.is_calendar_visible.is_(True)).distinct().order_by(
        extract('year', Release.release_date), 
        extract('month', Release.release_date)
    ).all()
    
    # Format months as "YYYY-MM" for the dropdown value and "Month Year" for the display
    months_for_filter = []
    for year, month in distinct_months_tuples:
        # Explicitly convert to integers to handle database differences
        year = int(year)
        month = int(month)
        # Now the formatting will work correctly
        date_obj = datetime(year, month, 1)
        display_text = date_obj.strftime('%B %Y')
        value_text = f"{year}-{month:02d}"
        months_for_filter.append((value_text, display_text))

    # Apply filters to the main query
    current_filter_brand = None
    if filter_brand_param and filter_brand_param.lower() != 'all':
        current_filter_brand = filter_brand_param
        query = query.filter(Release.brand == current_filter_brand)

    current_filter_month = None
    if filter_month_param and filter_month_param.lower() != 'all':
        current_filter_month = filter_month_param
        year, month = map(int, current_filter_month.split('-'))
        query = query.filter(extract('year', Release.release_date) == year, extract('month', Release.release_date) == month)

    current_search_term = search_term_param.strip() if search_term_param else None
    if current_search_term:
        query = query.filter(or_(
            Release.name.ilike(f"%{current_search_term}%"),
            Release.brand.ilike(f"%{current_search_term}%")
        ))

    upcoming_releases = query.order_by(Release.release_date.asc()).all()
    total_count = len(upcoming_releases)
    total_pages = max(1, math.ceil(total_count / per_page)) if total_count else 1
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    end = start + per_page
    upcoming_releases = upcoming_releases[start:end]
    _ensure_heat_for_releases(upcoming_releases)

    release_display_map = build_release_display_map(
        upcoming_releases,
        db.session,
        user=current_user if current_user.is_authenticated else None,
    )

    upcoming_releases = sorted(
        upcoming_releases,
        key=lambda item: (
            (release_display_map.get(item.id, {}).get("release_date") or item.release_date),
            (item.name or "").lower(),
        ),
    )

    releases_by_month = OrderedDict()
    for release in upcoming_releases:
        display_date = release_display_map.get(release.id, {}).get("release_date") or release.release_date
        month_year_key = display_date.strftime('%B %Y') if display_date else "TBD"
        if month_year_key not in releases_by_month:
            releases_by_month[month_year_key] = []
        releases_by_month[month_year_key].append(release)

    return render_template('release_calendar.html', 
                           show_sort_controls=False,
                           title='Upcoming Sneaker Releases', 
                           releases_by_month=releases_by_month,
                           form=form,
                           brands_for_filter=brands_for_filter,
                           months_for_filter=months_for_filter,
                           current_filter_brand=current_filter_brand,
                           current_filter_month=current_filter_month,
                           current_search_term=current_search_term,
                           page=page,
                           total_pages=total_pages,
                           pagination_params={k: v for k, v in request.args.to_dict(flat=True).items() if k != 'page'},
                           release_display_map=release_display_map,
                           delete_all_form=delete_all_form)


def _render_release_detail(release, source=None):
    _ensure_heat_for_releases([release])
    display_data = resolve_release_display(
        release,
        db.session,
        user=current_user if current_user.is_authenticated else None,
    )
    active_offers = display_data.get("offers", [])
    offer_groups = {
        "retailer": [],
        "aftermarket": [],
        "raffle": [],
    }
    for offer in active_offers:
        offer_type = offer.offer_type or "aftermarket"
        if offer_type not in offer_groups:
            offer_type = "aftermarket"
        offer_groups[offer_type].append(offer)

    for offers in offer_groups.values():
        offers.sort(key=lambda item: (item.priority or 100, (item.retailer or "").lower()))

    modal_form = SneakerForm()
    if current_user.is_authenticated:
        modal_form.purchase_currency.data = current_user.preferred_currency or "GBP"
    preferred_currency = current_user.preferred_currency if current_user.is_authenticated else "GBP"
    avg_resale_price, avg_resale_currency = _average_resale(release.offers, preferred_currency)
    needs_resale_refresh = current_user.is_authenticated and _needs_resale_refresh(release.offers)
    needs_size_bid_refresh = current_user.is_authenticated and _needs_size_bid_refresh(release)
    size_bids, size_bids_fetched_at = _get_release_size_bids(release, allow_live_refresh=False)
    size_bid_series, size_type_options, size_type_default = _serialize_size_bid_series(
        size_bids,
        preferred_currency,
    )
    sneaker_material = None
    sneaker_record = find_matching_sneaker_record(release, db.session)
    if sneaker_record:
        sneaker_material = sneaker_record.primary_material

    market_stats = release.market_stats

    extras = build_release_detail_extras(
        release,
        db.session,
        preferred_currency,
        display_data=display_data,
        avg_resale_price=avg_resale_price,
        avg_resale_currency=avg_resale_currency,
        sneaker_record=sneaker_record,
        market_stats=market_stats,
    )
    admin_diagnostics = None
    if current_user.is_authenticated and getattr(current_user, "is_admin", False):
        stats_debug = None
        if market_stats is None and release.source_product_id:
            if release.source == "kicksdb_goat":
                stats_debug = "not returned by GOAT product endpoint"
            else:
                stats_debug = "missing"
        sneaker_description = None
        if sneaker_record:
            sneaker_description = sneaker_record.description or None
        aftermarket_offers = [o for o in release.offers if _is_aftermarket_offer(o)]
        offers_missing_price = [o for o in aftermarket_offers if o.price is None]
        total_sales = (
            db.session.query(func.count(ReleaseSalePoint.id))
            .filter(ReleaseSalePoint.release_id == release.id)
            .scalar()
        )
        admin_diagnostics = {
            "has_description": bool(extras.get("release_description")),
            "has_sneakerdb_description": bool(sneaker_description),
            "has_avg_price": bool((extras.get("average_resale_summary") or {}).get("primary")),
            "has_sales": bool(total_sales),
            "sales_count": int(total_sales or 0),
            "aftermarket_offers": len(aftermarket_offers),
            "offers_missing_price": len(offers_missing_price),
            "has_volatility": bool(market_stats and market_stats.volatility is not None),
            "has_sales_price_range": bool(
                market_stats
                and (
                    market_stats.sales_price_range_low is not None
                    or market_stats.sales_price_range_high is not None
                )
            ),
            "market_stats_debug": stats_debug,
            "source": release.source,
            "source_slug": release.source_slug,
            "source_product_id": release.source_product_id,
            "needs_refresh": _needs_resale_refresh(release.offers),
        }
    return render_template(
        'release_detail.html',
        title=release.name,
        release=release,
        release_display=display_data,
        release_description=extras.get("release_description"),
        market_metrics=extras.get("market_metrics"),
        average_resale_summary=extras.get("average_resale_summary"),
        admin_diagnostics=admin_diagnostics,
        offer_groups=offer_groups,
        avg_resale_price=avg_resale_price,
        avg_resale_currency=avg_resale_currency,
        needs_resale_refresh=needs_resale_refresh,
        needs_market_refresh=needs_resale_refresh or needs_size_bid_refresh,
        sneaker_material=sneaker_material,
        size_bid_series=size_bid_series,
        size_bids_fetched_at=size_bids_fetched_at,
        size_type_options=size_type_options,
        size_type_default=size_type_default,
        form=EmptyForm(),
        form_for_modal=modal_form,
        source=source,
    )


def _normalize_product_key(value):
    if not value:
        return ""
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()


def _lookup_release_by_product_key(product_key):
    if not product_key:
        return None
    normalized_key = _normalize_product_key(product_key)
    release = None
    if normalized_key:
        release = (
            Release.query.options(
                joinedload(Release.offers),
                joinedload(Release.prices),
                joinedload(Release.regions),
                joinedload(Release.market_stats),
            )
            .filter(
                func.upper(
                    func.replace(
                        func.replace(func.replace(Release.sku, "-", "_"), " ", "_"), "/", "_"
                    )
                )
                == normalized_key
            )
            .first()
        )
    if release:
        return release
    if product_key.lower().startswith("release_"):
        try:
            release_id = int(product_key.split("_", 1)[1])
        except (ValueError, IndexError):
            release_id = None
        if release_id:
            return (
                Release.query.options(
                    joinedload(Release.offers),
                    joinedload(Release.prices),
                    joinedload(Release.regions),
                    joinedload(Release.market_stats),
                )
                .filter_by(id=release_id)
                .first()
            )
    if "_" in product_key:
        source, source_product_id = product_key.split("_", 1)
        if source and source_product_id:
            source = source.lower()
            return (
                Release.query.options(
                    joinedload(Release.offers),
                    joinedload(Release.prices),
                    joinedload(Release.regions),
                    joinedload(Release.market_stats),
                )
                .filter(
                    func.lower(Release.source) == source,
                    Release.source_product_id == source_product_id,
                )
                .first()
            )
    return None


@main_bp.route('/releases/<int:release_id>')
def release_detail(release_id):
    release = (
        Release.query.options(
            joinedload(Release.offers),
            joinedload(Release.prices),
            joinedload(Release.regions),
            joinedload(Release.market_stats),
        )
        .filter_by(id=release_id)
        .first()
    )
    if not release:
        abort(404)
    canonical_key = build_product_key(release)
    canonical_slug = build_product_slug(release)
    return redirect(
        url_for('main.product_detail', product_key=canonical_key, slug=canonical_slug),
        code=302 if current_app.debug else 301,
    )


@main_bp.route('/products/<product_key>')
def product_detail_redirect(product_key):
    release = _lookup_release_by_product_key(product_key)
    if not release:
        abort(404)
    canonical_key = build_product_key(release)
    canonical_slug = build_product_slug(release)
    return redirect(
        url_for('main.product_detail', product_key=canonical_key, slug=canonical_slug),
        code=302 if current_app.debug else 301,
    )


@main_bp.route('/products/<nodash:product_key>-<slug>')
def product_detail(product_key, slug):
    release = _lookup_release_by_product_key(product_key)
    if not release:
        abort(404)
    canonical_key = build_product_key(release)
    canonical_slug = build_product_slug(release)
    if product_key != canonical_key or slug != canonical_slug:
        return redirect(
            url_for('main.product_detail', product_key=canonical_key, slug=canonical_slug),
            code=302 if current_app.debug else 301,
        )
    return _render_release_detail(release, source=request.args.get("source"))


@main_bp.route('/out/<int:offer_id>')
def outbound_offer(offer_id):
    offer = db.session.get(AffiliateOffer, offer_id)
    if not offer or not offer.is_active:
        return redirect(url_for('main.release_calendar'))
    target = offer.affiliate_url or offer.base_url
    return redirect(target, code=302)

@main_bp.route('/releases/<int:release_id>/refresh-resale', methods=['POST'])
@login_required
def refresh_release_resale(release_id):
    release = (
        Release.query.options(joinedload(Release.offers))
        .filter_by(id=release_id)
        .first()
    )
    if not release:
        return jsonify({'message': 'Release not found.'}), 404

    updated = _refresh_resale_for_release(release, max_per_day=3)
    preferred_currency = current_user.preferred_currency or "GBP"
    size_bids = []
    size_bids_fetched_at = release.size_bids_last_fetched_at
    if _needs_size_bid_refresh(release):
        size_bids, size_bids_fetched_at = _get_release_size_bids(release, allow_live_refresh=True)
    else:
        size_bids, size_bids_fetched_at = _get_release_size_bids(release, allow_live_refresh=False)
    size_bid_series, size_type_options, size_type_default = _serialize_size_bid_series(
        size_bids,
        preferred_currency,
    )
    avg_resale_price, avg_resale_currency = _average_resale(release.offers, preferred_currency)
    extras = build_release_detail_extras(
        release,
        db.session,
        preferred_currency,
        display_data={},
        avg_resale_price=avg_resale_price,
        avg_resale_currency=avg_resale_currency,
    )
    average_resale_summary = extras.get("average_resale_summary") or {}
    primary_average = average_resale_summary.get("primary") or {}
    avg_resale_display = primary_average.get("display") or {}
    return jsonify({
        'updated': updated,
        'avg_resale_price': float(avg_resale_price) if avg_resale_price is not None else None,
        'avg_resale_currency': avg_resale_currency,
        'avg_resale_display': avg_resale_display.get("display"),
        'avg_resale_label': primary_average.get("label"),
        'size_bid_series': size_bid_series,
        'size_type_options': size_type_options,
        'size_type_default': size_type_default,
        'size_bids_fetched_at': size_bids_fetched_at.isoformat() if size_bids_fetched_at else None,
    })


@main_bp.route('/admin/releases/<int:release_id>/refresh-market', methods=['POST'])
@login_required
@admin_required
def refresh_release_market_admin(release_id):
    form = EmptyForm()
    if not form.validate_on_submit():
        flash('Invalid request.', 'danger')
        return redirect(url_for('main.release_calendar'))

    release = (
        Release.query.options(joinedload(Release.offers))
        .filter_by(id=release_id)
        .first()
    )
    if not release:
        flash('Release not found.', 'warning')
        return redirect(url_for('main.release_calendar'))

    if release.sku:
        refreshed_release = _ensure_release_for_sku_with_resale(release.sku)
        if refreshed_release:
            release = refreshed_release

    refreshed = _refresh_resale_for_release(release, max_per_day=10, force_refresh=True)
    # Pull sales history so market metrics (sales volume) can populate after refresh.
    release.sales_last_fetched_at = None
    release.size_bids_last_fetched_at = None
    _get_release_sales_series(release, max_points=30)
    _get_release_size_bids(release, allow_live_refresh=True)
    if refreshed:
        flash('Market data refreshed.', 'success')
    else:
        flash('No new market data found.', 'info')

    return redirect(request.form.get("next") or url_for('main.product_detail', product_key=build_product_key(release), slug=build_product_slug(release)))


@main_bp.route('/admin/fx-rates', methods=['GET', 'POST'])
@login_required
@admin_required
def manage_fx_rates():
    form = FXRateForm()
    if form.validate_on_submit():
        base_currency = form.base_currency.data
        quote_currency = form.quote_currency.data
        rate = form.rate.data

        existing = (
            db.session.query(ExchangeRate)
            .filter_by(base_currency=base_currency, quote_currency=quote_currency)
            .first()
        )
        if not existing:
            existing = ExchangeRate(
                base_currency=base_currency,
                quote_currency=quote_currency,
                rate=rate,
            )
            db.session.add(existing)
        else:
            existing.rate = rate
            existing.as_of = datetime.utcnow()

        db.session.commit()
        flash('FX rate saved.', 'success')
        return redirect(url_for('main.manage_fx_rates'))

    rates = (
        db.session.query(ExchangeRate)
        .order_by(ExchangeRate.base_currency, ExchangeRate.quote_currency)
        .all()
    )
    return render_template('admin_fx_rates.html', title='Manage FX Rates', form=form, rates=rates)

# Admin Sneaker Sales Breakdown

@main_bp.route('/admin/sales-breakdown')
@login_required
@admin_required
def sales_breakdown():
    preferred_currency = current_user.preferred_currency or "GBP"
    delete_form = EmptyForm()

    sales_rows = (
        db.session.query(SneakerSale, Sneaker, Release)
        .outerjoin(Sneaker, Sneaker.id == SneakerSale.sneaker_id)
        .outerjoin(Release, Release.id == SneakerSale.release_id)
        .order_by(SneakerSale.sold_at.desc())
        .all()
    )

    def _display_amount(amount, currency):
        if amount is None or not currency:
            return None, None
        if currency == preferred_currency:
            return format_money(amount, currency), preferred_currency
        converted = convert_money(db.session, amount, currency, preferred_currency)
        if converted is None:
            return format_money(amount, currency), currency
        return format_money(converted, preferred_currency), preferred_currency

    sales_by_time = []
    brand_totals = {}
    total_sales_value = Decimal("0")
    total_purchase_value = Decimal("0")
    total_sales_count = 0

    for sale, sneaker, release in sales_rows:
        total_sales_count += 1
        sold_display, sold_display_currency = _display_amount(sale.sold_price, sale.sold_currency)
        purchase_display, purchase_display_currency = _display_amount(sale.purchase_price, sale.purchase_currency)

        sold_converted = None
        purchase_converted = None
        if sale.sold_price is not None and sale.sold_currency:
            sold_converted = convert_money(db.session, sale.sold_price, sale.sold_currency, preferred_currency)
            if sold_converted is None and sale.sold_currency == preferred_currency:
                sold_converted = sale.sold_price
        if sale.purchase_price is not None and sale.purchase_currency:
            purchase_converted = convert_money(db.session, sale.purchase_price, sale.purchase_currency, preferred_currency)
            if purchase_converted is None and sale.purchase_currency == preferred_currency:
                purchase_converted = sale.purchase_price

        roi_display = None
        if sold_converted is not None and purchase_converted is not None:
            roi_value = Decimal(str(sold_converted)) - Decimal(str(purchase_converted))
            roi_display = format_money(roi_value, preferred_currency)
            total_sales_value += Decimal(str(sold_converted))
            total_purchase_value += Decimal(str(purchase_converted))
        elif sold_converted is not None:
            total_sales_value += Decimal(str(sold_converted))

        brand_name = None
        if release and release.brand:
            brand_name = release.brand
        elif sneaker and sneaker.brand:
            brand_name = sneaker.brand
        brand_key = brand_name or "Unknown"
        if brand_key not in brand_totals:
            brand_totals[brand_key] = {"sale_count": 0, "total_value": Decimal("0")}
        brand_totals[brand_key]["sale_count"] += 1
        if sold_converted is not None:
            brand_totals[brand_key]["total_value"] += Decimal(str(sold_converted))

        sales_by_time.append({
            "id": sale.id,
            "date": sale.sold_at,
            "name": release.name if release else (f"{sneaker.brand} {sneaker.model}" if sneaker else "Unknown"),
            "sku": release.sku if release and release.sku else (sneaker.sku if sneaker and sneaker.sku else None),
            "sold_display": sold_display,
            "purchase_display": purchase_display,
            "roi_display": roi_display,
        })

    total_roi_display = None
    if total_purchase_value > 0:
        total_roi_value = total_sales_value - total_purchase_value
        total_roi_display = format_money(total_roi_value, preferred_currency)

    total_sales_display = format_money(total_sales_value, preferred_currency) if total_sales_count else None

    sales_by_brand = []
    for brand_name, stats in sorted(brand_totals.items(), key=lambda item: item[1]["sale_count"], reverse=True):
        display_total = format_money(stats["total_value"], preferred_currency) if stats["total_value"] else None
        sales_by_brand.append({
            "brand": brand_name,
            "sale_count": stats["sale_count"],
            "total_display": display_total,
        })

    return render_template(
        'admin_sales_breakdown.html',
        title='Sneaker Sales Breakdown',
        preferred_currency=preferred_currency,
        delete_form=delete_form,
        total_sales_count=total_sales_count,
        total_sales_display=total_sales_display,
        total_roi_display=total_roi_display,
        sales_by_time=sales_by_time,
        sales_by_brand=sales_by_brand,
    )


@main_bp.route('/admin/sales-breakdown/delete/<int:sale_id>', methods=['POST'])
@login_required
@admin_required
def delete_sale_record(sale_id):
    form = EmptyForm()
    if not form.validate_on_submit():
        flash('Invalid delete request.', 'danger')
        return redirect(url_for('main.sales_breakdown'))

    sale = db.session.get(SneakerSale, sale_id)
    if not sale:
        flash('Sale record not found.', 'warning')
        return redirect(url_for('main.sales_breakdown'))

    try:
        db.session.delete(sale)
        db.session.commit()
        flash('Sale record deleted.', 'success')
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(f"Failed to delete sale record {sale_id}: {exc}")
        flash('Unable to delete sale record.', 'danger')

    return redirect(url_for('main.sales_breakdown'))

# Admin Add New Release Route

def _set_if_value(model, field: str, value):
    if value is None:
        return
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return
        value = cleaned
    setattr(model, field, value)


@main_bp.route('/admin/add-release', methods=['GET', 'POST'])
@login_required
@admin_required
def add_release():
    form = ReleaseForm()
    if request.method == 'GET':
        form.retail_currency.data = current_user.preferred_currency or "GBP"
        form.us_currency.data = "USD"
        form.uk_currency.data = "GBP"
        form.eu_currency.data = "EUR"
    if form.validate_on_submit():
        errors = []
        final_image_location = None  # Will hold the URL or filename

        # --- IMAGE HANDLING LOGIC ---
        if form.image_option.data == 'url':
            if form.image_url.data:
                final_image_location = form.image_url.data.strip()
        elif form.image_option.data == 'upload':
            image_file = form.sneaker_image_file.data
            if image_file and image_file.filename != '':
                if allowed_file(image_file.filename):
                    original_filename = secure_filename(image_file.filename)
                    extension = os.path.splitext(original_filename)[1].lower()
                    unique_filename = str(uuid.uuid4().hex) + extension
                    save_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
                    try:
                        image_file.save(save_path)
                        final_image_location = unique_filename
                    except Exception as e:
                        current_app.logger.error(f"Failed to save release image: {e}")
                        errors.append('There was an error saving the uploaded image.')
                else:
                    errors.append('Invalid image file type.')
        # --- END IMAGE HANDLING LOGIC ---

        if form.retail_price.data is not None and not form.retail_currency.data:
            errors.append('Retail currency is required when a retail price is provided.')

        regions, region_errors = _collect_region_blocks_from_form(form)
        errors.extend(region_errors)
        earliest_region_date = _earliest_region_date_from_row(regions)
        if not earliest_region_date and not form.release_date.data:
            errors.append('Provide a fallback release date or at least one regional release date.')

        has_regional_price = any(block.get('retail_price') is not None for block in regions.values())
        if not has_regional_price and form.retail_price.data is None:
            errors.append('Provide a retail price for at least one region or a fallback retail price.')

        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template('add_release.html', title='Add New Release', form=form)

        model_name = (form.model_name.data or '').strip()
        display_name = (form.name.data or '').strip() or model_name
        sku_value = (form.sku.data or '').strip()
        normalized_sku = normalize_sku(sku_value) or sku_value

        new_release = Release(
            name=display_name,
            model_name=model_name,
            brand=form.brand.data.strip() if form.brand.data else None,
            sku=normalized_sku,
            colorway=(form.colorway.data or '').strip() or None,
            description=form.description.data,
            notes=form.notes.data,
            release_date=form.release_date.data or earliest_region_date,
            retail_price=form.retail_price.data,
            retail_currency=form.retail_currency.data or current_user.preferred_currency or "GBP",
            image_url=final_image_location,
            ingestion_source="admin_manual",
            ingested_at=datetime.utcnow(),
            ingested_by_user_id=current_user.id,
        )
        new_release.release_slug = build_product_slug(new_release)
        db.session.add(new_release)
        db.session.flush()

        for region, block in regions.items():
            if not block.get('release_date'):
                continue
            _upsert_release_region(db.session, new_release, region, block)
            _upsert_release_price(db.session, new_release, region, block)
            _upsert_retailer_links(db.session, new_release, region, block)

        if form.stockx_url.data:
            _upsert_affiliate_offer(db.session, new_release, "stockx", None, form.stockx_url.data.strip(), "aftermarket")
        if form.goat_url.data:
            _upsert_affiliate_offer(db.session, new_release, "goat", None, form.goat_url.data.strip(), "aftermarket")

        earliest_date = _earliest_region_date(db.session, new_release)
        if earliest_date:
            new_release.release_date = earliest_date

        db.session.commit()
        flash('New release has been added to the calendar!', 'success')
        return redirect(url_for('main.release_calendar'))

    return render_template('add_release.html', title='Add New Release', form=form)

# Admin Edit Sneaker Release Route

@main_bp.route('/admin/edit-release/<int:release_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_release(release_id):
    release_to_edit = db.session.get(Release, release_id)
    if not release_to_edit:
        abort(404)

    form = ReleaseForm(obj=release_to_edit)
    if request.method == 'GET':
        form.model_name.data = release_to_edit.model_name
        form.sku.data = release_to_edit.sku
        form.colorway.data = release_to_edit.colorway
        form.description.data = release_to_edit.description
        form.notes.data = release_to_edit.notes
        form.release_date.data = release_to_edit.release_date
        form.retail_currency.data = release_to_edit.retail_currency or current_user.preferred_currency or "GBP"

        stockx_offer = AffiliateOffer.query.filter_by(
            release_id=release_to_edit.id, retailer="stockx", region=None
        ).first()
        goat_offer = AffiliateOffer.query.filter_by(
            release_id=release_to_edit.id, retailer="goat", region=None
        ).first()
        if stockx_offer:
            form.stockx_url.data = stockx_offer.base_url
        if goat_offer:
            form.goat_url.data = goat_offer.base_url

        _populate_region_form(form, release_to_edit)

    if form.validate_on_submit():
        errors = []
        regions, region_errors = _collect_region_blocks_from_form(form)
        errors.extend(region_errors)
        if form.retail_price.data is not None and not form.retail_currency.data:
            errors.append('Retail currency is required when a retail price is provided.')

        has_regional_price = any(block.get('retail_price') is not None for block in regions.values())
        if not has_regional_price and form.retail_price.data is None:
            errors.append('Provide a retail price for at least one region or a fallback retail price.')

        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template('edit_release.html', title='Edit Release', form=form)

        _set_if_value(release_to_edit, 'model_name', form.model_name.data)
        _set_if_value(release_to_edit, 'name', form.name.data or form.model_name.data)
        _set_if_value(release_to_edit, 'brand', form.brand.data)
        if form.sku.data:
            sku_value = (form.sku.data or '').strip()
            release_to_edit.sku = normalize_sku(sku_value) or sku_value
        _set_if_value(release_to_edit, 'colorway', form.colorway.data)
        _set_if_value(release_to_edit, 'description', form.description.data)
        _set_if_value(release_to_edit, 'notes', form.notes.data)

        if form.retail_price.data is not None:
            release_to_edit.retail_price = form.retail_price.data
        if form.retail_currency.data:
            release_to_edit.retail_currency = form.retail_currency.data

        if form.image_option.data == 'url' and form.image_url.data:
            release_to_edit.image_url = form.image_url.data.strip()
        elif form.image_option.data == 'upload':
            image_file = form.sneaker_image_file.data
            if image_file and image_file.filename != '':
                if allowed_file(image_file.filename):
                    original_filename = secure_filename(image_file.filename)
                    extension = os.path.splitext(original_filename)[1].lower()
                    unique_filename = str(uuid.uuid4().hex) + extension
                    save_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
                    try:
                        image_file.save(save_path)
                        release_to_edit.image_url = unique_filename
                    except Exception as e:
                        current_app.logger.error(f"Failed to save release image: {e}")
                        flash('There was an error saving the uploaded image.', 'danger')
                else:
                    flash('Invalid image file type.', 'warning')

        if form.stockx_url.data:
            _upsert_affiliate_offer(db.session, release_to_edit, "stockx", None, form.stockx_url.data.strip(), "aftermarket")
        if form.goat_url.data:
            _upsert_affiliate_offer(db.session, release_to_edit, "goat", None, form.goat_url.data.strip(), "aftermarket")

        for region, block in regions.items():
            if not block.get('release_date'):
                continue
            _upsert_release_region(db.session, release_to_edit, region, block)
            _upsert_release_price(db.session, release_to_edit, region, block)
            _upsert_retailer_links(db.session, release_to_edit, region, block)

        earliest_date = _earliest_region_date(db.session, release_to_edit)
        if earliest_date:
            release_to_edit.release_date = earliest_date
        elif form.release_date.data:
            release_to_edit.release_date = form.release_date.data

        release_to_edit.release_slug = build_product_slug(release_to_edit)

        db.session.commit()
        flash('Release has been updated!', 'success')
        return redirect(url_for('main.release_calendar'))

    return render_template('edit_release.html', title='Edit Release', form=form)

# Admin Delete Sneaker Release Route

@main_bp.route('/admin/delete-release/<int:release_id>', methods=['POST'])
@login_required
@admin_required
def delete_release(release_id):
    """Deletes a specific release from the database."""
    # For now, we assume any logged-in user can delete. We can add admin checks later.

    release_to_delete = db.session.get(Release, release_id)

    if not release_to_delete:
        flash('Release not found.', 'warning')
        return redirect(url_for('main.release_calendar'))

    try:
        db.session.delete(release_to_delete)
        db.session.commit()
        flash(f"'{release_to_delete.name}' has been successfully deleted.", 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting release {release_id}: {e}")
        flash('Error deleting release. Please try again.', 'danger')

    return redirect(url_for('main.release_calendar'))


@main_bp.route('/admin/delete-all-releases', methods=['POST'])
@login_required
@admin_required
def delete_all_releases():
    form = DeleteAllReleasesForm()
    if not form.validate_on_submit():
        flash('Invalid delete request.', 'danger')
        return redirect(url_for('main.release_calendar'))

    confirmation = (form.confirmation.data or "").strip()
    if confirmation != "DELETE ALL RELEASES":
        flash('Confirmation text did not match. No releases were deleted.', 'warning')
        return redirect(url_for('main.release_calendar'))

    try:
        releases = Release.query.filter(Release.is_calendar_visible.is_(True)).all()
        for release in releases:
            release.is_calendar_visible = False
        db.session.commit()
        flash('All releases have been removed from the release calendar.', 'success')
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(
            f"Delete all releases failed by user {current_user.id}: {exc}"
        )
        flash('Failed to delete releases. Please try again.', 'danger')

    return redirect(url_for('main.release_calendar'))


@main_bp.route('/admin/release-import', methods=['GET', 'POST'])
@login_required
@admin_required
def release_csv_import():
    form = ReleaseCsvImportForm()
    confirm_form = EmptyForm()
    preview = None
    csv_text = None
    summary = None
    allow_confirm = False

    if form.validate_on_submit():
        upload = form.csv_file.data
        if not upload:
            flash('Please choose a CSV file.', 'warning')
        else:
            try:
                raw = upload.read()
                csv_text = raw.decode('utf-8-sig', errors='replace')
            except Exception as exc:
                current_app.logger.error("Failed to read CSV upload: %s", exc)
                flash('Unable to read the CSV file.', 'danger')
            else:
                preview = build_release_import_preview(db.session, csv_text)
                summary = _summarize_release_import_preview(preview, form.skip_existing.data)
                blocking_errors = bool(preview.get("errors")) or any(
                    row.get("errors") for row in (preview.get("rows") or [])
                )
                allow_confirm = not blocking_errors
                if blocking_errors:
                    flash('Fix the errors below before importing.', 'danger')
                else:
                    flash('Preview generated. Review and confirm to import.', 'info')

    return render_template(
        'admin/release_import.html',
        title='Release CSV Import',
        form=form,
        confirm_form=confirm_form,
        preview=preview,
        csv_text=csv_text,
        summary=summary,
        allow_confirm=allow_confirm,
    )


@main_bp.route('/admin/release-import/confirm', methods=['POST'])
@login_required
@admin_required
def release_csv_import_confirm():
    form = EmptyForm()
    if not form.validate_on_submit():
        flash('Invalid import request.', 'danger')
        return redirect(url_for('main.release_csv_import'))

    csv_text = request.form.get('csv_text', '')
    if not csv_text:
        flash('Missing CSV data. Please re-upload.', 'warning')
        return redirect(url_for('main.release_csv_import'))

    skip_existing = request.form.get('skip_existing') == 'y'
    batch_id = str(uuid.uuid4().hex)
    try:
        result = apply_release_csv_import(
            db.session,
            csv_text,
            ingestion_batch_id=batch_id,
            ingested_by_user_id=current_user.id,
            dry_run=False,
            skip_existing=skip_existing,
        )
    except Exception as exc:
        current_app.logger.error("CSV import failed: %s", exc)
        flash('CSV import failed. Please check logs.', 'danger')
        return redirect(url_for('main.release_csv_import'))

    if result.get("has_errors"):
        flash('Import failed due to validation errors. Please re-upload.', 'danger')
        summary = _summarize_release_import_preview(result, skip_existing)
        return render_template(
            'admin/release_import.html',
            title='Release CSV Import',
            form=ReleaseCsvImportForm(),
            confirm_form=form,
            preview=result,
            csv_text=csv_text,
            summary=summary,
            allow_confirm=False,
        )

    applied = result.get("applied") or {}
    created = applied.get("created", 0)
    updated = applied.get("updated", 0)
    flash(f'CSV import completed. Created {created} release(s), updated {updated} release(s).', 'success')
    summary = _summarize_release_import_preview(result, skip_existing)
    if summary and summary.get("past_dated_valid_rows"):
        flash(
            f"{summary['past_dated_valid_rows']} imported row(s) are past-dated and will not appear on the upcoming release calendar.",
            'info',
        )
    return redirect(url_for('main.release_csv_import'))


@main_bp.route('/admin/release-import/template', methods=['GET'])
@login_required
@admin_required
def release_csv_import_template():
    headers = list(RELEASE_CSV_HEADERS)
    guide_row = [
        "__FORMAT_GUIDE__",
        "Model name",
        "Colourway (optional)",
        "SKU",
        "https://image.url",
        "https://stockx.url",
        "https://goat.url",
        "Notes",
        "Description",
        "YYYY-MM-DD",
        "HH:MM",
        "America/New_York",
        "200",
        "USD",
        "Retailer Name|https://example.com; Retailer Name|https://example.com",
        "YYYY-MM-DD",
        "HH:MM",
        "Europe/London",
        "180",
        "GBP",
        "Retailer Name|https://example.com; Retailer Name|https://example.com",
        "YYYY-MM-DD",
        "HH:MM",
        "Europe/Paris",
        "190",
        "EUR",
        "Retailer Name|https://example.com; Retailer Name|https://example.com",
    ]
    sample_row = [
        "Nike",
        "Air Max 1",
        "University Red/White",
        "SKU123",
        "https://example.com/image.jpg",
        "https://stockx.com/air-max-1",
        "https://goat.com/air-max-1",
        "Launch notes",
        "Sample description",
        "2026-04-10",
        "08:00",
        "America/New_York",
        "200",
        "USD",
        "Nike|https://nike.com; Foot Locker|https://footlocker.com",
        "2026-04-10",
        "08:00",
        "Europe/London",
        "180",
        "GBP",
        "Foot Locker|https://footlocker.co.uk",
        "2026-04-10",
        "09:00",
        "Europe/Paris",
        "190",
        "EUR",
        "Foot Locker|https://footlocker.eu",
    ]
    rows = [headers, guide_row, sample_row]
    for row in rows:
        if len(row) != len(headers):
            raise ValueError("CSV template row length mismatch.")

    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerows(rows)
    response = current_app.response_class(
        csv_buffer.getvalue(),
        mimetype='text/csv',
    )
    response.headers['Content-Disposition'] = 'attachment; filename=release_import_template.csv'
    return response


def _summarize_release_import_preview(preview, skip_existing: bool):
    if not preview:
        return None
    rows = preview.get("rows") or []
    error_rows = [row for row in rows if row.get("errors")]
    warning_rows = [row for row in rows if row.get("warnings")]
    matched_rows = [row for row in rows if row.get("match") and not row.get("errors")]
    create_rows = [row for row in rows if not row.get("match") and not row.get("errors")]
    past_dated_valid_rows = 0
    for row in rows:
        if row.get("errors"):
            continue
        regions = ((row.get("normalized") or {}).get("regions") or {}).values()
        regional_dates = [block.get("release_date") for block in regions if isinstance(block, dict) and block.get("release_date")]
        if regional_dates and min(regional_dates) < date.today():
            past_dated_valid_rows += 1
    would_update = 0 if skip_existing else len(matched_rows)
    return {
        "total_rows": len(rows),
        "valid_rows": len(rows) - len(error_rows),
        "invalid_rows": len(error_rows),
        "warnings_count": sum(len(row.get("warnings") or []) for row in rows),
        "warning_rows": len(warning_rows),
        "would_create": len(create_rows),
        "would_update": would_update,
        "skipped": len(matched_rows) if skip_existing else 0,
        "past_dated_valid_rows": past_dated_valid_rows,
    }


def _upsert_release_prices(release_id, form):
    price_map = {
        "GBP": form.regional_price_gbp.data,
        "USD": form.regional_price_usd.data,
        "EUR": form.regional_price_eur.data,
    }
    for currency, price in price_map.items():
        existing = (
            db.session.query(ReleasePrice)
            .filter_by(release_id=release_id, currency=currency, region=None)
            .first()
        )
        if price is None:
            if existing:
                db.session.delete(existing)
            continue
        if not existing:
            existing = ReleasePrice(
                release_id=release_id,
                currency=currency,
                price=price,
                region=None,
            )
            db.session.add(existing)
        else:
            existing.price = price



REGION_FORM_PREFIXES = {"US": "us", "UK": "uk", "EU": "eu"}


def _collect_region_blocks_from_form(form):
    regions = {}
    errors = []
    for region, prefix in REGION_FORM_PREFIXES.items():
        release_date = getattr(form, f"{prefix}_release_date").data
        time_raw = (getattr(form, f"{prefix}_release_time").data or "").strip()
        release_time = _parse_time_value(time_raw) if time_raw else None
        timezone = (getattr(form, f"{prefix}_timezone").data or "").strip() or None
        price = getattr(form, f"{prefix}_retail_price").data
        currency = getattr(form, f"{prefix}_currency").data or None
        links_value = (getattr(form, f"{prefix}_retailer_links").data or "").strip()
        retailer_links = []
        if links_value:
            parsed_links, link_errors = _parse_retailer_links(links_value, region)
            retailer_links = parsed_links
            errors.extend(link_errors)

        regions[region] = {
            "release_date": release_date,
            "release_time": release_time,
            "timezone": timezone,
            "retail_price": price,
            "currency": currency,
            "retailer_links": retailer_links,
        }

    def apply_date(source, targets, flag_name):
        if not getattr(form, flag_name).data:
            return
        source_date = regions[source]["release_date"]
        if not source_date:
            return
        for target in targets:
            if not regions[target]["release_date"]:
                regions[target]["release_date"] = source_date

    apply_date("US", ["UK"], "apply_us_date_to_uk")
    apply_date("US", ["EU"], "apply_us_date_to_eu")
    apply_date("UK", ["US"], "apply_uk_date_to_us")
    apply_date("UK", ["EU"], "apply_uk_date_to_eu")
    apply_date("EU", ["US"], "apply_eu_date_to_us")
    apply_date("EU", ["UK"], "apply_eu_date_to_uk")

    for region, block in regions.items():
        price = block.get("retail_price")
        currency = block.get("currency")
        release_date = block.get("release_date")
        release_time = block.get("release_time")
        timezone = block.get("timezone")
        retailer_links = block.get("retailer_links") or []

        if price is not None and not currency:
            errors.append(f"{region} currency is required when a retail price is provided.")

        has_regional_payload = any([price is not None, retailer_links, release_time])
        if has_regional_payload and not release_date:
            errors.append(f"{region} release date is required when providing regional data.")

        if not release_date:
            timezone = None
            if price is None:
                currency = None

        block["timezone"] = timezone
        block["currency"] = currency

    return regions, errors


def _populate_region_form(form, release):
    regions = {row.region: row for row in (release.regions or [])}
    prices_by_region = {}
    for price in (release.prices or []):
        if price.region:
            prices_by_region[price.region] = price
    offers_by_region = {}
    for offer in (release.offers or []):
        if offer.offer_type != "retailer" or not offer.region:
            continue
        offers_by_region.setdefault(offer.region, []).append(offer)

    for region, prefix in REGION_FORM_PREFIXES.items():
        region_row = regions.get(region)
        if region_row:
            getattr(form, f"{prefix}_release_date").data = region_row.release_date
            getattr(form, f"{prefix}_release_time").data = region_row.release_time.strftime("%H:%M") if region_row.release_time else None
            getattr(form, f"{prefix}_timezone").data = region_row.timezone
        price_row = prices_by_region.get(region)
        if price_row:
            getattr(form, f"{prefix}_retail_price").data = price_row.price
            getattr(form, f"{prefix}_currency").data = price_row.currency
        offers = offers_by_region.get(region, [])
        if offers:
            links_value = "; ".join([f"{o.retailer}|{o.base_url}" for o in offers if o.base_url])
            getattr(form, f"{prefix}_retailer_links").data = links_value

# Add to Wishlist Route

@main_bp.route('/wishlist/add/<int:release_id>', methods=['POST'])
@login_required
def add_to_wishlist(release_id):
    release = db.session.get(Release, release_id)
    if not release or release in current_user.wishlist:
        return jsonify({'status': 'error', 'message': 'Invalid request.'}), 400
    current_user.wishlist.append(release)
    db.session.commit()
    _refresh_resale_for_release(release)
    wishlist_variant = request.args.get('source')
    new_button_html = render_template('_wishlist_button.html', release=release, wishlist_variant=wishlist_variant)
    return jsonify({'status': 'success', 'message': 'Added to wishlist!', 'new_button_html': new_button_html})


# Remove from Wishlist Route

@main_bp.route('/wishlist/remove/<int:release_id>', methods=['POST'])
@login_required
def remove_from_wishlist(release_id):
    release = db.session.get(Release, release_id)
    if not release or release not in current_user.wishlist:
        return jsonify({'status': 'error', 'message': 'Invalid request.'}), 400
    current_user.wishlist.remove(release)
    db.session.commit()
    wishlist_variant = request.args.get('source')
    new_button_html = render_template('_wishlist_button.html', release=release, wishlist_variant=wishlist_variant)
    return jsonify({'status': 'success', 'message': 'Removed from wishlist.', 'new_button_html': new_button_html})

# Add to Wishlist by SKU Route
@main_bp.route('/wishlist/add-by-sku', methods=['POST'])
@login_required
def add_to_wishlist_by_sku():
    query = (request.form.get('sku') or '').strip()
    if not query:
        flash('Please enter a SKU.', 'warning')
        return redirect(url_for('main.wishlist'))

    sku_filters = [Release.sku.ilike(value) for value in sku_variants(query)]
    if not sku_filters:
        sku_filters = [Release.sku.ilike(query)]
    release = Release.query.filter(or_(*sku_filters)).first()
    if not release:
        api_key = current_app.config.get('KICKS_API_KEY')
        if not api_key:
            flash('KICKS_API_KEY is not configured.', 'danger')
            return redirect(url_for('main.wishlist'))

        client = KicksClient(
            api_key=api_key,
            base_url=current_app.config.get('KICKS_API_BASE_URL', 'https://api.kicks.dev'),
            logger=current_app.logger,
        )
        try:
            lookup_query = normalize_sku(query) or query
            result = lookup_or_fetch_sneaker(
                query=lookup_query,
                db_session=db.session,
                client=client,
                max_age_hours=24,
                force_best=True,
                return_candidates=False,
                mode="lite",
            )
        except Exception as exc:
            current_app.logger.warning("Wishlist lookup failed for '%s': %s", query, exc)
            flash('Unable to look up that sneaker right now.', 'warning')
            return redirect(url_for('main.wishlist'))

        if result.get('status') != 'ok' or not result.get('sneaker'):
            flash('Sneaker not found.', 'warning')
            return redirect(url_for('main.wishlist'))

        data = result.get('sneaker')
        sku = data.get('sku')
        release_date = _parse_release_date_from_lookup(data.get('release_date'))
        release_visible = True
        if not release_date:
            release_date = date.today()
            release_visible = False
        if isinstance(release_date, date) and release_date < date.today():
            release_visible = False

        release = Release(
            sku=sku,
            name=data.get('model_name') or data.get('name') or sku or 'Unknown',
            model_name=data.get('model_name'),
            brand=data.get('brand'),
            colorway=data.get('colorway'),
            image_url=data.get('image_url'),
            retail_price=data.get('retail_price'),
            retail_currency=data.get('retail_currency') or "USD",
            release_date=release_date,
            source="kicksdb_stockx" if data.get('stockx_id') or data.get('stockx_slug') else "kicksdb_goat" if data.get('goat_id') or data.get('goat_slug') else "wishlist_lookup",
            source_product_id=data.get('stockx_id') or data.get('goat_id'),
            source_slug=data.get('stockx_slug') or data.get('goat_slug'),
            is_calendar_visible=release_visible,
        )
        db.session.add(release)
        db.session.commit()
        _ensure_offers_from_lookup(release, data)
        db.session.commit()
        _refresh_resale_for_release(release)
    else:
        data = {}
        if release.source == "kicksdb_goat":
            data = {"goat_slug": release.source_slug, "goat_id": release.source_product_id}
        elif release.source == "kicksdb_stockx":
            data = {"stockx_slug": release.source_slug, "stockx_id": release.source_product_id}
        _ensure_offers_from_lookup(release, data)
        db.session.commit()
        _refresh_resale_for_release(release)

    if release in current_user.wishlist:
        flash('That release is already in your wishlist.', 'info')
        return redirect(url_for('main.wishlist'))

    current_user.wishlist.append(release)
    db.session.commit()
    flash('Release added to your wishlist.', 'success')
    return redirect(url_for('main.wishlist'))

# Wishlist Route

@main_bp.route('/my-wishlist')
@login_required
def wishlist():
    """Displays the current user's wishlist, with filtering and searching."""
    form = EmptyForm()
    sneaker_form = SneakerForm()
    sneaker_form.purchase_currency.data = current_user.preferred_currency or "GBP"
    page = request.args.get('page', default=1, type=int)
    per_page = 40
    sort_by_param = request.args.get('sort_by')
    order_param = request.args.get('order')

    # Get filter/search parameters from the URL
    filter_brand_param = request.args.get('filter_brand')
    filter_month_param = request.args.get('filter_month')
    search_term_param = request.args.get('search_term')

    # Base query: Get all releases on the current user's wishlist
    query = Release.query.options(joinedload(Release.prices), joinedload(Release.offers)).join(wishlist_items).filter(wishlist_items.c.user_id == current_user.id)

    # Get distinct brands and months for the dropdowns from the user's wishlist
    distinct_brands_tuples = query.with_entities(Release.brand).distinct().order_by(Release.brand).all()
    brands_for_filter = [brand[0] for brand in distinct_brands_tuples if brand[0]]

    distinct_months_tuples = query.with_entities(
        extract('year', Release.release_date), 
        extract('month', Release.release_date)
    ).distinct().order_by(
        extract('year', Release.release_date), 
        extract('month', Release.release_date)
    ).all()
    months_for_filter = _format_month_filter_choices(distinct_months_tuples)

    # Apply filters to the main query
    current_filter_brand = filter_brand_param if filter_brand_param and filter_brand_param != 'all' else None
    if current_filter_brand:
        query = query.filter(Release.brand == current_filter_brand)

    current_filter_month = filter_month_param if filter_month_param and filter_month_param != 'all' else None
    if current_filter_month:
        year, month = map(int, current_filter_month.split('-'))
        query = query.filter(extract('year', Release.release_date) == year, extract('month', Release.release_date) == month)

    current_search_term = search_term_param.strip() if search_term_param else None
    if current_search_term:
        query = query.filter(Release.name.ilike(f"%{current_search_term}%"))

    allowed_sort_by = {"date_added", "retail_price", "resale_price"}
    sort_active_in_url = sort_by_param in allowed_sort_by
    effective_sort_by = sort_by_param if sort_active_in_url else None
    effective_order = order_param if order_param in ['asc', 'desc'] else 'desc'

    if sort_active_in_url:
        wishlist_items_list = query.all()
        _ensure_heat_for_releases(wishlist_items_list)
        total_count = len(wishlist_items_list)
        total_pages = max(1, math.ceil(total_count / per_page)) if total_count else 1
        page = max(1, min(page, total_pages))
        preferred_currency = current_user.preferred_currency or "GBP"
        avg_resale_map = {
            release.id: _average_resale(release.offers, preferred_currency)
            for release in wishlist_items_list
        }
        wishlist_added_map = {
            release_id: created_at
            for release_id, created_at in db.session.query(
                wishlist_items.c.release_id, wishlist_items.c.created_at
            ).filter(wishlist_items.c.user_id == current_user.id)
        }

        if effective_sort_by == "retail_price":
            def sort_key(item):
                value = item.retail_price
                return (value is None, value)
        elif effective_sort_by == "resale_price":
            def sort_key(item):
                avg = avg_resale_map.get(item.id)
                value = avg[0] if avg else None
                return (value is None, value)
        else:
            def sort_key(item):
                value = wishlist_added_map.get(item.id)
                return (value is None, value or datetime.min)

        wishlist_items_list.sort(key=sort_key, reverse=(effective_order == 'desc'))
        start = (page - 1) * per_page
        end = start + per_page
        wishlist_items_list = wishlist_items_list[start:end]

        return render_template('wishlist.html', 
                               show_sort_controls=True,
                               title='My Wishlist', 
                               releases=wishlist_items_list,
                               release_display_map=build_release_display_map(wishlist_items_list, db.session, user=current_user),
                               avg_resale_map=avg_resale_map,
                               form=form,
                               form_for_modal=sneaker_form,
                               brands_for_filter=brands_for_filter,
                               months_for_filter=months_for_filter,
                               current_filter_brand=current_filter_brand,
                               current_filter_month=current_filter_month,
                               current_search_term=current_search_term,
                               current_sort_by=effective_sort_by,
                               current_order=effective_order,
                               sort_active_in_url=sort_active_in_url,
                               allowed_sort_fields=[
                                   {"key": "date_added", "label": "Date Added", "default_order": "desc"},
                                   {"key": "retail_price", "label": "Retail Price", "default_order": "desc"},
                                   {"key": "resale_price", "label": "Resale Price", "default_order": "desc"},
                               ],
                               page=page,
                               total_pages=total_pages,
                               pagination_params={k: v for k, v in request.args.to_dict(flat=True).items() if k != 'page'})

    today = date.today()
    past_count = query.filter(Release.release_date < today).count()
    months_query = query.filter(Release.release_date >= today).with_entities(
        extract('year', Release.release_date).label('year'),
        extract('month', Release.release_date).label('month'),
        func.count(Release.id).label('count')
    ).group_by(
        extract('year', Release.release_date),
        extract('month', Release.release_date)
    ).order_by(
        extract('year', Release.release_date),
        extract('month', Release.release_date)
    )
    months_with_counts = [(int(year), int(month), int(count)) for year, month, count in months_query.all()]
    group_entries = []
    if past_count:
        group_entries.append(("Past Releases", None, None, past_count))
    for year, month, count in months_with_counts:
        label = datetime(year, month, 1).strftime('%B %Y')
        group_entries.append((label, year, month, count))

    pages = []
    current_page_groups = []
    current_count = 0
    for label, year, month, count in group_entries:
        if current_page_groups and current_count + count > per_page:
            pages.append(current_page_groups)
            current_page_groups = []
            current_count = 0
        current_page_groups.append((label, year, month))
        current_count += count
        if current_count >= per_page:
            pages.append(current_page_groups)
            current_page_groups = []
            current_count = 0
    if current_page_groups:
        pages.append(current_page_groups)

    total_pages = max(1, len(pages))
    page = max(1, min(page, total_pages))
    group_slice = pages[page - 1] if pages else []

    if group_slice:
        group_filters = []
        for label, year, month in group_slice:
            if label == "Past Releases":
                group_filters.append(Release.release_date < today)
            elif year and month:
                group_filters.append(
                    (extract('year', Release.release_date) == year)
                    & (extract('month', Release.release_date) == month)
                )
        if group_filters:
            query = query.filter(or_(*group_filters))

    wishlist_items_list = query.order_by(Release.release_date.asc()).all()
    _ensure_heat_for_releases(wishlist_items_list)
    preferred_currency = current_user.preferred_currency or "GBP"
    avg_resale_map = {
        release.id: _average_resale(release.offers, preferred_currency)
        for release in wishlist_items_list
    }
    release_display_map = build_release_display_map(wishlist_items_list, db.session, user=current_user)

    releases_by_month = OrderedDict()
    for release in wishlist_items_list:
        if release.release_date and release.release_date < today:
            month_year_key = "Past Releases"
        elif not release.release_date:
            month_year_key = "Unknown Release Date"
        else:
            month_year_key = release.release_date.strftime('%B %Y')
        if month_year_key not in releases_by_month:
            releases_by_month[month_year_key] = []
        releases_by_month[month_year_key].append(release)

    return render_template('wishlist.html', 
                           show_sort_controls=True,
                           title='My Wishlist', 
                           releases_by_month=releases_by_month,
                           release_display_map=release_display_map,
                           avg_resale_map=avg_resale_map,
                           form=form,
                           form_for_modal=sneaker_form,
                           brands_for_filter=brands_for_filter,
                           months_for_filter=months_for_filter,
                           current_filter_brand=current_filter_brand,
                           current_filter_month=current_filter_month,
                           current_search_term=current_search_term,
                           current_sort_by=effective_sort_by,
                           current_order=effective_order,
                           sort_active_in_url=sort_active_in_url,
                           allowed_sort_fields=[
                               {"key": "date_added", "label": "Date Added", "default_order": "desc"},
                               {"key": "retail_price", "label": "Retail Price", "default_order": "desc"},
                               {"key": "resale_price", "label": "Resale Price", "default_order": "desc"},
                           ],
                           page=page,
                           total_pages=total_pages,
                           pagination_params={k: v for k, v in request.args.to_dict(flat=True).items() if k != 'page'})

# Select For Wishlist Route
@main_bp.route('/select-for-wishlist', methods=['GET', 'POST'])
@login_required
def select_for_wishlist():
    form = EmptyForm() # For CSRF protection on the POST
    
    if request.method == 'POST':
        selected_ids = request.form.getlist('release_ids')
        if not selected_ids:
            flash('You did not select any releases to add.', 'warning')
            return redirect(url_for('main.select_for_wishlist'))

        releases_to_add = Release.query.filter(Release.id.in_(selected_ids)).all()
        
        added_count = 0
        for release in releases_to_add:
            # Avoid adding duplicates
            if release not in current_user.wishlist:
                current_user.wishlist.append(release)
                added_count += 1
        
        if added_count > 0:
            db.session.commit()
            flash(f'Successfully added {added_count} new release(s) to your wishlist!', 'success')
        else:
            flash('The selected releases were already on your wishlist.', 'info')
            
        return redirect(url_for('main.wishlist'))

    wishlist_release_ids = {release.id for release in current_user.wishlist}

    today = date.today()
    query = Release.query.filter(Release.release_date >= today, Release.is_calendar_visible.is_(True))

    # This is the same logic from your release_calendar route to handle filters
    filter_brand_param = request.args.get('filter_brand')
    filter_month_param = request.args.get('filter_month')
    search_term_param = request.args.get('search_term')

    distinct_brands_tuples = query.with_entities(Release.brand).distinct().order_by(Release.brand).all()
    brands_for_filter = [brand[0] for brand in distinct_brands_tuples if brand[0]]

    distinct_months_tuples = db.session.query(extract('year', Release.release_date), extract('month', Release.release_date)) \
        .filter(Release.release_date >= today, Release.is_calendar_visible.is_(True)).distinct().order_by(extract('year', Release.release_date), extract('month', Release.release_date)).all()

    months_for_filter = []
    for year, month in distinct_months_tuples:
        year, month = int(year), int(month)
        date_obj = datetime(year, month, 1)
        months_for_filter.append((f"{year}-{month:02d}", date_obj.strftime('%B %Y')))

    current_filter_brand = filter_brand_param if filter_brand_param and filter_brand_param != 'all' else None
    if current_filter_brand:
        query = query.filter(Release.brand == current_filter_brand)

    current_filter_month = filter_month_param if filter_month_param and filter_month_param != 'all' else None
    if current_filter_month:
        year, month = map(int, current_filter_month.split('-'))
        query = query.filter(extract('year', Release.release_date) == year, extract('month', Release.release_date) == month)

    current_search_term = search_term_param.strip() if search_term_param else None
    if current_search_term:
        query = query.filter(Release.name.ilike(f"%{current_search_term}%"))

    available_releases = query.order_by(Release.release_date.asc()).all()

    for release in available_releases:
        release.is_on_wishlist = release.id in wishlist_release_ids

    return render_template('select_for_wishlist.html',
                           title='Add to Wishlist from Calendar',
                           available_releases=available_releases,
                           form=form,
                           brands_for_filter=brands_for_filter,
                           months_for_filter=months_for_filter,
                           current_filter_brand=current_filter_brand,
                           current_filter_month=current_filter_month,
                           current_search_term=current_search_term,
                           show_sort_controls=False) # No sorting on this page
