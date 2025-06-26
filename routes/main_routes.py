# routes/main_routes.py
import requests
import os
import uuid
from collections import OrderedDict
from datetime import datetime, date
from flask import Blueprint, render_template, redirect, url_for, flash, request, send_from_directory, current_app, jsonify
from flask_login import login_required, current_user
from extensions import db
from models import User, Sneaker, Release, wishlist_items
from forms import EditProfileForm, ReleaseForm, EmptyForm
from werkzeug.utils import secure_filename
from decorators import admin_required
from sqlalchemy import or_, asc, desc, extract, func

main_bp = Blueprint('main', __name__)

# Home Route

@main_bp.route('/')
def home():
    # Initialize all lists and default stats
    recent_sneakers, upcoming_wishlist, rotation_sneakers = [], [], []
    stats = { "overall_total_count": 0, "total_value": 0.0, "most_owned_brand": "N/A", "in_rotation_count": 0 }

    # --- THIS QUERY NOW RUNS FOR ALL VISITORS ---
    today = date.today()
    general_releases = Release.query.filter(Release.release_date >= today) \
                            .order_by(Release.release_date.asc()) \
                            .limit(4).all()

    if current_user.is_authenticated:
        # --- These queries ONLY run for logged-in users ---
        base_query = Sneaker.query.filter_by(user_id=current_user.id)

        # Content for homepage sections
        recent_sneakers = base_query.order_by(Sneaker.id.desc()).limit(4).all()
        upcoming_wishlist = Release.query.join(wishlist_items).filter(wishlist_items.c.user_id == current_user.id, Release.release_date >= today).order_by(Release.release_date.asc()).limit(4).all()
        rotation_sneakers = base_query.filter_by(in_rotation=True).order_by(Sneaker.last_worn_date.asc().nullsfirst()).limit(4).all()

        # Stats for stat cards
        stats["overall_total_count"] = base_query.count()
        stats["in_rotation_count"] = base_query.filter_by(in_rotation=True).count()
        stats["total_value"] = float(base_query.with_entities(func.sum(Sneaker.purchase_price)).scalar() or 0.0)
        brand_dist = base_query.with_entities(Sneaker.brand, func.count(Sneaker.brand)).filter(Sneaker.brand.isnot(None)).group_by(Sneaker.brand).order_by(func.count(Sneaker.brand).desc()).first()
        if brand_dist:
            stats["most_owned_brand"] = brand_dist[0]

    return render_template('home.html', 
                           recent_sneakers=recent_sneakers,
                           upcoming_wishlist=upcoming_wishlist,
                           rotation_sneakers=rotation_sneakers,
                           general_releases=general_releases, # This is now available to everyone
                           stats=stats)

# Profile Route

@main_bp.route('/profile')
@login_required
def profile():
    return render_template('profile.html', title='Your Profile')

# Edit Profile Route

@main_bp.route('/edit-profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    form = EditProfileForm()
    if form.validate_on_submit():
        new_email = form.email.data.lower()
        email_changed = (new_email != current_user.email.lower())
        can_proceed_with_update = True
        email_update_pending = False

        if email_changed:
            existing_user_by_email = User.query.filter(User.email == new_email, User.id != current_user.id).first()
            other_user_pending_this_email = User.query.filter(User.pending_email == new_email, User.id != current_user.id).first()
            if existing_user_by_email or other_user_pending_this_email:
                form.email.errors.append('That email address is already in use or pending confirmation by another account.')
                can_proceed_with_update = False
            else:
                from .auth_routes import send_confirm_new_email_address_email
                current_user.pending_email = new_email
                send_confirm_new_email_address_email(current_user, new_email)
                email_update_pending = True
        
        if can_proceed_with_update:
            current_user.first_name = form.first_name.data.strip()
            current_user.last_name = form.last_name.data.strip()
            current_user.marketing_opt_in = form.marketing_opt_in.data
            try:
                db.session.commit()
                if email_update_pending:
                    flash('Your profile details have been updated. A confirmation link has been sent to your new email address to complete the change.', 'info')
                else:
                    flash('Your profile has been updated successfully!', 'success')
                return redirect(url_for('main.profile'))
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Error updating profile for user {current_user.id}: {e}")
                flash('Error updating profile. Please try again.', 'danger')
    
    elif request.method == 'GET':
        form.first_name.data = current_user.first_name
        form.last_name.data = current_user.last_name
        form.email.data = current_user.pending_email or current_user.email
        form.marketing_opt_in.data = current_user.marketing_opt_in
    
    return render_template('edit_profile.html', title='Edit Your Profile', form=form)

# Upload Image Route

@main_bp.route('/uploads/<path:filename>')
def uploaded_file(filename):
    # Use current_app to access config['UPLOAD_FOLDER'] safely from within blueprint
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)

# Release Calendar Route

@main_bp.route('/release-calendar')
def release_calendar():
    """Displays upcoming sneaker releases from our own database, with filtering and searching."""
    form = EmptyForm()
    today = date.today()

    # Get filter/search parameters from the URL
    filter_brand_param = request.args.get('filter_brand')
    filter_month_param = request.args.get('filter_month')
    search_term_param = request.args.get('search_term')

    # Base query: all releases from today onwards
    query = Release.query.filter(Release.release_date >= today)

    # Get distinct brands and months for the filter dropdowns BEFORE filtering the main query
    distinct_brands_tuples = query.with_entities(Release.brand).distinct().order_by(Release.brand).all()
    brands_for_filter = [brand[0] for brand in distinct_brands_tuples if brand[0]]

    distinct_months_tuples = db.session.query(
        extract('year', Release.release_date), 
        extract('month', Release.release_date)
    ).filter(Release.release_date >= today).distinct().order_by(
        extract('year', Release.release_date), 
        extract('month', Release.release_date)
    ).all()
    # Format months as "YYYY-MM" for the dropdown value and "Month Year" for the display
    months_for_filter = []
    for year, month in distinct_months_tuples:
        # Explicitly convert to integers to handle database differences
        year = int(year)
        month = int(month)
        # Now the formatting will work correctly
        date_obj = datetime(year, month, 1)
        display_text = date_obj.strftime('%B %Y')
        value_text = f"{year}-{month:02d}"
        months_for_filter.append((value_text, display_text))

    # Apply filters to the main query
    current_filter_brand = None
    if filter_brand_param and filter_brand_param.lower() != 'all':
        current_filter_brand = filter_brand_param
        query = query.filter(Release.brand == current_filter_brand)

    current_filter_month = None
    if filter_month_param and filter_month_param.lower() != 'all':
        current_filter_month = filter_month_param
        year, month = map(int, current_filter_month.split('-'))
        query = query.filter(extract('year', Release.release_date) == year, extract('month', Release.release_date) == month)

    current_search_term = search_term_param.strip() if search_term_param else None
    if current_search_term:
        query = query.filter(or_(
            Release.name.ilike(f"%{current_search_term}%"),
            Release.brand.ilike(f"%{current_search_term}%")
        ))

    upcoming_releases = query.order_by(Release.release_date.asc()).all()

    # The existing grouping logic will now work on the filtered results
    releases_by_month = OrderedDict()
    for release in upcoming_releases:
        month_year_key = release.release_date.strftime('%B %Y')
        if month_year_key not in releases_by_month:
            releases_by_month[month_year_key] = []
        releases_by_month[month_year_key].append(release)

    return render_template('release_calendar.html', 
                           show_sort_controls=False,
                           title='Upcoming Sneaker Releases', 
                           releases_by_month=releases_by_month,
                           form=form,
                           brands_for_filter=brands_for_filter,
                           months_for_filter=months_for_filter,
                           current_filter_brand=current_filter_brand,
                           current_filter_month=current_filter_month,
                           current_search_term=current_search_term)

# Admin Add New Release Route

@main_bp.route('/admin/add-release', methods=['GET', 'POST'])
@login_required
@admin_required
def add_release():
    form = ReleaseForm()
    if form.validate_on_submit():
        final_image_location = None # Will hold the URL or filename

        # --- NEW IMAGE HANDLING LOGIC ---
        if form.image_option.data == 'url':
            if form.image_url.data:
                final_image_location = form.image_url.data.strip()
        elif form.image_option.data == 'upload':
            image_file = form.sneaker_image_file.data
            if image_file and image_file.filename != '':
                if allowed_file(image_file.filename):
                    original_filename = secure_filename(image_file.filename)
                    extension = os.path.splitext(original_filename)[1].lower()
                    unique_filename = str(uuid.uuid4().hex) + extension
                    save_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
                    try:
                        image_file.save(save_path)
                        final_image_location = unique_filename
                    except Exception as e:
                        current_app.logger.error(f"Failed to save release image: {e}")
                        flash('There was an error saving the uploaded image.', 'danger')
                else:
                    # This case should be caught by form validation, but it's good to have
                    flash('Invalid file type.', 'warning')
        # --- END OF IMAGE HANDLING LOGIC ---

        new_release = Release(
            name=form.name.data,
            brand=form.brand.data,
            release_date=form.release_date.data,
            retail_price=form.retail_price.data,
            retail_currency=form.retail_currency.data,
            image_url=final_image_location # Use the final determined image location
        )
        db.session.add(new_release)
        db.session.commit()
        flash('New release has been added to the calendar!', 'success')
        return redirect(url_for('main.release_calendar'))

    return render_template('add_release.html', title='Add New Release', form=form)

# Admin Edit Sneaker Release Route

@main_bp.route('/admin/edit-release/<int:release_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_release(release_id):
    # Find the existing release in the database or show a 404 error
    release_to_edit = db.session.get(Release, release_id)
    if not release_to_edit:
        abort(404)

    # For a GET request, pre-populate the form with the release's existing data
    form = ReleaseForm(obj=release_to_edit)

    # For a POST request, process the submitted form data
    if form.validate_on_submit():
        # Update the existing release object with the new data from the form
        release_to_edit.name = form.name.data
        release_to_edit.brand = form.brand.data
        release_to_edit.release_date = form.release_date.data
        release_to_edit.retail_price = form.retail_price.data
        release_to_edit.retail_currency = form.retail_currency.data

        # Image handling logic (copied and adapted from your sneaker edit route)
        if form.image_option.data == 'url' and form.image_url.data:
            release_to_edit.image_url = form.image_url.data.strip()
        elif form.image_option.data == 'upload':
            image_file = form.sneaker_image_file.data
            if image_file and image_file.filename != '':
                # You would add your file saving logic here
                # For now, let's assume we are just updating the URL for simplicity
                pass # Placeholder for file upload logic

        db.session.commit()
        flash('Release has been updated!', 'success')
        return redirect(url_for('main.release_calendar'))

    return render_template('edit_release.html', 
                           title='Edit Release', 
                           form=form)

# Admin Delete Sneaker Release Route

@main_bp.route('/admin/delete-release/<int:release_id>', methods=['POST'])
@login_required
@admin_required
def delete_release(release_id):
    """Deletes a specific release from the database."""
    # For now, we assume any logged-in user can delete. We can add admin checks later.

    release_to_delete = db.session.get(Release, release_id)

    if not release_to_delete:
        flash('Release not found.', 'warning')
        return redirect(url_for('main.release_calendar'))

    try:
        db.session.delete(release_to_delete)
        db.session.commit()
        flash(f"'{release_to_delete.name}' has been successfully deleted.", 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting release {release_id}: {e}")
        flash('Error deleting release. Please try again.', 'danger')

    return redirect(url_for('main.release_calendar'))

# Add to Wishlist Route

@main_bp.route('/wishlist/add/<int:release_id>', methods=['POST'])
@login_required
def add_to_wishlist(release_id):
    release = db.session.get(Release, release_id)
    if not release or release in current_user.wishlist:
        return jsonify({'status': 'error', 'message': 'Invalid request.'}), 400
    current_user.wishlist.append(release)
    db.session.commit()
    new_button_html = render_template('_wishlist_button.html', release=release)
    return jsonify({'status': 'success', 'message': 'Added to wishlist!', 'new_button_html': new_button_html})


# Remove from Wishlist Route

@main_bp.route('/wishlist/remove/<int:release_id>', methods=['POST'])
@login_required
def remove_from_wishlist(release_id):
    release = db.session.get(Release, release_id)
    if not release or release not in current_user.wishlist:
        return jsonify({'status': 'error', 'message': 'Invalid request.'}), 400
    current_user.wishlist.remove(release)
    db.session.commit()
    new_button_html = render_template('_wishlist_button.html', release=release)
    return jsonify({'status': 'success', 'message': 'Removed from wishlist.', 'new_button_html': new_button_html})

# Wishlist Route

@main_bp.route('/my-wishlist')
@login_required
def wishlist():
    """Displays the current user's wishlist, with filtering and searching."""
    form = EmptyForm()

    # Get filter/search parameters from the URL
    filter_brand_param = request.args.get('filter_brand')
    filter_month_param = request.args.get('filter_month')
    search_term_param = request.args.get('search_term')

    # Base query: Get all releases on the current user's wishlist
    query = Release.query.join(wishlist_items).filter(wishlist_items.c.user_id == current_user.id)

    # Get distinct brands and months for the dropdowns from the user's wishlist
    distinct_brands_tuples = query.with_entities(Release.brand).distinct().order_by(Release.brand).all()
    brands_for_filter = [brand[0] for brand in distinct_brands_tuples if brand[0]]

    distinct_months_tuples = query.with_entities(
        extract('year', Release.release_date), 
        extract('month', Release.release_date)
    ).distinct().order_by(
        extract('year', Release.release_date), 
        extract('month', Release.release_date)
    ).all()
    months_for_filter = [(f"{y}-{m:02d}", datetime(y, m, 1).strftime('%B %Y')) for y, m in distinct_months_tuples]

    # Apply filters to the main query
    current_filter_brand = filter_brand_param if filter_brand_param and filter_brand_param != 'all' else None
    if current_filter_brand:
        query = query.filter(Release.brand == current_filter_brand)

    current_filter_month = filter_month_param if filter_month_param and filter_month_param != 'all' else None
    if current_filter_month:
        year, month = map(int, current_filter_month.split('-'))
        query = query.filter(extract('year', Release.release_date) == year, extract('month', Release.release_date) == month)

    current_search_term = search_term_param.strip() if search_term_param else None
    if current_search_term:
        query = query.filter(Release.name.ilike(f"%{current_search_term}%"))

    # Sort the final list by release date
    wishlist_items_list = query.order_by(Release.release_date.asc()).all()

    return render_template('wishlist.html', 
                           show_sort_controls=False,
                           title='My Wishlist', 
                           releases=wishlist_items_list,
                           form=form,
                           brands_for_filter=brands_for_filter,
                           months_for_filter=months_for_filter,
                           current_filter_brand=current_filter_brand,
                           current_filter_month=current_filter_month,
                           current_search_term=current_search_term)





