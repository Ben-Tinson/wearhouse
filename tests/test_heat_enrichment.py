from datetime import date, timedelta
from decimal import Decimal

from extensions import db
from models import Release, ReleaseSizeBid
from release_updater import _enrich_top_releases


class FakeClient:
    def get_stockx_product(self, product_id, include_variants=True, include_market=True):
        return {
            "variants": [
                {"size": "10", "highestBid": "210", "lowestAsk": "240"},
            ]
        }

    def get_stockx_sales_history(self, product_id, limit=50, page=1, variant_id=None):
        return {"sales": []}


def test_enrichment_respects_request_budget(test_app):
    with test_app.app_context():
        release_one = Release(
            brand="Nike",
            name="Ask Only One",
            model_name="Jordan 4",
            release_date=date.today() + timedelta(days=10),
            retail_price=200,
            retail_currency="USD",
            source="kicksdb_stockx",
            source_product_id="prod_1",
        )
        release_two = Release(
            brand="Nike",
            name="Ask Only Two",
            model_name="Jordan 4",
            release_date=date.today() + timedelta(days=12),
            retail_price=200,
            retail_currency="USD",
            source="kicksdb_stockx",
            source_product_id="prod_2",
        )
        db.session.add_all([release_one, release_two])
        db.session.commit()

        for release in (release_one, release_two):
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

        stats = _enrich_top_releases(
            db_session=db.session,
            client=FakeClient(),
            top_n=2,
            max_total_requests=1,
            enrich_sources=["bids"],
            window_days=120,
        )

        assert stats["calls_used"] == 1
        assert stats["releases_enriched"] == 1

        bid_rows = ReleaseSizeBid.query.filter_by(price_type="bid").all()
        ask_rows = ReleaseSizeBid.query.filter_by(price_type="ask").all()
        assert len(bid_rows) == 1
        assert len(ask_rows) == 2
