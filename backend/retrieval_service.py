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
    # We use area_id as the location filter to trigger our "Strict Shield"
    results = rag.query(f"transport issues in {area_id}", top_k=limit, location_filter=area_id)
    
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
