"""Auth resolver shim — Phase 1 + Phase 2 (flag-off-by-default).

This module is the single seam through which Supabase Auth-aware code
will resolve "who is the current app user?".

Resolution order (codified per ``docs/DECISIONS.md``):

    1. Flask-Login session — if ``current_user`` is authenticated, return it.
       This protects existing logged-in users and the documented admin
       break-glass path during the dual-run window.
    2. Supabase JWT branch — only when ``SUPABASE_AUTH_ENABLED`` is True and
       a structurally-valid JWT is present in the ``Authorization: Bearer …``
       header. The JWT is verified against ``SUPABASE_JWT_SECRET`` and the
       app user is looked up by ``user.supabase_auth_user_id``.
    3. ``None``.

Hard invariants (must not regress):

    - When ``SUPABASE_AUTH_ENABLED`` is False, the resolver is byte-for-byte
      equivalent to the Phase 1 shape: ``current_user if authenticated
      else None``. Verified by ``tests/test_auth_resolver_phase2.py``.
    - The resolver never writes to ``user.supabase_auth_user_id``. A JWT
      whose email matches an unlinked app user is logged but does not
      auto-link; explicit linkage is the linkage CLI's job.
    - The resolver tolerates being called outside a request context by
      simply skipping the JWT branch.

Public surface:
    - ``get_current_app_user() -> Optional[User]``
    - ``get_current_app_user_id() -> Optional[int]``
    - ``is_current_app_user_admin() -> bool``

This module is **not yet imported by any live route or decorator**. Phase 2
ships only the resolver capability; existing routes and decorators continue
to read ``flask_login.current_user`` directly.
"""

from __future__ import annotations

from typing import Optional

from flask import current_app, has_request_context, request
from flask_login import current_user

from models import User
from services.supabase_auth_linkage import (
    find_app_user_by_email,
    find_app_user_by_supabase_id,
)
from services.supabase_auth_service import (
    SupabaseAuthError,
    is_enabled as supabase_auth_is_enabled,
    looks_like_jwt,
    verify_access_token,
)


def _flask_login_user() -> Optional[User]:
    """Return the underlying ``User`` row backing ``current_user``, or None."""
    if not current_user or not current_user.is_authenticated:
        return None
    return current_user  # type: ignore[return-value]


def _supabase_jwt_user() -> Optional[User]:
    """Resolve the request to an app User via a verified Supabase JWT.

    Returns None when the flag is off, when there is no request context,
    when no ``Authorization: Bearer …`` header is present, when the value
    is not structurally a JWT, when verification fails, or when the JWT is
    valid but its identity is not yet linked to an app user.

    Never writes ``user.supabase_auth_user_id``. Per the accepted
    write-safety rule, linkage is performed only by the explicit linkage
    tooling.
    """
    if not supabase_auth_is_enabled():
        return None
    if not has_request_context():
        return None

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header.split(None, 1)[1].strip()
    if not looks_like_jwt(token):
        return None

    try:
        claims = verify_access_token(token)
    except SupabaseAuthError:
        return None

    linked = find_app_user_by_supabase_id(claims.supabase_user_id)
    if linked is not None:
        return linked

    if claims.email:
        candidate = find_app_user_by_email(claims.email)
        if candidate is not None and candidate.supabase_auth_user_id is None:
            current_app.logger.warning(
                "Supabase JWT for unlinked email %s — explicit linkage required",
                claims.email,
            )
    return None


def _resolved_user() -> Optional[User]:
    """Resolve the current request to an app User, or ``None``.

    Flask-Login wins when present; the Supabase JWT branch is only
    consulted when no Flask-Login session is established and the feature
    flag is on.
    """
    user = _flask_login_user()
    if user is not None:
        return user
    return _supabase_jwt_user()


def get_current_app_user() -> Optional[User]:
    """Return the resolved app ``User`` row for this request, or ``None``."""
    return _resolved_user()


def get_current_app_user_id() -> Optional[int]:
    """Return the integer ``user.id`` for this request, or ``None``."""
    user = _resolved_user()
    if user is None:
        return None
    return int(user.id)


def is_current_app_user_admin() -> bool:
    """Return ``True`` iff a resolved user exists and is an admin."""
    user = _resolved_user()
    if user is None:
        return False
    return bool(getattr(user, "is_admin", False))
