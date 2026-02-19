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
    
    # External API Keys
    RETAILED_API_KEY = os.environ.get('RETAILED_API_KEY')
    RAPIDAPI_KEY = os.environ.get('RAPIDAPI_KEY')
    KICKS_API_KEY = os.environ.get('KICKS_API_KEY')
    KICKS_API_BASE_URL = os.environ.get('KICKS_API_BASE_URL', 'https://api.kicks.dev')
    KICKS_STOCKX_PRICES_ENABLED = os.environ.get('KICKS_STOCKX_PRICES_ENABLED', 'true').lower() in ('1', 'true', 'yes')


class TestConfig(Config):
    TESTING = True
    # Use an in-memory SQLite database for fast, clean tests
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:' 
    # Disable CSRF protection in tests to simplify form submissions
    WTF_CSRF_ENABLED = False
    SERVER_NAME = 'localhost.localdomain'
