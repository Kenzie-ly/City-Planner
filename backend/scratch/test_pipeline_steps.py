import sys
import os
import json

# Add parent directory to path so we can import the new services
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from retrieval_service import search_rag_chunks_by_area_and_challenge
from evidence_pack_builder import build_evidence_pack

def test_pipeline_steps():
    print("--- Testing Pipeline Steps 3 & 4 ---")
    
    # Mock output from Step 1 and 2
    area_id = "Sungai Besi"
    challenge_type = "road" # We know there are roadworks in the Sg Besi data!
    
    mock_indicators = {
        "verified_stops": 8,
        "active_routes": 2,
        "demand_score": 85.0,
        "congestion_index": "High"
    }
    
    # 1. Test Step 3: Retrieval Service
    print(f"\n[Step 3] Querying RAG for Area: {area_id}, Challenge: {challenge_type}...")
    chunks = search_rag_chunks_by_area_and_challenge(
        area_id=area_id,
        challenge_type=challenge_type,
        limit=2
    )
    
    print(f"-> Found {len(chunks)} matching chunks in RAG.")
    for i, chunk in enumerate(chunks, 1):
        print(f"   {i}. Title: {chunk['title']}")
        print(f"      Text: {chunk['chunk_text'][:80]}...")
        
    # 2. Test Step 4: Evidence Pack Builder
    print(f"\n[Step 4] Building Evidence Pack...")
    evidence_pack = build_evidence_pack(
        area_id=area_id,
        indicators=mock_indicators,
        rag_chunks=chunks
    )
    
    # 3. Validate Result
    print("\n=== FINAL EVIDENCE PACK GENERATED ===")
    print(json.dumps(evidence_pack, indent=2))
    print("======================================")
    
    if "ev_pack" in evidence_pack["evidence_pack_id"] and len(chunks) > 0:
        print("\n[SUCCESS] Both the Retrieval Service and Evidence Pack Builder are working perfectly!")
    else:
        print("\n[WARNING] Pack generated, but no RAG chunks were found. (Check if keywords match).")

if __name__ == "__main__":
    test_pipeline_steps()
