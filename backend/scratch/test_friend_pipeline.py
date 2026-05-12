import sys
import os
import json

# Add the project root and backend folder to the python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import your friend's code from the new location
from backend.evidence_pack_builder import fetchers
from backend.evidence_pack_builder.evidence_pack_builder import EvidencePackAssembler

def main():
    print("--- Testing Combined Step 3 & 4 (Elite Version) ---")
    
    city_name = "Sungai Besi"
    
    # 1. MONKEY PATCHING: We replace the DB fetchers with fake data so it doesn't crash!
    print("[Mock] Bypassing database calls...")
    fetchers.get_area_profile = lambda area_id: {"area_id": area_id, "area_name": city_name, "region_id": "kuala_lumpur"}
    fetchers.get_route_frequency_summary = lambda area_id: [{"route_id": "LRT_1", "trips_per_day": 120}]
    fetchers.get_transit_coverage_summary = lambda area_id: {"coverage": "Good"}
    fetchers.get_demand_proxy_summary = lambda area_id: {"malls": 2}
    fetchers.get_candidate_problem_directions = lambda area_id: [{"challenge_type": "bus_frequency_gap"}]
    
    try:
        # 2. Run the actual assembler!
        print(f"\n[Step 4] Running Elite Assembler for: {city_name}...")
        assembler = EvidencePackAssembler(city_name)
        pack = assembler.assemble()
        
        # 3. Convert result to dictionary to print
        pack_dict = pack.to_dict()
        
        print("\n" + "="*60)
        print("[SUCCESS] Evidence Pack Generated successfully!")
        print("="*60)
        
        # Print the RAG part to prove it worked!
        print(f"\nRAG Support Status: {pack_dict['rag_support']['message']}")
        if pack_dict['rag_support']['rag_by_challenge']:
            print(f"Found {len(pack_dict['rag_support']['rag_by_challenge'][0]['chunks'])} real chunks for the city!")
            
        # Save it to a file
        output_path = "scratch/elite_packs.json"
        with open(output_path, "w") as f:
            json.dump(pack_dict, f, indent=2)
            
        print(f"\nFull pack saved to {output_path} for you to inspect!")
        
    except Exception as e:
        print(f"\n[ERROR] Failed to run assembler: {e}")

if __name__ == "__main__":
    main()
