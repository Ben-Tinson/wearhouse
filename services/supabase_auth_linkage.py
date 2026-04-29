"""Supabase Auth ⇄ app User linkage helpers — Phase 2 foundation.

The functions in this module are the **only** sanctioned writers of
``User.supabase_auth_user_id``. The resolver / auth request path must
never call ``link_app_user_to_supabase`` (per the accepted decision in
``docs/DECISIONS.md``); only the forthcoming admin linkage CLI is
allowed to perform linkage.

Read-only helpers (``find_app_user_by_supabase_id``,
``find_app_user_by_email``) are safe for the resolver to consult.

Linkage rules enforced here:
- target app user must exist
- target Supabase UUID must not already be linked to a different app user
- a target user that is already linked is rejected unless ``by_admin=True``
- email matching is case-insensitive on ``lower(trim(email))`` and
  ignores ``pending_email``
"""

from __future__ import annotations

from typing import Optional, Union
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import User


SupabaseUuid = Union[str, UUID]


class LinkageError(Exception):
    """Base error for app ⇄ Supabase linkage failures."""


class AppUserNotFound(LinkageError):
    """Raised when the target app user does not exist."""


class AppUserAlreadyLinked(LinkageError):
    """Raised when the target app user is already linked to a Supabase identity.

    Re-linking an already-linked user requires explicit ``by_admin=True``
    so admin overrides remain a deliberate, auditable action.
    """


class SupabaseIdentityAlreadyLinked(LinkageError):
    """Raised when the target Supabase UUID already maps to a different app user."""


def _coerce_uuid(value: SupabaseUuid) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def find_app_user_by_supabase_id(supabase_uuid: SupabaseUuid) -> Optional[User]:
    """Look up an app user by their linked Supabase Auth UUID."""
    uuid_value = _coerce_uuid(supabase_uuid)
    return User.query.filter(User.supabase_auth_user_id == uuid_value).first()


def find_app_user_by_email(email: Optional[str]) -> Optional[User]:
    """Case-insensitive lookup against ``User.email``.

    ``pending_email`` is intentionally **not** consulted: per the accepted
    linkage decision, an in-flight email change is an edge case for human
    review, not for automatic matching.
    """
    if not email or not isinstance(email, str):
        return None
    normalised = email.strip().lower()
    if not normalised:
        return None
    return User.query.filter(func.lower(User.email) == normalised).first()


def link_app_user_to_supabase(
    app_user_id: int,
    supabase_uuid: SupabaseUuid,
    *,
    by_admin: bool = False,
    source: str = "cli",
) -> User:
    """Bind an app user row to a Supabase Auth identity.

    Args:
        app_user_id: integer ``user.id``.
        supabase_uuid: the Supabase Auth identity UUID (string or UUID).
        by_admin: when True, allows overriding an existing link on the
            target app user. Audit logging is the caller's responsibility
            (the linkage CLI writes a structured audit row).
        source: free-form tag stored only for the caller's logging; this
            function does not persist it.

    Returns:
        The updated ``User`` row.

    Raises:
        AppUserNotFound, AppUserAlreadyLinked, SupabaseIdentityAlreadyLinked.
    """
    user = db.session.get(User, app_user_id)
    if user is None:
        raise AppUserNotFound(f"app user {app_user_id} does not exist")

    uuid_value = _coerce_uuid(supabase_uuid)

    other = (
        User.query.filter(
            User.supabase_auth_user_id == uuid_value,
            User.id != app_user_id,
        )
        .first()
    )
    if other is not None:
        raise SupabaseIdentityAlreadyLinked(
            f"Supabase identity {uuid_value} already linked to user {other.id}"
        )

    if (
        user.supabase_auth_user_id is not None
        and user.supabase_auth_user_id != uuid_value
        and not by_admin
    ):
        raise AppUserAlreadyLinked(
            f"user {user.id} already linked to {user.supabase_auth_user_id}"
        )

    user.supabase_auth_user_id = uuid_value
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise SupabaseIdentityAlreadyLinked(str(exc)) from exc
    return user


def unlink_app_user(app_user_id: int) -> User:
    """Reversibility helper for the CLI's ``--unlink`` mode."""
    user = db.session.get(User, app_user_id)
    if user is None:
        raise AppUserNotFound(f"app user {app_user_id} does not exist")
    user.supabase_auth_user_id = None
    db.session.commit()
    return user
