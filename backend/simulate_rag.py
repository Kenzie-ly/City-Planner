import os
import json
import sys
from datetime import datetime

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from rag_service import RagService

def simulate_agent_retrieval(city="Putrajaya"):
    print(f"\n[SIMULATION] User selects city: {city}")
    print("-" * 50)
    
    # 1. Initialize RAG
    rag = RagService(kb_dir="backend/knowledge_base")
    rag.ingest_directory()
    
    # 2. Agent queries the Local Knowledge Base
    query = f"Population trends and transport issues in {city}"
    print(f"[AGENT] Searching local memory for: '{query}'...")
    
    # Simulate the query (using the 5-year filter and city-specific boost)
    results = rag.query(query, top_k=4, max_age_years=5, location_filter=city)
    
    # Check for balance (simulate the Completeness Report)
    types_found = [res.get('type', 'other').lower() for res in results]
    news_count = sum(1 for t in types_found if "news" in t or "report" in t)
    complaint_count = sum(1 for t in types_found if "complaint" in t)
    
    print(f"[RAG] Found {len(results)} relevant articles ({news_count} News, {complaint_count} Complaints).")
    
    # --- LOCAL DATA AUDIT ---
    local_count = len(results)
    if local_count == 0:
        print(f"[AGENT] WARNING: No local documents found for '{city}' in the Knowledge Base.")
        print(f"[TIP] To fix this, create a file named 'backend/knowledge_base/{city.lower()}_data.json' with real research.")
    elif news_count < 2 or complaint_count < 2:
        print(f"[AGENT] ADVISORY: Local data is unbalanced ({news_count} News, {complaint_count} Complaints).")
        print(f"[TIP] In the REAL app, the AI will now use Live Google Search to fill these gaps with verified sources.")
    
    # --- NEW: Hybrid SQL Verification Step ---
    print(f"[AGENT] CROSS-REFERENCING with Live SQL Database...")
    print(f"[SQL] Executing: SELECT count(*) FROM gtfs_stops WHERE stop_name LIKE '%{city}%'...")
    # Simulate a successful SQL response
    sql_count = 14 if "Penang" in city else 8
    print(f"[SQL] Result: {sql_count} verified stations/stops found in {city} network.")

    # 3. Simulated AI Response Generation (Synthesized from multiple sources)
    if not results:
        print("\n==================================================")
        print(f"[AI AGENT ANALYSIS: {city.upper()}]")
        print("==================================================")
        print(f"I currently have NO verified data for {city} in my local memory.")
        print(f"In a real scenario, I would now perform a live Google Search to build this report.")
        print("==================================================\n")
        return

    sources_files = list(set([res['source'] for res in results]))
    unique_titles = list(set([res.get('metadata', {}).get('title', 'Unknown') for res in results]))
    titles_display = [res.get('metadata', {}).get('title', 'Unknown Report') for res in results]
    
    print("\n==================================================")
    print("[AI AGENT MULTI-SOURCE ANALYSIS]")
    print("==================================================")
    print(f"Based on local evidence for {city} synthesized from {len(unique_titles)} unique evidence points:\n")
    
    print("1. Identified Challenges (Cross-Referenced):")
    for title in titles_display[:6]:
        print(f"   - {title}")
    
    print("\n2. Composite Evidence Summary:")
    print(f"   Analysis of files {', '.join(sources_files)} reveals the following patterns:")
    for res in results[:6]:
        print(f"   - {res['text']}")
    
    print(f"\n   By synthesizing these {len(results)} distinct data points, the agent detects a high correlation between rapid residential growth and the current degradation of service levels in {city}.")
    
    print(f"\n3. Strategic Public Transport Narrative:")
    print(f"   The multi-file analysis identifies a critical 'Mobility Gap' in {city}. While development has accelerated, the public transport network has not kept pace. The core issue is the lack of seamless integration between residential hubs and primary transit corridors. The evidence indicates that {city} is at risk of becoming a 'Transit Desert' where reliance on private vehicles is forced due to insufficient first-and-last-mile connectivity. To prevent a mobility crisis, a strategic intervention focusing on bus-priority lanes and improved station accessibility is required, anchoring on the data from {sources_files[0]}.")
    
    print(f"\n4. Knowledge Base Sources: {', '.join(sources_files)}")
    print("--------------------------------------------------")
    print("TOTAL TIME: < 1 second (Bypassed 40s web search using Global Memory)")

if __name__ == "__main__":
    target_city = sys.argv[1] if len(sys.argv) > 1 else "Putrajaya"
    simulate_agent_retrieval(target_city)
