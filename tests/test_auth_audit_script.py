"""Tests for ``scripts/auth_audit_users.py``.

These tests exercise the audit's check functions and reporting layer in the
test app context. They never subprocess the script; they use the
``render_report`` helper directly.
"""

import json
import uuid

import pytest

from extensions import db
from models import User, UserApiToken
from scripts.auth_audit_users import (
    SEVERITY_BLOCKING,
    SEVERITY_WARNING,
    render_report,
    run_checks,
    summarise,
)


def _make_user(**kwargs) -> User:
    defaults = {
        "username": "audit_user",
        "email": "audit_user@example.com",
        "first_name": "Audit",
        "last_name": "User",
        "is_email_confirmed": True,
        "is_admin": False,
    }
    defaults.update(kwargs)
    user = User(**defaults)
    user.set_password("password123")
    db.session.add(user)
    return user


def _check_by_id(results, check_id):
    for result in results:
        if result["id"] == check_id:
            return result
    raise AssertionError(f"check {check_id} not present in audit output")


def test_audit_clean_database_exits_zero(test_app):
    """An empty user table is clean; only baseline info is returned."""
    with test_app.app_context():
        exit_code, rendered = render_report(db.session, output_format="text")
        assert exit_code == 0
        assert "Soletrak Auth Audit" in rendered
        assert "Exit code: 0" in rendered


def test_audit_baseline_counts_match_seeded_data(test_app):
    with test_app.app_context():
        _make_user(username="u1", email="one@example.com")
        _make_user(username="u2", email="two@example.com", is_admin=True)
        db.session.commit()

        results = run_checks(db.session)
        baseline = _check_by_id(results, "C1")
        assert baseline["details"]["users_total"] == 2
        assert baseline["details"]["users_admin"] == 1
        assert baseline["details"]["users_email_confirmed"] == 2


def test_audit_flags_email_case_collisions_as_blocking(test_app):
    with test_app.app_context():
        _make_user(username="ua", email="dup@Example.com")
        _make_user(username="ub", email="dup@example.com")
        db.session.commit()

        exit_code, _rendered = render_report(db.session, output_format="text")
        assert exit_code == 1

        results = run_checks(db.session)
        c2 = _check_by_id(results, "C2")
        assert c2["severity"] == SEVERITY_BLOCKING
        assert c2["count"] == 1
        assert c2["rows"][0]["normalised_email"] == "dup@example.com"


def test_audit_flags_unconfirmed_admin_as_blocking(test_app):
    with test_app.app_context():
        _make_user(
            username="rogue_admin",
            email="rogue@example.com",
            is_admin=True,
            is_email_confirmed=False,
        )
        db.session.commit()

        results = run_checks(db.session)
        c7 = _check_by_id(results, "C7")
        assert c7["severity"] == SEVERITY_BLOCKING
        assert c7["count"] == 1

        exit_code, _ = render_report(db.session, output_format="text")
        assert exit_code == 1


def test_audit_flags_pending_email_collision_as_blocking(test_app):
    with test_app.app_context():
        _make_user(username="claimer", email="taken@example.com")
        _make_user(
            username="claimant",
            email="claimant@example.com",
            pending_email="TAKEN@example.com",
        )
        db.session.commit()

        results = run_checks(db.session)
        c8 = _check_by_id(results, "C8")
        assert c8["severity"] == SEVERITY_WARNING
        assert c8["count"] == 1

        c9 = _check_by_id(results, "C9")
        assert c9["severity"] == SEVERITY_BLOCKING
        assert c9["count"] == 1


def test_audit_phase1_sentinel_blocks_when_supabase_link_set(test_app):
    """C10 must block if any row already has supabase_auth_user_id populated."""
    with test_app.app_context():
        u = _make_user(username="early_link", email="early@example.com")
        u.supabase_auth_user_id = uuid.uuid4()
        db.session.commit()

        results = run_checks(db.session)
        c10 = _check_by_id(results, "C10")
        assert c10["severity"] == SEVERITY_BLOCKING
        assert c10["count"] == 1


def test_audit_flags_orphan_api_tokens_as_blocking(test_app):
    from sqlalchemy import text

    with test_app.app_context():
        u = _make_user(username="token_owner", email="tok@example.com")
        db.session.commit()
        token = UserApiToken(
            user_id=u.id,
            token_hash="a" * 64,
            scopes="steps:write",
        )
        db.session.add(token)
        db.session.commit()
        token_id = token.id
        user_id = u.id
        # Drop the user via raw SQL so the ORM ``cascade='all, delete-orphan'``
        # configured on ``User.api_tokens`` does not also delete the token —
        # we are deliberately producing the orphan-token state the audit is
        # meant to detect.
        db.session.expunge_all()
        db.session.execute(text("DELETE FROM user WHERE id = :uid"), {"uid": user_id})
        db.session.commit()

        results = run_checks(db.session)
        c11 = _check_by_id(results, "C11")
        assert c11["severity"] == SEVERITY_BLOCKING
        assert c11["count"] == 1
        assert c11["rows"][0]["token_id"] == token_id


def test_audit_json_output_is_valid_json(test_app):
    with test_app.app_context():
        _make_user(username="json_user", email="json@example.com")
        db.session.commit()

        _exit_code, rendered = render_report(db.session, output_format="json")
        payload = json.loads(rendered)
        assert "checks" in payload
        assert "summary" in payload
        assert payload["summary"]["blocking"] == 0


def test_audit_summary_reflects_severity(test_app):
    with test_app.app_context():
        # Trigger one warning (pending email, no collision) and zero blocking.
        _make_user(
            username="pending_only",
            email="pending@example.com",
            pending_email="newpending@example.com",
        )
        db.session.commit()
        results = run_checks(db.session)
        summary = summarise(results)
        assert summary["blocking"] == 0
        assert summary["warning"] >= 1


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_audit_render_report_contract(test_app, output_format):
    """``render_report`` always returns ``(exit_code, str)``."""
    with test_app.app_context():
        exit_code, rendered = render_report(db.session, output_format=output_format)
        assert isinstance(exit_code, int)
        assert isinstance(rendered, str)
        assert rendered  # non-empty
