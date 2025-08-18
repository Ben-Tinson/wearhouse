# sneaker_db_updater.py
import os
import requests
from datetime import datetime
from decimal import Decimal
from app import create_app, db
from models import SneakerDB
from dotenv import load_dotenv

load_dotenv()

API_URL = "https://the-sneaker-database.p.rapidapi.com/sneakers"
API_KEY = os.environ.get("RAPIDAPI_KEY")
API_HOST = "the-sneaker-database.p.rapidapi.com"

def populate_sneaker_db():
    if not API_KEY:
        print("ERROR: RAPIDAPI_KEY not found.")
        return

    print("Fetching master sneaker list from API...")
    headers = {"X-RapidAPI-Key": API_KEY, "X-RapidAPI-Host": API_HOST}
    params = {"limit": "100"}
    
    try:
        response = requests.get(API_URL, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        
        sneakers = data.get('results', [])
        print(f"API returned {len(sneakers)} sneakers.")
        
        app = create_app()
        with app.app_context():
            new_sneaker_count = 0
            for sneaker_data in sneakers:
                if sneaker_data.get('sku'):
                    existing = SneakerDB.query.filter_by(sku=sneaker_data['sku']).first()
                    if not existing:
                        # --- THIS IS THE FIX ---
                        # Split the date string and take only the first part (the date)
                        release_date_str = sneaker_data.get('releaseDate')
                        release_date_obj = None
                        if release_date_str:
                            release_date_obj = datetime.strptime(release_date_str.split(' ')[0], '%Y-%m-%d').date()
                        
                        new_sneaker = SneakerDB(
                            name=sneaker_data.get('name'),
                            brand=sneaker_data.get('brand'),
                            colorway=sneaker_data.get('colorway'),
                            gender=sneaker_data.get('gender'),
                            release_date=release_date_obj,
                            retail_price=Decimal(sneaker_data.get('retailPrice', 0)) if sneaker_data.get('retailPrice') else None,
                            sku=sneaker_data.get('sku'),
                            image_url=sneaker_data.get('image', {}).get('original')
                        )
                        db.session.add(new_sneaker)
                        new_sneaker_count += 1
            
            if new_sneaker_count > 0:
                db.session.commit()
                print(f"SUCCESS: Added {new_sneaker_count} new sneakers to the master database.")
            else:
                print("Master database is already up-to-date with this batch.")

    except requests.exceptions.HTTPError as e:
        print(f"Error calling API: {e.response.status_code} - {e.response.text}")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    populate_sneaker_db()