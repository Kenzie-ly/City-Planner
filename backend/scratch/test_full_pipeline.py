import json
import sys
import os

# Add the project root and backend folder to the python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.area_resolver import resolve_area
from backend.indicator_engine import run_indicator_engine
from backend.retrieval_service import search_rag_chunks_by_area_and_challenge

def main():
    print("--- Testing Pipeline Steps 1, 2, and 3 ---")
    
    # We will use Sungai Besi as the test city
    city_name = "Sungai Besi"
    
    try:
        # Step 1: Resolve Area
        print(f"\n[Step 1] Resolving Area for: {city_name}...")
        area_info = resolve_area(city_name)
        print(f"-> Resolved Area ID: {area_info['area_id']}")
        print(f"-> Source: {area_info['source']}")
        
        area_id = area_info['area_id']
        
        # Step 2: Indicator Engine
        print(f"\n[Step 2] Running Indicator Engine for: {area_id}...")
        indicator_res = run_indicator_engine(area_id)
        print(f"-> Status: {indicator_res['status']}")
        
        # Step 3: RAG Retrieval
        print(f"\n[Step 3] Querying RAG for challenges in {area_id}...")
        # We query for general transit issues in this area
        chunks = search_rag_chunks_by_area_and_challenge(area_id=area_id, limit=5)
        print(f"-> Found {len(chunks)} matching chunks in the knowledge base.")
        
        if chunks:
            print("\nSample RAG Chunk Found:")
            print(f"  - Title: {chunks[0]['title']}")
            print(f"  - Snippet: {chunks[0]['chunk_text'][:150]}...")
            
        print("\n" + "="*60)
        print("[SUCCESS] Steps 1, 2, and 3 are connected and working!")
        print("============================================================")
        
    except Exception as e:
        print("\n" + "="*60)
        print(f"[ERROR] Pipeline failed at some step: {e}")
        print("If it says 'Table missing', you may need to run your DB migrations first!")
        print("============================================================")

if __name__ == "__main__":
    main()
