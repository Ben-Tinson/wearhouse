# tests/test_basic.py

def test_home_page_logged_out(test_client):
    response = test_client.get('/')
    assert response.status_code == 200
    assert b"Welcome to Soletrak!" in response.data

def test_dashboard_page_logged_in(test_client, auth, init_database):
    # The 'init_database' fixture creates the user and sneaker.
    # The 'auth' fixture provides the login method.
    auth.login() # Log in the user ('testuser' by default)

    response_dashboard = test_client.get('/my-collection')

    assert response_dashboard.status_code == 200
    assert b"My Collection" in response_dashboard.data
    assert b"Hey, Test!" in response_dashboard.data # Use the correct greeting
    assert b"Initial Brand" in response_dashboard.data # Check that the sneaker is there
