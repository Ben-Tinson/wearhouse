# tests/test_releases.py
from models import Release, User
from extensions import db
from datetime import date, timedelta

def test_admin_can_add_release(test_client, auth, admin_user):
    """
    GIVEN a logged-in admin user
    WHEN they submit the 'add_release' form with valid data
    THEN check that a new Release object is created in the database
    """
    # Log in as the admin user provided by our new fixture
    auth.login(username=admin_user.username, password='password123')

    # Define the data for our new release
    tomorrow = date.today() + timedelta(days=1)
    release_data = {
        'brand': 'Testsuo',
        'name': 'Shima',
        'release_date': tomorrow.strftime('%Y-%m-%d'),
        'retail_price': '190.00',
        'retail_currency': 'USD',
        'image_option': 'url', # Assuming we are providing a URL
        'image_url': 'http://example.com/image.jpg'
    }

    # POST the data to the add_release route
    response = test_client.post('/admin/add-release', data=release_data, follow_redirects=True)

    # 1. Check the response
    assert response.status_code == 200 # Should redirect to the calendar page
    assert b"New release has been added" in response.data # Check for the flash message

    # 2. Check the database directly to confirm creation
    new_release = Release.query.filter_by(name='Shima').first()
    assert new_release is not None
    assert new_release.brand == 'Testsuo'
    assert new_release.release_date == tomorrow

def test_non_admin_cannot_access_add_release_page(test_client, auth, init_database):
    """
    GIVEN a logged-in non-admin user
    WHEN they attempt to access the '/admin/add_release' page via GET or POST
    THEN check that they receive a 403 Forbidden error
    """
    # The 'init_database' fixture provides a standard, non-admin user
    user, _ = init_database

    # 1. Log in as the regular user
    auth.login(username=user.username, password='password123')

    # 2. Attempt to access the page with a GET request
    response_get = test_client.get('/admin/add-release')

    # Assert that access is forbidden
    assert response_get.status_code == 403

    # 3. Attempt to submit data with a POST request
    tomorrow = date.today() + timedelta(days=1)
    release_data = {
        'brand': 'Unauthorized',
        'name': 'Entry',
        'release_date': tomorrow.strftime('%Y-%m-%d'),
        'image_option': 'url'
    }
    response_post = test_client.post('/admin/add-release', data=release_data)

    # Assert that this action is also forbidden
    assert response_post.status_code == 403

    # 4. Double-check that no release was created
    unauthorized_release = Release.query.filter_by(brand='Unauthorized').first()
    assert unauthorized_release is None





