"""Supabase session bridge — Phase 3a PR 2.

Orchestrates the verify → resolve → create → link → session sequence
that turns a Supabase Auth identity into a normal Flask-Login session.
The bridge is the second sanctioned writer of
``user.supabase_auth_user_id`` (alongside ``scripts/link_supabase_identities.py``
from Phase 2). It is **not yet wired into any HTTP route** — PR 3
introduces ``routes/supabase_auth_routes.py`` and the bridge endpoint
that calls this service. Until then, this module is inert with respect
to live behaviour: it has no callers in production code.

Public surface:

    - ``bridge_supabase_identity(access_token, profile_payload, *,
        login_callback, audit_path, no_audit) -> BridgeOutcome``
      Single entry point. Verifies, resolves or creates the app User,
      writes ``last_login_at``, and (when ``login_callback`` is provided)
      issues a Flask-Login session. Returns a ``BridgeOutcome`` on
      success, raises a typed ``BridgeError`` on failure.

    - ``BridgeOutcome(app_user, was_created, source)``
      Success result. ``source`` is one of ``BRIDGE_SOURCE_NEW_USER``
      / ``BRIDGE_SOURCE_RETURNING``.

    - ``BridgeError`` (base) and the typed children:
        * ``BridgeFlagDisabled`` — defensive: ``SUPABASE_AUTH_ENABLED``
          is False. The route handler enforces the flag at the HTTP
          layer; this guard exists so a misconfigured caller cannot
          drive the bridge while the master flag is off.
        * ``BridgeTokenInvalid`` — token verification failed. Wraps the
          underlying ``SupabaseAuthError``.
        * ``ExistingLegacyUserConflict(app_user_id)`` — the JWT's email
          matches an existing app user that has no
          ``supabase_auth_user_id``. The bridge **refuses to silently
          link** (per accepted decision in ``docs/DECISIONS.md``); the
          route handler translates this into a user-facing prompt.
          Existing-user consent linkage is Phase 3b work.
        * ``BridgeProfileInvalid(field_errors)`` — profile payload is
          missing or invalid for new-user creation. ``field_errors``
          maps field name → human-readable message.

Atomicity:
    The verify → resolve → create → link → ``last_login_at`` write
    sequence runs inside one DB transaction. ``login_callback`` is
    invoked only after a successful commit so a failed session
    establishment does not roll back the persisted user row.

Audit logging:
    Each successful ``link`` (new user) and ``login`` (returning user)
    appends one JSONL row to
    ``backups/auth/supabase_bridge_audit_<YYYYMMDD>.jsonl``. The shape
    matches the linkage CLI's audit rows for forensics consistency.
    Tests pass ``audit_path`` explicitly or ``no_audit=True`` to
    suppress.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from flask import current_app

from extensions import db
from models import User
from services.supabase_auth_linkage import (
    find_app_user_by_email,
    find_app_user_by_supabase_id,
    link_app_user_to_supabase,
)
from services.supabase_auth_service import (
    SupabaseAuthError,
    SupabaseClaims,
    is_enabled as supabase_auth_is_enabled,
    verify_access_token,
)


_BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
DEFAULT_AUDIT_DIR = os.path.join(_BASE_DIR, "backups", "auth")

ALLOWED_REGIONS = frozenset({"UK", "US", "EU"})

BRIDGE_SOURCE_NEW_USER = "bridge_signup"
BRIDGE_SOURCE_RETURNING = "bridge_login"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BridgeError(Exception):
    """Base error for bridge failures."""


class BridgeFlagDisabled(BridgeError):
    """Raised when the bridge is invoked while ``SUPABASE_AUTH_ENABLED`` is False.

    This is defence-in-depth. In production the HTTP route returns 404
    when the flag is off; the bridge service guards against accidental
    direct invocation (e.g. from a misconfigured CLI).
    """


class BridgeTokenInvalid(BridgeError):
    """Raised when token verification fails. Wraps the underlying error."""


class ExistingLegacyUserConflict(BridgeError):
    """Raised when the JWT's email matches an existing unlinked app user.

    The bridge refuses to silently link; the caller (route handler)
    translates this into the user-facing "you already have a Soletrak
    account" prompt. Linking such accounts requires the explicit
    consent + legacy re-authentication flow that ships in Phase 3b.
    """

    def __init__(self, app_user_id: int) -> None:
        super().__init__(
            f"app user {app_user_id} exists by email and is not linked to Supabase"
        )
        self.app_user_id = app_user_id


class BridgeProfileInvalid(BridgeError):
    """Raised when the profile payload is missing or invalid for new-user
    creation. ``field_errors`` maps field name → human-readable message.
    """

    def __init__(self, field_errors: Dict[str, str]) -> None:
        super().__init__(f"profile payload invalid: {sorted(field_errors)}")
        self.field_errors = dict(field_errors)


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BridgeOutcome:
    """Successful bridge result."""

    app_user: User
    was_created: bool
    source: str


# ---------------------------------------------------------------------------
# Profile validation
# ---------------------------------------------------------------------------


def _validate_profile_payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate the new-user profile payload.

    Required: ``username``, ``first_name``, ``last_name``, ``preferred_region``.
    Optional: ``marketing_opt_in`` (defaults to False).

    Username uniqueness is checked here as a friendly pre-flight; the SQL
    unique constraint remains the authoritative guard.
    """
    payload = payload or {}
    errors: Dict[str, str] = {}

    def _strip_str(field: str, *, min_len: int, max_len: int) -> Optional[str]:
        raw = payload.get(field)
        if not isinstance(raw, str):
            errors[field] = f"{field} is required"
            return None
        value = raw.strip()
        if len(value) < min_len:
            errors[field] = f"{field} must be at least {min_len} characters"
            return None
        if len(value) > max_len:
            errors[field] = f"{field} must be at most {max_len} characters"
            return None
        return value

    username = _strip_str("username", min_len=4, max_len=80)
    first_name = _strip_str("first_name", min_len=1, max_len=50)
    last_name = _strip_str("last_name", min_len=1, max_len=50)

    region_raw = payload.get("preferred_region")
    if not isinstance(region_raw, str) or region_raw.strip().upper() not in ALLOWED_REGIONS:
        errors["preferred_region"] = "preferred_region must be one of UK, US, EU"
        preferred_region = None
    else:
        preferred_region = region_raw.strip().upper()

    marketing_opt_in = bool(payload.get("marketing_opt_in", False))

    if username is not None and "username" not in errors:
        existing = User.query.filter_by(username=username).first()
        if existing is not None:
            errors["username"] = "username is already taken"

    if errors:
        raise BridgeProfileInvalid(errors)

    return {
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "preferred_region": preferred_region,
        "marketing_opt_in": marketing_opt_in,
    }


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


def _default_audit_path() -> str:
    """Path of the bridge's per-day JSONL audit file.

    Per-day rotation keeps the file count bounded under any signup volume
    while preserving useful forensics granularity. Operators rotate /
    archive these files via the existing ``backups/`` lifecycle.
    """
    audit_dir = (
        current_app.config.get("SUPABASE_BRIDGE_AUDIT_DIR") if current_app else None
    ) or DEFAULT_AUDIT_DIR
    Path(audit_dir).mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return os.path.join(audit_dir, f"supabase_bridge_audit_{stamp}.jsonl")


def _audit_record(
    audit_path: Optional[str],
    *,
    action: str,
    user: User,
    supabase_uuid: Optional[str],
    auth_provider: Optional[str],
) -> None:
    if audit_path is None:
        return
    entry = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "action": action,
        "app_user_id": user.id,
        "email": user.email,
        "is_admin": bool(user.is_admin),
        "supabase_uuid": str(supabase_uuid) if supabase_uuid else None,
        "auth_provider": auth_provider,
        "source": "bridge",
    }
    Path(os.path.dirname(audit_path)).mkdir(parents=True, exist_ok=True)
    with open(audit_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, default=str) + "\n")


# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------


def _detect_auth_provider(claims: SupabaseClaims) -> str:
    """Pick an ``auth_provider`` value from the JWT claims.

    Supabase exposes the source in ``app_metadata.provider``:
    ``"email"`` for email/password, ``"google"`` / ``"apple"`` / etc.
    for OAuth providers. Map to our ``auth_provider`` taxonomy.
    """
    raw = claims.raw or {}
    app_meta = raw.get("app_metadata") if isinstance(raw.get("app_metadata"), dict) else {}
    provider = app_meta.get("provider") if isinstance(app_meta, dict) else None
    if isinstance(provider, str) and provider:
        if provider == "email":
            return "supabase_email"
        return f"supabase_oauth_{provider}"
    return "supabase_email"


# ---------------------------------------------------------------------------
# The bridge
# ---------------------------------------------------------------------------


def bridge_supabase_identity(
    access_token: str,
    profile_payload: Optional[Dict[str, Any]] = None,
    *,
    login_callback: Optional[Callable[[User], Any]] = None,
    audit_path: Optional[str] = None,
    no_audit: bool = False,
) -> BridgeOutcome:
    """Verify a Supabase access token, resolve or create the app user,
    and (if a callback is provided) issue a Flask-Login session.

    Args:
        access_token: a Supabase Auth JWT (any algorithm supported by
            ``services.supabase_auth_service.verify_access_token``).
        profile_payload: required for new-user creation; ignored when
            the user already exists. See ``_validate_profile_payload``
            for the required shape.
        login_callback: called with the resolved/created ``User`` after
            a successful commit. Use ``flask_login.login_user`` in
            production; tests pass a recorder or ``None``.
        audit_path: explicit audit file path (tests inject a tmp path).
            Defaults to the per-day file under ``backups/auth/``.
        no_audit: when True, skip audit logging entirely.

    Returns:
        ``BridgeOutcome``.

    Raises:
        ``BridgeFlagDisabled``, ``BridgeTokenInvalid``,
        ``ExistingLegacyUserConflict``, ``BridgeProfileInvalid``.
    """
    if not supabase_auth_is_enabled():
        raise BridgeFlagDisabled("SUPABASE_AUTH_ENABLED is False")

    try:
        claims = verify_access_token(access_token)
    except SupabaseAuthError as exc:
        raise BridgeTokenInvalid(str(exc)) from exc

    effective_audit_path: Optional[str] = None
    if not no_audit:
        effective_audit_path = audit_path or _default_audit_path()

    # 1. Returning user — already linked by supabase_auth_user_id.
    linked = find_app_user_by_supabase_id(claims.supabase_user_id)
    if linked is not None:
        try:
            linked.last_login_at = datetime.utcnow()
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        if login_callback is not None:
            login_callback(linked)
        _audit_record(
            effective_audit_path,
            action="login",
            user=linked,
            supabase_uuid=claims.supabase_user_id,
            auth_provider=linked.auth_provider,
        )
        return BridgeOutcome(
            app_user=linked,
            was_created=False,
            source=BRIDGE_SOURCE_RETURNING,
        )

    # 2. Email-match against an unlinked legacy user — refuse to silently link.
    if claims.email:
        existing = find_app_user_by_email(claims.email)
        if existing is not None and existing.supabase_auth_user_id is None:
            raise ExistingLegacyUserConflict(existing.id)

    # 3. New user — validate profile, create row, link.
    if not claims.email:
        raise BridgeProfileInvalid(
            {"email": "Supabase JWT did not provide an email claim"}
        )

    validated = _validate_profile_payload(profile_payload)
    auth_provider = _detect_auth_provider(claims)

    new_user = User(
        username=validated["username"],
        email=claims.email.strip().lower(),
        first_name=validated["first_name"],
        last_name=validated["last_name"],
        preferred_region=validated["preferred_region"],
        marketing_opt_in=validated["marketing_opt_in"],
        is_email_confirmed=True,
        is_admin=False,
        password_hash=None,
        auth_provider=auth_provider,
        last_login_at=datetime.utcnow(),
    )
    db.session.add(new_user)

    try:
        db.session.flush()
        # link_app_user_to_supabase commits the surrounding session,
        # which persists both the new User row and the linkage write
        # atomically. Any failure here (LinkageError on collision, or
        # an IntegrityError from the unique email constraint losing a
        # race with another request) rolls back the in-memory User row
        # too.
        link_app_user_to_supabase(
            new_user.id,
            claims.supabase_user_id,
            by_admin=False,
            source=BRIDGE_SOURCE_NEW_USER,
        )
    except Exception:
        db.session.rollback()
        raise

    if login_callback is not None:
        login_callback(new_user)

    _audit_record(
        effective_audit_path,
        action="link",
        user=new_user,
        supabase_uuid=claims.supabase_user_id,
        auth_provider=auth_provider,
    )
    return BridgeOutcome(
        app_user=new_user,
        was_created=True,
        source=BRIDGE_SOURCE_NEW_USER,
    )
