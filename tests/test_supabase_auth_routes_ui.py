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
    assert config["endpoints"]["dashboard"] == "/dashboard"


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
