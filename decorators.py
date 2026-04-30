# decorators.py
from functools import wraps
from flask import abort, g, request
from flask_login import current_user

from models import UserApiToken
from services.api_tokens import hash_token
from services.supabase_auth_linkage import find_app_user_by_supabase_id
from services.supabase_auth_service import (
    SupabaseAuthError,
    is_enabled as supabase_auth_is_enabled,
    looks_like_jwt,
    verify_access_token,
)
from extensions import db

def admin_required(f):
    """
    A decorator to ensure a user is logged in AND is an administrator.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check if user is authenticated and is an admin
        if not current_user.is_authenticated or not current_user.is_admin:
            # If not, return a 403 Forbidden error
            abort(403)
        # Otherwise, proceed with the original route function
        return f(*args, **kwargs)
    return decorated_function


def bearer_or_login_required(scope: str = None):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            auth_header = request.headers.get("Authorization", "")
            if auth_header.lower().startswith("bearer "):
                token_value = auth_header.split(None, 1)[1].strip()

                # Phase 2 format-disambiguation (per docs/DECISIONS.md):
                # exactly two '.' separators ⇒ candidate Supabase JWT
                # path; everything else ⇒ existing UserApiToken path
                # (byte-for-byte unchanged). The JWT branch is gated by
                # SUPABASE_AUTH_ENABLED so an accidentally-presented JWT
                # cannot authorise traffic when the flag is off.
                if looks_like_jwt(token_value):
                    if not supabase_auth_is_enabled():
                        abort(401)
                    try:
                        claims = verify_access_token(token_value)
                    except SupabaseAuthError:
                        abort(401)
                    linked = find_app_user_by_supabase_id(claims.supabase_user_id)
                    if linked is None:
                        # No auto-linking. An unlinked Supabase identity
                        # is rejected outright; explicit linkage tooling
                        # is the only sanctioned writer.
                        abort(401)
                    # Verified JWT for a linked app user. Treated as
                    # session-equivalent: no scope check (consistent
                    # with the Flask-Login fallback below) and no DB
                    # writes (per the accepted decision: the JWT branch
                    # must perform pure verification only).
                    g.api_user = linked
                    g.api_token = None
                    return f(*args, **kwargs)

                # Opaque bearer ⇒ UserApiToken path (unchanged).
                token_hash = hash_token(token_value)
                token = (
                    UserApiToken.query.filter_by(token_hash=token_hash)
                    .filter(UserApiToken.revoked_at.is_(None))
                    .first()
                )
                if not token:
                    abort(401)
                if scope:
                    scopes = {s.strip() for s in (token.scopes or "").split(",") if s.strip()}
                    if scope not in scopes:
                        abort(403)
                token.last_used_at = db.func.now()
                db.session.commit()
                g.api_user = token.user
                g.api_token = token
                return f(*args, **kwargs)

            if not current_user.is_authenticated:
                abort(401)
            g.api_user = current_user
            g.api_token = None
            return f(*args, **kwargs)

        return decorated_function

    return decorator
