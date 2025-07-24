# import_data.py
import csv
import sys
from datetime import datetime
from decimal import Decimal
from app import create_app, db
from models import Sneaker, User

def run_import():
    if len(sys.argv) < 2 or not sys.argv[1].isdigit():
        print("ERROR: Please provide the user's ID as an argument. Example: python3 import_data.py 1")
        return

    target_user_id = int(sys.argv[1])
    print(f"Attempting to import sneakers for user with ID: {target_user_id}")
    
    app = create_app()
    with app.app_context():
        # Find the specified user by their primary key (ID)
        main_user = db.session.get(User, target_user_id)
        
        if not main_user:
            print(f"FATAL ERROR: User with ID '{target_user_id}' not found in the live database.")
            return

        print(f"Found user: {main_user.username}. Starting import...")
        imported_count = 0
        try:
            with open('sneakers.csv', 'r', newline='', encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    # --- NEW LOGIC: Create and commit one by one ---
                    try:
                        purchase_price = Decimal(row['purchase_price']) if row['purchase_price'] else None
                        purchase_date = datetime.strptime(row['purchase_date'], '%Y-%m-%d').date() if row['purchase_date'] else None
                        last_worn_date = datetime.strptime(row['last_worn_date'], '%Y-%m-%d').date() if row['last_worn_date'] else None
                        in_rotation = row['in_rotation'].lower() in ['true', '1', 't']

                        sneaker = Sneaker(
                            owner=main_user,
                            brand=row['brand'],
                            model=row['model'],
                            colorway=row['colorway'] or None,
                            purchase_price=purchase_price,
                            purchase_currency=row['purchase_currency'] or None,
                            size=row['size'] or None,
                            size_type=row['size_type'] or None,
                            condition=row['condition'] or None,
                            purchase_date=purchase_date,
                            last_worn_date=last_worn_date,
                            in_rotation=in_rotation,
                            image_url=row['image_url'] or None
                        )
                        db.session.add(sneaker)
                        db.session.commit() # Commit each sneaker individually
                        imported_count += 1
                        print(f"  - Imported: {sneaker.brand} {sneaker.model}")
                    except Exception as inner_e:
                        print(f"  - FAILED to import row: {row}. Error: {inner_e}")
                        db.session.rollback()
                        continue # Skip to the next row
            
            print(f"\nSUCCESS: Finished import. Added {imported_count} new sneakers for user '{main_user.username}'.")

        except Exception as e:
            print(f"A critical error occurred: {e}")
            db.session.rollback()

if __name__ == '__main__':
    run_import()