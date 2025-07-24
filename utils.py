# utils.py
from flask import current_app

def allowed_file(filename):
    """Checks if a filename has an allowed extension."""
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']