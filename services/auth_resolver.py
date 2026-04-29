"""Auth resolver shim — Phase 1 of the Supabase Auth migration.

This module is the single seam through which future Supabase Auth-aware code
will resolve "who is the current app user?". In Phase 1 it is a deliberate
no-op shim that delegates entirely to ``flask_login.current_user``; no
existing route or decorator is migrated to use it yet.

Phase 2 will extend ``get_current_app_user`` to fall back to a Supabase JWT
verification branch behind a feature flag. Until then, this module changes
no live behaviour.

Public surface:
    - ``get_current_app_user() -> Optional[User]``
    - ``get_current_app_user_id() -> Optional[int]``
    - ``is_current_app_user_admin() -> bool``

Do not delete this module as "unused"; it exists ahead of its callers on
purpose so Phase 2 can extend it in one place rather than touching every
route.
"""

from __future__ import annotations

from typing import Optional

from flask_login import current_user

from models import User


def _resolved_user() -> Optional[User]:
    """Return the underlying ``User`` row backing ``current_user``, or None.

    Flask-Login exposes ``current_user`` as a proxy that, when no user is
    authenticated, behaves like an anonymous user (``is_authenticated`` is
    False). This helper normalises that to ``None`` so callers do not have
    to interrogate the proxy.
    """
    if not current_user or not current_user.is_authenticated:
        return None
    # ``current_user`` is a LocalProxy; ``_get_current_object`` returns the
    # real underlying ``User`` instance. We avoid touching that private API
    # in Phase 1 and instead rely on attribute access, which is sufficient.
    return current_user  # type: ignore[return-value]


def get_current_app_user() -> Optional[User]:
    """Return the resolved app ``User`` row for this request, or ``None``.

    Phase 1 semantics: equivalent to
        ``current_user if current_user.is_authenticated else None``.

    Phase 2 will extend this with a Supabase JWT branch behind a feature
    flag. The return shape will not change.
    """
    return _resolved_user()


def get_current_app_user_id() -> Optional[int]:
    """Return the integer ``user.id`` for this request, or ``None``.

    Convenience for query filters. Returns ``None`` when no user is
    authenticated.
    """
    user = _resolved_user()
    if user is None:
        return None
    return int(user.id)


def is_current_app_user_admin() -> bool:
    """Return ``True`` iff a resolved user exists and is an admin.

    Phase 1 semantics: equivalent to
        ``current_user.is_authenticated and current_user.is_admin``.
    """
    user = _resolved_user()
    if user is None:
        return False
    return bool(getattr(user, "is_admin", False))
