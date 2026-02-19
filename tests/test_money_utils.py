from datetime import date, timedelta, datetime
from decimal import Decimal

from extensions import db
from models import ExchangeRate, Release, User, ReleasePrice
from utils.money import convert_money, display_money, format_money


def test_preferred_currency_saved(test_app):
    with test_app.app_context():
        user = User(
            username="currency_user",
            email="currency@example.com",
            first_name="Currency",
            last_name="User",
            is_email_confirmed=True,
            preferred_currency="EUR",
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()

        saved_user = db.session.get(User, user.id)
        assert saved_user.preferred_currency == "EUR"


def test_format_money_outputs_symbol():
    assert format_money(Decimal("12.5"), "GBP") == "£12.50"
    assert format_money(Decimal("12.5"), "USD") == "$12.50"
    assert format_money(Decimal("12.5"), "EUR") == "€12.50"


def test_convert_money_no_rate_returns_none(test_app):
    with test_app.app_context():
        assert convert_money(db.session, Decimal("10.00"), "USD", "GBP") is None


def test_display_money_converts_when_rate_exists(test_app):
    with test_app.app_context():
        rate = ExchangeRate(
            base_currency="USD",
            quote_currency="GBP",
            rate=Decimal("0.80"),
            as_of=datetime.utcnow(),
        )
        db.session.add(rate)
        db.session.commit()

        display = display_money(db.session, Decimal("100.00"), "USD", "GBP")
        assert display["is_converted"] is True
        assert display["display"] == "£80.00"
        assert display["original"] == "$100.00"


def test_release_calendar_shows_regional_msrp_when_available(test_app, test_client):
    with test_app.app_context():
        release = Release(
            name="Currency Release",
            brand="Nike",
            release_date=date.today() + timedelta(days=1),
            retail_price=Decimal("100.00"),
            retail_currency="USD",
        )
        db.session.add(release)
        db.session.commit()

        response_no_regional = test_client.get("/release-calendar")
        assert b"MSRP:" not in response_no_regional.data

        regional = ReleasePrice(
            release_id=release.id,
            currency="GBP",
            price=Decimal("130.00"),
        )
        db.session.add(regional)
        db.session.commit()

        response_with_regional = test_client.get("/release-calendar")
        assert b"MSRP:" in response_with_regional.data
