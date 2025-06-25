# app.py
import os
from flask import Flask, current_app
from extensions import db, migrate, login_manager, mail, csrf
from models import User
from config import Config, TestConfig

# Import your Blueprints
from routes.auth_routes import auth_bp
from routes.main_routes import main_bp
from routes.sneakers_routes import sneakers_bp


# Define App Configuration
UPLOAD_FOLDER = 'uploads'
basedir = os.path.abspath(os.path.dirname(__file__))

def create_app(config_class=Config): # Existing default
    app = Flask(__name__)
    app.config.from_object(config_class)

    # --- ADD THIS DEBUGGING BLOCK ---
    print("\n--- Flask App Configuration Check ---")
    print(f"SECRET_KEY loaded: {'Yes' if app.config.get('SECRET_KEY') else 'No'}")
    print(f"DATABASE_URI loaded: {app.config.get('SQLALCHEMY_DATABASE_URI')}")
    print(f"RAPIDAPI_KEY loaded: {app.config.get('RAPIDAPI_KEY')}")
    print(f"RAPIDAPI_HOST loaded: {app.config.get('RAPIDAPI_HOST')}")
    print("---------------------------------\n")
    # --- END DEBUGGING BLOCK ---

    # Initialize extensions with the app
    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message_category = 'info'
    mail.init_app(app)
    csrf.init_app(app)

    # User loader for Flask-Login
    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))
    
    with app.app_context():
        # Register Blueprints
        app.register_blueprint(auth_bp)
        app.register_blueprint(main_bp)
        app.register_blueprint(sneakers_bp)

    return app

# Main entry point for running the app
if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)