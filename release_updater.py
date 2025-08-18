# release_updater.py
import os
import requests
from datetime import datetime, date
from decimal import Decimal
from app import create_app, db
from models import Release
from dotenv import load_dotenv

# Explicitly load the .flaskenv file
project_folder = os.path.dirname(os.path.abspath(__file__))
dotenv_path = os.path.join(project_folder, '.flaskenv')
load_dotenv(dotenv_path)

# --- API Configuration ---
API_URL = "https://the-sneaker-database.p.rapidapi.com/sneakers"
API_KEY = os.environ.get("RAPIDAPI_KEY")
API_HOST = "the-sneaker-database.p.rapidapi.com"

def update_releases_from_api():
    if not API_KEY:
        print("ERROR: RAPIDAPI_KEY not found.")
        return

    print("Fetching releases from The Sneaker Database API...")
    headers = {"X-RapidAPI-Key": API_KEY, "X-RapidAPI-Host": API_HOST}
    
    today_str = date.today().isoformat()
    params = {"limit": "100", "releaseDate": f"gte:{today_str}"}
    
    try:
        response = requests.get(API_URL, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        
        releases = data.get('results', [])
        print(f"API returned {len(releases)} upcoming releases.")
        
        app = create_app()
        with app.app_context():
            new_releases_count = 0
            for release_data in releases:
                if release_data.get('releaseDate') and release_data.get('name'):
                    existing = Release.query.filter_by(name=release_data['name']).first()
                    if not existing:
                        # --- THIS IS THE FIX ---
                        # Split the date string and take only the first part (the date)
                        date_string = release_data['releaseDate'].split(' ')[0]
                        
                        new_release = Release(
                            name=release_data['name'],
                            brand=release_data.get('brand'),
                            release_date=datetime.strptime(date_string, '%Y-%m-%d').date(),
                            retail_price=Decimal(release_data.get('retailPrice', 0)) if release_data.get('retailPrice') else None,
                            image_url=release_data.get('image', {}).get('original'),
                            retail_currency='USD'
                        )
                        db.session.add(new_release)
                        new_releases_count += 1
            
            if new_releases_count > 0:
                db.session.commit()
                print(f"SUCCESS: Added {new_releases_count} new releases to the database.")
            else:
                print("Database is already up-to-date with the latest releases.")

    except requests.exceptions.HTTPError as e:
        print(f"Error calling API: {e.response.status_code} - {e.response.text}")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    update_releases_from_api()