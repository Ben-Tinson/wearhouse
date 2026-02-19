# tests/test_sneaker_lookup.py
from datetime import datetime, timedelta

from extensions import db
from models import SneakerDB
from services.sneaker_lookup_service import lookup_or_fetch_sneaker


class FakeKicksClient:
    def __init__(self, stockx_data=None, goat_data=None, goat_detail=None):
        self.stockx_data = stockx_data or {"results": []}
        self.goat_data = goat_data or {"results": []}
        self.goat_detail = goat_detail or {}
        self.calls = {"search_stockx": 0, "search_goat": 0, "get_goat_product": 0}
        self.request_count = 0

    def search_stockx(self, query, include_traits=True):
        self.calls["search_stockx"] += 1
        self.request_count += 1
        return self.stockx_data

    def search_goat(self, query):
        self.calls["search_goat"] += 1
        self.request_count += 1
        return self.goat_data

    def get_goat_product(self, id_or_slug):
        self.calls["get_goat_product"] += 1
        self.request_count += 1
        return self.goat_detail


def test_lookup_local_hit_skips_external(test_app):
    with test_app.app_context():
        record = SneakerDB(
            sku="SB123",
            model_name="Jordan 4 SB",
            name="Jordan 4 SB",
            last_synced_at=datetime.utcnow(),
        )
        db.session.add(record)
        db.session.commit()

        client = FakeKicksClient()
        result = lookup_or_fetch_sneaker("SB123", db.session, client)

        assert result["status"] == "ok"
        assert result["source"] == "cache"
        assert result["sneaker"]["sku"] == "SB123"
        assert client.calls["search_stockx"] == 0
        assert client.calls["search_goat"] == 0


def test_lookup_cache_miss_fetches_and_persists(test_app):
    with test_app.app_context():
        stockx_data = {
            "results": [
                {
                    "sku": "SB124",
                    "name": "Jordan 4 SB Pine Green",
                    "colorway": "Pine Green",
                    "brand": "Jordan",
                    "retailPrice": 200,
                    "id": "stockx-123",
                    "slug": "jordan-4-sb-pine-green",
                    "lowestAsk": 250,
                    "image": {"original": "https://example.com/stockx.png"},
                }
            ]
        }
        goat_data = {
            "results": [
                {
                    "sku": "SB124",
                    "name": "Jordan 4 SB Pine Green",
                    "colorway": "Pine Green",
                    "brand": "Jordan",
                    "retail_price": 200,
                    "id": "goat-456",
                    "slug": "jordan-4-sb-pine-green",
                    "lowest_ask": 245,
                    "image_url": "https://example.com/goat.png",
                }
            ]
        }
        client = FakeKicksClient(stockx_data=stockx_data, goat_data=goat_data)

        result = lookup_or_fetch_sneaker("Jordan 4 SB", db.session, client)

        assert result["status"] == "ok"
        assert result["source"] == "kicksdb"
        assert result["sneaker"]["sku"] == "SB124"
        assert client.calls["search_stockx"] == 1
        assert client.calls["search_goat"] == 1

        persisted = SneakerDB.query.filter_by(sku="SB124").first()
        assert persisted is not None
        assert persisted.stockx_id == "stockx-123"
        assert persisted.goat_id == "goat-456"
        assert persisted.last_synced_at is not None


def test_lookup_stale_record_refreshes(test_app):
    with test_app.app_context():
        record = SneakerDB(
            sku="SB125",
            model_name="Jordan 4 SB Old",
            name="Jordan 4 SB Old",
            last_synced_at=datetime.utcnow() - timedelta(days=2),
        )
        db.session.add(record)
        db.session.commit()

        stockx_data = {
            "results": [
                {
                    "sku": "SB125",
                    "name": "Jordan 4 SB Updated",
                    "colorway": "Gray",
                    "brand": "Jordan",
                    "retailPrice": 210,
                    "id": "stockx-999",
                    "slug": "jordan-4-sb-updated",
                    "lowestAsk": 260,
                }
            ]
        }
        goat_data = {"results": []}
        client = FakeKicksClient(stockx_data=stockx_data, goat_data=goat_data)

        result = lookup_or_fetch_sneaker("SB125", db.session, client)

        assert result["status"] == "ok"
        assert client.calls["search_stockx"] == 1


def test_lookup_calls_goat_when_missing_fields(test_app):
    with test_app.app_context():
        stockx_data = {
            "results": [
                {
                    "sku": "SB126",
                    "name": "Jordan 4 SB Missing Fields",
                    "brand": "Jordan",
                    "retailPrice": 200,
                    "id": "stockx-126",
                    "slug": "jordan-4-sb-missing",
                }
            ]
        }
        goat_data = {
            "results": [
                {
                    "sku": "SB126",
                    "name": "Jordan 4 SB Missing Fields",
                    "colorway": "Pine Green",
                    "brand": "Jordan",
                    "image_url": "https://example.com/goat.png",
                    "lowest_ask": 240,
                }
            ]
        }
        client = FakeKicksClient(stockx_data=stockx_data, goat_data=goat_data)

        result = lookup_or_fetch_sneaker("SB126", db.session, client)

        assert result["status"] == "ok"
        assert client.calls["search_stockx"] == 1
        assert client.calls["search_goat"] == 1


def test_lookup_full_mode_allows_detail_call(test_app):
    with test_app.app_context():
        stockx_data = {
            "results": [
                {
                    "sku": "SB127",
                    "name": "Jordan 4 SB Detail",
                    "brand": "Jordan",
                }
            ]
        }
        goat_data = {
            "results": [
                {
                    "sku": "SB127",
                    "name": "Jordan 4 SB Detail",
                    "brand": "Jordan",
                    "slug": "jordan-4-sb-detail",
                }
            ]
        }
        goat_detail = {
            "variants": [
                {"lowest_ask": 210},
                {"lowest_ask": 205},
            ]
        }
        client = FakeKicksClient(stockx_data=stockx_data, goat_data=goat_data, goat_detail=goat_detail)

        result = lookup_or_fetch_sneaker("SB127", db.session, client, mode="full")

        assert result["status"] == "ok"
        assert client.calls["search_stockx"] == 1
        assert client.calls["search_goat"] == 1
        assert client.calls["get_goat_product"] == 1


def test_materials_cached_from_description_no_api_call(test_app):
    with test_app.app_context():
        record = SneakerDB(
            sku="CT8012-104",
            model_name="Air Jordan 1",
            name="Air Jordan 1",
            description="Leather upper with suede overlays.",
            last_synced_at=datetime.utcnow(),
        )
        db.session.add(record)
        db.session.commit()

        client = FakeKicksClient()
        result = lookup_or_fetch_sneaker("CT8012 104", db.session, client)

        assert result["status"] == "ok"
        assert result["source"] == "cache"
        assert client.calls["search_stockx"] == 0
        assert client.calls["search_goat"] == 0

        updated = SneakerDB.query.filter_by(sku="CT8012-104").first()
        assert updated.primary_material == "Leather"
        assert updated.materials_json is not None


def test_materials_fresh_skips_recompute(test_app):
    with test_app.app_context():
        record = SneakerDB(
            sku="CT8012-104",
            model_name="Air Jordan 1",
            name="Air Jordan 1",
            description="Leather upper with suede overlays.",
            primary_material="Leather",
            materials_json='["Leather", "Suede"]',
            materials_updated_at=datetime.utcnow(),
            last_synced_at=datetime.utcnow(),
        )
        db.session.add(record)
        db.session.commit()

        original_updated_at = record.materials_updated_at
        client = FakeKicksClient()
        result = lookup_or_fetch_sneaker("CT8012-104", db.session, client)

        assert result["status"] == "ok"
        assert result["source"] == "cache"
        refreshed = SneakerDB.query.filter_by(sku="CT8012-104").first()
        assert refreshed.materials_updated_at == original_updated_at
