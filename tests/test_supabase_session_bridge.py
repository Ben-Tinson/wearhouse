"""Tests for ``services/supabase_session_bridge.py`` — Phase 3a PR 2.

Pins the bridge contract:

    - happy-path new user creation,
    - returning-user fast path (already linked),
    - email-match against an unlinked legacy user is refused (no silent
      linkage),
    - token verification failures and flag-disabled defence,
    - profile validation (missing fields, bad region, taken username),
    - atomic rollback when linkage fails after the User row was added,
    - audit logging shape and append behaviour,
    - login-callback invocation only on success and only after commit.

The bridge is not yet wired to any HTTP route — these tests exercise
the service directly. The Supabase JWT verifier is monkey-patched so
the tests do not need real ES256 / JWKS infrastructure.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, Optional

import pytest

from extensions import db
from models import User
from services.supabase_auth_service import (
    SupabaseAuthDisabled,
    SupabaseAuthMisconfigured,
    SupabaseClaims,
    SupabaseTokenInvalid,
)
from services.supabase_session_bridge import (
    BRIDGE_SOURCE_NEW_USER,
    BRIDGE_SOURCE_RETURNING,
    BridgeFlagDisabled,
    BridgeOutcome,
    BridgeProfileInvalid,
    BridgeTokenInvalid,
    ExistingLegacyUserConflict,
    ExistingSupabaseLinkConflict,
    bridge_supabase_identity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enable_supabase(app):
    app.config["SUPABASE_AUTH_ENABLED"] = True


def _disable_supabase(app):
    app.config["SUPABASE_AUTH_ENABLED"] = False


def _make_claims(
    *,
    sub: Optional[str] = None,
    email: str = "newuser@example.com",
    provider: str = "email",
    extra_raw: Optional[Dict[str, Any]] = None,
) -> SupabaseClaims:
    raw = {"app_metadata": {"provider": provider}}
    if extra_raw:
        raw.update(extra_raw)
    return SupabaseClaims(
        supabase_user_id=str(sub or uuid.uuid4()),
        email=email,
        raw=raw,
    )


def _patch_verifier(monkeypatch, *, claims=None, raise_with=None):
    """Replace the bridge's verifier with a fixture.

    The bridge imports ``verify_access_token`` from
    ``services.supabase_auth_service`` at module load. Patching that
    binding inside the bridge module replaces it for the call.
    """

    def _fake_verify(token: str):
        if raise_with is not None:
            raise raise_with
        return claims

    monkeypatch.setattr(
        "services.supabase_session_bridge.verify_access_token",
        _fake_verify,
    )


def _valid_profile(**overrides) -> Dict[str, Any]:
    payload = {
        "username": "fresh_user",
        "first_name": "Fresh",
        "last_name": "User",
        "preferred_region": "UK",
        "marketing_opt_in": False,
    }
    payload.update(overrides)
    return payload


def _make_legacy_user(**overrides) -> User:
    defaults = {
        "username": "legacy_user",
        "email": "legacy@example.com",
        "first_name": "Legacy",
        "last_name": "User",
        "is_email_confirmed": True,
        "is_admin": False,
    }
    defaults.update(overrides)
    user = User(**defaults)
    user.set_password("password123")
    db.session.add(user)
    db.session.commit()
    return user


# ---------------------------------------------------------------------------
# Defence-in-depth: flag and verifier
# ---------------------------------------------------------------------------


def test_bridge_raises_when_flag_disabled(test_app):
    with test_app.app_context():
        _disable_supabase(test_app)
        with pytest.raises(BridgeFlagDisabled):
            bridge_supabase_identity("any.jwt.value", _valid_profile(), no_audit=True)


def test_bridge_wraps_supabase_token_invalid(test_app, monkeypatch):
    with test_app.app_context():
        _enable_supabase(test_app)
        _patch_verifier(monkeypatch, raise_with=SupabaseTokenInvalid("bad signature"))
        with pytest.raises(BridgeTokenInvalid) as excinfo:
            bridge_supabase_identity("any", _valid_profile(), no_audit=True)
        assert "bad signature" in str(excinfo.value)


def test_bridge_wraps_supabase_misconfigured(test_app, monkeypatch):
    """Verifier-level config errors surface as ``BridgeTokenInvalid``."""
    with test_app.app_context():
        _enable_supabase(test_app)
        _patch_verifier(
            monkeypatch,
            raise_with=SupabaseAuthMisconfigured("SUPABASE_JWT_SECRET missing"),
        )
        with pytest.raises(BridgeTokenInvalid):
            bridge_supabase_identity("any", _valid_profile(), no_audit=True)


def test_bridge_wraps_supabase_disabled_into_token_invalid(test_app, monkeypatch):
    """If the verifier itself raises ``SupabaseAuthDisabled`` (race with
    flag flip), the bridge surfaces it as ``BridgeTokenInvalid`` — its
    own ``BridgeFlagDisabled`` is reserved for the upfront flag check."""
    with test_app.app_context():
        _enable_supabase(test_app)
        _patch_verifier(monkeypatch, raise_with=SupabaseAuthDisabled("disabled"))
        with pytest.raises(BridgeTokenInvalid):
            bridge_supabase_identity("any", _valid_profile(), no_audit=True)


# ---------------------------------------------------------------------------
# Happy path: new user creation
# ---------------------------------------------------------------------------


def test_bridge_creates_new_user_on_first_signup(test_app, monkeypatch):
    target_uuid = uuid.uuid4()
    claims = _make_claims(sub=str(target_uuid), email="newuser@example.com")
    _patch_verifier(monkeypatch, claims=claims)

    captured = {}

    def _record_login(user):
        captured["user_id"] = user.id

    with test_app.app_context():
        _enable_supabase(test_app)
        outcome = bridge_supabase_identity(
            "any.jwt.value",
            _valid_profile(username="new_signup", preferred_region="EU", marketing_opt_in=True),
            login_callback=_record_login,
            no_audit=True,
        )

        assert isinstance(outcome, BridgeOutcome)
        assert outcome.was_created is True
        assert outcome.source == BRIDGE_SOURCE_NEW_USER
        assert outcome.app_user.username == "new_signup"
        assert outcome.app_user.email == "newuser@example.com"
        assert outcome.app_user.preferred_region == "EU"
        assert outcome.app_user.marketing_opt_in is True
        assert outcome.app_user.is_email_confirmed is True
        assert outcome.app_user.is_admin is False
        assert outcome.app_user.password_hash is None
        assert outcome.app_user.auth_provider == "supabase_email"
        assert outcome.app_user.last_login_at is not None
        assert outcome.app_user.supabase_auth_user_id == target_uuid
        assert captured.get("user_id") == outcome.app_user.id


def test_bridge_lowercases_email_from_jwt(test_app, monkeypatch):
    claims = _make_claims(email="MixedCase@Example.com")
    _patch_verifier(monkeypatch, claims=claims)

    with test_app.app_context():
        _enable_supabase(test_app)
        outcome = bridge_supabase_identity(
            "tok",
            _valid_profile(username="case_user"),
            no_audit=True,
        )
        assert outcome.app_user.email == "mixedcase@example.com"


def test_bridge_detects_oauth_provider_from_app_metadata(test_app, monkeypatch):
    claims = _make_claims(provider="google")
    _patch_verifier(monkeypatch, claims=claims)

    with test_app.app_context():
        _enable_supabase(test_app)
        outcome = bridge_supabase_identity(
            "tok",
            _valid_profile(username="g_user"),
            no_audit=True,
        )
        assert outcome.app_user.auth_provider == "supabase_oauth_google"


def test_bridge_defaults_provider_to_email_when_metadata_missing(test_app, monkeypatch):
    claims = SupabaseClaims(
        supabase_user_id=str(uuid.uuid4()),
        email="meta_missing@example.com",
        raw={},
    )
    _patch_verifier(monkeypatch, claims=claims)

    with test_app.app_context():
        _enable_supabase(test_app)
        outcome = bridge_supabase_identity(
            "tok",
            _valid_profile(username="meta_missing"),
            no_audit=True,
        )
        assert outcome.app_user.auth_provider == "supabase_email"


# ---------------------------------------------------------------------------
# Returning user (already linked)
# ---------------------------------------------------------------------------


def test_bridge_returns_linked_user_without_recreating(test_app, monkeypatch):
    from services.supabase_auth_linkage import link_app_user_to_supabase

    target_uuid = uuid.uuid4()
    claims = _make_claims(sub=str(target_uuid), email="returning@example.com")
    _patch_verifier(monkeypatch, claims=claims)

    captured_callbacks = []

    with test_app.app_context():
        _enable_supabase(test_app)
        existing = _make_legacy_user(username="returner", email="returning@example.com")
        link_app_user_to_supabase(existing.id, target_uuid)
        existing_id = existing.id
        before_count = User.query.count()

        outcome = bridge_supabase_identity(
            "tok",
            None,  # profile payload not needed for returning user
            login_callback=lambda u: captured_callbacks.append(u.id),
            no_audit=True,
        )

        assert outcome.was_created is False
        assert outcome.source == BRIDGE_SOURCE_RETURNING
        assert outcome.app_user.id == existing_id
        assert User.query.count() == before_count
        assert captured_callbacks == [existing_id]


def test_bridge_updates_last_login_at_for_returning_user(test_app, monkeypatch):
    from datetime import datetime, timedelta
    from services.supabase_auth_linkage import link_app_user_to_supabase

    target_uuid = uuid.uuid4()
    claims = _make_claims(sub=str(target_uuid), email="ll@example.com")
    _patch_verifier(monkeypatch, claims=claims)

    with test_app.app_context():
        _enable_supabase(test_app)
        existing = _make_legacy_user(username="ll_user", email="ll@example.com")
        # Give the row a stale last_login_at so we can detect the update.
        stale = datetime.utcnow() - timedelta(days=30)
        existing.last_login_at = stale
        db.session.commit()
        link_app_user_to_supabase(existing.id, target_uuid)
        existing_id = existing.id

        bridge_supabase_identity("tok", None, no_audit=True)

        reloaded = db.session.get(User, existing_id)
        assert reloaded.last_login_at > stale


# ---------------------------------------------------------------------------
# Email-match conflict (no silent linking)
# ---------------------------------------------------------------------------


def test_bridge_refuses_silent_linking_on_email_match(test_app, monkeypatch):
    """Existing legacy user with matching email must NOT be silently
    linked; the bridge raises ``ExistingLegacyUserConflict`` so the
    caller can present a consent prompt (Phase 3b)."""
    new_supabase_uuid = uuid.uuid4()
    claims = _make_claims(
        sub=str(new_supabase_uuid),
        email="conflict@example.com",
    )
    _patch_verifier(monkeypatch, claims=claims)

    with test_app.app_context():
        _enable_supabase(test_app)
        existing = _make_legacy_user(
            username="conflict_user",
            email="conflict@example.com",
        )
        existing_id = existing.id

        with pytest.raises(ExistingLegacyUserConflict) as excinfo:
            bridge_supabase_identity(
                "tok",
                _valid_profile(username="otheruser"),
                no_audit=True,
            )
        assert excinfo.value.app_user_id == existing_id

        # Crucial: the bridge must NOT have written supabase_auth_user_id.
        reloaded = db.session.get(User, existing_id)
        assert reloaded.supabase_auth_user_id is None
        # And no new user row was created.
        assert User.query.filter_by(username="otheruser").first() is None


def test_bridge_email_match_is_case_insensitive(test_app, monkeypatch):
    claims = _make_claims(email="MIXED@example.com")
    _patch_verifier(monkeypatch, claims=claims)

    with test_app.app_context():
        _enable_supabase(test_app)
        _make_legacy_user(username="mixed", email="mixed@example.com")
        with pytest.raises(ExistingLegacyUserConflict):
            bridge_supabase_identity("tok", _valid_profile(username="x"), no_audit=True)


# ---------------------------------------------------------------------------
# Existing-link mismatch: app row already linked to a DIFFERENT Supabase
# identity than the incoming JWT's sub. Before the fix, the bridge fell
# through to a duplicate INSERT and 500'd on the unique email constraint.
# ---------------------------------------------------------------------------


def test_bridge_raises_supabase_link_mismatch_when_app_row_linked_to_different_sub(
    test_app, monkeypatch
):
    """The specific manual-testing scenario: an app user already linked
    to ``UUID_A``, an incoming JWT for the same email with a different
    ``UUID_B``. The bridge must raise the typed conflict and not attempt
    a duplicate insert."""
    from services.supabase_auth_linkage import link_app_user_to_supabase

    original_uuid = uuid.uuid4()
    incoming_uuid = uuid.uuid4()
    claims = _make_claims(sub=str(incoming_uuid), email="alice@example.com")
    _patch_verifier(monkeypatch, claims=claims)

    with test_app.app_context():
        _enable_supabase(test_app)
        original = _make_legacy_user(
            username="alice", email="alice@example.com"
        )
        link_app_user_to_supabase(original.id, original_uuid)
        original_id = original.id
        before_count = User.query.count()

        with pytest.raises(ExistingSupabaseLinkConflict) as excinfo:
            bridge_supabase_identity(
                "tok",
                _valid_profile(username="alice_alt"),
                no_audit=True,
            )
        assert excinfo.value.app_user_id == original_id

        # No row mutated, no duplicate row created.
        reloaded = db.session.get(User, original_id)
        assert reloaded.supabase_auth_user_id == original_uuid
        assert User.query.count() == before_count
        assert User.query.filter_by(username="alice_alt").first() is None


def test_bridge_mismatch_does_not_silently_relink(test_app, monkeypatch):
    """The matched app row's existing linkage must NOT be overwritten."""
    from services.supabase_auth_linkage import link_app_user_to_supabase

    original_uuid = uuid.uuid4()
    incoming_uuid = uuid.uuid4()
    claims = _make_claims(sub=str(incoming_uuid), email="norelink@example.com")
    _patch_verifier(monkeypatch, claims=claims)

    with test_app.app_context():
        _enable_supabase(test_app)
        user = _make_legacy_user(username="norelink", email="norelink@example.com")
        link_app_user_to_supabase(user.id, original_uuid)
        user_id = user.id

        with pytest.raises(ExistingSupabaseLinkConflict):
            bridge_supabase_identity("tok", _valid_profile(username="nlk"), no_audit=True)

        reloaded = db.session.get(User, user_id)
        assert reloaded.supabase_auth_user_id == original_uuid  # unchanged
        assert reloaded.supabase_auth_user_id != incoming_uuid


def test_bridge_mismatch_check_is_case_insensitive_on_email(test_app, monkeypatch):
    from services.supabase_auth_linkage import link_app_user_to_supabase

    original_uuid = uuid.uuid4()
    claims = _make_claims(sub=str(uuid.uuid4()), email="MISMatch@Example.com")
    _patch_verifier(monkeypatch, claims=claims)

    with test_app.app_context():
        _enable_supabase(test_app)
        user = _make_legacy_user(username="mm_case", email="mismatch@example.com")
        link_app_user_to_supabase(user.id, original_uuid)
        with pytest.raises(ExistingSupabaseLinkConflict):
            bridge_supabase_identity("tok", _valid_profile(username="mmx"), no_audit=True)


def test_bridge_mismatch_does_not_write_audit(test_app, monkeypatch, tmp_path):
    from services.supabase_auth_linkage import link_app_user_to_supabase

    claims = _make_claims(
        sub=str(uuid.uuid4()), email="audit_mismatch@example.com"
    )
    _patch_verifier(monkeypatch, claims=claims)
    audit_file = str(tmp_path / "audit.jsonl")

    with test_app.app_context():
        _enable_supabase(test_app)
        user = _make_legacy_user(
            username="audit_mm", email="audit_mismatch@example.com"
        )
        link_app_user_to_supabase(user.id, uuid.uuid4())
        with pytest.raises(ExistingSupabaseLinkConflict):
            bridge_supabase_identity(
                "tok",
                _valid_profile(username="ammx"),
                audit_path=audit_file,
            )
        assert not os.path.exists(audit_file)


def test_bridge_existing_legacy_conflict_still_fires_for_unlinked_user(
    test_app, monkeypatch
):
    """Sanity: the pre-existing unlinked-legacy path is unchanged. The
    new mismatch error must NOT subsume the legacy-conflict error."""
    claims = _make_claims(email="stillegacy@example.com")
    _patch_verifier(monkeypatch, claims=claims)

    with test_app.app_context():
        _enable_supabase(test_app)
        legacy = _make_legacy_user(
            username="still_legacy", email="stillegacy@example.com"
        )
        legacy_id = legacy.id
        with pytest.raises(ExistingLegacyUserConflict) as excinfo:
            bridge_supabase_identity(
                "tok", _valid_profile(username="newer"), no_audit=True
            )
        assert excinfo.value.app_user_id == legacy_id


# ---------------------------------------------------------------------------
# Profile validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing_field",
    ["username", "first_name", "last_name", "preferred_region"],
)
def test_bridge_rejects_missing_required_profile_field(
    test_app, monkeypatch, missing_field
):
    claims = _make_claims()
    _patch_verifier(monkeypatch, claims=claims)

    with test_app.app_context():
        _enable_supabase(test_app)
        payload = _valid_profile()
        payload.pop(missing_field)
        with pytest.raises(BridgeProfileInvalid) as excinfo:
            bridge_supabase_identity("tok", payload, no_audit=True)
        assert missing_field in excinfo.value.field_errors


def test_bridge_rejects_invalid_region(test_app, monkeypatch):
    claims = _make_claims()
    _patch_verifier(monkeypatch, claims=claims)

    with test_app.app_context():
        _enable_supabase(test_app)
        with pytest.raises(BridgeProfileInvalid) as excinfo:
            bridge_supabase_identity(
                "tok",
                _valid_profile(preferred_region="ZZ"),
                no_audit=True,
            )
        assert "preferred_region" in excinfo.value.field_errors


def test_bridge_rejects_username_too_short(test_app, monkeypatch):
    claims = _make_claims()
    _patch_verifier(monkeypatch, claims=claims)

    with test_app.app_context():
        _enable_supabase(test_app)
        with pytest.raises(BridgeProfileInvalid) as excinfo:
            bridge_supabase_identity(
                "tok",
                _valid_profile(username="abc"),
                no_audit=True,
            )
        assert "username" in excinfo.value.field_errors


def test_bridge_rejects_taken_username(test_app, monkeypatch):
    claims = _make_claims(email="newuser@example.com")
    _patch_verifier(monkeypatch, claims=claims)

    with test_app.app_context():
        _enable_supabase(test_app)
        _make_legacy_user(username="taken_name", email="legacy_taken@example.com")
        with pytest.raises(BridgeProfileInvalid) as excinfo:
            bridge_supabase_identity(
                "tok",
                _valid_profile(username="taken_name"),
                no_audit=True,
            )
        assert "username" in excinfo.value.field_errors


def test_bridge_rejects_jwt_without_email_claim(test_app, monkeypatch):
    """A JWT without an email claim cannot create a new app user."""
    claims = SupabaseClaims(
        supabase_user_id=str(uuid.uuid4()),
        email=None,
        raw={"app_metadata": {"provider": "email"}},
    )
    _patch_verifier(monkeypatch, claims=claims)

    with test_app.app_context():
        _enable_supabase(test_app)
        with pytest.raises(BridgeProfileInvalid) as excinfo:
            bridge_supabase_identity("tok", _valid_profile(), no_audit=True)
        assert "email" in excinfo.value.field_errors


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------


def test_bridge_rolls_back_when_linkage_fails(test_app, monkeypatch):
    """If linkage fails after the User row has been added, the partial
    row must not survive.

    Simulating a real concurrent-insert collision deterministically is
    awkward, so we monkey-patch ``link_app_user_to_supabase`` (within
    the bridge module's namespace) to raise. This still exercises the
    bridge's atomicity guarantee — the roll-back happens regardless of
    why the link call failed.
    """
    claims = _make_claims(email="rollback@example.com")
    _patch_verifier(monkeypatch, claims=claims)

    def _boom_link(*_args, **_kwargs):
        raise RuntimeError("simulated linkage failure")

    monkeypatch.setattr(
        "services.supabase_session_bridge.link_app_user_to_supabase",
        _boom_link,
    )

    with test_app.app_context():
        _enable_supabase(test_app)
        before_count = User.query.count()

        with pytest.raises(RuntimeError):
            bridge_supabase_identity(
                "tok",
                _valid_profile(username="rollback_user"),
                no_audit=True,
            )

        # Crucial: no half-created User row survives the failed link.
        assert User.query.count() == before_count
        assert User.query.filter_by(username="rollback_user").first() is None


def test_bridge_does_not_call_login_callback_on_failure(test_app, monkeypatch):
    """``login_callback`` must run only after a successful commit."""
    claims = _make_claims(email="nocb@example.com")
    _patch_verifier(monkeypatch, claims=claims)

    def _boom_link(*_args, **_kwargs):
        raise RuntimeError("simulated linkage failure")

    monkeypatch.setattr(
        "services.supabase_session_bridge.link_app_user_to_supabase",
        _boom_link,
    )

    callbacks = []

    with test_app.app_context():
        _enable_supabase(test_app)
        with pytest.raises(RuntimeError):
            bridge_supabase_identity(
                "tok",
                _valid_profile(username="cb_should_not_login"),
                login_callback=lambda u: callbacks.append(u.id),
                no_audit=True,
            )

        assert callbacks == []


# ---------------------------------------------------------------------------
# Login callback
# ---------------------------------------------------------------------------


def test_bridge_calls_login_callback_with_resolved_user(test_app, monkeypatch):
    claims = _make_claims(email="cb@example.com")
    _patch_verifier(monkeypatch, claims=claims)

    seen = []

    with test_app.app_context():
        _enable_supabase(test_app)
        outcome = bridge_supabase_identity(
            "tok",
            _valid_profile(username="cb_user"),
            login_callback=lambda u: seen.append(u),
            no_audit=True,
        )
        assert seen == [outcome.app_user]


def test_bridge_succeeds_with_no_login_callback(test_app, monkeypatch):
    """Bridge must work without a callback; it just skips session establishment."""
    claims = _make_claims(email="nocb@example.com")
    _patch_verifier(monkeypatch, claims=claims)

    with test_app.app_context():
        _enable_supabase(test_app)
        outcome = bridge_supabase_identity(
            "tok",
            _valid_profile(username="no_cb"),
            login_callback=None,
            no_audit=True,
        )
        assert outcome.was_created is True


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


def test_bridge_writes_audit_row_for_new_user(test_app, monkeypatch, tmp_path):
    target_uuid = uuid.uuid4()
    claims = _make_claims(sub=str(target_uuid), email="audit_new@example.com")
    _patch_verifier(monkeypatch, claims=claims)

    audit_file = str(tmp_path / "audit.jsonl")

    with test_app.app_context():
        _enable_supabase(test_app)
        outcome = bridge_supabase_identity(
            "tok",
            _valid_profile(username="audit_new"),
            audit_path=audit_file,
        )

        assert os.path.exists(audit_file)
        with open(audit_file, "r", encoding="utf-8") as fh:
            entries = [json.loads(line) for line in fh if line.strip()]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["action"] == "link"
        assert entry["app_user_id"] == outcome.app_user.id
        assert entry["email"] == "audit_new@example.com"
        assert entry["supabase_uuid"] == str(target_uuid)
        assert entry["auth_provider"] == "supabase_email"
        assert entry["source"] == "bridge"
        assert entry["is_admin"] is False


def test_bridge_writes_audit_row_for_returning_user(test_app, monkeypatch, tmp_path):
    from services.supabase_auth_linkage import link_app_user_to_supabase

    target_uuid = uuid.uuid4()
    claims = _make_claims(sub=str(target_uuid), email="audit_ret@example.com")
    _patch_verifier(monkeypatch, claims=claims)
    audit_file = str(tmp_path / "audit.jsonl")

    with test_app.app_context():
        _enable_supabase(test_app)
        existing = _make_legacy_user(username="audit_ret", email="audit_ret@example.com")
        link_app_user_to_supabase(existing.id, target_uuid)

        bridge_supabase_identity("tok", None, audit_path=audit_file)

        with open(audit_file, "r", encoding="utf-8") as fh:
            entries = [json.loads(line) for line in fh if line.strip()]
        assert len(entries) == 1
        assert entries[0]["action"] == "login"


def test_bridge_no_audit_flag_suppresses_writes(test_app, monkeypatch, tmp_path):
    claims = _make_claims(email="silent@example.com")
    _patch_verifier(monkeypatch, claims=claims)
    audit_file = str(tmp_path / "audit.jsonl")

    with test_app.app_context():
        _enable_supabase(test_app)
        bridge_supabase_identity(
            "tok",
            _valid_profile(username="silent"),
            audit_path=audit_file,
            no_audit=True,
        )
        assert not os.path.exists(audit_file)


def test_bridge_does_not_write_audit_on_token_invalid(test_app, monkeypatch, tmp_path):
    """No audit row should be written when verification itself fails."""
    _patch_verifier(monkeypatch, raise_with=SupabaseTokenInvalid("nope"))
    audit_file = str(tmp_path / "audit.jsonl")

    with test_app.app_context():
        _enable_supabase(test_app)
        with pytest.raises(BridgeTokenInvalid):
            bridge_supabase_identity(
                "tok",
                _valid_profile(),
                audit_path=audit_file,
            )
        assert not os.path.exists(audit_file)


def test_bridge_does_not_write_audit_on_email_conflict(test_app, monkeypatch, tmp_path):
    claims = _make_claims(email="audit_conflict@example.com")
    _patch_verifier(monkeypatch, claims=claims)
    audit_file = str(tmp_path / "audit.jsonl")

    with test_app.app_context():
        _enable_supabase(test_app)
        _make_legacy_user(
            username="audit_conflict",
            email="audit_conflict@example.com",
        )
        with pytest.raises(ExistingLegacyUserConflict):
            bridge_supabase_identity(
                "tok",
                _valid_profile(username="someother"),
                audit_path=audit_file,
            )
        assert not os.path.exists(audit_file)
