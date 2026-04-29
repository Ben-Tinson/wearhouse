"""Supabase Auth verification helpers — Phase 2 foundation.

This module exposes pure JWT verification helpers. It is **not** wired
into any live request path in this slice; the resolver shim consults it
behind the ``SUPABASE_AUTH_ENABLED`` feature flag.

Admin-side SDK helpers (identity creation, password-reset trigger) are
deliberately deferred to the linkage-CLI slice so that the foundation
does not require the Supabase SDK as a runtime dependency. This keeps
Phase 2 boot-time and import-time risk minimal.

Verification is performed against the project's HS256 JWT signing
secret (``SUPABASE_JWT_SECRET``). Issuer / audience claim hardening is
intentionally a Phase 3 concern — Phase 2 establishes the verification
seam, not the full claim-policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import jwt
import requests
from flask import current_app


class SupabaseAuthError(Exception):
    """Base error for Supabase Auth verification failures."""


class SupabaseAuthDisabled(SupabaseAuthError):
    """Raised when verification is attempted while the feature flag is off.

    Phase 2 callers must check ``is_enabled()`` first; this error exists so
    that any accidental invocation while disabled fails loudly rather than
    silently returning a "no auth" outcome that callers might misinterpret.
    """


class SupabaseAuthMisconfigured(SupabaseAuthError):
    """Raised when ``SUPABASE_JWT_SECRET`` is missing while the flag is on."""


class SupabaseTokenInvalid(SupabaseAuthError):
    """Raised when the token fails structural / signature / claims checks."""


class SupabaseAdminError(SupabaseAuthError):
    """Raised when a Supabase Admin REST API call fails."""


@dataclass(frozen=True)
class SupabaseClaims:
    """The minimal subset of Supabase JWT claims we depend on."""

    supabase_user_id: str
    email: Optional[str]
    raw: Dict[str, Any]


def is_enabled() -> bool:
    """Return True iff ``SUPABASE_AUTH_ENABLED`` is True in the active config."""
    try:
        return bool(current_app.config.get("SUPABASE_AUTH_ENABLED", False))
    except RuntimeError:
        return False


def looks_like_jwt(value: Optional[str]) -> bool:
    """Cheap structural check: a JWT has exactly two ``.`` separators.

    This is the documented disambiguator from the Phase 2 plan / accepted
    bearer-collision policy: ``UserApiToken`` values (43-char URL-safe
    base64) contain zero ``.`` characters; Supabase JWTs always contain
    exactly two. This helper does not verify the token; it only decides
    whether verification should be attempted.
    """
    if not isinstance(value, str) or not value:
        return False
    return value.count(".") == 2


def verify_access_token(token: str) -> SupabaseClaims:
    """Verify a Supabase access token and return its claims.

    Raises:
        SupabaseAuthDisabled: when the feature flag is off.
        SupabaseAuthMisconfigured: when ``SUPABASE_JWT_SECRET`` is unset.
        SupabaseTokenInvalid: for any structural / signature / claims failure.
    """
    if not is_enabled():
        raise SupabaseAuthDisabled("SUPABASE_AUTH_ENABLED is False")

    secret = current_app.config.get("SUPABASE_JWT_SECRET")
    if not secret:
        raise SupabaseAuthMisconfigured("SUPABASE_JWT_SECRET is not configured")

    if not looks_like_jwt(token):
        raise SupabaseTokenInvalid("not a structurally valid JWT")

    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"require": ["exp", "sub"], "verify_aud": False},
        )
    except jwt.InvalidTokenError as exc:
        raise SupabaseTokenInvalid(str(exc)) from exc

    sub = claims.get("sub")
    if not sub:
        raise SupabaseTokenInvalid("token missing 'sub' claim")

    email = claims.get("email")
    return SupabaseClaims(
        supabase_user_id=str(sub),
        email=email if isinstance(email, str) and email else None,
        raw=claims,
    )


class SupabaseAdminClient:
    """Thin wrapper around the Supabase Admin REST API.

    Used **only** by the admin linkage CLI. It is intentionally not
    imported by any request-path code; live request handling never talks
    to the Supabase admin endpoints.

    The wrapper covers the three operations the CLI needs:

    - ``get_user_by_email`` — look up a Supabase Auth identity by email.
    - ``create_user`` — create a Supabase Auth identity.
    - ``send_recovery_link`` — trigger Supabase's password-reset email.

    All methods raise ``SupabaseAdminError`` on transport or HTTP failure.
    Tests inject a fake client by passing it directly to the CLI rather
    than invoking the real Supabase API.
    """

    def __init__(
        self,
        base_url: str,
        service_role_key: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        if not base_url or not service_role_key:
            raise SupabaseAdminError("SupabaseAdminClient requires base_url + service_role_key")
        self._base_url = base_url.rstrip("/")
        self._key = service_role_key
        self._timeout = timeout

    def _headers(self) -> Dict[str, str]:
        return {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs):
        url = f"{self._base_url}{path}"
        try:
            response = requests.request(
                method,
                url,
                headers=self._headers(),
                timeout=self._timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise SupabaseAdminError(f"{method} {url} failed: {exc}") from exc
        if response.status_code >= 400:
            raise SupabaseAdminError(
                f"{method} {url} → HTTP {response.status_code}: {response.text[:200]}"
            )
        return response

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Look up a Supabase user by email. Returns the raw user dict or None."""
        response = self._request(
            "GET",
            "/auth/v1/admin/users",
            params={"email": email},
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise SupabaseAdminError(f"non-JSON response from admin/users: {exc}") from exc
        users = body.get("users") if isinstance(body, dict) else None
        if not isinstance(users, list):
            return None
        target = (email or "").strip().lower()
        for user in users:
            if isinstance(user, dict) and (user.get("email") or "").lower() == target:
                return user
        return None

    def create_user(self, email: str, *, email_confirm: bool = True) -> Dict[str, Any]:
        """Create a Supabase Auth user. Returns the new user dict."""
        response = self._request(
            "POST",
            "/auth/v1/admin/users",
            json={"email": email, "email_confirm": email_confirm},
        )
        try:
            return response.json()
        except ValueError as exc:
            raise SupabaseAdminError(f"non-JSON response from admin/users create: {exc}") from exc

    def send_recovery_link(self, email: str) -> None:
        """Trigger a Supabase password-reset / recovery email."""
        self._request(
            "POST",
            "/auth/v1/recover",
            json={"email": email},
        )
