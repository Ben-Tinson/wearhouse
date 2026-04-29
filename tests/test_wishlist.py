# tests/test_wishlist.py
from decimal import Decimal

from models import User, Release, wishlist_items
from extensions import db
from routes.main_routes import _format_month_filter_choices

def test_wishlist_filter(test_client, auth, user_with_wishlist):
    """
    GIVEN a logged-in user with a populated wishlist
    WHEN they filter their wishlist page by brand
    THEN check that only the correct items are displayed
    """
    # Log in as the user created by the fixture
    auth.login(username=user_with_wishlist.username, password='password123')

    # Make a GET request to the wishlist page, filtering for 'Nike'
    response = test_client.get('/my-wishlist?filter_brand=Nike')

    # 1. Check the response is successful
    assert response.status_code == 200
    response_data_string = response.data.decode()

    # 2. Assert that BOTH Nike models are present
    assert 'Wishlist Nike 1' in response_data_string
    assert 'Wishlist Nike 2' in response_data_string

    # 3. Assert that the Adidas model is NOT present
    assert 'Non-Wishlist Adidas' not in response_data_string


def test_month_filter_choices_handle_postgres_decimal_extract_results():
    choices = _format_month_filter_choices([(Decimal("2025"), Decimal("12"))])
    assert choices == [("2025-12", "December 2025")]


