from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select

from extensions import db
from models import (
    AffiliateOffer,
    Release,
    ReleasePrice,
    ReleaseRegion,
    ReleaseSalePoint,
    ReleaseSalesMonthly,
    ReleaseSizeBid,
    wishlist_items,
)


def _create_release_with_children():
    release = Release(
        name="Delete Me",
        brand="Nike",
        release_date=date(2026, 1, 1),
        retail_price=Decimal("200.00"),
        retail_currency="USD",
    )
    db.session.add(release)
    db.session.commit()

    region = ReleaseRegion(
        release_id=release.id,
        region="US",
        release_date=date(2026, 1, 1),
    )
    price = ReleasePrice(
        release_id=release.id,
        region="US",
        currency="USD",
        price=Decimal("200.00"),
    )
    offer = AffiliateOffer(
        release_id=release.id,
        retailer="nike",
        base_url="https://example.com/nike",
        offer_type="retailer",
        region="US",
        is_active=True,
    )
    size_bid = ReleaseSizeBid(
        release_id=release.id,
        size_label="10",
        size_type="US",
        highest_bid=Decimal("250.00"),
        currency="USD",
    )
    sale_point = ReleaseSalePoint(
        release_id=release.id,
        sale_at=datetime.utcnow(),
        price=Decimal("240.00"),
        currency="USD",
    )
    sales_monthly = ReleaseSalesMonthly(
        release_id=release.id,
        month_start=date(2026, 1, 1),
        avg_price=Decimal("230.00"),
        currency="USD",
    )
    db.session.add_all([region, price, offer, size_bid, sale_point, sales_monthly])
    db.session.commit()
    return release


def test_delete_all_releases_admin_requires_confirmation(test_client, auth, admin_user, test_app):
    with test_app.app_context():
        _create_release_with_children()
        assert Release.query.count() == 1

    auth.login(username=admin_user.username, password="password123")
    response = test_client.post(
        "/admin/delete-all-releases",
        data={"confirmation": "NOPE"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with test_app.app_context():
        assert Release.query.count() == 1


def test_delete_all_releases_hides_from_calendar_without_touching_wishlist(
    test_client, auth, admin_user, test_app, init_database, monkeypatch
):
    with test_app.app_context():
        release = _create_release_with_children()
        release_id = release.id
        user, _ = init_database
        user = db.session.get(type(user), user.id)
        user.wishlist.append(release)
        db.session.commit()

        assert Release.query.count() == 1
        assert AffiliateOffer.query.count() == 1
        assert ReleasePrice.query.count() == 1
        assert ReleaseRegion.query.count() == 1
        assert ReleaseSizeBid.query.count() == 1
        assert ReleaseSalePoint.query.count() == 1
        assert ReleaseSalesMonthly.query.count() == 1

        wishlist_count = db.session.execute(
            select(func.count()).select_from(wishlist_items)
        ).scalar_one()
        assert wishlist_count == 1

    original_execute = db.session.execute

    def guarded_execute(statement, *args, **kwargs):
        if (
            getattr(statement, "table", None) is wishlist_items
            and getattr(statement, "whereclause", None) is None
        ):
            raise AssertionError("delete-all releases should not bulk-delete wishlist_items")
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db.session, "execute", guarded_execute)

    auth.login(username=admin_user.username, password="password123")
    response = test_client.post(
        "/admin/delete-all-releases",
        data={"confirmation": "DELETE ALL RELEASES"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with test_app.app_context():
        release = db.session.get(Release, release_id)
        assert release is not None
        assert release.is_calendar_visible is False
        assert AffiliateOffer.query.count() == 1
        assert ReleasePrice.query.count() == 1
        assert ReleaseRegion.query.count() == 1
        assert ReleaseSizeBid.query.count() == 1
        assert ReleaseSalePoint.query.count() == 1
        assert ReleaseSalesMonthly.query.count() == 1

        wishlist_count = db.session.execute(
            select(func.count()).select_from(wishlist_items)
        ).scalar_one()
        assert wishlist_count == 1


def test_delete_all_releases_non_admin_forbidden(test_client, auth, init_database):
    user, _ = init_database
    auth.login(username=user.username, password="password123")
    response = test_client.post(
        "/admin/delete-all-releases",
        data={"confirmation": "DELETE ALL RELEASES"},
    )
    assert response.status_code == 403


def test_delete_all_releases_get_not_allowed(test_client, auth, admin_user):
    auth.login(username=admin_user.username, password="password123")
    response = test_client.get("/admin/delete-all-releases")
    assert response.status_code == 405
