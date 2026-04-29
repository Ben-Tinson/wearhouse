# release_updater.py
import argparse
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable, List, Optional, Tuple

from app import create_app
from extensions import db
from models import Release, ReleaseSalePoint, ReleaseSizeBid
from services.kicks_client import KicksClient
from services.release_ingestion_service import ingest_kicksdb_releases, refresh_aftermarket_prices_for_skus
from services.heat_service import compute_heat_for_release, get_market_snapshot
from services.release_ingestion_service import _normalize_kicks_detail


STALE_AFTER = timedelta(hours=24)


def _parse_iso_datetime(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.utcfromtimestamp(value)
        except (ValueError, OSError):
            return None
    if isinstance(value, str):
        cleaned = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(cleaned)
        except ValueError:
            return None
    return None


def _to_decimal(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _extract_variant_prices(detail: Dict) -> List[Tuple[str, Optional[str], Decimal, str]]:
    variants = detail.get("variants") or []
    results = []
    for variant in variants:
        size_label = (
            variant.get("size")
            or variant.get("sizeLabel")
            or variant.get("size_label")
            or variant.get("display_size")
        )
        size_type = variant.get("sizeType") or variant.get("size_type")
        if not size_label:
            continue
        highest_bid = variant.get("highest_bid") or variant.get("highestBid")
        lowest_ask = variant.get("lowest_ask") or variant.get("lowestAsk")
        bid_value = _to_decimal(highest_bid)
        ask_value = _to_decimal(lowest_ask)
        if bid_value is not None:
            results.append((str(size_label), size_type, bid_value, "bid"))
        if ask_value is not None:
            results.append((str(size_label), size_type, ask_value, "ask"))
    return results


def _extract_sales_points(response: Dict) -> List[Tuple[datetime, Decimal, Optional[str]]]:
    items = response.get("sales") or response.get("results") or response.get("data") or []
    points = []
    for item in items:
        sale_at = _parse_iso_datetime(
            item.get("sale_at")
            or item.get("saleAt")
            or item.get("sale_date")
            or item.get("saleDate")
            or item.get("created_at")
            or item.get("createdAt")
            or item.get("timestamp")
        )
        price = _to_decimal(
            item.get("price")
            or item.get("sale_price")
            or item.get("salePrice")
            or item.get("amount")
        )
        currency = item.get("currency") or item.get("currencyCode")
        if sale_at and price is not None:
            points.append((sale_at, price, currency))
    return points


def _is_bid_stale(db_session, release_id: int, now: datetime) -> bool:
    latest = (
        db_session.query(ReleaseSizeBid.fetched_at)
        .filter(ReleaseSizeBid.release_id == release_id, ReleaseSizeBid.price_type == "bid")
        .order_by(ReleaseSizeBid.fetched_at.desc())
        .first()
    )
    if not latest or not latest[0]:
        return True
    return latest[0] <= now - STALE_AFTER


def _is_sales_stale(db_session, release_id: int, now: datetime) -> bool:
    latest = (
        db_session.query(ReleaseSalePoint.fetched_at)
        .filter(ReleaseSalePoint.release_id == release_id)
        .order_by(ReleaseSalePoint.fetched_at.desc())
        .first()
    )
    if not latest or not latest[0]:
        return True
    return latest[0] <= now - STALE_AFTER


def _upsert_size_bid(
    db_session,
    release_id: int,
    size_label: str,
    size_type: Optional[str],
    value: Decimal,
    currency: str,
    price_type: str,
    fetched_at: datetime,
) -> None:
    existing = (
        db_session.query(ReleaseSizeBid)
        .filter_by(
            release_id=release_id,
            size_label=size_label,
            size_type=size_type,
            price_type=price_type,
        )
        .first()
    )
    if existing:
        existing.highest_bid = value
        existing.currency = currency
        existing.price_type = price_type
        existing.fetched_at = fetched_at
        return
    db_session.add(
        ReleaseSizeBid(
            release_id=release_id,
            size_label=size_label,
            size_type=size_type,
            highest_bid=value,
            currency=currency,
            price_type=price_type,
            fetched_at=fetched_at,
        )
    )


def _upsert_sale_point(
    db_session,
    release_id: int,
    sale_at: datetime,
    price: Decimal,
    currency: str,
    fetched_at: datetime,
) -> None:
    existing = (
        db_session.query(ReleaseSalePoint)
        .filter_by(release_id=release_id, sale_at=sale_at)
        .first()
    )
    if existing:
        existing.price = price
        existing.currency = currency
        existing.fetched_at = fetched_at
        return
    db_session.add(
        ReleaseSalePoint(
            release_id=release_id,
            sale_at=sale_at,
            price=price,
            currency=currency,
            fetched_at=fetched_at,
        )
    )


def _select_enrichment_candidates(
    db_session,
    today: date,
    window_days: int,
    top_n: int,
) -> List[Tuple[Release, float]]:
    upcoming = (
        db_session.query(Release)
        .filter(
            Release.release_date.isnot(None),
            Release.release_date >= today,
            Release.release_date <= today + timedelta(days=window_days),
            Release.retail_price.isnot(None),
            Release.retail_currency.isnot(None),
        )
        .all()
    )
    ranked: List[Tuple[Release, float, bool, date]] = []
    for release in upcoming:
        snapshot = get_market_snapshot(db_session, release, today)
        asks_only = bool(snapshot["ask_count"] and not snapshot["bid_count"] and not snapshot["has_sales"])
        if not asks_only:
            continue
        if not release.retail_price:
            continue
        ask_median = snapshot["asks_median"]
        if ask_median is None:
            continue
        raw_ratio = float(ask_median / Decimal(release.retail_price))
        ranked.append((release, raw_ratio, asks_only, release.release_date))

    ranked.sort(key=lambda item: (0 if item[2] else 1, -item[1], item[3]))
    return [(release, ratio) for release, ratio, _, _ in ranked[:top_n]]


def _fetch_and_store_bids(db_session, client: KicksClient, release: Release, now: datetime) -> int:
    if not release.source_product_id or release.source != "kicksdb_stockx":
        return 0
    detail = client.get_stockx_product(release.source_product_id, include_variants=True, include_market=True)
    normalized = _normalize_kicks_detail(detail or {})
    prices = _extract_variant_prices(normalized)
    currency = release.retail_currency or "USD"
    updated = 0
    for size_label, size_type, value, price_type in prices:
        if price_type != "bid":
            continue
        _upsert_size_bid(
            db_session,
            release_id=release.id,
            size_label=size_label,
            size_type=size_type,
            value=value,
            currency=currency,
            price_type=price_type,
            fetched_at=now,
        )
        updated += 1
    release.size_bids_last_fetched_at = now
    return updated


def _fetch_and_store_sales(db_session, client: KicksClient, release: Release, now: datetime) -> int:
    if not release.source_product_id or release.source != "kicksdb_stockx":
        return 0
    response = client.get_stockx_sales_history(release.source_product_id, limit=50, page=1)
    points = _extract_sales_points(response or {})
    currency = release.retail_currency or "USD"
    updated = 0
    for sale_at, price, sale_currency in points:
        _upsert_sale_point(
            db_session,
            release_id=release.id,
            sale_at=sale_at,
            price=price,
            currency=sale_currency or currency,
            fetched_at=now,
        )
        updated += 1
    release.sales_last_fetched_at = now
    return updated


def _backfill_heat(db_session, start_date: date, end_date: date) -> Dict[str, int]:
    releases = (
        db_session.query(Release)
        .filter(
            Release.release_date.isnot(None),
            Release.release_date >= start_date,
            Release.release_date <= end_date,
        )
        .all()
    )
    updated = 0
    for release in releases:
        compute_heat_for_release(db_session, release)
        updated += 1
    db_session.commit()
    return {"releases_processed": updated}


def _enrich_top_releases(
    db_session,
    client: KicksClient,
    top_n: int,
    max_total_requests: int,
    enrich_sources: Iterable[str],
    window_days: int,
) -> Dict[str, int]:
    now = datetime.utcnow()
    today = now.date()
    sources = {item.strip() for item in enrich_sources if item.strip()}
    candidates = _select_enrichment_candidates(db_session, today, window_days, top_n)
    calls_used = 0
    releases_enriched = 0
    bids_updated = 0
    sales_updated = 0

    for release, _ in candidates:
        if calls_used >= max_total_requests:
            break
        did_any = False

        if "bids" in sources and _is_bid_stale(db_session, release.id, now):
            if calls_used >= max_total_requests:
                break
            bids_updated += _fetch_and_store_bids(db_session, client, release, now)
            calls_used += 1
            did_any = True

        if "sales" in sources and _is_sales_stale(db_session, release.id, now):
            if calls_used >= max_total_requests:
                break
            sales_updated += _fetch_and_store_sales(db_session, client, release, now)
            calls_used += 1
            did_any = True

        if did_any:
            compute_heat_for_release(db_session, release, now=now, force=True)
            releases_enriched += 1

    db_session.commit()
    return {
        "releases_considered": len(candidates),
        "releases_enriched": releases_enriched,
        "calls_used": calls_used,
        "bids_updated": bids_updated,
        "sales_updated": sales_updated,
    }


def update_releases_from_api():
    parser = argparse.ArgumentParser(description="Populate releases from KicksDB (StockX + optional GOAT backfill).")
    parser.add_argument("--mode", choices=["lite", "full"], default=None)
    parser.add_argument("--start", type=str, default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--days-ahead", type=int, default=None, help="Override end date as today + N days")
    parser.add_argument("--per-page", type=int, default=100)
    parser.add_argument("--max-pages-stockx", type=int, default=5)
    parser.add_argument("--max-pages-goat", type=int, default=5)
    parser.add_argument("--backfill-threshold", type=int, default=40)
    parser.add_argument("--max-total-requests", type=int, default=10)
    parser.add_argument("--no-backfill-goat", action="store_true")
    parser.add_argument("--refresh-pricing", action="store_true", help="Fetch aftermarket prices via detail endpoints (capped).")
    parser.add_argument("--pricing-max-calls", type=int, default=20, help="Max detail calls for pricing refresh.")
    parser.add_argument("--pricing-force", action="store_true", help="Refresh existing resale prices, not just missing ones.")
    parser.add_argument("--pricing-skus", type=str, default=None, help="Comma-separated SKUs to refresh resale prices.")
    parser.add_argument("--backfill-heat", action="store_true", help="Recompute heat for releases in the window.")
    parser.add_argument("--enrich-top", type=int, default=0, help="Enrich bids/sales for top N upcoming releases.")
    parser.add_argument("--enrich-sources", type=str, default="bids", help="Comma list: bids,sales")
    parser.add_argument("--window-days", type=int, default=120)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()

    if args.mode is None and not args.backfill_heat and args.enrich_top <= 0 and not args.pricing_skus:
        args.mode = "lite"

    start_date = datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else date.today() - timedelta(days=7)
    if args.days_ahead is not None:
        end_date = date.today() + timedelta(days=args.days_ahead)
    else:
        end_date = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today() + timedelta(days=120)

    app = create_app()
    with app.app_context():
        api_key = app.config.get("KICKS_API_KEY")
        needs_client = bool(args.mode or args.refresh_pricing or args.pricing_skus or args.enrich_top > 0 or args.probe)
        if needs_client and not api_key:
            print("ERROR: KICKS_API_KEY not found.")
            return

        client = None
        if needs_client:
            client = KicksClient(
                api_key=api_key,
                base_url=app.config.get("KICKS_API_BASE_URL", "https://api.kicks.dev"),
                logger=app.logger,
            )

        if args.probe:
            from services.release_ingestion_service import run_probe
            stats = run_probe(client, per_page=args.per_page, start_date=start_date, end_date=end_date)
            print(
                "Probe complete: filters_param={filters_param} per_page={per_page} "
                "stockx_count={stockx_count} stockx_with_date={stockx_with_date} "
                "stockx_in_window={stockx_in_window} stockx_non_sneakers={stockx_non_sneakers} "
                "stockx_earliest={stockx_earliest} stockx_latest={stockx_latest} "
                "stockx_sample_dates={stockx_sample_dates} stockx_meta_per_page={stockx_meta_per_page} "
                "stockx_meta_current_page={stockx_meta_current_page} stockx_meta_total_pages={stockx_meta_total_pages} "
                "goat_count={goat_count} goat_with_date={goat_with_date} "
                "goat_in_window={goat_in_window} goat_non_sneakers={goat_non_sneakers} "
                "goat_earliest={goat_earliest} goat_latest={goat_latest} "
                "goat_sample_dates={goat_sample_dates} goat_meta_per_page={goat_meta_per_page} "
                "goat_meta_current_page={goat_meta_current_page} goat_meta_total_pages={goat_meta_total_pages} "
                "requests_used_total={requests_used_total}".format(**stats)
            )
            return

        if args.pricing_skus:
            skus = [sku.strip().upper() for sku in args.pricing_skus.split(",") if sku.strip()]
            if not skus:
                print("No valid SKUs provided for pricing refresh.")
                return
            stats = refresh_aftermarket_prices_for_skus(
                db_session=db.session,
                client=client,
                skus=skus,
                max_calls=args.pricing_max_calls,
            )
            print(
                "Resale refresh complete: skus={skus} calls_used={calls_used} "
                "offers_updated={offers_updated} offers_skipped={offers_skipped} "
                "skus_not_found={skus_not_found}".format(**stats)
            )
            return

        if args.mode:
            stats = ingest_kicksdb_releases(
                db_session=db.session,
                client=client,
                start_date=start_date,
                end_date=end_date,
                mode=args.mode,
                per_page=args.per_page,
                max_pages_stockx=args.max_pages_stockx,
                max_pages_goat=args.max_pages_goat,
                max_total_requests=args.max_total_requests,
                backfill_goat=not args.no_backfill_goat,
                backfill_threshold=args.backfill_threshold,
                refresh_pricing=args.refresh_pricing,
                pricing_max_calls=args.pricing_max_calls,
                pricing_force=args.pricing_force,
            )

            print(
                "Release ingestion complete: mode={mode} pages={pages_fetched} "
                "requests={total_kicks_requests} stockx_requests_used={stockx_requests_used} "
                "goat_requests_used={goat_requests_used} upserted={items_upserted} "
                "created={items_created} updated={items_updated} "
                "skipped_non_sneakers={skipped_non_sneakers} "
                "skipped_missing_release_date={skipped_missing_release_date} "
                "skipped_out_of_window={skipped_out_of_window} "
                "earliest_release_date={earliest_release_date} "
                "latest_release_date={latest_release_date} "
                "stop_reason={stop_reason} "
                "goat_created={goat_created} goat_updated={goat_updated} "
                "goat_skipped_non_sneakers={goat_skipped_non_sneakers} "
                "goat_skipped_missing_release_date={goat_skipped_missing_release_date} "
                "goat_deduped={goat_deduped} goat_stop_reason={goat_stop_reason}".format(**stats)
            )

        if args.backfill_heat:
            stats = _backfill_heat(db.session, start_date, end_date)
            print("Heat backfill complete: releases_processed={releases_processed}".format(**stats))

        if args.enrich_top > 0:
            print("Heat enrichment is paused; no KicksDB calls will be made for heat.")
            return
            stats = _enrich_top_releases(
                db_session=db.session,
                client=client,
                top_n=args.enrich_top,
                max_total_requests=args.max_total_requests,
                enrich_sources=[s.strip() for s in args.enrich_sources.split(",")],
                window_days=args.window_days,
            )
            print(
                "Heat enrichment complete: releases_considered={releases_considered} "
                "releases_enriched={releases_enriched} calls_used={calls_used} "
                "bids_updated={bids_updated} sales_updated={sales_updated}".format(**stats)
            )


if __name__ == "__main__":
    update_releases_from_api()
