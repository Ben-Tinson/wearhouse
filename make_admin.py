# make_admin.py
import sys
from app import create_app, db
from models import User

def set_admin_status(user_id):
    """Finds a user by ID and sets their is_admin flag to True."""
    app = create_app()
    with app.app_context():
        user = db.session.get(User, user_id)

        if not user:
            print(f"--- ERROR: Could not find user with ID: {user_id} ---")
            return

        try:
            user.is_admin = True
            db.session.commit()
            print(f"--- SUCCESS: User '{user.username}' (ID: {user.id}) has been granted admin rights. ---")
        except Exception as e:
            db.session.rollback()
            print(f"--- FAILED: An error occurred while updating user. ---")
            print(e)

if __name__ == '__main__':
    if len(sys.argv) < 2 or not sys.argv[1].isdigit():
        print("Usage: python3 make_admin.py <user_id>")
    else:
        user_id_to_promote = int(sys.argv[1])
        set_admin_status(user_id_to_promote)