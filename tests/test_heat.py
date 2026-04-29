from datetime import date, timedelta, datetime
from decimal import Decimal

from extensions import db
from models import Release, ReleaseSizeBid, ReleaseSalePoint
from routes.sneakers_routes import _get_release_sales_series
from services.kicks_client import KicksClient
from services.heat_service import (
    heat_label_for_score,
    _heat_score_from_premium,
    compute_heat_for_release,
    get_comps_for_release,
)


def test_heat_label_boundaries():
    assert heat_label_for_score(24) == ("Low", "🔥")
    assert heat_label_for_score(25) == ("Medium", "🔥🔥")
    assert heat_label_for_score(49) == ("Medium", "🔥🔥")
    assert heat_label_for_score(50) == ("High", "🔥🔥🔥")
    assert heat_label_for_score(74) == ("High", "🔥🔥🔥")
    assert heat_label_for_score(75) == ("Very high", "🔥🔥🔥🔥")


def test_heat_score_mapping_boundaries():
    assert _heat_score_from_premium(1.05) == 10
    assert _heat_score_from_premium(1.2) == 25
    assert _heat_score_from_premium(1.45) == 45
    assert _heat_score_from_premium(1.8) == 65
    assert _heat_score_from_premium(2.3) == 80
    assert _heat_score_from_premium(2.8) == 90
    assert _heat_score_from_premium(3.2) == 100


def test_comps_selection_by_family(test_app):
    with test_app.app_context():
        target = Release(
            brand="Nike",
            name="Air Jordan 4 Sample",
            model_name="Jordan 4 Sample",
            release_date=date.today() + timedelta(days=10),
            retail_price=200,
            retail_currency="USD",
        )
        comp = Release(
            brand="Nike",
            name="Air Jordan 4 Retro",
            model_name="Jordan 4 Retro",
            release_date=date.today() - timedelta(days=40),
            retail_price=200,
            retail_currency="USD",
        )
        other = Release(
            brand="Nike",
            name="Dunk Low",
            model_name="Dunk Low",
            release_date=date.today() - timedelta(days=40),
            retail_price=200,
            retail_currency="USD",
        )
        db.session.add_all([target, comp, other])
        db.session.commit()

        comps = get_comps_for_release(db.session, target)
        assert comp in comps
        assert other not in comps


def test_asks_only_caps_far_from_release(test_app):
    with test_app.app_context():
        release = Release(
            brand="Nike",
            name="Jordan 4 Asks",
            model_name="Jordan 4",
            release_date=date.today() + timedelta(days=30),
            retail_price=200,
            retail_currency="USD",
        )
        db.session.add(release)
        db.session.commit()

        ask = ReleaseSizeBid(
            release_id=release.id,
            size_label="10",
            size_type="US",
            highest_bid=Decimal("480"),
            currency="USD",
            price_type="ask",
        )
        db.session.add(ask)

        for idx in range(8):
            comp = Release(
                brand="Nike",
                name=f"Jordan 4 Comp {idx}",
                model_name="Jordan 4",
                release_date=date.today() - timedelta(days=40),
                retail_price=200,
                retail_currency="USD",
            )
            db.session.add(comp)
            db.session.flush()
            db.session.add(
                ReleaseSizeBid(
                    release_id=comp.id,
                    size_label="10",
                    size_type="US",
                    highest_bid=Decimal("260"),
                    currency="USD",
                    price_type="bid",
                )
            )
        db.session.commit()

        compute_heat_for_release(db.session, release)
        assert release.heat_basis.startswith("asks_volatile_comps")
        assert release.heat_premium_ratio is not None
        assert release.heat_premium_ratio <= 1.30
        assert release.heat_confidence == "low"


def test_asks_only_caps_near_release(test_app):
    with test_app.app_context():
        release = Release(
            brand="Nike",
            name="Jordan 4 Asks Near",
            model_name="Jordan 4",
            release_date=date.today() + timedelta(days=3),
            retail_price=200,
            retail_currency="USD",
        )
        db.session.add(release)
        db.session.commit()

        db.session.add(
            ReleaseSizeBid(
                release_id=release.id,
                size_label="10",
                size_type="US",
                highest_bid=Decimal("520"),
                currency="USD",
                price_type="ask",
            )
        )
        for idx in range(8):
            comp = Release(
                brand="Nike",
                name=f"Jordan 4 Near Comp {idx}",
                model_name="Jordan 4",
                release_date=date.today() - timedelta(days=40),
                retail_price=200,
                retail_currency="USD",
            )
            db.session.add(comp)
            db.session.flush()
            db.session.add(
                ReleaseSizeBid(
                    release_id=comp.id,
                    size_label="10",
                    size_type="US",
                    highest_bid=Decimal("260"),
                    currency="USD",
                    price_type="bid",
                )
            )
        db.session.commit()

        compute_heat_for_release(db.session, release)
        assert release.heat_basis.startswith("asks_volatile_comps")
        assert release.heat_premium_ratio is not None
        assert release.heat_premium_ratio <= 1.60


def test_enrichment_switches_to_bids_based(test_app):
    with test_app.app_context():
        release = Release(
            brand="Nike",
            name="Jordan 4 Enrich",
            model_name="Jordan 4",
            release_date=date.today() + timedelta(days=10),
            retail_price=200,
            retail_currency="USD",
        )
        db.session.add(release)
        db.session.commit()

        db.session.add(
            ReleaseSizeBid(
                release_id=release.id,
                size_label="10",
                size_type="US",
                highest_bid=Decimal("480"),
                currency="USD",
                price_type="ask",
            )
        )
        db.session.commit()

        compute_heat_for_release(db.session, release, force=True)
        assert release.heat_basis.startswith("asks_volatile_comps") or release.heat_basis == "insufficient_data"

        ask_row = ReleaseSizeBid.query.filter_by(release_id=release.id, size_label="10", size_type="US").first()
        ask_row.highest_bid = Decimal("220")
        ask_row.price_type = "bid"
        db.session.commit()

        compute_heat_for_release(db.session, release, force=True)
        assert release.heat_basis == "bids_based"


def test_heat_caching_skips_recent_update(test_app):
    with test_app.app_context():
        release = Release(
            brand="Nike",
            name="Cached Heat",
            model_name="Cached Heat",
            release_date=date.today() + timedelta(days=5),
            retail_price=200,
            retail_currency="USD",
            heat_score=20,
            heat_basis="comps_only",
            heat_confidence="low",
            heat_updated_at=datetime.utcnow(),
        )
        db.session.add(release)
        db.session.commit()

        compute_heat_for_release(db.session, release)
        assert release.heat_score == 20


def test_sales_series_dedupes_duplicate_timestamps(test_app, monkeypatch):
    with test_app.app_context():
        release = Release(
            brand="Nike",
            name="Duplicate Sales",
            model_name="Duplicate Sales",
            release_date=date.today() - timedelta(days=1),
            retail_price=200,
            retail_currency="USD",
            source="kicksdb_stockx",
            source_product_id="dup-1",
        )
        db.session.add(release)
        db.session.commit()

        dup_time = "2026-03-23T09:09:35Z"
        payload = {
            "data": [
                {"created_at": dup_time, "amount": 382},
                {"created_at": dup_time, "amount": 382},
            ]
        }

        def fake_sales_history(self, product_id, limit=15, page=1, variant_id=None):
            return payload

        monkeypatch.setattr(KicksClient, "get_stockx_sales_history", fake_sales_history)

        rows, _ = _get_release_sales_series(release, max_points=30)
        assert len(rows) == 1
