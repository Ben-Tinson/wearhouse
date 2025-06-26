# routes/auth_routes.py
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, current_user, login_required
from flask_mail import Message

from extensions import db, mail
from models import User
from forms import LoginForm, RegistrationForm, RequestResetForm, ResetPasswordForm, EmptyForm
from email_utils import send_email

auth_bp = Blueprint('auth', __name__)

def send_password_reset_email(user_instance):
    token = user_instance.get_reset_password_token()
    reset_url = url_for('auth.reset_password_with_token', token=token, _external=True)

    # CORRECTED: Use your existing template file
    html_body = render_template('emails/reset_password_email.html', 
                                user=user_instance, 
                                reset_url=reset_url)

    # --- THIS IS THE ONLY PART THAT CHANGES ---
    # Instead of building a Message object and printing, we call our new utility
    send_email(to_email=user_instance.email,
               subject='Password Reset Request - WearHouse',
               html_content=html_body)

def send_confirm_new_email_address_email(user_instance, new_email_address):
    token = user_instance.get_confirm_new_email_token(new_email_address) # Use the new token method
    # We'll create the 'confirm_new_email_with_token' route in a later step
    confirm_url = url_for('auth.confirm_new_email_with_token', token=token, _external=True)

    # We'll create templates/email/confirm_new_email_address.html next
    html_body = render_template('email/confirm_new_email_address.html', 
                                user=user_instance, 
                                confirm_url=confirm_url,
                                new_email=new_email_address)

    msg = Message(subject='Confirm Your New Email Address - WearHouse',
                  sender=current_app.config.get('MAIL_DEFAULT_SENDER', 'noreply@wearhouse.com'),
                  recipients=[new_email_address]) # Send to the NEW email address
    msg.html = html_body

    # For now, print to console
    print("---- SENDING CONFIRM NEW EMAIL ADDRESS (to console) ----")
    print(f"To: {msg.recipients}")
    print(f"From: {msg.sender}")
    print(f"Subject: {msg.subject}")
    print("---- HTML Body ----")
    print(msg.html)
    print("---------------------------------------------------------")
    # Later, you would use: mail.send(msg)


# Registration Route

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.home'))

    form = RegistrationForm()
    if form.validate_on_submit():
        username_from_form = form.username.data
        email_from_form = form.email.data.lower()
        password_from_form = form.password.data
        first_name_from_form = form.first_name.data
        last_name_from_form = form.last_name.data
        marketing_opt_in_from_form = form.marketing_opt_in.data # Get this value

        existing_user_by_username = User.query.filter_by(username=username_from_form).first()
        existing_user_by_email = User.query.filter_by(email=email_from_form).first()

        can_proceed = True
        if existing_user_by_username:
            flash('That username is already taken. Please choose a different one.', 'warning')
            can_proceed = False
        if existing_user_by_email:
            flash('That email address is already registered. Please use a different one or try logging in.', 'warning')
            can_proceed = False

        if can_proceed:
            new_user = User(
                username=username_from_form,
                email=email_from_form,
                first_name=first_name_from_form.strip(),
                last_name=last_name_from_form.strip(),
                marketing_opt_in=marketing_opt_in_from_form 
                # is_email_confirmed will default to False as per model definition
            )
            new_user.set_password(password_from_form)
            db.session.add(new_user)
            db.session.commit() # User is saved and gets an ID

            # --- SEND CONFIRMATION EMAIL ---
            try:
                send_account_confirmation_email(new_user)
                flash('Registration successful! A confirmation email has been sent to your email address. Please check your inbox (and spam folder) to activate your account.', 'info')
            except Exception as e:
                current_app.logger.error(f"Error sending confirmation email to {new_user.email}: {e}")
                flash('Registration successful, but there was an issue sending the confirmation email. Please contact support if you do not receive it.', 'warning')

            return redirect(url_for('auth.login')) # Redirect to login after registration

    return render_template('register.html', title='Register', form=form)

# Login Route
@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        # Redirect to home if user is already logged in
        # (as per previous logic - this page is /my_collection for dashboard)
        # Assuming you want to go to 'my_collection' or 'main.home'
        return redirect(url_for('sneakers.dashboard')) # Or 'main.home'

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.check_password(form.password.data):
            # --- ADDED CHECK FOR EMAIL CONFIRMATION ---
            if user.is_email_confirmed:
                login_user(user)
                flash('Logged in successfully!', 'success')
                next_page = request.args.get('next')
                # Redirect to home page after login, or to the page they were trying to access
                return redirect(next_page or url_for('main.home')) 
            else:
                # Email is not confirmed
                flash('Your account has not been activated. Please check your email for the confirmation link. If you did not receive it, you may need to register again or contact support.', 'warning')
                return redirect(url_for('auth.login')) # Stay on login page or redirect as appropriate
            # --- END OF ADDED CHECK ---
        else:
            flash('Login Unsuccessful. Please check username and password.', 'danger')

    return render_template('login.html', title='Login', form=form)

# Logout Route
@auth_bp.route('/logout')
@login_required # Ensure only logged-in users can access logout
def logout():
    logout_user() # Log out the user with Flask-Login
    flash('You have been logged out.', 'info')
    return redirect(url_for('main.home'))

# Reset Password Route

@auth_bp.route('/reset-password-request', methods=['GET', 'POST'])
def reset_password_request():
    if current_user.is_authenticated: # Logged-in users don't need this
        return redirect(url_for('main.home')) 

    form = RequestResetForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower()).first()
        if user:
            send_password_reset_email(user) # Call our helper function

        # Always show the same message to prevent email enumeration
        flash('If an account with that email exists, instructions to reset your password have been sent.', 'info')
        return redirect(url_for('auth.login'))

    return render_template('reset_request.html', title='Request Password Reset', form=form)

# Resetting password with a token route

@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password_with_token(token):
    # REMOVE or COMMENT OUT this block:
    # if current_user.is_authenticated:
    #     print("DEBUG: User is already authenticated, redirecting to home.") 
    #     return redirect(url_for('main.home')) 
    
    # Verify the token first, this gives us the user the token is for
    user_for_token = User.verify_reset_password_token(token) 
    
    if not user_for_token:
        flash('That is an invalid or expired password reset token. Please try requesting a reset again.', 'warning')
        return redirect(url_for('auth.reset_password_request'))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        # The user_for_token is the user whose password should be changed.
        user_for_token.set_password(form.password.data)
        db.session.commit()
        flash('Your password has been successfully updated!', 'success')

        # If the person using this link happened to be logged in AND it was their own password they changed,
        # log them out to ensure they re-authenticate with the new password.
        if current_user.is_authenticated and current_user.id == user_for_token.id:
            logout_user()
            flash('Please log in with your new password.', 'info') # Additional message
        
        return redirect(url_for('auth.login'))
            
    # For a GET request (token was valid and user_for_token was found), 
    # or if POST form validation failed
    print(f"DEBUG: Rendering reset_password_form.html for token, user_for_token ID: {user_for_token.id if user_for_token else 'None'}")
    return render_template('reset_password_form.html', title='Reset Your Password', form=form, token=token)

# Change Password Route

@auth_bp.route('/change-password', methods=['GET'])
@login_required
def change_password():
    form = EmptyForm() # Create an instance of the EmptyForm
    return render_template('request_password_change_link.html', 
                           title='Change Your Password', 
                           form=form) # Pass the form object to the template

# Confirm New Email Route

@auth_bp.route('/confirm-new-email/<token>', methods=['GET']) # Usually GET for confirmation links
# No @login_required here, as user clicks this from an email and might not be logged in
def confirm_new_email_with_token(token):
    user_id, new_email_from_token = User.verify_confirm_new_email_token(token)

    if not user_id or not new_email_from_token:
        flash('The email confirmation link is invalid or has expired.', 'danger')
        return redirect(url_for('main.home')) # Or login page

    user = db.session.get(User, user_id)

    if not user:
        # This case should be rare if token verification implies a valid user_id was encoded
        flash('User not found. The email confirmation link may be invalid.', 'danger')
        return redirect(url_for('main.home'))

    # Security and integrity checks
    if user.pending_email != new_email_from_token:
        flash('This email confirmation link does not match the pending email change request or has already been used.', 'warning')
        return redirect(url_for('main.profile') if current_user.is_authenticated and current_user.id == user.id else url_for('main.home'))

    # Final check: Has the new email been taken by *another* user since the token was issued?
    other_user_with_new_email = User.query.filter(User.email == new_email_from_token, User.id != user.id).first()
    if other_user_with_new_email:
        user.pending_email = None # Clear the pending email as it's no longer valid for this user
        db.session.commit()
        flash(f"The email address '{new_email_from_token}' has recently been registered by another account. Your email change could not be completed. Please try updating your email again with a different address if needed.", 'danger')
        return redirect(url_for('main.profile') if current_user.is_authenticated and current_user.id == user.id else url_for('main.home'))

    # All checks passed, update the email
    user.email = user.pending_email # Which is new_email_from_token
    user.pending_email = None # Clear the pending email

    try:
        db.session.commit()
        flash('Your email address has been successfully confirmed and updated!', 'success')
        # If the user performing this action is the one logged in, they are fine.
        # If they were not logged in, they can now log in with their (potentially new) email if login uses email.
        # Our login currently uses username.
        if current_user.is_authenticated and current_user.id == user.id:
            return redirect(url_for('main.profile'))
        else:
            return redirect(url_for('auth.login')) # Good place to go after confirming
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error finalizing email change for user {user.id}: {e}")
        flash('An error occurred while updating your email. Please try again.', 'danger')
        return redirect(url_for('main.home'))

# Send Password Change Link Route

@auth_bp.route('/send-change-password-link', methods=['POST'])
@login_required
def send_change_password_link_route(): # Renamed for clarity
    # The user is logged in, so current_user is available.
    # We reuse the send_password_reset_email function as it generates the correct type of token and link.
    send_password_reset_email(current_user) 
    flash(f'A password change link has been sent to your email address: {current_user.email}', 'info')
    return redirect(url_for('main.profile'))

# Confirm Account Registration Email

def send_account_confirmation_email(user_instance):
    token = user_instance.get_email_confirmation_token()
    confirm_url = url_for('auth.confirm_email_from_token', token=token, _external=True)

    # CORRECTED: Use your existing template file
    html_body = render_template('emails/confirm_registration_email.html', 
                                user=user_instance, 
                                confirm_url=confirm_url)

    # --- THIS IS THE ONLY PART THAT CHANGES ---
    send_email(to_email=user_instance.email,
               subject='Confirm Your WearHouse Account Email',
               html_content=html_body)

# Confirm Regisration with Token Route

@auth_bp.route('/confirm-email/<token>', methods=['GET'])
# No @login_required, as user clicks this from an email and might not be logged in
def confirm_email_from_token(token):
    user_to_confirm = User.verify_email_confirmation_token(token) # This method now returns the User object or None

    if not user_to_confirm:
        flash('The email confirmation link is invalid or has expired. Please try registering again or request a new link if applicable.', 'danger')
        return redirect(url_for('auth.register')) # Or 'main.home' or 'auth.login'

    if user_to_confirm.is_email_confirmed:
        flash('Your email address has already been confirmed. Please log in.', 'info')
        return redirect(url_for('auth.login'))

    user_to_confirm.is_email_confirmed = True
    try:
        db.session.commit()
        flash('Your email address has been successfully confirmed! You can now log in.', 'success')
        return redirect(url_for('auth.login'))
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error confirming email for user {user_to_confirm.id if user_to_confirm else 'unknown'}: {e}")
        flash('An error occurred while confirming your email. Please try again or contact support.', 'danger')
        return redirect(url_for('auth.register')) # Or 'main.home'