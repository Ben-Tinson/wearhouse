"""Tests for ``services/supabase_auth_linkage.py``.

The linkage service is the **only** sanctioned writer of
``User.supabase_auth_user_id``. These tests pin the guards that protect
that invariant.
"""

from __future__ import annotations

import uuid

import pytest

from extensions import db
from models import User
from services.supabase_auth_linkage import (
    AppUserAlreadyLinked,
    AppUserNotFound,
    LinkageError,
    SupabaseIdentityAlreadyLinked,
    find_app_user_by_email,
    find_app_user_by_supabase_id,
    link_app_user_to_supabase,
    unlink_app_user,
)


def _make_user(**overrides) -> User:
    defaults = {
        "username": "linkage_user",
        "email": "linkage@example.com",
        "first_name": "Linkage",
        "last_name": "User",
        "is_email_confirmed": True,
    }
    defaults.update(overrides)
    user = User(**defaults)
    user.set_password("password123")
    db.session.add(user)
    db.session.commit()
    return user


# ---------------------------------------------------------------------------
# Read-only helpers
# ---------------------------------------------------------------------------


def test_find_app_user_by_supabase_id_returns_linked_row(test_app):
    with test_app.app_context():
        u = _make_user()
        target = uuid.uuid4()
        u.supabase_auth_user_id = target
        db.session.commit()

        found = find_app_user_by_supabase_id(target)
        assert found is not None
        assert found.id == u.id


def test_find_app_user_by_supabase_id_accepts_string_uuid(test_app):
    with test_app.app_context():
        u = _make_user()
        target = uuid.uuid4()
        u.supabase_auth_user_id = target
        db.session.commit()

        assert find_app_user_by_supabase_id(str(target)).id == u.id


def test_find_app_user_by_supabase_id_returns_none_when_unknown(test_app):
    with test_app.app_context():
        assert find_app_user_by_supabase_id(uuid.uuid4()) is None


def test_find_app_user_by_email_is_case_insensitive(test_app):
    with test_app.app_context():
        u = _make_user(username="case_match", email="MixedCase@Example.com")
        assert find_app_user_by_email("mixedcase@example.com").id == u.id
        assert find_app_user_by_email("MIXEDCASE@EXAMPLE.COM").id == u.id


def test_find_app_user_by_email_strips_whitespace(test_app):
    with test_app.app_context():
        u = _make_user(username="strip_match", email="strip@example.com")
        assert find_app_user_by_email("  strip@example.com  ").id == u.id


def test_find_app_user_by_email_handles_empty_inputs(test_app):
    with test_app.app_context():
        assert find_app_user_by_email(None) is None
        assert find_app_user_by_email("") is None
        assert find_app_user_by_email("   ") is None


def test_find_app_user_by_email_ignores_pending_email(test_app):
    """``pending_email`` is excluded from match per accepted decision."""
    with test_app.app_context():
        _make_user(
            username="pending_only",
            email="actual@example.com",
            pending_email="changing@example.com",
        )
        # The pending email should NOT match a search for "changing@…".
        assert find_app_user_by_email("changing@example.com") is None


# ---------------------------------------------------------------------------
# link_app_user_to_supabase
# ---------------------------------------------------------------------------


def test_link_links_unlinked_user(test_app):
    with test_app.app_context():
        u = _make_user()
        target = uuid.uuid4()

        result = link_app_user_to_supabase(u.id, target)
        assert result.id == u.id
        assert result.supabase_auth_user_id == target

        reloaded = db.session.get(User, u.id)
        assert reloaded.supabase_auth_user_id == target


def test_link_accepts_string_uuid(test_app):
    with test_app.app_context():
        u = _make_user()
        target = uuid.uuid4()
        link_app_user_to_supabase(u.id, str(target))
        assert db.session.get(User, u.id).supabase_auth_user_id == target


def test_link_idempotent_for_same_uuid(test_app):
    """Re-linking the same user to the same UUID must succeed (no churn)."""
    with test_app.app_context():
        u = _make_user()
        target = uuid.uuid4()
        link_app_user_to_supabase(u.id, target)
        # Second call with identical UUID is a no-op assignment, not an error.
        link_app_user_to_supabase(u.id, target)
        assert db.session.get(User, u.id).supabase_auth_user_id == target


def test_link_rejects_when_user_already_linked_to_different_uuid(test_app):
    with test_app.app_context():
        u = _make_user()
        link_app_user_to_supabase(u.id, uuid.uuid4())
        with pytest.raises(AppUserAlreadyLinked):
            link_app_user_to_supabase(u.id, uuid.uuid4())


def test_link_admin_override_allows_relinking(test_app):
    with test_app.app_context():
        u = _make_user()
        first = uuid.uuid4()
        link_app_user_to_supabase(u.id, first)
        second = uuid.uuid4()
        link_app_user_to_supabase(u.id, second, by_admin=True)
        assert db.session.get(User, u.id).supabase_auth_user_id == second


def test_link_rejects_when_supabase_id_already_used_by_other_user(test_app):
    with test_app.app_context():
        u1 = _make_user(username="u1", email="u1@example.com")
        u2 = _make_user(username="u2", email="u2@example.com")
        target = uuid.uuid4()
        link_app_user_to_supabase(u1.id, target)
        with pytest.raises(SupabaseIdentityAlreadyLinked):
            link_app_user_to_supabase(u2.id, target)


def test_link_admin_override_does_not_bypass_other_user_collision(test_app):
    """Admin override only relaxes the "this user already linked" guard;
    it must not let an admin attach a Supabase identity that's already
    bound to a different user."""
    with test_app.app_context():
        u1 = _make_user(username="u1", email="u1@example.com")
        u2 = _make_user(username="u2", email="u2@example.com")
        target = uuid.uuid4()
        link_app_user_to_supabase(u1.id, target)
        with pytest.raises(SupabaseIdentityAlreadyLinked):
            link_app_user_to_supabase(u2.id, target, by_admin=True)


def test_link_raises_when_user_does_not_exist(test_app):
    with test_app.app_context():
        with pytest.raises(AppUserNotFound):
            link_app_user_to_supabase(99999, uuid.uuid4())


# ---------------------------------------------------------------------------
# unlink_app_user
# ---------------------------------------------------------------------------


def test_unlink_clears_supabase_auth_user_id(test_app):
    with test_app.app_context():
        u = _make_user()
        link_app_user_to_supabase(u.id, uuid.uuid4())
        unlink_app_user(u.id)
        assert db.session.get(User, u.id).supabase_auth_user_id is None


def test_unlink_is_idempotent_on_unlinked_user(test_app):
    with test_app.app_context():
        u = _make_user()
        unlink_app_user(u.id)
        assert db.session.get(User, u.id).supabase_auth_user_id is None


def test_unlink_raises_when_user_not_found(test_app):
    with test_app.app_context():
        with pytest.raises(AppUserNotFound):
            unlink_app_user(99999)


def test_linkage_errors_share_base_class():
    assert issubclass(AppUserNotFound, LinkageError)
    assert issubclass(AppUserAlreadyLinked, LinkageError)
    assert issubclass(SupabaseIdentityAlreadyLinked, LinkageError)
