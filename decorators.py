# decorators.py
from functools import wraps
from flask import abort, g, request
from flask_login import current_user

from models import UserApiToken
from services.api_tokens import hash_token
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
