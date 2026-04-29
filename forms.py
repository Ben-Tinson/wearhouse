# forms.py
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed, FileRequired # For file uploads
from datetime import datetime
import re

from wtforms import StringField, PasswordField, SubmitField, SelectField, DateField, DecimalField, RadioField, URLField, BooleanField, TextAreaField # Added more field types
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


CURRENCY_CHOICES = [
    ('GBP', '£ GBP'),
    ('USD', '$ USD'),
    ('EUR', '€ EUR'),
]

REGION_CHOICES = [
    ('UK', 'UK'),
    ('US', 'US'),
    ('EU', 'EU'),
]
class SneakerForm(FlaskForm):
    brand = StringField('Brand', validators=[DataRequired(), Length(max=150)])
    model = StringField('Model', validators=[DataRequired(), Length(max=150)])
    sku = StringField('SKU', validators=[Optional(), Length(max=50)])
    colorway = StringField('Colorway', validators=[Optional(), Length(max=150)])
    size_type = SelectField('Size Type', choices=[('UK', 'UK'), ('US M', "US Men's"), ('US W', "US Women's"), ('EU', 'EU'), ('CM', 'CM'), ('KR', 'KR')], validators=[Optional()])
    size = StringField('Size', validators=[Optional(), Length(max=20)])
    purchase_date = DateField('Purchase Date', format='%Y-%m-%d', validators=[Optional()])
    purchase_price = DecimalField('Purchase Price', places=2, validators=[Optional()])
    purchase_currency = SelectField('Currency', choices=CURRENCY_CHOICES, validators=[Optional()])
    condition = SelectField('Condition', choices=[("", "Select..."), ('Deadstock', 'Deadstock'), ('Near New', 'Near New'), ('Lightly Worn', 'Lightly Worn'), ('Heavily Worn', 'Heavily Worn'), ('Beater', 'Beater')], validators=[Optional()])
    last_worn_date = DateField('Last Worn Date', format='%Y-%m-%d', validators=[Optional()])
    image_option = RadioField('Image Source', choices=[('url', 'Link to URL'), ('upload', 'Upload File')], default='url')
    sneaker_image_url = StringField('Image URL', validators=[Optional(), Length(max=1024)], render_kw={"id": "modal_sneaker_image_url"})
    sneaker_image_file = FileField('Image File', validators=[Optional(), FileAllowed(['jpg', 'jpeg', 'png', 'gif', 'webp'], 'Images only!')], render_kw={"id": "modal_sneaker_image_file"})

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
    preferred_region = SelectField('Region / Market', choices=REGION_CHOICES, validators=[DataRequired()])
    # --- END OF NEW FIELDS ---
    password = PasswordField('Password',
                             validators=[DataRequired(), Length(min=6, message='Password must be at least 6 characters long.')])
    confirm_password = PasswordField('Confirm Password',
                                     validators=[DataRequired(),
                                                 EqualTo('password', message='Passwords must match.')])
    marketing_opt_in = BooleanField("I'd like to receive marketing communications from Soletrak.")
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
    username = StringField('Username',
                             validators=[DataRequired(), Length(max=80)])
    first_name = StringField('First Name',
                             validators=[DataRequired(), Length(max=50)])
    last_name = StringField('Last Name',
                            validators=[DataRequired(), Length(max=50)])
    email = StringField('Email Address',
                        validators=[DataRequired(message="Please enter your email address."),
                                    Email(message="Please enter a valid email address.")])
    marketing_opt_in = BooleanField("I'd like to receive marketing communications and newsletters from Soletrak.")
    preferred_currency = SelectField('Preferred Currency', choices=CURRENCY_CHOICES, validators=[Optional()])
    preferred_region = SelectField('Region / Market', choices=REGION_CHOICES, validators=[DataRequired()])
    submit = SubmitField('Update Profile')
    
    def validate_username(self, username):
        if username.data != current_user.username:
            user = User.query.filter_by(username=username.data).first()
            if user:
                raise ValidationError('That username is already taken.')

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

class MobileTokenForm(FlaskForm):
    name = StringField('Device name (optional)', validators=[Optional(), Length(max=100)])
    submit = SubmitField('Create token')

class ReleaseForm(FlaskForm):
    model_name = StringField('Model', validators=[DataRequired()])
    name = StringField('Display Name (optional)', validators=[Optional()])
    brand = StringField('Brand', validators=[DataRequired()])
    sku = StringField('SKU', validators=[DataRequired()])
    colorway = StringField('Colorway', validators=[Optional()])
    release_date = DateField('Release Date (fallback)', format='%Y-%m-%d', validators=[Optional()])
    retail_currency = SelectField('Currency', choices=CURRENCY_CHOICES, validators=[Optional()])
    retail_price = DecimalField('Retail Price', places=2, validators=[Optional()])
    description = TextAreaField('Description', validators=[Optional()])
    notes = TextAreaField('Notes', validators=[Optional()])
    stockx_url = StringField('StockX URL', validators=[Optional(), URL()])
    goat_url = StringField('GOAT URL', validators=[Optional(), URL()])

    us_release_date = DateField('US Release Date', format='%Y-%m-%d', validators=[Optional()])
    us_release_time = StringField('US Release Time (HH:MM)', validators=[Optional()])
    us_timezone = StringField('US Timezone', validators=[Optional()])
    us_retail_price = DecimalField('US Retail Price', places=2, validators=[Optional()])
    us_currency = SelectField('US Currency', choices=CURRENCY_CHOICES, validators=[Optional()])
    us_retailer_links = TextAreaField('US Retailer Links', validators=[Optional()])
    apply_us_date_to_uk = BooleanField('Apply US date to UK')
    apply_us_date_to_eu = BooleanField('Apply US date to EU')

    uk_release_date = DateField('UK Release Date', format='%Y-%m-%d', validators=[Optional()])
    uk_release_time = StringField('UK Release Time (HH:MM)', validators=[Optional()])
    uk_timezone = StringField('UK Timezone', validators=[Optional()])
    uk_retail_price = DecimalField('UK Retail Price', places=2, validators=[Optional()])
    uk_currency = SelectField('UK Currency', choices=CURRENCY_CHOICES, validators=[Optional()])
    uk_retailer_links = TextAreaField('UK Retailer Links', validators=[Optional()])
    apply_uk_date_to_us = BooleanField('Apply UK date to US')
    apply_uk_date_to_eu = BooleanField('Apply UK date to EU')

    eu_release_date = DateField('EU Release Date', format='%Y-%m-%d', validators=[Optional()])
    eu_release_time = StringField('EU Release Time (HH:MM)', validators=[Optional()])
    eu_timezone = StringField('EU Timezone', validators=[Optional()])
    eu_retail_price = DecimalField('EU Retail Price', places=2, validators=[Optional()])
    eu_currency = SelectField('EU Currency', choices=CURRENCY_CHOICES, validators=[Optional()])
    eu_retailer_links = TextAreaField('EU Retailer Links', validators=[Optional()])
    apply_eu_date_to_us = BooleanField('Apply EU date to US')
    apply_eu_date_to_uk = BooleanField('Apply EU date to UK')

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


class ReleaseCsvImportForm(FlaskForm):
    csv_file = FileField(
        'CSV File',
        validators=[
            FileRequired(),
            FileAllowed(['csv'], 'CSV files only.'),
        ],
    )
    skip_existing = BooleanField('Skip rows that match existing releases')
    submit = SubmitField('Preview Import')


class DeleteAllReleasesForm(FlaskForm):
    confirmation = StringField(
        'Type DELETE ALL RELEASES to confirm',
        validators=[DataRequired(), Length(max=50)],
    )
    submit = SubmitField('Delete All Releases')


class FlexibleDateField(DateField):
    def __init__(self, *args, **kwargs):
        self.extra_formats = kwargs.pop("extra_formats", [])
        super().__init__(*args, **kwargs)

    def process_formdata(self, valuelist):
        if not valuelist:
            self.data = None
            return
        value = " ".join(valuelist).strip()
        if not value:
            self.data = None
            return
        if len(value) >= 10:
            prefix = value[:10]
            if re.match(r"^\\d{4}-\\d{2}-\\d{2}$", prefix):
                value = prefix
        formats = [self.format] + list(self.extra_formats)
        for fmt in formats:
            try:
                self.data = datetime.strptime(value, fmt).date()
                return
            except (ValueError, TypeError):
                continue
        self.data = None
        return


class ArticleForm(FlaskForm):
    title = StringField('Title', validators=[DataRequired(), Length(max=255)])
    slug = StringField('Slug', validators=[Optional(), Length(max=255)])
    brand = StringField('Brand', validators=[Optional(), Length(max=150)])
    excerpt = TextAreaField('Excerpt', validators=[Optional(), Length(max=500)])
    tags = StringField('Tags (comma separated)', validators=[Optional(), Length(max=500)])
    meta_title = StringField('Meta title', validators=[Optional(), Length(max=70)])
    meta_description = StringField('Meta description', validators=[Optional(), Length(max=300)])
    canonical_url = StringField('Canonical URL', validators=[Optional(), Length(max=1024)])
    robots = SelectField(
        'Robots',
        choices=[
            ('index,follow', 'Index, follow'),
            ('noindex,follow', 'No index, follow'),
            ('index,nofollow', 'Index, no follow'),
            ('noindex,nofollow', 'No index, no follow'),
        ],
        default='index,follow',
        validators=[Optional()],
    )
    og_title = StringField('OG title', validators=[Optional(), Length(max=255)])
    og_description = StringField('OG description', validators=[Optional(), Length(max=300)])
    og_image_url = StringField('OG image URL', validators=[Optional(), Length(max=1024)])
    twitter_card = SelectField(
        'Twitter card',
        choices=[
            ('summary_large_image', 'Summary large image'),
            ('summary', 'Summary'),
        ],
        default='summary_large_image',
        validators=[Optional()],
    )
    product_schema_json = TextAreaField('Product schema JSON-LD', validators=[Optional()])
    faq_schema_json = TextAreaField('FAQ schema JSON-LD', validators=[Optional()])
    video_schema_json = TextAreaField('Video schema JSON-LD', validators=[Optional()])
    published_at = FlexibleDateField(
        'Published Date',
        format='%Y-%m-%d',
        extra_formats=['%Y-%m-%d %H:%M'],
        validators=[Optional()],
    )
    is_published = BooleanField('Published')
    hero_image_option = RadioField(
        'Hero Image Source',
        choices=[('url', 'Link to Image URL'), ('upload', 'Upload Image File')],
        default='upload',
        validators=[Optional()]
    )
    hero_image_url = StringField('Hero Image URL', validators=[Optional(), URL(), Length(max=1024)])
    hero_image_file = FileField('Hero Image File', validators=[
        FileAllowed(['jpg', 'jpeg', 'png', 'gif', 'avif', 'webp'], 'Images only! (jpg, png, gif, avif, webp)'),
        Optional()
    ])
    hero_image_alt = StringField('Hero image alt text', validators=[Optional(), Length(max=255)])
    author_name = StringField('Author name', validators=[Optional(), Length(max=120)])
    author_title = StringField('Author title', validators=[Optional(), Length(max=120)])
    author_bio = TextAreaField('Author bio', validators=[Optional(), Length(max=500)])
    author_image_option = RadioField(
        'Author Image Source',
        choices=[('url', 'Link to Image URL'), ('upload', 'Upload Image File')],
        default='upload',
        validators=[Optional()]
    )
    author_image_url = StringField('Author Image URL', validators=[Optional(), URL(), Length(max=1024)])
    author_image_file = FileField('Author Image File', validators=[
        FileAllowed(['jpg', 'jpeg', 'png', 'gif', 'avif', 'webp'], 'Images only! (jpg, png, gif, avif, webp)'),
        Optional()
    ])
    author_image_alt = StringField('Author image alt text', validators=[Optional(), Length(max=255)])
    submit = SubmitField('Save Article')


class FXRateForm(FlaskForm):
    base_currency = SelectField('Base Currency', choices=CURRENCY_CHOICES, validators=[DataRequired()])
    quote_currency = SelectField('Quote Currency', choices=CURRENCY_CHOICES, validators=[DataRequired()])
    rate = DecimalField('Rate', places=6, validators=[DataRequired(), NumberRange(min=0.000001)])
    submit = SubmitField('Save Rate')


class DamageReportForm(FlaskForm):
    damage_type = SelectField(
        'Damage type',
        choices=[
            ('tear_upper', 'Tear (Upper/Knit)'),
            ('upper_scuff', 'Upper scuff / abrasion (leather/suede)'),
            ('upper_paint_chip', 'Upper paint chip / colour loss'),
            ('sole_separation', 'Sole separation'),
            ('midsole_crumble', 'Midsole crumbling'),
            ('midsole_scuff', 'Midsole scuff / marks'),
            ('midsole_paint_chip', 'Midsole paint chip / peeling'),
            ('outsole_wear', 'Outsole wear (balding)'),
            ('other', 'Other'),
        ],
        validators=[DataRequired()],
    )
    severity = SelectField(
        'Severity',
        choices=[('1', 'Light'), ('2', 'Moderate'), ('3', 'Severe')],
        validators=[DataRequired()],
    )
    notes = TextAreaField('Notes', validators=[Optional(), Length(max=280)])
    submit = SubmitField('Report damage')

    def validate_notes(self, field):
        if self.damage_type.data == 'other':
            if not field.data or not field.data.strip():
                raise ValidationError('Please provide details for "Other" damage.')


class RepairEventForm(FlaskForm):
    repair_kind = SelectField(
        'Repair kind',
        choices=[('repair', 'Repair'), ('restoration', 'Full restoration')],
        validators=[DataRequired()],
    )
    repair_type = SelectField(
        'Repair type',
        choices=[
            ('stitching', 'Stitching'),
            ('patch', 'Patch'),
            ('glue_sole', 'Glue / Sole separation fix'),
            ('midsole_repair', 'Midsole repair'),
            ('repaint', 'Repaint / touch-up'),
            ('sole_swap', 'Sole swap'),
            ('deep_clean', 'Deep clean'),
            ('lace_replacement', 'Lace replacement'),
            ('insole_replacement', 'Insole replacement'),
            ('waterproofing', 'Waterproofing treatment'),
            ('full_restoration', 'Full restoration'),
            ('other', 'Other'),
        ],
        validators=[DataRequired()],
    )
    repair_type_other = StringField('Other repair type', validators=[Optional(), Length(max=120)])
    provider = SelectField(
        'Provider',
        choices=[
            ('self', 'Self / DIY'),
            ('local_cobbler', 'Local cobbler'),
            ('specialist_restorer', 'Specialist sneaker restorer'),
            ('brand', 'Brand (e.g. Nike)'),
            ('retailer', 'Retailer service'),
            ('other', 'Other'),
        ],
        validators=[Optional()],
    )
    provider_other = StringField('Other provider', validators=[Optional(), Length(max=120)])
    repair_area = SelectField(
        'Repair area',
        choices=[
            ('upper', 'Upper'),
            ('midsole', 'Midsole'),
            ('outsole', 'Outsole'),
            ('insole', 'Insole'),
            ('lace', 'Lace'),
            ('other', 'Other'),
        ],
        validators=[Optional()],
    )
    cost_amount = DecimalField('Cost', places=2, validators=[Optional(), NumberRange(min=0)])
    cost_currency = SelectField('Currency', choices=CURRENCY_CHOICES, validators=[Optional()])
    notes = TextAreaField('Notes', validators=[Optional(), Length(max=280)])
    resolved_all_active_damage = BooleanField('Resolve all active damage', default=True)
    submit = SubmitField('Save')

    def validate_repair_type_other(self, field):
        if self.repair_kind.data == 'restoration':
            return
        if self.repair_type.data == 'other':
            if not field.data or not field.data.strip():
                raise ValidationError('Please specify the repair type.')

    def validate_provider_other(self, field):
        if self.provider.data == 'other':
            if not field.data or not field.data.strip():
                raise ValidationError('Please specify the provider.')
