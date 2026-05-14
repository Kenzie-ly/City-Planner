import sys
import os
import json
import uuid
import re
from datetime import datetime

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Copying the function directly here to avoid the import conflict 
def build_evidence_pack(
    area_id: str,
    indicators: dict,
    rag_chunks: list[dict],
    custom_rules: list[str] | None = None,
    blocked_claims: list[str] | None = None
) -> dict:
    pack_id = f"ev_pack_{uuid.uuid4().hex[:8]}"
    
    rules = [
        "Structured data (SQL) is the PRIMARY evidence and must be trusted over RAG news.",
        "You must cite at least one specific RAG source by title in your summary.",
        "If RAG evidence contradicts SQL data, prioritize the SQL data.",
    ]
    if custom_rules:
        rules.extend(custom_rules)
        
    blocked = [
        "Do not invent exact percentage growth numbers unless explicitly stated in the evidence.",
        "Do not claim a service is 'excellent' if there are active complaints in the pack.",
    ]
    if blocked_claims:
        blocked.extend(blocked_claims)
        
    evidence_pack = {
        "evidence_pack_id": pack_id,
        "area_id": area_id,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "structured_indicators": indicators,
        "rag_support": rag_chunks,
        "rules": rules,
        "blocked_claims": blocked
    }
    
    return evidence_pack

def diagnostic_agent(evidence_pack):
    print("\n[Diagnostic Agent] Processing Evidence Pack...")
    
    rag_chunks = evidence_pack.get('rag_support', [])
    hotspots = []
    
    # Mapping to use until Step 2 is updated to pull from osm_edges
    road_mapping = {
        "Johor Bahru Sentral": "Jalan Jim Quee",
        "Procleaners Johor Bahru": "Jalan Setia Tropika",
        "Angsana Johor Bahru Mall": "Jalan Tampoi",
        "Johor Bahru City Square": "Jalan Wong Ah Fook"
    }
    
    for chunk in rag_chunks:
        title = chunk.get('title', '')
        text = chunk.get('chunk_text', '') or chunk.get('text', '')
        
        if title.startswith("POI: "):
            place_name = title.replace("POI: ", "").strip()
            
            # Remove parentheses like (stop_position)
            clean_name = re.sub(r'\(.*?\)', '', place_name).strip()
            
            # Find the road name from our mapping
            road_name = "Unknown Road"
            for key, value in road_mapping.items():
                if key in clean_name:
                    road_name = value
                    break
            
            # Extract demand score if available in the text (ignoring trailing dots)
            demand_score = "N/A"
            score_match = re.search(r'Demand Score:\s*([0-9]+(?:\.[0-9]+)?)', text)
            if score_match:
                demand_score = score_match.group(1)

            # Generate a specific issue sentence based on the direction
            selected_direction = "Transit Coverage Gap" # Default fallback
            
            if "Coverage" in selected_direction:
                issue_text = f"The area around {road_name} is identified as a high-activity hub (Demand Score: {demand_score}), but our data shows a gap in transit coverage nearby."
            elif "Frequency" in selected_direction:
                issue_text = f"High demand detected at {road_name} (Demand Score: {demand_score}), but the frequency of nearby bus routes is insufficient."
            else:
                issue_text = f"Database records indicate {road_name} as a major trip generator (Demand Score: {demand_score}). Recommend running detailed transport accessibility analysis."

            hotspots.append({
                "id": f"hotspot_{len(hotspots)+1}",
                "name": f"{place_name} ({road_name})",
                "issue": issue_text,
                "severity": "High" if demand_score != "N/A" and float(demand_score) >= 7.0 else "Medium",
                "recommended_action": "Run detailed transport accessibility analysis for this specific area."
            })
            
        if len(hotspots) >= 3:
            break
            
    # No fallback! If data is missing, return empty so we can troubleshoot it!
    if not hotspots:
        print("[Warning] No specific POI areas found in the evidence pack!")
    
    return hotspots[:3]

def main():
    print("--- Testing Focused Evidence Pack & Diagnostic Agent ---")
    
    # Skip Step 1 and 2: Assume user selected "Transit Coverage Gap" for Johor Bahru
    area_id = "johor_bahru"
    selected_direction = "Transit Coverage Gap"
    print(f"Selected Direction (Skipped Step 1 & 2): {selected_direction}")
    
    # Load Real Evidence Pack from Step 2 output
    pack_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "complete_pipeline_pack.json")
    print(f"Loading real evidence pack from: {pack_path}")
    
    try:
        with open(pack_path, 'r', encoding='utf-8') as f:
            packs = json.load(f)
            
        # Find the pack for Johor Bahru
        real_pack = None
        for p in packs:
            if p.get('area', {}).get('area_id') == area_id:
                real_pack = p
                break
                
        if not real_pack:
            print(f"[Warning] No real pack found for {area_id}. Using the first pack in the file.")
            real_pack = packs[0] if packs else {}
            
    except Exception as e:
        print(f"[Error] Could not load real pack: {e}. Falling back to mock data.")
        real_pack = {}
        
    # Extract real indicators and RAG chunks
    indicators = real_pack.get('indicators', {})
    
    # Recursive search to find all chunks in the file
    def extract_chunks(obj):
        found = []
        if isinstance(obj, dict):
            if 'chunk_id' in obj and ('chunk_text' in obj or 'text' in obj):
                found.append(obj)
            else:
                for v in obj.values():
                    found.extend(extract_chunks(v))
        elif isinstance(obj, list):
            for item in obj:
                found.extend(extract_chunks(item))
        return found
        
    rag_chunks = extract_chunks(real_pack)
    print(f"Extracted {len(rag_chunks)} real RAG/POI chunks from the pack.")
    
    # Step 3: Build Focused Evidence Pack
    print("\n[Step 3] Building Focused Evidence Pack...")
    evidence_pack = build_evidence_pack(
        area_id=area_id,
        indicators=indicators,
        rag_chunks=rag_chunks,
        custom_rules=[f"Focus analysis specifically on the challenge: {selected_direction}"]
    )
    
    print("Pack Generated Successfully!")
    print(f"Pack ID: {evidence_pack['evidence_pack_id']}")
    
    # Show full content of the generated evidence pack
    print("\n" + "="*20 + " FULL FOCUSED EVIDENCE PACK CONTENT " + "="*20)
    print(json.dumps(evidence_pack, indent=2))
    print("="*68)
    
    # Step 4: Run Diagnostic Agent
    selected_hotspots = diagnostic_agent(evidence_pack)
    
    print("\n=== DIAGNOSTIC AGENT OUTPUT: 3 SPECIFIC HOTSPOTS ===")
    print("The agent has filtered the evidence and selected these 3 choices for the user:")
    print(json.dumps(selected_hotspots, indent=2))
    print("=====================================================")
    
    print("\n📍 Quick List of Specific Areas to Process:")
    for i, hotspot in enumerate(selected_hotspots):
        print(f"  {i+1}. {hotspot.get('name')} ({hotspot.get('severity')} Severity)")
    
    print("\n[Next Step in Flow]: User selects one of these 3 hotspots to process.")

if __name__ == "__main__":
    main()
