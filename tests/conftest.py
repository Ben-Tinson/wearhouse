# tests/conftest.py
import pytest
import warnings
from urllib3.exceptions import NotOpenSSLWarning
from app import create_app
from extensions import db
from config import TestConfig
from models import User, Sneaker, Release
from datetime import date

# Suppress the specific NotOpenSSLWarning from the urllib3 package
warnings.filterwarnings("ignore", category=NotOpenSSLWarning)

@pytest.fixture(scope='function')
def test_app():
    """Creates a test instance of the app with a fresh database and temp upload folder."""

    # Create a temporary upload folder for this test run
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()

@pytest.fixture(scope='function')
def test_client(test_app):
    """Creates a test client for the app."""
    return test_app.test_client()

@pytest.fixture(scope='function')
def auth(test_client):
    """Provides helper methods for authentication actions."""
    class AuthActions:
        def __init__(self, client):
            self._client = client

        def login(self, username='testuser', password='password123'):
            return self._client.post('/login', 
                                    data={'username': username, 'password': password}, 
                                    follow_redirects=True) # <-- ADD THIS
        
        def logout(self):
            return self._client.get('/logout', follow_redirects=True)
            
    return AuthActions(test_client)

@pytest.fixture(scope='function')
def init_database(test_app):
    """Creates a test user and a test sneaker owned by that user."""
    with test_app.app_context():
        # Create the user
        user = User(
            username='testuser', 
            email='test@example.com',
            first_name='Test',
            last_name='User',
            is_email_confirmed=True
        )
        user.set_password('password123')
        db.session.add(user)
        
        # Create a sneaker owned by this user
        sneaker = Sneaker(
            brand='Initial Brand',
            model='Initial Model',
            colorway='Initial Colors',
            owner=user # Associate with the user from THIS session
        )
        db.session.add(sneaker)
        
        db.session.commit()
        
        yield user, sneaker # Yield both objects within the same session context

@pytest.fixture(scope='function')
def another_user_in_db(test_app):
    """Fixture to create and commit a second, different user."""
    with test_app.app_context():
        user = User(
            username='other_user', 
            email='other@example.com',
            first_name='Other',
            last_name='User',
            is_email_confirmed=True
        )
        user.set_password('password789')
        db.session.add(user)
        db.session.commit()
        yield user

@pytest.fixture(scope='function')
def user_with_mixed_sneakers(test_app):
    """Creates a user and 3 sneakers with distinct data for search/filter tests."""
    with test_app.app_context():
        user = User(username='testuser', email='test@example.com', first_name='Test', last_name='User', is_email_confirmed=True)
        user.set_password('password123')

        sneaker1 = Sneaker(brand='Nike', model='Air Force 1', colorway='White on White', owner=user)
        sneaker2 = Sneaker(brand='Adidas', model='Superstar', colorway='Cloud White / Core Black', owner=user)
        sneaker3 = Sneaker(brand='Jordan', model='Air Jordan 4', colorway='Bred Reimagined', owner=user)

        db.session.add(user)
        db.session.add_all([sneaker1, sneaker2, sneaker3])
        db.session.commit()

        yield user, [sneaker1, sneaker2, sneaker3]

@pytest.fixture(scope='function')
def admin_user(test_app):
    """Fixture that creates and commits an admin user to the database."""
    with test_app.app_context():
        user = User(
            username='adminuser', 
            email='admin@example.com',
            first_name='Admin',
            last_name='User',
            is_email_confirmed=True,
            is_admin=True # <-- This makes the user an admin
        )
        user.set_password('password123')
        db.session.add(user)
        db.session.commit()
        yield user

@pytest.fixture(scope='function')
def user_with_wishlist(test_app):
    """Creates a user and several releases, adding two to the user's wishlist."""
    with test_app.app_context():
        user = User(
            username='wishlister', 
            email='wish@list.com', 
            first_name='Wish',
            last_name='User', # <-- ADD THIS LINE
            is_email_confirmed=True
        )
        user.set_password('password123')

        # Create some releases
        release1 = Release(brand='Nike', name='Wishlist Nike 1', release_date=date(2025, 12, 1))
        release2 = Release(brand='Adidas', name='Non-Wishlist Adidas', release_date=date(2025, 12, 2))
        release3 = Release(brand='Nike', name='Wishlist Nike 2', release_date=date(2025, 12, 3))

        # Add releases to the user's wishlist
        user.wishlist.append(release1)
        user.wishlist.append(release3)

        db.session.add(user)
        db.session.add_all([release1, release2, release3])
        db.session.commit()

        yield user




