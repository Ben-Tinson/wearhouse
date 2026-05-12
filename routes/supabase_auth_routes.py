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

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    render_template,
    request,
    url_for,
)
from flask_login import login_user

from sqlalchemy import func, or_

from extensions import csrf
from models import User
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

    # The post-success redirect must point at a route that actually
    # exists in this app. The legacy login path lands users at
    # ``main.home``, so Phase 3a does the same for parity. Callers may
    # override on the client by reading a ``?next=...`` query parameter.
    return jsonify({
        "ok": True,
        "user_id": outcome.app_user.id,
        "was_created": outcome.was_created,
        "source": outcome.source,
        "redirect": url_for("main.home"),
    }), 200


@supabase_auth_bp.route("/check-username", methods=["GET"])
def check_username():
    """Lightweight availability check used by the signup page's JS to
    catch duplicate usernames before driving Supabase to create an
    identity that would only fail at bridge time.

    Response::

        {"available": true|false, "reason": "length"|"taken"|null}

    Reveals only whether the specific queried username is free — the
    legacy ``/register`` form already exhibits the same property by
    flashing on POST. Rate-limiting is a separate Phase 4 concern.
    """
    _flag_or_404()
    raw = (request.args.get("username") or "").strip()
    if len(raw) < 4 or len(raw) > 80:
        return jsonify({"available": False, "reason": "length"}), 200
    existing = User.query.filter_by(username=raw).first()
    if existing is not None:
        return jsonify({"available": False, "reason": "taken"}), 200
    return jsonify({"available": True, "reason": None}), 200


@supabase_auth_bp.route("/check-email", methods=["GET"])
def check_email():
    """Lightweight availability check used by the signup page's JS to
    surface email collisions earlier than bridge time.

    Response::

        {"available": true|false, "reason": "format"|"in_use"|null}

    The ``in_use`` reason is **intentionally non-specific**: it covers
    legacy users with this email, linked existing users, Supabase-first
    users created via the bridge, and in-flight legacy email changes
    (``User.pending_email``) — all conflated into one answer so a
    probing caller cannot distinguish those states. The signup page's
    JS translates the reason into safer copy:
    *"This email can't be used for a new account. Try signing in or
    use another email."*

    Email enumeration risk note: this endpoint can confirm whether a
    specific guessed email exists in Soletrak's app `user` table. The
    legacy ``/register`` form has the same property (POST returns the
    same flash for a taken email). Rate-limiting on the entire Phase 3a
    Supabase Auth blueprint is a separate Phase 4 concern noted in
    ``docs/SUPABASE_AUTH_PHASE3_IMPLEMENTATION_PLAN.md`` §15.16.
    """
    _flag_or_404()
    raw = (request.args.get("email") or "").strip()
    if not raw or "@" not in raw or len(raw) > 320:
        return jsonify({"available": False, "reason": "format"}), 200
    normalised = raw.lower()
    existing = (
        User.query.filter(
            or_(
                func.lower(User.email) == normalised,
                func.lower(User.pending_email) == normalised,
            )
        )
        .first()
    )
    if existing is not None:
        return jsonify({"available": False, "reason": "in_use"}), 200
    return jsonify({"available": True, "reason": None}), 200


# ---------------------------------------------------------------------------
# Browser-facing HTML routes — Phase 3a PR 4
#
# These pages serve the new-user Supabase Auth UX. They all 404 when
# SUPABASE_NEW_USER_SIGNUP_ENABLED is False. They render Supabase JS
# SDK-driven pages whose only server-side write path is back through
# the JSON bridge endpoint above (POST /bridge). The HTML routes
# themselves create no DB rows.
#
# The non-secret config (Supabase project URL, anon key, configured SSO
# providers, bridge endpoint URL) is injected into each page as a small
# JSON blob the browser-side ``supabase_auth_client.js`` consumes. The
# service role key and JWT secret are server-only and never reach a
# template.
# ---------------------------------------------------------------------------


def _public_client_config() -> dict:
    """Build the browser-safe config blob for the Supabase JS SDK.

    Includes only values that are intended to be visible to the user
    agent: the Supabase project URL, the publishable anon key, the list
    of configured SSO providers, the configured browser-side bridge
    redirect URL, and the absolute URLs of our four Phase 3a HTML
    endpoints. Sensitive values (service role key, JWT secret) are
    never included.
    """
    raw_providers = (current_app.config.get("SUPABASE_SSO_PROVIDERS") or "").strip()
    providers = [p.strip().lower() for p in raw_providers.split(",") if p.strip()]
    return {
        "supabase_url": current_app.config.get("SUPABASE_URL") or "",
        "supabase_anon_key": current_app.config.get("SUPABASE_ANON_KEY") or "",
        "sso_providers": providers,
        "bridge_redirect_url": current_app.config.get("SUPABASE_BRIDGE_REDIRECT_URL") or "",
        "endpoints": {
            "bridge": url_for("supabase_auth.bridge"),
            "signup": url_for("supabase_auth.supabase_signup"),
            "confirm": url_for("supabase_auth.supabase_confirm"),
            "oauth_callback": url_for("supabase_auth.supabase_oauth_callback"),
            "onboarding": url_for("supabase_auth.supabase_onboarding"),
            "check_username": url_for("supabase_auth.check_username"),
            "check_email": url_for("supabase_auth.check_email"),
            "dashboard": url_for("main.home"),
        },
    }


@supabase_auth_bp.route("/signup", methods=["GET"])
def supabase_signup():
    """New-user signup entry point.

    Renders the email/password form plus one button per configured SSO
    provider. The page itself performs no server-side authentication or
    user creation; the Supabase JS SDK drives the email/password sign-up
    or OAuth handoff, and the resulting access token is POSTed to
    ``/auth/supabase/bridge`` (the JSON endpoint above).
    """
    _flag_or_404()
    return render_template(
        "auth/supabase_signup.html",
        title="Sign up",
        supabase_config=_public_client_config(),
    )


@supabase_auth_bp.route("/confirm", methods=["GET"])
def supabase_confirm():
    """Landing page for email/password confirmation links.

    Supabase sends users here from the confirmation email with the
    access token in the URL fragment. The Supabase JS SDK picks up the
    token, reads the profile fields stashed in user_metadata at signup,
    and POSTs them to the bridge endpoint. No server-side state is
    written by this route — it is a pure render plus a JS handler.
    """
    _flag_or_404()
    return render_template(
        "auth/supabase_confirm.html",
        title="Confirming your email",
        supabase_config=_public_client_config(),
    )


@supabase_auth_bp.route("/oauth-callback", methods=["GET"])
def supabase_oauth_callback():
    """Landing page for Supabase OAuth (Google etc.) callbacks.

    Supabase brokers the OAuth flow and redirects here with the access
    token in the URL fragment. The JS handler POSTs to the bridge
    endpoint **without** a profile payload first; if the bridge replies
    400 invalid_profile (i.e. this is a first-time OAuth user) it
    stashes the token in sessionStorage and redirects to the onboarding
    page.
    """
    _flag_or_404()
    return render_template(
        "auth/supabase_oauth_callback.html",
        title="Completing sign-in",
        supabase_config=_public_client_config(),
    )


@supabase_auth_bp.route("/onboarding", methods=["GET"])
def supabase_onboarding():
    """Profile-completion form for first-time OAuth sign-ups.

    Email is pre-filled (read-only) from the JWT once the JS handler
    extracts it. Submission POSTs to the bridge endpoint with the
    access token (recovered from sessionStorage) plus the collected
    profile fields.
    """
    _flag_or_404()
    return render_template(
        "auth/supabase_oauth_onboarding.html",
        title="Complete your profile",
        supabase_config=_public_client_config(),
    )
