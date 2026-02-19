from datetime import datetime, timedelta, time

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from app import create_app
from extensions import db
from models import User, Sneaker, SneakerWear, StepBucket, StepAttribution
from services.steps_attribution_service import ALGORITHM_V1, recompute_attribution
from services.steps_seed_service import seed_fake_steps, seed_fake_wear, verify_steps_attribution
from utils.slugs import build_my_sneaker_slug


def _my_sneaker_url(sneaker_id, slug):
    return f"/my/sneakers/{sneaker_id}-{slug}"


def _login(auth, username):
    return auth.login(username=username, password="password123")


def test_step_bucket_upsert(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="stepsuser",
            email="steps@example.com",
            first_name="Steps",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()
        username = user.username
        user_id = user.id

    _login(auth, username)

    payload = {
        "source": "apple_health",
        "timezone": "UTC",
        "granularity": "day",
        "buckets": [
            {
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-02T00:00:00Z",
                "steps": 100,
            }
        ],
    }
    response = test_client.post("/api/steps/buckets", json=payload)
    assert response.status_code == 200
    data = response.get_json()
    assert data["upserted_count"] == 1

    with test_app.app_context():
        bucket = StepBucket.query.filter_by(user_id=user_id, source="apple_health").first()
        assert bucket.steps == 100

    payload["buckets"][0]["steps"] = 150
    response = test_client.post("/api/steps/buckets", json=payload)
    assert response.status_code == 200
    data = response.get_json()
    assert data["updated_count"] == 1

    with test_app.app_context():
        bucket = StepBucket.query.filter_by(user_id=user_id, source="apple_health").first()
        assert bucket.steps == 150


def test_recompute_attribution_equal_split(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="attribuser",
            email="attrib@example.com",
            first_name="Attrib",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker_one = Sneaker(brand="Nike", model="One", sku="AA1111-001", owner=user)
        sneaker_two = Sneaker(brand="Nike", model="Two", sku="AA1111-002", owner=user)
        db.session.add_all([user, sneaker_one, sneaker_two])
        db.session.commit()
        username = user.username
        user_id = user.id

        wear_date = datetime(2026, 1, 5).date()
        db.session.add_all(
            [
                SneakerWear(sneaker_id=sneaker_one.id, worn_at=wear_date),
                SneakerWear(sneaker_id=sneaker_two.id, worn_at=wear_date),
            ]
        )
        db.session.add(
            StepBucket(
                user_id=user.id,
                source="apple_health",
                granularity="day",
                bucket_start=datetime(2026, 1, 5),
                bucket_end=datetime(2026, 1, 6),
                steps=5,
                timezone="UTC",
            )
        )
        db.session.commit()

    _login(auth, username)
    response = test_client.post(
        "/api/attribution/recompute",
        json={"granularity": "day", "start": "2026-01-05", "end": "2026-01-06"},
    )
    assert response.status_code == 200

    with test_app.app_context():
        rows = StepAttribution.query.filter_by(user_id=user_id).order_by(StepAttribution.sneaker_id.asc()).all()
        assert len(rows) == 2
        total = sum(int(row.steps_attributed) for row in rows)
        assert total == 5


def test_recompute_attribution_no_wear(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="nowearuser",
            email="nowear@example.com",
            first_name="NoWear",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Three", sku="AA1111-003", owner=user)
        db.session.add_all([user, sneaker])
        db.session.flush()
        username = user.username
        user_id = user.id
        db.session.add(
            StepBucket(
                user_id=user_id,
                source="apple_health",
                granularity="day",
                bucket_start=datetime(2026, 2, 1),
                bucket_end=datetime(2026, 2, 2),
                steps=10,
                timezone="UTC",
            )
        )
        db.session.commit()

    _login(auth, username)
    response = test_client.post(
        "/api/attribution/recompute",
        json={"granularity": "day", "start": "2026-02-01", "end": "2026-02-02"},
    )
    assert response.status_code == 200

    with test_app.app_context():
        rows = StepAttribution.query.filter_by(user_id=user_id).all()
        assert rows == []


def test_sneaker_detail_steps_summary(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="stepsview",
            email="stepsview@example.com",
            first_name="Steps",
            last_name="View",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Steps", sku="AA1111-004", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()
        username = user.username
        user_id = user.id
        sneaker_id = sneaker.id
        sneaker_slug = build_my_sneaker_slug(sneaker)

        db.session.add(
            StepAttribution(
                user_id=user_id,
                sneaker_id=sneaker_id,
                bucket_granularity="day",
                bucket_start=datetime.utcnow() - timedelta(days=5),
                steps_attributed=123,
                algorithm_version=ALGORITHM_V1,
            )
        )
        db.session.commit()

    _login(auth, username)
    response = test_client.get(_my_sneaker_url(sneaker_id, sneaker_slug))
    assert response.status_code == 200
    assert b"Estimated Steps" in response.data
    assert b"123" in response.data


def test_seed_fake_steps_and_verify(test_app):
    with test_app.app_context():
        user = User(
            username="seedsteps",
            email="seedsteps@example.com",
            first_name="Seed",
            last_name="Steps",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Seed", sku="AA1111-005", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()

        today = datetime.utcnow().date()
        yesterday = today - timedelta(days=1)
        db.session.add_all(
            [
                SneakerWear(sneaker_id=sneaker.id, worn_at=yesterday),
                SneakerWear(sneaker_id=sneaker.id, worn_at=today),
            ]
        )
        db.session.commit()

        stats = seed_fake_steps(
            user_id=user.id,
            days=2,
            steps_min=1000,
            steps_max=1000,
            source="apple_health",
            granularity="day",
            timezone_name="UTC",
            seed="test-seed",
        )
        assert stats["buckets_upserted"] == 2
        assert stats["attributions_written"] == 2

        verify = verify_steps_attribution(user_id=user.id, days=2)
        assert verify["total_bucket_steps"] == 2000
        assert verify["total_attributed_steps"] == 2000
        assert verify["missing_wear_days"] == []


def test_seed_fake_wear_creates_rows(test_app):
    with test_app.app_context():
        user = User(
            username="seedwear",
            email="seedwear@example.com",
            first_name="Seed",
            last_name="Wear",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Wear", sku="AA1111-006", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()

        stats = seed_fake_wear(
            user_id=user.id,
            days=3,
            sneaker_ids=[sneaker.id],
            timezone_name="UTC",
        )
        assert stats["wears_created"] == 3


def test_step_bucket_date_payload_timezone(test_client, auth, test_app):
    if ZoneInfo is None:
        return
    with test_app.app_context():
        user = User(
            username="tzuser",
            email="tzuser@example.com",
            first_name="TZ",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        user.timezone = "America/Los_Angeles"
        db.session.add(user)
        db.session.commit()
        username = user.username
        user_id = user.id

    _login(auth, username)

    payload = {
        "source": "apple_health",
        "timezone": "America/Los_Angeles",
        "granularity": "day",
        "buckets": [
            {"date": "2026-01-12", "steps": 8421}
        ],
    }
    response = test_client.post("/api/steps/buckets", json=payload)
    assert response.status_code == 200

    with test_app.app_context():
        bucket = StepBucket.query.filter_by(user_id=user_id).first()
        tz = ZoneInfo("America/Los_Angeles")
        local_start = datetime(2026, 1, 12, 0, 0, 0, tzinfo=tz)
        local_end = local_start + timedelta(days=1)
        expected_start = local_start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        expected_end = local_end.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        assert bucket.bucket_start == expected_start
        assert bucket.bucket_end == expected_end
        assert bucket.timezone == "America/Los_Angeles"


def test_step_bucket_falls_back_to_user_timezone(test_client, auth, test_app):
    if ZoneInfo is None:
        return
    with test_app.app_context():
        user = User(
            username="tzfallback",
            email="tzfallback@example.com",
            first_name="TZ",
            last_name="Fallback",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        user.timezone = "Asia/Tokyo"
        db.session.add(user)
        db.session.commit()
        username = user.username
        user_id = user.id

    _login(auth, username)

    payload = {
        "source": "apple_health",
        "granularity": "day",
        "buckets": [
            {"date": "2026-01-12", "steps": 5000}
        ],
    }
    response = test_client.post("/api/steps/buckets", json=payload)
    assert response.status_code == 200

    with test_app.app_context():
        bucket = StepBucket.query.filter_by(user_id=user_id).first()
        assert bucket.timezone == "Asia/Tokyo"


def test_recompute_attribution_respects_bucket_timezone(test_app):
    if ZoneInfo is None:
        return
    with test_app.app_context():
        user = User(
            username="traveluser",
            email="travel@example.com",
            first_name="Travel",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        user.timezone = "Europe/London"
        sneaker_one = Sneaker(brand="Nike", model="One", sku="TRAVEL-1", owner=user)
        sneaker_two = Sneaker(brand="Nike", model="Two", sku="TRAVEL-2", owner=user)
        db.session.add_all([user, sneaker_one, sneaker_two])
        db.session.commit()

        la_date = datetime(2026, 1, 10).date()
        tokyo_date = datetime(2026, 1, 11).date()
        db.session.add_all(
            [
                SneakerWear(sneaker_id=sneaker_one.id, worn_at=la_date),
                SneakerWear(sneaker_id=sneaker_two.id, worn_at=tokyo_date),
            ]
        )

        la_tz = ZoneInfo("America/Los_Angeles")
        tokyo_tz = ZoneInfo("Asia/Tokyo")
        la_start = datetime.combine(la_date, time.min).replace(tzinfo=la_tz)
        tokyo_start = datetime.combine(tokyo_date, time.min).replace(tzinfo=tokyo_tz)

        db.session.add_all(
            [
                StepBucket(
                    user_id=user.id,
                    source="apple_health",
                    granularity="day",
                    bucket_start=la_start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None),
                    bucket_end=(la_start + timedelta(days=1)).astimezone(ZoneInfo("UTC")).replace(tzinfo=None),
                    steps=10,
                    timezone="America/Los_Angeles",
                ),
                StepBucket(
                    user_id=user.id,
                    source="apple_health",
                    granularity="day",
                    bucket_start=tokyo_start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None),
                    bucket_end=(tokyo_start + timedelta(days=1)).astimezone(ZoneInfo("UTC")).replace(tzinfo=None),
                    steps=12,
                    timezone="Asia/Tokyo",
                ),
            ]
        )
        db.session.commit()

        stats = recompute_attribution(
            user_id=user.id,
            granularity="day",
            start=datetime(2026, 1, 10),
            end=datetime(2026, 1, 12),
            algorithm_version=ALGORITHM_V1,
        )
        assert stats["buckets_processed"] == 2
        rows = StepAttribution.query.filter_by(user_id=user.id).all()
        assert len(rows) == 2
        total = sum(int(row.steps_attributed) for row in rows)
        assert total == 22


def test_dst_bucket_local_date_mapping(test_app):
    if ZoneInfo is None:
        return
    with test_app.app_context():
        user = User(
            username="dstuser",
            email="dst@example.com",
            first_name="DST",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        user.timezone = "Europe/London"
        sneaker = Sneaker(brand="Nike", model="DST", sku="DST-1", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()

        dst_date = datetime(2026, 3, 29).date()
        db.session.add(SneakerWear(sneaker_id=sneaker.id, worn_at=dst_date))

        tz = ZoneInfo("Europe/London")
        local_start = datetime.combine(dst_date, time.min).replace(tzinfo=tz)
        db.session.add(
            StepBucket(
                user_id=user.id,
                source="apple_health",
                granularity="day",
                bucket_start=local_start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None),
                bucket_end=(local_start + timedelta(days=1)).astimezone(ZoneInfo("UTC")).replace(tzinfo=None),
                steps=7,
                timezone="Europe/London",
            )
        )
        db.session.commit()

        stats = recompute_attribution(
            user_id=user.id,
            granularity="day",
            start=datetime(2026, 3, 29),
            end=datetime(2026, 3, 30),
            algorithm_version=ALGORITHM_V1,
        )
        assert stats["attributions_written"] == 1
