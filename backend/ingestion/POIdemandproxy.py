import os
import osmnx as ox
import pandas as pd
import json
from sqlalchemy import create_engine, Column, Integer, String, Float, JSON
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

load_dotenv()

# Database Setup
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL not found in .env")

engine = create_engine(DATABASE_URL)
Base = declarative_base()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class POIDemandProxy(Base):
    __tablename__ = "poi_demand_proxy"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    category = Column(String)
    region = Column(String) # New field
    latitude = Column(Float)
    longitude = Column(Float)
    demand_score = Column(Float) # Calculated based on type
    details = Column(JSON)

def get_demand_score(category):
    """Assign a relative demand proxy score to different POI types."""
    scores = {
        'mall': 10.0,
        'university': 9.0,
        'school': 7.0,
        'hospital': 8.0,
        'office': 8.5,
        'station': 10.0,
        'bus_stop': 5.0,
        'industrial': 7.5,
        'apartments': 6.0,
        'supermarket': 8.0,
        'commercial': 7.0
    }
    return scores.get(category, 1.0)

import requests

def download_malaysia_poi_data():
    # Load regions from regions.json
    try:
        regions_path = os.path.join(os.path.dirname(__file__), "..", "regions.json")
        with open(regions_path, "r") as f:
            regions_data = json.load(f)
    except Exception as e:
        print(f"Error loading regions.json: {e}")
        return []

    print(f"Starting POI download for {len(regions_data)} regions via Overpass API...")
    
    all_pois = []
    overpass_url = "https://overpass-api.de/api/interpreter"

    for region_name, details in regions_data.items():
        print(f"Region: {region_name.upper()}")
        cities = details.get("cities", [])
        
        for city_name in cities:
            try:
                print(f" -> Fetching data for {city_name}...")
                
                # Overpass QL Query: Target specific amenities and buildings
                query = f"""
                [out:json][timeout:180];
                area["name"="{city_name}"]["admin_level"~"4|5|6|7|8"]->.searchArea;
                (
                  node["amenity"~"university|school|hospital|bus_station|ferry_terminal"](area.searchArea);
                  way["amenity"~"university|school|hospital|bus_station|ferry_terminal"](area.searchArea);
                  node["building"~"apartments|office|commercial|industrial|retail"](area.searchArea);
                  way["building"~"apartments|office|commercial|industrial|retail"](area.searchArea);
                  node["shop"~"mall|supermarket"](area.searchArea);
                  way["shop"~"mall|supermarket"](area.searchArea);
                  node["public_transport"~"station|stop_position"](area.searchArea);
                  way["public_transport"~"station|stop_position"](area.searchArea);
                );
                out center;
                """
                
                headers = {
                    'User-Agent': 'CityPlannerTransportBot/1.0 (https://github.com/Kenzie-ly/City-Planner)',
                    'Accept': 'application/json'
                }
                
                response = requests.post(overpass_url, data={'data': query}, headers=headers)
                response.raise_for_status()
                data = response.json()
                
                elements = data.get('elements', [])
                if not elements:
                    print(f"    No data found for {city_name}, skipping.")
                    continue

                # Process raw JSON data
                city_poi_count = 0
                for el in elements:
                    tags = el.get('tags', {})
                    
                    # Get coordinates
                    if el['type'] == 'node':
                        lat, lon = el['lat'], el['lon']
                    else:
                        lat, lon = el['center']['lat'], el['center']['lon']
                    
                    # Determine category
                    category = 'other'
                    for key in ['amenity', 'building', 'shop', 'public_transport']:
                        if key in tags:
                            category = tags[key]
                            break
                    
                    all_pois.append({
                        'name': tags.get('name', 'Unnamed POI'),
                        'category': category,
                        'region': region_name,
                        'latitude': lat,
                        'longitude': lon,
                        'demand_score': get_demand_score(category),
                        'details': {k: str(v) for k, v in tags.items()}
                    })
                    city_poi_count += 1
                
                print(f"    Successfully processed {city_poi_count} POIs for {city_name}.")
                
            except Exception as e:
                print(f"    Error for {city_name}: {e}")
                continue
            
    return all_pois

def save_to_db(pois):
    print(f"Saving {len(pois)} POIs to database...")
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # Clear existing data if needed
        # db.query(POIDemandProxy).delete() 
        
        # Save in batches to prevent timeouts/memory issues
        batch_size = 1000
        for i in range(0, len(pois), batch_size):
            batch = pois[i:i + batch_size]
            db_pois = [POIDemandProxy(**p) for p in batch]
            db.bulk_save_objects(db_pois)
            db.commit()
            print(f"    Progress: Saved {min(i + batch_size, len(pois))} / {len(pois)} POIs...")
            
        print("Success! All data stored in cloud database.")
    except Exception as e:
        print(f"Error saving to database: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    poi_data = download_malaysia_poi_data()
    if poi_data:
        save_to_db(poi_data)
    else:
        print("No data found or download failed.")
