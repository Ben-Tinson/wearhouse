# config.py
import os

basedir = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = 'uploads'

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or '6238418573691154'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.path.join(basedir, UPLOAD_FOLDER)
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'avif'}
    DATABASE_URL = os.environ.get('DATABASE_URL')
     # If the URL exists and starts with the old 'postgres://', replace it
    if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

    # Set the final configuration variable
    SQLALCHEMY_DATABASE_URI = DATABASE_URL or \
        'sqlite:///' + os.path.join(basedir, 'site.db')

    SQLALCHEMY_TRACK_MODIFICATIONS = False


    # Flask-Mail configuration
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.example.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() in ['true', 'on', '1']
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = ('WearHouse Admin', os.environ.get('MAIL_USERNAME', 'noreply@wearhouse.com'))

class TestConfig(Config):
    TESTING = True
    # Use an in-memory SQLite database for fast, clean tests
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:' 
    # Disable CSRF protection in tests to simplify form submissions
    WTF_CSRF_ENABLED = False

