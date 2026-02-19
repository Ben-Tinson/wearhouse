from datetime import datetime
from extensions import db # Import db from our new extensions.py
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous.url_safe import URLSafeTimedSerializer as Serializer
from flask import current_app

wishlist_items = db.Table('wishlist_items',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('release_id', db.Integer, db.ForeignKey('release.id'), primary_key=True)
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


    sneakers = db.relationship('Sneaker', backref='owner', lazy=True)
    wishlist = db.relationship('Release', secondary=wishlist_items, lazy='subquery',
        backref=db.backref('wishlisted_by', lazy=True))

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
    condition = db.Column(db.String(50), nullable=True)          # E.g., "New", "Used - Like New", "Good"
    purchase_date = db.Column(db.Date, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    in_rotation = db.Column(db.Boolean, nullable=False, default=False, index=True)


    def __repr__(self):
        return f'<Sneaker {self.brand} {self.model}>'

class Release(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    brand = db.Column(db.String(100), nullable=True, index=True)
    name = db.Column(db.String(200), nullable=False)    
    release_date = db.Column(db.Date, nullable=False, index=True)
    # --- MODIFIED AND NEW LINES ---
    retail_price = db.Column(db.Numeric(10, 2), nullable=True) # Changed from String
    retail_currency = db.Column(db.String(10), nullable=True)   # New field
    image_url = db.Column(db.String(500), nullable=True)
    # --- END OF CHANGES ---

    def __repr__(self):
        return f'<Release {self.name}>'

class SneakerDB(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    brand = db.Column(db.String(150), index=True)
    name = db.Column(db.String(255), index=True)
    model_name = db.Column(db.String(255), index=True)
    colorway = db.Column(db.String(255))
    gender = db.Column(db.String(20))
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
    last_synced_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow, onupdate=datetime.utcnow)
    image_url = db.Column(db.String(1024))

    def __repr__(self):
        return f'<SneakerDB {self.name}>'
