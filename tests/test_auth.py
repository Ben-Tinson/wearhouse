# tests/test_auth.py
from models import User
from extensions import db
def test_duplicate_username_registration(test_client, init_database):
    """
    GIVEN a registered user exists
    WHEN another user attempts to register with the same username
    THEN check that the registration fails and an error message is shown
    """
    # The 'new_user_in_db' fixture has already created a user with username 'testuser'.

    # Now, attempt to register a NEW user with the SAME username
    response = test_client.post('/register', data={
        'username': 'testuser', # Using the existing username
        'first_name': 'Another',
        'last_name': 'User',
        'email': 'another@example.com', # Different email
        'password': 'password123',
        'confirm_password': 'password123',
        'preferred_region': 'US'
    }, follow_redirects=True)

    # 1. Check the response
    # A failed registration should re-render the page, not redirect successfully
    # However, our POST logic redirects to login even on failure to show the flash message.
    # Let's check for the flash message on the resulting page.
    assert response.status_code == 200 
    assert b"That username is already taken" in response.data # Check for the specific error flash
    assert b"Registration successful!" not in response.data # Ensure success message is NOT there

    # 2. Check the database to ensure a second user was NOT created
    user_count = db.session.query(User).count()
    assert user_count == 1 # Should still only be the one user from the fixture


def test_registration_saves_preferred_region(test_client):
    response = test_client.post('/register', data={
        'username': 'regionuser',
        'first_name': 'Region',
        'last_name': 'User',
        'email': 'region@example.com',
        'password': 'password123',
        'confirm_password': 'password123',
        'preferred_region': 'EU'
    }, follow_redirects=True)
    assert response.status_code == 200
    user = User.query.filter_by(username='regionuser').first()
    assert user is not None
    assert user.preferred_region == 'EU'

def test_duplicate_email_registration(test_client, init_database):
    """
    GIVEN a registered user exists
    WHEN another user attempts to register with the same email address
    THEN check that the registration fails and an error message is shown
    """
    # The 'init_database' fixture has already created a user with email 'test@example.com'

    # Attempt to register a NEW user with the SAME email but a different username
    response = test_client.post('/register', data={
        'username': 'newuser', # Different username
        'first_name': 'Another',
        'last_name': 'User',
        'email': 'test@example.com', # Using the EXISTING email
        'password': 'password123',
        'confirm_password': 'password123',
        'preferred_region': 'US'
    }, follow_redirects=True)

    # 1. Check the response
    assert response.status_code == 200 # Should re-render the page successfully
    # Check for the specific error flash message
    assert b"That email address is already registered" in response.data
    # Ensure the success message is NOT present
    assert b"Registration successful!" not in response.data

    # 2. Check the database to ensure a second user was NOT created
    user_count = db.session.query(User).count()
    assert user_count == 1 # Should still only be the one user from the fixture

def test_login_with_wrong_password(test_client, init_database):
    """
    GIVEN a registered user exists
    WHEN the user attempts to log in with a correct username but wrong password
    THEN check that the login fails and an error message is shown
    """
    # The 'init_database' fixture provides a user with username 'testuser'
    # and password 'password123'

    # Attempt to log in with the correct username but an incorrect password
    response = test_client.post('/login', data={
        'username': 'testuser',
        'password': 'wrongpassword' # Using an incorrect password
    }, follow_redirects=True)

    # 1. Check the response
    assert response.status_code == 200 # Should re-render the login page

    # 2. Check for the specific failure flash message
    assert b"Login Unsuccessful. Please check username and password" in response.data

    # 3. Check that content for logged-in users is NOT present
    # (e.g., the link to their profile is not in the navbar)
    assert b"Profile" not in response.data

def test_password_reset_flow(test_client, auth, init_database):
    """
    GIVEN an existing user
    WHEN they request a password reset, get a token, and submit a new password
    THEN check that the password is changed and they can log in with the new password
    """
    user, _ = init_database # Get the user created by the fixture

    # --- Part 1: Request the reset link ---
    # This part isn't strictly necessary for testing the reset itself,
    # but confirms the request page works.
    response_request = test_client.post('/reset-password-request', data={
        'email': user.email
    }, follow_redirects=True)
    assert response_request.status_code == 200 # Should redirect to login
    assert b"instructions to reset your password have been sent" in response_request.data

    # --- Part 2: Generate a token (simulating what the user receives in email) ---
    # In a real test, you might mock the email sending to capture the token.
    # For our purposes, we can generate it directly using the method on our user object.
    token = user.get_reset_password_token()
    assert token is not None

    # --- Part 3: Use the token to set a new password ---
    new_password = 'a_brand_new_password'
    response_reset = test_client.post(f'/reset-password/{token}', data={
        'password': new_password,
        'confirm_password': new_password
    }, follow_redirects=True)

    # Assert that the reset was successful
    assert response_reset.status_code == 200 # Should redirect to login page
    assert b"Your password has been successfully updated!" in response_reset.data

    # --- Part 4: Verify the new password works and the old one fails ---
    # Attempt to log in with the NEW password (should succeed)
    response_login_new = auth.login(username=user.username, password=new_password)
    assert response_login_new.status_code == 200 # Successful login redirects

    # Log out
    auth.logout()

    # Attempt to log in with the OLD password (should fail)
    response_login_old = test_client.post('/login', data={
        'username': user.username,
        'password': 'password123' # The original password from the fixture
    }, follow_redirects=True)
    assert response_login_old.status_code == 200 # Failed login re-renders page
    assert b"Login Unsuccessful" in response_login_old.data

def test_change_password_flow_logged_in(test_client, auth, init_database):
    """
    GIVEN a logged-in user
    WHEN they initiate a password change from their profile and use the token link
    THEN check that the password is changed and they can log in with the new password
    """
    user, _ = init_database # Get the user from the fixture

    # 1. Log in as the user
    auth.login(username=user.username, password='password123')

    # 2. Simulate user clicking the button on the /change_password page
    #    to request the password change link.
    response_request = test_client.post('/send-change-password-link', follow_redirects=True)
    assert response_request.status_code == 200 # Should redirect back to profile
    assert b'A password change link has been sent' in response_request.data

    # 3. Simulate getting the token from the email
    #    We generate it directly here for the test.
    token = user.get_reset_password_token()
    assert token is not None

    # 4. It's good practice to log out before using a reset link, as the final
    #    step of the reset flow will log the user out anyway.
    auth.logout()

    # 5. Use the token to POST a new password
    new_password = 'a_much_better_password'
    response_reset = test_client.post(f'/reset-password/{token}', data={
        'password': new_password,
        'confirm_password': new_password
    }, follow_redirects=True)

    # Assert the reset was successful
    assert response_reset.status_code == 200 # Should redirect to login page
    assert b"Your password has been successfully updated!" in response_reset.data

    # 6. Verify login works with the new password
    response_login_new = auth.login(username=user.username, password=new_password)
    assert response_login_new.status_code == 200 # Successful login redirects to home

def test_logout_functionality(test_client, auth, init_database):
    """
    GIVEN a logged-in user
    WHEN they access the logout route
    THEN check that they are logged out and can no longer access protected pages
    """
    user, _ = init_database

    # 1. Log in the user first
    auth.login(username=user.username, password='password123')

    # 2. As a sanity check, verify they can access a protected page
    response_auth_check = test_client.get('/my-collection')
    assert response_auth_check.status_code == 200

    # 3. Now, perform the logout action
    response_logout = auth.logout() # Our auth fixture handles the redirect

    # 4. Assert that the logout page shows the correct message
    assert response_logout.status_code == 200
    assert b"You have been logged out." in response_logout.data

    # 5. The most important check: try to access the protected page again
    response_after_logout = test_client.get('/my-collection')
    # Assert that this now results in a redirect (status 302) to the login page
    assert response_after_logout.status_code == 302
