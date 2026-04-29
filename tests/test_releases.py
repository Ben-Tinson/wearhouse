# tests/test_releases.py
import pytest

import routes.main_routes as main_routes
import routes.sneakers_routes as sneakers_routes
from models import Release, User, AffiliateOffer, ReleaseSalePoint, ReleaseRegion, ReleaseMarketStats, ExchangeRate, SneakerDB
from extensions import db
from datetime import date, datetime, timedelta
from decimal import Decimal
from routes.main_routes import _upsert_release_market_stats
from utils.slugs import build_product_key, build_product_slug

REAL_ENSURE_RELEASE_FOR_SKU_WITH_RESALE = main_routes._ensure_release_for_sku_with_resale
REAL_REFRESH_RESALE_FOR_RELEASE = main_routes._refresh_resale_for_release

@pytest.fixture(autouse=True)
def stub_release_detail_network(monkeypatch):
    monkeypatch.setattr(main_routes, "_ensure_release_for_sku_with_resale", lambda sku: None)
    monkeypatch.setattr(main_routes, "_refresh_resale_for_release", lambda release: False)
    monkeypatch.setattr(
        main_routes,
        "_get_release_size_bids",
        lambda release, allow_live_refresh=True: ([], None),
    )

def test_admin_can_add_release(test_client, auth, admin_user):
    """
    GIVEN a logged-in admin user
    WHEN they submit the 'add_release' form with valid data
    THEN check that a new Release object is created in the database
    """
    # Log in as the admin user provided by our new fixture
    auth.login(username=admin_user.username, password='password123')

    # Define the data for our new release
    tomorrow = date.today() + timedelta(days=1)
    release_data = {
        'brand': 'Testsuo',
        'model_name': 'Shima',
        'name': 'Shima',
        'sku': 'SHI-001',
        'release_date': tomorrow.strftime('%Y-%m-%d'),
        'retail_price': '190.00',
        'retail_currency': 'USD',
        'image_option': 'url', # Assuming we are providing a URL
        'image_url': 'http://example.com/image.jpg'
    }

    # POST the data to the add_release route
    response = test_client.post('/admin/add-release', data=release_data, follow_redirects=True)

    # 1. Check the response
    assert response.status_code == 200 # Should redirect to the calendar page
    assert b"New release has been added" in response.data # Check for the flash message

    # 2. Check the database directly to confirm creation
    new_release = Release.query.filter_by(name='Shima').first()
    assert new_release is not None
    assert new_release.brand == 'Testsuo'
    assert new_release.release_date == tomorrow

def test_non_admin_cannot_access_add_release_page(test_client, auth, init_database):
    """
    GIVEN a logged-in non-admin user
    WHEN they attempt to access the '/admin/add_release' page via GET or POST
    THEN check that they receive a 403 Forbidden error
    """
    # The 'init_database' fixture provides a standard, non-admin user
    user, _ = init_database

    # 1. Log in as the regular user
    auth.login(username=user.username, password='password123')

    # 2. Attempt to access the page with a GET request
    response_get = test_client.get('/admin/add-release')

    # Assert that access is forbidden
    assert response_get.status_code == 403

    # 3. Attempt to submit data with a POST request
    tomorrow = date.today() + timedelta(days=1)
    release_data = {
        'brand': 'Unauthorized',
        'name': 'Entry',
        'release_date': tomorrow.strftime('%Y-%m-%d'),
        'image_option': 'url'
    }
    response_post = test_client.post('/admin/add-release', data=release_data)

    # Assert that this action is also forbidden
    assert response_post.status_code == 403

    # 4. Double-check that no release was created
    unauthorized_release = Release.query.filter_by(brand='Unauthorized').first()
    assert unauthorized_release is None


def test_release_detail_page_shows_release(test_client, test_app):
    with test_app.app_context():
        release = Release(
            name="Detail Release",
            brand="Nike",
            release_date=date.today() + timedelta(days=1),
        )
        db.session.add(release)
        db.session.commit()

        product_key = build_product_key(release)
        product_slug = build_product_slug(release)
        response = test_client.get(f"/products/{product_key}-{product_slug}")
        assert response.status_code == 200
        assert b"Detail Release" in response.data


def test_release_detail_uses_source_identifiers_to_match_sneakerdb(test_client, test_app):
    with test_app.app_context():
        release = Release(
            name="Source Match Release",
            brand="Nike",
            sku="REL-123",
            source="kicksdb_stockx",
            source_product_id="stockx-prod-123",
            source_slug="stockx-source-match",
            release_date=date.today() + timedelta(days=1),
        )
        sneaker_record = SneakerDB(
            sku="OTHER-123",
            name="Source Match Sneaker",
            stockx_id="stockx-prod-123",
            stockx_slug="stockx-source-match",
            description="Matched from SneakerDB source identifiers.",
            primary_material="Leather",
        )
        db.session.add_all([release, sneaker_record])
        db.session.commit()

        product_key = build_product_key(release)
        product_slug = build_product_slug(release)
        response = test_client.get(f"/products/{product_key}-{product_slug}")
        assert response.status_code == 200
        assert b"About this release" in response.data
        assert b"Matched from SneakerDB source identifiers." in response.data


def test_release_detail_get_does_not_trigger_server_side_market_refresh(
    test_client, test_app, monkeypatch
):
    with test_app.app_context():
        release = Release(
            name="Cached Release",
            brand="Nike",
            sku="CACHE-123",
            release_date=date.today() + timedelta(days=1),
            is_calendar_visible=True,
        )
        db.session.add(release)
        db.session.commit()
        product_key = build_product_key(release)
        product_slug = build_product_slug(release)

    def fail_refresh(*args, **kwargs):
        raise AssertionError("server-side refresh should not run during normal GET")

    monkeypatch.setattr(main_routes, "_ensure_release_for_sku_with_resale", fail_refresh)
    monkeypatch.setattr(main_routes, "_refresh_resale_for_release", fail_refresh)

    response = test_client.get(f"/products/{product_key}-{product_slug}")
    assert response.status_code == 200
    assert b"Cached Release" in response.data


def test_release_detail_async_refresh_returns_size_bid_series(
    test_client, test_app, auth, init_database, monkeypatch
):
    class FakeBid:
        def __init__(self, label, size_type, value, currency="USD", price_type="ask"):
            self.size_label = label
            self.size_type = size_type
            self.highest_bid = Decimal(value)
            self.currency = currency
            self.price_type = price_type

    with test_app.app_context():
        test_app.config["KICKS_API_KEY"] = "test-key"
        release = Release(
            name="Async Size Bid Release",
            brand="Nike",
            sku="ASYNC-123",
            source="kicksdb_stockx",
            source_product_id="async-stockx-id",
            source_slug="async-stockx-slug",
            release_date=date.today() + timedelta(days=1),
        )
        db.session.add(release)
        db.session.commit()
        release_id = release.id

    auth.login()
    monkeypatch.setattr(main_routes, "_refresh_resale_for_release", lambda release, max_per_day=3: False)
    monkeypatch.setattr(
        main_routes,
        "_get_release_size_bids",
        lambda release, allow_live_refresh=True: (
            [FakeBid("9", "US", "250.00", "USD", "ask")],
            datetime(2026, 4, 13, 12, 0, 0),
        ),
    )

    response = test_client.post(f"/releases/{release_id}/refresh-resale")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["size_bid_series"] == [
        {
            "label": "9",
            "size_type": "US",
            "value": 250.0,
            "currency": "USD",
            "price_type": "ask",
        }
    ]
    assert payload["size_type_options"] == ["US"]
    assert payload["size_type_default"] == "US"
    assert payload["size_bids_fetched_at"] == "2026-04-13T12:00:00"


def test_ensure_release_for_sku_reuses_existing_external_identity(test_app, monkeypatch):
    with test_app.app_context():
        test_app.config["KICKS_API_KEY"] = "test-key"
        existing_release = Release(
            name="Existing External Release",
            brand="Nike",
            sku="EXIST-123",
            source="kicksdb_stockx",
            source_product_id="shared-stockx-id",
            source_slug="shared-stockx-slug",
            release_date=date.today() + timedelta(days=1),
        )
        stale_release = Release(
            name="Stale SKU Release",
            brand="Nike",
            sku="STALE-123",
            release_date=date.today() + timedelta(days=1),
        )
        db.session.add_all([existing_release, stale_release])
        db.session.commit()

        class FakeClient:
            pass

        def fake_lookup(**kwargs):
            return {
                "status": "ok",
                "sneaker": {
                    "sku": "STALE-123",
                    "model_name": "Resolved Model",
                    "brand": "Nike",
                    "colorway": "Black/White",
                    "stockx_id": "shared-stockx-id",
                    "stockx_slug": "shared-stockx-slug",
                    "retail_currency": "USD",
                },
            }

        monkeypatch.setattr(main_routes, "KicksClient", lambda *args, **kwargs: FakeClient())
        monkeypatch.setattr(main_routes, "lookup_or_fetch_sneaker", fake_lookup)
        monkeypatch.setattr(main_routes, "_refresh_resale_for_release", lambda release, *args, **kwargs: False)

        release = REAL_ENSURE_RELEASE_FOR_SKU_WITH_RESALE("STALE-123")

        db.session.expire_all()
        stale = db.session.get(Release, stale_release.id)
        reused = db.session.get(Release, existing_release.id)
        assert release.id == existing_release.id
        assert reused.source_product_id == "shared-stockx-id"
        assert stale.source is None
        assert stale.source_product_id is None


def test_admin_refresh_market_reuses_conflicting_release_identity(
    test_client, test_app, auth, admin_user, monkeypatch
):
    with test_app.app_context():
        test_app.config["KICKS_API_KEY"] = "test-key"
        existing_release = Release(
            name="Existing External Release",
            brand="Nike",
            sku="EXIST-999",
            source="kicksdb_stockx",
            source_product_id="refresh-shared-id",
            source_slug="refresh-shared-slug",
            release_date=date.today() + timedelta(days=1),
        )
        stale_release = Release(
            name="Refresh Target Release",
            brand="Nike",
            sku="REFRESH-123",
            release_date=date.today() + timedelta(days=1),
        )
        db.session.add_all([existing_release, stale_release])
        db.session.commit()
        stale_id = stale_release.id
        existing_id = existing_release.id

    auth.login(username=admin_user.username, password='password123')

    class FakeClient:
        pass

    def fake_lookup(**kwargs):
        return {
            "status": "ok",
            "sneaker": {
                "sku": "REFRESH-123",
                "model_name": "Resolved Refresh Model",
                "brand": "Nike",
                "colorway": "Black/White",
                "stockx_id": "refresh-shared-id",
                "stockx_slug": "refresh-shared-slug",
                "retail_currency": "USD",
            },
        }

    monkeypatch.setattr(main_routes, "_ensure_release_for_sku_with_resale", REAL_ENSURE_RELEASE_FOR_SKU_WITH_RESALE)
    monkeypatch.setattr(main_routes, "KicksClient", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr(main_routes, "lookup_or_fetch_sneaker", fake_lookup)
    monkeypatch.setattr(main_routes, "_refresh_resale_for_release", lambda release, *args, **kwargs: False)
    monkeypatch.setattr(main_routes, "_get_release_sales_series", lambda release, max_points=30: ([], None))
    monkeypatch.setattr(main_routes, "_get_release_size_bids", lambda release, allow_live_refresh=True: ([], None))

    response = test_client.post(
        f"/admin/releases/{stale_id}/refresh-market",
        data={"csrf_token": "", "next": ""},
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = response.headers["Location"]
    assert "EXIST_999-existing-external-release" in location
    assert "REFRESH_123" not in location


def test_release_detail_renders_async_refresh_hook_when_size_bids_missing(
    test_client, test_app, auth, init_database, monkeypatch
):
    with test_app.app_context():
        test_app.config["KICKS_API_KEY"] = "test-key"
        release = Release(
            name="Needs Size Bid Refresh",
            brand="Nike",
            sku="ASYNC-456",
            source="kicksdb_stockx",
            source_product_id="async-stockx-id-2",
            source_slug="async-stockx-slug-2",
            release_date=date.today() + timedelta(days=1),
        )
        offer = AffiliateOffer(
            release_id=1,
            retailer="stockx",
            base_url="https://example.com/stockx",
            offer_type="aftermarket",
            price=Decimal("200.00"),
            currency="USD",
            is_active=True,
            last_checked_at=datetime.utcnow(),
        )
        db.session.add(release)
        db.session.commit()
        offer.release_id = release.id
        db.session.add(offer)
        db.session.commit()
        product_key = build_product_key(release)
        product_slug = build_product_slug(release)

    auth.login()
    monkeypatch.setattr(main_routes, "_get_release_size_bids", lambda release, allow_live_refresh=True: ([], None))

    response = test_client.get(f"/products/{product_key}-{product_slug}")
    assert response.status_code == 200
    assert f"/releases/{release.id}/refresh-resale".encode() in response.data


def test_kicksdb_market_stats_default_to_usd_currency(test_app):
    with test_app.app_context():
        release = Release(
            name="Currency Source Release",
            brand="Nike",
            sku="CUR-123",
            source="kicksdb_stockx",
            retail_currency="GBP",
            release_date=date.today() + timedelta(days=1),
        )
        db.session.add(release)
        db.session.commit()

        changed = _upsert_release_market_stats(
            release,
            {
                "statistics": {
                    "annual_volatility": 36.84,
                    "annual_range_low": "167.00",
                    "annual_range_high": "362.00",
                }
            },
        )
        assert changed is True
        db.session.commit()

        stats = ReleaseMarketStats.query.filter_by(release_id=release.id).first()
        assert stats is not None
        assert stats.currency == "USD"
        assert stats.sales_price_range_low == Decimal("167.00")
        assert stats.sales_price_range_high == Decimal("362.00")


def test_normalize_kicks_detail_supports_attributes_wrapped_market_stats(test_app):
    with test_app.app_context():
        release = Release(
            name="GOAT Wrapped Stats Release",
            brand="Nike",
            sku="GOAT-123",
            source="kicksdb_goat",
            retail_currency="USD",
            release_date=date.today() + timedelta(days=1),
        )
        db.session.add(release)
        db.session.commit()

        payload = {
            "data": {
                "id": "goat-prod-1",
                "type": "product",
                "attributes": {
                    "name": "GOAT Wrapped Stats Release",
                    "lowest_ask": "215.00",
                    "statistics": {
                        "annual_volatility": 28.1,
                        "annual_range_low": "180.00",
                        "annual_range_high": "295.00",
                        "annual_average_price": "240.00",
                        "sales_volume": 18,
                    },
                },
            }
        }

        normalized = main_routes._normalize_kicks_detail(payload)
        assert normalized["lowest_ask"] == "215.00"
        assert normalized["statistics"]["annual_average_price"] == "240.00"

        changed = _upsert_release_market_stats(release, normalized)
        assert changed is True
        db.session.commit()

        stats = ReleaseMarketStats.query.filter_by(release_id=release.id).first()
        assert stats is not None
        assert stats.currency == "USD"
        assert stats.average_price_1y == Decimal("240.00")
        assert stats.volatility == 28.1
        assert stats.sales_price_range_low == Decimal("180.00")
        assert stats.sales_price_range_high == Decimal("295.00")
        assert stats.sales_volume == 18


def test_goat_detail_persists_release_identity_description_and_retail_info(test_app):
    with test_app.app_context():
        release = Release(
            name="GOAT Mapping Release",
            brand="Nike",
            sku="GOAT-MAP-1",
            release_date=date.today() + timedelta(days=1),
        )
        db.session.add(release)
        db.session.commit()

        changed = main_routes._update_release_from_detail(
            release,
            {
                "id": "goat-123",
                "slug": "goat-mapped-product",
                "description": "Mapped from GOAT detail.",
                "retail_prices": [{"amount": "190.00", "currency": "USD"}],
            },
        )
        db.session.commit()

        assert changed is True
        assert release.source == "kicksdb_goat"
        assert release.source_product_id == "goat-123"
        assert release.source_slug == "goat-mapped-product"
        assert release.description == "Mapped from GOAT detail."
        assert release.retail_price == Decimal("190.00")
        assert release.retail_currency == "USD"


def test_stockx_detail_promotes_canonical_source_over_goat_when_available(test_app):
    with test_app.app_context():
        release = Release(
            name="Source Priority Release",
            brand="Nike",
            sku="SRC-PRIORITY-1",
            source="kicksdb_goat",
            source_product_id="goat-123",
            source_slug="goat-slug",
            release_date=date.today() + timedelta(days=1),
        )
        db.session.add(release)
        db.session.commit()

        changed = main_routes._update_release_from_detail(
            release,
            {
                "id": "stockx-999",
                "slug": "stockx-priority-slug",
                "retailPrice": "210",
            },
            source_hint="stockx",
        )
        db.session.commit()

        assert changed is True
        assert release.source == "kicksdb_stockx"
        assert release.source_product_id == "stockx-999"
        assert release.source_slug == "stockx-priority-slug"


def test_goat_detail_does_not_override_existing_stockx_canonical_source(test_app):
    with test_app.app_context():
        release = Release(
            name="StockX Canonical Release",
            brand="Nike",
            sku="SRC-PRIORITY-2",
            source="kicksdb_stockx",
            source_product_id="stockx-111",
            source_slug="stockx-slug-111",
            release_date=date.today() + timedelta(days=1),
        )
        db.session.add(release)
        db.session.commit()

        changed = main_routes._update_release_from_detail(
            release,
            {
                "id": "goat-222",
                "slug": "goat-slug-222",
                "description": "GOAT data is supplemental.",
            },
            source_hint="goat",
        )
        db.session.commit()

        assert changed is True
        assert release.source == "kicksdb_stockx"
        assert release.source_product_id == "stockx-111"
        assert release.source_slug == "stockx-slug-111"
        assert release.description == "GOAT data is supplemental."


def test_goat_weekly_orders_map_to_sales_volume(test_app):
    with test_app.app_context():
        release = Release(
            name="GOAT Weekly Orders Release",
            brand="Nike",
            sku="GOAT-ORDERS-1",
            source="kicksdb_goat",
            release_date=date.today() + timedelta(days=1),
        )
        db.session.add(release)
        db.session.commit()

        changed = main_routes._upsert_release_market_stats(
            release,
            {"weekly_orders": [{"orders": 3}, {"orders": 4}, {"orders": "2"}]},
            source_label="goat",
        )
        assert changed is True
        db.session.commit()

        stats = ReleaseMarketStats.query.filter_by(release_id=release.id).first()
        assert stats is not None
        assert stats.sales_volume == 9
        assert stats.currency == "USD"


def test_get_release_size_bids_supports_goat_variant_asks(test_app, monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_goat_product(self, id_or_slug, **kwargs):
            return {
                "data": {
                    "id": id_or_slug,
                    "variants": [
                        {"size": "9", "lowest_ask": "210.00"},
                        {"size": "10", "prices": {"lowest_ask": "220.00"}},
                    ],
                }
            }

    with test_app.app_context():
        test_app.config["KICKS_API_KEY"] = "test-key"
        release = Release(
            name="GOAT Size Release",
            brand="Nike",
            sku="GOAT-SIZE-1",
            source="kicksdb_goat",
            source_product_id="goat-size-123",
            release_date=date.today() + timedelta(days=1),
        )
        db.session.add(release)
        db.session.commit()

        monkeypatch.setattr(sneakers_routes, "KicksClient", FakeClient)
        bids, fetched_at = sneakers_routes._get_release_size_bids(release, allow_live_refresh=True)

        assert fetched_at is not None
        assert len(bids) == 2
        assert {(bid.size_label, bid.price_type, bid.currency) for bid in bids} == {
            ("9", "ask", "USD"),
            ("10", "ask", "USD"),
        }


def test_mixed_source_refresh_prefers_stockx_canonical_when_stockx_offer_exists(
    test_app, monkeypatch
):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.stockx_called = False
            self.goat_called = False
            self.goat_request_arg = None
            self.stockx_request_arg = None
            self.stockx_search_called = False

        def get_stockx_product(self, id_or_slug, *args, **kwargs):
            self.stockx_called = True
            self.stockx_request_arg = id_or_slug
            return {
                "product": {
                    "id": "stockx-123",
                    "slug": "stockx-mapped-product",
                    "retailPrice": 150,
                    "statistics": {
                        "last_90_days_average_price": 210,
                        "annual_average_price": 220,
                        "annual_volatility": 0.21,
                        "annual_sales_count": 11,
                    },
                    "market": {
                        "last_90_days_range_low": 180,
                        "last_90_days_range_high": 260,
                    },
                }
            }

        def get_stockx_sales_history(self, *args, **kwargs):
            return {"data": []}

        def search_stockx(self, query, include_traits=True):
            self.stockx_search_called = True
            return {"data": []}

        def get_goat_product(self, id_or_slug, **kwargs):
            self.goat_called = True
            self.goat_request_arg = id_or_slug
            return {
                "product": {
                    "id": "019d30ec-8b69-7ce4-9f59-f4d9752f626d",
                    "title": "Nike Air Max 90 Ultramarine (2026)",
                    "brand": "Nike",
                    "model": "Nike Air Max 90",
                    "description": "",
                    "sku": "IU0767-001",
                    "slug": "nike-air-max-90-ultramarine-2026",
                    "weekly_orders": 79,
                    "min_price": 263,
                    "max_price": 450,
                    "avg_price": 309.5882352941176,
                    "variants": [
                        {"size": "9", "lowest_ask": 263},
                        {"size": "10", "lowest_ask": 290},
                    ],
                    "statistics": {
                        "annual_high": 416,
                        "annual_low": 151,
                        "annual_range_high": 355,
                        "annual_range_low": 242,
                        "annual_sales_count": 15,
                        "annual_average_price": 230,
                        "annual_volatility": 0.188464,
                        "annual_price_premium": 0.992,
                        "annual_total_dollars": 3449,
                        "last_90_days_range_high": 416,
                        "last_90_days_range_low": 151,
                        "last_90_days_sales_count": 15,
                        "last_90_days_average_price": 230,
                    },
                }
            }

    fake_client = FakeClient()

    with test_app.app_context():
        test_app.config["KICKS_API_KEY"] = "test-key"
        release = Release(
            name="GOAT Refresh Branch Release",
            brand="Nike",
            sku="IU0767-001",
            source="kicksdb_goat",
            source_product_id="1748509",
            source_slug="nike-air-max-90-ultramarine-iu0767-001",
            release_date=date.today() + timedelta(days=1),
        )
        db.session.add(release)
        db.session.commit()
        db.session.add_all(
            [
                AffiliateOffer(
                    release_id=release.id,
                    retailer="stockx",
                    base_url="https://stockx.com/example",
                    offer_type="aftermarket",
                    is_active=True,
                ),
                AffiliateOffer(
                    release_id=release.id,
                    retailer="goat",
                    base_url="https://www.goat.com/sneakers/nike-air-max-90-ultramarine-iu0767-001",
                    offer_type="aftermarket",
                    is_active=True,
                ),
            ]
        )
        db.session.commit()

        monkeypatch.setattr(main_routes, "KicksClient", lambda *args, **kwargs: fake_client)
        monkeypatch.setattr(main_routes, "_refresh_resale_for_release", REAL_REFRESH_RESALE_FOR_RELEASE)

        updated = main_routes._refresh_resale_for_release(release, force_refresh=True)
        db.session.commit()

        stats = ReleaseMarketStats.query.filter_by(release_id=release.id).first()
        stockx_offer = AffiliateOffer.query.filter_by(release_id=release.id, retailer="stockx").first()
        goat_offer = AffiliateOffer.query.filter_by(release_id=release.id, retailer="goat").first()

        assert updated is True
        assert fake_client.goat_called is True
        assert fake_client.stockx_called is True
        assert fake_client.stockx_request_arg == "example"
        assert fake_client.stockx_search_called is False
        assert release.source == "kicksdb_stockx"
        assert release.source_product_id == "stockx-123"
        assert release.source_slug == "stockx-mapped-product"
        assert stats is not None
        assert stats.average_price_3m == Decimal("230")
        assert stats.average_price_1y == Decimal("230")
        assert stats.sales_price_range_low == Decimal("242")
        assert stats.sales_price_range_high == Decimal("355")
        assert stats.sales_volume == 15
        assert stockx_offer.price == Decimal("210")
        assert stockx_offer.currency == "USD"
        assert goat_offer.price == Decimal("263")


def test_mixed_source_refresh_uses_stockx_sku_fallback_when_offer_url_missing(
    test_app, monkeypatch
):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.stockx_called = False
            self.stockx_request_arg = None
            self.stockx_search_called = False
            self.stockx_search_query = None
            self.goat_called = False

        def search_stockx(self, query, include_traits=True):
            self.stockx_search_called = True
            self.stockx_search_query = query
            return {
                "data": [
                    {
                        "id": "stockx-fallback-456",
                        "slug": "stockx-fallback-product",
                        "sku": "VN000E8VFST",
                        "link": "https://stockx.com/stockx-fallback-product",
                    }
                ]
            }

        def get_stockx_product(self, id_or_slug, *args, **kwargs):
            self.stockx_called = True
            self.stockx_request_arg = id_or_slug
            return {
                "product": {
                    "id": "stockx-fallback-456",
                    "slug": "stockx-fallback-product",
                    "statistics": {"last_90_days_average_price": 255},
                }
            }

        def get_stockx_sales_history(self, *args, **kwargs):
            return {"data": []}

        def get_goat_product(self, id_or_slug, **kwargs):
            self.goat_called = True
            return {}

    fake_client = FakeClient()

    with test_app.app_context():
        test_app.config["KICKS_API_KEY"] = "test-key"
        release = Release(
            name="Mixed Source SKU Fallback Release",
            brand="Vans",
            sku="VN000E8VFST",
            source="kicksdb_goat",
            source_product_id="1740767",
            source_slug="vans-old-skool-36-pearlized-pack-vintage-cocoa-brown-vn000e8vfst",
            release_date=date.today() + timedelta(days=1),
        )
        db.session.add(release)
        db.session.commit()
        db.session.add_all(
            [
                AffiliateOffer(
                    release_id=release.id,
                    retailer="stockx",
                    base_url="https://example.com/no-stockx-slug-yet",
                    offer_type="aftermarket",
                    is_active=True,
                ),
                AffiliateOffer(
                    release_id=release.id,
                    retailer="goat",
                    base_url="https://www.goat.com/sneakers/vans-old-skool-36-pearlized-pack-vintage-cocoa-brown-vn000e8vfst",
                    offer_type="aftermarket",
                    is_active=True,
                ),
            ]
        )
        db.session.commit()

        monkeypatch.setattr(main_routes, "KicksClient", lambda *args, **kwargs: fake_client)
        monkeypatch.setattr(main_routes, "_refresh_resale_for_release", REAL_REFRESH_RESALE_FOR_RELEASE)

        updated = main_routes._refresh_resale_for_release(release, force_refresh=True)
        db.session.commit()

        stockx_offer = AffiliateOffer.query.filter_by(release_id=release.id, retailer="stockx").first()

        assert updated is True
        assert fake_client.stockx_search_called is True
        assert fake_client.stockx_search_query == "VN000E8VFST"
        assert fake_client.stockx_called is True
        assert fake_client.stockx_request_arg == "stockx-fallback-456"
        assert release.source == "kicksdb_stockx"
        assert release.source_product_id == "stockx-fallback-456"
        assert release.source_slug == "stockx-fallback-product"
        assert stockx_offer.base_url == "https://stockx.com/stockx-fallback-product"
        assert stockx_offer.price == Decimal("255")
        assert stockx_offer.currency == "USD"


def test_extract_retail_price_info_supports_trait_fallback():
    price, currency = main_routes._extract_retail_price_info(
        {
            "traits": [
                {"name": "Retail Price", "value": "$160"},
            ]
        }
    )
    assert price == Decimal("160")
    assert currency == "USD"


def test_goat_refresh_persists_fallback_market_and_offer_and_retail_without_statistics(
    test_app, monkeypatch
):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_goat_product(self, id_or_slug, **kwargs):
            return {
                "data": {
                    "id": "1748509",
                    "slug": "nike-air-max-90-ultramarine-iu0767-001",
                    "weekly_orders": 79,
                    "min_price": 263,
                    "max_price": 450,
                    "avg_price": 309.5882352941176,
                    "retail_prices": {"usd": 160},
                    "variants": [
                        {"size": "9", "lowest_ask": 263},
                        {"size": "10", "prices": {"lowest_ask": 290}},
                    ],
                }
            }

    with test_app.app_context():
        test_app.config["KICKS_API_KEY"] = "test-key"
        release = Release(
            name="GOAT Fallback Mapping Release",
            brand="Nike",
            sku="IU0767-001",
            source="kicksdb_goat",
            source_product_id="1748509",
            source_slug="nike-air-max-90-ultramarine-iu0767-001",
            release_date=date.today() + timedelta(days=1),
            retail_currency="GBP",
        )
        db.session.add(release)
        db.session.commit()
        db.session.add(
            AffiliateOffer(
                release_id=release.id,
                retailer="goat",
                base_url="https://www.goat.com/sneakers/nike-air-max-90-ultramarine-iu0767-001",
                offer_type="aftermarket",
                is_active=True,
            )
        )
        db.session.commit()

        monkeypatch.setattr(main_routes, "_refresh_resale_for_release", REAL_REFRESH_RESALE_FOR_RELEASE)
        monkeypatch.setattr(main_routes, "KicksClient", lambda *args, **kwargs: FakeClient())

        updated = main_routes._refresh_resale_for_release(release, force_refresh=True)
        db.session.commit()

        stats = ReleaseMarketStats.query.filter_by(release_id=release.id).first()
        goat_offer = AffiliateOffer.query.filter_by(release_id=release.id, retailer="goat").first()

        assert updated is True
        assert release.retail_price == Decimal("160")
        assert release.retail_currency == "USD"
        assert stats is not None
        assert stats.average_price_3m == Decimal("309.59")
        assert stats.sales_price_range_low == Decimal("263")
        assert stats.sales_price_range_high == Decimal("450")
        assert stats.sales_volume == 79
        assert goat_offer.price == Decimal("263")
        assert goat_offer.currency == "USD"


def test_goat_refresh_maps_lean_live_payload_shapes_for_retail_offer_and_sales_volume(
    test_app, monkeypatch
):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_goat_product(self, id_or_slug, **kwargs):
            return {
                "data": {
                    "id": "1748509",
                    "slug": "nike-air-max-90-ultramarine-iu0767-001",
                    "weekly_orders": [
                        {"week": "2026-W10", "orders": {"count": "30"}},
                        {"week": "2026-W11", "orders": {"count": "49"}},
                    ],
                    "retail_prices": [
                        {"currency": "usd", "amount": {"value": "160.00"}},
                    ],
                    "variants": {
                        "data": [
                            {"size": "9", "prices": {"ask": {"amount": "263.00", "currency": "USD"}}},
                            {"size": "10", "prices": {"lowest_ask": {"value": "290.00"}}},
                        ]
                    },
                }
            }

    with test_app.app_context():
        test_app.config["KICKS_API_KEY"] = "test-key"
        release = Release(
            name="GOAT Lean Live Mapping Release",
            brand="Nike",
            sku="IU0767-001",
            source="kicksdb_goat",
            source_product_id="1748509",
            source_slug="nike-air-max-90-ultramarine-iu0767-001",
            release_date=date.today() + timedelta(days=1),
            retail_currency="GBP",
        )
        db.session.add(release)
        db.session.commit()
        db.session.add(
            AffiliateOffer(
                release_id=release.id,
                retailer="goat",
                base_url="https://www.goat.com/sneakers/nike-air-max-90-ultramarine-iu0767-001",
                offer_type="aftermarket",
                is_active=True,
            )
        )
        db.session.commit()

        monkeypatch.setattr(main_routes, "_refresh_resale_for_release", REAL_REFRESH_RESALE_FOR_RELEASE)
        monkeypatch.setattr(main_routes, "KicksClient", lambda *args, **kwargs: FakeClient())

        updated = main_routes._refresh_resale_for_release(release, force_refresh=True)
        db.session.commit()

        stats = ReleaseMarketStats.query.filter_by(release_id=release.id).first()
        goat_offer = AffiliateOffer.query.filter_by(release_id=release.id, retailer="goat").first()

        assert updated is True
        assert release.retail_price == Decimal("160")
        assert release.retail_currency == "USD"
        assert stats is not None
        assert stats.sales_volume == 79
        assert goat_offer.price == Decimal("263")
        assert goat_offer.currency == "USD"


def test_goat_refresh_handles_blank_offer_type_legacy_rows(
    test_app, monkeypatch
):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_goat_product(self, id_or_slug, **kwargs):
            return {
                "data": {
                    "id": "1748509",
                    "slug": "nike-air-max-90-ultramarine-iu0767-001",
                    "weekly_orders": 79,
                    "retail_prices": {"usd": 160},
                    "variants": [{"size": "9", "lowest_ask": 263}],
                }
            }

    with test_app.app_context():
        test_app.config["KICKS_API_KEY"] = "test-key"
        release = Release(
            name="GOAT Legacy OfferType Release",
            brand="Nike",
            sku="IU0767-001",
            source="kicksdb_goat",
            source_product_id="1748509",
            source_slug="nike-air-max-90-ultramarine-iu0767-001",
            release_date=date.today() + timedelta(days=1),
        )
        db.session.add(release)
        db.session.commit()
        db.session.add(
            AffiliateOffer(
                release_id=release.id,
                retailer="goat",
                base_url="https://www.goat.com/sneakers/nike-air-max-90-ultramarine-iu0767-001",
                offer_type="",
                is_active=True,
            )
        )
        db.session.commit()

        monkeypatch.setattr(main_routes, "_refresh_resale_for_release", REAL_REFRESH_RESALE_FOR_RELEASE)
        monkeypatch.setattr(main_routes, "KicksClient", lambda *args, **kwargs: FakeClient())

        updated = main_routes._refresh_resale_for_release(release, force_refresh=True)
        db.session.commit()

        stats = ReleaseMarketStats.query.filter_by(release_id=release.id).first()
        goat_offer = AffiliateOffer.query.filter_by(release_id=release.id, retailer="goat").first()

        assert updated is True
        assert goat_offer.offer_type == "aftermarket"
        assert goat_offer.price == Decimal("263")
        assert goat_offer.currency == "USD"
        assert release.retail_price == Decimal("160")
        assert stats is not None
        assert stats.sales_volume == 79


def test_goat_refresh_handles_retailer_with_case_and_whitespace(
    test_app, monkeypatch
):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_goat_product(self, id_or_slug, **kwargs):
            return {
                "data": {
                    "id": "1748509",
                    "slug": "nike-air-max-90-ultramarine-iu0767-001",
                    "weekly_orders": 12,
                    "retail_prices": {"usd": 150},
                    "variants": [{"size": "9", "lowest_ask": 240}],
                }
            }

    with test_app.app_context():
        test_app.config["KICKS_API_KEY"] = "test-key"
        release = Release(
            name="GOAT Retailer Normalization Release",
            brand="Nike",
            sku="IU0767-001",
            source="kicksdb_goat",
            source_product_id="1748509",
            source_slug="nike-air-max-90-ultramarine-iu0767-001",
            release_date=date.today() + timedelta(days=1),
        )
        db.session.add(release)
        db.session.commit()
        db.session.add(
            AffiliateOffer(
                release_id=release.id,
                retailer=" GOAT ",
                base_url="https://www.goat.com/sneakers/nike-air-max-90-ultramarine-iu0767-001",
                offer_type="aftermarket",
                is_active=True,
            )
        )
        db.session.commit()

        monkeypatch.setattr(main_routes, "_refresh_resale_for_release", REAL_REFRESH_RESALE_FOR_RELEASE)
        monkeypatch.setattr(main_routes, "KicksClient", lambda *args, **kwargs: FakeClient())

        updated = main_routes._refresh_resale_for_release(release, force_refresh=True)
        db.session.commit()

        stats = ReleaseMarketStats.query.filter_by(release_id=release.id).first()
        goat_offer = AffiliateOffer.query.filter_by(release_id=release.id, retailer=" GOAT ").first()

        assert updated is True
        assert release.retail_price == Decimal("150")
        assert goat_offer.price == Decimal("240")
        assert goat_offer.currency == "USD"
        assert stats is not None
        assert stats.sales_volume == 12


def test_release_detail_admin_diagnostics_explain_missing_goat_market_stats(
    test_client, test_app, auth, admin_user
):
    with test_app.app_context():
        release = Release(
            name="GOAT Missing Stats Release",
            brand="Nike",
            sku="GOAT-MISS-1",
            source="kicksdb_goat",
            source_product_id="goat-prod-1",
            source_slug="goat-prod-slug-1",
            release_date=date.today() + timedelta(days=1),
        )
        db.session.add(release)
        db.session.commit()
        product_key = build_product_key(release)
        product_slug = build_product_slug(release)

    auth.login(username=admin_user.username, password='password123')
    response = test_client.get(f"/products/{product_key}-{product_slug}")
    assert response.status_code == 200
    assert b"Market stats: not returned by GOAT product endpoint" in response.data


def test_release_detail_groups_offers(test_client, test_app):
    with test_app.app_context():
        release = Release(
            name="Grouped Release",
            release_date=date.today() + timedelta(days=1),
        )
        db.session.add(release)
        db.session.commit()

        offers = [
            AffiliateOffer(
                release_id=release.id,
                retailer="nike",
                base_url="https://example.com/nike",
                offer_type="retailer",
                is_active=True,
            ),
            AffiliateOffer(
                release_id=release.id,
                retailer="stockx",
                base_url="https://example.com/stockx",
                offer_type="aftermarket",
                is_active=True,
            ),
            AffiliateOffer(
                release_id=release.id,
                retailer="raffleco",
                base_url="https://example.com/raffle",
                offer_type="raffle",
                is_active=True,
            ),
        ]
        db.session.add_all(offers)
        db.session.commit()

        product_key = build_product_key(release)
        product_slug = build_product_slug(release)
        response = test_client.get(f"/products/{product_key}-{product_slug}")
        assert response.status_code == 200
        assert b"Retailers" in response.data
        assert b"Aftermarket" in response.data
        assert b"Raffles" in response.data
        assert b"/out/" in response.data


def test_release_detail_description_and_metrics_render_conditionally(test_client, test_app):
    with test_app.app_context():
        release = Release(
            name="Metrics Release",
            brand="Nike",
            sku="MET-123",
            release_date=date.today() + timedelta(days=1),
            retail_price=Decimal("200.00"),
            retail_currency="USD",
        )
        db.session.add(release)
        db.session.commit()

        offer = AffiliateOffer(
            release_id=release.id,
            retailer="stockx",
            base_url="https://example.com/stockx",
            offer_type="aftermarket",
            price=Decimal("220.00"),
            currency="USD",
            is_active=True,
        )
        db.session.add(offer)

        sale = ReleaseSalePoint(
            release_id=release.id,
            sale_at=datetime.utcnow(),
            price=Decimal("220.00"),
            currency="USD",
        )
        db.session.add(sale)
        stats = ReleaseMarketStats(
            release_id=release.id,
            currency="USD",
            average_price_3m=Decimal("299.00"),
            average_price_1y=Decimal("310.00"),
            volatility=0.35,
            sales_price_range_low=Decimal("165.00"),
            sales_price_range_high=Decimal("416.00"),
        )
        db.session.add(stats)
        db.session.commit()

        product_key = build_product_key(release)
        product_slug = build_product_slug(release)
        response = test_client.get(f"/products/{product_key}-{product_slug}")
        assert response.status_code == 200
        assert b"About this release" not in response.data
        assert b"Avg 3-Month Resale" in response.data
        assert b"$299.00" in response.data
        assert b"Price premium (1Y)" in response.data
        assert b"Sales volume (3M)" in response.data
        assert b"Volatility (1Y)" in response.data
        assert b"35.0%" in response.data
        assert b"Sales price range (1Y)" in response.data
        assert b"$165.00" in response.data
        assert b"$416.00" in response.data


def test_release_detail_layout_updates(test_client, test_app):
    with test_app.app_context():
        release = Release(
            name="Layout Release",
            brand="Nike",
            sku="LAY-123",
            release_date=date.today() + timedelta(days=1),
        )
        region = ReleaseRegion(
            release_id=1,
            region="UK",
            release_date=date.today() + timedelta(days=1),
        )
        db.session.add(release)
        db.session.commit()
        region.release_id = release.id
        db.session.add(region)
        db.session.commit()

        product_key = build_product_key(release)
        product_slug = build_product_slug(release)
        response = test_client.get(f"/products/{product_key}-{product_slug}")
        assert response.status_code == 200
        assert b"Pricing & Market" in response.data
        assert b"Showing UK release data" in response.data or b"Only UK release data currently available" in response.data


def test_release_detail_metrics_omit_when_missing(test_client, test_app):
    with test_app.app_context():
        release = Release(
            name="No Metrics Release",
            brand="Nike",
            sku="MET-456",
            release_date=date.today() + timedelta(days=1),
            retail_price=Decimal("200.00"),
            retail_currency="USD",
        )
        db.session.add(release)
        db.session.commit()

        product_key = build_product_key(release)
        product_slug = build_product_slug(release)
        response = test_client.get(f"/products/{product_key}-{product_slug}")
        assert response.status_code == 200
        assert b"Avg 1-Month Resale" not in response.data
        assert b"Avg 3-Month Resale" not in response.data
        assert b"Avg 1-Year Resale" not in response.data
        assert b"Price premium (1Y)" not in response.data
        assert b"Sales volume (3M)" not in response.data


def test_release_detail_premium_not_shown_when_currency_mismatch(test_client, test_app):
    with test_app.app_context():
        release = Release(
            name="Mismatch Release",
            brand="Nike",
            sku="MET-789",
            release_date=date.today() + timedelta(days=1),
            retail_price=Decimal("200.00"),
            retail_currency="GBP",
        )
        db.session.add(release)
        db.session.commit()

        sale = ReleaseSalePoint(
            release_id=release.id,
            sale_at=datetime.utcnow(),
            price=Decimal("220.00"),
            currency="USD",
        )
        db.session.add(sale)
        db.session.commit()

        product_key = build_product_key(release)
        product_slug = build_product_slug(release)
        response = test_client.get(f"/products/{product_key}-{product_slug}")
        assert response.status_code == 200
        assert b"Price premium (1Y)" not in response.data
