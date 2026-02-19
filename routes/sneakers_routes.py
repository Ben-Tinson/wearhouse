# routes/sneakers_routes.py
import os
import uuid
import requests
from datetime import date
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app, abort # <-- ADD current_app
from flask_login import login_required, current_user
from sqlalchemy import or_, asc, desc, func
from werkzeug.utils import secure_filename
from utils import allowed_file
from extensions import db
from models import User, Sneaker, SneakerDB
from forms import SneakerForm, EmptyForm
from services.kicks_client import KicksClient
from services.sneaker_lookup_service import lookup_or_fetch_sneaker

sneakers_bp = Blueprint('sneakers', __name__)

def get_sort_order(sort_by, order):
    # This helper can contain your if/elif block to determine the sort criteria
    # For now, we'll keep the logic inside the main function.
    pass

# --- Sneaker Collection Routes ---

# My Collection Route (Formerly Dashboard)

# In routes/sneakers_routes.py

@sneakers_bp.route('/my-collection')
@login_required
def dashboard():
    is_ajax = "X-Requested-With" in request.headers and request.headers['X-Requested-With'] == 'XMLHttpRequest'
    
    # --- 1. Get Parameters & Define State ---
    sort_by_param = request.args.get('sort_by')
    order_param = request.args.get('order')
    filter_brand_param = request.args.get('filter_brand')
    search_term_param = request.args.get('search_term')

    sort_active_in_url = bool(sort_by_param)
    
    sortable_columns = ['id', 'brand', 'model', 'purchase_date', 'last_worn_date', 'purchase_price']
    effective_sort_by = sort_by_param if sort_by_param in sortable_columns else 'purchase_date'
    default_order = 'asc' if effective_sort_by in ['brand', 'model'] else 'desc'
    effective_order = order_param if order_param in ['asc', 'desc'] else default_order

    current_filter_brand = filter_brand_param.strip() if (filter_brand_param and filter_brand_param.lower() != 'all') else None
    current_search_term = search_term_param.strip() if (search_term_param and search_term_param.strip()) else None

    # --- 2. Build the Main Query with Filters ---
    query = Sneaker.query.filter_by(user_id=current_user.id)

    if current_filter_brand:
        query = query.filter(Sneaker.brand == current_filter_brand)

    if current_search_term:
        keywords = current_search_term.split()
        search_conditions = [or_(Sneaker.brand.ilike(f"%{k}%"), Sneaker.model.ilike(f"%{k}%"), Sneaker.colorway.ilike(f"%{k}%")) for k in keywords if k]
        if search_conditions:
            query = query.filter(*search_conditions)
    
    # --- 3. Apply Sorting ---
    sort_column = getattr(Sneaker, effective_sort_by, Sneaker.id)
    if effective_sort_by in ['brand', 'model', 'colorway']:
        sort_expression = sort_column.collate('NOCASE')
    else:
        sort_expression = sort_column

    if effective_order == 'desc':
        query = query.order_by(sort_expression.desc().nullslast(), Sneaker.id.desc())
    else:
        query = query.order_by(sort_expression.asc().nullsfirst(), Sneaker.id.desc())
        
    user_sneakers_list = query.all()

    if sort_active_in_url:
        # Use a lambda function for robust, case-insensitive sorting that handles None
        is_reverse = (effective_order == 'desc')
        def sort_key(sneaker):
            val = getattr(sneaker, effective_sort_by)
            if val is None:
                return (1, None) # Group None values together
            if isinstance(val, str):
                return (0, val.lower()) # Sort strings case-insensitively
            return (0, val) # Sort other types normally

        user_sneakers_list.sort(key=sort_key, reverse=is_reverse)
    
    displayed_count = len(user_sneakers_list)

    # --- 4. Calculate All Stats & Dropdown Data ---
    base_query = Sneaker.query.filter_by(user_id=current_user.id)
    overall_total_count = base_query.count()
    total_value = float(base_query.with_entities(func.sum(Sneaker.purchase_price)).scalar() or 0.0)
    brand_distribution = base_query.with_entities(Sneaker.brand, func.count(Sneaker.brand)).filter(Sneaker.brand.isnot(None)).group_by(Sneaker.brand).order_by(func.count(Sneaker.brand).desc()).all()
    most_owned_brand = brand_distribution[0][0] if brand_distribution else "N/A"
    brand_labels = [item[0] for item in brand_distribution]
    brand_data = [item[1] for item in brand_distribution]
    brands_for_filter = [b[0] for b in base_query.with_entities(Sneaker.brand).distinct().order_by(Sneaker.brand).all() if b[0]]
    brand_specific_count = base_query.filter(Sneaker.brand == current_filter_brand).count() if current_filter_brand else None
    in_rotation_count = base_query.filter_by(in_rotation=True).count()

    # --- 5. Prepare Final Context Dictionary ---
    context = {
        "sneakers": user_sneakers_list, "displayed_count": len(user_sneakers_list),
        "overall_total_count": overall_total_count, "brand_specific_count": brand_specific_count,
        "total_value": total_value, "most_owned_brand": most_owned_brand, "in_rotation_count": in_rotation_count,
        "brand_labels": brand_labels, "brand_data": brand_data,
        "brands_for_filter": brands_for_filter, "months_for_filter": [],
        "current_sort_by": effective_sort_by, "current_order": effective_order,
        "sort_active_in_url": sort_active_in_url, "current_filter_brand": current_filter_brand,
        "current_filter_month": None, "current_search_term": current_search_term,
        "show_sort_controls": True, "form_for_modal": SneakerForm()
    }

    # --- 6. Respond ---
    if is_ajax:
        context['sneaker_grid_html'] = render_template('_sneaker_grid.html', **context)
        context['controls_bar_html'] = render_template('_controls_bar.html', target_endpoint='sneakers.dashboard', **context)
        context['summary_message_html'] = render_template('_collection_summary_message.html', **context)
        context.pop('sneakers', None)
        context.pop('form_for_modal', None)
        return jsonify(context)

    return render_template('dashboard.html', **context)

# My Rotation Route

@sneakers_bp.route('/my-rotation') # NEW URL
@login_required
def rotation():
    # Get parameters from request arguments (this part is the same)
    sort_by_param = request.args.get('sort_by')
    order_param = request.args.get('order')
    filter_brand_param = request.args.get('filter_brand')
    search_term_param = request.args.get('search_term')

    # --- Base query is the KEY DIFFERENCE ---
    # Instead of all sneakers, we only get those where in_rotation is True
    query = Sneaker.query.filter_by(user_id=current_user.id, in_rotation=True)

    # --- Calculate Counts Specific to Rotation ---
    # The total count of sneakers just in the rotation
    rotation_total_count = query.count()
    # The total count of ALL sneakers in the user's collection for context
    overall_collection_count = Sneaker.query.filter_by(user_id=current_user.id).count()


    # --- Determine if sorting was explicitly set via URL (for highlighting) ---
    sort_active_in_url = bool(sort_by_param)

    # --- Determine effective sort criteria for the query ---
    effective_sort_by = 'purchase_date'  # Default sort field
    effective_order = 'desc'           # Default order for purchase_date (newest first)

    if sort_by_param: # Only override defaults if sort_by_param actually exists
        if sort_by_param == 'brand':
            effective_sort_by = 'brand'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'asc'
        elif sort_by_param == 'model':
            effective_sort_by = 'model'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'asc'
        elif sort_by_param == 'purchase_date': 
            effective_sort_by = 'purchase_date'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'desc'
        elif sort_by_param == 'last_worn_date':
            effective_sort_by = 'last_worn_date'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'desc'
        elif sort_by_param == 'purchase_price':
            effective_sort_by = 'purchase_price'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'desc'
        elif sort_by_param == 'id': 
            effective_sort_by = 'id' 
            effective_order = order_param if order_param in ['asc', 'desc'] else 'desc'
        # If sort_by_param is an unrecognized value, defaults for effective_sort_by/order remain.
    
    # --- Apply brand filter ---
    is_brand_filter_active = bool(filter_brand_param and filter_brand_param.lower() != 'all')
    current_filter_brand = filter_brand_param.strip() if is_brand_filter_active else None
    if current_filter_brand:
        query = query.filter(Sneaker.brand == current_filter_brand)
    
    # --- Calculate brand_specific_count (after brand filter, before search) ---
    brand_specific_count = None
    if current_filter_brand:
         brand_query_for_count = Sneaker.query.filter_by(user_id=current_user.id, brand=current_filter_brand)
         brand_specific_count = brand_query_for_count.count()

    # --- Apply search term filter ---
    is_search_active = bool(search_term_param and search_term_param.strip())
    current_search_term = search_term_param.strip() if is_search_active else None
    if current_search_term:
        keywords = current_search_term.split()
        search_conditions = []
        for keyword in keywords:
            if keyword: 
                keyword_pattern = f"%{keyword}%"
                keyword_condition = or_(
                    Sneaker.brand.ilike(keyword_pattern),
                    Sneaker.model.ilike(keyword_pattern),
                    Sneaker.colorway.ilike(keyword_pattern)
                )
                search_conditions.append(keyword_condition)
        if search_conditions:
            query = query.filter(*search_conditions)

    if current_filter_brand and current_filter_brand.lower() != 'all':
         brand_query_for_count = query.filter(Sneaker.brand == current_filter_brand) # Apply to rotation query
         brand_specific_count = brand_query_for_count.count()

    # --- Apply sorting to the query ---
    if effective_sort_by == 'brand':
        order_obj = Sneaker.brand.desc() if effective_order == 'desc' else Sneaker.brand.asc()
    elif effective_sort_by == 'model':
        order_obj = Sneaker.model.desc() if effective_order == 'desc' else Sneaker.model.asc()
    elif effective_sort_by == 'purchase_date':
        order_obj = Sneaker.purchase_date.desc().nullslast() if effective_order == 'desc' else Sneaker.purchase_date.asc().nullsfirst()
    elif effective_sort_by == 'last_worn_date':
        order_obj = Sneaker.last_worn_date.desc().nullslast() if effective_order == 'desc' else Sneaker.last_worn_date.asc().nullsfirst()
    elif effective_sort_by == 'purchase_price':
        order_obj = Sneaker.purchase_price.desc().nullslast() if effective_order == 'desc' else Sneaker.purchase_price.asc().nullsfirst()
    elif effective_sort_by == 'id': # "Added" sort
        order_obj = Sneaker.id.desc() if effective_order == 'desc' else Sneaker.id.asc()
    else: # Default case, should match initialized effective_sort_by ('purchase_date')
        order_obj = Sneaker.purchase_date.desc().nullslast() 
        # Re-affirm defaults if sort_by_param was invalid, though effective_sort_by should already be set
        effective_sort_by = 'purchase_date' 
        effective_order = 'desc'

    rotation_sneakers = query.all()
    displayed_count = len(rotation_sneakers)

    query = query.order_by(order_obj)
    user_sneakers = query.all()
    
    # --- Calculate counts ---
    overall_total_count = Sneaker.query.filter_by(user_id=current_user.id).count()
    displayed_count = len(user_sneakers)

    # --- Get distinct brands for the filter dropdown ---
    distinct_brands_tuples = db.session.query(Sneaker.brand).filter(Sneaker.user_id == current_user.id).distinct().order_by(Sneaker.brand).all()
    brands_for_filter = [brand[0] for brand in distinct_brands_tuples if brand[0]]

    # --- Form for the "Add/Edit Sneaker" Modal ---
    modal_form = SneakerForm() 

    return render_template('rotation.html', 
                           show_sort_controls=True,
                           on_rotation_page=True,
                           name=current_user.first_name or current_user.username,
                           sneakers=user_sneakers,
                           rotation_total_count=rotation_total_count,
                           overall_collection_count=overall_collection_count,
                           brand_specific_count=brand_specific_count,
                           displayed_count=displayed_count,
                           current_sort_by=effective_sort_by,
                           current_order=effective_order,
                           sort_active_in_url=sort_active_in_url, # Flag for template highlighting
                           brands_for_filter=brands_for_filter,
                           current_filter_brand=current_filter_brand,
                           current_search_term=current_search_term,
                           form_for_modal=modal_form # Pass modal form as form_for_modal
                           )

# Add Sneaker Route

@sneakers_bp.route('/add-sneaker', methods=['POST'])
@login_required
def add_sneaker():
    form = SneakerForm()
    if form.validate_on_submit():
        final_image_location = None
        # Handle Image URL or Upload
        if form.image_option.data == 'upload' and form.sneaker_image_file.data:
            image_file = form.sneaker_image_file.data
            if allowed_file(image_file.filename):
                filename = secure_filename(image_file.filename)
                unique_filename = str(uuid.uuid4()) + os.path.splitext(filename)[1]
                image_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
                image_file.save(image_path)
                final_image_location = unique_filename
        elif form.image_option.data == 'url' and form.sneaker_image_url.data:
            final_image_location = form.sneaker_image_url.data

        # Create New Sneaker Object
        new_sneaker = Sneaker(
            brand=form.brand.data,
            model=form.model.data,
            sku=form.sku.data.strip() if form.sku.data else None,
            colorway=form.colorway.data.strip() if form.colorway.data else None,
            size_type=form.size_type.data,
            size=form.size.data,
            purchase_date=form.purchase_date.data,
            purchase_price=form.purchase_price.data,
            purchase_currency=form.purchase_currency.data,
            condition=form.condition.data,
            last_worn_date=form.last_worn_date.data,
            image_url=final_image_location,
            owner=current_user
        )
        db.session.add(new_sneaker)
        db.session.commit()

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'status': 'success', 'message': 'Sneaker added successfully!'})
        
        flash('Sneaker added successfully!', 'success')
        return redirect(url_for('sneakers.dashboard'))

    # Handle validation errors
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'status': 'error', 'errors': form.errors}), 400
    
    flash('There were errors with your submission.', 'danger')
    return redirect(url_for('sneakers.dashboard'))

# Edit Sneaker Route

@sneakers_bp.route('/edit-sneaker/<int:sneaker_id>', methods=['POST'])
@login_required
def edit_sneaker(sneaker_id):
    sneaker_to_edit = db.session.get(Sneaker, sneaker_id)
    if not sneaker_to_edit or sneaker_to_edit.owner != current_user:
        # For AJAX, return a JSON error; otherwise, abort
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'status': 'error', 'message': 'Permission denied.'}), 403
        abort(403)
    
    form = SneakerForm()
    if form.validate_on_submit():
        # Update text-based fields
        sneaker_to_edit.brand = form.brand.data
        sneaker_to_edit.model = form.model.data
        sneaker_to_edit.sku = form.sku.data.strip() if form.sku.data else None
        sneaker_to_edit.colorway = form.colorway.data.strip() if form.colorway.data else None
        sneaker_to_edit.size_type = form.size_type.data
        sneaker_to_edit.size = form.size.data
        sneaker_to_edit.purchase_date = form.purchase_date.data
        sneaker_to_edit.purchase_price = form.purchase_price.data
        sneaker_to_edit.purchase_currency = form.purchase_currency.data
        sneaker_to_edit.condition = form.condition.data
        sneaker_to_edit.last_worn_date = form.last_worn_date.data
        
        # Handle new image (URL or Upload)
        if form.image_option.data == 'upload' and form.sneaker_image_file.data:
            image_file = form.sneaker_image_file.data
            if allowed_file(image_file.filename):
                filename = secure_filename(image_file.filename)
                unique_filename = str(uuid.uuid4()) + os.path.splitext(filename)[1]
                image_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
                image_file.save(image_path)
                sneaker_to_edit.image_url = unique_filename
        elif form.image_option.data == 'url' and form.sneaker_image_url.data:
            sneaker_to_edit.image_url = form.sneaker_image_url.data
        
        db.session.commit()

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'status': 'success', 'message': 'Sneaker updated successfully!'})
            
        flash('Sneaker updated successfully!', 'success')
        return redirect(url_for('sneakers.dashboard'))

    # Handle validation errors
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'status': 'error', 'errors': form.errors}), 400
        
    flash('There were errors with your submission.', 'danger')
    return redirect(url_for('sneakers.dashboard'))

# Delete Sneaker Route

@sneakers_bp.route('/delete-sneaker/<int:sneaker_id>', methods=['POST'])
@login_required
def delete_sneaker(sneaker_id):
    sneaker_to_delete = db.session.get(Sneaker, sneaker_id)
    if not sneaker_to_delete:
        abort(404)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
              (request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html)

    if sneaker_to_delete.owner != current_user:
        if is_ajax:
            return jsonify({'status': 'error', 'message': 'You do not have permission.'}), 403
        else:
            flash('You do not have permission to delete this sneaker.', 'danger')
            return redirect(url_for('sneakers.dashboard'))

    try:
        # If it's an uploaded image, delete the file from the server
        if sneaker_to_delete.image_url and not (sneaker_to_delete.image_url.startswith('http://') or sneaker_to_delete.image_url.startswith('https://')):
            old_file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], sneaker_to_delete.image_url)
            if os.path.exists(old_file_path):
                try:
                    os.remove(old_file_path)
                    current_app.logger.info(f"Deleted image file during sneaker delete: {old_file_path}")
                except Exception as e:
                    current_app.logger.error(f"Error deleting image file {old_file_path}: {e}")

        # --- Get data needed for count updates BEFORE deleting ---
        deleted_sneaker_brand = sneaker_to_delete.brand

        db.session.delete(sneaker_to_delete)
        db.session.commit()

        if is_ajax:
            # --- Get updated counts AFTER deleting ---
            overall_total_count = Sneaker.query.filter_by(user_id=current_user.id).count()

            # Get count for the brand of the deleted sneaker
            brand_specific_count = Sneaker.query.filter_by(user_id=current_user.id, brand=deleted_sneaker_brand).count()

            return jsonify({
                'status': 'success', 
                'message': 'Sneaker removed.',
                'overall_total_count': overall_total_count,
                'deleted_sneaker_brand': deleted_sneaker_brand,
                'brand_specific_count_for_deleted_brand': brand_specific_count
            })
        else:
            flash('Sneaker removed from your collection.', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting sneaker {sneaker_id}: {str(e)}")
        if is_ajax: return jsonify({'status': 'error', 'message': f'Error deleting sneaker: {str(e)}'}), 500
        else: flash(f'Error deleting sneaker: {str(e)}', 'danger')

    return redirect(url_for('sneakers.dashboard'))

# Update Last Worn Route

@sneakers_bp.route('/update-last-worn/<int:sneaker_id>', methods=['POST'])
@login_required
def update_last_worn(sneaker_id):
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    sneaker = db.session.get(Sneaker, sneaker_id)

    if not sneaker:
        if is_ajax:
            return jsonify({'status': 'error', 'message': 'Sneaker not found.'}), 404
        abort(404)

    # --- THIS IS THE CRUCIAL SECURITY CHECK ---
    if sneaker.owner != current_user:
        if is_ajax:
            # For an AJAX request, return a JSON error with a 403 status
            return jsonify({'status': 'error', 'message': 'Permission denied.'}), 403
        else:
            # For a normal form post, flash and redirect
            flash('You do not have permission to update this sneaker.', 'danger')
            return redirect(url_for('sneakers.dashboard'))

    # --- Rest of the function logic ---
    new_date_str = request.form.get('new_last_worn')
    if not new_date_str:
        return jsonify({'status': 'error', 'message': 'No date provided.'}), 400

    try:
        sneaker.last_worn_date = date.fromisoformat(new_date_str)
        db.session.commit()
        return jsonify({
            'status': 'success',
            'message': 'Date updated!',
            'new_date_display': sneaker.last_worn_date.strftime('%b %d, %Y')
        })
    except (ValueError, TypeError):
        return jsonify({'status': 'error', 'message': 'Invalid date format.'}), 400
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating last_worn_date for sneaker {sneaker_id}: {e}")
        return jsonify({'status': 'error', 'message': 'A database error occurred.'}), 500

# Fetch Sneaker Data Route

@sneakers_bp.route('/sneaker-data/<int:sneaker_id>', methods=['GET'])
@login_required
def get_sneaker_data(sneaker_id):
    sneaker = db.session.get(Sneaker, sneaker_id)
    if not sneaker or sneaker.owner != current_user:
        return jsonify({'status': 'error', 'message': 'Sneaker not found or permission denied.'}), 404

    # Determine the correct URL for the preview image
    image_display_url = None
    if sneaker.image_url:
        if sneaker.image_url.startswith('http'):
            image_display_url = sneaker.image_url
        else:
            # Use _external=True to generate a full URL for AJAX
            image_display_url = url_for('main.uploaded_file', filename=sneaker.image_url, _external=True)

    sneaker_data = {
        'brand': sneaker.brand,
        'model': sneaker.model,
        'colorway': sneaker.colorway,
        'size': sneaker.size,
        'size_type': sneaker.size_type,
        'last_worn_date': sneaker.last_worn_date.strftime('%Y-%m-%d') if sneaker.last_worn_date else '',
        'purchase_price': str(sneaker.purchase_price) if sneaker.purchase_price is not None else '',
        'purchase_currency': sneaker.purchase_currency,
        'condition': sneaker.condition,
        'purchase_date': sneaker.purchase_date.strftime('%Y-%m-%d') if sneaker.purchase_date else '',
        'sneaker_image_url': sneaker.image_url if sneaker.image_url and sneaker.image_url.startswith('http') else '',
        'current_image_display_url': image_display_url
    }
    return jsonify({'status': 'success', 'sneaker': sneaker_data})

# Add to Rotation Route

@sneakers_bp.route('/add-to-rotation/<int:sneaker_id>', methods=['POST'])
@login_required
def add_to_rotation(sneaker_id):
    sneaker = db.session.get(Sneaker, sneaker_id)
    if not sneaker_to_add_to_rotation:
        abort(404)
    if sneaker.owner != current_user:
        return jsonify({'status': 'error', 'message': 'Permission denied.'}), 403

    sneaker.in_rotation = True
    db.session.commit()

    # Return the re-rendered button HTML so the UI can update
    new_button_html = render_template('_rotation_button.html', sneaker=sneaker)
    return jsonify({
        'status': 'success', 
        'message': f"Added '{sneaker.brand} {sneaker.model}' to your rotation.",
        'new_button_html': new_button_html
    })

# Remove from Rotation Route

@sneakers_bp.route('/remove-from-rotation/<int:sneaker_id>', methods=['POST'])
@login_required
def remove_from_rotation(sneaker_id):
    sneaker = db.session.get(Sneaker, sneaker_id)

    # Check if sneaker exists
    if not sneaker:
        abort(404) # Or return a JSON error for AJAX

    # Check ownership
    if sneaker.owner != current_user:
        return jsonify({'status': 'error', 'message': 'Permission denied.'}), 403

    sneaker.in_rotation = False
    db.session.commit()

    # Re-render the button HTML so the UI can update
    new_button_html = render_template('_rotation_button.html', sneaker=sneaker)
    return jsonify({
        'status': 'success', 
        'message': f"Removed '{sneaker.brand} {sneaker.model}' from your rotation.",
        'in_rotation': False, # So JS knows the sneaker was removed from rotation
        'new_button_html': new_button_html
    })

# Select for Rotation Route

@sneakers_bp.route('/select-for-rotation', methods=['GET', 'POST'])
@login_required
def select_for_rotation():
    # --- POST request logic: Handles the form submission ---
    if request.method == 'POST':
        sneaker_ids_to_add_str = request.form.getlist('sneaker_ids')
        if not sneaker_ids_to_add_str:
            flash('You did not select any sneakers to add.', 'warning')
            return redirect(url_for('sneakers.select_for_rotation'))
        try:
            sneaker_ids_to_add = [int(id_str) for id_str in sneaker_ids_to_add_str]
            sneakers_to_update = Sneaker.query.filter(
                Sneaker.id.in_(sneaker_ids_to_add), 
                Sneaker.user_id == current_user.id # This security check is key
            ).all()
            
            updated_count = 0
            for sneaker in sneakers_to_update:
                sneaker.in_rotation = True
                updated_count += 1
            
            db.session.commit()
            
            if updated_count > 0:
                flash(f'{updated_count} sneaker{"s" if updated_count != 1 else ""} {"have" if updated_count != 1 else "has"} been added to your rotation.', 'success')

            else:
                # This is the message the test is looking for
                flash('No sneakers were added. Please check your selection.', 'warning') 

            return redirect(url_for('sneakers.rotation'))
        
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error adding sneakers to rotation: {e}")
            flash('An error occurred while updating your rotation.', 'danger')
            return redirect(url_for('sneakers.select_for_rotation'))

    # --- GET request logic: Displays the page with sorting/filtering/searching ---
    sort_by_param = request.args.get('sort_by')
    order_param = request.args.get('order')
    filter_brand_param = request.args.get('filter_brand')
    search_term_param = request.args.get('search_term')

    sort_active_in_url = bool(sort_by_param)
    effective_sort_by = 'purchase_date'
    effective_order = 'desc'

    if sort_by_param:
        if sort_by_param == 'brand':
            effective_sort_by = 'brand'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'asc'
        elif sort_by_param == 'model':
            effective_sort_by = 'model'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'asc'
        elif sort_by_param == 'purchase_date': 
            effective_sort_by = 'purchase_date'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'desc'
        elif sort_by_param == 'last_worn_date':
            effective_sort_by = 'last_worn_date'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'desc'
        elif sort_by_param == 'purchase_price':
            effective_sort_by = 'purchase_price'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'desc'
        elif sort_by_param == 'id': 
            effective_sort_by = 'id'
            effective_order = order_param if order_param in ['asc', 'desc'] else 'desc'
    
    # Base query: all sneakers for the user that are NOT in rotation
    query = Sneaker.query.filter_by(user_id=current_user.id, in_rotation=False)

    is_brand_filter_active = bool(filter_brand_param and filter_brand_param.lower() != 'all')
    is_search_active = bool(search_term_param and search_term_param.strip())
    current_filter_brand = filter_brand_param.strip() if is_brand_filter_active else None
    current_search_term = search_term_param.strip() if is_search_active else None

    if current_filter_brand:
        query = query.filter(Sneaker.brand == current_filter_brand)
    if current_search_term:
        keywords = current_search_term.split()
        search_conditions = [or_(Sneaker.brand.ilike(f"%{k}%"), Sneaker.model.ilike(f"%{k}%"), Sneaker.colorway.ilike(f"%{k}%")) for k in keywords if k]
        if search_conditions:
            query = query.filter(*search_conditions)

    # Apply sorting
    if effective_sort_by == 'brand':
        order_obj = Sneaker.brand.desc() if effective_order == 'desc' else Sneaker.brand.asc()
    elif effective_sort_by == 'model':
        order_obj = Sneaker.model.desc() if effective_order == 'desc' else Sneaker.model.asc()
    elif effective_sort_by == 'purchase_date':
        order_obj = Sneaker.purchase_date.desc().nullslast() if effective_order == 'desc' else Sneaker.purchase_date.asc().nullsfirst()
    elif effective_sort_by == 'last_worn_date':
        order_obj = Sneaker.last_worn_date.desc().nullslast() if effective_order == 'desc' else Sneaker.last_worn_date.asc().nullsfirst()
    elif effective_sort_by == 'purchase_price':
        order_obj = Sneaker.purchase_price.desc().nullslast() if effective_order == 'desc' else Sneaker.purchase_price.asc().nullsfirst()
    elif effective_sort_by == 'id':
        order_obj = Sneaker.id.desc() if effective_order == 'desc' else Sneaker.id.asc()
    else: # Default case
        order_obj = Sneaker.purchase_date.desc().nullslast()

    query = query.order_by(order_obj)
    
    available_sneakers = query.order_by(Sneaker.brand, Sneaker.model).all() # Using a simple sort for this example

    # Get distinct brands for the filter dropdown
    base_available_query = Sneaker.query.filter_by(user_id=current_user.id, in_rotation=False)
    distinct_brands_tuples = base_available_query.with_entities(Sneaker.brand).distinct().order_by(Sneaker.brand).all()
    brands_for_filter = [brand[0] for brand in distinct_brands_tuples if brand[0]]

    form = EmptyForm() # For CSRF protection

    return render_template('select_for_rotation.html', 
                           title='Add Sneakers to Rotation', 
                           available_sneakers=available_sneakers,
                           form=form,
                           brands_for_filter=brands_for_filter,
                           current_sort_by=effective_sort_by,
                           current_order=effective_order,
                           sort_active_in_url=sort_active_in_url,
                           current_filter_brand=current_filter_brand,
                           current_search_term=current_search_term)

# --- V2 FEATURE: API ENDPOINT FOR SNEAKER SEARCH ---
@sneakers_bp.route('/api/search-sneakers')
@login_required
def search_sneakers():
    """
    Searches the local SneakerDB table and returns results as JSON.
    """
    search_query = request.args.get('q', '')
    if not search_query:
        return jsonify({'results': []}) # Return empty list if no query

    # Build a search pattern for a LIKE query
    search_pattern = f"%{search_query}%"

    # Query our local SneakerDB table, searching across multiple fields
    sneakers_found = SneakerDB.query.filter(
        or_(
            SneakerDB.model_name.ilike(search_pattern),
            SneakerDB.name.ilike(search_pattern),
            SneakerDB.brand.ilike(search_pattern),
            SneakerDB.sku.ilike(search_pattern)
        )
    ).limit(20).all() # Limit to 20 results for performance

    # Convert the sneaker objects into a list of dictionaries to be sent as JSON
    results = []
    for sneaker in sneakers_found:
        results.append({
            'name': sneaker.model_name or sneaker.name,
            'brand': sneaker.brand,
            'sku': sneaker.sku,
            'releaseDate': sneaker.release_date.strftime('%Y-%m-%d') if sneaker.release_date else None,
            'retailPrice': float(sneaker.retail_price) if sneaker.retail_price else None,
            'image': {
                'original': sneaker.image_url
            }
        })

    return jsonify({'results': results})


@sneakers_bp.route('/sneakers/db/search')
@login_required
def search_sneaker_db():
    query = request.args.get('q', '').strip()
    mode = request.args.get('mode', '').strip().lower()
    force_best = request.args.get('force_best', 'false').strip().lower() in ('1', 'true', 'yes')

    if not query:
        return jsonify({'status': 'error', 'message': 'Query is required.'}), 400

    api_key = current_app.config.get('KICKS_API_KEY')
    if not api_key:
        return jsonify({'status': 'error', 'message': 'KICKS_API_KEY is not configured.'}), 500

    client = KicksClient(
        api_key=api_key,
        base_url=current_app.config.get('KICKS_API_BASE_URL', 'https://api.kicks.dev'),
        logger=current_app.logger,
    )

    try:
        result = lookup_or_fetch_sneaker(
            query=query,
            db_session=db.session,
            client=client,
            max_age_hours=24,
            force_best=force_best,
            return_candidates=(mode == 'pick'),
        )
    except Exception as e:
        current_app.logger.error("KicksDB lookup failed for query '%s': %s", query, e)
        return jsonify({'status': 'error', 'message': 'External lookup failed.'}), 502

    status = result.get('status')
    if status in ('ok', 'pick'):
        return jsonify(result)
    if status == 'not_found':
        return jsonify(result), 404
    return jsonify(result), 400


@sneakers_bp.route('/api/sneaker-lookup')
@login_required
def sneaker_lookup():
    query = request.args.get('q', '').strip()
    limit = request.args.get('limit', '5').strip()
    force_best = request.args.get('force_best', 'false').strip().lower() in ('1', 'true', 'yes')

    if not query:
        return jsonify({'message': 'Query is required.'}), 400

    try:
        limit_value = max(1, min(int(limit), 10))
    except ValueError:
        limit_value = 5

    api_key = current_app.config.get('KICKS_API_KEY')
    if not api_key:
        return jsonify({'message': 'KICKS_API_KEY is not configured.'}), 500

    client = KicksClient(
        api_key=api_key,
        base_url=current_app.config.get('KICKS_API_BASE_URL', 'https://api.kicks.dev'),
        logger=current_app.logger,
    )

    try:
        result = lookup_or_fetch_sneaker(
            query=query,
            db_session=db.session,
            client=client,
            max_age_hours=24,
            force_best=force_best,
            return_candidates=True,
        )
    except Exception as e:
        current_app.logger.error("Sneaker lookup failed for '%s': %s", query, e)
        return jsonify({'message': 'External lookup failed.'}), 502

    if result.get('status') == 'ok':
        return jsonify({'mode': 'single', 'sneaker': result.get('sneaker'), 'source': result.get('source')})
    if result.get('status') == 'pick':
        candidates = result.get('candidates') or []
        return jsonify({'mode': 'pick', 'candidates': candidates[:limit_value], 'source': result.get('source')})
    if result.get('status') == 'not_found':
        return jsonify({'mode': 'none', 'message': result.get('message', 'No results found.')}), 404
    return jsonify({'mode': 'error', 'message': result.get('message', 'Lookup failed.')}), 400
