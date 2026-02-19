import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_

from models import SneakerDB

# Migration: `flask db upgrade`
# Tests: `python -m pytest tests/test_sneaker_lookup.py`


LOW_CONFIDENCE_SCORE = 70


def normalize_query(query: str) -> str:
    return " ".join(query.split()).strip()


def looks_like_sku(query: str) -> bool:
    if not query:
        return False
    if " " in query:
        return False
    has_digit = any(ch.isdigit() for ch in query)
    return has_digit and len(query) >= 4


def is_stale(record: SneakerDB, max_age_hours: int = 24) -> bool:
    if not record.last_synced_at:
        return True
    return record.last_synced_at < datetime.utcnow() - timedelta(hours=max_age_hours)


def find_local_candidates(db_session, query: str, limit: int = 5) -> List[SneakerDB]:
    normalized = normalize_query(query)
    if not normalized:
        return []

    exact = db_session.query(SneakerDB).filter(SneakerDB.sku.ilike(normalized)).first()
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
    query_sku = normalize_query(query).upper()

    scored: List[Tuple[int, Dict[str, Any]]] = []
    for item in results:
        score = 0
        sku = (item.get("sku") or "").upper()
        model_name = (item.get("model_name") or "").lower()
        colorway = (item.get("colorway") or "").lower()

        if prefer_sku and sku and query_sku == sku:
            score += 100
        elif prefer_sku and sku and query_sku in sku:
            score += 85

        if query_norm and query_norm in model_name:
            score += 40
        if query_norm and query_norm in colorway:
            score += 20

        scored.append((score, item))

    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_item = scored[0]
    return best_item, [item for _, item in scored], best_score


def score_local_record(record: SneakerDB, query: str) -> int:
    query_norm = normalize_query(query).lower()
    query_sku = normalize_query(query).upper()
    score = 0

    if record.sku and query_sku == record.sku.upper():
        score += 100
    elif record.sku and query_sku in record.sku.upper():
        score += 85

    model_name = (record.model_name or record.name or "").lower()
    colorway = (record.colorway or "").lower()

    if query_norm and query_norm in model_name:
        score += 40
    if query_norm and query_norm in colorway:
        score += 20

    return score


def upsert_sneakerdb(db_session, fields: Dict[str, Any]) -> Optional[SneakerDB]:
    sku = fields.get("sku")
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
        "retail_price": float(record.retail_price) if record.retail_price is not None else None,
        "retail_currency": record.retail_currency,
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
) -> Dict[str, Any]:
    normalized = normalize_query(query)
    if not normalized:
        return {"status": "error", "message": "Query is required."}

    local_candidates = find_local_candidates(db_session, normalized)
    if local_candidates:
        scored_locals = [(score_local_record(record, normalized), record) for record in local_candidates]
        scored_locals.sort(key=lambda item: item[0], reverse=True)
        best_score, best_record = scored_locals[0]
        if best_record and not is_stale(best_record, max_age_hours=max_age_hours):
            return {"status": "ok", "source": "cache", "sneaker": serialize_sneaker(best_record)}
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
            return {"status": "pick", "source": "cache", "candidates": candidates}

    stockx_data = client.search_stockx(normalized, include_traits=True)
    stockx_candidates = _extract_stockx_candidates(stockx_data)
    best_stockx, stockx_ranked, stockx_score = choose_best_match(
        stockx_candidates, normalized, prefer_sku=looks_like_sku(normalized)
    )

    if return_candidates and stockx_candidates and not force_best and stockx_score < LOW_CONFIDENCE_SCORE:
        return {
            "status": "pick",
            "source": "kicksdb",
            "candidates": [serialize_candidate(item) for item in stockx_ranked[:5]],
        }

    sku_hint = best_stockx.get("sku") if best_stockx else None
    goat_query = sku_hint or normalized
    goat_data = client.search_goat(goat_query)
    goat_candidates = _extract_goat_candidates(goat_data)
    best_goat, _, _ = choose_best_match(
        goat_candidates, sku_hint or normalized, prefer_sku=bool(sku_hint)
    )

    if best_goat and not best_goat.get("current_lowest_ask_goat"):
        goat_id_or_slug = best_goat.get("goat_slug") or best_goat.get("goat_id")
        if goat_id_or_slug:
            goat_detail = client.get_goat_product(goat_id_or_slug)
            best_goat["current_lowest_ask_goat"] = _extract_goat_lowest_ask(goat_detail)

    fields = _build_canonical_fields(best_stockx, best_goat, normalized)
    record = upsert_sneakerdb(db_session, fields)
    if record:
        return {"status": "ok", "source": "kicksdb", "sneaker": serialize_sneaker(record)}

    return {"status": "not_found", "message": "No sneaker match found."}


def _build_canonical_fields(
    stockx: Optional[Dict[str, Any]],
    goat: Optional[Dict[str, Any]],
    query: str,
) -> Dict[str, Any]:
    model_name = (stockx or {}).get("model_name") or (goat or {}).get("model_name") or query
    fields = {
        "sku": (stockx or {}).get("sku") or (goat or {}).get("sku"),
        "model_name": model_name,
        "name": model_name,
        "brand": (stockx or {}).get("brand") or (goat or {}).get("brand"),
        "colorway": (stockx or {}).get("colorway") or (goat or {}).get("colorway"),
        "retail_price": (stockx or {}).get("retail_price") or (goat or {}).get("retail_price"),
        "retail_currency": (stockx or {}).get("retail_currency") or (goat or {}).get("retail_currency"),
        "stockx_id": (stockx or {}).get("stockx_id"),
        "stockx_slug": (stockx or {}).get("stockx_slug"),
        "goat_id": (goat or {}).get("goat_id"),
        "goat_slug": (goat or {}).get("goat_slug"),
        "current_lowest_ask_stockx": (stockx or {}).get("current_lowest_ask_stockx"),
        "current_lowest_ask_goat": (goat or {}).get("current_lowest_ask_goat"),
        "image_url": (stockx or {}).get("image_url") or (goat or {}).get("image_url"),
        "last_synced_at": datetime.utcnow(),
    }
    return fields


def _extract_stockx_candidates(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = _extract_items_list(data)
    candidates: List[Dict[str, Any]] = []
    for item in items:
        traits = item.get("traits") or item.get("productTraits") or []
        model_name = item.get("name") or item.get("title") or item.get("model_name")
        sku = item.get("sku") or _extract_trait_value(traits, "Style") or _extract_trait_value(traits, "SKU")
        candidate = {
            "sku": sku,
            "model_name": model_name,
            "colorway": item.get("colorway"),
            "brand": _extract_brand_from_stockx(item, model_name),
            "retail_price": _to_decimal(item.get("retailPrice") or _extract_trait_value(traits, "Retail Price")),
            "retail_currency": _extract_trait_value(traits, "Retail Price Currency"),
            "stockx_id": item.get("id") or item.get("product_id"),
            "stockx_slug": item.get("slug"),
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
        model_name = item.get("name") or item.get("title")
        candidate = {
            "sku": item.get("sku") or item.get("style_id") or item.get("styleId"),
            "model_name": model_name,
            "colorway": item.get("colorway"),
            "brand": _extract_brand_from_goat(item, model_name),
            "retail_price": _to_decimal(item.get("retail_price") or item.get("retailPrice")),
            "retail_currency": item.get("retail_currency"),
            "goat_id": item.get("id"),
            "goat_slug": item.get("slug"),
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


def _extract_items_list(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return data
    for key in ("results", "data", "products", "items"):
        value = data.get(key)
        if isinstance(value, list):
            return value
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
