"""Supabase Auth blueprint — Phase 3a PR 3.

Hosts the bridge endpoint that turns a verified Supabase JWT into a
Flask-Login session for a brand-new user. This is the **first live
caller** of ``services.supabase_session_bridge.bridge_supabase_identity``.

All routes in this blueprint return 404 unless
``SUPABASE_NEW_USER_SIGNUP_ENABLED`` is True. The Phase 3a default is
False, so until the launch flag flip the entire blueprint is invisible
to end users.

PR 3 ships only the JSON bridge endpoint. The HTML signup / onboarding /
OAuth-callback / confirm pages ship in PR 4 alongside the Supabase JS
client glue.
"""

from __future__ import annotations

from flask import Blueprint, abort, current_app, jsonify, request
from flask_login import login_user

from extensions import csrf
from services.supabase_session_bridge import (
    BridgeFlagDisabled,
    BridgeProfileInvalid,
    BridgeTokenInvalid,
    ExistingLegacyUserConflict,
    bridge_supabase_identity,
)


supabase_auth_bp = Blueprint('supabase_auth', __name__, url_prefix='/auth/supabase')


def _flag_or_404() -> None:
    """Hide the entire blueprint when Phase 3a is not yet enabled."""
    if not current_app.config.get('SUPABASE_NEW_USER_SIGNUP_ENABLED'):
        abort(404)


@supabase_auth_bp.route('/bridge', methods=['POST'])
@csrf.exempt
def bridge():
    """Verify a Supabase access token, resolve / create the app User,
    issue a Flask-Login session.

    Request body (JSON)::

        {
            "access_token": "<supabase access token JWT>",
            "profile": {
                "username": "...",
                "first_name": "...",
                "last_name": "...",
                "preferred_region": "UK" | "US" | "EU",
                "marketing_opt_in": false
            }
        }

    The ``profile`` object is required for new-user creation and ignored
    for returning users (resolved by ``user.supabase_auth_user_id``).

    Response codes:
        - 404 — ``SUPABASE_NEW_USER_SIGNUP_ENABLED`` is False (the
          endpoint is invisible).
        - 200 — success. Body has ``ok=true``, ``user_id``,
          ``was_created``, ``source``, ``redirect``. A Flask-Login
          session cookie is set on the response.
        - 400 — request body invalid OR profile validation failed. Body
          carries ``error`` and (for profile failures) ``field_errors``.
        - 401 — token verification failed. Body carries ``error`` and
          ``message``.
        - 409 — JWT email matches an existing legacy user that is not
          yet linked. Body carries ``error="existing_legacy_account"``
          and ``existing_user_id_hint``. The caller (front-end) shows a
          consent prompt; explicit-consent linkage ships in Phase 3b.
        - 503 — defensive: ``SUPABASE_AUTH_ENABLED`` is False while
          ``SUPABASE_NEW_USER_SIGNUP_ENABLED`` is True (config drift).
          The bridge service itself raised ``BridgeFlagDisabled``.

    The endpoint is CSRF-exempt because the JWT in the body is the
    credential — there is no cookie-based authentication at the moment
    of this call. Once Flask-Login issues a session cookie, subsequent
    cookie-bearing requests use the existing CSRF-protected routes.
    """
    _flag_or_404()

    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        return jsonify({
            "ok": False,
            "error": "invalid_request",
            "message": "request body must be a JSON object",
        }), 400

    access_token = body.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        return jsonify({
            "ok": False,
            "error": "missing_access_token",
            "message": "access_token is required",
        }), 400

    profile_payload = body.get("profile")
    if profile_payload is not None and not isinstance(profile_payload, dict):
        return jsonify({
            "ok": False,
            "error": "invalid_profile",
            "message": "profile must be a JSON object when provided",
        }), 400

    try:
        outcome = bridge_supabase_identity(
            access_token,
            profile_payload,
            login_callback=login_user,
        )
    except BridgeFlagDisabled:
        # Defence-in-depth: the master Supabase Auth flag is off but
        # the Phase 3a signup flag is on. This is config drift and
        # should be treated as a server-side misconfiguration, not as
        # an auth failure attributable to the caller.
        current_app.logger.error(
            "Supabase bridge invoked with SUPABASE_AUTH_ENABLED=False "
            "while SUPABASE_NEW_USER_SIGNUP_ENABLED=True (config drift)"
        )
        return jsonify({
            "ok": False,
            "error": "supabase_auth_unavailable",
            "message": "Supabase Auth verification is not currently enabled.",
        }), 503
    except BridgeTokenInvalid as exc:
        return jsonify({
            "ok": False,
            "error": "invalid_token",
            "message": str(exc),
        }), 401
    except ExistingLegacyUserConflict as exc:
        return jsonify({
            "ok": False,
            "error": "existing_legacy_account",
            "existing_user_id_hint": exc.app_user_id,
            "message": (
                "An existing Soletrak account exists for this email. "
                "Please sign in with your existing username."
            ),
        }), 409
    except BridgeProfileInvalid as exc:
        return jsonify({
            "ok": False,
            "error": "invalid_profile",
            "field_errors": exc.field_errors,
            "message": "Profile payload was invalid.",
        }), 400

    return jsonify({
        "ok": True,
        "user_id": outcome.app_user.id,
        "was_created": outcome.was_created,
        "source": outcome.source,
        "redirect": "/dashboard",
    }), 200
