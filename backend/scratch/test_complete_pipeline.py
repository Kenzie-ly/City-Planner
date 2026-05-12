import sys
import os
import json

# Add the project root and backend folder to the python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.area_resolver import resolve_area
from backend.indicator_engine import run_indicator_engine
from backend.evidence_pack_builder.evidence_pack_builder import EvidencePackAssembler

def main():
    print("--- Running Complete Pipeline (Steps 1 to 4) ---")
    
    print("Available cities in regions.json include: Johor Bahru, George Town, Shah Alam, Sungai Besi, etc.")
    city_name = input("Enter the city name you want to test: ").strip()
    
    if not city_name:
        print("No city entered. Defaulting to Sungai Besi.")
        city_name = "Sungai Besi"
    
    try:
        # Step 1: Resolve Area
        print(f"\n[Step 1] Resolving Area for: {city_name}...")
        area_info = resolve_area(city_name)
        print(f"-> Resolved Area ID: {area_info['area_id']}")
        
        area_id = area_info['area_id']
        
        # Step 2: Indicator Engine
        print(f"\n[Step 2] Running Indicator Engine for: {area_id}...")
        indicator_res = run_indicator_engine(area_id)
        print(f"-> Status: {indicator_res['status']}")
        
        # Step 3 & 4: Evidence Pack Assembly
        print(f"\n[Step 3 & 4] Assembling Elite Evidence Pack for: {area_id}...")
        assembler = EvidencePackAssembler(area_id)
        pack = assembler.assemble()
        
        pack_dict = pack.to_dict()
        
        print("\n" + "="*60)
        print("[SUCCESS] Complete Pipeline executed successfully!")
        print("============================================================")
        
        # --- PRINT SUMMARY ---
        print("\n📊 --- EVIDENCE PACK SUMMARY ---")
        
        # 1. Transit Coverage
        coverage = pack_dict.get('indicators', {}).get('transit_coverage', {}).get('summary', {}).get('coverage_score', 'N/A')
        print(f"📍 Transit Coverage Score: {coverage}")
        
        # 2. Bus Frequency
        routes = pack_dict.get('indicators', {}).get('route_frequency', {}).get('summary', [])
        if routes:
            top_trips = routes[0].get('trips_per_day', 0)
            print(f"🚌 Bus Frequency: Top route has {top_trips} trips/day")
        else:
            print("🚌 Bus Frequency: No route data found.")
            
        # 3. Activity Score
        demand = pack_dict.get('indicators', {}).get('demand_proxy', {}).get('summary', {})
        activity_score = demand.get('estimated_activity_score', 'N/A')
        print(f"🏢 Activity Score: {activity_score}")
        
        # 4. Identified Challenges
        challenges = pack_dict.get('candidate_problem_directions', {}).get('directions', [])
        challenge_names = [c.get('challenge_type') for c in challenges if c.get('challenge_type')]
        print(f"⚠️ Identified Challenges: {', '.join(challenge_names) if challenge_names else 'None'}")
        
        print("="*60 + "\n")
        
        # Save it to a file (Append instead of overwrite)
        output_path = "scratch/complete_pipeline_pack.json"
        
        packs = []
        if os.path.exists(output_path):
            try:
                with open(output_path, "r") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        packs = data
                    else:
                        # If the file had a single object, convert it to a list
                        packs = [data]
            except Exception as e:
                print(f"[Warning] Could not read existing file, starting a new list: {e}")
                packs = []
                
        packs.append(pack_dict)
        
        with open(output_path, "w") as f:
            json.dump(packs, f, indent=2)
            
        print(f"\nFull pack added to {output_path}! Total packs in file: {len(packs)}")
        
    except Exception as e:
        print("\n" + "="*60)
        print(f"[ERROR] Pipeline failed: {e}")
        print("If it is a timeout, your IP is likely still blocked by Google Cloud.")
        print("============================================================")

if __name__ == "__main__":
    main()
