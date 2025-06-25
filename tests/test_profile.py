# tests/test_profile.py
from models import User
from extensions import db

def test_update_profile_details(test_client, auth, init_database):
    """
    GIVEN a logged-in user
    WHEN they submit the edit profile form with a new first and last name
    THEN check that their details are updated in the database
    """
    user, _ = init_database # Get the user from our fixture

    # 1. Log in as the user
    auth.login(username=user.username, password='password123')

    # 2. POST new data to the edit_profile route
    new_first_name = "UpdatedFirst"
    new_last_name = "UpdatedLast"

    response = test_client.post('/edit-profile', data={
        'first_name': new_first_name,
        'last_name': new_last_name,
        'email': user.email, # Keep the email the same for this test
        'marketing_opt_in': user.marketing_opt_in
    }, follow_redirects=True)

    # 3. Assert the response is successful
    assert response.status_code == 200 # Should redirect to the profile page
    assert b"Your profile has been updated successfully!" in response.data

    # 4. Check the database directly to confirm the changes
    updated_user = db.session.get(User, user.id)
    assert updated_user.first_name == new_first_name
    assert updated_user.last_name == new_last_name

    # 5. Verify the new name appears on the profile page
    assert bytes(new_first_name, 'utf-8') in response.data
    assert bytes(new_last_name, 'utf-8') in response.data

def test_profile_email_change_flow(test_client, auth, init_database):
    """
    GIVEN a logged-in user
    WHEN they request to change their email and confirm via the token link
    THEN check that their email address is updated correctly in the database
    """
    user, _ = init_database # Get the user from our fixture
    original_email = user.email
    new_email = "new.email@example.com"

    # 1. Log in as the user
    auth.login(username=user.username, password='password123')

    # 2. POST the new email address to the edit_profile route
    response_request = test_client.post('/edit-profile', data={
        'first_name': user.first_name,
        'last_name': user.last_name,
        'email': new_email, # Provide the new email
        'marketing_opt_in': user.marketing_opt_in
    }, follow_redirects=True)

    # 3. Assert that the initial request was successful
    assert response_request.status_code == 200 # Should redirect to profile page
    assert b"A confirmation link has been sent" in response_request.data

    # 4. Check the database: email should NOT be changed yet, pending_email SHOULD be set
    user_in_db = db.session.get(User, user.id)
    assert user_in_db.email == original_email # Email is still the old one
    assert user_in_db.pending_email == new_email # New email is in pending

    # 5. Simulate getting the token from the email
    token = user_in_db.get_confirm_new_email_token(new_email)
    assert token is not None

    # 6. Make a GET request to the confirmation URL with the token
    response_confirm = test_client.get(f'/confirm-new-email/{token}', follow_redirects=True)

    # 7. Assert that the confirmation was successful
    assert response_confirm.status_code == 200 # Should redirect to profile or login
    assert b"Your email address has been successfully confirmed" in response_confirm.data

    # 8. Check the database again for the final state
    final_user_state = db.session.get(User, user.id)
    assert final_user_state.email == new_email # Email is now the new one
    assert final_user_state.pending_email is None # Pending email has been cleared

def test_edit_profile_duplicate_email(test_client, auth, init_database, another_user_in_db):
    """
    GIVEN two registered users (user_1 and user_2)
    WHEN user_1 is logged in and tries to change their email to user_2's email
    THEN check that form validation fails and an error message is shown
    """
    # The 'init_database' fixture provides user_1
    user_1, _ = init_database

    # The 'another_user_in_db' fixture provides user_2
    user_2 = another_user_in_db
    email_of_user_2 = user_2.email

    # 1. Log in as user_1
    auth.login(username=user_1.username, password='password123')

    # 2. POST to the edit_profile route, attempting to take user_2's email
    response = test_client.post('/edit-profile', data={
        'first_name': user_1.first_name,
        'last_name': user_1.last_name,
        'email': email_of_user_2, # Attempting to use the duplicate email
        'marketing_opt_in': user_1.marketing_opt_in
    }, follow_redirects=True)

    # 3. Assert the response indicates a validation failure
    assert response.status_code == 200 # A failed validation should re-render the page

    # 4. Check for the specific validation error message from your form
    assert b"That email address is already registered." in response.data
    assert b"Your profile has been updated successfully!" not in response.data

    # 5. Check the database to ensure user_1's email was NOT changed
    user1_in_db = db.session.get(User, user_1.id)
    assert user1_in_db.email != email_of_user_2










