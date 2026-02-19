# tests/test_sneakers.py
from models import (
    Sneaker,
    User,
    Release,
    AffiliateOffer,
    SneakerNote,
    SneakerWear,
    SneakerCleanEvent,
    SneakerDamageEvent,
    SneakerRepairEvent,
    SneakerRepairResolvedDamage,
    SneakerExpense,
    SneakerHealthSnapshot,
    SneakerDB,
    StepAttribution,
    StepBucket,
    SneakerExposureAttribution,
    ExposureEvent,
)
from extensions import db
from io import BytesIO
from datetime import date, datetime, timedelta
import os
from services.steps_attribution_service import ALGORITHM_V1
from services.health_service import compute_damage_penalty_points, compute_health_components, derive_care_tags
from utils.slugs import build_my_sneaker_slug


def _my_sneaker_url(sneaker_id, slug):
    return f"/my/sneakers/{sneaker_id}-{slug}"

def test_add_sneaker(test_client, auth, init_database):
    # 'init_database' already created one user and one sneaker
    user, _ = init_database
    auth.login(username=user.username, password='password123')

    sneaker_data = { 'brand': 'Test Brand 2', 'model': 'Test Model 2', 'image_option': 'url' }
    response = test_client.post('/add-sneaker', data=sneaker_data, follow_redirects=True)
    assert response.status_code == 200
    # UPDATED: Check for the new, correct flash message
    assert b"Sneaker added successfully!" in response.data

    added_sneaker = Sneaker.query.filter_by(model='Test Model 2').first()
    assert added_sneaker is not None

def test_edit_sneaker(test_client, auth, init_database):
    user, sneaker_to_edit = init_database
    auth.login(username=user.username, password='password123')

    sneaker_id = sneaker_to_edit.id
    updated_data = { 'brand': 'Updated Brand', 'model': 'Updated Model', 'image_option': 'url' }
    response = test_client.post(f'/edit-sneaker/{sneaker_id}', data=updated_data, follow_redirects=True)
    assert response.status_code == 200
    # UPDATED: Check for the new, correct flash message
    assert b"Sneaker updated successfully!" in response.data

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

    # POSTing with missing required fields (brand, model)
    invalid_sneaker_data = { 'size': '10', 'image_option': 'url' }
    # Make it an AJAX request so the server returns the 400 error
    response = test_client.post('/add-sneaker', data=invalid_sneaker_data, headers={'X-Requested-With': 'XMLHttpRequest'})
    # UPDATED: The route now correctly returns 400 on validation failure for AJAX
    assert response.status_code == 400
    json_response = response.get_json()
    assert json_response['status'] == 'error'

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
    assert response.status_code == 403

    # 4. Assert that the sneaker was NOT actually changed in the database
    unchanged_sneaker = db.session.get(Sneaker, sneaker_id_to_edit)
    assert unchanged_sneaker.brand == 'Initial Brand'

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

        message_from_server = flashed_messages[0][1]
        substring_to_find = "added to your rotation"
        lowercase_message = message_from_server.lower()
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


def test_update_last_worn_logs_wear_and_shows_cpw(test_client, auth, init_database, test_app):
    user, sneaker = init_database
    with test_app.app_context():
        user.preferred_currency = "USD"
        sneaker.purchase_price = 100
        sneaker.purchase_currency = "USD"
        db.session.commit()
        sneaker_slug = build_my_sneaker_slug(sneaker)

    auth.login(username=user.username, password='password123')
    response = test_client.post(
        f'/update-last-worn/{sneaker.id}',
        data={'new_last_worn': '2025-01-10'},
        headers={'X-Requested-With': 'XMLHttpRequest'}
    )
    assert response.status_code == 200

    with test_app.app_context():
        wear_count = db.session.query(SneakerWear).filter_by(sneaker_id=sneaker.id).count()
        assert wear_count == 1

    detail_response = test_client.get(_my_sneaker_url(sneaker.id, sneaker_slug))
    assert detail_response.status_code == 200
    assert b"Wear Count" in detail_response.data
    assert b"CPW" in detail_response.data
    assert b"$100.00" in detail_response.data

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


def test_collection_avg_resale_matches_release_by_normalized_sku(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username='resaleuser',
            email='resale@example.com',
            first_name='Resale',
            last_name='User',
            is_email_confirmed=True
        )
        user.set_password('password123')
        user.preferred_currency = "USD"
        db.session.add(user)
        db.session.commit()

        sneaker = Sneaker(
            brand='Nike',
            model='Air Presto Off-White',
            sku='aa3830-001',
            owner=user
        )
        db.session.add(sneaker)

        release = Release(
            sku='AA3830-001',
            name='Nike Air Presto Off-White',
            brand='Nike',
            release_date=date.today()
        )
        db.session.add(release)
        db.session.commit()

        offer = AffiliateOffer(
            release_id=release.id,
            retailer='stockx',
            base_url='https://stockx.com/nike-air-presto-off-white',
            offer_type='aftermarket',
            price=200,
            currency='USD',
            is_active=True
        )
        db.session.add(offer)
        db.session.commit()

    auth.login(username='resaleuser', password='password123')
    response = test_client.get('/my-collection')
    assert response.status_code == 200
    assert b"Avg Resale:" in response.data
    assert b"$200.00" in response.data


def test_rotation_avg_resale_matches_release_by_normalized_sku(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username='rotationresale',
            email='rotation@example.com',
            first_name='Rotation',
            last_name='User',
            is_email_confirmed=True
        )
        user.set_password('password123')
        user.preferred_currency = "USD"
        db.session.add(user)
        db.session.commit()

        sneaker = Sneaker(
            brand='Nike',
            model='Air Presto Off-White',
            sku='aa3830-001',
            in_rotation=True,
            owner=user
        )
        db.session.add(sneaker)

        release = Release(
            sku='AA3830-001',
            name='Nike Air Presto Off-White',
            brand='Nike',
            release_date=date.today()
        )
        db.session.add(release)
        db.session.commit()

        offer = AffiliateOffer(
            release_id=release.id,
            retailer='stockx',
            base_url='https://stockx.com/nike-air-presto-off-white',
            offer_type='aftermarket',
            price=200,
            currency='USD',
            is_active=True
        )
        db.session.add(offer)
        db.session.commit()

    auth.login(username='rotationresale', password='password123')
    response = test_client.get('/my-rotation')
    assert response.status_code == 200
    assert b"Avg Resale:" in response.data
    assert b"$200.00" in response.data


def test_sneaker_detail_shows_notes_and_back_link(test_client, auth, init_database):
    user, sneaker = init_database
    note = SneakerNote(sneaker_id=sneaker.id, body="Personal note about this pair.")
    db.session.add(note)
    sneaker.in_rotation = True
    db.session.commit()
    sneaker_slug = build_my_sneaker_slug(sneaker)

    auth.login(username=user.username, password='password123')
    response = test_client.get(f"{_my_sneaker_url(sneaker.id, sneaker_slug)}?source=rotation")

    assert response.status_code == 200
    assert b"Personal note about this pair." in response.data
    assert b"Back to Rotation" in response.data


def test_my_sneaker_detail_requires_owner(test_client, auth, test_app, another_user_in_db):
    with test_app.app_context():
        owner = User(
            username="owneruser",
            email="owner@example.com",
            first_name="Owner",
            last_name="User",
            is_email_confirmed=True,
        )
        owner.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Owner Pair", owner=owner)
        db.session.add_all([owner, sneaker])
        db.session.commit()
        sneaker_id = sneaker.id
        sneaker_slug = build_my_sneaker_slug(sneaker)

    auth.login(username=another_user_in_db.username, password="password789")
    response = test_client.get(_my_sneaker_url(sneaker_id, sneaker_slug))
    assert response.status_code == 404


def test_my_sneaker_detail_redirects_on_bad_slug(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="sluguser",
            email="sluguser@example.com",
            first_name="Slug",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Slug Pair", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()
        sneaker_id = sneaker.id
        username = user.username

    auth.login(username=username, password="password123")
    response = test_client.get(f"/my/sneakers/{sneaker_id}-wrong-slug")
    assert response.status_code in {301, 302}


def test_sneaker_detail_health_score_calculation(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="healthuser",
            email="health@example.com",
            first_name="Health",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(
            brand="Nike",
            model="Health Pair",
            owner=user,
            condition="Lightly Worn",
            starting_health=95.0,
        )
        sneaker.last_cleaned_at = datetime.utcnow() - timedelta(days=2)
        db.session.add_all([user, sneaker])
        db.session.commit()

        db.session.add(
            StepAttribution(
                user_id=user.id,
                sneaker_id=sneaker.id,
                bucket_granularity="day",
                bucket_start=datetime.utcnow() - timedelta(days=1),
                steps_attributed=1500,
                algorithm_version=ALGORITHM_V1,
            )
        )
        db.session.add(
            SneakerExposureAttribution(
                user_id=user.id,
                sneaker_id=sneaker.id,
                date_local=date.today(),
                wet_points=2.0,
                dirty_points=1.0,
            )
        )
        db.session.commit()
        sneaker_id = sneaker.id
        username = user.username
        sneaker_slug = build_my_sneaker_slug(sneaker)

    auth.login(username=username, password="password123")
    response = test_client.get(_my_sneaker_url(sneaker_id, sneaker_slug))

    assert response.status_code == 200
    assert b"91.9" in response.data
    assert b"Baseline" in response.data
    assert b"Purchase Condition" in response.data
    assert b"Purchase Condition: Lightly Worn" in response.data
    assert b"95.0" in response.data
    assert b"Wear (steps)" in response.data
    assert b"(1500 steps)" in response.data
    assert b"Cosmetic" in response.data
    assert b"Action:" not in response.data


def test_health_score_rebounds_after_clean_without_persistent_penalty(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="cleanrebound",
            email="cleanrebound@example.com",
            first_name="Clean",
            last_name="Rebound",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Rebound Pair", owner=user)
        sneaker.last_cleaned_at = datetime.utcnow() - timedelta(days=3)
        db.session.add_all([user, sneaker])
        db.session.commit()

        db.session.add(
            SneakerWear(sneaker_id=sneaker.id, worn_at=date.today())
        )
        db.session.add(
            SneakerExposureAttribution(
                user_id=user.id,
                sneaker_id=sneaker.id,
                date_local=date.today(),
                wet_points=2.0,
                dirty_points=1.0,
            )
        )
        db.session.add(
            StepAttribution(
                user_id=user.id,
                sneaker_id=sneaker.id,
                bucket_granularity="day",
                bucket_start=datetime.utcnow() - timedelta(days=1),
                steps_attributed=5000,
                algorithm_version=ALGORITHM_V1,
            )
        )
        db.session.commit()
        before_score = compute_health_components(sneaker, user.id, materials=[])["health_score"]
        sneaker_id = sneaker.id
        username = user.username

    auth.login(username=username, password="password123")
    response = test_client.post(
        f"/sneakers/{sneaker_id}/mark-cleaned",
        data={"notes_action": "keep"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with test_app.app_context():
        sneaker = db.session.get(Sneaker, sneaker_id)
        after_score = compute_health_components(sneaker, sneaker.user_id, materials=[])["health_score"]
        assert after_score > before_score
        assert float(sneaker.persistent_stain_points or 0.0) == 0.0


def test_persistent_stain_penalty_when_not_removed(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="stainpenalty",
            email="stainpenalty@example.com",
            first_name="Stain",
            last_name="Penalty",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Stain Pair", owner=user)
        sneaker.last_cleaned_at = datetime.utcnow() - timedelta(days=2)
        db.session.add_all([user, sneaker])
        db.session.commit()

        wear_date = date.today()
        db.session.add(SneakerWear(sneaker_id=sneaker.id, worn_at=wear_date))
        db.session.add(
            ExposureEvent(
                user_id=user.id,
                date_local=wear_date,
                got_dirty=True,
                dirty_severity=2,
                stain_flag=True,
                stain_severity=3,
            )
        )
        db.session.commit()
        sneaker_id = sneaker.id
        username = user.username

    auth.login(username=username, password="password123")
    response = test_client.post(
        f"/sneakers/{sneaker_id}/mark-cleaned",
        data={"notes_action": "keep", "stain_removed": "no"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with test_app.app_context():
        sneaker = db.session.get(Sneaker, sneaker_id)
        assert float(sneaker.persistent_stain_points or 0.0) > 0.0
        clean_event = SneakerCleanEvent.query.filter_by(sneaker_id=sneaker.id).first()
        assert clean_event is not None
        assert clean_event.stain_removed is False


def test_suede_prompt_shown_on_clean_modal(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="suedeuser",
            email="suede@example.com",
            first_name="Suede",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Suede Pair", sku="SU-123", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()

        db.session.add(
            SneakerDB(
                sku="SU-123",
                brand="Nike",
                model_name="Suede Pair",
                materials_json='[\"Suede\"]',
                primary_material="Suede",
            )
        )
        db.session.commit()
        sneaker_id = sneaker.id
        username = user.username
        sneaker_slug = build_my_sneaker_slug(sneaker)

    auth.login(username=username, password="password123")
    response = test_client.get(_my_sneaker_url(sneaker_id, sneaker_slug))
    assert response.status_code == 200
    assert b"lasting impact on suede/nubuck" in response.data


def test_health_snapshot_created_on_wear_and_clean(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="snapshotuser",
            email="snapshot@example.com",
            first_name="Snap",
            last_name="Shot",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Snapshot Pair", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()
        sneaker_id = sneaker.id
        username = user.username

    auth.login(username=username, password="password123")
    response = test_client.post(
        f"/update-last-worn/{sneaker_id}",
        data={
            "new_last_worn": date.today().isoformat(),
            "exposure_update": "1",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with test_app.app_context():
        snapshots = SneakerHealthSnapshot.query.filter_by(sneaker_id=sneaker_id).all()
        assert len(snapshots) == 1
        assert snapshots[0].reason == "wear"

    response = test_client.post(
        f"/sneakers/{sneaker_id}/mark-cleaned",
        data={"notes_action": "keep"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with test_app.app_context():
        snapshots = SneakerHealthSnapshot.query.filter_by(sneaker_id=sneaker_id).order_by(SneakerHealthSnapshot.recorded_at.asc()).all()
        assert len(snapshots) == 2
        assert snapshots[1].reason == "clean"


def test_health_history_only_accessible_to_owner(test_client, auth, test_app, another_user_in_db):
    with test_app.app_context():
        user = User(
            username="historyowner",
            email="historyowner@example.com",
            first_name="History",
            last_name="Owner",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="History Pair", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()
        sneaker_id = sneaker.id

    auth.login(username=another_user_in_db.username, password="password789")
    response = test_client.get(f"/sneakers/{sneaker_id}/health-history")
    assert response.status_code == 404


def test_health_steps_penalty_gentle_for_10k_steps(test_app):
    with test_app.app_context():
        user = User(
            username="steps10k",
            email="steps10k@example.com",
            first_name="Steps",
            last_name="TenK",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Steps Pair", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()

        db.session.add(
            StepAttribution(
                user_id=user.id,
                sneaker_id=sneaker.id,
                bucket_granularity="day",
                bucket_start=datetime.utcnow() - timedelta(days=1),
                steps_attributed=10394,
                algorithm_version=ALGORITHM_V1,
            )
        )
        db.session.commit()

        health = compute_health_components(sneaker, user.id, materials=[])
        assert health["health_score"] >= 99.0


def test_health_steps_penalty_caps_at_restore_threshold(test_app):
    with test_app.app_context():
        user = User(
            username="stepscap",
            email="stepscap@example.com",
            first_name="Steps",
            last_name="Cap",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Cap Pair", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()

        db.session.add(
            StepAttribution(
                user_id=user.id,
                sneaker_id=sneaker.id,
                bucket_granularity="day",
                bucket_start=datetime.utcnow() - timedelta(days=1),
                steps_attributed=750000,
                algorithm_version=ALGORITHM_V1,
            )
        )
        db.session.commit()

        health = compute_health_components(sneaker, user.id, materials=[])
        assert 49.5 <= health["health_score"] <= 50.5


def test_health_steps_penalty_caps_above_threshold(test_app):
    with test_app.app_context():
        user = User(
            username="stepscapplus",
            email="stepscapplus@example.com",
            first_name="Steps",
            last_name="CapPlus",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Cap Plus Pair", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()

        db.session.add(
            StepAttribution(
                user_id=user.id,
                sneaker_id=sneaker.id,
                bucket_granularity="day",
                bucket_start=datetime.utcnow() - timedelta(days=1),
                steps_attributed=1000000,
                algorithm_version=ALGORITHM_V1,
            )
        )
        db.session.commit()

        health = compute_health_components(sneaker, user.id, materials=[])
        assert 49.5 <= health["health_score"] <= 50.5


def test_exposure_before_clean_is_excluded_from_health(test_app):
    with test_app.app_context():
        user = User(
            username="exposureclean",
            email="exposureclean@example.com",
            first_name="Exposure",
            last_name="Clean",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Exposure Pair", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()

        dirty_day = date.today() - timedelta(days=2)
        db.session.add(
            SneakerExposureAttribution(
                user_id=user.id,
                sneaker_id=sneaker.id,
                date_local=dirty_day,
                wet_points=0.0,
                dirty_points=3.0,
            )
        )
        db.session.commit()

        health_before = compute_health_components(sneaker, user.id, materials=[])
        assert health_before["health_score"] < 100.0

        sneaker.last_cleaned_at = datetime.utcnow()
        db.session.commit()

        health_after = compute_health_components(sneaker, user.id, materials=[])
        assert health_after["health_score"] == 100.0


def test_persistent_penalties_reduce_health_after_clean(test_app):
    with test_app.app_context():
        user = User(
            username="persistentpenalty",
            email="persistentpenalty@example.com",
            first_name="Persistent",
            last_name="Penalty",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(
            brand="Nike",
            model="Persistent Pair",
            owner=user,
            persistent_stain_points=2.0,
            persistent_material_damage_points=1.5,
        )
        sneaker.last_cleaned_at = datetime.utcnow()
        db.session.add_all([user, sneaker])
        db.session.commit()

        health = compute_health_components(sneaker, user.id, materials=[])
        assert health["health_score"] == 96.5


def test_hygiene_penalty_by_wears(test_app):
    with test_app.app_context():
        user = User(
            username="hygieneuser",
            email="hygieneuser@example.com",
            first_name="Hygiene",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Hygiene Pair", owner=user)
        sneaker.last_cleaned_at = datetime.utcnow()
        db.session.add_all([user, sneaker])
        db.session.commit()

        health = compute_health_components(sneaker, user.id, materials=[])
        assert health["hygiene_penalty"] == 0.0

        for offset in range(5):
            db.session.add(SneakerWear(sneaker_id=sneaker.id, worn_at=date.today() + timedelta(days=offset + 1)))
        db.session.commit()

        health = compute_health_components(sneaker, user.id, materials=[])
        assert round(health["hygiene_penalty"], 1) == 2.5

        for offset in range(5):
            db.session.add(SneakerWear(sneaker_id=sneaker.id, worn_at=date.today() + timedelta(days=offset + 10)))
        db.session.commit()

        health = compute_health_components(sneaker, user.id, materials=[])
        assert round(health["hygiene_penalty"], 1) == 5.0


def test_recommendation_restore_for_steps_only(test_app):
    with test_app.app_context():
        user = User(
            username="recosteps",
            email="recosteps@example.com",
            first_name="Reco",
            last_name="Steps",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Reco Steps Pair", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()

        db.session.add(
            StepAttribution(
                user_id=user.id,
                sneaker_id=sneaker.id,
                bucket_granularity="day",
                bucket_start=datetime.utcnow() - timedelta(days=1),
                steps_attributed=750000,
                algorithm_version=ALGORITHM_V1,
            )
        )
        db.session.commit()

        health = compute_health_components(sneaker, user.id, materials=[])
        assert health["recommendation_label"] == "Restore"


def test_recommendation_clean_from_hygiene(test_app):
    with test_app.app_context():
        user = User(
            username="recohygiene",
            email="recohygiene@example.com",
            first_name="Reco",
            last_name="Hygiene",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Reco Clean Pair", owner=user)
        sneaker.last_cleaned_at = datetime.utcnow() - timedelta(days=2)
        db.session.add_all([user, sneaker])
        db.session.commit()

        for offset in range(5):
            db.session.add(
                SneakerWear(
                    sneaker_id=sneaker.id,
                    worn_at=date.today() + timedelta(days=offset),
                )
            )
        db.session.commit()

        health = compute_health_components(sneaker, user.id, materials=[])
        assert health["recommendation_label"] == "Clean"


def test_recommendation_repair_with_active_damage(test_app):
    with test_app.app_context():
        user = User(
            username="recorepair",
            email="recorepair@example.com",
            first_name="Reco",
            last_name="Repair",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Reco Repair Pair", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()

        db.session.add(
            SneakerDamageEvent(
                sneaker_id=sneaker.id,
                user_id=user.id,
                damage_type="tear_upper",
                severity=2,
                health_penalty_points=15.0,
                is_active=True,
            )
        )
        db.session.commit()

        health = compute_health_components(sneaker, user.id, materials=[])
        assert health["recommendation_label"] == "Repair"


def test_recommendation_no_repair_for_light_outsole_wear(test_app):
    with test_app.app_context():
        user = User(
            username="recosole",
            email="recosole@example.com",
            first_name="Reco",
            last_name="Outsole",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Reco Outsole Pair", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()

        db.session.add(
            SneakerDamageEvent(
                sneaker_id=sneaker.id,
                user_id=user.id,
                damage_type="outsole_wear",
                severity=1,
                health_penalty_points=10.0,
                is_active=True,
            )
        )
        db.session.commit()

        health = compute_health_components(sneaker, user.id, materials=[])
        assert health["recommendation_label"] in {None, "Minor wear logged"}


def test_recommendation_repair_for_sole_separation(test_app):
    with test_app.app_context():
        user = User(
            username="recosolefix",
            email="recosolefix@example.com",
            first_name="Reco",
            last_name="Separation",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Reco Separation Pair", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()

        db.session.add(
            SneakerDamageEvent(
                sneaker_id=sneaker.id,
                user_id=user.id,
                damage_type="sole_separation",
                severity=1,
                health_penalty_points=10.0,
                is_active=True,
            )
        )
        db.session.commit()

        health = compute_health_components(sneaker, user.id, materials=[])
        assert health["recommendation_label"] == "Repair"


def test_recommendation_no_repair_for_cosmetic_only(test_app):
    with test_app.app_context():
        user = User(
            username="recocosmetic",
            email="recocosmetic@example.com",
            first_name="Reco",
            last_name="Cosmetic",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Reco Cosmetic Pair", owner=user)
        sneaker.last_cleaned_at = datetime.utcnow()
        db.session.add_all([user, sneaker])
        db.session.commit()

        db.session.add(
            SneakerExposureAttribution(
                user_id=user.id,
                sneaker_id=sneaker.id,
                date_local=date.today() + timedelta(days=1),
                wet_points=2.0,
                dirty_points=2.0,
            )
        )
        db.session.commit()

        health = compute_health_components(sneaker, user.id, materials=[])
        assert health["recommendation_label"] != "Repair"


def test_status_thresholds_and_override(test_app):
    with test_app.app_context():
        user = User(
            username="statususer",
            email="status@example.com",
            first_name="Status",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("password123")

        healthy = Sneaker(brand="Nike", model="Healthy Pair", owner=user, starting_health=95.0)
        ok_pair = Sneaker(brand="Nike", model="OK Pair", owner=user, starting_health=85.0)
        watch_pair = Sneaker(brand="Nike", model="Monitor Pair", owner=user, starting_health=70.0)
        needs_pair = Sneaker(brand="Nike", model="Needs Pair", owner=user, starting_health=60.0)
        override_pair = Sneaker(brand="Nike", model="Override Pair", owner=user, starting_health=92.0)
        db.session.add_all([user, healthy, ok_pair, watch_pair, needs_pair, override_pair])
        db.session.commit()

        db.session.add(
            SneakerDamageEvent(
                sneaker_id=override_pair.id,
                user_id=user.id,
                damage_type="sole_separation",
                severity=1,
                health_penalty_points=10.0,
                is_active=True,
            )
        )
        db.session.commit()

        assert compute_health_components(healthy, user.id, materials=[])["status_label"] == "Healthy"
        assert compute_health_components(ok_pair, user.id, materials=[])["status_label"] == "OK"
        assert compute_health_components(watch_pair, user.id, materials=[])["status_label"] == "Monitor"
        assert compute_health_components(needs_pair, user.id, materials=[])["status_label"] == "Needs attention"
        assert compute_health_components(override_pair, user.id, materials=[])["status_label"] == "Needs attention"


def test_breakdown_sum_matches_health_score(test_app):
    with test_app.app_context():
        user = User(
            username="breakdownuser",
            email="breakdown@example.com",
            first_name="Break",
            last_name="Down",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(
            brand="Nike",
            model="Break Pair",
            owner=user,
            persistent_stain_points=1.0,
            persistent_material_damage_points=2.0,
            persistent_structural_damage_points=3.0,
        )
        sneaker.last_cleaned_at = datetime.utcnow()
        db.session.add_all([user, sneaker])
        db.session.commit()

        db.session.add(
            SneakerExposureAttribution(
                user_id=user.id,
                sneaker_id=sneaker.id,
                date_local=date.today() + timedelta(days=1),
                wet_points=1.0,
                dirty_points=1.0,
            )
        )
        db.session.commit()

        health = compute_health_components(sneaker, user.id, materials=[])
        total_penalty = (
            health["wear_penalty"]
            + health["cosmetic_penalty"]
            + health["structural_penalty"]
            + health["hygiene_penalty"]
        )
        assert health["health_score"] == max(0.0, min(100.0, round(100.0 - total_penalty, 1)))


def test_confidence_scoring_high_when_data_present(test_app):
    with test_app.app_context():
        user = User(
            username="confidenceuser",
            email="confidence@example.com",
            first_name="Con",
            last_name="Fidence",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Confidence Pair", owner=user)
        sneaker.last_cleaned_at = datetime.utcnow()
        db.session.add_all([user, sneaker])
        db.session.commit()

        for offset in range(20):
            start = datetime.utcnow() - timedelta(days=offset + 1)
            db.session.add(
                StepBucket(
                    user_id=user.id,
                    source="apple_health",
                    granularity="day",
                    bucket_start=start,
                    bucket_end=start + timedelta(days=1),
                    steps=1000,
                    timezone="Europe/London",
                )
            )
        db.session.add(
            StepAttribution(
                user_id=user.id,
                sneaker_id=sneaker.id,
                bucket_granularity="day",
                bucket_start=datetime.utcnow() - timedelta(days=1),
                steps_attributed=1200,
                algorithm_version=ALGORITHM_V1,
            )
        )
        db.session.add(
            ExposureEvent(
                user_id=user.id,
                date_local=date.today(),
                got_dirty=True,
                dirty_severity=1,
            )
        )
        db.session.add(SneakerWear(sneaker_id=sneaker.id, worn_at=date.today()))
        db.session.commit()

        health = compute_health_components(sneaker, user.id, materials=[])
        assert health["confidence_label"] in {"Medium", "High"}


def test_care_tags_from_materials():
    tags = derive_care_tags(["Suede", "Mesh", "Rubber"])
    assert "suede_or_nubuck" in tags
    assert "knit_mesh" in tags
    assert "rubber_foam" in tags


def test_reporting_damage_lowers_health_and_creates_snapshot(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="damageuser",
            email="damageuser@example.com",
            first_name="Damage",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Damage Pair", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()
        sneaker_id = sneaker.id
        username = user.username

    auth.login(username=username, password="password123")
    response = test_client.post(
        f"/sneakers/{sneaker_id}/damage",
        data={
            "damage_type": "sole_separation",
            "severity": "2",
            "notes": "Sole coming apart",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with test_app.app_context():
        sneaker = db.session.get(Sneaker, sneaker_id)
        assert sneaker.persistent_structural_damage_points > 0.0
        damage = SneakerDamageEvent.query.filter_by(sneaker_id=sneaker_id, is_active=True).first()
        assert damage is not None
        snapshot = SneakerHealthSnapshot.query.filter_by(sneaker_id=sneaker_id, reason="damage").first()
        assert snapshot is not None


def test_repair_resolves_damage_and_creates_expense_and_snapshot(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="repairuser",
            email="repairuser@example.com",
            first_name="Repair",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Repair Pair", owner=user, purchase_price=100, purchase_currency="GBP")
        db.session.add_all([user, sneaker])
        db.session.commit()

        db.session.add(
            SneakerDamageEvent(
                sneaker_id=sneaker.id,
                user_id=user.id,
                damage_type="scuff",
                severity=3,
                health_penalty_points=10.0,
                is_active=True,
            )
        )
        sneaker.persistent_structural_damage_points = 10.0
        db.session.commit()
        sneaker_id = sneaker.id
        username = user.username

    auth.login(username=username, password="password123")
    response = test_client.post(
        f"/sneakers/{sneaker_id}/repair",
        data={
            "repair_kind": "repair",
            "repair_type": "glue",
            "provider": "Local cobbler",
            "cost_amount": "25.00",
            "cost_currency": "GBP",
            "resolved_all_active_damage": "y",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with test_app.app_context():
        sneaker = db.session.get(Sneaker, sneaker_id)
        assert sneaker.persistent_structural_damage_points == 0.0
        assert SneakerDamageEvent.query.filter_by(sneaker_id=sneaker_id, is_active=True).count() == 0
        repair = SneakerRepairEvent.query.filter_by(sneaker_id=sneaker_id).first()
        assert repair is not None
        assert SneakerRepairResolvedDamage.query.filter_by(repair_event_id=repair.id).count() == 1
        expense = SneakerExpense.query.filter_by(sneaker_id=sneaker_id, category="repair").first()
        assert expense is not None
        snapshot = SneakerHealthSnapshot.query.filter_by(sneaker_id=sneaker_id, reason="repair").first()
        assert snapshot is not None


def test_restoration_sets_baseline_to_90(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="restoreuser",
            email="restoreuser@example.com",
            first_name="Restore",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Restore Pair", owner=user, starting_health=80.0)
        db.session.add_all([user, sneaker])
        db.session.commit()
        sneaker_id = sneaker.id
        username = user.username

    auth.login(username=username, password="password123")
    response = test_client.post(
        f"/sneakers/{sneaker_id}/repair",
        data={
            "repair_kind": "restoration",
            "provider": "self",
            "resolved_all_active_damage": "y",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with test_app.app_context():
        sneaker = db.session.get(Sneaker, sneaker_id)
        assert sneaker.starting_health == 90.0
        repair = SneakerRepairEvent.query.filter_by(sneaker_id=sneaker_id).first()
        assert repair.baseline_delta_applied == 10.0


def test_repair_no_damage_allows_diy_with_area(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="repairnodamage",
            email="repairnodamage@example.com",
            first_name="Repair",
            last_name="NoDamage",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="NoDamage Pair", owner=user, starting_health=80.0)
        db.session.add_all([user, sneaker])
        db.session.commit()
        sneaker_id = sneaker.id
        username = user.username

    auth.login(username=username, password="password123")
    response = test_client.post(
        f"/sneakers/{sneaker_id}/repair",
        data={
            "repair_kind": "repair",
            "repair_type": "stitching",
            "provider": "self",
            "repair_area": "upper",
            "resolved_all_active_damage": "y",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with test_app.app_context():
        sneaker = db.session.get(Sneaker, sneaker_id)
        assert sneaker.starting_health == 84.0
        repair = SneakerRepairEvent.query.filter_by(sneaker_id=sneaker_id).order_by(SneakerRepairEvent.id.desc()).first()
        assert repair.repair_area == "upper"
        assert repair.baseline_delta_applied == 4.0


def test_confidence_bonus_by_provider(test_app):
    with test_app.app_context():
        user = User(
            username="confidenceprovider",
            email="confidenceprovider@example.com",
            first_name="Con",
            last_name="Provider",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Provider Pair", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()

        diy_event = SneakerRepairEvent(
            sneaker_id=sneaker.id,
            user_id=user.id,
            repair_kind="repair",
            repair_type="stitching",
            provider="self",
        )
        pro_event = SneakerRepairEvent(
            sneaker_id=sneaker.id,
            user_id=user.id,
            repair_kind="repair",
            repair_type="stitching",
            provider="local_cobbler",
        )
        db.session.add_all([diy_event, pro_event])
        db.session.commit()

        # Remove pro event to measure DIY bonus
        db.session.delete(pro_event)
        db.session.commit()
        diy_health = compute_health_components(sneaker, user.id, materials=[])

        # Swap to pro event only
        db.session.delete(diy_event)
        db.session.add(
            SneakerRepairEvent(
                sneaker_id=sneaker.id,
                user_id=user.id,
                repair_kind="repair",
                repair_type="stitching",
                provider="local_cobbler",
            )
        )
        db.session.commit()
        pro_health = compute_health_components(sneaker, user.id, materials=[])

        assert pro_health["confidence_score"] >= diy_health["confidence_score"]


def test_partial_repair_resolves_only_selected_damage(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="partialrepair",
            email="partialrepair@example.com",
            first_name="Partial",
            last_name="Repair",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Partial Pair", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()

        damage_one = SneakerDamageEvent(
            sneaker_id=sneaker.id,
            user_id=user.id,
            damage_type="tear_knit",
            severity=1,
            health_penalty_points=8.0,
            is_active=True,
        )
        damage_two = SneakerDamageEvent(
            sneaker_id=sneaker.id,
            user_id=user.id,
            damage_type="midsole_crumble",
            severity=1,
            health_penalty_points=15.0,
            is_active=True,
        )
        db.session.add_all([damage_one, damage_two])
        sneaker.persistent_structural_damage_points = 23.0
        db.session.commit()
        sneaker_id = sneaker.id
        username = user.username
        resolve_id = damage_one.id

    auth.login(username=username, password="password123")
    response = test_client.post(
        f"/sneakers/{sneaker_id}/repair",
        data={
            "repair_kind": "repair",
            "repair_type": "stitch",
            "resolved_all_active_damage": "",
            "resolved_damage_ids": str(resolve_id),
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with test_app.app_context():
        sneaker = db.session.get(Sneaker, sneaker_id)
        remaining = SneakerDamageEvent.query.filter_by(sneaker_id=sneaker_id, is_active=True).all()
        assert len(remaining) == 1
        assert remaining[0].health_penalty_points == 15.0
        assert sneaker.persistent_structural_damage_points == 15.0
        repair = SneakerRepairEvent.query.filter_by(sneaker_id=sneaker_id).first()
        assert repair is not None
        links = SneakerRepairResolvedDamage.query.filter_by(repair_event_id=repair.id).all()
        assert len(links) == 1
        assert links[0].damage_event_id == resolve_id


def test_cpw_includes_expenses(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="cpwexpense",
            email="cpwexpense@example.com",
            first_name="Cpw",
            last_name="Expense",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="CPW Pair", owner=user, purchase_price=100, purchase_currency="GBP")
        db.session.add_all([user, sneaker])
        db.session.commit()

        db.session.add(SneakerWear(sneaker_id=sneaker.id, worn_at=date.today()))
        db.session.add(
            SneakerExpense(
                sneaker_id=sneaker.id,
                user_id=user.id,
                category="repair",
                amount=50,
                currency="GBP",
            )
        )
        db.session.commit()
        sneaker_id = sneaker.id
        username = user.username
        sneaker_slug = build_my_sneaker_slug(sneaker)

    auth.login(username=username, password="password123")
    response = test_client.get(_my_sneaker_url(sneaker_id, sneaker_slug))
    assert response.status_code == 200
    assert b"CPW" in response.data
    assert b"150.00" in response.data


def test_damage_and_repair_authorization(test_client, auth, test_app, another_user_in_db):
    with test_app.app_context():
        user = User(
            username="damageowner",
            email="damageowner@example.com",
            first_name="Damage",
            last_name="Owner",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Auth Pair", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()
        sneaker_id = sneaker.id

    auth.login(username=another_user_in_db.username, password="password789")
    response = test_client.post(
        f"/sneakers/{sneaker_id}/damage",
        data={"damage_type": "scuff", "severity": "1"},
        follow_redirects=True,
    )
    assert response.status_code == 404

    response = test_client.post(
        f"/sneakers/{sneaker_id}/repair",
        data={"repair_kind": "repair", "repair_type": "glue", "resolved_all_active_damage": "y"},
        follow_redirects=True,
    )
    assert response.status_code == 404


def test_repair_other_requires_text(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="repairother",
            email="repairother@example.com",
            first_name="Repair",
            last_name="Other",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Other Pair", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()
        sneaker_id = sneaker.id
        username = user.username

    auth.login(username=username, password="password123")
    response = test_client.post(
        f"/sneakers/{sneaker_id}/repair",
        data={
            "repair_kind": "repair",
            "repair_type": "other",
            "provider": "other",
            "resolved_all_active_damage": "y",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with test_app.app_context():
        assert SneakerRepairEvent.query.filter_by(sneaker_id=sneaker_id).count() == 0


def test_repair_other_saves_custom_values(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="repairotherok",
            email="repairotherok@example.com",
            first_name="Repair",
            last_name="OtherOk",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Other Ok Pair", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()
        sneaker_id = sneaker.id
        username = user.username

    auth.login(username=username, password="password123")
    response = test_client.post(
        f"/sneakers/{sneaker_id}/repair",
        data={
            "repair_kind": "repair",
            "repair_type": "other",
            "repair_type_other": "Custom stitch",
            "provider": "other",
            "provider_other": "My workshop",
            "repair_area": "upper",
            "resolved_all_active_damage": "y",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with test_app.app_context():
        repair = SneakerRepairEvent.query.filter_by(sneaker_id=sneaker_id).first()
        assert repair is not None
        assert repair.repair_type == "other"
        assert repair.repair_type_other == "Custom stitch"
        assert repair.provider == "other"
        assert repair.provider_other == "My workshop"


def test_repair_dropdown_saves_values(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="repairdropdown",
            email="repairdropdown@example.com",
            first_name="Repair",
            last_name="Dropdown",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Dropdown Pair", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()
        sneaker_id = sneaker.id
        username = user.username

    auth.login(username=username, password="password123")
    response = test_client.post(
        f"/sneakers/{sneaker_id}/repair",
        data={
            "repair_kind": "repair",
            "repair_type": "stitching",
            "provider": "local_cobbler",
            "repair_area": "upper",
            "resolved_all_active_damage": "y",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with test_app.app_context():
        repair = SneakerRepairEvent.query.filter_by(sneaker_id=sneaker_id).first()
        assert repair is not None
        assert repair.repair_type == "stitching"
        assert repair.repair_type_other is None
        assert repair.provider == "local_cobbler"
        assert repair.provider_other is None


def test_damage_other_requires_details(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="damageother",
            email="damageother@example.com",
            first_name="Damage",
            last_name="Other",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Damage Other Pair", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()
        sneaker_id = sneaker.id
        username = user.username

    auth.login(username=username, password="password123")
    response = test_client.post(
        f"/sneakers/{sneaker_id}/damage",
        data={
            "damage_type": "other",
            "severity": "2",
            "notes": "",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with test_app.app_context():
        assert SneakerDamageEvent.query.filter_by(sneaker_id=sneaker_id).count() == 0


def test_damage_type_normalization_for_legacy_values(test_app):
    with test_app.app_context():
        user = User(
            username="damagelegacy",
            email="damagelegacy@example.com",
            first_name="Damage",
            last_name="Legacy",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Legacy Pair", owner=user)
        db.session.add_all([user, sneaker])
        db.session.commit()

        legacy_damage = SneakerDamageEvent(
            sneaker_id=sneaker.id,
            user_id=user.id,
            damage_type="Scuff",
            severity=2,
            health_penalty_points=0.0,
            is_active=True,
        )
        db.session.add(legacy_damage)
        db.session.commit()

        penalty = compute_damage_penalty_points(legacy_damage.damage_type, legacy_damage.severity)
        assert penalty == 6.0


def test_damage_penalties_for_new_types():
    cases = [
        ("tear_upper", 1, 8.0),
        ("tear_upper", 2, 15.0),
        ("tear_upper", 3, 25.0),
        ("upper_scuff", 2, 8.0),
        ("upper_paint_chip", 3, 15.0),
        ("sole_separation", 1, 10.0),
        ("midsole_crumble", 2, 30.0),
        ("midsole_scuff", 3, 10.0),
        ("midsole_paint_chip", 1, 4.0),
        ("outsole_wear", 2, 20.0),
        ("other", 3, 15.0),
    ]
    for damage_type, severity, expected in cases:
        assert compute_damage_penalty_points(damage_type, severity) == expected


def test_starting_health_from_condition_on_create(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="conditionuser",
            email="conditionuser@example.com",
            first_name="Condition",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()
        username = user.username

    auth.login(username=username, password="password123")
    response = test_client.post(
        "/add-sneaker",
        data={
            "brand": "Nike",
            "model": "Condition Pair",
            "condition": "Beater",
            "image_option": "url",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with test_app.app_context():
        sneaker = Sneaker.query.filter_by(model="Condition Pair").first()
        assert sneaker is not None
        assert sneaker.starting_health == 70.0


def test_health_uses_starting_health_baseline(test_app):
    with test_app.app_context():
        user = User(
            username="baselineuser",
            email="baseline@example.com",
            first_name="Base",
            last_name="Line",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(
            brand="Nike",
            model="Baseline Pair",
            owner=user,
            starting_health=85.0,
        )
        db.session.add_all([user, sneaker])
        db.session.commit()

        health = compute_health_components(sneaker, user.id, materials=[])
        assert health["health_score"] == 85.0


def test_edit_condition_updates_starting_health_and_snapshot(test_client, auth, test_app):
    with test_app.app_context():
        user = User(
            username="editcondition",
            email="editcondition@example.com",
            first_name="Edit",
            last_name="Condition",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        sneaker = Sneaker(brand="Nike", model="Edit Pair", owner=user, condition="Deadstock", starting_health=100.0)
        db.session.add_all([user, sneaker])
        db.session.commit()
        sneaker_id = sneaker.id
        username = user.username

    auth.login(username=username, password="password123")
    response = test_client.post(
        f"/edit-sneaker/{sneaker_id}",
        data={
            "brand": "Nike",
            "model": "Edit Pair",
            "condition": "Heavily Worn",
            "image_option": "url",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with test_app.app_context():
        sneaker = db.session.get(Sneaker, sneaker_id)
        assert sneaker.starting_health == 85.0
        snapshot = SneakerHealthSnapshot.query.filter_by(
            sneaker_id=sneaker_id, reason="purchase_condition_update"
        ).first()
        assert snapshot is not None


def test_card_does_not_show_condition(test_client, auth, init_database):
    user, sneaker = init_database
    sneaker.condition = "Deadstock"
    db.session.commit()
    auth.login(username=user.username, password="password123")
    response = test_client.get("/my-collection")
    assert response.status_code == 200
    assert b"Condition:" not in response.data
