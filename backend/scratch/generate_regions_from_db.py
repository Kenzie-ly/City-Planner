import json
import os
import sys
from sqlalchemy import text

# Add the project root to the python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from backend.db.database import engine

def main():
    print("--- Extracting Cities from Database to regions.json ---")
    
    regions_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'regions.json'))
    
    # 1. Load existing regions to preserve coordinates
    if os.path.exists(regions_path):
        with open(regions_path, "r") as f:
            regions = json.load(f)
    else:
        regions = {}
        
    # 2. Fetch cities from DB
    try:
        with engine.connect() as conn:
            # We query the city_areas table with the new columns!
            print("Querying database for city_areas...")
            rows = conn.execute(text("SELECT name, city_name, region FROM city_areas WHERE name IS NOT NULL;")).mappings().all()
            
            print(f"Found {len(rows)} areas. Processing...")
            
            added_count = 0
            for row in rows:
                area_name = row['name']
                city_name = row['city_name'] or "Unknown City"
                region_id = row['region']
                
                # If the region doesn't exist in our file yet, create it
                if region_id not in regions:
                    regions[region_id] = {
                        "center_lat": 0.0,
                        "center_lon": 0.0,
                        "dist_m": 50000,
                        "cities": {}, # Change to dict to classify areas by city!
                        "graphml": f"data/graphs/{region_id}.graphml"
                    }
                
                # If 'cities' is still a list from the old format, convert it to a dict
                if isinstance(regions[region_id]['cities'], list):
                    old_cities = regions[region_id]['cities']
                    regions[region_id]['cities'] = {city: [] for city in old_cities}
                
                # Add the city to the dict if it doesn't exist
                if city_name not in regions[region_id]['cities']:
                    regions[region_id]['cities'][city_name] = []
                    
                # Add the area to the city's list
                if area_name not in regions[region_id]['cities'][city_name]:
                    regions[region_id]['cities'][city_name].append(area_name)
                    added_count += 1
                    
        # 3. Save the updated file
        with open(regions_path, "w") as f:
            json.dump(regions, f, indent=2)
            
        print(f"\n[SUCCESS] Successfully updated regions.json!")
        print(f"Added {added_count} new cities across the regions.")
        
    except Exception as e:
        print(f"\n[ERROR] Failed to extract data: {e}")
        print("This is likely because the database connection timed out. Run this again once your IP is whitelisted!")

if __name__ == "__main__":
    main()
