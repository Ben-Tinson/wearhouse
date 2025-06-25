# forms.py
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed # For file uploads
from wtforms import StringField, PasswordField, SubmitField, SelectField, DateField, DecimalField, RadioField, URLField, TextAreaField, BooleanField # Added more field types
from wtforms.validators import DataRequired, Length, EqualTo, Optional, URL, NumberRange, ValidationError, Email # Added Optional, URL, NumberRange
from models import User
from flask_login import current_user

# Helper function for size validation (can be moved or kept here)
def validate_sneaker_size(form, field):
    if field.data and field.data.strip():
        try:
            val_str = field.data.strip()
            val_float = float(val_str)
            if not ((val_float * 2) % 1 == 0): # Checks for whole or half number
                raise ValidationError('Size must be a whole or half number (e.g., 9, 9.5).')
        except ValueError:
            raise ValidationError('Invalid size format. Size must be a numeric value.')

class SneakerForm(FlaskForm):
    brand = StringField('Brand', validators=[DataRequired(), Length(max=100)])
    model = StringField('Model', validators=[DataRequired(), Length(max=100)])
    colorway = StringField('Colorway', validators=[Optional(), Length(max=100)])

    size_type_choices = [
        ('UK', 'UK'), ('US M', "US Men's"), 
        ('US W', "US Women's"), ('EU', 'EU'), ('CM', 'CM'), 
        ('KR', 'KR')
    ]
    size_type = SelectField('Size Type', choices=size_type_choices, validators=[Optional()])
    size = StringField('Size Value', validators=[Optional(), Length(max=20), validate_sneaker_size]) # Custom validator

    last_worn_date = DateField('Last Worn Date', validators=[Optional()], format='%Y-%m-%d')

    purchase_currency_choices = [
        ('GBP', '£ GBP'), ('USD', '$ USD'), ('EUR', '€ EUR'),
        ('JPY', '¥ JPY'), ('CAD', 'C$ CAD'), ('AUD', 'A$ AUD'), ('KRW', '₩ KRW')
    ]
    purchase_currency = SelectField('Currency', choices=purchase_currency_choices, validators=[Optional()])
    purchase_price = DecimalField('Purchase Price', validators=[Optional(), NumberRange(min=0)], places=2) # Allows 2 decimal places

    condition_choices = [
        ("", "Select Condition..."), ('Deadstock', 'Deadstock'), ('Near New', 'Near New'),
        ('Lightly Worn', 'Lightly Worn'), ('Heavily Worn', 'Heavily Worn')
    ]
    condition = SelectField('Condition', choices=condition_choices, validators=[Optional()])
    purchase_date = DateField('Purchase Date', validators=[Optional()], format='%Y-%m-%d')

    image_option = RadioField('Image Source', choices=[('url', 'Link to Image URL'), ('upload', 'Upload Image File')], 
                              default='url', validators=[Optional()]) # Optional now, logic in route will handle
    sneaker_image_url = URLField('Image URL', validators=[Optional(), URL(message="Invalid URL format.")])
    sneaker_image_file = FileField('Image File', validators=[
        Optional(),
        FileAllowed(['jpg', 'jpeg', 'png', 'gif', 'avif'], 'Images only! (jpg, png, gif, avif)')
    ])

    submit = SubmitField('Save Sneaker') # Generic name for add/edit
    pass

# Your existing LoginForm should be here
class LoginForm(FlaskForm):
    username = StringField('Username', 
                           validators=[DataRequired(), Length(min=4, max=80)])
    password = PasswordField('Password', 
                             validators=[DataRequired(), Length(min=6)])
    submit = SubmitField('Login')
    pass

# --- REGISTRATION FORM ---
class RegistrationForm(FlaskForm):
    username = StringField('Username',
                           validators=[DataRequired(), Length(min=4, max=80)])
    # --- NEW FIELDS ---
    email = StringField('Email Address', # Using StringField with Email validator
                        validators=[DataRequired(message="Please enter your email address."), 
                                    Email(message="Please enter a valid email address.")])
    first_name = StringField('First Name', 
                             validators=[DataRequired(), Length(max=50)])
    last_name = StringField('Last Name', 
                            validators=[DataRequired(), Length(max=50)])
    # --- END OF NEW FIELDS ---
    password = PasswordField('Password',
                             validators=[DataRequired(), Length(min=6, message='Password must be at least 6 characters long.')])
    confirm_password = PasswordField('Confirm Password',
                                     validators=[DataRequired(),
                                                 EqualTo('password', message='Passwords must match.')])
    marketing_opt_in = BooleanField("I'd like to receive marketing communications from WearHouse.")
    submit = SubmitField('Register')
    pass

# Request Password Reset Form
class RequestResetForm(FlaskForm):
    email = StringField('Email Address',
                        validators=[DataRequired(message="Please enter your email address."),
                                    Email(message="Please enter a valid email address.")])
    submit = SubmitField('Request Password Reset')
    pass

# Reset Password Form
class ResetPasswordForm(FlaskForm):
    password = PasswordField('New Password',
                             validators=[DataRequired(), 
                                         Length(min=6, message='Password must be at least 6 characters long.')])
    confirm_password = PasswordField('Confirm New Password',
                                     validators=[DataRequired(),
                                                 EqualTo('password', message='Passwords must match.')])
    submit = SubmitField('Reset Password')
    pass

# Edit Profile Form

class EditProfileForm(FlaskForm):
    first_name = StringField('First Name',
                             validators=[DataRequired(), Length(max=50)])
    last_name = StringField('Last Name',
                            validators=[DataRequired(), Length(max=50)])
    email = StringField('Email Address',
                        validators=[DataRequired(message="Please enter your email address."),
                                    Email(message="Please enter a valid email address.")])
    marketing_opt_in = BooleanField("I'd like to receive marketing communications and newsletters from WearHouse.")
    submit = SubmitField('Update Profile')
    
    def validate_email(self, email):
        # We only need to check for duplicates if the user is changing their email.
        if email.data != current_user.email:
            # Check if the new email is already taken by another user.
            user = User.query.filter_by(email=email.data).first()
            if user:
                raise ValidationError('That email address is already registered.')

class EmptyForm(FlaskForm):
    # This form is intentionally empty. 
    # Its purpose is to provide form.hidden_tag() for CSRF protection
    # on simple forms that only have a submit button.
    pass

class ReleaseForm(FlaskForm):
    name = StringField('Sneaker Name', validators=[DataRequired()])
    brand = StringField('Brand', validators=[DataRequired()])
    release_date = DateField('Release Date', format='%Y-%m-%d', validators=[DataRequired()])
    retail_currency = SelectField('Currency', choices=[('GBP', '£ GBP'), ('USD', '$ USD'), ('EUR', '€ EUR'),
        ('JPY', '¥ JPY'), ('CAD', 'C$ CAD'), ('AUD', 'A$ AUD'), ('KRW', '₩ KRW')], validators=[Optional()]) # Keep your existing choices
    retail_price = DecimalField('Retail Price', places=2, validators=[Optional()])

    # --- NEW & MODIFIED IMAGE FIELDS ---
    image_option = RadioField(
        'Image Source', 
        choices=[('url', 'Link to Image URL'), ('upload', 'Upload Image File')],
        default='url',
        validators=[DataRequired()]
    )
    image_url = StringField('Image URL', validators=[Optional(), URL()])

    sneaker_image_file = FileField('Image File', validators=[
        FileAllowed(
            ['jpg', 'jpeg', 'png', 'gif', 'avif'], 
            'Images only! (jpg, png, gif, avif)'
        ), 
        Optional()
    ])
    # --- END OF NEW & MODIFIED FIELDS ---

    submit = SubmitField('Add Release')





