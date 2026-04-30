# config.py
import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))

class Config:
    """Base config."""
    # Security and App Keys
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'a_default_secret_key_for_development'
    
    # Database Configuration
    DATABASE_URL = os.environ.get('DATABASE_URL')
    if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

    SQLALCHEMY_DATABASE_URI = DATABASE_URL or 'sqlite:///' + os.path.join(basedir, 'instance', 'site.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # File Upload Configuration
    UPLOAD_FOLDER = os.path.join(basedir, 'uploads')
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'avif', 'webp'}

    # Email Configuration (using SendGrid API)
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER')
    SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY')
    APP_BASE_URL = os.environ.get('APP_BASE_URL')
    PREFERRED_URL_SCHEME = os.environ.get('PREFERRED_URL_SCHEME', 'http')
    
    # External API Keys
    RETAILED_API_KEY = os.environ.get('RETAILED_API_KEY')
    RAPIDAPI_KEY = os.environ.get('RAPIDAPI_KEY')
    KICKS_API_KEY = os.environ.get('KICKS_API_KEY')
    KICKS_API_BASE_URL = os.environ.get('KICKS_API_BASE_URL', 'https://api.kicks.dev')
    KICKS_STOCKX_PRICES_ENABLED = os.environ.get('KICKS_STOCKX_PRICES_ENABLED', 'true').lower() in ('1', 'true', 'yes')

    # Supabase Auth (Phase 2 foundation — disabled by default).
    # SUPABASE_AUTH_ENABLED is the master kill switch: while False, no
    # Supabase Auth code path executes for any live request. The other
    # vars are only consulted when the flag is True or by the admin-side
    # linkage CLI.
    #
    # Operational rule (per docs/DECISIONS.md "Phase 2 ships with
    # SUPABASE_AUTH_ENABLED=false as production steady state"):
    # this flag MUST NOT be set to True in any version-controlled file.
    # It may be set True only in a controlled staging probe window or
    # the documented 15-minute production probe exercise, then returned
    # to False. Tests may mutate it on a test-only Flask app instance.
    SUPABASE_URL = os.environ.get('SUPABASE_URL')
    SUPABASE_ANON_KEY = os.environ.get('SUPABASE_ANON_KEY')
    SUPABASE_SERVICE_ROLE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
    SUPABASE_JWT_SECRET = os.environ.get('SUPABASE_JWT_SECRET')
    SUPABASE_AUTH_ENABLED = os.environ.get('SUPABASE_AUTH_ENABLED', 'false').lower() in ('1', 'true', 'yes')


class TestConfig(Config):
    TESTING = True
    # Use an in-memory SQLite database for fast, clean tests
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:' 
    # Disable CSRF protection in tests to simplify form submissions
    WTF_CSRF_ENABLED = False
    SERVER_NAME = 'localhost.localdomain'
