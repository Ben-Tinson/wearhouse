# tests/test_release_ingestion.py
from datetime import date

from extensions import db
from models import Release, AffiliateOffer
from services.release_ingestion_service import (
    ingest_kicksdb_releases,
    parse_release_date,
    run_probe,
    build_goat_filter,
    build_stockx_filter,
)


class FakeKicksClient:
    def __init__(self, pages, goat_pages=None):
        self.pages = pages
        self.goat_pages = goat_pages or {}
        self.request_count = 0
        self.endpoints_hit = []

    def stockx_list(self, page=1, per_page=100, filters=None, sort="release_date", include_traits=False):
        self.request_count += 1
        self.endpoints_hit.append("/v3/stockx/products")
        return self.pages.get(page, {"results": []})

    def goat_list(self, page=1, per_page=100, filters=None, sort=None, include_traits=False):
        self.request_count += 1
        self.endpoints_hit.append("/v3/goat/products")
        return self.goat_pages.get(page, {"results": []})

    def get_stockx_product(self, id_or_slug, include_variants=False, include_traits=True):
        self.request_count += 1
        self.endpoints_hit.append("/v3/stockx/products/{id}")
        return {"name": "Detail", "id": id_or_slug}


def test_ingestion_single_page_min_requests(test_app):
    with test_app.app_context():
        pages = {
            1: {
                "results": [
                        {
                            "id": "stockx-1",
                            "sku": "REL-1",
                            "name": "Release One",
                            "brand": "Nike",
                            "category": "sneakers",
                            "release_date": str(date.today()),
                            "image": {"original": "https://example.com/rel.png"},
                        }
                ]
            }
        }
        client = FakeKicksClient(pages)
        stats = ingest_kicksdb_releases(
            db.session,
            client,
            per_page=100,
            mode="lite",
            backfill_threshold=999,
            backfill_goat=False,
        )

        assert stats["total_kicks_requests"] == 1
        assert Release.query.filter_by(sku="REL-1").first() is not None
        offer = AffiliateOffer.query.first()
        assert offer is not None


def test_ingestion_no_detail_calls_in_lite(test_app):
    with test_app.app_context():
        pages = {
            1: {
                "results": [
                    {
                        "id": "stockx-2",
                        "sku": "REL-2",
                        "name": "Release Two",
                        "brand": "Nike",
                        "release_date": str(date.today()),
                    }
                ]
            }
        }
        client = FakeKicksClient(pages)
        stats = ingest_kicksdb_releases(
            db.session,
            client,
            per_page=100,
            mode="lite",
            backfill_threshold=999,
            backfill_goat=False,
        )

        assert stats["total_kicks_requests"] == 1
        assert all("/v3/stockx/products/{id}" not in hit for hit in client.endpoints_hit)


def test_ingestion_skips_apparel(test_app):
    with test_app.app_context():
        pages = {
            1: {
                "results": [
                    {
                        "id": "stockx-apparel",
                        "sku": "APP-1",
                        "name": "The North Face 1996 Retro Nuptse",
                        "category": "apparel",
                        "release_date": str(date.today()),
                    }
                ]
            }
        }
        client = FakeKicksClient(pages)
        stats = ingest_kicksdb_releases(db.session, client, per_page=100, mode="lite", backfill_threshold=999)

        assert stats["skipped_non_sneakers"] == 1
        assert Release.query.count() == 0


def test_ingestion_skips_missing_release_date(test_app):
    with test_app.app_context():
        pages = {
            1: {
                "results": [
                    {
                        "id": "stockx-missing-date",
                        "sku": "REL-3",
                        "name": "Sneaker Missing Date",
                        "category": "sneakers",
                    }
                ]
            }
        }
        client = FakeKicksClient(pages)
        stats = ingest_kicksdb_releases(db.session, client, per_page=100, mode="lite", backfill_threshold=999)

        assert stats["skipped_missing_release_date"] == 1
        assert Release.query.count() == 0


def test_ingestion_skips_out_of_window(test_app):
    with test_app.app_context():
        pages = {
            1: {
                "results": [
                    {
                        "id": "stockx-old",
                        "sku": "REL-OLD",
                        "name": "Old Release",
                        "category": "sneakers",
                        "release_date": "2020-01-01",
                    }
                ]
            }
        }
        client = FakeKicksClient(pages)
        stats = ingest_kicksdb_releases(db.session, client, per_page=100, mode="lite", backfill_threshold=999)

        assert stats["skipped_out_of_window"] == 1
        assert Release.query.count() == 0


def test_ingestion_stops_after_end_date(test_app):
    with test_app.app_context():
        start_date = date(2026, 1, 1)
        end_date = date(2026, 3, 1)
        pages = {
            1: {
                "results": [
                    {
                        "id": "stockx-early",
                        "sku": "REL-4",
                        "name": "Early Release",
                        "category": "sneakers",
                        "release_date": "2026-02-01",
                    },
                    {
                        "id": "stockx-late",
                        "sku": "REL-5",
                        "name": "Late Release",
                        "category": "sneakers",
                        "release_date": "2026-06-01",
                    },
                ]
            },
            2: {
                "results": [
                    {
                        "id": "stockx-should-not-fetch",
                        "sku": "REL-6",
                        "name": "Should Not Fetch",
                        "category": "sneakers",
                        "release_date": "2026-07-01",
                    }
                ]
            },
        }
        client = FakeKicksClient(pages)
        stats = ingest_kicksdb_releases(
            db.session,
            client,
            mode="lite",
            backfill_threshold=999,
            start_date=start_date,
            end_date=end_date,
        )

        assert stats["stop_reason"] == "end_date_reached"
        assert stats["pages_fetched"] == 1
        assert Release.query.filter_by(sku="REL-4").first() is not None


def test_goat_backfill_runs_when_below_threshold(test_app):
    with test_app.app_context():
        start_date = date(2026, 1, 1)
        end_date = date(2026, 3, 1)
        stockx_pages = {
            1: {
                "results": [
                    {
                        "id": "stockx-10",
                        "sku": "REL-10",
                        "name": "Release Ten",
                        "category": "sneakers",
                        "release_date": "2026-02-01",
                    }
                ]
            }
        }
        goat_pages = {
            1: {
                "results": [
                    {
                        "id": "goat-11",
                        "sku": "REL-11",
                        "name": "Release Eleven",
                        "category": "sneakers",
                        "release_date": "2026-02-02T23:59:59.000Z",
                        "url": "https://goat.com/rel-11",
                    }
                ]
            }
        }
        client = FakeKicksClient(stockx_pages, goat_pages=goat_pages)
        stats = ingest_kicksdb_releases(
            db.session,
            client,
            mode="lite",
            backfill_threshold=5,
            max_total_requests=6,
            start_date=start_date,
            end_date=end_date,
        )

        assert stats["goat_created"] == 1
        assert stats["goat_requests_used"] == 1


def test_goat_backfill_skipped_when_threshold_met(test_app):
    with test_app.app_context():
        start_date = date(2026, 1, 1)
        end_date = date(2026, 3, 1)
        stockx_pages = {
            1: {
                "results": [
                    {
                        "id": "stockx-20",
                        "sku": "REL-20",
                        "name": "Release Twenty",
                        "category": "sneakers",
                        "release_date": "2026-02-01",
                    }
                ]
            }
        }
        client = FakeKicksClient(stockx_pages, goat_pages={})
        stats = ingest_kicksdb_releases(
            db.session,
            client,
            mode="lite",
            backfill_threshold=1,
            max_total_requests=6,
            start_date=start_date,
            end_date=end_date,
        )

        assert stats["goat_requests_used"] == 0


def test_goat_dedupe_by_sku(test_app):
    with test_app.app_context():
        start_date = date(2026, 1, 1)
        end_date = date(2026, 3, 1)
        stockx_pages = {
            1: {
                "results": [
                    {
                        "id": "stockx-30",
                        "sku": "REL-30",
                        "name": "Release Thirty",
                        "category": "sneakers",
                        "release_date": "2026-02-01",
                        "image": {"original": "https://example.com/stockx.png"},
                    }
                ]
            }
        }
        goat_pages = {
            1: {
                "results": [
                    {
                        "id": "goat-30",
                        "sku": "REL-30",
                        "name": "Release Thirty",
                        "category": "sneakers",
                        "release_date": "2026-02-01T23:59:59.000Z",
                        "url": "https://goat.com/rel-30",
                    }
                ]
            }
        }
        client = FakeKicksClient(stockx_pages, goat_pages=goat_pages)
        stats = ingest_kicksdb_releases(
            db.session,
            client,
            mode="lite",
            backfill_threshold=5,
            max_total_requests=6,
            start_date=start_date,
            end_date=end_date,
        )

        assert stats["goat_deduped"] >= 0
        assert Release.query.filter_by(sku="REL-30").count() == 1


def test_outbound_offer_redirect(test_client, test_app):
    with test_app.app_context():
        release = Release(
            name="Redirect Release",
            release_date=date.today(),
        )
        db.session.add(release)
        db.session.commit()

        offer = AffiliateOffer(
            release_id=release.id,
            retailer="stockx",
            base_url="https://stockx.com/example",
            affiliate_url="https://aff.example.com/stockx",
            is_active=True,
        )
        db.session.add(offer)
        db.session.commit()

        response = test_client.get(f"/out/{offer.id}")
        assert response.status_code == 302
        assert response.location == "https://aff.example.com/stockx"


def test_goat_iso_dates_inserted(test_app):
    with test_app.app_context():
        start_date = date(2026, 1, 1)
        end_date = date(2026, 3, 1)
        stockx_pages = {1: {"results": []}}
        goat_pages = {
            1: {
                "results": [
                    {
                        "id": "goat-iso",
                        "sku": "REL-ISO",
                        "name": "GOAT ISO Release",
                        "category": "sneakers",
                        "release_date": "2026-02-05T23:59:59.000Z",
                        "url": "https://goat.com/rel-iso",
                    }
                ]
            }
        }
        client = FakeKicksClient(stockx_pages, goat_pages=goat_pages)
        stats = ingest_kicksdb_releases(
            db.session,
            client,
            mode="lite",
            backfill_threshold=5,
            max_total_requests=6,
            start_date=start_date,
            end_date=end_date,
        )

        assert stats["goat_created"] == 1
        assert Release.query.filter_by(sku="REL-ISO").first() is not None


def test_parse_release_date_formats():
    assert parse_release_date("20260112") == date(2026, 1, 12)
    assert parse_release_date("2026-01-12") == date(2026, 1, 12)
    assert parse_release_date("2026-01-12T23:59:59.000Z") == date(2026, 1, 12)
    assert parse_release_date("2026") is None


def test_probe_mode_no_db_writes(test_app):
    with test_app.app_context():
        pages = {
            1: {
                "results": [
                    {
                        "id": "stockx-probe",
                        "sku": "REL-PROBE",
                        "name": "Probe Release",
                        "category": "sneakers",
                        "release_date": "2026-02-01",
                    }
                ]
            }
        }
        goat_pages = {
            1: {
                "results": [
                    {
                        "id": "goat-probe",
                        "sku": "REL-PROBE-2",
                        "name": "Probe Release Goat",
                        "category": "sneakers",
                        "release_date": "20260202",
                    }
                ]
            }
        }
        client = FakeKicksClient(pages, goat_pages=goat_pages)
        stats = run_probe(client, per_page=100)

        assert stats["requests_used_total"] == 2
        assert Release.query.count() == 0


def test_build_filters_include_date_window():
    start = date(2026, 1, 5)
    end = date(2026, 5, 12)
    stockx_filter = build_stockx_filter(start, end)
    goat_filter = build_goat_filter(start, end)

    assert '(product_type = "sneakers")' in stockx_filter
    assert 'release_date >= "2026-01-05"' in stockx_filter
    assert 'release_date <= "2026-05-12"' in stockx_filter
    assert '(product_type = "sneakers")' in goat_filter
    assert 'release_date >= "2026-01-05"' in goat_filter
    assert 'release_date <= "2026-05-12"' in goat_filter


def test_pagination_respects_meta_and_max_pages(test_app):
    with test_app.app_context():
        page_one_results = [
            {
                "id": f"stockx-p1-{idx}",
                "sku": f"REL-P1-{idx}",
                "name": f"Release P1 {idx}",
                "category": "sneakers",
                "release_date": str(date.today()),
            }
            for idx in range(20)
        ]
        pages = {
            1: {
                "results": page_one_results,
                "meta": {"current_page": 1, "total_pages": 2, "per_page": 20},
            },
            2: {
                "results": [
                    {
                        "id": "stockx-p2",
                        "sku": "REL-P2",
                        "name": "Release P2",
                        "category": "sneakers",
                        "release_date": str(date.today()),
                    }
                ],
                "meta": {"current_page": 2, "total_pages": 2, "per_page": 20},
            },
        }
        client = FakeKicksClient(pages)
        stats = ingest_kicksdb_releases(
            db.session,
            client,
            mode="lite",
            per_page=100,
            backfill_threshold=999,
            max_pages_stockx=2,
            max_total_requests=2,
        )

        assert stats["stockx_requests_used"] == 2
        assert Release.query.filter_by(sku="REL-P2").first() is not None
