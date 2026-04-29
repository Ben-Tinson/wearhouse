"""Tests for ``scripts/link_supabase_identities.py``.

The CLI is the only sanctioned writer of ``user.supabase_auth_user_id``
in production. These tests pin its dry-run-by-default contract,
``--admins-only`` scoping, idempotency, audit logging, and reversibility
via ``--unlink``. Tests inject a fake Supabase admin client so no
network calls are made.
"""

from __future__ import annotations

import io
import json
import os
import uuid
from argparse import Namespace
from typing import Any, Dict, List, Optional

import pytest

from extensions import db
from models import User
from scripts.link_supabase_identities import (
    _validate_args,
    main as cli_main,
    run_link,
    run_unlink,
)


# ---------------------------------------------------------------------------
# Fake Supabase admin client (no network)
# ---------------------------------------------------------------------------


class FakeSupabaseAdminClient:
    """In-memory stand-in for ``SupabaseAdminClient``.

    Records every call so tests can assert behaviour. Returns deterministic
    UUIDs for ``create_user`` so assertions are stable.
    """

    def __init__(
        self,
        existing_users: Optional[List[Dict[str, Any]]] = None,
        next_create_uuid: str = "33333333-3333-3333-3333-333333333333",
    ) -> None:
        self.existing_users: List[Dict[str, Any]] = list(existing_users or [])
        self._next_uuid = next_create_uuid
        self.created_users: List[Dict[str, Any]] = []
        self.recovery_calls: List[str] = []
        self.lookup_calls: List[str] = []

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        self.lookup_calls.append(email)
        target = (email or "").strip().lower()
        for user in self.existing_users:
            if (user.get("email") or "").lower() == target:
                return user
        return None

    def create_user(self, email: str, *, email_confirm: bool = True) -> Dict[str, Any]:
        new_user = {
            "id": self._next_uuid,
            "email": email,
            "email_confirm": email_confirm,
        }
        self.existing_users.append(new_user)
        self.created_users.append(new_user)
        # Roll the next UUID so a second create_user call is distinct.
        self._next_uuid = str(uuid.uuid4())
        return new_user

    def send_recovery_link(self, email: str) -> None:
        self.recovery_calls.append(email)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(**overrides) -> User:
    defaults = {
        "username": "cli_user",
        "email": "cli_user@example.com",
        "first_name": "CLI",
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


def _args(**overrides) -> Namespace:
    defaults = {
        "apply": False,
        "admins_only": True,
        "user_id": None,
        "unlink": False,
        "send_onboarding": False,
        "audit_dir": "/tmp/_unused_audit_dir_should_not_be_created",
        "audit_path": None,
        "no_audit": False,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


def _read_audit(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args, expected_error_substring",
    [
        ({"apply": False, "admins_only": False, "user_id": None}, "explicit scope"),
        ({"apply": False, "admins_only": True, "user_id": 1}, "either --admins-only or --user-id"),
        ({"unlink": True, "user_id": None}, "requires --user-id"),
        ({"unlink": True, "user_id": 1, "admins_only": True}, "does not accept --admins-only"),
        ({"unlink": True, "user_id": 1, "send_onboarding": True}, "no meaning with --unlink"),
    ],
)
def test_validate_args_rejects_invalid_combinations(args, expected_error_substring):
    base = _args(**args)
    base.admins_only = args.get("admins_only", False)  # don't default to True for these tests
    error = _validate_args(base)
    assert error is not None
    assert expected_error_substring in error


def test_validate_args_accepts_admins_only_dry_run():
    assert _validate_args(_args()) is None


def test_validate_args_accepts_user_id_dry_run():
    assert _validate_args(_args(admins_only=False, user_id=42)) is None


def test_validate_args_accepts_unlink_with_user_id():
    assert _validate_args(_args(admins_only=False, user_id=42, unlink=True)) is None


# ---------------------------------------------------------------------------
# Dry-run mode
# ---------------------------------------------------------------------------


def test_dry_run_admins_only_is_read_only(test_app, tmp_path):
    """Default invocation produces a report and writes nothing."""
    with test_app.app_context():
        admin = _make_user(username="admin_one", email="admin_one@example.com", is_admin=True)
        _make_user(username="regular", email="regular@example.com", is_admin=False)

        out = io.StringIO()
        client = FakeSupabaseAdminClient()
        exit_code = run_link(_args(audit_path=str(tmp_path / "audit.jsonl")), client=client, output=out)
        assert exit_code == 0

        # No audit file written in dry-run.
        assert not (tmp_path / "audit.jsonl").exists()

        # No DB writes.
        reloaded = db.session.get(User, admin.id)
        assert reloaded.supabase_auth_user_id is None

        # Output reports the admin only (regular user is excluded by --admins-only).
        out_text = out.getvalue()
        assert "DRY-RUN" in out_text
        assert "admin_one@example.com" in out_text
        assert "regular@example.com" not in out_text


def test_dry_run_with_existing_supabase_identity_classified_correctly(test_app, tmp_path):
    with test_app.app_context():
        admin = _make_user(username="admin_two", email="admin_two@example.com", is_admin=True)
        out = io.StringIO()
        client = FakeSupabaseAdminClient(
            existing_users=[{"id": "11111111-1111-1111-1111-111111111111", "email": "admin_two@example.com"}],
        )
        run_link(_args(), client=client, output=out)
        text = out.getvalue()
        assert "would link to existing Supabase id 11111111-1111-1111-1111-111111111111" in text

        reloaded = db.session.get(User, admin.id)
        assert reloaded.supabase_auth_user_id is None


def test_dry_run_user_already_linked_reports_noop(test_app):
    with test_app.app_context():
        existing_uuid = uuid.uuid4()
        admin = _make_user(username="already_linked", email="al@example.com", is_admin=True)
        admin.supabase_auth_user_id = existing_uuid
        db.session.commit()

        out = io.StringIO()
        run_link(_args(), client=FakeSupabaseAdminClient(), output=out)
        assert "already linked, skipping" in out.getvalue()


def test_dry_run_unconfirmed_admin_blocked(test_app):
    with test_app.app_context():
        _make_user(
            username="unconf_admin",
            email="unconf@example.com",
            is_admin=True,
            is_email_confirmed=False,
        )
        out = io.StringIO()
        exit_code = run_link(_args(), client=FakeSupabaseAdminClient(), output=out)
        assert "BLOCKED: admin email is not confirmed" in out.getvalue()
        assert exit_code == 1  # blocked candidates → non-zero exit


def test_dry_run_with_no_client_works_offline(test_app):
    """Dry-run without a Supabase client still produces a useful report."""
    with test_app.app_context():
        _make_user(username="off_admin", email="off@example.com", is_admin=True)
        out = io.StringIO()
        run_link(_args(), client=None, output=out)
        text = out.getvalue()
        assert "would create new Supabase identity and link (offline dry-run)" in text


# ---------------------------------------------------------------------------
# Apply mode
# ---------------------------------------------------------------------------


def test_apply_creates_supabase_identity_and_links(test_app, tmp_path):
    with test_app.app_context():
        admin = _make_user(username="new_admin", email="new_admin@example.com", is_admin=True)
        out = io.StringIO()
        audit_path = str(tmp_path / "audit.jsonl")
        client = FakeSupabaseAdminClient()

        exit_code = run_link(
            _args(apply=True, audit_path=audit_path),
            client=client,
            output=out,
        )
        assert exit_code == 0

        reloaded = db.session.get(User, admin.id)
        assert str(reloaded.supabase_auth_user_id) == "33333333-3333-3333-3333-333333333333"
        assert client.created_users[0]["email"] == "new_admin@example.com"
        assert client.created_users[0]["email_confirm"] is True
        assert client.recovery_calls == []  # send_onboarding was False

        entries = _read_audit(audit_path)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["action"] == "link"
        assert entry["app_user_id"] == admin.id
        assert entry["email"] == "new_admin@example.com"
        assert entry["is_admin"] is True
        assert entry["supabase_uuid"] == "33333333-3333-3333-3333-333333333333"
        assert entry["dry_run"] is False
        assert entry["source"] == "cli"
        assert entry["by_admin"] is False


def test_apply_links_to_existing_supabase_identity(test_app, tmp_path):
    with test_app.app_context():
        admin = _make_user(username="existing_admin", email="existing@example.com", is_admin=True)
        out = io.StringIO()
        client = FakeSupabaseAdminClient(
            existing_users=[{"id": "22222222-2222-2222-2222-222222222222", "email": "existing@example.com"}],
        )
        audit_path = str(tmp_path / "audit.jsonl")
        run_link(_args(apply=True, audit_path=audit_path), client=client, output=out)

        reloaded = db.session.get(User, admin.id)
        assert str(reloaded.supabase_auth_user_id) == "22222222-2222-2222-2222-222222222222"
        # We should NOT have called create_user when an existing identity matched.
        assert client.created_users == []
        entry = _read_audit(audit_path)[0]
        assert entry["action"] == "link"
        assert entry["supabase_uuid"] == "22222222-2222-2222-2222-222222222222"


def test_apply_with_send_onboarding_sends_recovery_email(test_app, tmp_path):
    with test_app.app_context():
        _make_user(username="onb_admin", email="onb@example.com", is_admin=True)
        client = FakeSupabaseAdminClient()
        run_link(
            _args(apply=True, send_onboarding=True, audit_path=str(tmp_path / "audit.jsonl")),
            client=client,
            output=io.StringIO(),
        )
        assert client.recovery_calls == ["onb@example.com"]


def test_apply_idempotent_for_already_linked_admin(test_app, tmp_path):
    """Re-running apply for an already-linked admin must be a no-op, not an error."""
    with test_app.app_context():
        existing_uuid = uuid.uuid4()
        admin = _make_user(username="idem_admin", email="idem@example.com", is_admin=True)
        admin.supabase_auth_user_id = existing_uuid
        db.session.commit()

        client = FakeSupabaseAdminClient()
        audit_path = str(tmp_path / "audit.jsonl")
        exit_code = run_link(
            _args(apply=True, audit_path=audit_path),
            client=client,
            output=io.StringIO(),
        )
        assert exit_code == 0

        # No identity created; no recovery sent.
        assert client.created_users == []
        assert client.recovery_calls == []

        # Audit row records the noop.
        entries = _read_audit(audit_path)
        assert len(entries) == 1
        assert entries[0]["action"] == "noop"
        assert entries[0]["app_user_id"] == admin.id

        # User row unchanged.
        reloaded = db.session.get(User, admin.id)
        assert reloaded.supabase_auth_user_id == existing_uuid


def test_apply_records_blocked_admins_as_errors(test_app, tmp_path):
    with test_app.app_context():
        _make_user(
            username="blocked_admin",
            email="blocked@example.com",
            is_admin=True,
            is_email_confirmed=False,
        )
        client = FakeSupabaseAdminClient()
        audit_path = str(tmp_path / "audit.jsonl")
        exit_code = run_link(
            _args(apply=True, audit_path=audit_path),
            client=client,
            output=io.StringIO(),
        )
        assert exit_code == 1

        entries = _read_audit(audit_path)
        assert len(entries) == 1
        assert entries[0]["action"] == "error"
        assert "admin email is not confirmed" in entries[0]["error"]
        assert client.created_users == []


def test_apply_admins_only_excludes_regular_users(test_app, tmp_path):
    with test_app.app_context():
        admin = _make_user(username="scope_admin", email="scope_admin@example.com", is_admin=True)
        regular = _make_user(username="scope_reg", email="scope_reg@example.com", is_admin=False)
        client = FakeSupabaseAdminClient()
        run_link(
            _args(apply=True, audit_path=str(tmp_path / "audit.jsonl")),
            client=client,
            output=io.StringIO(),
        )

        assert db.session.get(User, admin.id).supabase_auth_user_id is not None
        assert db.session.get(User, regular.id).supabase_auth_user_id is None
        # Recovery email NOT sent (we didn't pass --send-onboarding).
        assert client.recovery_calls == []
        # Only one create_user call (for the admin).
        assert len(client.created_users) == 1
        assert client.created_users[0]["email"] == "scope_admin@example.com"


def test_apply_with_user_id_targets_single_user(test_app, tmp_path):
    with test_app.app_context():
        target = _make_user(username="single_target", email="single@example.com", is_admin=False)
        client = FakeSupabaseAdminClient()
        run_link(
            _args(apply=True, admins_only=False, user_id=target.id, audit_path=str(tmp_path / "audit.jsonl")),
            client=client,
            output=io.StringIO(),
        )
        assert db.session.get(User, target.id).supabase_auth_user_id is not None


def test_apply_requires_client(test_app):
    with test_app.app_context():
        _make_user(username="no_client_admin", email="ncl@example.com", is_admin=True)
        exit_code = run_link(_args(apply=True), client=None, output=io.StringIO())
        assert exit_code == 2  # config error


# ---------------------------------------------------------------------------
# Unlink mode
# ---------------------------------------------------------------------------


def test_unlink_dry_run_does_not_clear_link(test_app, tmp_path):
    with test_app.app_context():
        existing_uuid = uuid.uuid4()
        user = _make_user(username="ul_user", email="ul@example.com", is_admin=True)
        user.supabase_auth_user_id = existing_uuid
        db.session.commit()

        out = io.StringIO()
        exit_code = run_unlink(
            _args(unlink=True, admins_only=False, user_id=user.id, audit_path=str(tmp_path / "audit.jsonl")),
            output=out,
        )
        assert exit_code == 0
        assert "Dry-run" in out.getvalue()
        # Link still in place.
        assert db.session.get(User, user.id).supabase_auth_user_id == existing_uuid
        # No audit file.
        assert not (tmp_path / "audit.jsonl").exists()


def test_unlink_apply_clears_link_and_audits(test_app, tmp_path):
    with test_app.app_context():
        existing_uuid = uuid.uuid4()
        user = _make_user(username="ul_apply", email="ul_apply@example.com", is_admin=True)
        user.supabase_auth_user_id = existing_uuid
        db.session.commit()

        audit_path = str(tmp_path / "audit.jsonl")
        exit_code = run_unlink(
            _args(unlink=True, admins_only=False, apply=True, user_id=user.id, audit_path=audit_path),
            output=io.StringIO(),
        )
        assert exit_code == 0

        reloaded = db.session.get(User, user.id)
        assert reloaded.supabase_auth_user_id is None

        entries = _read_audit(audit_path)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["action"] == "unlink"
        assert entry["app_user_id"] == user.id
        assert entry["supabase_uuid"] == str(existing_uuid)


def test_unlink_idempotent_on_already_unlinked_user(test_app, tmp_path):
    with test_app.app_context():
        user = _make_user(username="ul_already", email="ula@example.com", is_admin=True)
        out = io.StringIO()
        exit_code = run_unlink(
            _args(unlink=True, admins_only=False, apply=True, user_id=user.id, audit_path=str(tmp_path / "audit.jsonl")),
            output=out,
        )
        assert exit_code == 0
        assert "already unlinked" in out.getvalue()
        # No audit file written for the no-op path (we exit before reaching the audit write).
        assert not (tmp_path / "audit.jsonl").exists()


def test_unlink_user_not_found(test_app):
    with test_app.app_context():
        exit_code = run_unlink(_args(unlink=True, admins_only=False, user_id=99999), output=io.StringIO())
        assert exit_code == 1


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


def test_no_audit_flag_suppresses_audit_file(test_app, tmp_path):
    with test_app.app_context():
        _make_user(username="no_audit_admin", email="noa@example.com", is_admin=True)
        client = FakeSupabaseAdminClient()
        audit_path = str(tmp_path / "audit.jsonl")
        run_link(
            _args(apply=True, no_audit=True, audit_path=audit_path),
            client=client,
            output=io.StringIO(),
        )
        assert not os.path.exists(audit_path)


def test_audit_appends_multiple_entries(test_app, tmp_path):
    with test_app.app_context():
        _make_user(username="aa1", email="aa1@example.com", is_admin=True)
        _make_user(username="aa2", email="aa2@example.com", is_admin=True)
        client = FakeSupabaseAdminClient()
        audit_path = str(tmp_path / "audit.jsonl")
        run_link(
            _args(apply=True, audit_path=audit_path),
            client=client,
            output=io.StringIO(),
        )
        entries = _read_audit(audit_path)
        assert len(entries) == 2
        assert {e["email"] for e in entries} == {"aa1@example.com", "aa2@example.com"}
        assert all(e["action"] == "link" for e in entries)


# ---------------------------------------------------------------------------
# main() argv parsing safety
# ---------------------------------------------------------------------------


def test_main_rejects_missing_scope():
    """Calling the CLI without --admins-only or --user-id must not silently broaden."""
    exit_code = cli_main([])
    assert exit_code == 2


def test_main_rejects_unlink_without_user_id():
    exit_code = cli_main(["--unlink"])
    assert exit_code == 2


def test_main_rejects_apply_without_supabase_config(test_app, monkeypatch):
    """--apply without SUPABASE_URL / SERVICE_ROLE_KEY must refuse, not panic."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    exit_code = cli_main(["--admins-only", "--apply"])
    assert exit_code == 2
