import os
import json
import sqlalchemy
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Load environment variables (DB_USER, DB_PASS, DB_NAME, DB_HOST, etc.)
load_dotenv()

def import_from_cloud_sql():
    """
    Template script to import infrastructure data from Google Cloud SQL 
    into the local RAG knowledge base.
    """
    
    # 1. Database Connection Configuration
    # We prioritize DATABASE_URL if available in the .env
    db_url = os.getenv("DATABASE_URL")
    
    if not db_url:
        db_user = os.getenv("DB_USER", "postgres")
        db_pass = os.getenv("DB_PASS", "your-password")
        db_name = os.getenv("DB_NAME", "city_planning")
        db_host = os.getenv("DB_HOST", "127.0.0.1")
        db_url = f"postgresql://{db_user}:{db_pass}@{db_host}/{db_name}"
    
    try:
        engine = create_engine(db_url)
        print(f"[SQL] Connecting to database...")
        
        # 2. Fetch Routes, Stops, and OSM Data
        print("[SQL] Extracting Transit & OSM Network Knowledge...")
        
        # GTFS Queries
        route_query = text("""
            SELECT route_short_name, route_long_name, route_type, route_url 
            FROM gtfs_routes 
            LIMIT 200
        """)
        
        stop_query = text("""
            SELECT stop_name, stop_id, stop_lat, stop_lon 
            FROM gtfs_stops 
            LIMIT 500
        """)

        # OSM Queries (New)
        poi_query = text("""
            SELECT name, category, region, demand_score 
            FROM poi_demand_proxy 
            LIMIT 1000
        """)
        
        area_query = text("""
            SELECT name, type, region 
            FROM city_areas 
            LIMIT 500
        """)
        
        with engine.connect() as conn:
            routes = conn.execute(route_query).fetchall()
            stops = conn.execute(stop_query).fetchall()
            pois = conn.execute(poi_query).fetchall()
            areas = conn.execute(area_query).fetchall()
            
        print(f"[SQL] Fetched {len(routes)} Routes, {len(stops)} Stops, {len(pois)} POIs, and {len(areas)} Areas.")
        
        # 3. Transform into RAG format
        all_entries = []
        
        # Process Routes
        for r in routes:
            mode = "Bus" if r.route_type == 3 else "Rail/LRT"
            all_entries.append({
                "title": f"Official Route: {r.route_long_name} ({r.route_short_name})",
                "snippet": f"This is an official {mode} route in the transport network. Route ID: {r.route_short_name}. Long Name: {r.route_long_name}.",
                "published_at": "2024-01-01",
                "type": "report",
                "url": r.route_url if r.route_url else ""
            })
            
        # Process Stops
        for s in stops:
            all_entries.append({
                "title": f"Official Stop: {s.stop_name}",
                "snippet": f"Official transit stop '{s.stop_name}' (ID: {s.stop_id}) located at coordinates {s.stop_lat}, {s.stop_lon}. This stop serves as a critical point in the local network.",
                "published_at": "2024-01-01",
                "type": "report",
                "url": ""
            })

        # Process POIs (New)
        for p in pois:
            all_entries.append({
                "title": f"POI: {p.name} ({p.category})",
                "snippet": f"High-demand Point of Interest '{p.name}' in {p.region}. Category: {p.category}. Demand Score: {p.demand_score}. This location is a major trip generator and anchor for transport planning.",
                "published_at": "2024-05-11",
                "type": "report",
                "url": ""
            })

        # Process Areas (New)
        for a in areas:
            all_entries.append({
                "title": f"Area: {a.name} ({a.type})",
                "snippet": f"Identified district/neighborhood '{a.name}' in {a.region}. Type: {a.type}. This area is used for geographical boundary validation and localized infrastructure analysis.",
                "published_at": "2024-05-11",
                "type": "report",
                "url": ""
            })
        
        # 4. Save to a single Network file
        city_data = {"Transport_Network": all_entries}
            
        # 5. Write to Knowledge Base
        kb_dir = os.path.join(os.path.dirname(__file__), "knowledge_base")
        os.makedirs(kb_dir, exist_ok=True)
        
        for city, data in city_data.items():
            filename = f"{city.lower().replace(' ', '_')}_cloud_sql.json"
            filepath = os.path.join(kb_dir, filename)
            
            with open(filepath, "w") as f:
                json.dump(data, f, indent=2)
            print(f"[SQL] Saved {len(data)} entries to {filename}")
            
        print("[SQL] Import complete. Please restart the backend to index new data.")
        
    except Exception as e:
        print(f"[SQL ERROR] {e}")

if __name__ == "__main__":
    # You will need to install: pip install sqlalchemy psycopg2-binary
    import_from_cloud_sql()
