from datetime import date

from extensions import db
from models import ExposureEvent, Sneaker, SneakerExposureAttribution, SneakerWear, User
from services.exposure_service import recompute_exposure_attributions, upsert_daily_exposure


def _login(auth, user):
    return auth.login(username=user.username, password="password123")


def test_exposure_upsert_unique(test_app):
    with test_app.app_context():
        user = User(
            username="exposureuser",
            email="exposure@example.com",
            first_name="Exposure",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()

        day = date(2026, 1, 5)
        upsert_daily_exposure(
            user_id=user.id,
            date_local=day,
            timezone="Europe/London",
            got_wet=True,
            got_dirty=False,
            wet_severity=1,
            dirty_severity=None,
            note="Light rain",
        )
        db.session.commit()

        upsert_daily_exposure(
            user_id=user.id,
            date_local=day,
            timezone="Europe/London",
            got_wet=True,
            got_dirty=True,
            wet_severity=3,
            dirty_severity=2,
            note="Mud",
        )
        db.session.commit()

        rows = ExposureEvent.query.filter_by(user_id=user.id, date_local=day).all()
        assert len(rows) == 1
        assert rows[0].got_dirty is True
        assert rows[0].wet_severity == 3
        assert rows[0].dirty_severity == 2


def test_exposure_attribution_split(test_app):
    with test_app.app_context():
        user = User(
            username="exposureattrib",
            email="attrib@example.com",
            first_name="Attrib",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker_one = Sneaker(brand="Nike", model="One", sku="EX-1", owner=user)
        sneaker_two = Sneaker(brand="Nike", model="Two", sku="EX-2", owner=user)
        db.session.add_all([user, sneaker_one, sneaker_two])
        db.session.commit()

        day = date(2026, 1, 10)
        db.session.add_all(
            [
                SneakerWear(sneaker_id=sneaker_one.id, worn_at=day),
                SneakerWear(sneaker_id=sneaker_two.id, worn_at=day),
            ]
        )
        db.session.commit()

        upsert_daily_exposure(
            user_id=user.id,
            date_local=day,
            timezone="Europe/London",
            got_wet=True,
            got_dirty=True,
            wet_severity=2,
            dirty_severity=3,
            note=None,
        )
        db.session.commit()

        recompute_exposure_attributions(user.id, day, day)

        rows = (
            SneakerExposureAttribution.query.filter_by(user_id=user.id, date_local=day)
            .order_by(SneakerExposureAttribution.sneaker_id.asc())
            .all()
        )
        assert len(rows) == 2
        assert rows[0].wet_points == 1.0
        assert rows[1].wet_points == 1.0
        assert rows[0].dirty_points == 1.5
        assert rows[1].dirty_points == 1.5


def test_exposure_no_wear_no_attribution(test_app):
    with test_app.app_context():
        user = User(
            username="exposurenowear",
            email="nowear@example.com",
            first_name="No",
            last_name="Wear",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Three", sku="EX-3", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()

        day = date(2026, 2, 1)
        upsert_daily_exposure(
            user_id=user.id,
            date_local=day,
            timezone="Europe/London",
            got_wet=True,
            got_dirty=False,
            wet_severity=2,
            dirty_severity=None,
            note=None,
        )
        db.session.commit()

        recompute_exposure_attributions(user.id, day, day)

        rows = SneakerExposureAttribution.query.filter_by(user_id=user.id, date_local=day).all()
        assert rows == []


def test_exposure_upsert_route(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="exposureweb",
            email="web@example.com",
            first_name="Web",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Exposure", sku="EX-4", owner=user)
        db.session.add(user)
        db.session.add(sneaker)
        db.session.commit()
        username = user.username
        user_id = user.id
        sneaker_id = sneaker.id

    _login(auth, type("Obj", (), {"username": username}))
    response = test_client.post(
        f"/update-last-worn/{sneaker_id}",
        data={
            "new_last_worn": "2026-01-15",
            "exposure_update": "1",
            "got_wet": "on",
            "wet_severity": "2",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with test_app.app_context():
        event = ExposureEvent.query.filter_by(user_id=user_id, date_local=date(2026, 1, 15)).first()
        assert event is not None
        assert event.got_wet is True
