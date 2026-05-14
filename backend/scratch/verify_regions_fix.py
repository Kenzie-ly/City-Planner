import sys
import os
import json

# Add backend to path
sys.path.append(os.getcwd())

from backend.FindRoads import find_region_for_city, run_city_road_connection_analysis
from backend.area_resolver import detect_region, resolve_area

REGIONS_PATH = "backend/regions.json"

def test_find_roads():
    print("--- Testing FindRoads ---")
    try:
        # Test finding region
        region = find_region_for_city("Johor Bahru", regions_path=REGIONS_PATH)
        print(f"Region for Johor Bahru: {region['region_name']}")
        
        # Test analysis with drive mode (should use graphml_drive)
        print("Testing drive mode path selection...")
        try:
            run_city_road_connection_analysis(
                user_city="Johor Bahru",
                road_a_queries=["Jalan Wong Ah Fook"],
                road_b_queries=["Jalan Ibrahim"],
                regions_path=REGIONS_PATH,
                routing_mode="drive"
            )
        except Exception as e:
            print(f"Drive mode analysis error (expected if graph file missing): {e}")

        print("Testing walk mode path selection...")
        try:
            run_city_road_connection_analysis(
                user_city="Johor Bahru",
                road_a_queries=["Jalan Wong Ah Fook"],
                road_b_queries=["Jalan Ibrahim"],
                regions_path=REGIONS_PATH,
                routing_mode="walk"
            )
        except Exception as e:
            print(f"Walk mode analysis error (expected if graph file missing): {e}")

    except Exception as e:
        print(f"FindRoads test failed: {e}")

def test_area_resolver():
    print("\n--- Testing Area Resolver ---")
    try:
        region = detect_region("Shah Alam")
        print(f"Detected region for Shah Alam: {region['region_id']}")
        print(f"GraphML Drive: {region.get('graphml_drive')}")
        print(f"GraphML Walk: {region.get('graphml_walk')}")
        
        if not region.get('graphml'):
            print("ERROR: 'graphml' key missing in returned dict")
        else:
            print(f"Base GraphML (fallback): {region['graphml']}")

    except Exception as e:
        print(f"Area Resolver test failed: {e}")

if __name__ == "__main__":
    test_find_roads()
    test_area_resolver()
