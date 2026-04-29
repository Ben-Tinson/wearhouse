from datetime import date
from decimal import Decimal

from extensions import db
from models import AffiliateOffer, ExchangeRate, Release, ReleasePrice, ReleaseRegion, User
from services.release_display_service import resolve_release_display


def _create_user(username, preferred_region="UK", preferred_currency="GBP"):
    user = User(
        username=username,
        email=f"{username}@example.com",
        first_name="Test",
        last_name="User",
        is_email_confirmed=True,
        preferred_region=preferred_region,
        preferred_currency=preferred_currency,
    )
    user.set_password("password123")
    db.session.add(user)
    db.session.commit()
    return user


def test_release_date_prefers_user_region(test_app):
    with test_app.app_context():
        release = Release(
            name="Regional Release",
            brand="Nike",
            release_date=date(2026, 6, 1),
        )
        db.session.add(release)
        db.session.commit()

        uk_region = ReleaseRegion(
            release_id=release.id,
            region="UK",
            release_date=date(2026, 5, 1),
        )
        db.session.add(uk_region)
        db.session.commit()

        user = _create_user("ukuser", preferred_region="UK")
        display = resolve_release_display(release, db.session, user=user)

        assert display["release_date"] == date(2026, 5, 1)
        assert display["release_region"] == "UK"


def test_price_prefers_exact_region_and_currency(test_app):
    with test_app.app_context():
        release = Release(
            name="Price Release",
            brand="Nike",
            release_date=date(2026, 6, 1),
        )
        db.session.add(release)
        db.session.commit()

        price = ReleasePrice(
            release_id=release.id,
            region="UK",
            currency="GBP",
            price=Decimal("200.00"),
        )
        db.session.add(price)
        db.session.commit()

        user = _create_user("priceuser", preferred_region="UK", preferred_currency="GBP")
        display = resolve_release_display(release, db.session, user=user)

        assert display["price_currency"] == "GBP"
        assert display["price_source"] == "region_currency"
        assert display["price_display"]["display"] == "£200.00"


def test_price_keeps_native_currency_when_region_matches_but_currency_differs(test_app):
    with test_app.app_context():
        release = Release(
            name="Converted Release",
            brand="Nike",
            release_date=date(2026, 6, 1),
        )
        db.session.add(release)
        db.session.commit()

        price = ReleasePrice(
            release_id=release.id,
            region="UK",
            currency="EUR",
            price=Decimal("190.00"),
        )
        rate = ExchangeRate(base_currency="EUR", quote_currency="GBP", rate=Decimal("0.85"))
        db.session.add_all([price, rate])
        db.session.commit()

        user = _create_user("convertuser", preferred_region="UK", preferred_currency="GBP")
        display = resolve_release_display(release, db.session, user=user)

        assert display["price_currency"] == "EUR"
        assert display["price_region"] == "UK"
        assert display["price_display"]["is_converted"] is False
        assert display["price_display"]["display"] == "€190.00"


def test_market_context_only_shows_for_true_region_match(test_app):
    with test_app.app_context():
        release = Release(
            name="Base Price Region Message Release",
            brand="Nike",
            release_date=date(2026, 6, 1),
            retail_price=Decimal("250.00"),
            retail_currency="USD",
        )
        db.session.add(release)
        db.session.commit()

        uk_region = ReleaseRegion(
            release_id=release.id,
            region="UK",
            release_date=date(2026, 5, 1),
        )
        db.session.add(uk_region)
        db.session.commit()

        user = _create_user("ukmessage", preferred_region="UK", preferred_currency="GBP")
        display = resolve_release_display(release, db.session, user=user)

        assert display["release_region"] == "UK"
        assert display["price_region"] is None
        assert display["market_context_message"] is None


def test_market_context_shows_when_date_and_price_match_user_region(test_app):
    with test_app.app_context():
        release = Release(
            name="True UK Match Release",
            brand="Nike",
            release_date=date(2026, 6, 1),
        )
        db.session.add(release)
        db.session.commit()

        db.session.add(
            ReleaseRegion(
                release_id=release.id,
                region="UK",
                release_date=date(2026, 5, 1),
            )
        )
        db.session.add(
            ReleasePrice(
                release_id=release.id,
                region="UK",
                currency="GBP",
                price=Decimal("180.00"),
            )
        )
        db.session.commit()

        user = _create_user("uktrue", preferred_region="UK", preferred_currency="GBP")
        display = resolve_release_display(release, db.session, user=user)

        assert display["release_region"] == "UK"
        assert display["price_region"] == "UK"
        assert display["market_context_message"] is None


def test_offers_prefer_region_then_global_then_any(test_app):
    with test_app.app_context():
        release = Release(
            name="Offer Release",
            brand="Nike",
            release_date=date(2026, 6, 1),
        )
        db.session.add(release)
        db.session.commit()

        offers = [
            AffiliateOffer(
                release_id=release.id,
                retailer="nike",
                base_url="https://nike.example",
                offer_type="retailer",
                region="US",
                is_active=True,
            ),
            AffiliateOffer(
                release_id=release.id,
                retailer="global",
                base_url="https://global.example",
                offer_type="retailer",
                region=None,
                is_active=True,
            ),
        ]
        db.session.add_all(offers)
        db.session.commit()

        uk_user = _create_user("offeruk", preferred_region="UK")
        display_uk = resolve_release_display(release, db.session, user=uk_user)
        assert len(display_uk["offers"]) == 1
        assert display_uk["offers"][0].retailer == "global"

        us_user = _create_user("offerus", preferred_region="US")
        display_us = resolve_release_display(release, db.session, user=us_user)
        assert len(display_us["offers"]) == 1
        assert display_us["offers"][0].retailer == "nike"


def test_logged_out_uses_base_price_when_no_region_prices(test_app):
    with test_app.app_context():
        release = Release(
            name="Base Price Release",
            brand="Nike",
            release_date=date(2026, 6, 1),
            retail_price=Decimal("180.00"),
            retail_currency="GBP",
        )
        db.session.add(release)
        db.session.commit()

        display = resolve_release_display(release, db.session, user=None)
        assert display["price_source"] == "base"
        assert display["price_display"]["display"] == "£180.00"


def test_single_region_forces_native_currency_and_label(test_app):
    with test_app.app_context():
        release = Release(
            name="US Only Release",
            brand="Nike",
            release_date=date(2026, 6, 1),
        )
        db.session.add(release)
        db.session.commit()

        region = ReleaseRegion(
            release_id=release.id,
            region="US",
            release_date=date(2026, 6, 2),
        )
        price = ReleasePrice(
            release_id=release.id,
            region="US",
            currency="USD",
            price=Decimal("210.00"),
        )
        db.session.add_all([region, price])
        db.session.commit()

        user = _create_user("ukviewer", preferred_region="UK", preferred_currency="GBP")
        display = resolve_release_display(release, db.session, user=user)

        assert display["single_region_only"] is True
        assert display["canonical_region"] == "US"
        assert display["region_context_label"] == "Only US release data currently available"
        assert display["price_currency"] == "USD"
        assert display["price_display"]["is_converted"] is False
        assert display["price_display"]["display"] == "$210.00"


def test_multiple_regions_still_prefer_user_region(test_app):
    with test_app.app_context():
        release = Release(
            name="Multi Region Release",
            brand="Nike",
            release_date=date(2026, 6, 1),
        )
        db.session.add(release)
        db.session.commit()

        regions = [
            ReleaseRegion(
                release_id=release.id,
                region="US",
                release_date=date(2026, 6, 2),
            ),
            ReleaseRegion(
                release_id=release.id,
                region="UK",
                release_date=date(2026, 5, 1),
            ),
        ]
        db.session.add_all(regions)
        db.session.commit()

        user = _create_user("ukpref", preferred_region="UK", preferred_currency="GBP")
        display = resolve_release_display(release, db.session, user=user)

        assert display["single_region_only"] is False
        assert display["release_region"] == "UK"


def test_logged_out_single_region_uses_region_data(test_app):
    with test_app.app_context():
        release = Release(
            name="Single Region Release",
            brand="Nike",
            release_date=date(2026, 6, 1),
        )
        db.session.add(release)
        db.session.commit()

        region = ReleaseRegion(
            release_id=release.id,
            region="EU",
            release_date=date(2026, 7, 1),
        )
        db.session.add(region)
        db.session.commit()

        display = resolve_release_display(release, db.session, user=None)
        assert display["single_region_only"] is True
        assert display["canonical_region"] == "EU"
        assert display["region_context_label"] == "Only EU release data currently available"


def test_kicksdb_base_data_is_treated_as_us_specific(test_app):
    with test_app.app_context():
        release = Release(
            name="KicksDB Base Release",
            brand="Nike",
            source="kicksdb_stockx",
            release_date=date(2026, 2, 14),
            retail_price=Decimal("250.00"),
            retail_currency="USD",
        )
        db.session.add(release)
        db.session.commit()

        user = _create_user("ukkicksdb", preferred_region="UK", preferred_currency="GBP")
        display = resolve_release_display(release, db.session, user=user)

        assert display["single_region_only"] is True
        assert display["canonical_region"] == "US"
        assert display["release_region"] == "US"
        assert display["price_region"] == "US"
        assert display["price_currency"] == "USD"
        assert display["region_context_label"] == "Only US release data currently available"
        assert display["market_context_message"] == "Only US release data currently available"
