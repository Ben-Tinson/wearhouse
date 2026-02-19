from datetime import date, datetime, timedelta
import logging
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from models import Release, AffiliateOffer
from sqlalchemy.orm import joinedload
from utils.sku import normalize_sku

logger = logging.getLogger(__name__)


def ingest_kicksdb_releases(
    db_session,
    client,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    mode: str = "lite",
    per_page: int = 100,
    max_pages_stockx: int = 5,
    max_pages_goat: int = 5,
    max_total_requests: int = 10,
    backfill_goat: bool = True,
    backfill_threshold: int = 40,
    max_extra_calls: int = 20,
    commit_per_page: bool = True,
    refresh_pricing: bool = False,
    pricing_max_calls: int = 20,
    pricing_force: bool = False,
) -> Dict[str, Any]:
    start_date = start_date or (date.today() - timedelta(days=30))
    end_date = end_date or (date.today() + timedelta(days=90))

    stockx_stats = _ingest_stockx_pass(
        db_session=db_session,
        client=client,
        start_date=start_date,
        end_date=end_date,
        mode=mode,
        per_page=per_page,
        max_pages=max_pages_stockx,
        max_total_requests=max_total_requests,
        max_extra_calls=max_extra_calls,
        commit_per_page=commit_per_page,
    )

    count_stockx_in_window = _count_releases_in_window(db_session, start_date, end_date)
    goat_stats = {
        "goat_created": 0,
        "goat_updated": 0,
        "goat_skipped_non_sneakers": 0,
        "goat_skipped_missing_release_date": 0,
        "goat_skipped_out_of_window": 0,
        "goat_deduped": 0,
        "goat_pages_fetched": 0,
        "goat_stop_reason": None,
        "goat_earliest_release_date": None,
        "goat_latest_release_date": None,
    }

    if backfill_goat and count_stockx_in_window < backfill_threshold and client.request_count < max_total_requests:
        goat_stats = _ingest_goat_pass(
            db_session=db_session,
            client=client,
            start_date=start_date,
            end_date=end_date,
            per_page=per_page,
            max_pages=max_pages_goat,
            max_total_requests=max_total_requests,
            commit_per_page=commit_per_page,
        )

    pricing_stats = {
        "pricing_calls_used": 0,
        "pricing_offers_updated": 0,
        "pricing_offers_skipped": 0,
    }
    if refresh_pricing and pricing_max_calls > 0:
        pricing_stats = _refresh_aftermarket_prices(
            db_session=db_session,
            client=client,
            start_date=start_date,
            end_date=end_date,
            max_calls=pricing_max_calls,
            force_refresh=pricing_force,
        )

    if not commit_per_page:
        _safe_commit(db_session, "final_commit")

    return {
        "pages_fetched": stockx_stats["pages_fetched"],
        "items_upserted": stockx_stats["items_upserted"],
        "items_updated": stockx_stats["items_updated"],
        "items_created": stockx_stats["items_created"],
        "skipped_non_sneakers": stockx_stats["skipped_non_sneakers"],
        "skipped_missing_release_date": stockx_stats["skipped_missing_release_date"],
        "skipped_out_of_window": stockx_stats["skipped_out_of_window"],
        "earliest_release_date": stockx_stats["earliest_release_date"],
        "latest_release_date": stockx_stats["latest_release_date"],
        "stop_reason": stockx_stats["stop_reason"],
        "stockx_requests_used": stockx_stats["requests_used"],
        "goat_requests_used": goat_stats.get("goat_requests_used", 0),
        "goat_created": goat_stats["goat_created"],
        "goat_updated": goat_stats["goat_updated"],
        "goat_skipped_non_sneakers": goat_stats["goat_skipped_non_sneakers"],
        "goat_skipped_missing_release_date": goat_stats["goat_skipped_missing_release_date"],
        "goat_skipped_out_of_window": goat_stats["goat_skipped_out_of_window"],
        "goat_deduped": goat_stats["goat_deduped"],
        "goat_pages_fetched": goat_stats["goat_pages_fetched"],
        "goat_stop_reason": goat_stats["goat_stop_reason"],
        "goat_earliest_release_date": goat_stats["goat_earliest_release_date"],
        "goat_latest_release_date": goat_stats["goat_latest_release_date"],
        "mode": mode,
        "total_kicks_requests": client.request_count,
        "endpoints_hit": client.endpoints_hit,
        "extra_calls_used": stockx_stats["extra_calls_used"],
        "pricing_calls_used": pricing_stats["pricing_calls_used"],
        "pricing_offers_updated": pricing_stats["pricing_offers_updated"],
        "pricing_offers_skipped": pricing_stats["pricing_offers_skipped"],
    }


def _ingest_stockx_pass(
    db_session,
    client,
    start_date: date,
    end_date: date,
    mode: str,
    per_page: int,
    max_pages: int,
    max_total_requests: int,
    max_extra_calls: int,
    commit_per_page: bool,
) -> Dict[str, Any]:
    page = 1
    items_created = 0
    items_updated = 0
    items_upserted = 0
    pages_fetched = 0
    extra_calls_used = 0
    skipped_non_sneakers = 0
    skipped_missing_release_date = 0
    skipped_out_of_window = 0
    earliest_release_date = None
    latest_release_date = None
    stop_reason = None
    include_traits = True
    requests_before = client.request_count
    total_requests_before = client.request_count
    filters = build_stockx_filter(start_date, end_date)
    last_release_date = None
    is_monotonic = True

    while True:
        if client.request_count - total_requests_before >= max_total_requests:
            stop_reason = "total_request_budget_reached"
            break
        if page > max_pages:
            stop_reason = "max_pages_reached"
            break

        response = client.stockx_list(
            page=page,
            per_page=per_page,
            filters=filters,
            include_traits=include_traits,
            sort="release_date",
        )
        pages_fetched += 1
        products = _extract_items_list(response)
        meta = _extract_meta(response)
        effective_per_page = meta.get("per_page") or meta.get("perPage") or per_page
        if not products:
            stop_reason = "no_results"
            break

        stop_after_page = False
        pending_stockx_offers = []
        needs_flush = False
        for product in products:
            if not is_sneaker_release(product):
                skipped_non_sneakers += 1
                continue

            release_date = extract_release_date(product, include_traits=include_traits)
            if not release_date:
                skipped_missing_release_date += 1
                continue
            if release_date < start_date or release_date > end_date:
                skipped_out_of_window += 1
                if release_date > end_date and is_monotonic:
                    stop_reason = "end_date_reached"
                    stop_after_page = True
                continue

            if last_release_date and release_date < last_release_date:
                is_monotonic = False
            if last_release_date is None or release_date > last_release_date:
                last_release_date = release_date

            if earliest_release_date is None or release_date < earliest_release_date:
                earliest_release_date = release_date
            if latest_release_date is None or release_date > latest_release_date:
                latest_release_date = release_date

            release_fields = _extract_release_fields(product, source="kicksdb_stockx", release_date=release_date)
            release, created = _upsert_release(db_session, release_fields)
            items_upserted += 1
            if created:
                items_created += 1
            elif release_fields.get("_updated"):
                items_updated += 1

            if release.id is None:
                needs_flush = True

            if mode == "full" and release_fields.get("_needs_detail"):
                if (
                    extra_calls_used < max_extra_calls
                    and client.request_count - total_requests_before < max_total_requests
                ):
                    detail = client.get_stockx_product(
                        release_fields.get("source_product_id") or release_fields.get("source_slug"),
                        include_variants=False,
                        include_traits=True,
                    )
                    extra_calls_used += 1
                    detail_fields = _extract_release_fields(detail, source="kicksdb_stockx")
                    detail_fields["sku"] = detail_fields.get("sku") or release.sku
                    detail_fields["source_product_id"] = release_fields.get("source_product_id")
                    detail_fields["source_slug"] = release_fields.get("source_slug")
                    _upsert_release(db_session, detail_fields)

            pending_stockx_offers.append(
                (
                    release,
                    release_fields.get("source_slug"),
                    release_fields.get("source_product_id"),
                    product,
                )
            )

        if needs_flush:
            db_session.flush()
        with db_session.no_autoflush:
            for release, slug, source_product_id, product in pending_stockx_offers:
                _ensure_stockx_offer(db_session, release, slug, source_product_id, product)

        if commit_per_page:
            _safe_commit(db_session, f"stockx_page_{page}")

        if stop_after_page:
            break

        if _has_more_pages(meta, page):
            page += 1
            continue

        if len(products) < effective_per_page:
            if not stop_reason:
                stop_reason = "last_page"
            break

        page += 1

    return {
        "pages_fetched": pages_fetched,
        "items_upserted": items_upserted,
        "items_updated": items_updated,
        "items_created": items_created,
        "skipped_non_sneakers": skipped_non_sneakers,
        "skipped_missing_release_date": skipped_missing_release_date,
        "skipped_out_of_window": skipped_out_of_window,
        "earliest_release_date": str(earliest_release_date) if earliest_release_date else None,
        "latest_release_date": str(latest_release_date) if latest_release_date else None,
        "stop_reason": stop_reason,
        "requests_used": client.request_count - requests_before,
        "extra_calls_used": extra_calls_used,
    }


def _ingest_goat_pass(
    db_session,
    client,
    start_date: date,
    end_date: date,
    per_page: int,
    max_pages: int,
    max_total_requests: int,
    commit_per_page: bool,
) -> Dict[str, Any]:
    page = 1
    pages_fetched = 0
    created = 0
    updated = 0
    deduped = 0
    skipped_non_sneakers = 0
    skipped_missing_release_date = 0
    skipped_out_of_window = 0
    earliest_release_date = None
    latest_release_date = None
    stop_reason = None
    requests_before = client.request_count
    total_requests_before = client.request_count

    include_traits = False
    filters = build_goat_filter(start_date, end_date)
    while True:
        if client.request_count - total_requests_before >= max_total_requests:
            stop_reason = "total_request_budget_reached"
            break
        if page > max_pages:
            stop_reason = "max_pages_reached"
            break

        response = client.goat_list(
            page=page,
            per_page=per_page,
            filters=filters,
            sort=None,
            include_traits=include_traits,
        )
        pages_fetched += 1
        products = _extract_items_list(response)
        meta = _extract_meta(response)
        effective_per_page = meta.get("per_page") or meta.get("perPage") or per_page
        if not products:
            stop_reason = "no_results"
            break

        pending_goat_offers = []
        needs_flush = False
        for product in products:
            if not is_sneaker_release(product):
                skipped_non_sneakers += 1
                continue

            release_date = extract_goat_release_date(product)
            if not release_date:
                skipped_missing_release_date += 1
                continue
            if release_date < start_date or release_date > end_date:
                skipped_out_of_window += 1
                continue

            if earliest_release_date is None or release_date < earliest_release_date:
                earliest_release_date = release_date
            if latest_release_date is None or release_date > latest_release_date:
                latest_release_date = release_date

            release_fields = _extract_release_fields(product, source="kicksdb_goat", release_date=release_date)
            release_fields["sku"] = normalize_sku(release_fields.get("sku"))

            matched, was_created, was_updated = _merge_goat_release(db_session, release_fields)
            if was_created:
                created += 1
            elif was_updated:
                updated += 1
            else:
                deduped += 1

            if matched and matched.id is None:
                needs_flush = True

            pending_goat_offers.append((matched, product))

        if needs_flush:
            db_session.flush()
        with db_session.no_autoflush:
            for matched, product in pending_goat_offers:
                _ensure_goat_offer(db_session, matched, product)

        if commit_per_page:
            _safe_commit(db_session, f"goat_page_{page}")

        if _has_more_pages(meta, page):
            page += 1
            continue

        if len(products) < effective_per_page:
            if not stop_reason:
                stop_reason = "last_page"
            break

        page += 1

    return {
        "goat_pages_fetched": pages_fetched,
        "goat_created": created,
        "goat_updated": updated,
        "goat_deduped": deduped,
        "goat_skipped_non_sneakers": skipped_non_sneakers,
        "goat_skipped_missing_release_date": skipped_missing_release_date,
        "goat_skipped_out_of_window": skipped_out_of_window,
        "goat_stop_reason": stop_reason,
        "goat_earliest_release_date": str(earliest_release_date) if earliest_release_date else None,
        "goat_latest_release_date": str(latest_release_date) if latest_release_date else None,
        "goat_requests_used": client.request_count - requests_before,
    }


def _count_releases_in_window(db_session, start_date: date, end_date: date) -> int:
    return (
        db_session.query(Release)
        .filter(Release.release_date >= start_date, Release.release_date <= end_date)
        .count()
    )


def _refresh_aftermarket_prices(
    db_session,
    client,
    start_date: date,
    end_date: date,
    max_calls: int,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    updated = 0
    skipped = 0
    calls = 0
    today = date.today()

    offers = (
        db_session.query(AffiliateOffer, Release)
        .join(Release, AffiliateOffer.release_id == Release.id)
        .filter(AffiliateOffer.offer_type == "aftermarket")
        .filter(Release.release_date <= today)
        .filter(Release.release_date >= start_date)
        .filter(Release.release_date <= end_date)
        .order_by(Release.release_date.desc())
        .all()
    )
    if not force_refresh:
        offers = [item for item in offers if item[0].price is None]

    for offer, release in offers:
        if calls >= max_calls:
            break

        if offer.retailer == "stockx":
            id_or_slug = release.source_product_id or release.source_slug
            if not id_or_slug:
                skipped += 1
                continue
            detail = client.get_stockx_product(
                id_or_slug,
                include_variants=False,
                include_traits=True,
                include_market=True,
                include_statistics=True,
            )
            price = _extract_stockx_resale_price(_normalize_kicks_detail(detail or {}))
            _update_release_from_detail(db_session, release, detail or {}, source="kicksdb_stockx")
            if price is None:
                skipped += 1
                calls += 1
                continue
            offer.price = price
            offer.currency = offer.currency or "USD"
            updated += 1
            calls += 1
        elif offer.retailer == "goat":
            if release.source != "kicksdb_goat":
                skipped += 1
                continue
            id_or_slug = release.source_product_id or release.source_slug
            if not id_or_slug:
                skipped += 1
                continue
            detail = client.get_goat_product(id_or_slug)
            price = _extract_goat_resale_price(_normalize_kicks_detail(detail or {}))
            _update_release_from_detail(db_session, release, detail or {}, source="kicksdb_goat")
            if price is None:
                skipped += 1
                calls += 1
                continue
            offer.price = price
            offer.currency = offer.currency or "USD"
            updated += 1
            calls += 1
        else:
            skipped += 1

        if updated % 10 == 0:
            _safe_commit(db_session, "pricing_batch")

    _safe_commit(db_session, "pricing_final")

    return {
        "pricing_calls_used": calls,
        "pricing_offers_updated": updated,
        "pricing_offers_skipped": skipped,
    }


def refresh_aftermarket_prices_for_skus(
    db_session,
    client,
    skus: List[str],
    max_calls: int = 20,
) -> Dict[str, Any]:
    normalized_skus = [normalize_sku(sku) for sku in skus if sku]
    normalized_skus = [sku for sku in normalized_skus if sku]
    if not normalized_skus:
        return {
            "skus": [],
            "calls_used": 0,
            "offers_updated": 0,
            "offers_skipped": 0,
            "skus_not_found": [],
        }

    releases = (
        db_session.query(Release)
        .options(joinedload(Release.offers))
        .filter(Release.sku.in_(normalized_skus))
        .all()
    )
    releases_by_sku = {release.sku: release for release in releases if release.sku}

    calls = 0
    updated = 0
    skipped = 0
    skus_not_found = []

    for sku in normalized_skus:
        release = releases_by_sku.get(sku)
        if not release:
            skus_not_found.append(sku)
            continue

        aftermarket_offers = [offer for offer in release.offers if offer.offer_type == "aftermarket"]
        if not aftermarket_offers:
            skipped += 1
            continue

        for offer in aftermarket_offers:
            if calls >= max_calls:
                break
            id_or_slug = release.source_product_id or release.source_slug
            if offer.retailer == "stockx":
                if not id_or_slug:
                    skipped += 1
                    continue
                detail = client.get_stockx_product(
                    id_or_slug,
                    include_variants=False,
                    include_traits=True,
                    include_market=True,
                    include_statistics=True,
                )
                normalized_detail = _normalize_kicks_detail(detail or {})
                price = _extract_stockx_resale_price(normalized_detail)
                _update_release_from_detail(db_session, release, normalized_detail, source="kicksdb_stockx")
            elif offer.retailer == "goat":
                if not id_or_slug:
                    skipped += 1
                    continue
                detail = client.get_goat_product(id_or_slug)
                normalized_detail = _normalize_kicks_detail(detail or {})
                price = _extract_goat_resale_price(normalized_detail)
                _update_release_from_detail(db_session, release, normalized_detail, source="kicksdb_goat")
            else:
                skipped += 1
                continue

            calls += 1
            if price is None:
                skipped += 1
                continue

            offer.price = price
            offer.currency = offer.currency or "USD"
            offer.last_checked_at = datetime.utcnow()
            updated += 1

            if (updated + skipped) % 5 == 0:
                _safe_commit(db_session, "pricing_sku_batch")

        if calls >= max_calls:
            break

    _safe_commit(db_session, "pricing_sku_final")

    return {
        "skus": normalized_skus,
        "calls_used": calls,
        "offers_updated": updated,
        "offers_skipped": skipped,
        "skus_not_found": skus_not_found,
    }


def _update_release_from_detail(db_session, release: Release, detail: Dict[str, Any], source: str) -> None:
    fields = _extract_release_fields(detail, source=source)
    for key in ("brand", "model_name", "colorway", "image_url", "retail_price", "retail_currency"):
        if getattr(release, key, None) is None and fields.get(key) is not None:
            setattr(release, key, fields[key])
    if release.source is None:
        release.source = fields.get("source")
    if release.source_product_id is None and fields.get("source_product_id"):
        release.source_product_id = fields.get("source_product_id")
    if release.source_slug is None and fields.get("source_slug"):
        release.source_slug = fields.get("source_slug")
    release.last_synced_at = datetime.utcnow()
    db_session.add(release)


def _safe_commit(db_session, label: str, retries: int = 3, delay_seconds: float = 0.2) -> None:
    for attempt in range(1, retries + 1):
        try:
            db_session.commit()
            db_session.expire_all()
            return
        except Exception as exc:
            db_session.rollback()
            if attempt >= retries:
                logger.exception("Commit failed (%s) after %s attempts", label, retries)
                raise
            logger.warning("Commit failed (%s) attempt %s/%s: %s", label, attempt, retries, exc)
            time.sleep(delay_seconds)


def build_stockx_filter(start_date: date, end_date: date) -> str:
    start_value = format_filter_date(start_date)
    end_value = format_filter_date(end_date)
    return (
        f'(product_type = "sneakers") AND '
        f'(release_date >= "{start_value}" AND release_date <= "{end_value}")'
    )


def build_goat_filter(start_date: date, end_date: date) -> str:
    start_value = format_filter_date(start_date)
    end_value = format_filter_date(end_date)
    return (
        f'(product_type = "sneakers") AND '
        f'(release_date >= "{start_value}" AND release_date <= "{end_value}")'
    )


def format_filter_date(value: date) -> str:
    return value.strftime("%Y-%m-%d")

def parse_release_date(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    if not isinstance(raw, str):
        raw = str(raw)
    raw = raw.strip()
    if raw.isdigit() and len(raw) == 8:
        try:
            return datetime.strptime(raw, "%Y%m%d").date()
        except ValueError:
            return None
    if raw.isdigit() and len(raw) == 4:
        return None
    if "T" in raw:
        raw = raw.split("T", 1)[0]
    return _parse_date(raw)


def extract_goat_release_date(product: Dict[str, Any]) -> Optional[date]:
    raw_value = product.get("release_date") or product.get("releaseDate")
    return parse_release_date(raw_value)


def _raw_release_date(item: Dict[str, Any]) -> Optional[str]:
    value = item.get("release_date") or item.get("releaseDate")
    if value:
        return str(value)
    traits = item.get("traits") or item.get("productTraits") or []
    trait_value = _extract_trait_value(traits, "Release Date")
    return str(trait_value) if trait_value else None


def run_probe(client, per_page: int = 100, start_date: Optional[date] = None, end_date: Optional[date] = None) -> Dict[str, Any]:
    start_date = start_date or (date.today() - timedelta(days=30))
    end_date = end_date or (date.today() + timedelta(days=90))
    stockx_filters = build_stockx_filter(start_date, end_date)
    goat_filters = build_goat_filter(start_date, end_date)
    stockx = client.stockx_list(
        page=1,
        per_page=per_page,
        filters=stockx_filters,
        sort="release_date",
        include_traits=True,
    )
    goat = client.goat_list(page=1, per_page=per_page, filters=goat_filters, sort=None)

    stockx_items = _extract_items_list(stockx)
    goat_items = _extract_items_list(goat)
    stockx_meta = _extract_meta(stockx)
    goat_meta = _extract_meta(goat)

    stockx_parsed = [extract_release_date(item, include_traits=True) for item in stockx_items]
    goat_parsed = [extract_goat_release_date(item) for item in goat_items]

    stockx_in_window = [d for d in stockx_parsed if d and start_date <= d <= end_date]
    goat_in_window = [d for d in goat_parsed if d and start_date <= d <= end_date]

    return {
        "filters_param": "filters",
        "per_page": per_page,
        "stockx_count": len(stockx_items),
        "stockx_with_date": sum(1 for value in stockx_parsed if value),
        "stockx_sample_dates": list(dict.fromkeys([_raw_release_date(item) for item in stockx_items if _raw_release_date(item)]))[:10],
        "stockx_non_sneakers": sum(1 for item in stockx_items if not is_sneaker_release(item)),
        "stockx_in_window": len(stockx_in_window),
        "stockx_earliest": str(min(stockx_in_window)) if stockx_in_window else None,
        "stockx_latest": str(max(stockx_in_window)) if stockx_in_window else None,
        "stockx_meta_per_page": stockx_meta.get("per_page") or stockx_meta.get("perPage"),
        "stockx_meta_current_page": stockx_meta.get("current_page") or stockx_meta.get("page"),
        "stockx_meta_total_pages": stockx_meta.get("total_pages") or stockx_meta.get("totalPages"),
        "goat_count": len(goat_items),
        "goat_with_date": sum(1 for value in goat_parsed if value),
        "goat_sample_dates": list(dict.fromkeys([_raw_release_date(item) for item in goat_items if _raw_release_date(item)]))[:10],
        "goat_non_sneakers": sum(1 for item in goat_items if not is_sneaker_release(item)),
        "goat_in_window": len(goat_in_window),
        "goat_earliest": str(min(goat_in_window)) if goat_in_window else None,
        "goat_latest": str(max(goat_in_window)) if goat_in_window else None,
        "goat_meta_per_page": goat_meta.get("per_page") or goat_meta.get("perPage"),
        "goat_meta_current_page": goat_meta.get("current_page") or goat_meta.get("page"),
        "goat_meta_total_pages": goat_meta.get("total_pages") or goat_meta.get("totalPages"),
        "requests_used_total": client.request_count,
    }


def _extract_release_fields(product: Dict[str, Any], source: str, release_date: Optional[date] = None) -> Dict[str, Any]:
    traits = product.get("traits") or product.get("productTraits") or []
    model_name = product.get("name") or product.get("title") or product.get("model_name")
    sku = product.get("sku") or _extract_trait_value(traits, "Style") or _extract_trait_value(traits, "SKU")
    retail_price = _to_decimal(product.get("retailPrice") or _extract_trait_value(traits, "Retail Price"))
    retail_currency = _extract_trait_value(traits, "Retail Price Currency")
    if not retail_currency and source == "kicksdb_stockx" and retail_price is not None:
        retail_currency = "USD"
    image_url = _extract_image_url(product)

    needs_detail = release_date is None or retail_price is None or not image_url

    return {
        "sku": sku,
        "name": model_name or "Unknown",
        "model_name": model_name,
        "brand": product.get("brand") or product.get("brandName"),
        "colorway": product.get("colorway"),
        "release_date": release_date,
        "retail_price": retail_price,
        "retail_currency": retail_currency,
        "image_url": image_url,
        "source": source,
        "source_product_id": product.get("id") or product.get("product_id"),
        "source_slug": product.get("slug"),
        "source_updated_at": _parse_datetime(product.get("updated_at") or product.get("updatedAt")),
        "last_synced_at": datetime.utcnow(),
        "_needs_detail": needs_detail,
    }


def _upsert_release(db_session, fields: Dict[str, Any]) -> Tuple[Release, bool]:
    sku = fields.get("sku")
    source = fields.get("source")
    source_product_id = fields.get("source_product_id")

    release = None
    with db_session.no_autoflush:
        if sku:
            release = db_session.query(Release).filter_by(sku=sku).first()
        if not release and source and source_product_id:
            release = db_session.query(Release).filter_by(source=source, source_product_id=source_product_id).first()

    created = False
    if not release:
        release = Release(name=fields.get("name") or "Unknown")
        created = True

    updated = False
    for key, value in fields.items():
        if key.startswith("_"):
            continue
        if value is None:
            continue
        if getattr(release, key, None) != value:
            setattr(release, key, value)
            updated = True

    fields["_updated"] = updated
    db_session.add(release)
    return release, created


def _ensure_stockx_offer(
    db_session,
    release: Release,
    slug: Optional[str],
    source_product_id: Optional[str],
    product: Optional[Dict[str, Any]],
) -> None:
    base_url = None
    if slug:
        base_url = f"https://stockx.com/{slug}"
    elif source_product_id:
        base_url = f"https://stockx.com/{source_product_id}"

    if not base_url:
        return

    offer = (
        db_session.query(AffiliateOffer)
        .filter_by(release_id=release.id, retailer="stockx", region=None)
        .first()
    )
    price = _extract_stockx_lowest_ask(product or {})
    currency = _infer_offer_currency(price, source="stockx")

    if not offer:
        offer = AffiliateOffer(
            release_id=release.id,
            retailer="stockx",
            region=None,
            base_url=base_url,
            offer_type="aftermarket",
            priority=50,
            is_active=True,
            price=price,
            currency=currency,
        )
        db_session.add(offer)
    else:
        offer.base_url = base_url
        if offer.offer_type is None:
            offer.offer_type = "aftermarket"
        if offer.priority is None:
            offer.priority = 50
        if offer.price is None and price is not None:
            offer.price = price
        if offer.currency is None and currency is not None:
            offer.currency = currency


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


def _extract_meta(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    for key in ("meta", "metadata", "pagination"):
        meta = data.get(key)
        if isinstance(meta, dict):
            return meta
    if isinstance(data.get("data"), dict):
        meta = data["data"].get("meta")
        if isinstance(meta, dict):
            return meta
    return {}


def _normalize_kicks_detail(detail: Dict[str, Any]) -> Dict[str, Any]:
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


def _extract_stockx_resale_price(item: Dict[str, Any]) -> Optional[Decimal]:
    if not item:
        return None
    market = item.get("market") if isinstance(item.get("market"), dict) else {}
    stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    price = (
        stats.get("last_90_days_average_price")
        or stats.get("average_sale_price")
        or stats.get("averageSalePrice")
        or stats.get("avg_sale_price")
        or stats.get("avgSalePrice")
        or stats.get("average_price")
        or stats.get("averagePrice")
        or stats.get("annual_average_price")
        or item.get("average_sale_price")
        or item.get("averageSalePrice")
        or item.get("avg_sale_price")
        or item.get("avgSalePrice")
        or item.get("average_price")
        or item.get("averagePrice")
        or market.get("average_sale_price")
        or market.get("averageSalePrice")
        or market.get("avg_sale_price")
        or market.get("avgSalePrice")
        or market.get("average_price")
        or market.get("averagePrice")
    )
    return _to_decimal(price) if price is not None else None


def _extract_goat_resale_price(item: Dict[str, Any]) -> Optional[Decimal]:
    if not item:
        return None
    price = (
        item.get("average_sale_price")
        or item.get("averageSalePrice")
        or item.get("avg_sale_price")
        or item.get("avgSalePrice")
        or item.get("average_price")
        or item.get("averagePrice")
        or item.get("lowest_ask")
        or item.get("lowestAsk")
    )
    return _to_decimal(price) if price is not None else None

def _extract_stockx_lowest_ask(item: Dict[str, Any]) -> Optional[Decimal]:
    lowest = item.get("lowestAsk") or item.get("lowest_ask")
    if lowest is None and isinstance(item.get("market"), dict):
        lowest = item["market"].get("lowestAsk")
    return _to_decimal(lowest) if lowest is not None else None


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


def _infer_offer_currency(price: Optional[Decimal], source: str) -> Optional[str]:
    if price is None:
        return None
    if source in ("stockx", "goat"):
        return "USD"
    return None


def _has_more_pages(meta: Dict[str, Any], current_page: int) -> bool:
    if not meta:
        return False
    total_pages = meta.get("total_pages") or meta.get("totalPages") or meta.get("last_page")
    if isinstance(total_pages, int):
        return current_page < total_pages
    has_more = meta.get("has_more") or meta.get("hasMore")
    if isinstance(has_more, bool):
        return has_more
    next_page = meta.get("next_page") or meta.get("nextPage")
    if isinstance(next_page, int):
        return next_page > current_page
    return False


def _merge_goat_release(db_session, fields: Dict[str, Any]) -> Tuple[Release, bool, bool]:
    sku = fields.get("sku")
    source = fields.get("source")
    source_product_id = fields.get("source_product_id")

    release = None
    with db_session.no_autoflush:
        if sku:
            release = db_session.query(Release).filter(Release.sku.ilike(sku)).first()
        if not release and source and source_product_id:
            release = db_session.query(Release).filter_by(source=source, source_product_id=source_product_id).first()

    created = False
    updated = False
    if not release:
        release = Release(name=fields.get("name") or "Unknown")
        created = True

    for key in ("brand", "model_name", "colorway", "image_url", "retail_price", "retail_currency"):
        if getattr(release, key, None) is None and fields.get(key) is not None:
            setattr(release, key, fields[key])
            updated = True

    if release.sku is None and fields.get("sku"):
        release.sku = fields["sku"]
        updated = True

    if release.release_date is None and fields.get("release_date"):
        release.release_date = fields["release_date"]
        updated = True

    if release.source is None:
        release.source = fields.get("source")
    if release.source_product_id is None:
        release.source_product_id = fields.get("source_product_id")
    if release.source_slug is None:
        release.source_slug = fields.get("source_slug")

    release.last_synced_at = datetime.utcnow()
    db_session.add(release)
    return release, created, updated


def _ensure_goat_offer(db_session, release: Optional[Release], product: Dict[str, Any]) -> None:
    if not release:
        return
    base_url = product.get("url") or product.get("link") or product.get("product_url") or product.get("productUrl")
    if not base_url:
        return
    price = _extract_goat_lowest_ask(product)
    currency = _infer_offer_currency(price, source="goat")
    offer = (
        db_session.query(AffiliateOffer)
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
            price=price,
            currency=currency,
        )
        db_session.add(offer)
    else:
        offer.base_url = base_url
        if offer.offer_type is None:
            offer.offer_type = "aftermarket"
        if offer.priority is None:
            offer.priority = 60
        if offer.price is None and price is not None:
            offer.price = price
        if offer.currency is None and currency is not None:
            offer.currency = currency


def extract_release_date(product: Dict[str, Any], include_traits: bool = False) -> Optional[date]:
    traits = product.get("traits") or product.get("productTraits") or []
    release_date_value = product.get("release_date") or product.get("releaseDate")
    if not release_date_value and include_traits:
        release_date_value = _extract_trait_value(traits, "Release Date")
    return parse_release_date(release_date_value)


def is_sneaker_release(product: Dict[str, Any]) -> bool:
    product_type = (product.get("product_type") or product.get("productType") or "").lower()
    category = (product.get("category") or "").lower()
    title = (product.get("name") or product.get("title") or "").lower()
    traits = product.get("traits") or product.get("productTraits") or []
    gender = (product.get("gender") or "").lower()

    footwear_signals = ["sneaker", "sneakers", "footwear", "shoe", "shoes"]
    apparel_keywords = ["jacket", "hoodie", "tee", "t-shirt", "pants", "hat", "bag", "beanie", "coat", "shorts", "shirt"]

    if product_type:
        return product_type in footwear_signals
    if category in footwear_signals:
        return True

    if any(keyword in title for keyword in apparel_keywords):
        return False

    trait_text = " ".join(
        str(_extract_trait_value(traits, trait_name) or "")
        for trait_name in ("Category", "Product Type", "Gender")
    ).lower()

    if any(keyword in trait_text for keyword in apparel_keywords):
        return False

    if any(keyword in trait_text for keyword in footwear_signals):
        return True

    if gender in ("men", "women", "kids", "youth"):
        # Weak signal only; require footwear cue in title to avoid apparel.
        return any(keyword in title for keyword in footwear_signals)

    return False


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


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        if not isinstance(value, str):
            value = str(value)
        raw = value.strip()
        if raw.isdigit() and len(raw) == 8:
            return datetime.strptime(raw, "%Y%m%d").date()
        if "T" in raw:
            raw = raw.split("T", 1)[0]
        return datetime.fromisoformat(raw.split(" ")[0]).date()
    except ValueError:
        return None
    return None


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return None


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
