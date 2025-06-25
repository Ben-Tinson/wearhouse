# tests/test_sneakers.py
from models import Sneaker, User
from extensions import db
from io import BytesIO
from datetime import date
import os

def test_add_sneaker(test_client, auth, init_database):
    # 'init_database' already created one user and one sneaker
    user, _ = init_database # We just need the user to log in
    auth.login(username=user.username, password='password123')

    sneaker_data = { 'brand': 'Test Brand 2', 'model': 'Test Model 2', 'image_option': 'url' }
    response = test_client.post('/add-sneaker', data=sneaker_data, follow_redirects=True)
    assert response.status_code == 200
    assert b"New sneaker added" in response.data

    added_sneaker = Sneaker.query.filter_by(model='Test Model 2').first()
    assert added_sneaker is not None

def test_edit_sneaker(test_client, auth, init_database):
    user, sneaker_to_edit = init_database # Get both user and sneaker
    auth.login(username=user.username, password='password123')

    sneaker_id = sneaker_to_edit.id
    updated_data = { 'brand': 'Updated Brand', 'model': 'Updated Model', 'image_option': 'url' }
    response = test_client.post(f'/edit-sneaker/{sneaker_id}', data=updated_data, follow_redirects=True)
    assert response.status_code == 200
    assert b"Sneaker details updated" in response.data

    edited_sneaker = db.session.get(Sneaker, sneaker_id)
    assert edited_sneaker.brand == 'Updated Brand'

def test_delete_sneaker(test_client, auth, init_database):
    user, sneaker_to_delete = init_database
    auth.login(username=user.username, password='password123')

    sneaker_id = sneaker_to_delete.id
    response_delete = test_client.post(f'/delete-sneaker/{sneaker_id}', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert response_delete.status_code == 200
    assert response_delete.get_json()['status'] == 'success'

    deleted_sneaker = db.session.get(Sneaker, sneaker_id)
    assert deleted_sneaker is None

def test_add_sneaker_validation_error(test_client, auth, init_database):
    user, _ = init_database
    auth.login(username=user.username, password='password123')

    invalid_sneaker_data = { 'colorway': 'Test Colors', 'image_option': 'url' }
    response = test_client.post('/add-sneaker', data=invalid_sneaker_data, follow_redirects=False)
    assert response.status_code == 200
    assert b"This field is required." in response.data

def test_add_sneaker_invalid_file_type(test_client, auth, init_database):
    """
    GIVEN a logged-in user
    WHEN they submit the add sneaker form with a file of a disallowed type (e.g., .txt)
    THEN check that form validation fails and returns a JSON error
    """
    # The 'init_database' fixture provides a user and one initial sneaker.
    user, _ = init_database # We only need the user object for this test.

    # Log in as the user
    auth.login(username=user.username, password='password123')

    # Create form data with a .txt file
    sneaker_data = {
        'brand': 'Test Brand',
        'model': 'Invalid File',
        'image_option': 'upload',
        'sneaker_image_file': (BytesIO(b"this is a test file"), 'test.txt')
    }

    # POST the invalid data to the add_sneaker endpoint with AJAX headers
    response = test_client.post('/add-sneaker', data=sneaker_data, headers={
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': 'application/json'
    }, content_type='multipart/form-data')

    # 1. Check the response for a validation error
    assert response.status_code == 400
    json_response = response.get_json()
    assert json_response['status'] == 'error'
    assert 'errors' in json_response
    assert 'sneaker_image_file' in json_response['errors']
    assert 'Images only!' in json_response['errors']['sneaker_image_file'][0]

    # 2. Check the database to ensure no new sneaker was created
    sneaker_count = Sneaker.query.filter_by(owner=user).count()
    # The init_database fixture creates one sneaker, so the count should still be 1
    assert sneaker_count == 1

def test_user_cannot_edit_others_sneaker(test_client, auth, init_database, another_user_in_db):
    """
    GIVEN two users, where sneaker_1 is owned by user_1
    WHEN user_2 logs in and attempts to edit sneaker_1
    THEN check that they are redirected and the sneaker is not changed
    """
    # The 'init_database' fixture provides user_1 and sneaker_1
    user_1, sneaker_1 = init_database

    # The 'another_user_in_db' fixture provides user_2
    user_2 = another_user_in_db

    # 1. Log in as the OTHER user (user_2)
    auth.login(username=user_2.username, password='password789')

    sneaker_id_to_edit = sneaker_1.id

    # 2. Attempt to POST data to edit the other user's sneaker
    updated_data = { 'brand': 'Malicious Edit', 'model': 'Hacked', 'image_option': 'url' }
    response = test_client.post(f'/edit-sneaker/{sneaker_id_to_edit}', data=updated_data, follow_redirects=True)

    # 3. Assert that the user was redirected and saw a permission error message
    assert response.status_code == 200 # The FINAL page after redirect should be 200 OK
    assert b"You do not have permission" in response.data # Check for the flash message

    # 4. Assert that the sneaker was NOT actually changed in the database
    unchanged_sneaker = db.session.get(Sneaker, sneaker_id_to_edit)
    assert unchanged_sneaker.brand == 'Initial Brand'
    assert unchanged_sneaker.brand != 'Malicious Edit'

def test_user_cannot_delete_others_sneaker(test_client, auth, init_database, another_user_in_db):
    """
    GIVEN two users, where sneaker_1 is owned by user_1
    WHEN user_2 logs in and attempts to delete sneaker_1
    THEN check that the request is forbidden (403) and the sneaker is not deleted
    """
    # The 'init_database' fixture provides user_1 ('testuser') and sneaker_1
    user_1, sneaker_1 = init_database 

    # The 'another_user_in_db' fixture provides user_2 ('other_user')
    user_2 = another_user_in_db

    # 1. Log in as the "attacker" (user_2)
    auth.login(username=user_2.username, password='password789')

    sneaker_id_to_delete = sneaker_1.id

    # 2. Make an AJAX POST request to delete the other user's sneaker
    response_delete = test_client.post(f'/delete-sneaker/{sneaker_id_to_delete}', headers={
        'X-Requested-With': 'XMLHttpRequest'
    })

    # 3. Assert that the server responded with 403 Forbidden
    assert response_delete.status_code == 403
    json_response = response_delete.get_json()
    assert json_response['status'] == 'error'
    assert 'You do not have permission' in json_response['message']

    # 4. Assert that the sneaker was NOT actually deleted from the database
    sneaker_still_exists = db.session.get(Sneaker, sneaker_id_to_delete)
    assert sneaker_still_exists is not None
    assert sneaker_still_exists.brand == 'Initial Brand' # Check it's still the same sneaker

def test_add_to_rotation_flow(test_client, auth, init_database):
    """
    GIVEN a logged-in user with a sneaker not in rotation
    WHEN the user adds that sneaker to their rotation
    THEN check that the sneaker's status is updated and it appears on the rotation page.
    """
    user, sneaker = init_database
    auth.login(username=user.username, password='password123')
    sneaker_id_to_add = sneaker.id

    # Use the test_client as a context manager to access the session
    with test_client:
        response_post = test_client.post('/select-for-rotation', data={
            'sneaker_ids': [sneaker_id_to_add]
        })

        # 1. Assert the POST results in a redirect
        assert response_post.status_code == 302

        # 2. Check the session for the success flash message
        from flask import session
        flashed_messages = session.get('_flashes', [])
        assert len(flashed_messages) > 0
        assert flashed_messages[0][0] == 'success'

        # --- DEEP DEBUGGING FOR THE FAILING ASSERTION ---
        message_from_server = flashed_messages[0][1]
        substring_to_find = "added to your rotation"
        lowercase_message = message_from_server.lower()

        print("\n--- DEEP DEBUGGING STRINGS (Add to Rotation Test) ---")
        print("Lowercase Message Characters:", list(lowercase_message))
        print("Substring Characters:        ", list(substring_to_find))
        print(f"Is Substring in Lowercase Message? -> {substring_to_find in lowercase_message}")
        print("-----------------------------------------------------")
        # --- END OF DEEP DEBUGGING ---

        assert substring_to_find in lowercase_message

    # 3. Check the database to confirm the change
    updated_sneaker = db.session.get(Sneaker, sneaker_id_to_add)
    assert updated_sneaker.in_rotation is True

    # 4. NOW, make a new GET request to the rotation page to see the result
    response_rotation_after = test_client.get('/my-rotation')
    assert response_rotation_after.status_code == 200
    assert bytes(sneaker.model, 'utf-8') in response_rotation_after.data

def test_remove_from_rotation_flow(test_client, auth, init_database):
    """
    GIVEN a logged-in user with a sneaker that IS in rotation
    WHEN the user removes that sneaker from their rotation
    THEN check that the sneaker's status is updated in the database
    AND the sneaker is no longer on the 'My Rotation' page.
    """
    user, sneaker = init_database

    # 1. Setup: Put sneaker in rotation and log in
    sneaker.in_rotation = True
    db.session.commit()
    auth.login(username=user.username, password='password123')

    sneaker_id_to_remove = sneaker.id
    sneaker_model_name = sneaker.model

    # 2. Verify state before action
    response_rotation_before = test_client.get('/my-rotation')
    assert bytes(sneaker_model_name, 'utf-8') in response_rotation_before.data

    # 3. Perform the action
    response_remove = test_client.post(f'/remove-from-rotation/{sneaker_id_to_remove}', headers={
        'X-Requested-With': 'XMLHttpRequest'
    })

    # 4. Assert response was successful
    assert response_remove.status_code == 200
    json_response = response_remove.get_json()
    assert json_response['status'] == 'success'

    # 5. Assert the key parts of the success message are present
    message_lower = json_response['message'].lower()
    assert "removed" in message_lower
    assert "from your rotation" in message_lower

    # 6. Check the database
    updated_sneaker = db.session.get(Sneaker, sneaker_id_to_remove)
    assert updated_sneaker.in_rotation is False

    # 7. Verify the sneaker is gone from the rotation page
    response_rotation_after = test_client.get('/my-rotation')
    assert bytes(sneaker_model_name, 'utf-8') not in response_rotation_after.data

def test_user_cannot_add_others_sneaker_to_rotation(test_client, auth, init_database, another_user_in_db):
    """
    GIVEN two users, where sneaker_1 is owned by user_1
    WHEN user_2 logs in and attempts to add sneaker_1 to their rotation
    THEN check that the action fails and the sneaker's state is unchanged.
    """
    # The 'init_database' fixture provides user_1 ('testuser') and sneaker_1
    user_1, sneaker_1 = init_database
    
    # The 'another_user_in_db' fixture provides user_2 ('other_user')
    user_2 = another_user_in_db
    
    # 1. Log in as the "attacker" (user_2)
    auth.login(username=user_2.username, password='password789')

    sneaker_id_to_add = sneaker_1.id

    # 2. Use the test_client as a context manager to access the session
    with test_client:
        # POST the ID of the other user's sneaker, but DO NOT follow the redirect
        response = test_client.post('/select-for-rotation', data={
            'sneaker_ids': [sneaker_id_to_add]
        })

        # 3. Assert that the server responded with 302 Redirect
        assert response.status_code == 302
        
        # 4. Check the session for the correct 'warning' flash message
        from flask import session
        flashed_messages = session.get('_flashes', [])
        assert len(flashed_messages) > 0
        assert flashed_messages[0][0] == 'warning' # Check category is 'warning'
        assert 'No sneakers were added' in flashed_messages[0][1] # Check message content

    # 5. Assert that the sneaker's 'in_rotation' status was NOT changed in the database
    sneaker_in_db = db.session.get(Sneaker, sneaker_id_to_add)
    assert sneaker_in_db.in_rotation is False

def test_user_cannot_remove_others_sneaker_from_rotation(test_client, auth, init_database, another_user_in_db):
    """
    GIVEN two users, where sneaker_1 is owned by user_1 and is in rotation
    WHEN user_2 logs in and attempts to remove sneaker_1 from rotation
    THEN check that the action is forbidden and the sneaker's state is unchanged
    """
    user_1, sneaker_1 = init_database
    user_2 = another_user_in_db

    # 1. Manually set user_1's sneaker to be IN rotation
    sneaker_1.in_rotation = True
    db.session.commit()

    # 2. Log in as the "attacker" (user_2)
    auth.login(username=user_2.username, password='password789')

    sneaker_id_to_remove = sneaker_1.id

    # 3. Attempt to POST to the remove_from_rotation endpoint for the other user's sneaker
    response = test_client.post(f'/remove-from-rotation/{sneaker_id_to_remove}', headers={
        'X-Requested-With': 'XMLHttpRequest'
    })

    # 4. Assert that the server responded with 403 Forbidden
    assert response.status_code == 403
    json_response = response.get_json()
    assert json_response['status'] == 'error'
    assert 'Permission denied' in json_response['message']

    # 5. Assert that the sneaker's 'in_rotation' status was NOT changed in the database
    sneaker_in_db = db.session.get(Sneaker, sneaker_id_to_remove)
    assert sneaker_in_db.in_rotation is True # Should still be True

def test_brand_filter(test_client, auth, user_with_mixed_sneakers):
    user, _ = user_with_mixed_sneakers
    auth.login(username=user.username, password='password123')
    response = test_client.get('/my-collection?filter_brand=Nike')
    assert response.status_code == 200
    response_data_string = response.data.decode()
    # Assert that the correct model IS present
    assert 'Air Force 1' in response_data_string
    # Assert that the other models are NOT present
    assert 'Superstar' not in response_data_string
    assert 'Air Jordan 4' not in response_data_string

def test_brand_sorting(test_client, auth, user_with_mixed_sneakers):
    user, _ = user_with_mixed_sneakers
    auth.login(username=user.username, password='password123')
    response = test_client.get('/my-collection?sort_by=brand&order=asc')
    assert response.status_code == 200
    response_data_string = response.data.decode()

    # Find the position of the unique models, which are sorted by brand
    adidas_pos = response_data_string.find('Superstar') # Adidas
    jordan_pos = response_data_string.find('Air Jordan 4') # Jordan
    nike_pos = response_data_string.find('Air Force 1') # Nike

    # Assert all were found and are in the correct A-Z order
    assert all(p != -1 for p in [adidas_pos, jordan_pos, nike_pos])
    assert adidas_pos < jordan_pos < nike_pos

def test_search_logic(test_client, auth, user_with_mixed_sneakers):
    """
    GIVEN a logged-in user with several distinct sneakers
    WHEN the user performs a search
    THEN check that only the sneakers matching the search term are displayed
    """
    user, _ = user_with_mixed_sneakers

    # Log in as the user
    auth.login(username=user.username, password='password123')

    # Make a GET request to the collection page, searching for 'Jordan'
    response = test_client.get('/my-collection?search_term=Jordan')

    # 1. Check the response is successful
    assert response.status_code == 200

    # 2. Decode the response data to easily search for text
    response_data_string = response.data.decode()

    # 3. Assert that the Jordan model is present
    assert 'Air Jordan 4' in response_data_string
    assert 'Bred Reimagined' in response_data_string

    # 4. Assert that the other models are NOT present
    assert 'Air Force 1' not in response_data_string
    assert 'Superstar' not in response_data_string

    # 5. Assert that the count summary message is correct for a search
    assert 'Found 1 pair' in response_data_string # Since 'Jordan' is unique to one sneaker

def test_user_cannot_update_last_worn_for_others_sneaker(test_client, auth, init_database, another_user_in_db):
    """
    GIVEN two users, where sneaker_1 is owned by user_1
    WHEN user_2 logs in and attempts to update the last_worn_date for sneaker_1
    THEN check that the action is forbidden and the date is not changed
    """
    user_1, sneaker_1 = init_database
    user_2 = another_user_in_db

    # 1. Log in as the "attacker" (user_2)
    auth.login(username=user_2.username, password='password789')

    sneaker_id_to_update = sneaker_1.id
    original_last_worn = sneaker_1.last_worn_date # Will be None initially

    # 2. Make an AJAX POST request to update the other user's sneaker
    response = test_client.post(f'/update-last-worn/{sneaker_id_to_update}', data={
        'new_last_worn': '2025-01-01'
    }, headers={'X-Requested-With': 'XMLHttpRequest'})

    # 3. Assert that the server responded with 403 Forbidden
    assert response.status_code == 403
    json_response = response.get_json()
    assert json_response['status'] == 'error'
    assert 'Permission denied' in json_response['message']

    # 4. Assert that the sneaker's last_worn_date was NOT changed in the database
    sneaker_in_db = db.session.get(Sneaker, sneaker_id_to_update)
    assert sneaker_in_db.last_worn_date == original_last_worn

def test_select_for_rotation_filter(test_client, auth, user_with_mixed_sneakers):
    """
    GIVEN a logged-in user with several sneakers not in rotation
    WHEN they filter the 'Select for Rotation' page by brand
    THEN check that only sneakers of that brand are listed for selection
    """
    # The fixture provides a user with Nike, Adidas, and Jordan sneakers
    user, _ = user_with_mixed_sneakers

    # Log in as the user
    auth.login(username=user.username, password='password123')

    # Make a GET request to the selection page, filtering for 'Nike'
    response = test_client.get('/select-for-rotation?filter_brand=Nike')

    # 1. Check the response is successful
    assert response.status_code == 200

    # 2. Decode the response data to check its content
    response_data_string = response.data.decode()

    # 3. Assert that the Nike model IS present in the list
    assert 'Air Force 1' in response_data_string

    # 4. Assert that the Adidas and Jordan models are NOT present
    assert 'Superstar' not in response_data_string
    assert 'Air Jordan 4' not in response_data_string

def test_select_for_rotation_search(test_client, auth, user_with_mixed_sneakers):
    """
    GIVEN a logged-in user with several sneakers not in rotation
    WHEN they use the search bar on the 'Select for Rotation' page
    THEN check that only sneakers matching the search term are listed
    """
    # The fixture provides a user with Nike, Adidas, and Jordan sneakers
    user, _ = user_with_mixed_sneakers

    # Log in as the user
    auth.login(username=user.username, password='password123')

    # Make a GET request to the selection page, searching for the term "Superstar"
    response = test_client.get('/select-for-rotation?search_term=Superstar')

    # 1. Check the response is successful
    assert response.status_code == 200

    # 2. Decode the response data to check its content
    response_data_string = response.data.decode()

    # 3. Assert that the Adidas model IS present
    assert 'Superstar' in response_data_string
    assert 'Cloud White / Core Black' in response_data_string

    # 4. Assert that the Nike and Jordan models are NOT present
    assert 'Air Force 1' not in response_data_string
    assert 'Air Jordan 4' not in response_data_string

def test_add_sneaker_invalid_price(test_client, auth, init_database):
    """
    GIVEN a logged-in user
    WHEN they submit the add sneaker form with a non-numeric value for price
    THEN check that form validation fails and returns a JSON error
    """
    user, _ = init_database

    # Log in as the user
    auth.login(username=user.username, password='password123')

    # Create form data with text in the price field
    invalid_sneaker_data = {
        'brand': 'Test Brand',
        'model': 'Invalid Price',
        'purchase_price': 'not-a-number', # Invalid data for a DecimalField
        'image_option': 'url'
    }

    # POST the invalid data to the add_sneaker endpoint with AJAX headers
    response = test_client.post('/add-sneaker', data=invalid_sneaker_data, headers={
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': 'application/json'
    })

    # 1. Check the response for a validation error
    assert response.status_code == 400 # Failed validation should return a 400 Bad Request

    json_response = response.get_json()
    assert json_response['status'] == 'error'
    assert 'errors' in json_response
    assert 'purchase_price' in json_response['errors'] # Check error is for the correct field

    # Check for the specific error message from the DecimalField validator
    assert 'Not a valid decimal value' in json_response['errors']['purchase_price'][0]

    # 2. Check the database to ensure no new sneaker was created
    sneaker_with_invalid_price = Sneaker.query.filter_by(model='Invalid Price').first()
    assert sneaker_with_invalid_price is None

def test_add_sneaker_with_image_upload(test_client, auth, init_database, tmp_path):
    """
    GIVEN a logged-in user
    WHEN they submit the add sneaker form with a valid image file upload
    THEN check that the file is saved to the filesystem and the sneaker is in the DB
    """
    user, _ = init_database
    auth.login(username=user.username, password='password123')

    # Configure the app to use a temporary folder for this specific test
    upload_folder = tmp_path / "uploads"
    upload_folder.mkdir()
    test_client.application.config['UPLOAD_FOLDER'] = str(upload_folder)

    # Use BytesIO to simulate a simple image file
    file_data = (BytesIO(b"a fake image"), 'test_image.jpg')

    sneaker_data = {
        'brand': 'File-Upload',
        'model': 'Test',
        'image_option': 'upload',
        'sneaker_image_file': file_data
    }

    response = test_client.post('/add-sneaker', data=sneaker_data, content_type='multipart/form-data')

    # 1. Check the database to get the new sneaker's details
    new_sneaker = Sneaker.query.filter_by(brand='File-Upload').first()
    assert new_sneaker is not None
    assert new_sneaker.image_url.endswith('.jpg')

    # 2. Check the filesystem directly
    saved_filepath = os.path.join(upload_folder, new_sneaker.image_url)
    assert os.path.exists(saved_filepath)


