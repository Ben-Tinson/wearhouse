# decorators.py
from functools import wraps
from flask import abort
from flask_login import current_user

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