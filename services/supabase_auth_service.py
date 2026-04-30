"""Supabase Auth verification helpers — Phase 2 foundation.

This module exposes pure JWT verification helpers. It is **not** wired
into any live request path until the resolver / decorator / probe
consult it behind the ``SUPABASE_AUTH_ENABLED`` feature flag.

Algorithm support:

    - ``HS256`` — legacy Supabase shared-secret tokens. Verified against
      ``SUPABASE_JWT_SECRET``.
    - ``ES256`` / ``RS256`` — Supabase asymmetric signing keys (the
      current default for new projects). Verified against the JWKS
      published at ``<SUPABASE_URL>/auth/v1/.well-known/jwks.json``.

The token's ``alg`` header drives the choice. We do **not** trust an
arbitrary algorithm — the value must be in our explicit allowlist
(``_ASYMMETRIC_ALGS`` ∪ ``_SYMMETRIC_ALGS``) or verification is
refused. This guards against the historic "alg confusion" class of
attacks (e.g. an attacker swapping ES256 → HS256 against the public
key).

Admin-side SDK helpers (identity creation, password-reset trigger) are
the ``SupabaseAdminClient`` REST wrapper used only by the linkage CLI.

Issuer / audience claim hardening is intentionally a Phase 3 concern —
Phase 2 establishes the verification seam, not the full claim-policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any, Dict, Optional

import jwt
import requests
from flask import current_app
from jwt import PyJWKClient


_ASYMMETRIC_ALGS = frozenset({"ES256", "RS256"})
_SYMMETRIC_ALGS = frozenset({"HS256"})
_ALL_ACCEPTED_ALGS = _ASYMMETRIC_ALGS | _SYMMETRIC_ALGS

# Process-wide cache of PyJWKClient instances keyed by JWKS URL. PyJWKClient
# performs its own per-key caching with a configurable lifespan; we just
# avoid building a new client per request. The lock keeps the cache safe
# under threaded WSGI workers (gunicorn sync workers do not share state, but
# threaded / async workers might).
_jwks_clients: Dict[str, PyJWKClient] = {}
_jwks_clients_lock = Lock()
_JWKS_KEY_LIFESPAN_SECONDS = 3600


def _jwks_url_for(supabase_url: str) -> str:
    """Return the JWKS URL for a Supabase project base URL."""
    return f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"


def _get_jwks_client(supabase_url: str) -> PyJWKClient:
    """Return a cached ``PyJWKClient`` for this Supabase project URL.

    Tests monkeypatch this helper to inject a fake client without touching
    the network. Production code paths consult Supabase's JWKS endpoint
    once per ``_JWKS_KEY_LIFESPAN_SECONDS`` per signing kid.
    """
    url = _jwks_url_for(supabase_url)
    with _jwks_clients_lock:
        client = _jwks_clients.get(url)
        if client is None:
            client = PyJWKClient(
                url,
                cache_keys=True,
                lifespan=_JWKS_KEY_LIFESPAN_SECONDS,
            )
            _jwks_clients[url] = client
        return client


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


def _resolve_verification_key(token: str, alg: str):
    """Pick the verification key for ``alg``, or raise.

    Asymmetric algs resolve to the JWKS public key matched by ``kid``.
    Symmetric algs resolve to ``SUPABASE_JWT_SECRET``. Anything else is
    refused before signature verification is attempted.
    """
    if alg in _ASYMMETRIC_ALGS:
        supabase_url = (current_app.config.get("SUPABASE_URL") or "").strip()
        if not supabase_url:
            raise SupabaseAuthMisconfigured(
                f"SUPABASE_URL is required to verify {alg} JWTs against JWKS"
            )
        try:
            jwks = _get_jwks_client(supabase_url)
            signing_key = jwks.get_signing_key_from_jwt(token)
        except jwt.InvalidTokenError as exc:
            raise SupabaseTokenInvalid(f"could not resolve JWKS signing key: {exc}") from exc
        except Exception as exc:  # network errors, JWKS parse errors, etc.
            raise SupabaseTokenInvalid(f"JWKS lookup failed: {exc}") from exc
        return signing_key.key

    if alg in _SYMMETRIC_ALGS:
        secret = current_app.config.get("SUPABASE_JWT_SECRET")
        if not secret:
            raise SupabaseAuthMisconfigured(
                f"SUPABASE_JWT_SECRET is required to verify {alg} JWTs"
            )
        return secret

    raise SupabaseTokenInvalid(f"unsupported JWT alg: {alg!r}")


def verify_access_token(token: str) -> SupabaseClaims:
    """Verify a Supabase access token and return its claims.

    The token's ``alg`` header determines the verification path:

        - ``ES256`` / ``RS256`` → JWKS lookup against
          ``<SUPABASE_URL>/auth/v1/.well-known/jwks.json``.
        - ``HS256`` → ``SUPABASE_JWT_SECRET``.

    Raises:
        SupabaseAuthDisabled: when the feature flag is off.
        SupabaseAuthMisconfigured: when the credential needed for the
            chosen alg is not configured (``SUPABASE_URL`` for
            asymmetric, ``SUPABASE_JWT_SECRET`` for symmetric).
        SupabaseTokenInvalid: for any structural / signature / claims
            failure, including an unsupported ``alg``.
    """
    if not is_enabled():
        raise SupabaseAuthDisabled("SUPABASE_AUTH_ENABLED is False")

    if not looks_like_jwt(token):
        raise SupabaseTokenInvalid("not a structurally valid JWT")

    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise SupabaseTokenInvalid(f"unparseable JWT header: {exc}") from exc

    alg = header.get("alg")
    if alg not in _ALL_ACCEPTED_ALGS:
        raise SupabaseTokenInvalid(f"unsupported JWT alg: {alg!r}")

    verification_key = _resolve_verification_key(token, alg)

    try:
        claims = jwt.decode(
            token,
            verification_key,
            algorithms=[alg],
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
