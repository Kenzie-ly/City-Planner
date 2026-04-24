
import os
import sys
import json
import pandas as pd
import osmnx as ox

# Add backend to path
sys.path.append(os.getcwd())
from FindRoads import run_city_road_connection_analysis

def test_missing_metadata():
    # Use a city and road queries that might have sparse metadata
    # or just run a known case and inspect candidates.
    try:
        result = run_city_road_connection_analysis(
            user_city="kuala lumpur",
            road_a_queries=["Jalan 2/27e"],
            road_b_queries=["Jalan Rampai Niaga 1"],
            regions_path="regions.json",
            city_buffer_m=500,
        )
        
        print(f"Mode: {result['mode']}")
        print(f"Number of candidates: {len(result['candidates'])}")
        
        for c in result['candidates']:
            print(f"\nCandidate ID: {c['candidate_id']}")
            print(f"Via roads: {c['via_roads']}")
            print(f"Dominant class: {c['dominant_class']}")
            print(f"Evidence: {json.dumps(c['evidence'], indent=2)}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_missing_metadata()
