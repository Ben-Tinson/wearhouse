# release_updater.py
import argparse
from datetime import datetime, date, timedelta

from app import create_app
from extensions import db
from services.kicks_client import KicksClient
from services.release_ingestion_service import ingest_kicksdb_releases, refresh_aftermarket_prices_for_skus


def update_releases_from_api():
    parser = argparse.ArgumentParser(description="Populate releases from KicksDB (StockX + optional GOAT backfill).")
    parser.add_argument("--mode", choices=["lite", "full"], default="lite")
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
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()

    start_date = datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else date.today() - timedelta(days=7)
    if args.days_ahead is not None:
        end_date = date.today() + timedelta(days=args.days_ahead)
    else:
        end_date = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today() + timedelta(days=120)

    app = create_app()
    with app.app_context():
        api_key = app.config.get("KICKS_API_KEY")
        if not api_key:
            print("ERROR: KICKS_API_KEY not found.")
            return

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


if __name__ == "__main__":
    update_releases_from_api()
