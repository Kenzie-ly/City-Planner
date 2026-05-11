import os
import sys

# Add backend to path so we can import rag_service
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from rag_service import RagService

def test_rag():
    print("--- Testing RAG with 5-Year Filter ---")
    rag = RagService(kb_dir="backend/knowledge_base")
    rag.ingest_directory()
    
    # Query for Putrajaya population
    print("\nQuerying: 'Putrajaya population trend'")
    results = rag.query("Putrajaya population trend", top_k=3)
    
    for i, res in enumerate(results, 1):
        print(f"{i}. [{res['year']}] {res['text'][:100]}...")
        
    # Test 5-year filter with a manual old doc
    print("\nAdding an old doc from 2010...")
    rag.add_documents([{
        "text": "In 2010, the population of Putrajaya was very small.",
        "source": "old_news.txt",
        "type": "news",
        "year": 2010
    }])
    
    print("Querying again with 5-year filter (should NOT see 2010 doc):")
    results = rag.query("Putrajaya population", top_k=5)
    for res in results:
        print(f"- {res['year']}: {res['text'][:50]}")

if __name__ == "__main__":
    test_rag()
