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
from flask_login import login_required, current_user
from extensions import db
from models import Article, User, Sneaker, Release, SneakerDB, AffiliateOffer, ExchangeRate, ReleasePrice, UserApiUsage, SneakerSale, wishlist_items, UserApiToken
from forms import EditProfileForm, ReleaseForm, EmptyForm, SneakerForm, FXRateForm, MobileTokenForm
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
from services.api_tokens import create_token_for_user
from routes.sneakers_routes import _get_release_size_bids


main_bp = Blueprint('main', __name__)


def _average_resale(offers, preferred_currency: str):
    aftermarket = [offer for offer in offers if offer.offer_type == "aftermarket" and offer.price is not None]
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
    if offer and offer.base_url and "goat.com" in offer.base_url:
        try:
            path = urlparse(offer.base_url).path.strip("/")
            if path:
                return path.split("/")[-1]
        except ValueError:
            pass
    if release.source == "kicksdb_goat":
        return release.source_product_id or release.source_slug
    return None


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
        if offer.offer_type != "aftermarket":
            continue
        if offer.price is None:
            return True
        if not offer.last_checked_at or offer.last_checked_at.strftime("%Y-%m") != current_month:
            return True
    return False


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


def _refresh_resale_for_release(release: Release, max_per_day: int = 3) -> bool:
    if not _needs_resale_refresh(release.offers):
        return False
    if has_request_context():
        if not (current_user.is_authenticated and getattr(current_user, "is_admin", False)):
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
    aftermarket_offers = [o for o in release.offers if o.offer_type == "aftermarket"]
    for offer in aftermarket_offers:
        try:
            if offer.retailer == "stockx":
                id_or_slug = release.source_product_id or release.source_slug
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
                updated = _update_release_from_detail(release, normalized_detail) or updated
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
            elif offer.retailer == "goat":
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
                detail = client.get_goat_product(id_or_slug)
                normalized_detail = _normalize_kicks_detail(detail or {})
                updated = _update_release_from_detail(release, normalized_detail) or updated
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


def _update_release_from_detail(release: Release, detail: dict) -> bool:
    if not detail:
        return False
    changed = False
    for key, attr in (
        ("brand", "brand"),
        ("model_name", "model_name"),
        ("name", "name"),
        ("colorway", "colorway"),
        ("image_url", "image_url"),
        ("retail_price", "retail_price"),
        ("retail_currency", "retail_currency"),
    ):
        value = detail.get(key) or detail.get(attr)
        if getattr(release, attr, None) is None and value is not None:
            setattr(release, attr, value)
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
    price = (
        short_window if prefer_short_window else None
        or stats.get("last_90_days_average_price")
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
    price = (
        detail.get("average_sale_price")
        or detail.get("averageSalePrice")
        or detail.get("avg_sale_price")
        or detail.get("avgSalePrice")
        or detail.get("average_price")
        or detail.get("averagePrice")
        or detail.get("lowest_ask")
        or detail.get("lowestAsk")
    )
    return _to_decimal(price)


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
            merged = dict(data["product"])
            for key in (
                "market",
                "statistics",
                "traits",
                "productTraits",
                "release_date",
                "releaseDate",
            ):
                if key in data and key not in merged:
                    merged[key] = data[key]
            return merged
        detail = data
    if isinstance(detail.get("product"), dict):
        product = dict(detail["product"])
        for key in ("market", "statistics", "traits", "productTraits"):
            if key in detail and key not in product:
                product[key] = detail[key]
        return product
    if isinstance(detail.get("result"), dict):
        detail = detail["result"]
    if isinstance(detail.get("results"), list) and detail["results"]:
        first = detail["results"][0]
        if isinstance(first, dict):
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
            source="kicksdb_stockx" if data.get('stockx_id') or data.get('stockx_slug') else "kicksdb_goat" if data.get('goat_id') or data.get('goat_slug') else "lookup",
            source_product_id=data.get('stockx_id') or data.get('goat_id'),
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
                release.name = data.get('model_name') or data.get('name') or release.name
                release.model_name = data.get('model_name') or release.model_name
                release.brand = data.get('brand') or release.brand
                release.colorway = data.get('colorway') or release.colorway
                release.image_url = data.get('image_url') or release.image_url
                release.retail_price = data.get('retail_price') or release.retail_price
                release.retail_currency = data.get('retail_currency') or release.retail_currency
                release.source = (
                    "kicksdb_stockx"
                    if data.get('stockx_id') or data.get('stockx_slug')
                    else "kicksdb_goat" if data.get('goat_id') or data.get('goat_slug') else release.source
                )
                release.source_product_id = data.get('stockx_id') or data.get('goat_id') or release.source_product_id
                release.source_slug = data.get('stockx_slug') or data.get('goat_slug') or release.source_slug
                db.session.commit()

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
    today = date.today()
    page = request.args.get('page', default=1, type=int)
    per_page = 40

    # Get filter/search parameters from the URL
    filter_brand_param = request.args.get('filter_brand')
    filter_month_param = request.args.get('filter_month')
    search_term_param = request.args.get('search_term')

    # Base query: all releases from today onwards
    query = Release.query.options(joinedload(Release.offers), joinedload(Release.prices)).filter(
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

    # The existing grouping logic will now work on the filtered results
    releases_by_month = OrderedDict()
    for release in upcoming_releases:
        month_year_key = release.release_date.strftime('%B %Y')
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
                           pagination_params={k: v for k, v in request.args.to_dict(flat=True).items() if k != 'page'})


def _render_release_detail(release, source=None):
    active_offers = [offer for offer in release.offers if offer.is_active]
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
    avg_resale_label = "Avg 3-Month Resale"
    if release.release_date and release.release_date >= (date.today() - timedelta(days=90)):
        avg_resale_label = "Avg 1-Month Resale"
    avg_resale_price, avg_resale_currency = _average_resale(release.offers, preferred_currency)
    needs_resale_refresh = current_user.is_authenticated and _needs_resale_refresh(release.offers)
    size_bids, size_bids_fetched_at = _get_release_size_bids(release)
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
    sneaker_material = None
    if release.sku:
        sku_values = sku_variants(release.sku)
        sku_filters = [SneakerDB.sku.ilike(value) for value in sku_values]
        sneaker_record = SneakerDB.query.filter(or_(*sku_filters)).first()
        if sneaker_record:
            sneaker_material = sneaker_record.primary_material
    return render_template(
        'release_detail.html',
        title=release.name,
        release=release,
        offer_groups=offer_groups,
        avg_resale_price=avg_resale_price,
        avg_resale_currency=avg_resale_currency,
        avg_resale_label=avg_resale_label,
        needs_resale_refresh=needs_resale_refresh,
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
            Release.query.options(joinedload(Release.offers), joinedload(Release.prices))
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
                Release.query.options(joinedload(Release.offers), joinedload(Release.prices))
                .filter_by(id=release_id)
                .first()
            )
    if "_" in product_key:
        source, source_product_id = product_key.split("_", 1)
        if source and source_product_id:
            source = source.lower()
            return (
                Release.query.options(joinedload(Release.offers), joinedload(Release.prices))
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
        Release.query.options(joinedload(Release.offers), joinedload(Release.prices))
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
    avg_resale_price, avg_resale_currency = _average_resale(release.offers, preferred_currency)
    avg_resale_display = None
    if avg_resale_price is not None and avg_resale_currency:
        avg_resale_display = display_money(
            db.session, avg_resale_price, avg_resale_currency, preferred_currency
        )
    return jsonify({
        'updated': updated,
        'avg_resale_price': float(avg_resale_price) if avg_resale_price is not None else None,
        'avg_resale_currency': avg_resale_currency,
        'avg_resale_display': avg_resale_display.get("display") if avg_resale_display else None,
    })


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

@main_bp.route('/admin/add-release', methods=['GET', 'POST'])
@login_required
@admin_required
def add_release():
    form = ReleaseForm()
    if request.method == 'GET':
        form.retail_currency.data = current_user.preferred_currency or "GBP"
    if form.validate_on_submit():
        final_image_location = None # Will hold the URL or filename

        # --- NEW IMAGE HANDLING LOGIC ---
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
                        flash('There was an error saving the uploaded image.', 'danger')
                else:
                    # This case should be caught by form validation, but it's good to have
                    flash('Invalid file type.', 'warning')
        # --- END OF IMAGE HANDLING LOGIC ---

        new_release = Release(
            name=form.name.data,
            brand=form.brand.data,
            release_date=form.release_date.data,
            retail_price=form.retail_price.data,
            retail_currency=form.retail_currency.data or current_user.preferred_currency or "GBP",
            image_url=final_image_location # Use the final determined image location
        )
        db.session.add(new_release)
        db.session.flush()
        _upsert_release_prices(new_release.id, form)
        db.session.commit()
        flash('New release has been added to the calendar!', 'success')
        return redirect(url_for('main.release_calendar'))

    return render_template('add_release.html', title='Add New Release', form=form)

# Admin Edit Sneaker Release Route

@main_bp.route('/admin/edit-release/<int:release_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_release(release_id):
    # Find the existing release in the database or show a 404 error
    release_to_edit = db.session.get(Release, release_id)
    if not release_to_edit:
        abort(404)

    # For a GET request, pre-populate the form with the release's existing data
    form = ReleaseForm(obj=release_to_edit)
    existing_prices = {
        price.currency: price for price in (release_to_edit.prices or []) if price.region is None
    }
    form.regional_price_gbp.data = existing_prices.get("GBP").price if existing_prices.get("GBP") else None
    form.regional_price_usd.data = existing_prices.get("USD").price if existing_prices.get("USD") else None
    form.regional_price_eur.data = existing_prices.get("EUR").price if existing_prices.get("EUR") else None
    if request.method == 'GET' and not form.retail_currency.data:
        form.retail_currency.data = release_to_edit.retail_currency or current_user.preferred_currency or "GBP"

    # For a POST request, process the submitted form data
    if form.validate_on_submit():
        # Update the existing release object with the new data from the form
        release_to_edit.name = form.name.data
        release_to_edit.brand = form.brand.data
        release_to_edit.release_date = form.release_date.data
        release_to_edit.retail_price = form.retail_price.data
        release_to_edit.retail_currency = form.retail_currency.data or current_user.preferred_currency or "GBP"

        # Image handling logic (copied and adapted from your sneaker edit route)
        if form.image_option.data == 'url' and form.image_url.data:
            release_to_edit.image_url = form.image_url.data.strip()
        elif form.image_option.data == 'upload':
            image_file = form.sneaker_image_file.data
            if image_file and image_file.filename != '':
                # You would add your file saving logic here
                # For now, let's assume we are just updating the URL for simplicity
                pass # Placeholder for file upload logic

        _upsert_release_prices(release_to_edit.id, form)
        db.session.commit()
        flash('Release has been updated!', 'success')
        return redirect(url_for('main.release_calendar'))

    return render_template('edit_release.html', 
                           title='Edit Release', 
                           form=form)

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
    new_button_html = render_template('_wishlist_button.html', release=release)
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
    new_button_html = render_template('_wishlist_button.html', release=release)
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
    months_for_filter = [(f"{y}-{m:02d}", datetime(y, m, 1).strftime('%B %Y')) for y, m in distinct_months_tuples]

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
    preferred_currency = current_user.preferred_currency or "GBP"
    avg_resale_map = {
        release.id: _average_resale(release.offers, preferred_currency)
        for release in wishlist_items_list
    }

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
