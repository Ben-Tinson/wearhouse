from datetime import date as date_type
from typing import Any, Dict, Iterable, List, Optional, Tuple

from utils.money import display_money

VALID_REGIONS = {"US", "UK", "EU"}
SOURCE_DEFAULT_REGION = {
    "kicksdb_stockx": "US",
    "kicksdb_goat": "US",
}


def resolve_preferred_region(user) -> Optional[str]:
    if not user:
        return None
    region = getattr(user, "preferred_region", None)
    if not region:
        return None
    region = str(region).upper()
    return region if region in VALID_REGIONS else None


def resolve_preferred_currency(user) -> str:
    if not user:
        return "GBP"
    currency = getattr(user, "preferred_currency", None)
    return currency or "GBP"


def resolve_release_display(release, db_session, user=None) -> Dict[str, Any]:
    preferred_region = resolve_preferred_region(user)
    preferred_currency = resolve_preferred_currency(user)
    single_region = _resolve_single_region(release)
    canonical_region = single_region or preferred_region

    display_date, display_time, display_tz, region_source = _resolve_release_datetime(
        release, canonical_region
    )
    price_info = _resolve_release_price(
        release,
        db_session,
        canonical_region,
        preferred_currency,
    )
    offers = _resolve_offers(release, canonical_region)

    market_context_message = _resolve_market_context_message(
        preferred_region,
        region_source,
        price_info.get("region"),
        single_region,
    )

    show_single_region_note = bool(single_region and single_region != preferred_region)

    return {
        "preferred_region": preferred_region,
        "preferred_currency": preferred_currency,
        "canonical_region": canonical_region,
        "single_region_only": single_region is not None,
        "region_context_label": (
            f"Only {single_region} release data currently available" if show_single_region_note else None
        ),
        "market_context_message": market_context_message,
        "release_date": display_date,
        "release_time": display_time,
        "release_timezone": display_tz,
        "release_region": region_source,
        "price": price_info.get("price"),
        "price_currency": price_info.get("currency"),
        "price_display": price_info.get("display"),
        "price_source": price_info.get("source"),
        "price_region": price_info.get("region"),
        "offers": offers,
    }


def build_release_display_map(
    releases: Iterable, db_session, user=None
) -> Dict[int, Dict[str, Any]]:
    display_map: Dict[int, Dict[str, Any]] = {}
    for release in releases:
        if not release or release.id is None:
            continue
        display_map[release.id] = resolve_release_display(release, db_session, user=user)
    return display_map


def _resolve_release_datetime(
    release, preferred_region: Optional[str]
) -> Tuple[Optional[date_type], Optional[Any], Optional[str], Optional[str]]:
    regions = list(getattr(release, "regions", []) or [])
    inferred_region = _infer_source_region(release)

    if preferred_region:
        matched = next(
            (region for region in regions if (region.region or "").upper() == preferred_region),
            None,
        )
        if matched:
            return matched.release_date, matched.release_time, matched.timezone, matched.region

    if getattr(release, "release_date", None):
        return release.release_date, None, None, inferred_region

    if regions:
        earliest = min(regions, key=lambda item: item.release_date)
        return earliest.release_date, earliest.release_time, earliest.timezone, earliest.region

    return None, None, None, None


def _resolve_release_price(
    release,
    db_session,
    preferred_region: Optional[str],
    preferred_currency: Optional[str],
) -> Dict[str, Any]:
    prices = list(getattr(release, "prices", []) or [])
    inferred_region = _infer_source_region(release)

    if preferred_region and preferred_currency:
        exact = next(
            (
                price
                for price in prices
                if (price.region or "").upper() == preferred_region
                and (price.currency or "").upper() == preferred_currency
            ),
            None,
        )
        if exact:
            return _price_payload(
                exact.price,
                exact.currency,
                "region_currency",
                exact.region,
                db_session,
                preferred_currency,
            )

    if preferred_region:
        regional_prices = [
            price for price in prices if (price.region or "").upper() == preferred_region
        ]
        if regional_prices:
            selected = _choose_price(regional_prices, preferred_currency)
            return _price_payload(
                selected.price,
                selected.currency,
                "region_only",
                selected.region,
                db_session,
                preferred_currency,
            )

    if getattr(release, "retail_price", None) is not None and getattr(
        release, "retail_currency", None
    ):
        return _price_payload(
            release.retail_price,
            release.retail_currency,
            "base",
            inferred_region,
            db_session,
            preferred_currency,
        )

    if prices:
        selected = _choose_price(prices, preferred_currency)
        return _price_payload(
            selected.price,
            selected.currency,
            "fallback",
            selected.region,
            db_session,
            preferred_currency,
        )

    return {"price": None, "currency": None, "display": None, "source": None}


def _choose_price(prices: List, preferred_currency: Optional[str]):
    if preferred_currency:
        for price in prices:
            if (price.currency or "").upper() == preferred_currency:
                return price
    return sorted(prices, key=lambda item: ((item.currency or ""), (item.region or "")))[0]


def _price_payload(
    price_value: Any,
    currency: Optional[str],
    source: str,
    region: Optional[str],
    db_session,
    preferred_currency: Optional[str],
) -> Dict[str, Any]:
    display = display_money(db_session, price_value, currency, None)
    return {
        "price": price_value,
        "currency": currency,
        "display": display,
        "source": source,
        "region": str(region).upper() if region else None,
    }


def _resolve_offers(release, preferred_region: Optional[str]) -> List:
    offers = list(getattr(release, "offers", []) or [])
    active = [offer for offer in offers if offer.is_active]
    if not active:
        return []

    if preferred_region:
        region_offers = [
            offer
            for offer in active
            if (offer.region or "").upper() == preferred_region
        ]
        if region_offers:
            return region_offers

    global_offers = [offer for offer in active if not offer.region]
    if global_offers:
        return global_offers

    return active


def _resolve_single_region(release) -> Optional[str]:
    regions = list(getattr(release, "regions", []) or [])
    prices = list(getattr(release, "prices", []) or [])

    region_values = set()
    for region in regions:
        value = (region.region or "").upper()
        if value in VALID_REGIONS:
            region_values.add(value)
    for price in prices:
        value = (price.region or "").upper()
        if value in VALID_REGIONS:
            region_values.add(value)

    if not region_values:
        inferred_region = _infer_source_region(release)
        has_base_data = bool(
            getattr(release, "release_date", None)
            or getattr(release, "retail_price", None) is not None
        )
        if inferred_region and has_base_data:
            region_values.add(inferred_region)

    if len(region_values) == 1:
        return next(iter(region_values))
    return None


def _infer_source_region(release) -> Optional[str]:
    source = getattr(release, "source", None)
    if not source:
        return None
    return SOURCE_DEFAULT_REGION.get(str(source).lower())


def _resolve_market_context_message(
    preferred_region: Optional[str],
    release_region: Optional[str],
    price_region: Optional[str],
    single_region: Optional[str],
) -> Optional[str]:
    if single_region:
        if preferred_region and preferred_region == single_region:
            return None
        return f"Only {single_region} release data currently available"
    if (
        preferred_region
        and release_region == preferred_region
        and price_region == preferred_region
    ):
        return f"Showing {preferred_region} release data"
    return None
