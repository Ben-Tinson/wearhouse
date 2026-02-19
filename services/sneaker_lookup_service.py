import json
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_

from models import SneakerDB
from utils.sku import normalize_sku, sku_variants
from services.materials_extractor import extract_materials

# Migration: `flask db upgrade`
# Tests: `python -m pytest tests/test_sneaker_lookup.py`


LOW_CONFIDENCE_SCORE = 70
MATERIALS_TTL_DAYS = 90


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1]
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _load_materials_list(value: Optional[str]) -> List[str]:
    if not value:
        return []
    try:
        data = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _materials_fresh(record: SneakerDB, now: datetime) -> bool:
    if record.materials_updated_at is None:
        return False
    if record.materials_json is None:
        return False
    if record.materials_updated_at < now - timedelta(days=MATERIALS_TTL_DAYS):
        return False
    if record.source_updated_at and record.materials_updated_at < record.source_updated_at:
        return False
    if record.description_last_seen and record.materials_updated_at < record.description_last_seen:
        return False
    return True


def _apply_materials(record: SneakerDB, result: Dict[str, object], now: datetime) -> None:
    record.primary_material = result.get("primary_material")
    record.materials_json = json.dumps(result.get("materials") or [])
    record.materials_source = result.get("source")
    record.materials_confidence = result.get("confidence")
    record.materials_updated_at = now


def _refresh_materials_from_description(
    db_session,
    record: SneakerDB,
    source_label: Optional[str] = None,
) -> bool:
    if not record.description:
        return False
    now = datetime.utcnow()
    if _materials_fresh(record, now):
        return False
    result = extract_materials(
        record.description,
        source=source_label or record.materials_source or "cached_description",
    )
    _apply_materials(record, result, now)
    record.description_last_seen = now
    db_session.add(record)
    db_session.commit()
    return True


def normalize_query(query: str) -> str:
    return " ".join(query.split()).strip()


def looks_like_sku(query: str) -> bool:
    if not query:
        return False
    cleaned = query.strip()
    if not cleaned:
        return False
    if not any(ch.isdigit() for ch in cleaned):
        return False
    parts = re.split(r"[\s-]+", cleaned)
    if len(parts) > 1:
        return all(any(ch.isdigit() for ch in part) for part in parts if part)
    return len(cleaned) >= 4


def is_stale(record: SneakerDB, max_age_hours: int = 24) -> bool:
    if not record.last_synced_at:
        return True
    return record.last_synced_at < datetime.utcnow() - timedelta(hours=max_age_hours)


def is_high_confidence_match(record: SneakerDB, query: str, score: int) -> bool:
    query_norm = normalize_query(query).lower()
    model_name = (record.model_name or record.name or "").lower()
    if query_norm and query_norm == model_name:
        return True
    return score >= 90


def find_local_candidates(db_session, query: str, limit: int = 5) -> List[SneakerDB]:
    normalized = normalize_query(query)
    if not normalized:
        return []

    sku_filters = [SneakerDB.sku.ilike(value) for value in sku_variants(normalized)]
    if not sku_filters:
        sku_filters = [SneakerDB.sku.ilike(normalized)]

    exact = db_session.query(SneakerDB).filter(or_(*sku_filters)).first()
    if exact:
        return [exact]

    pattern = f"%{normalized}%"
    return (
        db_session.query(SneakerDB)
        .filter(
            or_(
                SneakerDB.model_name.ilike(pattern),
                SneakerDB.name.ilike(pattern),
                SneakerDB.colorway.ilike(pattern),
                SneakerDB.sku.ilike(pattern),
            )
        )
        .limit(limit)
        .all()
    )


def choose_best_match(
    results: List[Dict[str, Any]],
    query: str,
    prefer_sku: bool = True,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], int]:
    if not results:
        return None, [], 0

    query_norm = normalize_query(query).lower()
    query_sku = normalize_sku(query) or ""
    if prefer_sku and query_sku:
        exact_matches = [
            item for item in results if (normalize_sku(item.get("sku")) or "") == query_sku
        ]
        if exact_matches:
            results = exact_matches

    scored: List[Tuple[int, Dict[str, Any]]] = []
    for item in results:
        score = 0
        sku = normalize_sku(item.get("sku")) or ""
        model_name = (item.get("model_name") or "").lower()
        colorway = (item.get("colorway") or "").lower()
        slug = item.get("stockx_slug") or item.get("goat_slug") or item.get("slug")

        if prefer_sku and sku and query_sku == sku:
            score += 100
        elif prefer_sku and sku and query_sku in sku:
            score += 85

        if query_norm and query_norm in model_name:
            score += 40
        if query_norm and query_norm in colorway:
            score += 20
        if _is_auction_slug(slug):
            score -= 200

        scored.append((score, item))

    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_item = scored[0]
    return best_item, [item for _, item in scored], best_score


def _is_auction_slug(slug: Optional[str]) -> bool:
    if not slug:
        return False
    return "-auction" in slug.lower()


def _has_auction_slug(record: SneakerDB) -> bool:
    return _is_auction_slug(record.stockx_slug) or _is_auction_slug(record.goat_slug)


def score_local_record(record: SneakerDB, query: str) -> int:
    query_norm = normalize_query(query).lower()
    query_sku = normalize_sku(query) or ""
    score = 0

    record_sku = normalize_sku(record.sku) or ""
    if record_sku and query_sku == record_sku:
        score += 100
    elif record_sku and query_sku in record_sku:
        score += 85

    model_name = (record.model_name or record.name or "").lower()
    colorway = (record.colorway or "").lower()

    if query_norm and query_norm in model_name:
        score += 40
    if query_norm and query_norm in colorway:
        score += 20

    return score


def upsert_sneakerdb(db_session, fields: Dict[str, Any]) -> Optional[SneakerDB]:
    sku = normalize_sku(fields.get("sku"))
    if not sku:
        return None

    record = db_session.query(SneakerDB).filter_by(sku=sku).first()
    if not record:
        record = SneakerDB(sku=sku)

    for key, value in fields.items():
        if value is not None:
            setattr(record, key, value)

    if fields.get("model_name") and not fields.get("name"):
        record.name = fields["model_name"]

    db_session.add(record)
    db_session.commit()
    return record


def serialize_sneaker(record: SneakerDB) -> Dict[str, Any]:
    return {
        "sku": record.sku,
        "brand": record.brand,
        "model_name": record.model_name or record.name,
        "colorway": record.colorway,
        "release_date": record.release_date.isoformat() if record.release_date else None,
        "retail_price": float(record.retail_price) if record.retail_price is not None else None,
        "retail_currency": record.retail_currency,
        "primary_material": record.primary_material,
        "materials": _load_materials_list(record.materials_json),
        "materials_confidence": record.materials_confidence,
        "materials_source": record.materials_source,
        "current_lowest_ask_stockx": float(record.current_lowest_ask_stockx)
        if record.current_lowest_ask_stockx is not None
        else None,
        "current_lowest_ask_goat": float(record.current_lowest_ask_goat)
        if record.current_lowest_ask_goat is not None
        else None,
        "stockx_id": record.stockx_id,
        "stockx_slug": record.stockx_slug,
        "goat_id": record.goat_id,
        "goat_slug": record.goat_slug,
        "image_url": record.image_url,
        "last_synced_at": record.last_synced_at.isoformat() if record.last_synced_at else None,
    }


def serialize_candidate(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "sku": item.get("sku"),
        "brand": item.get("brand"),
        "model_name": item.get("model_name"),
        "colorway": item.get("colorway"),
        "image_url": item.get("image_url"),
        "stockx_id": item.get("stockx_id"),
        "stockx_slug": item.get("stockx_slug"),
    }


def lookup_or_fetch_sneaker(
    query: str,
    db_session,
    client,
    max_age_hours: int = 24,
    force_best: bool = False,
    return_candidates: bool = False,
    mode: str = "lite",
    force_refresh: bool = False,
) -> Dict[str, Any]:
    normalized = normalize_query(query)
    if not normalized:
        return {"status": "error", "message": "Query is required."}

    cache_status = "cache_miss"
    sku_filters = [SneakerDB.sku.ilike(value) for value in sku_variants(normalized)]
    if not sku_filters:
        sku_filters = [SneakerDB.sku.ilike(normalized)]
    cached_record = db_session.query(SneakerDB).filter(or_(*sku_filters)).first()
    force_refresh = force_refresh and looks_like_sku(normalized)
    if cached_record:
        if not force_refresh:
            has_auction_slug = _has_auction_slug(cached_record)
            if not is_stale(cached_record, max_age_hours=max_age_hours) and not has_auction_slug:
                _refresh_materials_from_description(db_session, cached_record)
                return {
                    "status": "ok",
                    "source": "cache",
                    "cache_status": "cache_hit_exact",
                    "sneaker": serialize_sneaker(cached_record),
                }
            cache_status = "cache_stale"
        else:
            cache_status = "cache_forced"

    local_candidates = [] if force_refresh else find_local_candidates(db_session, normalized)
    if local_candidates:
        scored_locals = [(score_local_record(record, normalized), record) for record in local_candidates]
        scored_locals.sort(key=lambda item: item[0], reverse=True)
        best_score, best_record = scored_locals[0]
        if best_record and not is_stale(best_record, max_age_hours=max_age_hours) and not _has_auction_slug(best_record):
            _refresh_materials_from_description(db_session, best_record)
            if is_high_confidence_match(best_record, normalized, best_score):
                return {
                    "status": "ok",
                    "source": "cache",
                    "cache_status": "cache_hit_confident",
                    "sneaker": serialize_sneaker(best_record),
                }
        if return_candidates and not force_best and best_score < LOW_CONFIDENCE_SCORE:
            candidates = [
                serialize_candidate(
                    {
                        "sku": record.sku,
                        "brand": record.brand,
                        "model_name": record.model_name or record.name,
                        "colorway": record.colorway,
                        "image_url": record.image_url,
                    }
                )
                for _, record in scored_locals[:5]
            ]
            return {"status": "pick", "source": "cache", "cache_status": cache_status, "candidates": candidates}
        if cache_status == "cache_miss" and best_record:
            cached_record = best_record

    max_requests = 4 if mode == "full" else 2

    stockx_data = client.search_stockx(normalized, include_traits=True)
    stockx_candidates = _extract_stockx_candidates(stockx_data)
    best_stockx, stockx_ranked, stockx_score = choose_best_match(
        stockx_candidates, normalized, prefer_sku=looks_like_sku(normalized)
    )

    if return_candidates and stockx_candidates and not force_best and stockx_score < LOW_CONFIDENCE_SCORE:
        return {
            "status": "pick",
            "source": "kicksdb",
            "cache_status": cache_status,
            "candidates": [serialize_candidate(item) for item in stockx_ranked[:5]],
        }

    sku_hint = best_stockx.get("sku") if best_stockx else None
    needs_goat = _should_call_goat(
        best_stockx,
        cached_record,
        query=normalized,
        looks_like_sku_query=looks_like_sku(normalized),
    )

    best_goat = None
    if needs_goat and client.request_count < max_requests:
        goat_query = sku_hint or normalized
        goat_data = client.search_goat(goat_query)
        goat_candidates = _extract_goat_candidates(goat_data)
        best_goat, _, _ = choose_best_match(
            goat_candidates, sku_hint or normalized, prefer_sku=bool(sku_hint)
        )

        if mode == "full" and best_goat and not best_goat.get("current_lowest_ask_goat"):
            if client.request_count < max_requests:
                goat_id_or_slug = best_goat.get("goat_slug") or best_goat.get("goat_id")
                if goat_id_or_slug:
                    goat_detail = client.get_goat_product(goat_id_or_slug)
                    best_goat["current_lowest_ask_goat"] = _extract_goat_lowest_ask(goat_detail)

    fields = _build_canonical_fields(best_stockx, best_goat, normalized, cached_record)
    now = datetime.utcnow()
    description_text = fields.get("description")
    description_source = None
    source_updated_at = fields.get("source_updated_at")
    if best_stockx and best_stockx.get("description"):
        description_source = "stockx_description"
    elif best_goat and best_goat.get("description"):
        description_source = "goat_description"
    if description_text and not description_source:
        description_source = "cached_description"
    if description_text:
        fields["description_last_seen"] = now
        needs_materials = True
        if cached_record and _materials_fresh(cached_record, now):
            needs_materials = False
        if cached_record and cached_record.materials_updated_at and source_updated_at:
            if source_updated_at > cached_record.materials_updated_at:
                needs_materials = True
        if needs_materials:
            materials_result = extract_materials(description_text, source=description_source)
            fields["primary_material"] = materials_result.get("primary_material")
            fields["materials_json"] = json.dumps(materials_result.get("materials") or [])
            fields["materials_source"] = materials_result.get("source")
            fields["materials_confidence"] = materials_result.get("confidence")
            fields["materials_updated_at"] = now
    if cached_record:
        fields["primary_material"] = fields.get("primary_material") or cached_record.primary_material
        fields["materials_json"] = fields.get("materials_json") or cached_record.materials_json
        fields["materials_source"] = fields.get("materials_source") or cached_record.materials_source
        fields["materials_confidence"] = fields.get("materials_confidence") or cached_record.materials_confidence
        fields["materials_updated_at"] = fields.get("materials_updated_at") or cached_record.materials_updated_at
        fields["description_last_seen"] = fields.get("description_last_seen") or cached_record.description_last_seen
    record = upsert_sneakerdb(db_session, fields)
    if record:
        return {
            "status": "ok",
            "source": "kicksdb",
            "cache_status": cache_status,
            "sneaker": serialize_sneaker(record),
        }

    return {"status": "not_found", "cache_status": cache_status, "message": "No sneaker match found."}


def _build_canonical_fields(
    stockx: Optional[Dict[str, Any]],
    goat: Optional[Dict[str, Any]],
    query: str,
    cached_record: Optional[SneakerDB] = None,
) -> Dict[str, Any]:
    model_name = (stockx or {}).get("model_name") or (goat or {}).get("model_name") or query
    fields = {
        "sku": normalize_sku((stockx or {}).get("sku") or (goat or {}).get("sku")),
        "model_name": model_name,
        "name": model_name,
        "brand": (stockx or {}).get("brand") or (goat or {}).get("brand"),
        "colorway": (stockx or {}).get("colorway") or (goat or {}).get("colorway"),
        "description": (stockx or {}).get("description") or (goat or {}).get("description"),
        "release_date": (stockx or {}).get("release_date") or (goat or {}).get("release_date"),
        "retail_price": (stockx or {}).get("retail_price") or (goat or {}).get("retail_price"),
        "retail_currency": (stockx or {}).get("retail_currency") or (goat or {}).get("retail_currency"),
        "stockx_id": (stockx or {}).get("stockx_id"),
        "stockx_slug": (stockx or {}).get("stockx_slug"),
        "goat_id": (goat or {}).get("goat_id"),
        "goat_slug": (goat or {}).get("goat_slug"),
        "source_updated_at": (stockx or {}).get("source_updated_at") or (goat or {}).get("source_updated_at"),
        "current_lowest_ask_stockx": (stockx or {}).get("current_lowest_ask_stockx"),
        "current_lowest_ask_goat": (goat or {}).get("current_lowest_ask_goat"),
        "image_url": (stockx or {}).get("image_url") or (goat or {}).get("image_url"),
        "last_synced_at": datetime.utcnow(),
    }
    if cached_record:
        fields["sku"] = fields["sku"] or cached_record.sku
        fields["brand"] = fields["brand"] or cached_record.brand
        fields["colorway"] = fields["colorway"] or cached_record.colorway
        fields["description"] = fields["description"] or cached_record.description
        fields["release_date"] = fields["release_date"] or cached_record.release_date
        fields["retail_price"] = fields["retail_price"] or cached_record.retail_price
        fields["retail_currency"] = fields["retail_currency"] or cached_record.retail_currency
        fields["stockx_id"] = fields["stockx_id"] or cached_record.stockx_id
        fields["stockx_slug"] = fields["stockx_slug"] or cached_record.stockx_slug
        fields["goat_id"] = fields["goat_id"] or cached_record.goat_id
        fields["goat_slug"] = fields["goat_slug"] or cached_record.goat_slug
        fields["source_updated_at"] = fields["source_updated_at"] or cached_record.source_updated_at
        fields["current_lowest_ask_stockx"] = fields["current_lowest_ask_stockx"] or cached_record.current_lowest_ask_stockx
        fields["current_lowest_ask_goat"] = fields["current_lowest_ask_goat"] or cached_record.current_lowest_ask_goat
        fields["image_url"] = fields["image_url"] or cached_record.image_url
    return fields


def _extract_stockx_candidates(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = _extract_items_list(data)
    candidates: List[Dict[str, Any]] = []
    for item in items:
        if _is_auction_slug(item.get("slug")):
            continue
        traits = item.get("traits") or item.get("productTraits") or []
        model_name = item.get("name") or item.get("title") or item.get("model_name")
        colorway = (
            _extract_trait_value(traits, "Colorway")
            or _extract_trait_value(traits, "Color")
            or item.get("colorway")
        )
        sku = item.get("sku") or _extract_trait_value(traits, "Style") or _extract_trait_value(traits, "SKU")
        candidate = {
            "sku": sku,
            "model_name": model_name,
            "colorway": colorway,
            "brand": _extract_brand_from_stockx(item, model_name),
            "description": item.get("description") or item.get("short_description"),
            "release_date": _parse_release_date_string(item.get("release_date") or item.get("releaseDate")),
            "retail_price": _to_decimal(item.get("retailPrice") or _extract_trait_value(traits, "Retail Price")),
            "retail_currency": _extract_trait_value(traits, "Retail Price Currency"),
            "stockx_id": item.get("id") or item.get("product_id"),
            "stockx_slug": item.get("slug"),
            "source_updated_at": _parse_datetime(item.get("updated_at")),
            "current_lowest_ask_stockx": _to_decimal(
                item.get("lowestAsk") or item.get("lowest_ask") or item.get("market", {}).get("lowestAsk")
            ),
            "image_url": _extract_image_url(item),
        }
        candidates.append(candidate)
    return candidates


def _extract_goat_candidates(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = _extract_items_list(data)
    candidates: List[Dict[str, Any]] = []
    for item in items:
        if _is_auction_slug(item.get("slug")):
            continue
        model_name = item.get("name") or item.get("title")
        colorway = item.get("colorway") or item.get("color")
        candidate = {
            "sku": item.get("sku") or item.get("style_id") or item.get("styleId"),
            "model_name": model_name,
            "colorway": colorway,
            "brand": _extract_brand_from_goat(item, model_name),
            "description": item.get("description") or item.get("short_description"),
            "release_date": _parse_release_date_string(item.get("release_date") or item.get("releaseDate")),
            "retail_price": _to_decimal(item.get("retail_price") or item.get("retailPrice")),
            "retail_currency": item.get("retail_currency"),
            "goat_id": item.get("id"),
            "goat_slug": item.get("slug"),
            "source_updated_at": _parse_datetime(item.get("updated_at")),
            "current_lowest_ask_goat": _extract_goat_lowest_ask(item),
            "image_url": _extract_image_url(item),
        }
        candidates.append(candidate)
    return candidates


def _extract_goat_lowest_ask(item: Dict[str, Any]) -> Optional[Decimal]:
    lowest = item.get("lowest_ask") or item.get("lowestAsk")
    if lowest is not None:
        return _to_decimal(lowest)

    variants = item.get("variants") or []
    prices = []
    for variant in variants:
        value = variant.get("lowest_ask") or variant.get("lowestAsk")
        if value is not None:
            price = _to_decimal(value)
            if price is not None:
                prices.append(price)
    return min(prices) if prices else None


def _parse_release_date_string(value: Any) -> Optional[datetime.date]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.isdigit() and len(raw) == 8:
        try:
            return datetime.strptime(raw, "%Y%m%d").date()
        except ValueError:
            return None
    if "T" in raw:
        raw = raw.split("T", 1)[0]
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _extract_items_list(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return data
    for key in ("results", "data", "products", "items"):
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for inner_key in ("products", "items", "results"):
                inner_value = value.get(inner_key)
                if isinstance(inner_value, list):
                    return inner_value
    return []


def _extract_trait_value(traits: Any, trait_name: str) -> Optional[str]:
    if isinstance(traits, dict):
        return traits.get(trait_name)
    if isinstance(traits, list):
        for trait in traits:
            name = trait.get("name") or trait.get("trait")
            if name and name.lower() == trait_name.lower():
                return trait.get("value") or trait.get("displayValue")
    return None


def _extract_image_url(item: Dict[str, Any]) -> Optional[str]:
    if item.get("image_url"):
        return item["image_url"]
    image = item.get("image") or {}
    if isinstance(image, dict):
        return image.get("original") or image.get("primary") or image.get("thumbnail")
    return item.get("image") if isinstance(item.get("image"), str) else None


def _extract_brand_from_stockx(item: Dict[str, Any], model_name: Optional[str]) -> Optional[str]:
    return item.get("brand") or item.get("brandName") or _extract_brand_from_name(model_name)


def _extract_brand_from_goat(item: Dict[str, Any], model_name: Optional[str]) -> Optional[str]:
    return item.get("brand") or item.get("brand_name") or _extract_brand_from_name(model_name)


def _extract_brand_from_name(model_name: Optional[str]) -> Optional[str]:
    if not model_name:
        return None
    normalized = model_name.strip().lower()
    if normalized.startswith("air jordan") or normalized.startswith("jordan"):
        return "Jordan"
    known_brands = {
        "nike": "Nike",
        "adidas": "Adidas",
        "new balance": "New Balance",
        "puma": "Puma",
        "asics": "ASICS",
        "vans": "Vans",
        "converse": "Converse",
        "reebok": "Reebok",
        "saucony": "Saucony",
        "salomon": "Salomon",
    }
    for key, brand in known_brands.items():
        if normalized.startswith(key):
            return brand
    first_token = model_name.split(" ", 1)[0]
    return first_token if first_token else None


def _should_call_goat(
    stockx: Optional[Dict[str, Any]],
    cached_record: Optional[SneakerDB],
    query: str,
    looks_like_sku_query: bool,
) -> bool:
    if not (stockx or looks_like_sku_query):
        return False

    sku_hint = (stockx or {}).get("sku") if stockx else None
    if not sku_hint and not looks_like_sku_query:
        return False

    def has_value(value):
        return value is not None and value != ""

    cached_colorway = cached_record.colorway if cached_record else None
    cached_image = cached_record.image_url if cached_record else None
    cached_goat = cached_record.current_lowest_ask_goat if cached_record else None

    missing_colorway = not has_value((stockx or {}).get("colorway")) and not has_value(cached_colorway)
    missing_image = not has_value((stockx or {}).get("image_url")) and not has_value(cached_image)
    missing_goat = not has_value(cached_goat)

    return missing_colorway or missing_image or missing_goat


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            cleaned = re.sub(r"[^\d.]", "", value)
            if not cleaned:
                return None
            return Decimal(cleaned)
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
