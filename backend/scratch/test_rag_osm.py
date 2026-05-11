import os
import json
import sys
# Add parent directory to path so we can import rag_service
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rag_service import RagService
from dotenv import load_dotenv

load_dotenv()

def test_rag_osm_retrieval():
    print("--- Testing RAG Retrieval for new OSM Data ---")
    
    # Initialize RAG
    kb_dir = os.path.join(os.getcwd(), "knowledge_base")
    rag = RagService(kb_dir)
    rag.ingest_directory()
    
    # Test queries based on the data we just imported
    test_queries = [
        "What are some high demand POIs in Kuala Lumpur?",
        "Tell me about neighborhoods like Kiaramas",
        "Which areas have a high demand score?"
    ]
    
    for query in test_queries:
        print(f"\nQuery: {query}")
        results = rag.query(query, top_k=3)
        
        if not results:
            print("  No results found. (Ensure you restarted the backend if using the API)")
            continue
            
        for i, res in enumerate(results, 1):
            text = res.get("text", "No Content")
            source = res.get("source", "Unknown")
            print(f"  {i}. [Source: {source}]")
            print(f"     {text}")

if __name__ == "__main__":
    # Ensure we are in the backend directory
    if not os.path.exists("knowledge_base"):
        print("Error: knowledge_base directory not found. Please run from the backend directory.")
    else:
        test_rag_osm_retrieval()
