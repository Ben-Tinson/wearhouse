import hashlib
import re
from datetime import datetime

from models import User, UserApiToken, StepBucket
from extensions import db
from services.api_tokens import create_token_for_user


def _login(auth, user):
    return auth.login(username=user.username, password="password123")


def test_create_mobile_token_shows_plaintext_once(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="tokenuser",
            email="token@example.com",
            first_name="Token",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()
        username = user.username
        user_id = user.id

    _login(auth, type("Obj", (), {"username": username}))
    response = test_client.post(
        "/profile/tokens/create",
        data={"name": "My iPhone"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    match = re.search(r'id="mobile-token-value">([^<]+)<', response.data.decode("utf-8"))
    assert match, "Expected plaintext token to be displayed once."
    plaintext = match.group(1).strip()

    with test_app.app_context():
        token = UserApiToken.query.filter_by(user_id=user_id).first()
        assert token is not None
        assert token.token_hash == hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        assert plaintext not in token.token_hash

    response_again = test_client.get("/profile")
    assert plaintext.encode("utf-8") not in response_again.data


def test_revoke_mobile_token(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="tokenrevoke",
            email="tokenrevoke@example.com",
            first_name="Token",
            last_name="Revoke",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()
        token, _plaintext = create_token_for_user(user, name="Old Phone")
        username = user.username
        token_id = token.id

    _login(auth, type("Obj", (), {"username": username}))
    response = test_client.post(
        f"/profile/tokens/{token_id}/revoke",
        follow_redirects=True,
    )
    assert response.status_code == 200
    with test_app.app_context():
        revoked = db.session.get(UserApiToken, token.id)
        assert revoked.revoked_at is not None


def test_bearer_token_auth_for_steps(test_client, test_app):
    with test_app.app_context():
        user = User(
            username="tokensteps",
            email="tokensteps@example.com",
            first_name="Token",
            last_name="Steps",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()
        _token, plaintext = create_token_for_user(user, name="Steps App")
        user_id = user.id

    payload = {
        "source": "apple_health",
        "timezone": "Europe/London",
        "granularity": "day",
        "buckets": [{"date": "2026-01-12", "steps": 1000}],
    }
    response = test_client.post(
        "/api/steps/buckets",
        json=payload,
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert response.status_code == 200

    with test_app.app_context():
        bucket = StepBucket.query.filter_by(user_id=user_id).first()
        assert bucket is not None
        token_row = UserApiToken.query.filter_by(user_id=user_id).first()
        assert token_row.last_used_at is not None

    with test_app.app_context():
        token_row = UserApiToken.query.filter_by(user_id=user_id).first()
        token_row.revoked_at = datetime.utcnow()
        db.session.commit()

    response = test_client.post(
        "/api/steps/buckets",
        json=payload,
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert response.status_code == 401


def test_token_revoke_requires_owner(test_client, auth, test_app):
    with test_app.app_context():
        user_a = User(
            username="owner_a",
            email="owner_a@example.com",
            first_name="Owner",
            last_name="A",
            is_email_confirmed=True,
        )
        user_a.set_password("password123")
        user_b = User(
            username="owner_b",
            email="owner_b@example.com",
            first_name="Owner",
            last_name="B",
            is_email_confirmed=True,
        )
        user_b.set_password("password123")
        db.session.add_all([user_a, user_b])
        db.session.commit()
        token_b, _plaintext = create_token_for_user(user_b, name="B Phone")
        username_a = user_a.username
        token_b_id = token_b.id

    _login(auth, type("Obj", (), {"username": username_a}))
    response = test_client.post(f"/profile/tokens/{token_b_id}/revoke")
    assert response.status_code in (403, 404)
