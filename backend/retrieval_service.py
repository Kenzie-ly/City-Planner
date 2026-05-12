import os
from rag_service import RagService

# Initialize the local RAG service
kb_dir = os.path.join(os.path.dirname(__file__), "knowledge_base")
rag = RagService(kb_dir)
rag.ingest_directory()

def search_rag_chunks_by_area_and_challenge(
    area_id: str,
    challenge_type: str | None = None,
    limit: int = 5
) -> list[dict]:
    """
    Retrieve RAG document chunks related to an area using the LOCAL file system.
    This mimics the database structure so you can build your pipeline!
    """
    # Query the local RAG
    # Convert underscore to space for proper matching in text (e.g., sungai_besi -> Sungai Besi)
    clean_city = area_id.replace("_", " ").title()
    
    # We use clean_city as the location filter to trigger our "Strict Shield"
    # Handle common spelling variants (like George Town vs Georgetown)
    locations = [clean_city]
    if clean_city == "George Town":
        locations = ["George Town", "Georgetown", "Penang"]
    elif clean_city == "Johor Bahru":
        locations = ["Johor Bahru", "JB", "Johor"]
        
    results = []
    for loc in locations:
        # We increase top_k to 50 so that the math phase pulls enough files for the Location Shield to find a match!
        results.extend(rag.query(f"transport issues in {clean_city}", top_k=50, location_filter=loc))
        
    # Deduplicate results by text
    seen_texts = set()
    unique_results = []
    for res in results:
        if res["text"] not in seen_texts:
            seen_texts.add(res["text"])
            unique_results.append(res)
            
    results = unique_results
    
    # Map local results to the "Database" structure you designed
    mapped_rows = []
    for res in results:
        mapped_rows.append({
            "chunk_id": "local_chunk",
            "doc_id": "local_doc",
            "title": res.get("metadata", {}).get("title", "Unknown Report"),
            "source_type": res.get("type", "report"),
            "source_url": res.get("metadata", {}).get("url", ""),
            "publisher": "Local Knowledge Base",
            "published_date": res.get("metadata", {}).get("published_at", "2024-01-01"),
            "chunk_text": res["text"],
            "area_tags": [area_id],
            "challenge_type_tags": [challenge_type] if challenge_type else []
        })
        
    return mapped_rows[:limit]
