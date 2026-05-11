import requests
import json
import firebase_admin
from firebase_admin import credentials, firestore
import os
from typing import List, Dict

# Initialize Firebase/Firestore
# Note: In a local environment, you may need to set GOOGLE_APPLICATION_CREDENTIALS environment variable
# to the path of your service account key file.
try:
    if not firebase_admin._apps:
        firebase_admin.initialize_app()
except Exception as e:
    print(f"Firebase initialization info: {e}")

db = firestore.client()

REGIONS = ["Johor", "Selangor", "Kuala Lumpur", "Putrajaya"]

def fetch_areas_from_osm(region_name: str) -> List[Dict]:
    """
    Fetches area/city/town data from OpenStreetMap using the Overpass API.
    """
    print(f"--- Fetching data for {region_name} from OSM ---")
    overpass_url = "https://overpass-api.de/api/interpreter"
    
    # Overpass query:
    # - Searches for areas named region_name
    # - Within those areas, find nodes/ways/relations with place tags
    # - We include city, town, suburb, and neighbourhood for "nama-nama daerah"
    query = f"""
    [out:json][timeout:60];
    area["name"="{region_name}"]["admin_level"~"2|4|8"]->.searchArea;
    (
      node["place"~"city|town|suburb|neighbourhood"](area.searchArea);
      way["place"~"city|town|suburb|neighbourhood"](area.searchArea);
      rel["place"~"city|town|suburb|neighbourhood"](area.searchArea);
    );
    out center;
    """
    
    try:
        response = requests.post(overpass_url, data={'data': query}, timeout=65)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"Error fetching data from Overpass for {region_name}: {e}")
        return []
    
    elements = data.get('elements', [])
    print(f"Found {len(elements)} raw elements in {region_name}")
    
    areas = []
    for element in elements:
        tags = element.get('tags', {})
        name = tags.get('name')
        # Fallback to name:en or other tags if 'name' is missing
        if not name:
            name = tags.get('name:en') or tags.get('name:ms')
            
        place_type = tags.get('place')
        
        # Coordinates: nodes have 'lat'/'lon', ways/relations have 'center' from 'out center'
        lat = element.get('lat') or element.get('center', {}).get('lat')
        lon = element.get('lon') or element.get('center', {}).get('lon')
        
        if name and lat and lon:
            areas.append({
                'name': name,
                'type': place_type,
                'lat': lat,
                'lon': lon,
                'osm_id': element.get('id'),
                'region': region_name
            })
            
    # Remove duplicates by name
    unique_areas = {}
    for a in areas:
        unique_areas[a['name']] = a
        
    result = list(unique_areas.values())
    print(f"Extracted {len(result)} unique areas for {region_name}")
    return result

def save_to_google_cloud_database(region_name: str, areas: List[Dict]):
    """
    Saves the fetched area data to Firestore.
    """
    if not areas:
        print(f"No data to save for {region_name}")
        return

    print(f"Saving data to Google Cloud Firestore (Collection: cities)...")
    
    # Use batching for efficiency (Firestore batch limit is 500)
    batch = db.batch()
    collection_ref = db.collection("cities")
    
    count = 0
    for area in areas:
        # Create a document ID that is somewhat readable and unique
        # e.g., selangor_shah_alam
        clean_region = region_name.lower().replace(" ", "_")
        clean_name = area['name'].lower().replace(" ", "_").replace("/", "_")
        doc_id = f"{clean_region}_{clean_name}"
        
        doc_ref = collection_ref.document(doc_id)
        
        data_to_save = {
            **area,
            "updated_at": firestore.SERVER_TIMESTAMP
        }
        
        batch.set(doc_ref, data_to_save)
        count += 1
        
        if count >= 499: # Firestore batch limit
            batch.commit()
            batch = db.batch()
            count = 0
            
    batch.commit()
    print(f"Successfully saved {len(areas)} areas for {region_name}")

from fastapi import APIRouter, HTTPException
from typing import List, Dict

router = APIRouter(prefix="/ingestion", tags=["ingestion"])

@router.post("/run-osm")
async def trigger_osm_ingestion():
    """
    API Endpoint to trigger the OSM ingestion process.
    """
    try:
        run_ingestion()
        return {"status": "success", "message": "OSM ingestion completed successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")

def run_ingestion():
    """
    Main execution function to loop through regions and perform ingestion.
    """
    results = {}
    for region in REGIONS:
        try:
            areas = fetch_areas_from_osm(region)
            save_to_google_cloud_database(region, areas)
            results[region] = f"Saved {len(areas)} areas"
        except Exception as e:
            print(f"Critical error during ingestion for {region}: {e}")
            results[region] = f"Error: {str(e)}"
    return results

if __name__ == "__main__":
    # If run as a script, execute ingestion directly
    run_ingestion()
