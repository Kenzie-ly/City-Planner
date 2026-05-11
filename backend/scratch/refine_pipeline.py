import sys
import os
import json

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from retrieval_service import search_rag_chunks_by_area_and_challenge
from evidence_pack_builder import build_evidence_pack

def run_refinement_test(area_id: str, challenge_type: str, mock_indicators: dict):
    print(f"\n" + "="*60)
    print(f"RUNNING SCENARIO: {area_id} | Challenge: {challenge_type}")
    print("="*60)
    
    # 1. Step 3: Retrieval
    print(f"[Step 3] Querying RAG...")
    chunks = search_rag_chunks_by_area_and_challenge(
        area_id=area_id,
        challenge_type=challenge_type,
        limit=5
    )
    print(f"-> Found {len(chunks)} matching chunks.")
    
    # 2. Step 4: Pack Builder
    print(f"[Step 4] Building Evidence Pack...")
    evidence_pack = build_evidence_pack(
        area_id=area_id,
        indicators=mock_indicators,
        rag_chunks=chunks,
        custom_rules=[f"Focus heavily on issues specific to {area_id}."],
        blocked_claims=[f"Do not mention other cities besides {area_id}."]
    )
    
    # 3. Output
    print("\n[RESULT] Evidence Pack ID:", evidence_pack["evidence_pack_id"])
    print("[RESULT] RAG Chunks Included:", len(evidence_pack["rag_support"]))
    
    # Print the first chunk as a sample if it exists
    if chunks:
        print("\nSample RAG Chunk Used:")
        print(f"  - Title: {chunks[0]['title']}")
        print(f"  - Snippet: {chunks[0]['chunk_text']}")
        
    return evidence_pack

def main():
    print("--- Pipeline Refinement Sandbox (Interactive Mode) ---")
    
    # Ask the user for input
    user_city = input("Enter City Name (e.g., Sungai Besi, Petaling Jaya): ").strip()
    user_challenge = input("Enter Challenge Type (e.g., road, bus, traffic) [Leave empty for all]: ").strip()
    
    if not user_city:
        print("Error: City Name is required. Exiting.")
        return
        
    # Mock indicators for the interactive test
    mock_indicators = {
        "verified_stops": "Dynamic (Mocked)",
        "active_routes": "Dynamic (Mocked)",
        "congestion_level": "Analyzed on the fly"
    }
    
    # Run the scenario based on user input
    pack = run_refinement_test(user_city, user_challenge if user_challenge else None, mock_indicators)
    
    # Save the pack to a file (Appending mode)
    output_path = "scratch/generated_packs.json"
    
    packs_history = []
    # If the file already exists, read the old tests first
    if os.path.exists(output_path):
        try:
            with open(output_path, "r") as f:
                packs_history = json.load(f)
                if not isinstance(packs_history, list):
                    packs_history = [packs_history]
        except json.JSONDecodeError:
            # If the file was empty or corrupted, start fresh
            packs_history = []
            
    # Add the new pack to the list
    packs_history.append(pack)
    
    # Save the full list back to the file
    with open(output_path, "w") as f:
        json.dump(packs_history, f, indent=2)
        
    print("\n" + "="*60)
    print(f"[SUCCESS] Pack appended to history in {output_path}")
    print("You can now open that file to see all your refined JSON structures!")
    print("="*60)

if __name__ == "__main__":
    main()
