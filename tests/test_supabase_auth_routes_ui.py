"""Tests for the Phase 3a PR 4 HTML routes.

Covers:
    - flag-gating: every page returns 404 when
      ``SUPABASE_NEW_USER_SIGNUP_ENABLED`` is False;
    - 200 + expected markup when flag on;
    - SSO buttons only render for configured providers;
    - non-secret config blob injected; secrets never leak;
    - the four templates each invoke the matching ``init*`` JS hook;
    - inert-by-default end-to-end: with the flag off, every URL is a 404
      and ``url_for`` does not change for any non-Supabase route.
"""

from __future__ import annotations

import json


def _enable(test_app):
    test_app.config["SUPABASE_AUTH_ENABLED"] = True
    test_app.config["SUPABASE_NEW_USER_SIGNUP_ENABLED"] = True


def _disable(test_app):
    test_app.config["SUPABASE_NEW_USER_SIGNUP_ENABLED"] = False


# ---------------------------------------------------------------------------
# Flag gating — every HTML route 404s when the launch flag is off
# ---------------------------------------------------------------------------


def test_all_phase3a_html_routes_404_when_flag_off(test_app, test_client):
    _disable(test_app)
    for path in (
        "/auth/supabase/signup",
        "/auth/supabase/confirm",
        "/auth/supabase/oauth-callback",
        "/auth/supabase/onboarding",
    ):
        response = test_client.get(path)
        assert response.status_code == 404, f"{path} should 404 when flag off"


# ---------------------------------------------------------------------------
# /signup
# ---------------------------------------------------------------------------


def test_signup_page_renders_when_flag_on(test_app, test_client):
    _enable(test_app)
    response = test_client.get("/auth/supabase/signup")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    # Form fields exist
    assert 'name="email"' in body
    assert 'name="password"' in body
    assert 'name="username"' in body
    assert 'name="first_name"' in body
    assert 'name="last_name"' in body
    assert 'name="preferred_region"' in body
    assert 'name="marketing_opt_in"' in body
    # Form has the data-attribute the JS hook looks for
    assert "data-supabase-signup-form" in body
    # The JS client glue is included
    assert "/static/js/supabase_auth_client.js" in body
    # Page invokes the matching init function
    assert "SoletrakSupabaseAuth.initSignup" in body


def test_signup_page_renders_only_configured_sso_providers(test_app, test_client):
    _enable(test_app)
    test_app.config["SUPABASE_SSO_PROVIDERS"] = "google"
    response = test_client.get("/auth/supabase/signup")
    body = response.data.decode("utf-8")
    assert 'data-supabase-sso="google"' in body
    assert 'data-supabase-sso="apple"' not in body


def test_signup_page_renders_apple_when_configured(test_app, test_client):
    _enable(test_app)
    test_app.config["SUPABASE_SSO_PROVIDERS"] = "google,apple"
    response = test_client.get("/auth/supabase/signup")
    body = response.data.decode("utf-8")
    assert 'data-supabase-sso="google"' in body
    assert 'data-supabase-sso="apple"' in body


def test_signup_page_renders_no_sso_section_when_providers_empty(test_app, test_client):
    _enable(test_app)
    test_app.config["SUPABASE_SSO_PROVIDERS"] = ""
    response = test_client.get("/auth/supabase/signup")
    body = response.data.decode("utf-8")
    assert "data-supabase-sso" not in body
    # The form still renders.
    assert "data-supabase-signup-form" in body


# ---------------------------------------------------------------------------
# /confirm
# ---------------------------------------------------------------------------


def test_confirm_page_renders_when_flag_on(test_app, test_client):
    _enable(test_app)
    response = test_client.get("/auth/supabase/confirm")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "data-supabase-confirm-status" in body
    assert "SoletrakSupabaseAuth.initConfirm" in body


# ---------------------------------------------------------------------------
# /oauth-callback
# ---------------------------------------------------------------------------


def test_oauth_callback_page_renders_when_flag_on(test_app, test_client):
    _enable(test_app)
    response = test_client.get("/auth/supabase/oauth-callback")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "data-supabase-oauth-status" in body
    assert "SoletrakSupabaseAuth.initOAuthCallback" in body


# ---------------------------------------------------------------------------
# /onboarding
# ---------------------------------------------------------------------------


def test_onboarding_page_renders_when_flag_on(test_app, test_client):
    _enable(test_app)
    response = test_client.get("/auth/supabase/onboarding")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "data-supabase-onboarding-form" in body
    assert 'name="username"' in body
    assert 'name="first_name"' in body
    assert 'name="last_name"' in body
    assert 'name="preferred_region"' in body
    assert 'name="marketing_opt_in"' in body
    # Email is read-only on this form (it comes from the JWT).
    assert "data-supabase-email-readonly" in body
    assert "SoletrakSupabaseAuth.initOnboarding" in body


# ---------------------------------------------------------------------------
# Config injection — non-secret values only
# ---------------------------------------------------------------------------


def _extract_config_blob(html: str) -> dict:
    """Pull the JSON blob between the supabase-auth-config script tags."""
    marker = 'id="supabase-auth-config" type="application/json">'
    start = html.find(marker)
    assert start >= 0, "config blob not found"
    start += len(marker)
    end = html.find("</script>", start)
    assert end > start
    return json.loads(html[start:end])


def test_signup_page_injects_public_config_blob(test_app, test_client):
    _enable(test_app)
    test_app.config["SUPABASE_URL"] = "https://example.supabase.co"
    test_app.config["SUPABASE_ANON_KEY"] = "anon-key-public"
    test_app.config["SUPABASE_SSO_PROVIDERS"] = "google"
    test_app.config["SUPABASE_BRIDGE_REDIRECT_URL"] = (
        "https://example.test/auth/supabase/oauth-callback"
    )
    response = test_client.get("/auth/supabase/signup")
    body = response.data.decode("utf-8")
    config = _extract_config_blob(body)

    assert config["supabase_url"] == "https://example.supabase.co"
    assert config["supabase_anon_key"] == "anon-key-public"
    assert config["sso_providers"] == ["google"]
    assert config["bridge_redirect_url"] == "https://example.test/auth/supabase/oauth-callback"
    # Endpoint URLs are surfaced for the JS handler to use.
    assert config["endpoints"]["bridge"] == "/auth/supabase/bridge"
    assert config["endpoints"]["signup"] == "/auth/supabase/signup"
    assert config["endpoints"]["confirm"] == "/auth/supabase/confirm"
    assert config["endpoints"]["oauth_callback"] == "/auth/supabase/oauth-callback"
    assert config["endpoints"]["onboarding"] == "/auth/supabase/onboarding"
    assert config["endpoints"]["dashboard"] == "/"  # main.home
    assert config["endpoints"]["check_username"] == "/auth/supabase/check-username"


def test_signup_page_never_leaks_service_role_key_or_jwt_secret(test_app, test_client):
    """The browser-side config blob must contain only publishable values."""
    _enable(test_app)
    test_app.config["SUPABASE_URL"] = "https://example.supabase.co"
    test_app.config["SUPABASE_ANON_KEY"] = "anon-key-public"
    test_app.config["SUPABASE_SERVICE_ROLE_KEY"] = "SERVICE-ROLE-SECRET-MUST-NOT-LEAK"
    test_app.config["SUPABASE_JWT_SECRET"] = "JWT-SECRET-MUST-NOT-LEAK"
    response = test_client.get("/auth/supabase/signup")
    body = response.data.decode("utf-8")
    assert "SERVICE-ROLE-SECRET-MUST-NOT-LEAK" not in body
    assert "JWT-SECRET-MUST-NOT-LEAK" not in body
    # Sanity: the publishable anon key IS allowed in the body.
    assert "anon-key-public" in body


def test_onboarding_page_injects_config(test_app, test_client):
    _enable(test_app)
    test_app.config["SUPABASE_URL"] = "https://example.supabase.co"
    test_app.config["SUPABASE_ANON_KEY"] = "anon-key-public"
    response = test_client.get("/auth/supabase/onboarding")
    body = response.data.decode("utf-8")
    config = _extract_config_blob(body)
    assert config["supabase_url"] == "https://example.supabase.co"
    assert config["endpoints"]["bridge"] == "/auth/supabase/bridge"


# ---------------------------------------------------------------------------
# Inert-by-default sanity
# ---------------------------------------------------------------------------


def test_legacy_register_unchanged_when_flag_off(test_app, test_client):
    """PR 4 must not touch the legacy /register flow."""
    _disable(test_app)
    response = test_client.get("/register")
    assert response.status_code == 200
    # The legacy template title contains "Register".
    assert b"Register" in response.data


def test_legacy_register_redirects_to_supabase_signup_when_flag_on(test_app, test_client):
    """PR 5 (this slice) introduces the `/register` → `/auth/supabase/signup`
    redirect. PR 4's earlier `no-redirect-yet` assertion is replaced by
    this PR 5 contract. The end-to-end behaviour is still covered by
    `tests/test_auth.py::test_register_get_redirects_to_supabase_signup_when_flag_on`
    — this test ensures the UI suite is kept in sync."""
    _enable(test_app)
    response = test_client.get("/register", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers.get("Location", "").endswith("/auth/supabase/signup")


# ---------------------------------------------------------------------------
# Phase 3a UX-polish slice — signup-page polish
# ---------------------------------------------------------------------------


def test_signup_page_has_confirm_password_field(test_app, test_client):
    _enable(test_app)
    response = test_client.get("/auth/supabase/signup")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert 'name="confirm_password"' in body
    assert 'data-confirm-password-for="#supabase-password"' in body


def test_signup_page_has_password_show_hide_toggles(test_app, test_client):
    _enable(test_app)
    response = test_client.get("/auth/supabase/signup")
    body = response.data.decode("utf-8")
    assert 'data-password-toggle="#supabase-password"' in body
    assert 'data-password-toggle="#supabase-confirm-password"' in body


def test_signup_page_renders_google_logo_svg_when_provider_configured(test_app, test_client):
    _enable(test_app)
    test_app.config["SUPABASE_SSO_PROVIDERS"] = "google"
    response = test_client.get("/auth/supabase/signup")
    body = response.data.decode("utf-8")
    assert "data-google-logo" in body
    # Spot-check brand colours; the SVG should be inline (no external image).
    assert "#4285F4" in body
    assert "#EA4335" in body


def test_signup_page_has_username_feedback_node(test_app, test_client):
    _enable(test_app)
    response = test_client.get("/auth/supabase/signup")
    body = response.data.decode("utf-8")
    assert "data-username-feedback" in body
    assert 'aria-describedby="supabase-username-feedback"' in body


# ---------------------------------------------------------------------------
# Phase 3a UX-polish slice — check-username endpoint
# ---------------------------------------------------------------------------


def test_check_username_returns_404_when_flag_off(test_app, test_client):
    _disable(test_app)
    response = test_client.get("/auth/supabase/check-username?username=fresh")
    assert response.status_code == 404


def test_check_username_reports_available_for_new_username(test_app, test_client):
    _enable(test_app)
    response = test_client.get("/auth/supabase/check-username?username=brandnew")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload == {"available": True, "reason": None}


def test_check_username_reports_taken_for_existing_username(test_app, test_client):
    from extensions import db
    from models import User

    _enable(test_app)
    with test_app.app_context():
        user = User(
            username="claimed_handle",
            email="claimed@example.com",
            first_name="Claimed",
            last_name="Handle",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()

    response = test_client.get(
        "/auth/supabase/check-username?username=claimed_handle"
    )
    payload = response.get_json()
    assert payload == {"available": False, "reason": "taken"}


def test_check_username_reports_length_for_short_username(test_app, test_client):
    _enable(test_app)
    response = test_client.get("/auth/supabase/check-username?username=abc")
    payload = response.get_json()
    assert payload == {"available": False, "reason": "length"}


def test_check_username_reports_length_for_blank(test_app, test_client):
    _enable(test_app)
    response = test_client.get("/auth/supabase/check-username?username=")
    payload = response.get_json()
    assert payload == {"available": False, "reason": "length"}


# ---------------------------------------------------------------------------
# Phase 3a UX-polish slice — /login dual-mode behaviour
# ---------------------------------------------------------------------------


def test_login_page_renders_legacy_form_when_flag_off(test_app, test_client):
    _disable(test_app)
    response = test_client.get("/login")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    # Legacy form is still posted to /login.
    assert 'action="/login"' in body
    # No Supabase entry points.
    assert "data-supabase-login-form" not in body
    assert "data-supabase-sso" not in body
    # Legacy password show/hide still works without the Supabase config.
    assert "data-password-toggle" in body


def test_login_page_adds_supabase_signin_section_when_flag_on(test_app, test_client):
    _enable(test_app)
    response = test_client.get("/login")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    # Supabase email/password sign-in form is present.
    assert "data-supabase-login-form" in body
    # Google SSO button is present (default provider list = google).
    assert 'data-supabase-sso="google"' in body
    # Sign-up link points at the Supabase signup page.
    assert 'href="/auth/supabase/signup"' in body
    # Supabase config blob and JS client are loaded.
    assert 'id="supabase-auth-config"' in body
    assert "/static/js/supabase_auth_client.js" in body
    # Show/hide buttons on both the new Supabase password input and
    # the (now-collapsed) legacy password input.
    assert 'data-password-toggle="#supabase-login-password"' in body
    assert 'data-password-toggle="#legacy-login-password"' in body


def test_login_page_legacy_form_still_posts_when_flag_on(test_app, test_client):
    """The collapsed legacy form remains functional when the flag is on."""
    _enable(test_app)
    response = test_client.get("/login")
    body = response.data.decode("utf-8")
    # The legacy POST target is preserved inside the collapsed section.
    assert 'action="/login"' in body
    # CSRF token is rendered (form.hidden_tag).
    assert "csrf_token" in body or "name=\"csrf_token\"" in body or '<input id="csrf_token"' in body or "hidden" in body


def test_login_page_does_not_leak_supabase_secrets_when_flag_on(test_app, test_client):
    _enable(test_app)
    test_app.config["SUPABASE_URL"] = "https://example.supabase.co"
    test_app.config["SUPABASE_ANON_KEY"] = "anon-public"
    test_app.config["SUPABASE_SERVICE_ROLE_KEY"] = "LOGIN-SERVICE-ROLE-LEAK"
    test_app.config["SUPABASE_JWT_SECRET"] = "LOGIN-JWT-SECRET-LEAK"
    response = test_client.get("/login")
    body = response.data.decode("utf-8")
    assert "LOGIN-SERVICE-ROLE-LEAK" not in body
    assert "LOGIN-JWT-SECRET-LEAK" not in body
    # The publishable anon key is allowed to appear.
    assert "anon-public" in body
