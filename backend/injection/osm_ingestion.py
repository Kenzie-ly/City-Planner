import requests
import json
import os
import datetime
from sqlalchemy import create_engine, Column, String, Float, Integer, BigInteger, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from typing import List, Dict

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ---------------------------------------------------------
# DATABASE CONFIGURATION (CLOUD SQL)
# ---------------------------------------------------------
DB_URL = os.getenv("DATABASE_URL")

if not DB_URL:
    # Fallback jika .env tidak terbaca
    DB_URL = "postgresql+psycopg2://postgres:password@localhost:5432/city_planner_db"

Base = declarative_base()

# Definisi Tabel untuk menyimpan data daerah
class CityArea(Base):
    __tablename__ = 'city_areas'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    osm_id = Column(BigInteger, unique=True)
    name = Column(String(255), nullable=False)
    type = Column(String(50)) # city, town, suburb, neighbourhood
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    region = Column(String(100), nullable=False) # Johor, Selangor, etc.
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

# Inisialisasi Database
engine = create_engine(DB_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    """Membuat tabel jika belum ada."""
    Base.metadata.create_all(bind=engine)

# ---------------------------------------------------------
# OSM DATA FETCHING
# ---------------------------------------------------------
REGIONS = ["Johor", "Selangor", "Kuala Lumpur", "Putrajaya"]

def fetch_areas_from_osm(region_name: str) -> List[Dict]:
    """
    Mengambil data daerah/kota dari OpenStreetMap menggunakan Overpass API.
    """
    print(f"\n--- Fetching data for {region_name} from OSM ---")
    overpass_url = "https://overpass-api.de/api/interpreter"
    
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
    
    headers = {
        "User-Agent": "CityPlannerIngestionScript/1.0 (contact: your-email@example.com)"
    }
    
    try:
        response = requests.post(overpass_url, data={'data': query}, headers=headers, timeout=65)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"Error fetching data for {region_name}: {e}")
        return []
    
    elements = data.get('elements', [])
    areas = []
    for element in elements:
        tags = element.get('tags', {})
        name = tags.get('name') or tags.get('name:en') or tags.get('name:ms')
        place_type = tags.get('place')
        lat = element.get('lat') or element.get('center', {}).get('lat')
        lon = element.get('lon') or element.get('center', {}).get('lon')
        
        if name and lat and lon:
            areas.append({
                'osm_id': element.get('id'),
                'name': name,
                'type': place_type,
                'lat': lat,
                'lon': lon,
                'region': region_name
            })
            
    return areas

# ---------------------------------------------------------
# STORAGE LOGIC (CLOUD SQL)
# ---------------------------------------------------------
def save_to_cloud_sql(areas: List[Dict]):
    """
    Menyimpan list data daerah ke dalam database Cloud SQL.
    """
    if not areas:
        return

    db = SessionLocal()
    try:
        print(f"Saving {len(areas)} areas to Cloud SQL...")
        for area_data in areas:
            # Cari apakah data sudah ada berdasarkan osm_id (agar tidak duplikat)
            existing_area = db.query(CityArea).filter(CityArea.osm_id == area_data['osm_id']).first()
            
            if existing_area:
                # Update jika sudah ada
                existing_area.name = area_data['name']
                existing_area.type = area_data['type']
                existing_area.lat = area_data['lat']
                existing_area.lon = area_data['lon']
                existing_area.region = area_data['region']
                existing_area.updated_at = datetime.datetime.utcnow()
            else:
                # Insert baru jika belum ada
                new_area = CityArea(**area_data)
                db.add(new_area)
        
        db.commit()
        print("Success: Data saved successfully.")
    except Exception as e:
        db.rollback()
        print(f"Error saving to database: {e}")
    finally:
        db.close()

# ---------------------------------------------------------
# MAIN EXECUTION (PROSES BIASA)
# ---------------------------------------------------------
def run_main_ingestion():
    print("Starting OSM Ingestion Process...")
    
    # 1. Pastikan tabel sudah ada
    try:
        # Uncomment baris di bawah jika ingin mereset skema (HATI-HATI: Data akan terhapus)
        # Base.metadata.drop_all(bind=engine) 
        init_db()
    except Exception as e:
        print(f"Gagal inisialisasi database: {e}")
        print("Pastikan HOST, USER, PASS, dan DB_NAME sudah benar.")
        return

    # 2. Proses tiap wilayah
    for region in REGIONS:
        areas = fetch_areas_from_osm(region)
        save_to_cloud_sql(areas)
        
    print("\nAll regions processed!")

if __name__ == "__main__":
    # Menjalankan proses secara langsung (bukan API)
    run_main_ingestion()
