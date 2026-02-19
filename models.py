from datetime import datetime
from extensions import db # Import db from our new extensions.py
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous.url_safe import URLSafeTimedSerializer as Serializer
from flask import current_app

wishlist_items = db.Table('wishlist_items',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('release_id', db.Integer, db.ForeignKey('release.id'), primary_key=True),
    db.Column('created_at', db.DateTime, nullable=False, server_default=db.func.now())
)

# --- Database Models ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    marketing_opt_in = db.Column(db.Boolean, nullable=False, default=False)
    pending_email = db.Column(db.String(120), unique=True, nullable=True) 
    # unique=True to prevent multiple users trying to verify the same pending email simultaneously.
    # nullable=True as it's only populated during an email change process.

    is_email_confirmed = db.Column(db.Boolean, nullable=False, default=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    preferred_currency = db.Column(db.String(3), nullable=False, default="GBP")
    timezone = db.Column(db.String(64), nullable=False, default="Europe/London")


    sneakers = db.relationship('Sneaker', backref='owner', lazy=True)
    wishlist = db.relationship('Release', secondary=wishlist_items, lazy='subquery',
        backref=db.backref('wishlisted_by', lazy=True))
    api_tokens = db.relationship(
        'UserApiToken',
        backref='user',
        lazy=True,
        cascade='all, delete-orphan',
        order_by="desc(UserApiToken.created_at)",
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_reset_password_token(self, expires_sec=1800): # Token expires in 30 minutes (1800 seconds)
        s = Serializer(current_app.config['SECRET_KEY'])
        # The token will contain the user's ID.
        # We add a 'salt' to namespace this token specifically for password resets.
        return s.dumps(self.id, salt='password-reset-salt')

    @staticmethod
    def verify_reset_password_token(token, expires_sec=1800):
        s = Serializer(current_app.config['SECRET_KEY'])
        try:
            # Loads the token, checks signature, salt, and expiry (max_age)
            user_id = s.loads(token, salt='password-reset-salt', max_age=expires_sec)
        except Exception: # Catches BadSignature, SignatureExpired, etc.
            return None
        return db.session.get(User, user_id)

    def get_confirm_new_email_token(self, new_email, expires_sec=3600): # Token expires in 1 hour
        s = Serializer(current_app.config['SECRET_KEY'])
        return s.dumps({'user_id': self.id, 'new_email': new_email}, salt='confirm-new-email-salt')

    @staticmethod
    def verify_confirm_new_email_token(token, expires_sec=3600):
        s = Serializer(current_app.config['SECRET_KEY'])
        try:
            data = s.loads(token, salt='confirm-new-email-salt', max_age=expires_sec)
            user_id = data.get('user_id')
            new_email = data.get('new_email')
        except Exception: # Catches BadSignature, SignatureExpired, etc.
            return None, None 
        return user_id, new_email

    @staticmethod
    def verify_reset_password_token(token, expires_sec=1800): # Ensure default expiry matches get_reset_password_token if not explicitly passed
        s = Serializer(current_app.config['SECRET_KEY'])
        print(f"DEBUG: Verifying token: {token}") # 1. What token is received?
        print(f"DEBUG: SECRET_KEY used for Serializer: {'*' * len(current_app.config['SECRET_KEY'])}") # 2. Just to confirm key is loaded
        print(f"DEBUG: Max age for token verification: {expires_sec} seconds") # 3. What is max_age?
        try:
            user_id = s.loads(token, salt='password-reset-salt', max_age=expires_sec)
            print(f"DEBUG: Token loaded successfully. User ID: {user_id}") # 4. Success?
        except Exception as e:
            print(f"DEBUG: ERROR loading token: {e}") # 5. What's the error? (e.g., SignatureExpired, BadSignature)
            return None
        return db.session.get(User, user_id)

    def get_email_confirmation_token(self, expires_sec=86400): # 24 hour expiry for initial confirmation
        s = Serializer(current_app.config['SECRET_KEY'])
        return s.dumps({'user_id': self.id, 'action': 'confirm_email'}, salt='email-confirmation-salt')

    @staticmethod
    def verify_email_confirmation_token(token, expires_sec=86400):
        s = Serializer(current_app.config['SECRET_KEY'])
        try:
            data = s.loads(token, salt='email-confirmation-salt', max_age=expires_sec)
            if data.get('action') != 'confirm_email': # Ensure it's the right type of token
                return None
            user_id = data.get('user_id')
        except Exception:
            return None
        return db.session.get(User, user_id) # Return the user object directly

    def __repr__(self):
        return f'<User {self.username}>'

    # We will add methods for password reset tokens here later

class UserApiToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    scopes = db.Column(db.String(200), nullable=False, default="steps:write")
    last_used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    revoked_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.Index('ix_user_api_token_user_revoked', 'user_id', 'revoked_at'),
    )

    def __repr__(self):
        return f'<UserApiToken {self.user_id} {self.name}>'

class Sneaker(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    brand = db.Column(db.String(100), nullable=False)
    model = db.Column(db.String(100), nullable=False)
    sku = db.Column(db.String(50), nullable=True, index=True)
    colorway = db.Column(db.String(100), nullable=True)
    size = db.Column(db.String(20), nullable=True)
    size_type = db.Column(db.String(15), nullable=True) # E.g., "UK", "US Men's", "EU" 
    last_worn_date = db.Column(db.Date, nullable=True) # Make sure this is db.Date
    image_url = db.Column(db.String(255), nullable=True) # Stores external URL or local filename
    purchase_price = db.Column(db.Numeric(10, 2), nullable=True)  # For currency, e.g., 12345678.90
    purchase_currency = db.Column(db.String(3), nullable=True) # E.g., "USD", "GBP", "EUR"
    price_paid_currency = db.Column(db.String(3), nullable=True)
    condition = db.Column(db.String(50), nullable=True)          # E.g., "New", "Used - Like New", "Good"
    purchase_date = db.Column(db.Date, nullable=True)
    last_cleaned_at = db.Column(db.DateTime, nullable=True)
    starting_health = db.Column(db.Float, nullable=False, default=100.0)
    persistent_stain_points = db.Column(db.Float, nullable=False, default=0.0)
    persistent_material_damage_points = db.Column(db.Float, nullable=False, default=0.0)
    persistent_structural_damage_points = db.Column(db.Float, nullable=False, default=0.0)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    in_rotation = db.Column(db.Boolean, nullable=False, default=False, index=True)
    note_entries = db.relationship(
        'SneakerNote',
        backref='sneaker',
        lazy=True,
        cascade='all, delete-orphan',
        order_by="desc(SneakerNote.created_at)",
    )
    sales = db.relationship(
        'SneakerSale',
        backref='sneaker',
        lazy=True,
        cascade='save-update, merge',
        order_by="desc(SneakerSale.sold_at)",
    )
    wears = db.relationship(
        'SneakerWear',
        backref='sneaker',
        lazy=True,
        cascade='all, delete-orphan',
        order_by="desc(SneakerWear.worn_at)",
    )


    def __repr__(self):
        return f'<Sneaker {self.brand} {self.model}>'


class SneakerNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sneaker_id = db.Column(db.Integer, db.ForeignKey('sneaker.id'), nullable=False, index=True)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<SneakerNote {self.sneaker_id}>'


class SneakerSale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sneaker_id = db.Column(db.Integer, db.ForeignKey('sneaker.id'), nullable=True, index=True)
    release_id = db.Column(db.Integer, db.ForeignKey('release.id'), nullable=True, index=True)
    size_label = db.Column(db.String(50), nullable=True)
    size_type = db.Column(db.String(20), nullable=True)
    sold_price = db.Column(db.Numeric(10, 2), nullable=False)
    sold_currency = db.Column(db.String(3), nullable=False, default="USD")
    purchase_price = db.Column(db.Numeric(10, 2), nullable=True)
    purchase_currency = db.Column(db.String(3), nullable=True)
    sold_at = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f'<SneakerSale {self.sneaker_id} {self.sold_at}>'

class SneakerWear(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sneaker_id = db.Column(db.Integer, db.ForeignKey('sneaker.id'), nullable=False, index=True)
    worn_at = db.Column(db.Date, nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f'<SneakerWear {self.sneaker_id} {self.worn_at}>'


class SneakerCleanEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    sneaker_id = db.Column(db.Integer, db.ForeignKey('sneaker.id'), nullable=False, index=True)
    cleaned_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    stain_removed = db.Column(db.Boolean, nullable=True)
    lasting_material_impact = db.Column(db.Boolean, nullable=False, default=False)
    notes = db.Column(db.String(280), nullable=True)

    def __repr__(self):
        return f'<SneakerCleanEvent {self.sneaker_id} {self.cleaned_at}>'


class SneakerHealthSnapshot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sneaker_id = db.Column(db.Integer, db.ForeignKey('sneaker.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    recorded_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    health_score = db.Column(db.Float, nullable=False)
    wear_penalty = db.Column(db.Float, nullable=True)
    cosmetic_penalty = db.Column(db.Float, nullable=True)
    structural_penalty = db.Column(db.Float, nullable=True)
    hygiene_penalty = db.Column(db.Float, nullable=True)
    steps_total_used = db.Column(db.Integer, nullable=True)
    confidence_score = db.Column(db.Float, nullable=True)
    confidence_label = db.Column(db.String(20), nullable=True)
    reason = db.Column(db.String(40), nullable=False)

    def __repr__(self):
        return f'<SneakerHealthSnapshot {self.sneaker_id} {self.health_score}>'


class SneakerDamageEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    sneaker_id = db.Column(db.Integer, db.ForeignKey('sneaker.id'), nullable=False, index=True)
    reported_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    damage_type = db.Column(db.String(50), nullable=False)
    severity = db.Column(db.Integer, nullable=False)
    notes = db.Column(db.String(280), nullable=True)
    photo_url = db.Column(db.String(1024), nullable=True)
    health_penalty_points = db.Column(db.Float, nullable=False, default=0.0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<SneakerDamageEvent {self.sneaker_id} {self.damage_type} {self.severity}>'


class SneakerRepairEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    sneaker_id = db.Column(db.Integer, db.ForeignKey('sneaker.id'), nullable=False, index=True)
    repaired_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    repair_kind = db.Column(db.String(20), nullable=False)
    repair_type = db.Column(db.String(100), nullable=False)
    repair_type_other = db.Column(db.String(120), nullable=True)
    provider = db.Column(db.String(120), nullable=True)
    provider_other = db.Column(db.String(120), nullable=True)
    repair_area = db.Column(db.String(30), nullable=True)
    baseline_delta_applied = db.Column(db.Float, nullable=True)
    cost_amount = db.Column(db.Numeric(10, 2), nullable=True)
    cost_currency = db.Column(db.String(3), nullable=True)
    notes = db.Column(db.String(280), nullable=True)
    resolved_all_active_damage = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<SneakerRepairEvent {self.sneaker_id} {self.repair_kind}>'


class SneakerRepairResolvedDamage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    repair_event_id = db.Column(db.Integer, db.ForeignKey('sneaker_repair_event.id'), nullable=False, index=True)
    damage_event_id = db.Column(db.Integer, db.ForeignKey('sneaker_damage_event.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('repair_event_id', 'damage_event_id', name='uq_repair_resolved_damage'),
    )

    def __repr__(self):
        return f'<SneakerRepairResolvedDamage {self.repair_event_id} {self.damage_event_id}>'


class SneakerExpense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    sneaker_id = db.Column(db.Integer, db.ForeignKey('sneaker.id'), nullable=False, index=True)
    category = db.Column(db.String(30), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    expense_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    notes = db.Column(db.String(280), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<SneakerExpense {self.sneaker_id} {self.category} {self.amount}>'


class StepBucket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    source = db.Column(db.String(50), nullable=False)
    granularity = db.Column(db.String(10), nullable=False)
    bucket_start = db.Column(db.DateTime, nullable=False, index=True)
    bucket_end = db.Column(db.DateTime, nullable=False)
    steps = db.Column(db.Integer, nullable=False, default=0)
    timezone = db.Column(db.String(64), nullable=False, default="Europe/London")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'source', 'granularity', 'bucket_start', name='uq_step_bucket_user_source_start'),
    )

    def __repr__(self):
        return f'<StepBucket {self.user_id} {self.granularity} {self.bucket_start}>'


class StepAttribution(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    sneaker_id = db.Column(db.Integer, db.ForeignKey('sneaker.id'), nullable=False, index=True)
    bucket_granularity = db.Column(db.String(10), nullable=False)
    bucket_start = db.Column(db.DateTime, nullable=False, index=True)
    steps_attributed = db.Column(db.Integer, nullable=False, default=0)
    algorithm_version = db.Column(db.String(50), nullable=False)
    computed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint(
            'user_id',
            'sneaker_id',
            'bucket_granularity',
            'bucket_start',
            'algorithm_version',
            name='uq_step_attr_user_sneaker_bucket_algo',
        ),
    )

    def __repr__(self):
        return f'<StepAttribution {self.user_id} {self.sneaker_id} {self.bucket_start}>'


class ExposureEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    date_local = db.Column(db.Date, nullable=False, index=True)
    timezone = db.Column(db.String(64), nullable=False, default="Europe/London")
    got_wet = db.Column(db.Boolean, nullable=False, default=False)
    got_dirty = db.Column(db.Boolean, nullable=False, default=False)
    stain_flag = db.Column(db.Boolean, nullable=False, default=False)
    wet_severity = db.Column(db.Integer, nullable=True)
    dirty_severity = db.Column(db.Integer, nullable=True)
    stain_severity = db.Column(db.Integer, nullable=True)
    note = db.Column(db.String(140), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'date_local', name='uq_exposure_event_user_date'),
    )

    def __repr__(self):
        return f'<ExposureEvent {self.user_id} {self.date_local}>'


class SneakerExposureAttribution(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    sneaker_id = db.Column(db.Integer, db.ForeignKey('sneaker.id'), nullable=False, index=True)
    date_local = db.Column(db.Date, nullable=False, index=True)
    wet_points = db.Column(db.Float, nullable=False, default=0.0)
    dirty_points = db.Column(db.Float, nullable=False, default=0.0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'sneaker_id', 'date_local', name='uq_exposure_attr_user_sneaker_date'),
    )

    def __repr__(self):
        return f'<SneakerExposureAttribution {self.user_id} {self.sneaker_id} {self.date_local}>'

class Release(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(50), nullable=True, index=True)
    brand = db.Column(db.String(100), nullable=True, index=True)
    name = db.Column(db.String(200), nullable=False)
    model_name = db.Column(db.String(200), nullable=True)
    colorway = db.Column(db.String(200), nullable=True)
    release_date = db.Column(db.Date, nullable=False, index=True)
    is_calendar_visible = db.Column(db.Boolean, nullable=False, default=True)
    # --- MODIFIED AND NEW LINES ---
    retail_price = db.Column(db.Numeric(10, 2), nullable=True) # Changed from String
    retail_currency = db.Column(db.String(10), nullable=True)   # New field
    image_url = db.Column(db.String(500), nullable=True)
    source = db.Column(db.String(50), nullable=True)
    source_product_id = db.Column(db.String(100), nullable=True)
    source_slug = db.Column(db.String(255), nullable=True)
    source_updated_at = db.Column(db.DateTime, nullable=True)
    last_synced_at = db.Column(db.DateTime, nullable=True)
    sales_last_fetched_at = db.Column(db.DateTime, nullable=True)
    size_bids_last_fetched_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow, onupdate=datetime.utcnow)
    # --- END OF CHANGES ---

    offers = db.relationship('AffiliateOffer', backref='release', lazy=True)
    prices = db.relationship('ReleasePrice', backref='release', lazy=True)
    size_bids = db.relationship('ReleaseSizeBid', backref='release', lazy=True, cascade='all, delete-orphan')
    sales_points = db.relationship('ReleaseSalePoint', backref='release', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Release {self.name}>'


class AffiliateOffer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    release_id = db.Column(db.Integer, db.ForeignKey('release.id'), nullable=False, index=True)
    retailer = db.Column(db.String(50), nullable=False)
    region = db.Column(db.String(10), nullable=True)
    base_url = db.Column(db.String(1024), nullable=False)
    affiliate_url = db.Column(db.String(1024), nullable=True)
    offer_type = db.Column(db.String(20), nullable=False, default="aftermarket")
    price = db.Column(db.Numeric(10, 2), nullable=True)
    currency = db.Column(db.String(3), nullable=True)
    status = db.Column(db.String(50), nullable=True)
    priority = db.Column(db.Integer, nullable=False, default=100)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    last_checked_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('release_id', 'retailer', 'region', name='uq_offer_release_retailer_region'),
    )

    def __repr__(self):
        return f'<AffiliateOffer {self.retailer} {self.release_id}>'


class ReleaseSizeBid(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    release_id = db.Column(db.Integer, db.ForeignKey('release.id'), nullable=False, index=True)
    size_label = db.Column(db.String(50), nullable=False)
    size_type = db.Column(db.String(20), nullable=True)
    highest_bid = db.Column(db.Numeric(10, 2), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="USD")
    price_type = db.Column(db.String(10), nullable=False, default="bid")
    fetched_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('release_id', 'size_label', 'size_type', name='uq_release_size_bid'),
    )

    def __repr__(self):
        return f'<ReleaseSizeBid {self.release_id} {self.size_label}>'


class ReleaseSalePoint(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    release_id = db.Column(db.Integer, db.ForeignKey('release.id'), nullable=False, index=True)
    sale_at = db.Column(db.DateTime, nullable=False, index=True)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="USD")
    fetched_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('release_id', 'sale_at', name='uq_release_sale_point'),
    )

    def __repr__(self):
        return f'<ReleaseSalePoint {self.release_id} {self.sale_at}>'


class ExchangeRate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    base_currency = db.Column(db.String(3), nullable=False, index=True)
    quote_currency = db.Column(db.String(3), nullable=False, index=True)
    rate = db.Column(db.Numeric(18, 6), nullable=False)
    as_of = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('base_currency', 'quote_currency', name='uq_exchange_rate_pair'),
    )

    def __repr__(self):
        return f'<ExchangeRate {self.base_currency}->{self.quote_currency}>'


class ReleasePrice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    release_id = db.Column(db.Integer, db.ForeignKey('release.id'), nullable=False, index=True)
    currency = db.Column(db.String(3), nullable=False)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    region = db.Column(db.String(10), nullable=True)
    created_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('release_id', 'currency', 'region', name='uq_release_price_currency_region'),
    )

    def __repr__(self):
        return f'<ReleasePrice {self.release_id} {self.currency}>'


class Article(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255), nullable=False, unique=True, index=True)
    excerpt = db.Column(db.Text, nullable=True)
    brand = db.Column(db.String(150), nullable=True, index=True)
    tags = db.Column(db.Text, nullable=True)
    hero_image_url = db.Column(db.String(1024), nullable=True)
    hero_image_alt = db.Column(db.String(255), nullable=True)
    author_name = db.Column(db.String(120), nullable=True)
    author_title = db.Column(db.String(120), nullable=True)
    author_bio = db.Column(db.Text, nullable=True)
    author_image_url = db.Column(db.String(1024), nullable=True)
    author_image_alt = db.Column(db.String(255), nullable=True)
    meta_title = db.Column(db.String(70), nullable=True)
    meta_description = db.Column(db.String(300), nullable=True)
    canonical_url = db.Column(db.String(1024), nullable=True)
    robots = db.Column(db.String(40), nullable=True)
    og_title = db.Column(db.String(255), nullable=True)
    og_description = db.Column(db.String(300), nullable=True)
    og_image_url = db.Column(db.String(1024), nullable=True)
    twitter_card = db.Column(db.String(40), nullable=True)
    product_schema_json = db.Column(db.Text, nullable=True)
    faq_schema_json = db.Column(db.Text, nullable=True)
    video_schema_json = db.Column(db.Text, nullable=True)
    published_at = db.Column(db.DateTime, nullable=True, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow, onupdate=datetime.utcnow)

    blocks = db.relationship(
        'ArticleBlock',
        backref='article',
        cascade='all, delete-orphan',
        order_by='ArticleBlock.position',
        lazy=True,
    )

    def __repr__(self):
        return f'<Article {self.title}>'


class ArticleBlock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    article_id = db.Column(db.Integer, db.ForeignKey('article.id'), nullable=False, index=True)
    position = db.Column(db.Integer, nullable=False)
    block_type = db.Column(db.String(50), nullable=False)
    heading_text = db.Column(db.Text, nullable=True)
    heading_level = db.Column(db.String(4), nullable=True)
    body_text = db.Column(db.Text, nullable=True)
    image_url = db.Column(db.String(1024), nullable=True)
    image_alt = db.Column(db.String(255), nullable=True)
    caption = db.Column(db.String(255), nullable=True)
    align = db.Column(db.String(20), nullable=True)
    carousel_images_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('article_id', 'position', name='uq_article_block_position'),
    )

    def __repr__(self):
        return f'<ArticleBlock {self.article_id} {self.position} {self.block_type}>'


class SiteSchema(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    schema_type = db.Column(db.String(50), nullable=False, unique=True, index=True)
    json_text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<SiteSchema {self.schema_type}>'


class UserApiUsage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    action = db.Column(db.String(50), nullable=False, index=True)
    usage_date = db.Column(db.Date, nullable=False, index=True)
    count = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'action', 'usage_date', name='uq_user_api_usage'),
    )

class SneakerDB(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    brand = db.Column(db.String(150), index=True)
    name = db.Column(db.String(255), index=True)
    model_name = db.Column(db.String(255), index=True)
    colorway = db.Column(db.String(255))
    gender = db.Column(db.String(20))
    description = db.Column(db.Text, nullable=True)
    release_date = db.Column(db.Date, nullable=True)
    retail_price = db.Column(db.Numeric(10, 2), nullable=True)
    retail_currency = db.Column(db.String(10), nullable=True)
    sku = db.Column(db.String(50), unique=True, nullable=False, index=True)
    stockx_id = db.Column(db.String(100))
    stockx_slug = db.Column(db.String(255))
    goat_id = db.Column(db.String(100))
    goat_slug = db.Column(db.String(255))
    current_lowest_ask_stockx = db.Column(db.Numeric(10, 2), nullable=True)
    current_lowest_ask_goat = db.Column(db.Numeric(10, 2), nullable=True)
    primary_material = db.Column(db.String(100), nullable=True)
    materials_json = db.Column(db.Text, nullable=True)
    materials_source = db.Column(db.String(50), nullable=True)
    materials_confidence = db.Column(db.Float, nullable=True)
    materials_updated_at = db.Column(db.DateTime, nullable=True)
    description_last_seen = db.Column(db.DateTime, nullable=True)
    source_updated_at = db.Column(db.DateTime, nullable=True)
    last_synced_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow, onupdate=datetime.utcnow)
    image_url = db.Column(db.String(1024))

    def __repr__(self):
        return f'<SneakerDB {self.name}>'
