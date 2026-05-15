import os
import sys

# Ensure backend directory is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from db.database import engine
from sqlalchemy import text

def check_data():
    print("Checking Database Data...")
    tables = ["osm_transit_stops", "osm_pois", "gtfs_stops", "areas"]
    
    with engine.connect() as conn:
        for table in tables:
            try:
                result = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                print(f"Table {table}: {result} rows")
            except Exception as e:
                # Handle case where table might not exist
                print(f"Table {table}: Error or does not exist ({str(e).splitlines()[0]})")
                
        # Let's also check a sample of data to see if it covers the areas we need
        try:
            result = conn.execute(text("SELECT DISTINCT stop_type FROM osm_transit_stops")).fetchall()
            print(f"\nOSM Transit Stop Types: {[r[0] for r in result]}")
        except Exception as e:
            print(f"Could not fetch stop types: {str(e).splitlines()[0]}")

if __name__ == "__main__":
    check_data()
