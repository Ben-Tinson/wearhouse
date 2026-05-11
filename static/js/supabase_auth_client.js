/*
 * Supabase Auth client glue — Phase 3a PR 4.
 *
 * Shared browser-side helper for the four Phase 3a Supabase Auth pages:
 *
 *   - /auth/supabase/signup          → initSignup()
 *   - /auth/supabase/confirm         → initConfirm()
 *   - /auth/supabase/oauth-callback  → initOAuthCallback()
 *   - /auth/supabase/onboarding      → initOnboarding()
 *
 * Each template invokes the appropriate ``init*`` function after the
 * page has loaded. The handlers drive the Supabase JS SDK
 * (signUp / signInWithPassword / signInWithOAuth / getSession) and POST
 * the resulting access token to the JSON bridge endpoint
 * ``/auth/supabase/bridge``. The bridge is the **only** path that
 * creates app User rows; this client never writes server-side state
 * directly.
 *
 * Configuration is injected into each page as a JSON blob with id
 * ``supabase-auth-config``. Sensitive values (service role key, JWT
 * secret) are never included; the blob carries only the project URL,
 * publishable anon key, configured SSO providers, and the bridge /
 * page endpoint URLs.
 */
(function () {
    "use strict";

    var STORAGE_TOKEN_KEY = "soletrak_supabase_access_token";
    var STORAGE_EMAIL_KEY = "soletrak_supabase_email";

    function readConfig() {
        var node = document.getElementById("supabase-auth-config");
        if (!node) {
            return null;
        }
        try {
            return JSON.parse(node.textContent || "{}");
        } catch (err) {
            console.error("[supabase-auth] failed to parse config blob", err);
            return null;
        }
    }

    function getSupabaseClient(config) {
        if (!window.supabase || typeof window.supabase.createClient !== "function") {
            console.error("[supabase-auth] Supabase JS SDK not loaded");
            return null;
        }
        if (!config || !config.supabase_url || !config.supabase_anon_key) {
            console.error("[supabase-auth] Supabase config missing url/anon_key");
            return null;
        }
        return window.supabase.createClient(
            config.supabase_url,
            config.supabase_anon_key,
            {
                auth: {
                    persistSession: true,
                    autoRefreshToken: true,
                    detectSessionInUrl: true
                }
            }
        );
    }

    function setStatus(node, kind, message) {
        if (!node) {
            return;
        }
        node.hidden = false;
        node.className = "alert alert-" + (kind === "error" ? "danger" : kind);
        node.textContent = message;
    }

    function showError(message) {
        var node = document.querySelector("[data-supabase-error]");
        if (node) {
            node.classList.remove("d-none");
            node.textContent = message;
        }
    }

    function readProfileFromForm(form) {
        return {
            username: (form.username && form.username.value || "").trim(),
            first_name: (form.first_name && form.first_name.value || "").trim(),
            last_name: (form.last_name && form.last_name.value || "").trim(),
            preferred_region: (form.preferred_region && form.preferred_region.value || "").trim().toUpperCase(),
            marketing_opt_in: !!(form.marketing_opt_in && form.marketing_opt_in.checked)
        };
    }

    function postToBridge(config, accessToken, profile) {
        return fetch(config.endpoints.bridge, {
            method: "POST",
            credentials: "same-origin",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                access_token: accessToken,
                profile: profile || null
            })
        }).then(function (response) {
            return response.json().then(function (body) {
                return {status: response.status, body: body};
            }).catch(function () {
                return {status: response.status, body: null};
            });
        });
    }

    function dashboardUrl(config, body) {
        if (body && body.redirect) {
            return body.redirect;
        }
        return (config.endpoints && config.endpoints.dashboard) || "/dashboard";
    }

    // ---------------------------------------------------------------
    // /signup
    // ---------------------------------------------------------------
    function initSignup() {
        var config = readConfig();
        var client = getSupabaseClient(config);
        if (!client || !config) {
            return;
        }

        var form = document.querySelector("[data-supabase-signup-form]");
        var status = document.querySelector("[data-supabase-status]");

        if (form) {
            form.addEventListener("submit", function (event) {
                event.preventDefault();
                var profile = readProfileFromForm(form);
                var email = (form.email.value || "").trim();
                var password = form.password.value || "";

                if (!email || !password) {
                    setStatus(status, "error", "Email and password are required.");
                    return;
                }

                setStatus(status, "info", "Sending you a confirmation email…");

                var emailRedirectTo = (window.location.origin || "")
                    + config.endpoints.confirm;

                client.auth.signUp({
                    email: email,
                    password: password,
                    options: {
                        data: profile,
                        emailRedirectTo: emailRedirectTo
                    }
                }).then(function (result) {
                    if (result.error) {
                        setStatus(status, "error", result.error.message || "Sign-up failed.");
                        return;
                    }
                    // Stash profile + email for the confirm-page handler to use
                    // if Supabase user_metadata is unavailable on the JWT.
                    try {
                        window.sessionStorage.setItem(
                            "soletrak_supabase_pending_profile",
                            JSON.stringify(profile)
                        );
                        window.sessionStorage.setItem(STORAGE_EMAIL_KEY, email);
                    } catch (err) {
                        /* sessionStorage disabled — ignore */
                    }
                    setStatus(
                        status,
                        "success",
                        "Check your inbox for a confirmation email from Supabase to activate your account."
                    );
                }).catch(function (err) {
                    setStatus(status, "error", (err && err.message) || "Sign-up failed.");
                });
            });
        }

        // SSO buttons
        var ssoButtons = document.querySelectorAll("[data-supabase-sso]");
        Array.prototype.forEach.call(ssoButtons, function (button) {
            button.addEventListener("click", function () {
                var provider = button.getAttribute("data-supabase-sso");
                var redirectTo = (config.bridge_redirect_url
                    || (window.location.origin + config.endpoints.oauth_callback));
                client.auth.signInWithOAuth({
                    provider: provider,
                    options: {redirectTo: redirectTo}
                }).catch(function (err) {
                    setStatus(status, "error", (err && err.message) || "SSO sign-in failed.");
                });
            });
        });
    }

    // ---------------------------------------------------------------
    // /confirm — email/password confirmation landing
    // ---------------------------------------------------------------
    function initConfirm() {
        var config = readConfig();
        var client = getSupabaseClient(config);
        if (!client || !config) {
            showError("Supabase client unavailable. Please try again.");
            return;
        }

        // Supabase JS with detectSessionInUrl=true parses the URL fragment
        // automatically. ``getSession`` then returns the active session.
        client.auth.getSession().then(function (result) {
            var session = result && result.data && result.data.session;
            if (!session || !session.access_token) {
                showError("No active session found. Please return to sign up and try again.");
                return;
            }

            // Profile fields were stashed in user_metadata at signUp; we
            // also keep a sessionStorage copy as a fallback for cases
            // where Supabase strips metadata.
            var profile = null;
            var user = session.user || {};
            var metadata = user.user_metadata || {};
            if (metadata && metadata.username) {
                profile = {
                    username: metadata.username,
                    first_name: metadata.first_name,
                    last_name: metadata.last_name,
                    preferred_region: metadata.preferred_region,
                    marketing_opt_in: !!metadata.marketing_opt_in
                };
            } else {
                try {
                    var raw = window.sessionStorage.getItem(
                        "soletrak_supabase_pending_profile"
                    );
                    if (raw) {
                        profile = JSON.parse(raw);
                    }
                } catch (err) { /* ignore */ }
            }

            postToBridge(config, session.access_token, profile).then(function (out) {
                if (out.status === 200 && out.body && out.body.ok) {
                    try {
                        window.sessionStorage.removeItem(
                            "soletrak_supabase_pending_profile"
                        );
                    } catch (err) { /* ignore */ }
                    window.location.assign(dashboardUrl(config, out.body));
                } else {
                    var msg = (out.body && (out.body.message || out.body.error))
                        || ("Bridge returned " + out.status);
                    showError(msg);
                }
            }).catch(function (err) {
                showError((err && err.message) || "Bridge call failed.");
            });
        }).catch(function (err) {
            showError((err && err.message) || "Could not read Supabase session.");
        });
    }

    // ---------------------------------------------------------------
    // /oauth-callback — OAuth landing
    // ---------------------------------------------------------------
    function initOAuthCallback() {
        var config = readConfig();
        var client = getSupabaseClient(config);
        if (!client || !config) {
            showError("Supabase client unavailable. Please try again.");
            return;
        }

        client.auth.getSession().then(function (result) {
            var session = result && result.data && result.data.session;
            if (!session || !session.access_token) {
                showError("No active session found. Please return to sign up and try again.");
                return;
            }

            // Try the bridge with no profile first. For returning OAuth
            // users the bridge resolves by supabase_auth_user_id and
            // returns 200. For first-time OAuth users the bridge returns
            // 400 invalid_profile, at which point we redirect to
            // onboarding with the token + email stashed for re-use.
            postToBridge(config, session.access_token, null).then(function (out) {
                if (out.status === 200 && out.body && out.body.ok) {
                    window.location.assign(dashboardUrl(config, out.body));
                    return;
                }
                if (out.status === 400
                    && out.body
                    && out.body.error === "invalid_profile") {
                    try {
                        window.sessionStorage.setItem(
                            STORAGE_TOKEN_KEY,
                            session.access_token
                        );
                        window.sessionStorage.setItem(
                            STORAGE_EMAIL_KEY,
                            (session.user && session.user.email) || ""
                        );
                    } catch (err) { /* ignore */ }
                    window.location.assign(config.endpoints.onboarding);
                    return;
                }
                if (out.status === 409
                    && out.body
                    && out.body.error === "existing_legacy_account") {
                    showError(
                        "An existing Soletrak account exists for this email. "
                        + "Please sign in with your existing username instead."
                    );
                    return;
                }
                var msg = (out.body && (out.body.message || out.body.error))
                    || ("Bridge returned " + out.status);
                showError(msg);
            }).catch(function (err) {
                showError((err && err.message) || "Bridge call failed.");
            });
        }).catch(function (err) {
            showError((err && err.message) || "Could not read Supabase session.");
        });
    }

    // ---------------------------------------------------------------
    // /onboarding — first-time OAuth profile completion
    // ---------------------------------------------------------------
    function initOnboarding() {
        var config = readConfig();
        if (!config) {
            return;
        }

        var form = document.querySelector("[data-supabase-onboarding-form]");
        var status = document.querySelector("[data-supabase-status]");
        var emailField = document.querySelector("[data-supabase-email-readonly]");

        // Pre-fill the read-only email from sessionStorage.
        try {
            var email = window.sessionStorage.getItem(STORAGE_EMAIL_KEY) || "";
            if (emailField) {
                emailField.value = email;
            }
        } catch (err) { /* ignore */ }

        if (!form) {
            return;
        }

        form.addEventListener("submit", function (event) {
            event.preventDefault();
            var profile = readProfileFromForm(form);
            var token = null;
            try {
                token = window.sessionStorage.getItem(STORAGE_TOKEN_KEY);
            } catch (err) { /* ignore */ }
            if (!token) {
                setStatus(
                    status,
                    "error",
                    "Your sign-in session has expired. Please start sign-up again."
                );
                return;
            }
            setStatus(status, "info", "Finishing your account setup…");
            postToBridge(config, token, profile).then(function (out) {
                if (out.status === 200 && out.body && out.body.ok) {
                    try {
                        window.sessionStorage.removeItem(STORAGE_TOKEN_KEY);
                        window.sessionStorage.removeItem(STORAGE_EMAIL_KEY);
                    } catch (err) { /* ignore */ }
                    window.location.assign(dashboardUrl(config, out.body));
                    return;
                }
                if (out.status === 400
                    && out.body
                    && out.body.field_errors) {
                    var parts = [];
                    for (var key in out.body.field_errors) {
                        if (Object.prototype.hasOwnProperty.call(
                            out.body.field_errors, key
                        )) {
                            parts.push(key + ": " + out.body.field_errors[key]);
                        }
                    }
                    setStatus(status, "error", parts.join("; ") || "Profile invalid.");
                    return;
                }
                var msg = (out.body && (out.body.message || out.body.error))
                    || ("Bridge returned " + out.status);
                setStatus(status, "error", msg);
            }).catch(function (err) {
                setStatus(status, "error", (err && err.message) || "Bridge call failed.");
            });
        });
    }

    window.SoletrakSupabaseAuth = {
        initSignup: initSignup,
        initConfirm: initConfirm,
        initOAuthCallback: initOAuthCallback,
        initOnboarding: initOnboarding
    };
})();
