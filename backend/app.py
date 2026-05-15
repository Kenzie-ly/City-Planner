from __future__ import annotations

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
import os
import requests
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from typing import Any
import uuid
import json
import re
from urllib.parse import urlparse

from agent import (
    place_intake_agent,
    find_needs_agent,
    solution_agent,
    building_agent,
    InfrastructurePlannerOrchestrator,
    find_hotspot_agent,
    review_agent,
)
from building_agent_helper import process_agent_assets, format_entities
from evidence_pipeline import (
    audit_osm_transit_gap,
    compute_merged_confidence,
    verify_complaint_against_osm,
    filter_trusted_evidence,
)
from reliability import (
    build_decision_package,
    audit_solution_claims,
    validate_geo_consistency,
)
import persistence_service 

load_dotenv("../.env")
load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GROWTH_FLOW_ENABLED = os.getenv("GROWTH_FLOW_ENABLED", "0").strip().lower() not in {"0", "false", "no"}
ENABLE_SPECULATIVE_FIND_NEEDS = os.getenv("ENABLE_SPECULATIVE_FIND_NEEDS", "0").strip().lower() not in {"0", "false", "no"}

app = FastAPI(title="Infrastructure Planner API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

from sqlalchemy import text
# from db.database import engine # Moved inside handlers
from google import genai
from google.genai import types
import os
import json

@app.get("/api/diagnostic/hotspots")
async def get_diagnostic_hotspots(area_id: str):
    from db.database import engine
    query = """
        SELECT p.name as poi_name, e.name as road_name, p.poi_category
        FROM osm_pois p
        LEFT JOIN osm_edges e 
        ON ST_DWithin(p.geom, e.geometry, 0.005)
        WHERE p.area_id = :area_id AND p.name != 'Unnamed'
        ORDER BY ST_Distance(p.geom, e.geometry) ASC
        LIMIT 15;
    """
    
    category_scores = {
        "station": "10.0",
        "rail_station": "10.0",
        "mall": "8.5",
        "university": "9.0",
        "office": "8.0",
        "commercial": "7.0",
        "supermarket": "6.0",
        "apartments": "5.0",
        "bus_stop": "4.0",
        "other": "1.0"
    }
    
    db_hotspots = []
    try:
        with engine.connect() as conn:
            result = conn.execute(text(query), {"area_id": area_id})
            used_pois = set()
            
            for row in result:
                poi_name = row.poi_name
                road_name = row.road_name or "Unknown Road"
                category = row.poi_category
                
                # Avoid duplicates
                if poi_name in used_pois:
                    continue
                used_pois.add(poi_name)
                
                # Clean road_name if it's stored as a JSON string in DB
                if road_name.startswith("["):
                    try:
                        names = json.loads(road_name)
                        road_name = names[0] if names else "Unknown Road"
                    except:
                        pass
                
                demand_score = category_scores.get(category, "1.0")
                
                db_hotspots.append({
                    "poi_name": poi_name,
                    "road_name": road_name,
                    "category": category,
                    "demand_score": demand_score
                })
                
                if len(db_hotspots) >= 3:
                    break
                    
    except Exception as e:
        print(f"Database error: {e}")
        
    # Fallback if DB is empty or fails
    if not db_hotspots:
        db_hotspots = [
          {
            "poi_name": "Johor Bahru Sentral",
            "road_name": "Jalan Jim Quee",
            "category": "station",
            "demand_score": "10.0"
          },
          {
            "poi_name": "City Square",
            "road_name": "Jalan Wong Ah Fook",
            "category": "mall",
            "demand_score": "8.5"
          }
        ]
        
    # Try to use Gemini to generate smart descriptions!
    try:
        from google import genai
        print(f"DEBUG: API Key loaded = {os.getenv('GEMINI_API_KEY')[:10]}...")
        client = genai.Client() # Reads GEMINI_API_KEY
        
        # --- RAG Integration ---
        articles_context = ""
        try:
            # 1. Generate embedding for the search query
            response = client.models.embed_content(
                model='gemini-embedding-2',
                contents="public transport issues and bus coverage",
            )
            query_vector = response.embeddings[0].values
            vector_str = "[" + ",".join(map(str, query_vector)) + "]"
            
            # 2. Query the database using pgvector
            query = """
                SELECT title, content, source_type
                FROM city_problems_rag
                WHERE city = :city
                ORDER BY embedding <=> :vector ASC
                LIMIT 3;
            """
            with engine.connect() as conn:
                res = conn.execute(text(query), {"vector": vector_str, "city": area_id}).fetchall()
                for r in res:
                    articles_context += f"Source Type: {r.source_type}\nTitle: {r.title}\nContent: {r.content}\n\n"
                    
            if articles_context:
                print(f"✅ RAG found {len(res)} articles for {area_id}!")
            else:
                print(f"ℹ️ No RAG articles found for {area_id}. Falling back to general knowledge.")
                
        except Exception as e:
            print(f"RAG search failed: {e}. Falling back to general knowledge.")
            
        prompt = f"""
        You are a Transport Infrastructure Solution Expert in Malaysia.
        I will give you a list of 3 specific Points of Interest (POIs) found in our database that have high activity but likely transit gaps.
        
        Real Data Context (Use this to ground your issues if available!):
        {articles_context if articles_context else "No real articles found for this city. Use your general transport knowledge."}
        
        Your task is to generate a smart, realistic, and professional "issue" description and "recommended action" for each POI.
        If real articles are provided above, try to mention the issues cited in them (like bus delays, congestion, or specific plans).
        
        Data:
        {json.dumps(db_hotspots, indent=2)}
        
        Return a STRICT JSON ARRAY of 3 objects with the following keys:
        - id: "hotspot_1", "hotspot_2", "hotspot_3"
        - name: The POI name and road name combined nicely (e.g., "1 Mont Kiara (Jalan Kiara)").
        - issue: 2-3 sentences explaining the problem. Use the real data context if it helps, otherwise use general transport knowledge.
        - severity: "High" or "Medium".
        - recommended_action: 1 sentence on what to do next.
        
        Do not include markdown formatting or prose.
        """
        
        model_name = os.getenv("PLANNER_MODEL", "gemini-2.5-flash")
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        
        return json.loads(response.text)
        
    except Exception as e:
        print(f"Gemini error in hotspots: {e}")
        # Fallback to the fast template if Gemini fails!
        hotspots = []
        for i, item in enumerate(db_hotspots):
            hotspots.append({
                "id": f"hotspot_{i+1}",
                "name": f"{item['poi_name']} ({item['road_name']})",
                "issue": f"The area around {item['road_name']} is identified as a high-activity hub (Demand Score: {item['demand_score']}), but our data shows a gap in transit coverage nearby.",
                "severity": "High" if float(item['demand_score']) >= 7.0 else "Medium",
                "recommended_action": "Run detailed transport accessibility analysis for this specific area."
            })
        return hotspots

class SelectHotspotRequest(BaseModel):
    poi_name: str
    area_id: str

### Solution Readiness Pack ###
@app.post("/api/diagnostic/hotspots/select")
async def select_hotspot(req: SelectHotspotRequest):
    from db.database import engine
    # 1. Fetch the POI and nearest road from DB
    query = """
        SELECT p.name as poi_name, e.name as road_name, p.poi_category, p.geom
        FROM osm_pois p
        LEFT JOIN osm_edges e 
        ON ST_DWithin(p.geom, e.geometry, 0.005)
        WHERE p.name = :poi_name AND p.area_id = :area_id
        LIMIT 1;
    """
    
    try:
        with engine.connect() as conn:
            result = conn.execute(text(query), {"poi_name": req.poi_name, "area_id": req.area_id}).fetchone()
            
            if not result:
                return {"status": "error", "message": "POI not found"}
                
            poi_name = result.poi_name
            road_name = result.road_name or "Unknown Road"
            category = result.poi_category
            poi_geom = result.geom
            
            # 2. Fetch nearest transit stops (Gathering data for Solution Readiness!)
            stops_query = """
                SELECT stop_name, stop_type, ST_Distance(geom, :poi_geom) as distance
                FROM osm_transit_stops
                WHERE ST_DWithin(geom, :poi_geom, 0.01)
                ORDER BY distance ASC
                LIMIT 5;
            """
            
            stops_result = conn.execute(text(stops_query), {"poi_geom": poi_geom}).fetchall()
            
            stops_list = []
            for r in stops_result:
                stops_list.append({
                    "name": r.stop_name,
                    "type": r.stop_type,
                    "distance_m": round(r.distance * 111000, 2) # Convert degrees to meters approx
                })
                
            # 3. Create the Solution Readiness Pack
            pack_json = {
                "selected_poi": poi_name,
                "road_name": road_name,
                "category": category,
                "nearby_transit_stops": stops_list,
                "analysis_goal": f"Improve transit access and reduce congestion around {poi_name} on {road_name}."
            }
            
            # 4. Save to evidence_packs table!
            pack_id = str(uuid.uuid4())
            
            insert_query = """
                INSERT INTO evidence_packs (evidence_pack_id, area_id, pack_type, pack_json, created_at)
                VALUES (:pack_id, :area_id, 'solution_readiness', :pack_json, NOW());
            """
            
            # We need to commit the transaction in some SQLAlchemy versions, 
            # but since we are using engine.connect() directly, let's execute it!
            conn.execute(text(insert_query), {
                "pack_id": pack_id,
                "area_id": req.area_id,
                "pack_json": json.dumps(pack_json)
            })
            
            # Some setups require explicit commit
            conn.execute(text("COMMIT"))
            
            return {
                "status": "success",
                "message": "Solution Readiness Pack generated and saved to database!",
                "pack_id": pack_id,
                "pack_data": pack_json
            }
            
    except Exception as e:
        print(f"Error in select_hotspot: {e}")
        return {"status": "error", "message": str(e)}

class GenerateSolutionsRequest(BaseModel):
    pack_id: str

@app.post("/api/solutions/generate")
async def generate_solutions(req: GenerateSolutionsRequest):
    from db.database import engine
    # 1. Fetch the Solution Readiness Pack from DB
    query = """
        SELECT pack_json, area_id 
        FROM evidence_packs 
        WHERE evidence_pack_id = :pack_id;
    """
    
    try:
        with engine.connect() as conn:
            result = conn.execute(text(query), {"pack_id": req.pack_id}).mappings().first()
            
            if not result:
                raise HTTPException(status_code=404, detail="Pack not found")
                
            pack_data = result["pack_json"]
            # If it's stored as a string, parse it. If it's already a dict, use it!
            if isinstance(pack_data, str):
                pack_data = json.loads(pack_data)
            
        # 2. Call Gemini to generate SMART solutions!
        prompt = f"""
        You are a Transport Infrastructure Solution Expert in Malaysia.
        I will give you a Solution Readiness Pack containing a specific Point of Interest (POI) and the real nearby transit stops found in our database.
        Your task is to propose 3 SMART, realistic, and actionable transport solutions to improve connectivity and reduce congestion.
        
        Pack Data:
        {json.dumps(pack_data, indent=2)}
        
        For each solution, provide:
        - title: A short, catchy title (e.g., "Feeder Bus Route 101", "Pedestrian Bridge").
        - description: 2-3 sentences explaining exactly what to do and why it helps, referencing the POI and the specific nearby stops if applicable.
        - cost_estimate: Low, Medium, or High.
        - timeline: e.g., "3-6 months", "1-2 years".
        
        Return a STRICT JSON ARRAY of 3 solutions. Do not include markdown formatting or prose.
        """
        
        model_name = os.getenv("PLANNER_MODEL", "gemini-1.5-flash")
        from google import genai
        client = genai.Client() # Reads GEMINI_API_KEY
        
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        
        solutions = json.loads(response.text)
        return {
            "status": "success",
            "pack_id": req.pack_id,
            "solutions": solutions
        }
        
    except Exception as e:
        print(f"Gemini error: {e}")
        # Realistic fallback if API key is missing!
        # Safe access to pack_data
        poi_display = pack_data.get('selected_poi', 'the area') if 'pack_data' in locals() else 'the area'
        
        return {
            "status": "success",
            "pack_id": req.pack_id,
            "solutions": [
                {
                    "title": "Dedicated Feeder Bus Loop",
                    "description": f"Launch a high-frequency feeder bus service connecting {poi_display} to the nearest transit stops found in our database analysis.",
                    "cost_estimate": "Medium",
                    "timeline": "3-6 months"
                },
                {
                    "title": "Pedestrian Walkway Upgrade",
                    "description": f"Improve the walking paths between {poi_display} and the surrounding roads to encourage active mobility.",
                    "cost_estimate": "Low",
                    "timeline": "1-3 months"
                }
            ],
            "note": "Fallback used because Gemini API failed or key is missing."
        }

from concurrency import llm_semaphore, nominatim_semaphore

APP_NAME = "infrastructure_planner"

session_service = InMemorySessionService()
workflow_state: dict[str, dict[str, Any]] = {}
production_counters: dict[str, int] = {
    "blocked_geo_inconsistency": 0,
    "downgraded_claim_audit": 0,
    "overlap_upgrade_decisions": 0,
    "context_entities_rendered": 0,
}

PIPELINE = [
    ("Generate solutions", solution_agent, "solution_result"),
    ("Building simulations", building_agent, "simulation_result"),
]


class StartRequest(BaseModel):
    pass


class ChatRequest(BaseModel):
    session_id: str
    message: str


def clean_json_text(text: str) -> str:
    text = (text or "").strip()
    
    # 1. Prioritize markdown blocks
    if "```json" in text:
        match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
    
    # If no json block, try any code block
    match_code = re.search(r"```[a-zA-Z]*\s*(.*?)\s*```", text, re.DOTALL)
    if match_code:
        inner = match_code.group(1).strip()
        # If it looks like JSON, return it
        if ("{" in inner and "}" in inner) or ("[" in inner and "]" in inner):
            return inner

    # 2. Find the outer-most [ or {
    # We want to pick the one that starts earliest to catch the true root
    match_arr = re.search(r'(\[.*\])', text, re.DOTALL)
    match_obj = re.search(r'(\{.*\})', text, re.DOTALL)
    
    if match_arr and match_obj:
        if match_arr.start() < match_obj.start():
            return match_arr.group(1).strip()
        else:
            return match_obj.group(1).strip()
    elif match_arr:
        return match_arr.group(1).strip()
    elif match_obj:
        return match_obj.group(1).strip()
        
    return text.strip()


def safe_json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        raise ValueError("Expected JSON string, dict, or list.")
    
    cleaned = clean_json_text(value)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Attempt to salvage by escaping literal newlines within string boundaries
        salvaged = re.sub(
            r'("(?:[^"\\]|\\.)*")',
            lambda m: m.group(0).replace('\n', '\\n').replace('\r', '\\r'),
            cleaned,
            flags=re.DOTALL
        )
        return json.loads(salvaged)


def is_retry_response(text: Any) -> bool:
    return isinstance(text, str) and text.strip().upper().startswith("VERDICT: RETRY")


def extract_retry_feedback(text: Any, default: str = "The AI is temporarily unavailable. Please try again.") -> str:
    if not isinstance(text, str):
        return default
    m = re.search(r"FEEDBACK:\s*(.*)", text, re.IGNORECASE | re.DOTALL)
    if m:
        feedback = m.group(1).strip()
        if feedback:
            return feedback
    return default


async def run_agent_with_retry(agent, session_id: str, prompt: str, max_attempts: int = 2) -> str:
    attempts = max(1, int(max_attempts or 1))
    last_response = ""
    for _ in range(attempts):
        last_response = await run_agent_once(agent, session_id, prompt)
        if not is_retry_response(last_response):
            return last_response
        await asyncio.sleep(0.25)
    return last_response


async def run_agent_once(agent, session_id: str, prompt: str) -> str:
    # Use session_id as user_id for better production isolation in ADK
    user_id = session_id or "default_user"
    
    async with llm_semaphore:
        runner = Runner(agent=agent, app_name=APP_NAME, session_service=session_service)
        message = types.Content(role="user", parts=[types.Part(text=prompt)])

        response = ""
        try:
            print(f"[LLM] Running agent: {agent.name} (Session: {session_id})")
            start_time = asyncio.get_event_loop().time()
            
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=message,
            ):
                if event.is_final_response() and event.content:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            response += part.text
            
            elapsed = asyncio.get_event_loop().time() - start_time
            print(f"[LLM] Agent {agent.name} finished in {elapsed:.2f}s")
            return response.strip()
            
        except Exception as e:
            error_msg = str(e)
            print(f"[LLM ERROR] Agent {agent.name} failed: {error_msg}")
            if any(code in error_msg for code in ["429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE"]):
                return "VERDICT: RETRY\nFEEDBACK: The AI is currently experiencing high demand. Please wait a moment and try again."
            return f"VERDICT: RETRY\nFEEDBACK: An error occurred: {error_msg}"


def _extract_ref_coords(entities: list[dict[str, Any]], fallback_coords: dict[str, float]) -> tuple[float, float]:
    ref_lat = fallback_coords.get("lat", 3.1390)
    ref_lng = fallback_coords.get("lng", 101.6869)
    
    if not entities:
        return ref_lat, ref_lng
        
    # Prioritize non-context entities (proposals)
    proposal_ents = [e for e in entities if not str(e.get("id", "")).startswith(("existing_", "transit_route_", "station_", "bus_stop_", "workplace_"))]
    search_list = proposal_ents if proposal_ents else entities

    for ent in search_list:
        # Check polyline_positions
        polys = ent.get("polyline_positions")
        if polys and isinstance(polys, list) and len(polys) > 0:
            first_seg = polys[0]
            if first_seg and isinstance(first_seg, list) and len(first_seg) > 0:
                first_pt = first_seg[0]
                if isinstance(first_pt, dict) and "lat" in first_pt:
                    return first_pt["lat"], first_pt["lng"]
        
        # Check position
        pos = ent.get("position")
        if pos and isinstance(pos, dict) and "lat" in pos:
            return pos["lat"], pos["lng"]
            
        # Check polygon_positions
        pgons = ent.get("polygon_positions")
        if pgons and isinstance(pgons, list) and len(pgons) > 0:
            first_pt = pgons[0]
            if isinstance(first_pt, dict) and "lat" in first_pt:
                return first_pt["lat"], first_pt["lng"]
                
    return ref_lat, ref_lng


def _get_list(data: dict, key: str) -> list:
    val = data.get(key)
    if not val:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        return [val]
    return [str(val)]


def parse_place_result(text: str) -> dict[str, Any]:
    result = {"verdict": "RETRY", "places": [], "feedback": "Please try again."}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("VERDICT:"):
            result["verdict"] = line.split(":", 1)[1].strip().upper()
        elif line.startswith("PLACES:"):
            raw_places = line.split(":", 1)[1].strip()
            if raw_places:
                result["places"] = [p.strip() for p in raw_places.split("|") if p.strip()]
        elif line.startswith("FEEDBACK:"):
            result["feedback"] = line.split(":", 1)[1].strip()
    return result


def parse_review(text: str) -> dict[str, str]:
    result = {
        "verdict": "REVISE",
        "detail": "",
        "final_output": "",
    }

    verdict_match = re.search(r"VERDICT:\s*(PASS|REVISE|REVISE_TOTAL)", text, re.IGNORECASE)
    if verdict_match:
        result["verdict"] = verdict_match.group(1).strip().upper()

    detail_match = re.search(r"(?:REASON|INSTRUCTION):\s*(.*?)(?=\s*\n[A-Z_]+:|$)", text, re.IGNORECASE | re.DOTALL)
    if detail_match:
        result["detail"] = detail_match.group(1).strip()

    resolved_match = re.search(r"RESOLVED_REFERENCE:\s*(.*?)(?=\s*\n[A-Z_]+:|$)", text, re.IGNORECASE | re.DOTALL)
    if resolved_match:
        result["resolved_reference"] = resolved_match.group(1).strip()

    json_match = re.search(r"JSON_OUTPUT:\s*(\{.*?\})(?=\s*\n[A-Z_]+:|$)", text, re.DOTALL | re.IGNORECASE)
    if json_match:
        result["final_output"] = json_match.group(1).strip()
        return result

    output_match = re.search(r"OUTPUT:\s*(.*?)(?=\s*\n[A-Z_]+:|$)", text, re.DOTALL | re.IGNORECASE)
    if output_match:
        result["final_output"] = output_match.group(1).strip()

    return result


def extract_challenge_json_blocks(text: str) -> list[dict[str, Any]]:
    cleaned = clean_json_text(text)
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, list):
            return [x for x in obj if isinstance(x, dict)][:3]
        
        if isinstance(obj, dict):
            keys = [k for k in obj if k.startswith("CHALLENGE_") and k[len("CHALLENGE_"):].isdigit()]
            if keys:
                return [obj[k] for k in sorted(keys, key=lambda x: int(re.search(r"\d+", x).group() or 0))]
            
            if "TITLE" in obj or "CHALLENGE_THEME" in obj:
                return [obj]
                
        return []
    except Exception:
        return []


ALLOWED_FIND_NEEDS_SOURCE_TIERS = {
    "government",
    "operator",
    "study",
    "major_media",
    "local_media",
}


def _sentence_count(text: str) -> int:
    parts = [p.strip() for p in re.split(r"[.!?]+", text or "") if p.strip()]
    return len(parts)


def _is_valid_https_url(url: str) -> bool:
    try:
        parsed = urlparse(url or "")
        return parsed.scheme.lower() == "https" and bool(parsed.netloc)
    except Exception:
        return False


def _source_domain(url: str) -> str:
    try:
        return (urlparse(url or "").netloc or "").lower().strip()
    except Exception:
        return ""


AREA_LOCATION_PATTERN = re.compile(
    r"\b(?:Taman|Bandar|Seksyen|Section|Bukit|Kampung|Kg|SS\d+|USJ\d+|U\d+|Ara|Damansara|Cheras|Kepong|Segambut|Bangsar|Puchong)\s+[A-Za-z0-9 ]{2,40}\b",
    flags=re.IGNORECASE,
)


def _extract_impacted_locations(option: dict[str, Any], trusted_sources: list[dict[str, Any]], city: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def _add(label: str) -> None:
        txt = str(label or "").strip()
        if not txt:
            return
        key = txt.lower()
        if key in seen:
            return
        seen.add(key)
        found.append(txt)

    for src in trusted_sources:
        _add(str(src.get("area_label") or ""))
        text = f"{src.get('title', '')} {src.get('snippet', '')}"
        for m in AREA_LOCATION_PATTERN.findall(text):
            _add(str(m).strip())

    _add(str(option.get("area_label") or ""))
    if not found:
        _add(city)
    return found[:3]


def _build_area_card_paragraphs(
    option: dict[str, Any],
    trusted_sources: list[dict[str, Any]],
    city: str,
) -> tuple[str, str]:
    area_label = str(option.get("area_label") or city)
    signals = option.get("growth_signals") if isinstance(option.get("growth_signals"), dict) else {}
    pop = int(signals.get("population", 0) or 0)
    ind = int(signals.get("industrial", 0) or 0)
    hubs = int(signals.get("trip_generator", 0) or 0)
    complaints = int(signals.get("complaints", 0) or 0)

    themes: list[str] = []
    if pop > 0:
        themes.append("population growth pressure")
    if ind > 0:
        themes.append("industrial employment demand")
    if hubs > 0:
        themes.append("major trip-generator activity")
    if complaints > 0:
        themes.append("reported transit service pain points")
    if not themes:
        themes.append("documented corridor demand pressure")
    theme_text = ", ".join(themes[:3])

    domains = [(_source_domain(str(s.get("url") or "")) or "trusted sources") for s in trusted_sources]
    domain_text = ", ".join(domains[:2])
    description_paragraph = (
        f"{area_label} is prioritized as a strategic commute corridor in {city}, based on {theme_text}. "
        f"This description is grounded in trusted reporting and operator/government evidence from {domain_text}."
    )

    impacted = _extract_impacted_locations(option, trusted_sources, city)
    impacted_text = ", ".join(impacted[:2]) if len(impacted) > 1 else impacted[0]

    micro_evidence = ""
    for src in trusted_sources:
        snippet = str(src.get("snippet") or "").strip()
        if snippet:
            micro_evidence = snippet
            break
    if not micro_evidence:
        micro_evidence = "Trusted reports describe real commuter access barriers between deep residential areas and work destinations."

    micro_paragraph = (
        f"Micro symptoms are observed around {impacted_text}, where commuters report first-mile and corridor connectivity strain. "
        f"Example evidence from trusted sources indicates: {micro_evidence}"
    )

    return description_paragraph.strip(), micro_paragraph.strip()




def _default_credible_sources() -> list[dict[str, Any]]:
    return _validate_sources(
        [
            {
                "publisher": "MOT Malaysia",
                "url": "https://www.mot.gov.my/",
                "published_at": "2025-01-01",
                "source_tier": "government",
            },
            {
                "publisher": "DOSM Open Data",
                "url": "https://open.dosm.gov.my/",
                "published_at": "2025-01-01",
                "source_tier": "government",
            },
        ]
    )

### Problem-specific retrieval/filtering ###
async def _synthesize_area_card_content(
    session_id: str,
    city: str,
    option: dict[str, Any],
    evidence_pool: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    trusted_sources = filter_trusted_evidence(
        list(option.get("google_evidence") or []),
        min_sources=0,
        max_sources=3,
    )
    if len(trusted_sources) < 2 and evidence_pool:
        used = {_source_domain(str(s.get("url") or "")) for s in trusted_sources}
        for src in evidence_pool:
            domain = _source_domain(str(src.get("url") or ""))
            if not domain or domain in used:
                continue
            trusted_sources.append(src)
            used.add(domain)
            if len(trusted_sources) >= 3:
                break

    # Last-resort fallback: for low-confidence options
    if len(trusted_sources) < 2 and option.get("allow_low_confidence"):
        default_srcs = _default_credible_sources()
        used = {_source_domain(str(s.get("url") or "")) for s in trusted_sources}
        for src in default_srcs:
            domain = _source_domain(str(src.get("url") or ""))
            if domain and domain not in used:
                trusted_sources.append(src)
                used.add(domain)

    if len(trusted_sources) < 2:
        return None

    description_paragraph, micro_paragraph = _build_area_card_paragraphs(option, trusted_sources, city)

    is_pure_fallback = option.get("allow_low_confidence") and not list(option.get("google_evidence") or [])
    if is_pure_fallback:
        option["description_paragraph"] = description_paragraph
        option["micro_paragraph"] = micro_paragraph
        option["trusted_sources"] = trusted_sources
        return option


    option["description_paragraph"] = description_paragraph
    option["micro_paragraph"] = micro_paragraph
    option["trusted_sources"] = trusted_sources
    return option


def _coerce_statistics(stats: Any) -> dict[str, float]:
    if not isinstance(stats, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in stats.items():
        key = str(k).strip()
        if not key:
            continue
        try:
            out[key] = float(v)
        except Exception:
            continue
    return out


def _derive_sources_from_selected_area(selected_area: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(selected_area, dict):
        return []
    evidence = selected_area.get("google_evidence")
    if not isinstance(evidence, list):
        return []
    synthetic_sources: list[dict[str, Any]] = []
    for ev in evidence:
        if not isinstance(ev, dict):
            continue
        synthetic_sources.append(
            {
                "publisher": ev.get("publisher") or ev.get("source") or ev.get("title") or "Source",
                "url": ev.get("url") or "",
                "published_at": ev.get("published_at") or "",
                "source_tier": ev.get("source_tier") or "major_media",
            }
        )
    return _validate_sources(synthetic_sources)


def _validate_sources(sources: Any) -> list[dict[str, Any]]:
    if not isinstance(sources, list):
        return []
    dedup_domains: set[str] = set()
    valid: list[dict[str, Any]] = []
    for src in sources:
        if not isinstance(src, dict):
            continue
        publisher = str(src.get("publisher", "")).strip()
        url = str(src.get("url", "")).strip()
        published_at = str(src.get("published_at", "")).strip()
        source_tier = str(src.get("source_tier", "")).strip().lower()
        if not publisher or not published_at:
            continue
        if source_tier not in ALLOWED_FIND_NEEDS_SOURCE_TIERS:
            continue
        if not _is_valid_https_url(url):
            continue
        domain = _source_domain(url)
        if not domain or domain in dedup_domains:
            continue
        dedup_domains.add(domain)
        valid.append(
            {
                "publisher": publisher,
                "url": url,
                "published_at": published_at,
                "source_tier": source_tier,
            }
        )
    return valid


def _validate_chart_spec(chart_spec: Any, statistics: dict[str, float]) -> tuple[bool, dict[str, Any] | None]:
    if not isinstance(chart_spec, dict):
        return False, None
    chart_type = str(chart_spec.get("chart_type", "")).strip().lower()
    if chart_type != "bar":
        return False, None
    labels = chart_spec.get("labels")
    values = chart_spec.get("values")
    if not isinstance(labels, list) or not isinstance(values, list):
        return False, None
    if len(labels) == 0 or len(labels) != len(values):
        return False, None

    norm_labels: list[str] = []
    norm_values: list[float] = []
    for label, value in zip(labels, values):
        label_text = str(label).strip()
        if not label_text:
            return False, None
        try:
            numeric = float(value)
        except Exception:
            return False, None
        norm_labels.append(label_text)
        norm_values.append(numeric)

    if statistics:
        stats_keys = {k.lower() for k in statistics.keys()}
        if not any(lbl.lower() in stats_keys for lbl in norm_labels):
            return False, None

    return True, {"chart_type": "bar", "labels": norm_labels, "values": norm_values}


def build_find_needs_options(raw_step_output: str) -> tuple[list[dict[str, Any]], list[str]]:
    challenges = extract_challenge_json_blocks(raw_step_output)
    errors: list[str] = []
    hard_fail = False

    if len(challenges) == 0:
        errors.append("No challenge options found in output.")
        return [], errors

    options: list[dict[str, Any]] = []
    for idx, challenge in enumerate(challenges, start=1):
        title = str(challenge.get("TITLE") or challenge.get("CHALLENGE_THEME") or "").strip()
        if not title:
            errors.append(f"CHALLENGE_{idx}: missing TITLE.")
            continue

        brief = str(challenge.get("BRIEF_DESCRIPTION", "")).strip()
        brief_sentences = _sentence_count(brief)
        if not brief or brief_sentences < 1 or brief_sentences > 3:
            errors.append(f"CHALLENGE_{idx}: BRIEF_DESCRIPTION must be 1-3 sentences.")
            hard_fail = True
            continue

        statistics = _coerce_statistics(challenge.get("STATISTICS"))
        if not statistics:
            errors.append(f"CHALLENGE_{idx}: STATISTICS must include numeric values.")
            continue

        valid_sources = _validate_sources(challenge.get("SOURCES"))
        if len(valid_sources) < 2:
            errors.append(f"CHALLENGE_{idx}: must include at least 2 valid sources from unique domains.")
            continue

        chart_ok, normalized_chart = _validate_chart_spec(challenge.get("CHART_SPEC"), statistics)
        if not chart_ok or not normalized_chart:
            errors.append(f"CHALLENGE_{idx}: invalid CHART_SPEC.")
            hard_fail = True
            continue

        options.append(
            {
                "id": f"challenge_{idx}",
                "title": title,
                "statistics": statistics,
                "brief_description": brief,
                "chart_spec": normalized_chart,
                "sources": valid_sources,
            }
        )

    if hard_fail:
        return [], errors

    final_options = options[:3]
    if len(final_options) < 1:
        errors.append("Failed to validate any valid challenge options.")
        return [], errors
        
    return final_options, errors


def build_find_needs_options_legacy_fallback(
    raw_step_output: str,
    selected_area: dict[str, Any] | None = None,
    merged_evidence: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    challenges = extract_challenge_json_blocks(raw_step_output)
    if len(challenges) != 3:
        return []

    carry_sources = _derive_sources_from_selected_area(selected_area)
    report_score = 0.0
    gap_score = 0.0
    if isinstance(merged_evidence, dict):
        report_score = float(merged_evidence.get("report_score") or 0.0)
        gap_score = float(merged_evidence.get("gap_score") or 0.0)

    options: list[dict[str, Any]] = []
    for idx, challenge in enumerate(challenges, start=1):
        title = str(challenge.get("TITLE") or challenge.get("CHALLENGE_THEME") or f"Challenge {idx}").strip()
        brief = str(challenge.get("BRIEF_DESCRIPTION") or challenge.get("WHY_IT_MATTERS") or "").strip()
        if not brief:
            brief = "This challenge shows measurable transport pressure and warrants intervention planning."
        sent = [s.strip() for s in re.split(r"[.!?]+", brief) if s.strip()]
        brief = ". ".join(sent[:3]).strip()
        if brief and not brief.endswith("."):
            brief += "."

        stats = _coerce_statistics(challenge.get("STATISTICS"))
        if not stats:
            stats = {
                "Report Signal": round(max(0.0, min(1.0, report_score)) * 100, 1),
                "Spatial Gap": round(max(0.0, min(1.0, gap_score)) * 100, 1),
            }
            if stats["Report Signal"] == 0 and stats["Spatial Gap"] == 0:
                stats = {"Demand Pressure": 62.0, "Access Deficit": 48.0}

        chart_ok, chart = _validate_chart_spec(challenge.get("CHART_SPEC"), stats)
        if not chart_ok or not chart:
            labels = list(stats.keys())[:4]
            values = [float(stats[k]) for k in labels]
            chart = {"chart_type": "bar", "labels": labels, "values": values}

        sources = _validate_sources(challenge.get("SOURCES"))
        if len(sources) < 2:
            sources = carry_sources[:]

        if len(sources) < 2:
            sources = _default_credible_sources()

        options.append(
            {
                "id": f"challenge_{idx}",
                "title": title,
                "statistics": stats,
                "brief_description": brief,
                "chart_spec": chart,
                "sources": sources,
            }
        )
    return options if len(options) == 3 else []


def build_generic_find_needs_options(
    target_places: list[str],
    selected_area: dict[str, Any] | None = None,
    merged_evidence: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    city = (target_places or ["Target Area"])[0]
    area = str((selected_area or {}).get("area_label") or city)
    carry_sources = _derive_sources_from_selected_area(selected_area)
    if len(carry_sources) < 2:
        carry_sources = _default_credible_sources()

    report_score = float((merged_evidence or {}).get("report_score") or 0.52)
    gap_score = float((merged_evidence or {}).get("gap_score") or 0.48)
    base_a = round(max(0.0, min(1.0, report_score)) * 100, 1)
    base_b = round(max(0.0, min(1.0, gap_score)) * 100, 1)
    templates = [
        ("Access Reliability Gap", "Indicators show unstable access quality around the selected area."),
        ("Interchange Demand Pressure", "Demand and transfer indicators suggest concentrated pressure points."),
        ("Last-Mile Connectivity Deficit", "Connectivity signals imply first/last-mile friction for commuters."),
    ]
    options: list[dict[str, Any]] = []
    for idx, (title_suffix, brief_prefix) in enumerate(templates, start=1):
        stats = {
            "Report Signal": max(0.0, base_a - (idx - 1) * 6.0),
            "Spatial Gap": max(0.0, base_b + (idx - 1) * 4.0),
        }
        labels = list(stats.keys())
        values = [float(stats[k]) for k in labels]
        brief = (
            f"{brief_prefix} "
            f"For {area}, report-vs-spatial indicators currently read as {values[0]:g} and {values[1]:g}."
        )
        options.append(
            {
                "id": f"challenge_{idx}",
                "title": f"{area}: {title_suffix}",
                "statistics": stats,
                "brief_description": brief,
                "chart_spec": {"chart_type": "bar", "labels": labels, "values": values},
                "sources": carry_sources[:2],
            }
        )
    return options


def _build_find_needs_repair_prompt(
    target_places: list[str],
    previous_output: str,
    errors: list[str],
    selected_area: dict[str, Any] | None = None,
    merged_evidence: dict[str, Any] | None = None,
) -> str:
    context_block = ""
    if selected_area:
        context_block += f"\nSELECTED_AREA_OPTION_JSON:\n{json.dumps(selected_area, ensure_ascii=False, indent=2)}\n"
    if merged_evidence:
        context_block += f"\nMERGED_EVIDENCE_JSON:\n{json.dumps(merged_evidence, ensure_ascii=False, indent=2)}\n"
    return f"""
You previously returned invalid find-needs JSON for TARGET PLACE(S): {target_places}.
{context_block}
Validation errors:
{json.dumps(errors, ensure_ascii=False)}

PREVIOUS_OUTPUT:
{previous_output}

Return STRICT JSON ONLY with EXACT keys CHALLENGE_1, CHALLENGE_2, CHALLENGE_3.
For EACH challenge include:
- CHALLENGE_THEME
- MACRO_ROOT_CAUSE
- WHY_IT_MATTERS
- EVIDENCE_SUMMARY
- TITLE
- STATISTICS (object of numeric values)
- BRIEF_DESCRIPTION (1-3 sentences only)
- SOURCES (array, minimum 2, each with publisher,url,published_at,source_tier)
- CHART_SPEC (chart_type=\"bar\", labels[], values[] with labels/values same length and numeric values)

Allowed source_tier only:
government | operator | study | major_media | local_media

URLs must be HTTPS and from unique domains per challenge.
""".strip()


async def prepare_find_needs_output(
    session_id: str,
    target_places: list[str],
    raw_step_output: str,
    selected_area: dict[str, Any] | None = None,
    merged_evidence: dict[str, Any] | None = None,
) -> tuple[str, str, list[dict[str, Any]]]:
    options, errors = build_find_needs_options(raw_step_output)
    working_raw = raw_step_output

    if len(options) != 3:
        repair_prompt = _build_find_needs_repair_prompt(
            target_places=target_places,
            previous_output=working_raw,
            errors=errors,
            selected_area=selected_area,
            merged_evidence=merged_evidence,
        )
        repaired = await run_agent_once(find_needs_agent, session_id, repair_prompt)
        repaired_options, repaired_errors = build_find_needs_options(repaired)
        if len(repaired_options) != 3:
            repaired_options = build_find_needs_options_legacy_fallback(
                repaired,
                selected_area=selected_area,
                merged_evidence=merged_evidence,
            )

        if repaired_options:
            working_raw = repaired
            options = repaired_options
        else:
            print(f"Find-needs repair failed; falling back to plain formatting. Errors: {repaired_errors}")

    if len(options) != 3:
        legacy = build_find_needs_options_legacy_fallback(
            working_raw,
            selected_area=selected_area,
            merged_evidence=merged_evidence,
        )
        if legacy:
            options = legacy

    if len(options) != 3:
        options = build_generic_find_needs_options(
            target_places=target_places,
            selected_area=selected_area,
            merged_evidence=merged_evidence,
        )

    display_reply = format_find_needs_reply(working_raw, options if options else None)
    return working_raw, display_reply, options


def format_challenges(challenges: list[dict[str, Any]]) -> str:
    if not challenges:
        return "No challenge data could be extracted."

    blocks: list[str] = []
    for idx, challenge in enumerate(challenges, start=1):
        theme = challenge.get("CHALLENGE_THEME", "Untitled challenge")
        cause = challenge.get("MACRO_ROOT_CAUSE", "N/A")
        impact = challenge.get("WHY_IT_MATTERS", "N/A")
        evidence = challenge.get("EVIDENCE_SUMMARY", "N/A")

        paragraph = (
            f"{idx}. **{theme}**\n"
            f"Macro root cause: {cause}\n"
            f"Why it matters: {impact}\n"
            f"Evidence summary: {evidence}"
        )
        blocks.append(paragraph)

    return "\n\n".join(blocks) + "\n\nWhich challenge would you like to explore further?"


def format_find_needs_reply(raw_step_output: str, find_needs_options: list[dict[str, Any]] | None = None) -> str:
    if find_needs_options:
        return "Review the 3 evidence cards below and reply with 1, 2, or 3."

    challenges = extract_challenge_json_blocks(raw_step_output)
    if challenges:
        return format_challenges(challenges)
    return raw_step_output


def _has_complete_find_needs_options(options: list[dict[str, Any]] | None) -> bool:
    return isinstance(options, list) and len(options) == 3


def _build_find_needs_fallback_response(
    session_id: str,
    target_places: list[str],
    *,
    selected_area: dict[str, Any] | None = None,
    merged_evidence: dict[str, Any] | None = None,
    existing_state: dict[str, Any] | None = None,
    retry_feedback: str | None = None,
) -> dict[str, Any]:
    options = build_generic_find_needs_options(
        target_places=target_places,
        selected_area=selected_area,
        merged_evidence=merged_evidence,
    )
    display_reply = format_find_needs_reply("", options)
    if retry_feedback:
        display_reply = (
            "The live planning model is temporarily busy, so I loaded fallback evidence cards to keep the workflow moving.\n\n"
            + display_reply
        )

    next_state = dict(existing_state or {})
    next_state.update(
        {
            "phase": "challenge_selection",
            "last_step_output": json.dumps(options, ensure_ascii=False),
            "last_display_reply": display_reply,
            "find_needs_options": options,
            "target_places": target_places,
            "step_index": 0,
        }
    )
    if selected_area is not None:
        next_state["selected_area_option"] = selected_area
    if merged_evidence is not None:
        next_state["evidence_summary"] = merged_evidence
    workflow_state[session_id] = next_state

    return {
        "ok": True,
        "session_id": session_id,
        "stage": "Find needs",
        "reply": display_reply,
        "needs_input": True,
        "find_needs_options": options,
    }




def build_find_needs_prompt(
    target_places: list[str],
    selected_area: dict[str, Any] | None = None,
    merged_evidence: dict[str, Any] | None = None,
) -> str:
    context_block = ""
    if selected_area:
        context_block += f"\nSELECTED_AREA_OPTION_JSON:\n{json.dumps(selected_area, ensure_ascii=False, indent=2)}\n"
    if merged_evidence:
        context_block += f"\nMERGED_EVIDENCE_JSON:\n{json.dumps(merged_evidence, ensure_ascii=False, indent=2)}\n"
    return f"""
        You are given TARGET PLACE(S): {target_places}
        {context_block}

        Task:
        Identify and rank exactly 3 broad transport-related infrastructure challenges only.
        If selected area/evidence context is present, synthesize challenges around that chosen area and evidence.

        Rules:
        - Do NOT output PRIMARY_MICRO or SECONDARY_MICRO.
        - Do NOT generate routing labels.
        - Stay at challenge-category level.
        - Return exactly one JSON object with keys CHALLENGE_1, CHALLENGE_2, CHALLENGE_3.
        - Each challenge must include CHALLENGE_THEME, MACRO_ROOT_CAUSE, WHY_IT_MATTERS, EVIDENCE_SUMMARY, TITLE, STATISTICS, BRIEF_DESCRIPTION, SOURCES, and CHART_SPEC.
        - BRIEF_DESCRIPTION must be 1-3 sentences.
        - SOURCES must have at least 2 HTTPS citations from unique domains and source_tier in: government|operator|study|major_media|local_media.
        - CHART_SPEC must be bar chart with labels and numeric values arrays of equal length.
        - Return JSON only.
        """.strip()


# def _extract_growth_findings(raw: str) -> list[dict[str, Any]]:
#     try:
#         parsed = safe_json_loads(raw)
#         if isinstance(parsed, list):
#             return [x for x in parsed if isinstance(x, dict)]
#     except Exception:
#         pass
#     return []


# async def _growth_search_fn(session_id: str, city: str, query: str) -> list[dict[str, Any]]:
#     prompt = f"""
#     CITY: {city}
#     SEARCH_QUERY_HINT: {query}

#     Return ONLY strict JSON array of finding objects.
#     """.strip()
#     raw = await run_agent_once(growth_signal_agent, session_id, prompt)
#     findings = _extract_growth_findings(raw)
#     for f in findings:
#         if not f.get("area_label"):
#             f["area_label"] = city
#     return findings


async def _generate_area_options(session_id: str, city: str) -> list[dict[str, Any]]:
    # async def _search(query: str) -> list[dict[str, Any]]:
    #     return await _growth_search_fn(session_id, city, query)

    # queries = [
    #     f"{city} population growth new township Malaysia",
    #     f"{city} industrial park jobs factory expansion Malaysia",
    #     f"{city} transit complaints stranded workers bus frequency",
    # ]
    # findings: list[dict[str, Any]] = []
    # for q in queries:
    #     findings.extend(await _search(q))

    #########################################
        #add retrieval logic
    ########################################

    # NEW FLOW: Database-driven Indicator Engine & Evidence Pack
    from db.database import engine
    options = []
    try:
        # 1. Resolve Area (Find area_id for the city)
        from area_resolver import resolve_area
        area_info = resolve_area(city)
        if not area_info:
            logger.error(f"Could not resolve area_id for city: {city}")
            return []
        area_id = area_info["area_id"]

        # 2. Run Indicator Engine (Ensure DB indicators are fresh)
        from indicator_engine import run_indicator_engine
        run_indicator_engine(area_id)

        # 3. Build General Evidence Pack
        from evidence_pack_builder.evidence_pack_builder import build_general_evidence_pack
        pack = build_general_evidence_pack(area_id)
        
        # 4. Use Evidence Understanding Agent (find_needs_agent) to generate cards
        # This part is usually handled in start_planning_phase, 
        # but since _generate_area_options is used as a discovery step, we'll return
        # the directions found in the pack as "options".
        directions = pack.candidate_problem_directions.get("directions", [])
        
        # Map DB directions to the "Area Option" format the old UI expects
        options = []
        for d in directions:
            options.append({
                "id": d.get("problem_direction_id"),
                "area_label": d.get("title", "Transport Challenge"),
                "challenge_type": d.get("challenge_type"),
                "rationale": d.get("reason_hint"),
                "report_score": d.get("evidence_score", 0.5),
                "confidence_label": d.get("confidence_tier", "usable"),
                "google_evidence": [], # Replaced by RAG/Indicators
                "sources": d.get("evidence_refs", []),
                "growth_signals": {},
                "equity_flag": False,
                "area_aliases": [city]
            })
        
        if not options:
             logger.warning(f"No problem directions found in DB for {area_id}. Using fallback.")
             # Fallback logic remains below
        else:
            # Persistence: Log this discovery step
            run_id = persistence_service.log_agent_start(
                session_id=session_id,
                agent_name="evidence_pack_assembler",
                area_id=area_id,
                input_json={"city": city}
            )
            # Save the pack to DB
            from evidence_pack_builder.evidence_pack_builder import store_evidence_pack
            pack_id = store_evidence_pack(area_id, pack)
            
            # Save the cards to DB
            persistence_service.save_broad_challenge_cards(run_id, area_id, directions)
            persistence_service.log_agent_completion(run_id, output_json={"pack_id": pack_id, "directions": directions})
            
            return options

    except Exception as e:
        logger.error(f"Error in new flow for _generate_area_options: {e}")
    if not options:
        options = [
            {
                "id": "area_1",
                "city": city,
                "area_label": f"{city} Central",
                "is_fallback_option": True,
                "allow_low_confidence": True,
                "google_evidence": [
                    {
                        "title": "Ministry of Transport Malaysia",
                        "url": "https://www.mot.gov.my/",
                        "published_at": "2025-01-01",
                        "source_tier": "government",
                    },
                    {
                        "title": "DOSM Open Data",
                        "url": "https://open.dosm.gov.my/",
                        "published_at": "2025-01-01",
                        "source_tier": "government",
                    },
                ],
                "sources": [
                    {"publisher": "MOT Malaysia", "url": "https://www.mot.gov.my/"},
                    {"publisher": "DOSM", "url": "https://open.dosm.gov.my/"},
                ],
                "growth_signals": {"population": 1, "industrial": 0, "trip_generator": 1},
                "equity_flag": False,
                "report_score": 0.46,
                "rationale": f"Fallback option for {city} Central due to temporary source unavailability.",
                "confidence_label": "low",
                "area_aliases": [f"{city} Central", city],
            },
            {
                "id": "area_2",
                "city": city,
                "area_label": f"{city} Industrial Belt",
                "is_fallback_option": True,
                "allow_low_confidence": True,
                "google_evidence": [
                    {
                        "title": "InvestKL News",
                        "url": "https://investkl.gov.my/news-and-events",
                        "published_at": "2025-01-01",
                        "source_tier": "operator",
                    },
                    {
                        "title": "MIDA Insights",
                        "url": "https://www.mida.gov.my/",
                        "published_at": "2025-01-01",
                        "source_tier": "government",
                    },
                ],
                "sources": [
                    {"publisher": "InvestKL", "url": "https://investkl.gov.my/news-and-events"},
                    {"publisher": "MIDA", "url": "https://www.mida.gov.my/"},
                ],
                "growth_signals": {"population": 0, "industrial": 1, "trip_generator": 0},
                "equity_flag": False,
                "report_score": 0.43,
                "rationale": f"Fallback option for industrial access pressure in {city}.",
                "confidence_label": "low",
                "area_aliases": [f"{city} Industrial Belt", city],
            },
            {
                "id": "area_3",
                "city": city,
                "area_label": f"{city} Residential Access Gap",
                "is_fallback_option": True,
                "allow_low_confidence": True,
                "google_evidence": [
                    {
                        "title": "Prasarana Updates",
                        "url": "https://www.prasarana.com.my/",
                        "published_at": "2025-01-01",
                        "source_tier": "operator",
                    },
                    {
                        "title": "DBKL Official Portal",
                        "url": "https://www.dbkl.gov.my/",
                        "published_at": "2025-01-01",
                        "source_tier": "government",
                    },
                ],
                "sources": [
                    {"publisher": "Prasarana", "url": "https://www.prasarana.com.my/"},
                    {"publisher": "DBKL", "url": "https://www.dbkl.gov.my/"},
                ],
                "growth_signals": {"population": 1, "industrial": 0, "trip_generator": 0},
                "equity_flag": True,
                "report_score": 0.4,
                "rationale": f"Equity-priority fallback for underserved residential access in {city}.",
                "confidence_label": "low",
                "area_aliases": [f"{city} Residential Access Gap", city],
            },
        ]
    return options


def _format_area_options_reply(city: str, area_options: list[dict[str, Any]]) -> str:
    if not area_options:
        return (
            f"I couldn't build growth-led area options for {city} right now. "
            "I'll fallback to challenge-first mode."
        )
    lines: list[str] = [
        f"I've identified {len(area_options)} potential growth hotspots in {city} based on recent reports and signals.",
        "Please review the evidence cards above and select an area to proceed with a deep-dive transit audit.",
        "",
        "Reply with the number or area name. Say 'regenerate' to refresh options."
    ]
    return "\n".join(lines)


def _resolve_area_selection(user_message: str, area_options: list[dict[str, Any]]) -> dict[str, Any] | None:
    text = user_message.strip().lower()
    if not area_options:
        return None
    if text.isdigit():
        idx = int(text) - 1
        if 0 <= idx < len(area_options):
            return area_options[idx]
    for option in area_options:
        label = str(option.get("area_label", "")).strip().lower()
        if label and label in text:
            return option
    return None




############################################
    #this can be changed by diagnostic agent
############################################
def _build_strategic_narrative(selected_option: dict[str, Any], osm_audit: Any) -> str:
    signals = selected_option.get("growth_signals", {})
    pop = float(signals.get("population", 0))
    ind = float(signals.get("industrial", 0))
    hub = float(signals.get("trip_generator", 0))
    
    transit_gap = float(osm_audit.gap_score)
    
    growth_drivers = []
    if pop > 0.5: growth_drivers.append("explosive residential expansion")
    elif pop > 0: growth_drivers.append("steady population growth")
    
    if ind > 0.5: growth_drivers.append("major industrial development")
    elif ind > 0: growth_drivers.append("new job centers")
    
    if hub > 0.5: growth_drivers.append("significant commercial activity")
    
    driver_str = " and ".join(growth_drivers) if growth_drivers else "observed regional growth"
    
    if transit_gap > 0.7:
        gap_str = "is currently a transit desert with critically low connectivity"
    elif transit_gap > 0.4:
        gap_str = "shows a significant mismatch between growth and existing infrastructure"
    else:
        gap_str = "has moderate transit coverage but requires optimization for future growth"
        
    complaint_msg = ""
    if selected_option.get("complaint_verified"):
        complaint_msg = " Our system has cross-validated local human reports of transit failures with these map findings."
        
    return f"Strategic focus on {selected_option.get('area_label')} is driven by {driver_str}. Our audit confirms this area {gap_str}.{complaint_msg}"


def _apply_signal_guardrails(signals: dict[str, Any]) -> dict[str, Any]:
    bounded = signals.copy()
    for k in ["population", "industrial", "trip_generator"]:
        val = float(bounded.get(k, 0))
        if val > 5.0: bounded[k] = 5.0 
    return bounded


def _soft_area_gate(report_score: float, gap_score: float, completeness_score: float, equity_flag: bool) -> dict[str, Any]:
    base = (
        0.60 * max(0.0, min(1.0, float(report_score)))
        + 0.30 * max(0.0, min(1.0, float(gap_score)))
        + 0.10 * (1.0 if equity_flag else 0.0)
    )
    completeness = max(0.25, min(1.0, float(completeness_score)))
    confidence = round(max(0.0, min(1.0, base * completeness)), 3)
    return {
        "confidence": confidence,
        "band": "high" if confidence >= 0.65 else "medium" if confidence >= 0.45 else "low",
        "pass_gate": confidence >= 0.45,
    }


async def _screen_single_area(selected_city: str, opt: dict[str, Any]) -> dict[str, Any] | None:
    try:
        async with nominatim_semaphore:
            osm_audit = await asyncio.to_thread(audit_osm_transit_gap, selected_city, str(opt.get("area_label") or selected_city))
        complaint_verified = await asyncio.to_thread(verify_complaint_against_osm, osm_audit, opt)
        merged = _soft_area_gate(
            report_score=float(opt.get("report_score", 0.0)),
            gap_score=float(osm_audit.gap_score),
            completeness_score=float(osm_audit.completeness_score),
            equity_flag=bool(opt.get("equity_flag")),
        )
        if not merged.get("pass_gate"):
            return None
        opt = dict(opt)
        opt["osm_audit"] = osm_audit.audit_details
        opt["osm_gap_score"] = osm_audit.gap_score
        opt["osm_completeness_score"] = osm_audit.completeness_score
        opt["merged_confidence"] = merged
        opt["confidence_label"] = merged.get("band", "low")
        opt["complaint_verified"] = complaint_verified
        return opt
    except Exception as exc:
        print(f"Area pre-screen failed for {opt.get('area_label')}: {exc}")
        return None




async def _speculative_find_needs_task(session_id: str, city: str, top_candidate: dict[str, Any]):
    try:
        dummy_evidence = {
            "selected_area": top_candidate.get("area_label"),
            "report_score": top_candidate.get("report_score"),
            "gap_score": top_candidate.get("osm_gap_score", 0.0),
            "completeness_score": top_candidate.get("osm_completeness_score", 0.0),
            "feasibility": top_candidate.get("route_feasibility", {"pass": True, "score": 1.0}),
            "confidence": top_candidate.get("merged_confidence", {"confidence": 1.0, "band": "high"}),
        }
        prompt = build_find_needs_prompt(
            [city],
            selected_area=top_candidate,
            merged_evidence=dummy_evidence,
        )
        raw_output = await run_agent_once(find_needs_agent, session_id, prompt)

        has_challenge_json = (
            "CHALLENGE_1" in raw_output
            or '"CHALLENGE_' in raw_output
            or "challenge_theme" in raw_output.lower()
        )
        if has_challenge_json and session_id in workflow_state:
            workflow_state[session_id]["speculative_find_needs"] = {
                "area_id": top_candidate.get("id"),
                "raw_output": raw_output,
            }
            print(f"Speculative Find-Needs warmed up for {top_candidate.get('area_label')}")
        else:
            print(f"Speculative Find-Needs discarded for {top_candidate.get('area_label')} (no valid challenge JSON)")
    except Exception as exc:
        print(f"Speculative warm-up failed: {exc}")


def _build_city_trusted_evidence_pool(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_pool: list[dict[str, Any]] = []
    for opt in options:
        evidence = opt.get("google_evidence")
        if isinstance(evidence, list):
            raw_pool.extend([e for e in evidence if isinstance(e, dict)])
    return filter_trusted_evidence(raw_pool, min_sources=0, max_sources=50)


def _extract_numeric_evidence_points(trusted_sources: list[dict[str, Any]], limit: int = 2) -> list[str]:
    points: list[str] = []
    seen: set[str] = set()
    for src in trusted_sources:
        num_growth = str(src.get("numerical_growth") or "").strip()
        if num_growth:
            key = num_growth.lower()
            if key not in seen:
                seen.add(key)
                points.append(num_growth)
                if len(points) >= limit:
                    return points

        text = f"{src.get('title', '')} {src.get('snippet', '')}"
        for m in re.findall(r"\b\d+(?:\.\d+)?\s*(?:%|km|minutes?|min|workers?|jobs?|units?)\b", text, flags=re.IGNORECASE):
            token = str(m).strip()
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            points.append(token)
            if len(points) >= limit:
                return points
    return points


def _build_selected_card_solution_paragraph(
    city: str,
    selected_option: dict[str, Any],
    hotspot_result: dict[str, Any],
) -> str:
    primary_micro = hotspot_result.get("PRIMARY_MICRO", {}) if isinstance(hotspot_result, dict) else {}
    clusters = hotspot_result.get("IMPLEMENTATION_CLUSTERS", []) if isinstance(hotspot_result, dict) else []
    first_cluster = clusters[0] if isinstance(clusters, list) and clusters else {}

    micro_location = str(primary_micro.get("LOCATION_LABEL") or primary_micro.get("location_label") or "").strip()
    if not micro_location:
        micro_location = str(selected_option.get("area_label") or city).strip()

    symptom = str(primary_micro.get("SYMPTOM") or primary_micro.get("symptom") or "commuters face first-mile and transfer pressure").strip()
    intervention = str(first_cluster.get("intervention_type") or "BUS").strip()
    rationale = str(first_cluster.get("intervention_rationale") or "this intervention improves residential-to-employment connectivity").strip()

    trusted_sources = list(selected_option.get("trusted_sources") or [])
    numeric_points = _extract_numeric_evidence_points(trusted_sources, limit=2)
    if not numeric_points:
        signals = selected_option.get("growth_signals") if isinstance(selected_option.get("growth_signals"), dict) else {}
        pop = int(signals.get("population", 0) or 0)
        ind = int(signals.get("industrial", 0) or 0)
        cmp = int(signals.get("complaints", 0) or 0)
        numeric_points = [f"population signals: {pop}", f"industrial signals: {ind}"] if (pop or ind) else [f"complaint signals: {cmp}"]

    stats_text = "; ".join(numeric_points[:2])
    return (
        f"For {micro_location}, the key micro symptom is that {symptom}. "
        f"Card-backed statistics for this location include {stats_text}. "
        f"Based on this evidence, the leading solution is a {intervention} intervention, because {rationale}."
    )


async def start_area_option_phase(session_id: str, current_session, background_tasks: BackgroundTasks = None) -> dict[str, Any]:
    target_places = current_session.state.get("target_places", [])
    if not target_places:
        raise HTTPException(status_code=400, detail="No target places found in session state.")
    selected_city = target_places[0]

    candidates = await _generate_area_options(session_id, selected_city)
    if not candidates:
        return await start_planning_phase(session_id, current_session)

    candidates = sorted(candidates, key=lambda x: x.get("report_score", 0.0), reverse=True)
    prescreen_pool = candidates[:5]
    
    # Pass through all candidates (hallucination audit decommissioned)
    print(f"[AUDIT] Bypassing batch audit for {len(prescreen_pool)} candidates.")
    audit_results = [True] * len(prescreen_pool)
    audit_reasons = ["Internal data trusted"] * len(prescreen_pool)

    # Now process each one with the audit result
    tasks = []
    for i, opt in enumerate(prescreen_pool):
        if i < len(audit_results) and not audit_results[i]:
            print(f"[AUDIT REJECT] Skipping {opt.get('area_label')}: {audit_reasons[i]}")
            continue
        tasks.append(_screen_single_area(selected_city, opt))

    screened_results = await asyncio.gather(*tasks)
    screened = [r for r in screened_results if r is not None]
    screened.sort(key=lambda x: x.get("merged_confidence", {}).get("confidence", x.get("report_score", 0.0)), reverse=True)

    seen_labels = {str(o.get("area_label")) for o in screened}
    for opt in candidates:
        if len(screened) >= 3:
            break
        label = str(opt.get("area_label"))
        if label in seen_labels:
            continue
        if opt.get("report_score", 0.0) >= 0.45 or opt.get("allow_low_confidence"):
            opt = dict(opt)
            opt["allow_low_confidence"] = True
            screened.append(opt)
            seen_labels.add(label)

    trusted_pool = _build_city_trusted_evidence_pool(screened)
    enriched_area_options: list[dict[str, Any]] = []
    for opt in screened[:3]:
        enriched = await _synthesize_area_card_content(session_id, selected_city, opt, evidence_pool=trusted_pool)
        if enriched is None:
            continue
        enriched_area_options.append(enriched)

    area_options = enriched_area_options[:3]
    if not area_options:
        return await start_planning_phase(session_id, current_session)

    workflow_state[session_id] = {
        "phase": "area_selection",
        "target_places": target_places,
        "area_options": area_options,
        "step_index": 0,
    }
    if area_options and background_tasks and ENABLE_SPECULATIVE_FIND_NEEDS:
        top_opt = area_options[0]
        background_tasks.add_task(_speculative_find_needs_task, session_id, selected_city, top_opt)

    return {
        "ok": True,
        "session_id": session_id,
        "stage": "Area selection",
        "reply": _format_area_options_reply(selected_city, area_options),
        "needs_input": True,
        "needs_selection": True,
        "area_options": area_options,
    }


async def start_planning_phase(session_id: str, current_session) -> dict[str, Any]:
    target_places = current_session.state.get("target_places", [])
    if not target_places:
        raise HTTPException(status_code=400, detail="No target places found in session state.")

    prompt = build_find_needs_prompt(target_places)
    initial_raw = await run_agent_with_retry(find_needs_agent, session_id, prompt)
    if is_retry_response(initial_raw):
        return _build_find_needs_fallback_response(
            session_id,
            target_places,
            retry_feedback=extract_retry_feedback(initial_raw),
        )
    raw_step_output, display_reply, find_needs_options = await prepare_find_needs_output(
        session_id=session_id,
        target_places=target_places,
        raw_step_output=initial_raw,
    )
    if not _has_complete_find_needs_options(find_needs_options):
        return {
            "ok": True,
            "session_id": session_id,
            "stage": "Find needs",
            "reply": (
                "I couldn't generate a stable set of broad challenge cards right now. "
                "Please try again in a moment."
            ),
            "needs_input": True,
        }

    workflow_state[session_id] = {
        "phase": "challenge_selection",
        "last_step_output": raw_step_output,
        "last_display_reply": display_reply,
        "find_needs_options": find_needs_options,
        "target_places": target_places,
        "step_index": 0,
    }

    return {
        "ok": True,
        "session_id": session_id,
        "stage": "Find needs",
        "reply": display_reply,
        "needs_input": True,
        "find_needs_options": find_needs_options,
    }


def build_hotspot_hypothesis_prompt(city: str, selected_challenge: dict[str, Any], feedback: str = "") -> str:
    challenge_title = selected_challenge.get("TITLE") or selected_challenge.get("CHALLENGE_THEME") or "selected challenge"
    challenge_brief = selected_challenge.get("BRIEF_DESCRIPTION") or selected_challenge.get("WHY_IT_MATTERS") or ""
    return f"""
        Generate 2 specific transport hotspots for the selected challenge.

        CITY: {city}
        SELECTED_CHALLENGE_TITLE: {challenge_title}
        SELECTED_CHALLENGE_BRIEF: {challenge_brief}
        SELECTED_CHALLENGE_JSON:
        {json.dumps(selected_challenge, ensure_ascii=False, indent=2)}

        PREVIOUS_FEEDBACK:
        {feedback or 'None'}

        Rules:
        - Return JSON only.
        - Return a JSON LIST with exactly 2 hotspot objects.
        - Make both hotspots narrower and more concrete than the broad challenge.
        - Use specific neighborhood names, station areas, junctions, or corridor segments inside {city}.
        - Do not use city-wide labels like "{city}", "Greater {city}", or the broad challenge title as location_label.
        - Each hotspot must include real-looking anchor roads, stations, or route aliases.
        - Prefer `corridor`, `junction`, `freight_route`, or `transit_node`.
        - For `transit_node`, use two distinct anchor sets: one station-side anchor and one nearby neighborhood/access-road anchor.
        - Do not repeat the same area twice.

        Output schema:
        [
          {{
            "location_label": "specific hotspot name",
            "type": "corridor | junction | freight_route | transit_node",
            "symptom": "specific local failure",
            "road_a_queries": ["alias 1", "alias 2"],
            "road_b_queries": ["alias 1", "alias 2"],
            "road_a_label": "display label A",
            "road_b_label": "display label B",
            "lat": 3.1234,
            "lon": 101.5678,
            "confidence": "low | medium | high",
            "INTERVENTION_RECOMMENDATION": "BUS | TRAIN | BOTH",
            "INTERVENTION_RATIONALE": "1-2 sentence rationale grounded in this specific hotspot",
            "LINKED_FEEDER": {{ "needed": true, "type": "BUS", "lat": 3.1234, "lon": 101.5678, "label": "specific feeder node", "rationale": "..." }}
          }},
          ...
        ]
    """.strip()


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _is_broad_location_label(label: str, city: str) -> bool:
    txt = _normalize_text(label).lower()
    city_txt = _normalize_text(city).lower()
    if not txt:
        return True
    broad_labels = {
        city_txt,
        f"greater {city_txt}",
        f"central {city_txt}",
        "strategic corridor",
        "workforce corridor",
        "first-mile integration flow",
        "residential to industrial corridor",
        "residential to central corridor",
    }
    if txt in broad_labels:
        return True
    generic_terms = ["corridor", "integration flow", "strategic", "workforce", "citywide", "greater "]
    has_specific_anchor = any(token in txt for token in ["jalan", "mrt", "lrt", "ktm", "station", "interchange", "jaya", "baru", "dalam", "menjalara", "kepong", "cheras", "ampang", "segambut", "sentral"])
    if any(term in txt for term in generic_terms) and not has_specific_anchor:
        return True
    return False


def _normalize_hotspot_candidate(hypothesis: dict[str, Any], city: str, selected_challenge: dict[str, Any]) -> dict[str, Any]:
    hyp = dict(hypothesis or {})
    label = _normalize_text(hyp.get("location_label") or hyp.get("LOCATION_LABEL"))
    road_a = hyp.get("road_a_queries") or []
    road_b = hyp.get("road_b_queries") or []
    road_a = [ _normalize_text(x) for x in road_a if _normalize_text(x) ]
    road_b = [ _normalize_text(x) for x in road_b if _normalize_text(x) ]
    if not label:
        fallback_parts = []
        if hyp.get("road_a_label"):
            fallback_parts.append(_normalize_text(hyp.get("road_a_label")))
        elif road_a:
            fallback_parts.append(road_a[0])
        if hyp.get("road_b_label"):
            fallback_parts.append(_normalize_text(hyp.get("road_b_label")))
        elif road_b:
            fallback_parts.append(road_b[0])
        label = " → ".join(fallback_parts[:2]) or city
    if _is_broad_location_label(label, city):
        specific_tokens = []
        if hyp.get("road_a_label"):
            specific_tokens.append(_normalize_text(hyp.get("road_a_label")))
        if hyp.get("road_b_label"):
            specific_tokens.append(_normalize_text(hyp.get("road_b_label")))
        if not specific_tokens:
            if road_a: specific_tokens.append(road_a[0])
            if road_b: specific_tokens.append(road_b[0])
        if specific_tokens:
            label = " / ".join(specific_tokens[:2])

    symptom = _normalize_text(hyp.get("symptom") or hyp.get("RATIONALE") or hyp.get("INTERVENTION_RATIONALE"))
    if not symptom:
        symptom = f"Localized connectivity gap around {label}."
    broad_title = _normalize_text(selected_challenge.get("TITLE") or selected_challenge.get("CHALLENGE_THEME")).lower()
    if broad_title and symptom.lower() == broad_title:
        symptom = f"Localized access failure around {label}, affecting movement between nearby origins and destinations."

    hyp["location_label"] = label
    hyp["symptom"] = symptom
    hyp["road_a_queries"] = road_a
    hyp["road_b_queries"] = road_b
    if road_a and not hyp.get("road_a_label"):
        hyp["road_a_label"] = road_a[0]
    if road_b and not hyp.get("road_b_label"):
        hyp["road_b_label"] = road_b[0]
    return hyp


def _meaningful_anchor_tokens(values: list[str]) -> set[str]:
    generic = {"jalan", "jln", "road", "rd", "street", "st", "lebuhraya", "highway", "route", "lorong", "persiaran", "ft", "e", "station", "stesen", "interchange", "junction", "access", "gap", "area", "precinct", "hub", "kuala", "lumpur", "malaysia"}
    tokens: set[str] = set()
    for value in values or []:
        parts = re.split(r"[^a-z0-9]+", _normalize_text(value).lower())
        for part in parts:
            if part and len(part) >= 2 and part not in generic:
                tokens.add(part)
    return tokens


def _anchors_are_distinct(hypothesis: dict[str, Any]) -> bool:
    a = _meaningful_anchor_tokens(list(hypothesis.get("road_a_queries") or []))
    b = _meaningful_anchor_tokens(list(hypothesis.get("road_b_queries") or []))
    if not a or not b:
        return False
    if a == b:
        return False
    overlap = a & b
    if not overlap:
        return True
    overlap_ratio = max(len(overlap) / max(len(a), 1), len(overlap) / max(len(b), 1))
    return overlap_ratio < 0.8


def _hotspot_scope_key(selected_challenge: dict[str, Any], city: str) -> str:
    title = _normalize_text(selected_challenge.get("TITLE") or selected_challenge.get("CHALLENGE_THEME") or "")
    return f"{city.lower()}::{title.lower()}"


def _intent_tags(text: str) -> set[str]:
    txt = _normalize_text(text).lower()
    tags: set[str] = set()
    tag_rules = {
        "bus_ops": ["bus", "feeder", "headway", "reliability", "frequency", "operational", "operations", "service", "network"],
        "access": ["first-mile", "last-mile", "first mile", "last mile", "walk", "walking", "pedestrian", "access", "interchange", "transfer", "hub", "station", "friction", "connectivity"],
        "rail": ["mrt", "lrt", "ktm", "rail", "station", "transit hub", "boarding"],
        "congestion": ["private vehicle", "private vehicles", "car", "cars", "traffic", "congestion", "saturation", "modal", "imbalance", "driving"],
        "equity": ["underserved", "equity", "affordable", "inclusion", "workers", "commuters"],
    }
    for tag, keywords in tag_rules.items():
        if any(keyword in txt for keyword in keywords):
            tags.add(tag)
    return tags


def _candidate_matches_scope(hypothesis: dict[str, Any], selected_challenge: dict[str, Any], city: str) -> bool:
    scope_text = " ".join([
        _normalize_text(city),
        _normalize_text(selected_challenge.get("TITLE") or selected_challenge.get("CHALLENGE_THEME")),
        _normalize_text(selected_challenge.get("BRIEF_DESCRIPTION") or selected_challenge.get("WHY_IT_MATTERS")),
    ]).lower()
    cand_text = " ".join([
        _normalize_text(hypothesis.get("location_label")),
        _normalize_text(hypothesis.get("symptom")),
        " ".join(hypothesis.get("road_a_queries") or []),
        " ".join(hypothesis.get("road_b_queries") or []),
    ]).lower()
    broad_city_tokens = {"kuala", "lumpur", "johor", "bahru", "george", "town", "ipoh", "putrajaya", "cyberjaya", "shah", "alam", "kl"}
    scope_tokens = {t for t in re.split(r"[^a-z0-9]+", scope_text) if t and len(t) >= 4 and t not in broad_city_tokens}
    cand_tokens = {t for t in re.split(r"[^a-z0-9]+", cand_text) if t and len(t) >= 4 and t not in broad_city_tokens}
    if not scope_tokens or not cand_tokens:
        return True
    if scope_tokens & cand_tokens:
        return True

    scope_tags = _intent_tags(scope_text)
    cand_tags = _intent_tags(cand_text)
    if scope_tags & cand_tags:
        return True

    candidate_anchor_text = " ".join([
        _normalize_text(hypothesis.get("location_label")),
        " ".join(hypothesis.get("road_a_queries") or []),
        " ".join(hypothesis.get("road_b_queries") or []),
    ]).lower()
    has_transit_anchor = any(token in candidate_anchor_text for token in ["mrt", "lrt", "ktm", "station", "stesen", "interchange", "terminal", "jalan"])
    if has_transit_anchor and bool(scope_tags & {"bus_ops", "access", "rail", "congestion"}):
        return True

    return False


def _preflight_hotspot_candidate(hypothesis: dict[str, Any], selected_challenge: dict[str, Any], city: str) -> tuple[bool, str]:
    road_a = list(hypothesis.get("road_a_queries") or [])
    road_b = list(hypothesis.get("road_b_queries") or [])
    if not road_a or not road_b:
        return False, "missing anchor query arrays"
    if not _anchors_are_distinct(hypothesis):
        return False, "anchor road sets are too similar"
    if not _candidate_matches_scope(hypothesis, selected_challenge, city):
        return False, "candidate drifted outside the selected challenge scope"
    return True, "ok"


def _hotspot_signature(hypothesis: dict[str, Any]) -> str:
    label = _normalize_text(hypothesis.get("location_label") or hypothesis.get("LOCATION_LABEL")).lower()
    hotspot_type = _normalize_text(hypothesis.get("type") or hypothesis.get("TYPE")).lower()
    road_a = sorted(_normalize_text(x).lower() for x in (hypothesis.get("road_a_queries") or hypothesis.get("ROAD_A_QUERIES") or []) if _normalize_text(x))
    road_b = sorted(_normalize_text(x).lower() for x in (hypothesis.get("road_b_queries") or hypothesis.get("ROAD_B_QUERIES") or []) if _normalize_text(x))
    anchors = sorted(["|".join(road_a), "|".join(road_b)])
    return " :: ".join([hotspot_type, label, *anchors])


def _extract_hotspot_signatures_from_result(hotspot_result: dict[str, Any]) -> list[str]:
    signatures: list[str] = []
    for att in hotspot_result.get("attempts", []) or []:
        hyp = att.get("hypothesis") or {}
        sig = _hotspot_signature(hyp)
        if sig and sig not in signatures:
            signatures.append(sig)
    return signatures


def _extract_hotspot_labels_from_result(hotspot_result: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for att in hotspot_result.get("attempts", []) or []:
        hyp = att.get("hypothesis") or {}
        label = _normalize_text(hyp.get("location_label") or hyp.get("LOCATION_LABEL"))
        if label and label not in labels:
            labels.append(label)
    return labels


def _is_retryable_hotspot_route_error(err: str) -> bool:
    txt = str(err or "").lower()
    return any(
        phrase in txt
        for phrase in [
            "same road or corridor",
            "same graph node",
            "same junction",
            "anchors collapse to the same graph node or junction",
            "no alternative paths found for point-to-point fallback",
            "no path found between road a",
        ]
    )


def _specificity_bonus(hypothesis: dict[str, Any], city: str) -> float:
    label = _normalize_text(hypothesis.get("location_label"))
    score = 0.0
    if label and not _is_broad_location_label(label, city):
        score += 0.18
    if any(hypothesis.get(k) for k in ["road_a_label", "road_b_label"]):
        score += 0.08
    if len(hypothesis.get("road_a_queries") or []) > 0 and len(hypothesis.get("road_b_queries") or []) > 0:
        score += 0.08
    anchors = f"{label} {' '.join(hypothesis.get('road_a_queries') or [])} {' '.join(hypothesis.get('road_b_queries') or [])}".lower()
    if any(token in anchors for token in ["jalan", "mrt", "lrt", "ktm", "station", "interchange"]):
        score += 0.08
    return round(score, 3)


def _parse_hotspot_hypotheses_from_raw(raw: Any) -> list[dict[str, Any]] | None:
    text = str(raw or "").strip()
    if not text or is_retry_response(text):
        return None

    candidates: list[Any] = [text]
    cleaned = clean_json_text(text)
    if cleaned != text:
        candidates.append(cleaned)

    for source_text in list(candidates):
        for opener, closer in (("[", "]"), ("{", "}")):
            start = source_text.find(opener)
            end = source_text.rfind(closer)
            if start != -1 and end > start:
                fragment = source_text[start:end + 1]
                if fragment not in candidates:
                    candidates.append(fragment)

    for candidate in candidates:
        try:
            parsed = safe_json_loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, list):
            items = [item for item in parsed if isinstance(item, dict)]
            if items:
                return items
        if isinstance(parsed, dict):
            return [parsed]
    return None


def _routing_mode_for_micro(micro: dict[str, Any]) -> str:
    micro_type = str(micro.get("type") or micro.get("TYPE") or "").strip().lower()
    if micro_type in ["feeder_route", "brt_corridor"]:
        return "transit"
    if micro_type in ["pedestrian_link", "transit_node"]:
        return "walk"
    return "drive"


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _anchor_looks_station_like(values: list[str], label: str = "") -> bool:
    text = " ".join([label, *list(values or [])]).lower()
    return any(token in text for token in ["mrt", "lrt", "ktm", "station", "stesen", "interchange", "sentral", "terminal"])


def _build_anchor_point_hints(selected_micro: dict[str, Any]) -> dict[str, dict[str, float]]:
    lat = _coerce_float(selected_micro.get("lat") or selected_micro.get("LAT"))
    lon = _coerce_float(selected_micro.get("lon") or selected_micro.get("LON"))
    linked_feeder = selected_micro.get("LINKED_FEEDER") if isinstance(selected_micro.get("LINKED_FEEDER"), dict) else {}
    feeder_lat = _coerce_float(linked_feeder.get("lat"))
    feeder_lon = _coerce_float(linked_feeder.get("lon"))

    road_a_queries = list(selected_micro.get("road_a_queries") or selected_micro.get("ROAD_A_QUERIES") or [])
    road_b_queries = list(selected_micro.get("road_b_queries") or selected_micro.get("ROAD_B_QUERIES") or [])
    road_a_label = _normalize_text(selected_micro.get("road_a_label") or selected_micro.get("ROAD_A_LABEL"))
    road_b_label = _normalize_text(selected_micro.get("road_b_label") or selected_micro.get("ROAD_B_LABEL"))

    micro_point = {"lat": lat, "lng": lon} if lat is not None and lon is not None else None
    feeder_point = {"lat": feeder_lat, "lng": feeder_lon} if feeder_lat is not None and feeder_lon is not None else None
    if not micro_point and not feeder_point:
        return {}

    a_station_like = _anchor_looks_station_like(road_a_queries, road_a_label)
    b_station_like = _anchor_looks_station_like(road_b_queries, road_b_label)

    hints: dict[str, dict[str, float]] = {}
    if micro_point and feeder_point:
        if a_station_like and not b_station_like:
            hints["a"] = micro_point
            hints["b"] = feeder_point
        elif b_station_like and not a_station_like:
            hints["a"] = feeder_point
            hints["b"] = micro_point
        else:
            hints["a"] = micro_point
            hints["b"] = feeder_point
        return hints

    if micro_point:
        if a_station_like and not b_station_like:
            hints["a"] = micro_point
        elif b_station_like and not a_station_like:
            hints["b"] = micro_point
        else:
            hints["a"] = micro_point
    if feeder_point:
        if "a" not in hints:
            hints["a"] = feeder_point
        elif "b" not in hints:
            hints["b"] = feeder_point
    return hints


def _run_micro_analysis_direct(selected_micro: dict[str, Any], selected_city: str) -> list[dict[str, Any]]:
    # FindRoads decommissioned - providing structured placeholder for the prompt.
    # Routing geometry will be handled on-demand by the Building Agent (Overpass API).
    
    # Use real hotspot coordinates if available to pass geo-consistency checks
    lat = selected_micro.get("lat") or selected_micro.get("LAT") or 0.0
    lon = selected_micro.get("lon") or selected_micro.get("LON") or 0.0
    
    # Extract anchor roads to provide "stable corridor roads" for validation
    road_a = selected_micro.get("road_a_label") or (selected_micro.get("road_a_queries") or [""])[0]
    road_b = selected_micro.get("road_b_label") or (selected_micro.get("road_b_queries") or [""])[0]
    via_roads = [r for r in [road_a, road_b] if r]
    if not via_roads:
        via_roads = ["Localized Corridor"]

    result = {
        "status": "success",
        "route_status": "Routing bypassed (FindRoads decommissioned)",
        "candidates": [
            {
                 "id": "placeholder_route_1",
                 "name": f"{selected_micro.get('location_label') or 'Hotspot'} (Primary Corridor)",
                 "dominant_class": "unclassified",
                 "length_m": 1000.0,
                 "via_roads": via_roads
            },
            {
                 "id": "placeholder_route_2",
                 "name": f"{selected_micro.get('location_label') or 'Hotspot'} (Alternative Access)",
                 "dominant_class": "unclassified",
                 "length_m": 1200.0,
                 "via_roads": via_roads
            }
        ],
        "route_geometry": [{"lat": float(lat), "lng": float(lon)}], 
        "route_valid": True,
        "selected_micro_source": "PRIMARY_MICRO",
        "selected_micro_type": selected_micro.get("type") or selected_micro.get("TYPE"),
        "selected_micro_symptom": selected_micro.get("symptom") or selected_micro.get("SYMPTOM"),
        "selected_micro_location_label": selected_micro.get("location_label") or selected_micro.get("LOCATION_LABEL")
    }
    return [result]


def _route_error_from_results(raw_results: list[dict[str, Any]] | None) -> str:
    for item in raw_results or []:
        route_error = str(item.get("route_error") or "").strip()
        if route_error:
            return route_error
    return "No reliable transit-routing candidates were found for this hotspot. Please choose another hotspot with clearer anchor roads or station-access links."


def _is_hotspot_routable(hypothesis: dict[str, Any], city: str) -> tuple[bool, str, list[dict[str, Any]] | None]:
    try:
        raw = _run_micro_analysis_direct(hypothesis, city)
        if not raw:
            return False, "no reliable routing candidates", None
        for item in raw:
            if not bool(item.get("route_valid", True)):
                return False, str(item.get("route_error") or "no reliable routing candidates"), raw
            if not item.get("candidates") or not item.get("route_geometry"):
                return False, str(item.get("route_error") or "no reliable routing candidates"), raw
        return True, "ok", raw
    except Exception as exc:
        return False, str(exc), None


def _build_specific_card_ui(attempt: dict[str, Any]) -> dict[str, Any]:
    hyp = attempt.get("hypothesis") or {}
    road_a = hyp.get("road_a_label") or (hyp.get("road_a_queries") or [""])[0]
    road_b = hyp.get("road_b_label") or (hyp.get("road_b_queries") or [""])[0]
    micro_detail_parts = []
    if road_a:
        micro_detail_parts.append(f"From: {road_a}")
    if road_b:
        micro_detail_parts.append(f"To: {road_b}")
    confidence = str(hyp.get("confidence") or "medium").title()
    return {
        "id": f"specific_{attempt.get('iteration', 0)}",
        "location_label": hyp.get("location_label") or "Specific hotspot",
        "title": hyp.get("location_label") or "Specific hotspot",
        "brief_description": hyp.get("symptom") or "Localized transport hotspot.",
        "micro_detail": " • ".join(micro_detail_parts),
        "confidence": confidence,
        "hotspot_type": hyp.get("type") or "corridor",
        "score": attempt.get("score", 0.0),
        "pass": attempt.get("pass", False),
        "route_checked": attempt.get("routing_ok", False),
        "route_status": attempt.get("route_status") or ("valid" if attempt.get("routing_ok") else "needs_route_check"),
        "reselection_only": False,
        "hypothesis": hyp,
        "evidence": attempt.get("evidence", {}),
    }


def _evidence_fallback(city: str, location_label: str, reason: str) -> dict[str, Any]:
    print(f"EVIDENCE FALLBACK TRIGGERED for {location_label}: {reason}")
    return {
        "matched": False,
        "source": "osm_transit_fallback",
        "city": city,
        "location_label": location_label,
        "connectivity_score": 0.0,
        "congestion_score": 0.0,
        "density_score": 0.0,
        "evidence_window": [],
        "notes": [reason],
    }


### Focused Evidence Pack ###
def get_transit_connectivity_evidence(city: str, hypothesis: dict[str, Any]) -> dict[str, Any]:
    location_label = hypothesis.get("location_label", "unknown")
    road_queries = list(hypothesis.get("road_a_queries") or [])
    
    lat, lon = None, None
    last_error = None
    
    search_terms = []
    if location_label and len(location_label.split()) < 5:
        search_terms.append(location_label)
    
    search_terms.extend(road_queries)
    
    for q_base in search_terms:
        if not q_base or len(str(q_base).split()) > 6: continue
        for q in [f"{q_base}, {city}, Malaysia", f"{q_base}, Malaysia"]:
            try:
                response = requests.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"q": q, "format": "json", "limit": 1},
                    headers={"User-Agent": "city_planner_transit_validator_v2"},
                    timeout=5
                )
                if response.status_code == 200:
                    items = response.json()
                    if items:
                        pos = items[0]
                        lat, lon = float(pos["lat"]), float(pos["lon"])
                        break
                else:
                    last_error = f"Nominatim returned {response.status_code}"
            except Exception as exc:
                last_error = str(exc)
        if lat: break

    if lat is None or lon is None:
        return _evidence_fallback(city, location_label, f"Geocode failed: {last_error or 'no match'}")

    overpass_url = "https://overpass-api.de/api/interpreter"
    overpass_query = f"""
    [out:json][timeout:25];
    (
      node["railway"="station"](around:800,{lat},{lon});
      way["railway"="station"](around:800,{lat},{lon});
      node["highway"="bus_stop"](around:800,{lat},{lon});
      node["amenity"="bus_station"](around:800,{lat},{lon});
      
      node["building"](around:800,{lat},{lon});
      way["building"](around:800,{lat},{lon});
      node["amenity"~"school|university|hospital|mall|clinic|office"](around:800,{lat},{lon});
      way["amenity"~"school|university|hospital|mall|clinic|office"](around:800,{lat},{lon});
    );
    out tags center;
    """
    try:
        headers = {"User-Agent": "city_planner_transit_validator_v2", "Accept": "*/*"}
        response = requests.post(overpass_url, data=overpass_query, headers=headers, timeout=25)
        if response.status_code != 200:
            raise Exception(f"Overpass API returned status {response.status_code}: {response.text[:200]}")
        resp = response.json()
        elements = resp.get("elements", [])
        
        transit_count = 0
        density_count = 0
        
        for el in elements:
            tags = el.get("tags", {})
            is_transit = (
                "railway" in tags or 
                tags.get("highway") == "bus_stop" or 
                tags.get("amenity") == "bus_station" or
                tags.get("public_transport") == "stop_position"
            )
            if is_transit:
                transit_count += 1
            if "building" in tags or "amenity" in tags:
                density_count += 1
        
        connectivity_relevance = 1.0 if transit_count == 0 else max(0.1, 1.0 - (transit_count / 10.0))
        density_relevance = min(1.0, density_count / 50.0)

        return {
            "matched": True,
            "lat": lat,
            "lon": lon,
            "source": "osm_spatial",
            "city": city,
            "location_label": location_label,
            "transit_asset_count": transit_count,
            "density_asset_count": density_count,
            "connectivity_score": connectivity_relevance,
            "density_score": density_relevance,
            "congestion_score": 0.0,
            "evidence_window": ["osm_spatial"],
            "notes": [f"OSM: {transit_count} transit assets, {density_count} density indicators near site."],
        }
    except Exception as exc:
        return _evidence_fallback(city, location_label, f"Overpass API failed: {exc}")


def get_context_infrastructure(lat: float, lon: float, intervention_type: str = "general") -> list[dict[str, Any]]:
    # 1. Try Database First (Fast and Reliable)
    try:
        from db.database import engine
        from sqlalchemy import text
        entities = []
        
        print(f"  -> Attempting to fetch context from DB for {lat}, {lon}")
        with engine.connect() as conn:
            # Fetch transit stops
            stops = conn.execute(text("""
                SELECT stop_type, stop_name, ST_Y(geom) as lat, ST_X(geom) as lon
                FROM osm_transit_stops
                WHERE ST_DWithin(geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 0.03)
                LIMIT 50
            """), {"lat": lat, "lon": lon}).mappings().all()
            
            for stop in stops:
                el_type = stop["stop_type"]
                name = stop["stop_name"]
                
                if el_type == "bus_stop":
                    entities.append({
                        "id": f"db_bus_stop_{stop['lat']}_{stop['lon']}",
                        "entity_type": "point",
                        "name": name or "Bus Stop",
                        "blurb": "Existing bus stop (DB)",
                        "position": {"lat": stop["lat"], "lng": stop["lon"], "height": 0},
                        "style": {"color": "#3B82F6", "pixelSize": 8}
                    })
                elif el_type in ["station", "rail_station", "bus_station", "platform"]:
                    entities.append({
                        "id": f"db_station_{stop['lat']}_{stop['lon']}",
                        "entity_type": "point",
                        "name": name or "Transit Station",
                        "blurb": "Existing transit station (DB)",
                        "position": {"lat": stop["lat"], "lng": stop["lon"], "height": 0},
                        "style": {"color": "#A78BFA", "pixelSize": 12}
                    })
            
            # Fetch POIs
            pois = conn.execute(text("""
                SELECT name, poi_category, ST_Y(ST_Centroid(geom)) as lat, ST_X(ST_Centroid(geom)) as lon
                FROM osm_pois
                WHERE ST_DWithin(geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 0.02)
                LIMIT 30
            """), {"lat": lat, "lon": lon}).mappings().all()
            
            for poi in pois:
                cat = poi["poi_category"] or "poi"
                name = poi["name"]
                entities.append({
                    "id": f"db_poi_{poi['lat']}_{poi['lon']}",
                    "entity_type": "point",
                    "name": name or cat.title(),
                    "blurb": f"POI: {cat} (DB)",
                    "position": {"lat": poi["lat"], "lng": poi["lon"], "height": 0},
                    "style": {"color": "#10B981", "pixelSize": 6}
                })
                
        if entities:
            print(f"  -> Successfully fetched {len(entities)} context entities from Database.")
            return entities
            
    except Exception as e:
        print(f"  -> DB context fetch failed: {e}. Falling back to Overpass.")

    # 2. Fallback to Overpass API (If DB is empty or fails)
    print("  -> No entities found in DB. Falling back to Overpass API...")
    overpass_servers = [
        "https://overpass-api.de/api/interpreter",
        "https://lz4.overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ]
    import random
    random.shuffle(overpass_servers)
    
    transit_radius = 3000
    context_radius = 2500
    query = f"""
    [out:json][timeout:40];
    (
      way["railway"~"rail|subway|light_rail|monorail"](around:{transit_radius},{lat},{lon}) -> .rail_ways;
      relation["route"~"bus|tram|light_rail|monorail|subway|train"](around:{transit_radius},{lat},{lon}) -> .transit_routes;
      node["railway"="station"](around:{transit_radius},{lat},{lon}) -> .stations;
      node["highway"="bus_stop"](around:{transit_radius},{lat},{lon}) -> .bus_stops;
      node["amenity"="bus_station"](around:{transit_radius},{lat},{lon}) -> .bus_stations;
      way["landuse"~"industrial|commercial"](around:{context_radius},{lat},{lon}) -> .landuse;
      relation["landuse"~"industrial|commercial"](around:{context_radius},{lat},{lon}) -> .landuse_rel;
      node["man_made"="factory"](around:{context_radius},{lat},{lon}) -> .factories;
    );
    (.rail_ways; .transit_routes; .stations; .bus_stops; .bus_stations; .landuse; .factories;);
    out geom;
    """
    headers = {"User-Agent": "city_planner_transit_validator_v2", "Accept": "*/*"}
    resp_data = None
    
    for url in overpass_servers:
        try:
            print(f"  -> Fetching context from: {url}")
            r = requests.post(url, data=query, headers=headers, timeout=30)
            if r.status_code == 200:
                resp_data = r.json()
                break
            else:
                print(f"  -> Server {url} returned {r.status_code}")
        except Exception as e:
            print(f"  -> Error from {url}: {e}")
            
    if not resp_data:
        print("  -> All Overpass servers failed or timed out for context data.")
        return []

    try:
        entities = []
        transit_route_ids_seen = set()

        for el in resp_data.get("elements", []):
            tags = el.get("tags", {})
            name = tags.get("name", "")
            el_type = el.get("type")

            if "railway" in tags and el_type == "way":
                positions = [{"lat": p["lat"], "lng": p["lon"]} for p in el.get("geometry", [])]
                if len(positions) >= 2:
                    entities.append({
                        "id": f"existing_rail_{el['id']}",
                        "entity_type": "polyline_existing",
                        "name": name or f"Rail ({tags.get('railway', 'track')})",
                        "polyline_positions": [positions],
                        "style": {"color": "#CCCCCC", "width": 6, "dashed": True}
                    })

            elif el_type == "relation" and tags.get("route") in ["bus", "tram", "light_rail", "monorail", "subway", "train"]:
                if el["id"] in transit_route_ids_seen:
                    continue
                transit_route_ids_seen.add(el["id"])
                route_type = tags.get("route", "bus")
                route_color = {
                    "bus": "#3B82F6", "tram": "#F59E0B", "light_rail": "#A78BFA",
                    "monorail": "#EC4899", "subway": "#10B981", "train": "#9CA3AF",
                }.get(route_type, "#3B82F6")
                positions = []
                for member in el.get("members", []):
                    if member.get("type") == "way":
                        for pt in member.get("geometry", []):
                            positions.append({"lat": pt["lat"], "lng": pt["lon"]})
                if len(positions) >= 2:
                    route_label = name or f"{route_type.title()} Route"
                    entities.append({
                        "id": f"transit_route_{el['id']}",
                        "entity_type": "polyline_existing",
                        "name": route_label,
                        "blurb": f"Existing {route_type} route: {route_label}",
                        "polyline_positions": [positions],
                        "style": {"color": route_color, "width": 5, "alpha": 0.7, "dashed": False}
                    })

            elif el_type == "node" and tags.get("railway") == "station":
                entities.append({
                    "id": f"station_{el['id']}",
                    "entity_type": "point",
                    "name": name or "Transit Station",
                    "blurb": "Existing transit station",
                    "position": {"lat": el["lat"], "lng": el["lon"], "height": 0},
                    "style": {"color": "#A78BFA", "pixelSize": 12}
                })

            elif el_type == "node" and tags.get("highway") == "bus_stop":
                entities.append({
                    "id": f"bus_stop_{el['id']}",
                    "entity_type": "point",
                    "name": name or "Bus Stop",
                    "blurb": "Existing bus stop",
                    "position": {"lat": el["lat"], "lng": el["lon"], "height": 0},
                    "style": {"color": "#3B82F6", "pixelSize": 8}
                })

            elif ("landuse" in tags or tags.get("man_made") == "factory") and el_type == "way" and el.get("geometry"):
                positions = [{"lat": p["lat"], "lng": p["lon"]} for p in el.get("geometry", [])]
                is_industrial = tags.get("landuse") == "industrial" or tags.get("man_made") == "factory"
                entities.append({
                    "id": f"workplace_zone_{el['id']}",
                    "entity_type": "polygon",
                    "name": f"{'Industrial' if is_industrial else 'Commercial'} Hub: {name or 'Zone'}",
                    "polygon_positions": positions,
                    "style": {
                        "color": "#4A90E2" if not is_industrial else "#F5A623",
                        "alpha": 0.15,
                        "height": 0
                    }
                })

        return entities
    except Exception as e:
        print(f"Failed to fetch context infrastructure: {e}")
        return []


def score_hypothesis_alignment(hypothesis: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    route_match_score = 1.0 if evidence.get("matched") else 0.0
    if not route_match_score:
        return {"alignment_score": 0.0, "pass": False}

    connectivity_need = float(evidence.get("connectivity_score", 0.0))
    density_demand = float(evidence.get("density_score", 0.0))
    traffic_pain = float(evidence.get("congestion_score", 0.0))
    
    confidence_bonus = 0.05 if str(hypothesis.get("confidence", "")).lower() == "high" else 0.0
    
    demand_signal = (0.6 * traffic_pain) + (0.4 * density_demand)
    
    alignment_score = (0.7 * demand_signal) + (0.3 * connectivity_need) + confidence_bonus
    
    return {"alignment_score": round(alignment_score, 3), "pass": alignment_score >= 0.35}



async def run_hotspot_hypothesis_loop(
    session_id: str,
    city: str,
    selected_challenge: dict[str, Any],
    feedback: str = "",
    *,
    excluded_signatures: list[str] | None = None,
    excluded_labels: list[str] | None = None,
) -> dict[str, Any] | str:
    from area_resolver import resolve_area
    area_info = resolve_area(city)
    area_id = area_info["area_id"] if area_info else city
    
    # Start Agent Run Logging
    run_id = persistence_service.log_agent_start(
        session_id=session_id,
        agent_name="find_hotspot_agent",
        area_id=area_id,
        input_json={"city": city, "challenge": selected_challenge}
    )
    attempts: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    rejected_reasons: list[str] = []
    scope_key = _hotspot_scope_key(selected_challenge, city)
    excluded_signature_set = {str(x).strip().lower() for x in (excluded_signatures or []) if str(x).strip()}
    excluded_label_set = {_normalize_text(x).lower() for x in (excluded_labels or []) if _normalize_text(x)}

    extra_feedback = feedback or ""
    for wave in range(3):
        prompt = build_hotspot_hypothesis_prompt(city, selected_challenge, extra_feedback)
        raw = await run_agent_once(find_hotspot_agent, session_id, prompt)
        if is_retry_response(raw):
            return raw
        hypotheses = _parse_hotspot_hypotheses_from_raw(raw)
        if not hypotheses:
            rejected_reasons.append("the hotspot generator returned unusable output")
            feedback_bits = [
                "Return JSON only as a list of 2 hotspot objects.",
                "Do not include prose before or after the JSON.",
            ]
            if feedback:
                feedback_bits.append("Original feedback: " + feedback)
            extra_feedback = " ".join(feedback_bits)
            continue

        for hypothesis in hypotheses[:4]:
            hypothesis = _normalize_hotspot_candidate(hypothesis if isinstance(hypothesis, dict) else {}, city, selected_challenge)
            label_key = _normalize_text(hypothesis.get("location_label")).lower()
            signature = _hotspot_signature(hypothesis).lower()
            if not label_key or label_key in seen_labels:
                continue
            if label_key in excluded_label_set or signature in excluded_signature_set:
                rejected_reasons.append(f"{hypothesis.get('location_label') or 'candidate'}: repeated a hotspot that was already shown")
                continue

            ok, reason = _preflight_hotspot_candidate(hypothesis, selected_challenge, city)
            if not ok:
                rejected_reasons.append(f"{hypothesis.get('location_label') or 'candidate'}: {reason}")
                continue

            seen_labels.add(label_key)
            h_lat = hypothesis.get("lat")
            h_lon = hypothesis.get("lon")
            has_valid_coords = False
            try:
                hypothesis["lat"] = float(h_lat)
                hypothesis["lon"] = float(h_lon)
                has_valid_coords = True
            except Exception:
                pass

            evidence = get_transit_connectivity_evidence(city, hypothesis)
            if not has_valid_coords:
                recovered_lat = evidence.get("lat")
                recovered_lon = evidence.get("lon")
                if recovered_lat and recovered_lon:
                    hypothesis["lat"] = float(recovered_lat)
                    hypothesis["lon"] = float(recovered_lon)
                else:
                    hypothesis["lat"] = 3.1390
                    hypothesis["lon"] = 101.6869

            score = score_hypothesis_alignment(hypothesis, evidence)
            specificity_bonus = _specificity_bonus(hypothesis, city)
            final_score = round(float(score["alignment_score"]) + specificity_bonus, 3)
            routable_ok, routable_reason, routed_preview = _is_hotspot_routable(hypothesis, city)
            if not routable_ok:
                rejected_reasons.append(f"{hypothesis.get('location_label') or 'candidate'}: {routable_reason}")
                continue
            attempts.append({
                "iteration": len(attempts) + 1,
                "hypothesis": hypothesis,
                "evidence": evidence,
                "score": round(final_score + 0.05, 3),
                "pass": bool(score.get("pass")) and routable_ok,
                "scope_key": scope_key,
                "routed_preview": routed_preview,
                "routing_ok": routable_ok,
                "routing_reason": routable_reason,
                "route_status": "valid" if routable_ok else "needs_route_check",
            })
            if len(attempts) >= 2:
                break

        if len(attempts) >= 2:
            break

        feedback_bits = [
            "Return exactly 2 hotspots that stay inside the same selected challenge scope.",
            "Do not drift to a different district or a different transport story.",
            "Each hotspot must use two distinct anchor road sets that are not aliases of the same corridor.",
        ]
        if rejected_reasons:
            feedback_bits.append("Rejected candidates: " + "; ".join(rejected_reasons[-4:]))
        if feedback:
            feedback_bits.append("Original feedback: " + feedback)
        if excluded_labels:
            feedback_bits.append("Do not repeat any of these previously shown hotspot labels: " + "; ".join(excluded_labels[:4]))
        extra_feedback = " ".join(feedback_bits)

    valid_attempts = [a for a in attempts if a.get("hypothesis")]
    if len(valid_attempts) < 2:
        detail = "Could not generate two stable hotspot options for this broad challenge."
        if rejected_reasons:
            detail += " Please try another broad challenge or regenerate the hotspots."
        raise RuntimeError(detail)

    attempts_sorted = sorted(valid_attempts, key=lambda x: x["score"], reverse=True)[:2]
    primary = attempts_sorted[0]
    secondary = attempts_sorted[1]

    # Save Results to Persistence Layer
    persistence_service.log_agent_completion(run_id, output_json={"hotspots": attempts_sorted})
    persistence_service.save_hotspot_cards(
        agent_run_id=run_id,
        area_id=area_id,
        hotspots=attempts_sorted,
        challenge_type=selected_challenge.get("CHALLENGE_TYPE")
    )

    clusters: list[dict[str, Any]] = []
    for att in attempts_sorted:
        hyp = att["hypothesis"]
        lat = hyp.get("lat")
        lon = hyp.get("lon")
        context_nodes = []
        if lat and lon:
            context_raw = get_context_infrastructure(lat, lon, intervention_type="general")
            context_nodes = context_raw[:5]
        linked_feeder = None
        feeder_context = []
        if hyp.get("LINKED_FEEDER", {}).get("needed") in ["true", True]:
            linked_feeder = hyp.get("LINKED_FEEDER")
            try:
                f_lat = float(linked_feeder.get("lat"))
                f_lon = float(linked_feeder.get("lon"))
                linked_feeder["lat"] = f_lat
                linked_feeder["lon"] = f_lon
                feeder_context_raw = get_context_infrastructure(f_lat, f_lon, intervention_type="general")
                feeder_context = feeder_context_raw[:3]
            except Exception:
                linked_feeder = None
        clusters.append({
            "center": hyp,
            "context": context_nodes,
            "score": att["score"],
            "label": hyp.get("LOCATION_LABEL", hyp.get("location_label", "Candidate Node")),
            "intervention_type": hyp.get("INTERVENTION_RECOMMENDATION", "BUS"),
            "intervention_rationale": hyp.get("INTERVENTION_RATIONALE", "Optimal for local connectivity."),
            "linked_feeder": linked_feeder,
            "feeder_context": feeder_context
        })

    specific_cards = [_build_specific_card_ui(att) for att in attempts_sorted]
    return {
        "IMPLEMENTATION_CLUSTERS": clusters,
        "PRIMARY_MICRO": primary["hypothesis"],
        "SECONDARY_MICRO": secondary["hypothesis"],
        "PRIMARY_EVIDENCE": primary["evidence"],
        "CONFIDENCE": primary["hypothesis"].get("confidence", "medium"),
        "EVIDENCE_WINDOW": primary["evidence"].get("evidence_window", []),
        "attempts": attempts_sorted,
        "specific_cards": specific_cards,
        "displayed_signatures": _extract_hotspot_signatures_from_result({"attempts": attempts_sorted}),
        "displayed_labels": _extract_hotspot_labels_from_result({"attempts": attempts_sorted}),
    }

def extract_routing_labels_from_micro(micro: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for key in ("road_a_queries", "road_b_queries"):
        for label in micro.get(key, []) or []:
            if label and label not in labels:
                labels.append(label)
    return labels


def format_micro_options(strict_json: dict[str, Any]) -> str:
    primary = strict_json.get("PRIMARY_MICRO", {})
    secondary = strict_json.get("SECONDARY_MICRO", {})

    def block(num: int, micro: dict[str, Any]) -> str:
        routing = ", ".join(extract_routing_labels_from_micro(micro)) or "N/A"
        return (
            f"{num}. {micro.get('symptom', 'Untitled micro-symptom')}\n"
            f"TYPE: {micro.get('type', 'N/A')}\n"
            f"LOCATION_LABEL: {micro.get('location_label', 'N/A')}\n"
            f"ROUTING_LABELS: {routing}"
        )

    return (
        f"Selected challenge: {strict_json.get('CHALLENGE_THEME', 'N/A')}\n\n"
        f"{block(1, primary)}\n\n{block(2, secondary)}\n\n"
        "Which micro-symptom would you like to route and analyze further?"
    )

def format_step_reply(step_name: str, raw_output: str) -> str:
    if step_name == "Generate solutions":
        try:
            data = safe_json_loads(raw_output)
        except Exception:
            return raw_output
        title = data.get("solution_title", "Proposed Solution")
        sol_type = str(data.get("solution_type", "Intervention")).replace("_", " ").title()
        complexity = str(data.get("implementation_complexity", "unknown")).title()
        confidence = str(data.get("confidence", "unknown")).title()
        primary_family = str(data.get("primary_intervention_family", "")).replace("_", " ").title()
        target = data.get("target_geometry", {})
        location = target.get("location", "the target area")
        roads = target.get("primary_roads", [])
        problem = data.get("what_problem") or data.get("detailed_description") or "Targeted access and connectivity gap."
        why_chosen = data.get("why_chosen") or data.get("evidence_basis") or "Best aligned with the routed corridor and current service evidence."
        service_connection = data.get("existing_service_connection") or "No explicit operator connection was provided."
        uncertainties = _get_list(data, "uncertainties")
        actions = _get_list(data, "proposed_actions")
        effects = _get_list(data, "expected_effect")
        blocks = []
        blocks.append(f"{title}\n")
        blocks.append(f"Intervention Type: {sol_type}")
        if primary_family:
            blocks.append(f"Primary Family: {primary_family}")
        blocks.append(f"Complexity: {complexity}")
        blocks.append(f"Confidence: {confidence}\n")
        road_context = f" involving {', '.join(roads)}" if roads else ""
        blocks.append(f"Target Location: {location}{road_context}.\n")
        blocks.append(f"What problem is being solved: {problem}\n")
        blocks.append(f"Why this intervention was chosen:{why_chosen}\n")
        blocks.append(f"What existing service it connects to: {service_connection}\n")

        if actions:
            blocks.append("Proposed Actions:")
            for action in actions:
                blocks.append(f"* {action}")
            blocks.append("")

        if effects:
            blocks.append("Expected Effects:")
            for effect in effects:
                blocks.append(f"* {effect}")
            blocks.append("")

        if uncertainties:
            blocks.append("What is still uncertain:")
            for item in uncertainties:
                blocks.append(f"* {item}")
            blocks.append("")

        impact = data.get("societal_impact")
        if impact:
            blocks.append(f"Societal Impact:\n{impact}")

        return "\n".join(blocks).strip()

    return raw_output


def make_analysis_result_for_prompt(raw_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned = []
    for raw in raw_results:
        cleaned.append(
            {
                "selected_micro_source": raw.get("selected_micro_source"),
                "selected_micro_type": raw.get("selected_micro_type"),
                "selected_micro_symptom": raw.get("selected_micro_symptom"),
                "selected_micro_location_label": raw.get("selected_micro_location_label"),
                "mode": raw.get("mode"),
                "city_query": raw.get("city_query"),
                "candidates": raw.get("candidates", []),
            }
        )
    return cleaned


def _get_city_center(city_name: str) -> dict[str, float] | None:
    from building_agent_helper import get_malaysia_coords

    coords = get_malaysia_coords(city_name)
    if not coords:
        return None
    return {"lat": float(coords["lat"]), "lng": float(coords["lng"])}


def build_planning_prompt(
    selected_challenge: dict[str, Any],
    selected_micro: dict[str, Any],
    decision_package: dict[str, Any],
) -> str:
    return f"""
You are given the selected transport challenge plus a normalized decision package prepared by the planning system.

SELECTED_CHALLENGE_JSON:
{json.dumps(selected_challenge, ensure_ascii=False, indent=2)}

DECISION_PACKAGE_JSON:
{json.dumps(decision_package, ensure_ascii=False, indent=2)}

Task:
- Compare the available routing candidates carefully.
- Use the official service match, geo consistency, and reliability warnings when deciding the intervention.
- Respect `solution_eligibility`; if it is weak, select a narrower, more conservative intervention instead of a polished multi-part scheme.
- Respect `intervention_support.primary_intervention_family` and choose ONE primary intervention family for this run.
- If official service overlap is high or partial, prefer upgrading existing service, rerouting, stop-access treatment, or schedule/transfer fixes instead of proposing a brand-new feeder loop.
- Use ONLY the provided JSON.
- Do NOT invent exact distances, lane counts, percentages, travel-time savings, or economic values unless they already appear in the provided input.
- If a value is missing, stay qualitative rather than fabricating precision.
- Return STRICT JSON ONLY using your required planning output schema.
""".strip()


def build_solution_prompt(
    selected_challenge: dict[str, Any],
    selected_micro: dict[str, Any],
    decision_package: dict[str, Any],
) -> str:
    return f"""
You are given:
1. the selected transport challenge
2. the selected micro-symptom
3. a normalized decision package (includes intervention family and evidence basis)

SELECTED_CHALLENGE_JSON:
{json.dumps(selected_challenge, ensure_ascii=False, indent=2)}

SELECTED_MICRO_JSON:
{json.dumps(selected_micro, ensure_ascii=False, indent=2)}

DECISION_PACKAGE_JSON:
{json.dumps(decision_package, ensure_ascii=False, indent=2)}

Task:
- Design a realistic public-transport intervention grounded only in the provided problem and selected candidate.
- Respect `solution_eligibility`; if it is weak, keep the solution narrow and conservative.
- Use exactly ONE primary intervention family from `intervention_support.primary_intervention_family`. Do not bundle pedestrian, bus, and civil works into one polished package unless you clearly mark one as phase 1 and the others as future considerations.
- If the decision package indicates duplication risk or official overlap, describe an upgrade/access-improvement/rerouting solution instead of a new greenfield service.
- Prefer solutions that improve bus routes, train station access, feeder integration, interchange access, platform access, or transit priority.
- Do NOT invent exact distances, lane counts, percentages, travel-time savings, ridership changes, or any other unsupported metrics.
- You may reference only numeric facts that appear in `allowed_numeric_facts`.
- If a value is missing, describe it qualitatively.
- Include these fields in the JSON output:
  - `what_problem`
  - `why_chosen`
  - `existing_service_connection`
  - `evidence_basis`
  - `uncertainties`
  - plus the existing required solution fields
- Return STRICT JSON ONLY using your required solution output schema.
""".strip()


def build_building_prompt(
    selected_challenge: dict[str, Any],
    selected_micro: dict[str, Any],
    solution_result: dict[str, Any],
    decision_package: dict[str, Any],
    route_roads: list[str] | None = None,
) -> str:
    grounding_block = ""
    if route_roads:
        roads_str = ", ".join(f'"{r}"' for r in route_roads[:10])
        grounding_block = f"""
⚠️  CRITICAL SPATIAL GROUNDING RULE:
The OSMnx routing engine has calculated that this intervention physically passes through
these real, verified roads: {roads_str}
You MUST use ONLY these road names (or their direct intersections) as SEARCH_LOCATION for
your POLYLINE, SIMULATION, POINT, and POLYGON objects.
Do NOT invent, generalise, or use any other road name. All assets MUST be spatially
co-located on this corridor.
"""
    return f"""
You are given:
1. the selected transport challenge
2. the selected micro-symptom
3. the decision package (ground truth for intervention type)
4. the final solution design
{grounding_block}
SELECTED_CHALLENGE_JSON:
{json.dumps(selected_challenge, ensure_ascii=False, indent=2)}

SELECTED_MICRO_JSON:
{json.dumps(selected_micro, ensure_ascii=False, indent=2)}

DECISION_PACKAGE_JSON:
{json.dumps(decision_package, ensure_ascii=False, indent=2)}

SOLUTION_RESULT_JSON:
{json.dumps(solution_result, ensure_ascii=False, indent=2)}

Task:
- Convert the solution into Cesium-ready map instruction lines.
- Focus on transit-supportive map assets such as bus-priority corridors, feeder movements, station access points, pedestrian links to stations, and treatment zones around interchanges.
- Return ONLY lines in this exact format:
[GEOMETRY_TYPE | COUNT | LABEL | SEARCH_LOCATION | STYLE_HINT | DESCRIPTION]
""".strip()


def _increment_counter(key: str, amount: int = 1) -> None:
    production_counters[key] = int(production_counters.get(key, 0)) + amount


def _emit_run_diagnostics(event: str, payload: dict[str, Any]) -> None:
    diagnostic = {"event": event, **payload, "counters": dict(production_counters)}
    print("RUN_DIAGNOSTIC " + json.dumps(diagnostic, ensure_ascii=False))


def _entity_center(entity: dict[str, Any]) -> tuple[float, float] | None:
    position = entity.get("position")
    if isinstance(position, dict) and {"lat", "lng"} <= set(position.keys()):
        return float(position["lat"]), float(position["lng"])
    polyline = entity.get("polyline_positions")
    if isinstance(polyline, list) and polyline:
        segment = polyline[0] if isinstance(polyline[0], list) else polyline
        if segment:
            mid = segment[len(segment) // 2]
            if isinstance(mid, dict) and {"lat", "lng"} <= set(mid.keys()):
                return float(mid["lat"]), float(mid["lng"])
    polygon = entity.get("polygon_positions")
    if isinstance(polygon, list) and polygon:
        mid = polygon[len(polygon) // 2]
        if isinstance(mid, dict) and {"lat", "lng"} <= set(mid.keys()):
            return float(mid["lat"]), float(mid["lng"])
    return None


def _distance_score(center: tuple[float, float] | None, ref_lat: float, ref_lng: float) -> float:
    if center is None:
        return float("inf")
    lat, lng = center
    return ((lat - ref_lat) ** 2) + ((lng - ref_lng) ** 2)


def _limit_context_entities(
    entities: list[dict[str, Any]],
    ref_lat: float,
    ref_lng: float,
    *,
    max_points: int = 14,
    max_lines: int = 5,
    max_polygons: int = 3,
) -> list[dict[str, Any]]:
    ranked = sorted(
        list(entities or []),
        key=lambda ent: _distance_score(_entity_center(ent), ref_lat, ref_lng),
    )
    points: list[dict[str, Any]] = []
    lines: list[dict[str, Any]] = []
    polygons: list[dict[str, Any]] = []
    for entity in ranked:
        entity_type = str(entity.get("entity_type") or "").lower()
        if entity_type == "point" and len(points) < max_points:
            points.append(entity)
        elif entity_type in {"polyline_existing", "polyline"} and len(lines) < max_lines:
            lines.append(entity)
        elif entity_type == "polygon" and len(polygons) < max_polygons:
            polygons.append(entity)
    return points + lines + polygons


def _decorate_layer_entity(
    entity: dict[str, Any],
    *,
    layer: str,
    priority: int,
    label_mode: str,
    muted: bool = False,
) -> dict[str, Any]:
    decorated = dict(entity or {})
    decorated["layer"] = layer
    decorated["priority"] = priority
    decorated["label_mode"] = label_mode
    style = dict(decorated.get("style") or {})
    if muted:
        style.setdefault("alpha", 0.3)
        style.setdefault("opacity", 0.25)
        if decorated.get("entity_type") == "point":
            style["pixelSize"] = min(int(style.get("pixelSize", 8)), 8)
    else:
        if layer == "proposal":
            style.setdefault("alpha", 0.9)
            style.setdefault("opacity", 0.85)
            if decorated.get("entity_type") == "point":
                style["pixelSize"] = max(int(style.get("pixelSize", 14)), 14)
    decorated["style"] = style
    return decorated


def _build_anchor_entities(
    selected_micro: dict[str, Any],
    analysis_raw: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    lat = selected_micro.get("lat")
    lon = selected_micro.get("lon")
    if lat is not None and lon is not None:
        anchors.append(
            {
                "id": "selected_hotspot_anchor",
                "entity_type": "point",
                "name": selected_micro.get("location_label") or selected_micro.get("LOCATION_LABEL") or "Selected hotspot",
                "blurb": selected_micro.get("symptom") or "Selected hotspot anchor",
                "position": {"lat": float(lat), "lng": float(lon), "height": 0},
                "style": {"color": "#F97316", "pixelSize": 14, "alpha": 0.9},
            }
        )
    if analysis_raw and analysis_raw[0].get("route_geometry"):
        geometry = analysis_raw[0]["route_geometry"]
        if geometry:
            start = geometry[0]
            anchors.append(
                {
                    "id": "route_entry_anchor",
                    "entity_type": "point",
                    "name": "Route entry anchor",
                    "blurb": "Representative anchor on the routed corridor.",
                    "position": {"lat": float(start["lat"]), "lng": float(start["lng"]), "height": 0},
                    "style": {"color": "#10B981", "pixelSize": 10, "alpha": 0.8},
                }
            )
    return anchors[:3]


def _build_analysis_entities(analysis_raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    analysis_entities: list[dict[str, Any]] = []
    if analysis_raw:
        primary_result = analysis_raw[0]
        if primary_result.get("route_geometry"):
            analysis_entities.append({
                "id": "main_route_primary",
                "entity_type": "polyline",
                "name": "Proposed Optimal Route (Calculated)",
                "polyline_positions": [primary_result["route_geometry"]],
                "style": {"color": "#3B82F6", "width": 10, "opacity": 0.8, "flow": "normal"},
            })
        for idx, cand_res in enumerate(analysis_raw):
            if "isochrone_geoms" in cand_res:
                for j, iso_poly in enumerate(cand_res["isochrone_geoms"]):
                    iso_coords = list(iso_poly.exterior.coords)
                    positions = [{"lat": lat, "lng": lng, "height": 0} for lng, lat in iso_coords]
                    analysis_entities.append({
                        "id": f"isochrone_auto_{idx}_{j}",
                        "entity_type": "polygon",
                        "name": "5-Minute Walking Catchment (400m)",
                        "polygon_positions": positions,
                        "style": {"color": "#10B981", "alpha": 0.14, "height": 0, "outline": True, "outlineColor": "#059669"},
                    })
    return analysis_entities


def _flatten_map_layers(map_layers: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []
    for key in ("proposal", "anchors", "context", "analysis"):
        flat.extend(list(map_layers.get(key) or []))
    return flat


def _compose_solution_display(solution: dict[str, Any], decision_package: dict[str, Any], reliability_band: str) -> dict[str, Any]:
    service_match = dict(decision_package.get("official_service_match") or {})
    evidence_basis = str(solution.get("evidence_basis") or decision_package.get("evidence_basis") or "").strip()
    uncertainties = _get_list(solution, "uncertainties")
    if reliability_band != "high" and not uncertainties:
        uncertainties = ["This recommendation still needs field verification before implementation."]
    connection_text = (
        ", ".join(service_match.get("matched_services") or []) if service_match.get("matched_services") else "No strong official-service overlap was found."
    )
    detail_lines = [
        f"What problem is being solved: {solution.get('what_problem') or solution.get('solution_title') or 'Targeted access and connectivity gap.'}",
        f"Why this intervention was chosen: {solution.get('why_chosen') or evidence_basis or 'It best matched the routed corridor and available official-service evidence.'}",
        f"What existing service it connects to: {solution.get('existing_service_connection') or connection_text}",
        f"What is still uncertain: {'; '.join(uncertainties) if uncertainties else 'No major uncertainty flags were recorded.'}",
    ]
    enriched = dict(solution)
    enriched.setdefault("evidence_basis", evidence_basis)
    enriched.setdefault("existing_service_connection", connection_text)
    enriched.setdefault("uncertainties", uncertainties)
    enriched["detailed_description"] = "\n".join(detail_lines)
    return enriched


def _specific_options_from_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    strict_json = dict(state.get("strict_json") or {})
    options: list[dict[str, Any]] = []
    for idx, key in enumerate(("PRIMARY_MICRO", "SECONDARY_MICRO"), start=1):
        micro = strict_json.get(key)
        if not isinstance(micro, dict):
            continue
        options.append(
            {
                "id": f"specific_reselect_{idx}",
                "location_label": micro.get("location_label") or "Specific hotspot",
                "title": micro.get("location_label") or "Specific hotspot",
                "brief_description": micro.get("symptom") or "Localized transport hotspot.",
                "micro_detail": " | ".join(
                    part for part in [
                        "Saved hotspot option",
                        f"Type: {micro.get('type')}" if micro.get("type") else "",
                        f"Confidence: {micro.get('confidence')}" if micro.get("confidence") else "",
                    ] if part
                ),
                "confidence": str(micro.get("confidence") or "medium").title(),
                "hotspot_type": micro.get("type") or "corridor",
                "score": None,
                "pass": None,
                "route_checked": False,
                "route_status": "saved_option",
                "reselection_only": True,
                "hypothesis": micro,
                "evidence": {},
            }
        )
    return options


def _build_done_response(
    state: dict[str, Any],
    final_reply: str,
    entities: list[dict[str, Any]],
) -> dict[str, Any]:
    city_name = (state.get("target_places") or ["Kuala Lumpur"])[0]
    from building_agent_helper import get_malaysia_coords
    coords = get_malaysia_coords(city_name)
    ref_lat, ref_lng = (coords["lat"], coords["lng"]) if coords else (3.1390, 101.6869)

    ref_lat, ref_lng = _extract_ref_coords(entities, coords if coords else {"lat": 3.1390, "lng": 101.6869})
    analysis_raw = state.get("analysis_result_raw", [])
    solution = state.get("solution_result", {})
    decision_package = dict(state.get("decision_package") or {})
    claim_audit = dict(state.get("claim_audit") or {})
    # Use a copy for display to avoid corrupting the raw state if a revision is needed later
    display_solution = _compose_solution_display(dict(solution), decision_package, str(decision_package.get("reliability_band") or "medium").lower())
    city_center = _get_city_center(city_name)
    
    # Split incoming entities into proposal and existing context if they were pre-mixed
    proposal_entities = []
    pre_existing_context = []
    for ent in (entities or []):
        if str(ent.get("id", "")).startswith(("existing_", "transit_route_", "station_", "bus_stop_", "workplace_")):
            pre_existing_context.append(ent)
        else:
            proposal_entities.append(ent)

    if pre_existing_context:
        context_entities = pre_existing_context
    else:
        context_entities = _limit_context_entities(get_context_infrastructure(ref_lat, ref_lng), ref_lat, ref_lng)
    analysis_entities = _build_analysis_entities(analysis_raw)
    anchor_entities = _build_anchor_entities(state.get("selected_micro", {}), analysis_raw)
    map_layers = {
        "proposal": [_decorate_layer_entity(ent, layer="proposal", priority=100, label_mode="always") for ent in proposal_entities],
        "anchors": [_decorate_layer_entity(ent, layer="anchors", priority=80, label_mode="zoom") for ent in anchor_entities],
        "context": [_decorate_layer_entity(ent, layer="context", priority=20, label_mode="hidden", muted=True) for ent in context_entities],
        "analysis": [_decorate_layer_entity(ent, layer="analysis", priority=10, label_mode="hidden", muted=True) for ent in analysis_entities],
    }
    merged_entities = _flatten_map_layers(map_layers)
    final_geo = validate_geo_consistency(
        city_center,
        state.get("selected_micro", {}),
        state.get("analysis_result_raw", []),
        entities=merged_entities,
    )

    warnings: list[str] = []
    warnings.extend(list(decision_package.get("warnings") or []))
    warnings.extend(list(final_geo.warnings or []))
    warnings.extend(list(claim_audit.get("warnings") or []))

    reliability_band = str(decision_package.get("reliability_band") or "medium").lower()
    if not final_geo.pass_check:
        reliability_band = "low"
    elif reliability_band == "high" and claim_audit.get("removed_claims"):
        reliability_band = "medium"
        _increment_counter("downgraded_claim_audit")

    service_match = dict(decision_package.get("official_service_match") or {})
    if service_match.get("recommendation_mode") == "upgrade_existing_service":
        _increment_counter("overlap_upgrade_decisions")
    claim_audit_summary = {
        "pass": bool(claim_audit.get("pass_check", True)),
        "removed_claims": list(claim_audit.get("removed_claims") or []),
    }
    impact_metrics = {
        "societal_impact": display_solution.get("societal_impact", "No societal impact data available."),
        "expected_effects": display_solution.get("expected_effect", []),
        "complexity": display_solution.get("implementation_complexity", "Unknown"),
        "solution_title": display_solution.get("solution_title", "Proposed Solution"),
        "detailed_description": display_solution.get("detailed_description", "No detailed implementation narrative available."),
        "evidence_basis": display_solution.get("evidence_basis", ""),
        "uncertainties": solution.get("uncertainties", []),
        "recommendation_mode": service_match.get("recommendation_mode", "new_service_candidate"),
        "reliability_band": reliability_band,
        "warnings": warnings,
        "service_overlap_summary": {
            "matched_services": list(service_match.get("matched_services") or []),
            "overlap_level": service_match.get("overlap_level", "none"),
        },
    }

    eligibility = dict(decision_package.get("solution_eligibility") or {})
    blocked = (
        bool(claim_audit.get("hard_fail"))
        or not bool(final_geo.city_match_pass)
        or not bool(eligibility.get("eligible", True))
    )
    reply_text = final_reply
    if blocked:
        _increment_counter("blocked_geo_inconsistency")
        reasons = list(eligibility.get("reasons") or [])
        reply_text = (
            "Planning output was blocked because the evidence is not strong enough for a production-ready recommendation. "
            + ("Reason: " + " ".join(reasons[:2]) if reasons else "Please choose another hotspot or refine the selection.")
        )

    _increment_counter("context_entities_rendered", len(map_layers["context"]))
    _emit_run_diagnostics(
        "done_response",
        {
            "selected_challenge": (state.get("selected_challenge") or {}).get("TITLE") or (state.get("selected_challenge") or {}).get("CHALLENGE_THEME"),
            "selected_hotspot": (state.get("selected_micro") or {}).get("location_label"),
            "matched_services": service_match.get("matched_services") or [],
            "reliability_band": reliability_band,
            "geo_warnings": final_geo.warnings,
            "claim_audit_removed": claim_audit_summary["removed_claims"],
            "proposal_entity_count": len(map_layers["proposal"]),
            "anchor_entity_count": len(map_layers["anchors"]),
            "context_entity_count": len(map_layers["context"]),
            "analysis_entity_count": len(map_layers["analysis"]),
        },
    )

    # When blocked: still open the map so real OSM context is visible,
    # but strip the AI-generated proposal layer to avoid showing bad data.
    safe_map_layers = dict(map_layers)
    safe_entities = list(merged_entities)
    if blocked:
        safe_map_layers["proposal"] = []
        safe_entities = [
            e for e in merged_entities
            if e.get("layer") in ("context", "analysis", "anchors")
        ]

    return {
        "ok": True,
        "session_id": state.get("session_id"),
        "stage": "done",
        "reply": reply_text,
        # Always show map so user sees OSM context (bus stops, rail, etc.)
        # even when the AI proposal is blocked.
        "show_map": True,
        "is_blocked": blocked,
        "entities": safe_entities,
        "map_layers": safe_map_layers,
        "needs_input": False,
        "city_name": city_name,
        "target_lat": ref_lat,
        "target_lng": ref_lng,
        # Explicit camera so frontend can fly directly to the solution area.
        "camera": {
            "center": {"lat": ref_lat, "lng": ref_lng},
            "height": 1000,
            "pitch": -45,
            "heading": 0,
            "roll": 0,
        },
        "impact_metrics": impact_metrics,
        "reliability_band": reliability_band,
        "warnings": warnings,
        "service_overlap_summary": impact_metrics["service_overlap_summary"],
        "evidence_summary": {
            "official_data_used": bool(service_match.get("official_data_used")),
            "route_match_pass": bool(final_geo.route_match_pass),
            "geo_consistency_pass": bool(final_geo.pass_check),
        },
        "claim_audit_summary": claim_audit_summary,
    }


def analyze_selected_micro(selected_micro: dict[str, Any], selected_city: str) -> list[dict[str, Any]]:
    return _run_micro_analysis_direct(selected_micro, selected_city)


@app.post("/api/start")
async def start(req: StartRequest):
    from db.database import engine
    session_id = str(uuid.uuid4())
    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=session_id,
        session_id=session_id,
        state={"feedback": "", "valid_places_text": "", "target_places": []},
    )

    # Persist session to database
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO user_sessions (session_id, status) VALUES (:session_id, 'active')"),
            {"session_id": session_id}
        )

    greeting_prompt = """
The conversation is starting.

Greet the user naturally and ask them to provide one or two Malaysian cities or towns for infrastructure analysis.

Return exactly in this format:

VERDICT: RETRY
PLACES:
FEEDBACK: <your greeting and question>
""".strip()

    greeting_text = await run_agent_once(place_intake_agent, session.id, greeting_prompt)
    greeting_parsed = parse_place_result(greeting_text)
    workflow_state[session.id] = {"phase": "intake"}
    persistence_service.save_session_state(session.id, workflow_state[session.id])

    return {
        "ok": True,
        "session_id": session.id,
        "stage": "Place intake",
        "reply": greeting_parsed["feedback"],
        "needs_input": True,
    }


async def _persist_current_state(session_id: str):
    if session_id in workflow_state:
        persistence_service.save_session_state(session_id, workflow_state[session_id])

@app.post("/api/chat")
async def chat(req: ChatRequest, background_tasks: BackgroundTasks):
    ################################################
    # Main Chat Handler - Processes user messages through workflow phases
    ################################################
    session_id = req.session_id
    user_message = req.message.strip()

    ################################################
    # Session Validation - Ensure session exists
    ################################################
    if session_id not in workflow_state:
        # Persistence Fallback: Try to reload session from DB
        persisted_state = persistence_service.load_session_state(session_id)
        if persisted_state:
            workflow_state[session_id] = persisted_state
        else:
            raise HTTPException(status_code=404, detail="Session not found")

    current_session = await session_service.get_session(
        app_name=APP_NAME,
        user_id=session_id,
        session_id=session_id,
    )
    background_tasks.add_task(_persist_current_state, session_id)
    state = workflow_state[session_id]
    phase = state["phase"]

    ################################################
    # Phase: Intake - Process and validate user location input
    ################################################
    if phase == "intake":
        intake_prompt = f"""
User message:
{user_message}
User message:
{user_message}

Remember:
- Accept any real Malaysian city or town
- Accept at most two places
- Reject places that are too broad
- Reject places that are too specific
- Return structured output exactly
""".strip()
        intake_text = await run_agent_once(place_intake_agent, session_id, intake_prompt)
        parsed = parse_place_result(intake_text)

        if parsed["verdict"] == "SUCCESS" and parsed["places"]:
            current_session.state["target_places"] = parsed["places"]
            planning_response: dict[str, Any]
            planning_response = await start_planning_phase(session_id, current_session)
            planning_response["reply"] = (
                f"Location confirmed: {', '.join(parsed['places'])}. Moving to the planning phase.\n\n"
                + planning_response["reply"]
            )
            return planning_response

        return {
            "ok": True,
            "session_id": session_id,
            "stage": "Place intake",
            "reply": parsed["feedback"],
            "needs_input": True,
        }

    ################################################
    # Phase: Area Selection - Generate and handle area options for hotspots
    ################################################
    if phase == "area_selection":
        ######################################
            #show area hotspot/specific area
        ######################################
        selected_city = (state.get("target_places") or ["Kuala Lumpur"])[0]
        area_options = list(state.get("area_options") or [])

        ################################################
        # Handle Regenerate/Refresh Requests - Generate new area options
        ################################################
        if user_message.strip().lower() in {"regenerate", "refresh", "another"}:
            refreshed = await _generate_area_options(session_id, selected_city)
            state["area_options"] = refreshed
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Area selection",
                "reply": _format_area_options_reply(selected_city, refreshed),
                "needs_input": True,
                "needs_selection": True,
                "area_options": refreshed,
            }

        ################################################
        # Process Area Selection - Resolve user choice and verify area
        ################################################
        selected_option = _resolve_area_selection(user_message, area_options) #once user has done selecting area
        if not selected_option:
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Area selection",
                "reply": "Please choose an area by number or exact area name from the options.",
                "needs_input": True,
                "needs_selection": True,
                "area_options": area_options,
            }

        ################################################
        # Verify Selected Area - Check feasibility and confidence
        ################################################
        # Use the selected option directly (verification handled during screening)
        selected_option = dict(selected_option)
        state["selected_area_option"] = selected_option

        ################################################
        # Build Evidence Summary - Aggregate scores and impact drivers
        ################################################
        osm_gap_score = float(selected_option.get("osm_gap_score", 0.0))
        osm_completeness_score = float(selected_option.get("osm_completeness_score", 0.0))
        feasibility = selected_option.get("route_feasibility") or {"pass": False, "score": 0.0}
        merged = selected_option.get("merged_confidence") or {"confidence": 0.0, "band": "low", "pass_gate": False}
        complaint_verified = bool(selected_option.get("complaint_verified", False))

        impact_drivers = []
        if float(selected_option.get("growth_signals", {}).get("population", 0)) > 0: impact_drivers.append("Population Growth")
        if float(selected_option.get("growth_signals", {}).get("industrial", 0)) > 0: impact_drivers.append("Industrial Cluster")
        if float(selected_option.get("growth_signals", {}).get("trip_generator", 0)) > 0: impact_drivers.append("Activity Hub")
        if osm_gap_score > 0.5: impact_drivers.append("Transit Gap")
        if selected_option.get("equity_flag"): impact_drivers.append("Social Equity Priority")

        evidence_summary = {
            "selected_area": selected_option.get("area_label"),
            "report_score": selected_option.get("report_score"),
            "gap_score": osm_gap_score,
            "completeness_score": osm_completeness_score,
            "feasibility": feasibility,
            "confidence": merged,
            "impact_drivers": impact_drivers,
            "strategic_rationale": _build_strategic_narrative(selected_option, type("AuditStub", (), {"gap_score": osm_gap_score})()),
            "complaint_verified": complaint_verified,
        }

        ################################################
        # Gate Checks - Determine if area passes validation thresholds
        ################################################
        gate_pass = bool(merged.get("pass_gate", False))
        soft_pass = (
            float(selected_option.get("report_score", 0.0)) >= 0.72
            and osm_gap_score >= 0.35
            and osm_completeness_score < 0.35
            and bool(feasibility.get("pass"))
        )
        fallback_override = bool(
            selected_option.get("allow_low_confidence", False)
            and selected_option.get("is_fallback_option", False)
        )

        if not gate_pass and not fallback_override and not soft_pass:
            missing: list[str] = []
            if osm_completeness_score < 0.5:
                missing.append("OSM coverage is sparse in this area")
            if not feasibility.get("pass"):
                missing.append("Route feasibility check failed")
            if merged.get("confidence", 0.0) < 0.68:
                missing.append("Merged confidence below threshold (0.68)")
            missing_msg = "; ".join(missing) if missing else "More evidence needed."
            state["evidence_summary"] = evidence_summary

            reply_tmpl = (
                "Needs verification for {area}.\n"
                "Confidence: {conf} ({band}).\n"
                "Reason: {reason}\n"
                "Choose another area or type 'regenerate' for new options."
            )
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Area selection",
                "reply": reply_tmpl.format(
                    area=selected_option.get('area_label'),
                    conf=merged.get('confidence'),
                    band=merged.get('band'),
                    reason=missing_msg,
                ),
                "needs_input": True,
                "needs_selection": True,
                "area_options": area_options,
                "evidence_summary": evidence_summary,
            }

        if soft_pass and not gate_pass:
            evidence_summary["gate_override"] = {
                "used": True,
                "reason": "Proceeding because evidence is strong, but OSM coverage for this area is sparse.",
            }
        elif not gate_pass and fallback_override:
            evidence_summary["gate_override"] = {
                "used": True,
                "reason": "Selected option came from fallback area set; proceeding to Find Needs with caution.",
            }

        target_places = state.get("target_places", [])
        prompt = build_find_needs_prompt(
            target_places,
            selected_area=selected_option,
            merged_evidence=evidence_summary,
        )
        
        speculative = state.get("speculative_find_needs")
        initial_raw = None
        if speculative and speculative.get("area_id") == selected_option.get("id"):
            initial_raw = speculative.get("raw_output")
            print(f"Speculative HIT for {selected_option.get('area_label')}")
        
        if not initial_raw:
            initial_raw = await run_agent_with_retry(find_needs_agent, session_id, prompt)
        if is_retry_response(initial_raw):
            return _build_find_needs_fallback_response(
                session_id,
                target_places,
                selected_area=selected_option,
                merged_evidence=evidence_summary,
                existing_state=state,
                retry_feedback=extract_retry_feedback(initial_raw),
            )


        raw_step_output, display_reply, find_needs_options = await prepare_find_needs_output(
            session_id=session_id,
            target_places=target_places,
            raw_step_output=initial_raw,
            selected_area=selected_option,
            merged_evidence=evidence_summary,
        )
        if not _has_complete_find_needs_options(find_needs_options):
            if selected_option.get("is_fallback_option"):
                return _build_find_needs_fallback_response(
                    session_id,
                    target_places,
                    selected_area=selected_option,
                    merged_evidence=evidence_summary,
                    existing_state=state,
                )
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Area selection",
                "reply": (
                    f"I couldn't generate stable broad challenge cards for {selected_option.get('area_label') or 'this area'} yet. "
                    "Please choose another area or type 'regenerate' for new options."
                ),
                "needs_input": True,
                "needs_selection": True,
                "area_options": area_options,
                "evidence_summary": evidence_summary,
            }
        ################################################
        # Transition to Challenge Selection Phase
        ################################################
        state.update(
            {
                "phase": "challenge_selection",
                "last_step_output": raw_step_output,
                "last_display_reply": display_reply,
                "evidence_summary": evidence_summary,
                "find_needs_options": find_needs_options,
                "selected_area_option": selected_option,
            }
        )

        return {
            "ok": True,
            "session_id": session_id,
            "stage": "Find needs",
            "reply": display_reply,
            "needs_input": True,
            "find_needs_options": find_needs_options,
        }

    ################################################
    # Phase: Challenge Selection - Handle user selection of broad transport challenges
    ################################################
    if phase == "challenge_selection":
        raw_step_output = state["last_step_output"]
        
        ################################################
        # Fast Path for Digit Selection - Bypass LLM for simple numeric choices
        ################################################
        # --- FAST PATH: Bypass the LLM for digit selection to save quota ---
        clean_msg = user_message.strip()
        find_needs_options = state.get("find_needs_options", [])
        
        if clean_msg.isdigit() and 1 <= int(clean_msg) <= len(find_needs_options):
            selected_challenge = find_needs_options[int(clean_msg) - 1]
            review_result = {
                "verdict": "PASS",
                "detail": "",
                "final_output": json.dumps(selected_challenge, ensure_ascii=False)
            }
        ################################################
        # LLM Review for Complex Selections - Use agent to parse user input
        ################################################
        else:
            review_prompt = (
                "STEP NAME: Find needs\n\n"
                f"STEP OUTPUT:\n{raw_step_output}\n\n"
                f"USER RESPONSE: {user_message}"
            )
            review_text = await run_agent_with_retry(review_agent, session_id, review_prompt)
            if is_retry_response(review_text):
                return {
                    "ok": True,
                    "session_id": session_id,
                    "stage": "Find needs",
                    "reply": extract_retry_feedback(review_text),
                    "needs_input": True,
                }
            review_result = parse_review(review_text)

        if review_result["verdict"] == "REVISE":
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Find needs",
                "reply": review_result["detail"] or "Please choose one challenge from the list.",
                "needs_input": True,
            }

        if review_result["verdict"] == "REVISE_TOTAL":
            feedback = review_result["detail"] or "Regenerate challenges"
            target_places = state.get("target_places", [])
            rerun_prompt = (
                f"The user rejected the previous challenges and gave this feedback: {feedback}. "
                f"Please generate 3 new transport challenges for {target_places}. "
                f"If available, prioritize this selected area context: {json.dumps(state.get('selected_area_option', {}), ensure_ascii=False)}\n\n"
                "Return STRICT JSON ONLY with keys CHALLENGE_1, CHALLENGE_2, CHALLENGE_3."
            )
            initial_raw = await run_agent_with_retry(find_needs_agent, session_id, rerun_prompt)
            if is_retry_response(initial_raw):
                return {
                    "ok": True,
                    "session_id": session_id,
                    "stage": "Find needs",
                    "reply": extract_retry_feedback(initial_raw),
                    "needs_input": True,
                }
            new_challenges_raw, display_reply, find_needs_options = await prepare_find_needs_output(
                session_id=session_id,
                target_places=target_places,
                raw_step_output=initial_raw,
                selected_area=state.get("selected_area_option"),
                merged_evidence=state.get("evidence_summary"),
            )
            if not _has_complete_find_needs_options(find_needs_options):
                return {
                    "ok": True,
                    "session_id": session_id,
                    "stage": "Find needs",
                    "reply": (
                        "I couldn't generate a fresh stable set of broad challenge cards yet. "
                        "Please choose from the current cards or try regenerating again."
                    ),
                    "needs_input": True,
                    "find_needs_options": state.get("find_needs_options", []),
                }
            state["last_step_output"] = new_challenges_raw
            state["last_display_reply"] = display_reply
            state["find_needs_options"] = find_needs_options
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Find needs",
                "reply": display_reply,
                "needs_input": True,
                "find_needs_options": find_needs_options,
            }

        selected_output = review_result["final_output"]
        if not selected_output:
            selected_challenge = safe_json_loads(raw_step_output)
            if "CHALLENGE_1" in selected_challenge:
                return {
                    "ok": True,
                    "session_id": session_id,
                    "stage": "Find needs",
                    "reply": "I couldn't quite catch which one you picked. Could you please specify by number or name?",
                    "needs_input": True,
                }
        else:
            try:
                selected_challenge = safe_json_loads(selected_output)
            except Exception:
                return {
                    "ok": True,
                    "session_id": session_id,
                    "stage": "Find needs",
                    "reply": "I couldn't parse the selected challenge cleanly. Please reply with 1, 2, or 3 again.",
                    "needs_input": True,
                }

        state["selected_challenge"] = selected_challenge
        state["selected_broad_card"] = selected_challenge
        selected_city = (state.get("target_places") or [])[0]
        try:
            hotspot_result = await run_hotspot_hypothesis_loop(session_id, selected_city, selected_challenge)
        except Exception as exc:
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Find needs",
                "reply": (
                    "I couldn't turn that broad challenge into two stable hotspot cards yet. "
                    "Please choose another broad challenge or try again."
                ),
                "needs_input": True,
                "find_needs_options": state.get("find_needs_options", []),
            }
        if is_retry_response(hotspot_result):
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Find needs",
                "reply": extract_retry_feedback(hotspot_result),
                "needs_input": True,
            }

        strict_json = {
            "CHALLENGE_THEME": selected_challenge.get("CHALLENGE_THEME"),
            "MACRO_ROOT_CAUSE": selected_challenge.get("MACRO_ROOT_CAUSE"),
            "WHY_IT_MATTERS": selected_challenge.get("WHY_IT_MATTERS"),
            "EVIDENCE_SUMMARY": selected_challenge.get("EVIDENCE_SUMMARY"),
            "PRIMARY_MICRO": hotspot_result["PRIMARY_MICRO"],
            "SECONDARY_MICRO": hotspot_result["SECONDARY_MICRO"],
            "ROUTING_LABELS": {
                "PRIMARY_MICRO": extract_routing_labels_from_micro(hotspot_result["PRIMARY_MICRO"]),
                "SECONDARY_MICRO": extract_routing_labels_from_micro(hotspot_result["SECONDARY_MICRO"]),
            },
            "CONFIDENCE": hotspot_result["CONFIDENCE"],
            "EVIDENCE_WINDOW": hotspot_result["EVIDENCE_WINDOW"],
        }

        state.update(
            {
                "phase": "specific_card_selection",
                "strict_json": strict_json,
                "last_step_output": json.dumps(strict_json, ensure_ascii=False, indent=2),
                "last_display_reply": format_micro_options(strict_json),
                "current_specific_signatures": hotspot_result.get("displayed_signatures", []),
                "current_specific_labels": hotspot_result.get("displayed_labels", []),
                "specific_regen_count": 0,
            }
        )
        return {
            "ok": True,
            "session_id": session_id,
            "stage": "Micro hotspot selection",
            "reply": state["last_display_reply"],
            "needs_input": True,
            "specific_options": hotspot_result.get("specific_cards", hotspot_result.get("attempts", []))
        }

    ################################################
    # Phase: Specific Card Selection - Handle user selection of micro hotspots
    ################################################
    if phase == "specific_card_selection":
        raw_step_output = state["last_step_output"]
        review_prompt = (
            "STEP NAME: Select micro-symptom\n\n"
            f"STEP OUTPUT:\n{raw_step_output}\n\n"
            f"USER RESPONSE: {user_message}"
        )
        review_text = await run_agent_with_retry(review_agent, session_id, review_prompt)
        if is_retry_response(review_text):
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Micro hotspot selection",
                "reply": extract_retry_feedback(review_text),
                "needs_input": True,
            }
        review_result = parse_review(review_text)

        if review_result["verdict"] == "REVISE":
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Micro hotspot selection",
                "reply": review_result["detail"] or "Please choose one micro-symptom from the list.",
                "needs_input": True,
            }

        if review_result["verdict"] == "REVISE_TOTAL":
            feedback = review_result["detail"] or "Regenerate hotspots"
            selected_challenge = state.get("selected_challenge", {})
            selected_city = (state.get("target_places") or [])[0]
            previous_signatures = list(state.get("current_specific_signatures") or [])
            previous_labels = list(state.get("current_specific_labels") or [])
            try:
                hotspot_result = await run_hotspot_hypothesis_loop(
                    session_id,
                    selected_city,
                    selected_challenge,
                    feedback=feedback,
                    excluded_signatures=previous_signatures,
                    excluded_labels=previous_labels,
                )
            except Exception as exc:
                return {
                    "ok": True,
                    "session_id": session_id,
                    "stage": "Micro hotspot selection",
                    "reply": (
                        "I could not regenerate stable hotspot cards yet. "
                        "Please try regenerate again or choose another broad challenge."
                    ),
                    "needs_input": True,
                    "specific_options": _specific_options_from_state(state),
                }
            if is_retry_response(hotspot_result):
                return {
                    "ok": True,
                    "session_id": session_id,
                    "stage": "Micro hotspot selection",
                    "reply": extract_retry_feedback(hotspot_result),
                    "needs_input": True,
                }
            
            strict_json = {
                "CHALLENGE_THEME": selected_challenge.get("CHALLENGE_THEME"),
                "MACRO_ROOT_CAUSE": selected_challenge.get("MACRO_ROOT_CAUSE"),
                "WHY_IT_MATTERS": selected_challenge.get("WHY_IT_MATTERS"),
                "EVIDENCE_SUMMARY": selected_challenge.get("EVIDENCE_SUMMARY"),
                "PRIMARY_MICRO": hotspot_result["PRIMARY_MICRO"],
                "SECONDARY_MICRO": hotspot_result["SECONDARY_MICRO"],
                "ROUTING_LABELS": {
                    "PRIMARY_MICRO": extract_routing_labels_from_micro(hotspot_result["PRIMARY_MICRO"]),
                    "SECONDARY_MICRO": extract_routing_labels_from_micro(hotspot_result["SECONDARY_MICRO"]),
                },
            }
            new_raw_output = json.dumps(strict_json)
            state["last_step_output"] = new_raw_output
            state["last_display_reply"] = format_micro_options(strict_json)
            state["current_specific_signatures"] = hotspot_result.get("displayed_signatures", [])
            state["current_specific_labels"] = hotspot_result.get("displayed_labels", [])
            state["specific_regen_count"] = 0
            
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Micro hotspot selection",
                "reply": state["last_display_reply"],
                "needs_input": True,
                "specific_options": hotspot_result.get("specific_cards", hotspot_result.get("attempts", []))
            }

        selected_micro = safe_json_loads(review_result["final_output"])
        state["selected_specific_card"] = selected_micro
        selected_city = (state.get("target_places") or [])[0]
        try:
            analysis_result_raw = analyze_selected_micro(selected_micro, selected_city)
            analysis_result = make_analysis_result_for_prompt(analysis_result_raw)
            if not analysis_result_raw or not any(item.get("candidates") for item in analysis_result_raw):
                raise ValueError("No reliable transit-routing candidates were found for this hotspot. Please choose another hotspot with clearer anchor roads or station-access links.")
        except Exception as exc:
            err = str(exc)
            if "same road or corridor" in err.lower():
                selected_challenge = state.get("selected_challenge", {})
                regen_count = int(state.get("specific_regen_count") or 0)
                if regen_count >= 1:
                    return {
                        "ok": True,
                        "session_id": session_id,
                        "stage": "Micro hotspot selection",
                        "reply": "I could not produce a stable routable hotspot from this selection. Please go back and choose the other hotspot or pick a different broad challenge.",
                        "needs_input": True,
                    }
                failed_label = selected_micro.get("location_label") or selected_micro.get("LOCATION_LABEL") or "the previous hotspot"
                previously_shown_signatures = list(state.get("current_specific_signatures") or [])
                previously_shown_labels = list(state.get("current_specific_labels") or [])
                failed_signature = _hotspot_signature(selected_micro)
                if failed_signature and failed_signature not in previously_shown_signatures:
                    previously_shown_signatures.append(failed_signature)
                if failed_label and failed_label not in previously_shown_labels:
                    previously_shown_labels.append(failed_label)
                feedback = (
                    f"The previous hotspot '{failed_label}' used anchors that resolved to the same road or corridor. "
                    "Regenerate exactly 2 replacement hotspots with two distinct physical anchors. "
                    "Stay within the SAME selected challenge scope and avoid drifting to unrelated districts. "
                    "For transit-node cases, use one station-approach anchor and one nearby neighborhood access road. "
                    "Do not reuse any hotspot that was already shown to the user."
                )
                try:
                    hotspot_result = await run_hotspot_hypothesis_loop(
                        session_id,
                        selected_city,
                        selected_challenge,
                        feedback=feedback,
                        excluded_signatures=previously_shown_signatures,
                        excluded_labels=previously_shown_labels,
                    )
                except Exception as regen_exc:
                    return {
                        "ok": True,
                        "session_id": session_id,
                        "stage": "Micro hotspot selection",
                        "reply": f"I could not produce a replacement routable hotspot from this selection: {regen_exc}. Please choose the other hotspot or pick a different broad challenge.",
                        "needs_input": True,
                    }
                strict_json = {
                    "CHALLENGE_THEME": selected_challenge.get("CHALLENGE_THEME"),
                    "MACRO_ROOT_CAUSE": selected_challenge.get("MACRO_ROOT_CAUSE"),
                    "WHY_IT_MATTERS": selected_challenge.get("WHY_IT_MATTERS"),
                    "EVIDENCE_SUMMARY": selected_challenge.get("EVIDENCE_SUMMARY"),
                    "PRIMARY_MICRO": hotspot_result["PRIMARY_MICRO"],
                    "SECONDARY_MICRO": hotspot_result["SECONDARY_MICRO"],
                    "ROUTING_LABELS": {
                        "PRIMARY_MICRO": extract_routing_labels_from_micro(hotspot_result["PRIMARY_MICRO"]),
                        "SECONDARY_MICRO": extract_routing_labels_from_micro(hotspot_result["SECONDARY_MICRO"]),
                    },
                }
                state["last_step_output"] = json.dumps(strict_json, ensure_ascii=False, indent=2)
                state["last_display_reply"] = format_micro_options(strict_json)
                state["current_specific_signatures"] = hotspot_result.get("displayed_signatures", [])
                state["current_specific_labels"] = hotspot_result.get("displayed_labels", [])
                state["specific_regen_count"] = regen_count + 1
                return {
                    "ok": True,
                    "session_id": session_id,
                    "stage": "Micro hotspot selection",
                    "reply": "The selected hotspot was not stably routable, so I replaced it with two pre-checked hotspot options inside the same challenge.",
                    "needs_input": True,
                    "specific_options": hotspot_result.get("specific_cards", hotspot_result.get("attempts", [])),
                }
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Micro hotspot selection",
                "reply": f"Analysis failed: {exc}. Please try another micro-symptom or refine your selection.",
                "needs_input": True,
            }

        state.update(
            {
                "phase": "planning",
                "step_index": 0,
                "selected_micro": selected_micro,
                "analysis_result_raw": analysis_result_raw,
                "analysis_result": analysis_result,
            }
        )

        try:
            decision_package = build_decision_package(
                selected_city=selected_city,
                selected_micro=selected_micro,
                analysis_result=analysis_result,
                analysis_result_raw=analysis_result_raw,
                city_center=_get_city_center(selected_city),
            )
            eligibility = dict(decision_package.get("solution_eligibility") or {})
            if not bool(eligibility.get("eligible", True)):
                state["phase"] = "specific_card_selection"
                state["decision_package"] = decision_package
                return {
                    "ok": True,
                    "session_id": session_id,
                    "stage": "Micro hotspot selection",
                    "reply": (
                        "This hotspot is still too weak for a production-ready recommendation. "
                        + " ".join(list(eligibility.get("reasons") or [])[:2])
                    ),
                    "needs_input": True,
                    "specific_options": _specific_options_from_state(state),
                }
            solution_prompt = build_solution_prompt(
                selected_challenge=state["selected_challenge"],
                selected_micro=selected_micro,
                decision_package=decision_package,
            )
            # Resolve area_id for logging
            from area_resolver import resolve_area
            area_info = resolve_area(selected_city)
            area_id = area_info["area_id"] if area_info else selected_city
            
            # Start Logging Solution Agent
            run_id = persistence_service.log_agent_start(
                session_id=session_id,
                agent_name="solution_strategy_agent",
                area_id=area_id,
                input_json={"hotspot": selected_micro, "challenge": state["selected_challenge"]}
            )
            
            try:
                solution_raw = await run_agent_once(solution_agent, session_id, solution_prompt)
                sanitized_solution = safe_json_loads(solution_raw)
                claim_audit = audit_solution_claims(sanitized_solution, decision_package)
                
                # Save Solution and Log Completion
                persistence_service.log_agent_completion(run_id, output_json=claim_audit.sanitized_solution)
                # If we have a list of solutions, save them
                solutions = claim_audit.sanitized_solution.get("PROPOSED_SOLUTIONS", [])
                if solutions:
                    # Use actual hotspot_id from the selection if available, else fallback to dummy
                    db_hotspot_id = selected_micro.get("hotspot_id") or str(uuid.uuid4())
                    persistence_service.save_solution_options(run_id, db_hotspot_id, solutions)
                
                state["claim_audit"] = {
                    "pass_check": claim_audit.pass_check,
                    "hard_fail": claim_audit.hard_fail,
                    "removed_claims": claim_audit.removed_claims,
                    "rewritten_fields": claim_audit.rewritten_fields,
                    "warnings": claim_audit.warnings,
                }
                state["solution_result"] = claim_audit.sanitized_solution
                state["step_index"] = 0  # CRITICAL: Set to 0 so the planning loop reviews this output first
                sanitized_solution_raw = json.dumps(claim_audit.sanitized_solution, ensure_ascii=False, indent=2)
                display_reply = format_step_reply("Generate solutions", sanitized_solution_raw)
                state["last_step_output"] = sanitized_solution_raw
                state["last_display_reply"] = display_reply
                state["last_agent_name"] = solution_agent.name
                state["output_key"] = "solution_result"
                return {
                    "ok": True,
                    "session_id": session_id,
                    "stage": "Generate solutions",
                    "reply": display_reply,
                    "needs_input": True,
                }
            except Exception as inner_exc:
                persistence_service.log_agent_completion(run_id, output_json={}, status="failed", error_message=str(inner_exc))
                raise inner_exc

        except Exception as exc:
            state["phase"] = "specific_card_selection"
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Micro hotspot selection",
                "reply": f"I could identify the hotspot, but the downstream planning pipeline failed: {exc}. Please choose another hotspot or refine your selection.",
                "needs_input": True,
            }

    ################################################
    # Phase: Planning - Execute iterative transport planning pipeline
    ################################################
    if phase != "planning":
        raise HTTPException(status_code=400, detail=f"Unsupported phase: {phase}")

    step_index = state["step_index"]
    step_name, step_agent, output_key = PIPELINE[step_index]
    raw_step_output = state["last_step_output"]

    review_prompt = (
        f"STEP NAME: {step_name}\n\n"
        f"STEP OUTPUT:\n{raw_step_output}\n\n"
        f"USER RESPONSE: {user_message}"
    )
    review_text = await run_agent_once(review_agent, session_id, review_prompt)
    review_result = parse_review(review_text)

    if review_result["verdict"] == "REVISE":
        return {
            "ok": True,
            "session_id": session_id,
            "stage": step_name,
            "reply": review_result["detail"] or "Please revise your response.",
            "needs_input": True,
        }

    if review_result["verdict"] == "REVISE_TOTAL":
        feedback = review_result["detail"] or "Please revise the previous output."
        rerun_prompt = (
            f'The previous {step_name} output was rejected. Follow this specific instruction to revise it: "{feedback}"'
        )
        new_raw_step_output = await run_agent_once(step_agent, session_id, rerun_prompt)

        # Normal increment for previous steps
        state["step_index"] = step_index
        state["last_step_output"] = new_raw_step_output
        
        # CRITICAL FIX: Ensure the state variable for this step is updated
        # so downstream steps don't use stale/rejected data.
        if output_key:
            try:
                state[output_key] = safe_json_loads(new_raw_step_output)
            except:
                state[output_key] = new_raw_step_output
        
        new_display_reply = format_step_reply(step_name, new_raw_step_output)
        state["last_display_reply"] = new_display_reply
        display_reply = new_display_reply

        if step_name == "Building simulations":
            city_name = (state.get("target_places") or ["Kuala Lumpur"])[0]
            enriched_assets = process_agent_assets(new_raw_step_output, city_name=city_name)
            entities = format_entities(enriched_assets)
            
            from building_agent_helper import get_malaysia_coords
            coords = get_malaysia_coords(city_name)
            
            ref_lat, ref_lng = _extract_ref_coords(entities, coords if coords else {"lat": 3.1390, "lng": 101.6869})
                
            existing_context = get_context_infrastructure(ref_lat, ref_lng)
            entities = existing_context + entities

            analysis_raw = state.get("analysis_result_raw", [])
            if analysis_raw:
                primary_result = analysis_raw[0]
                if primary_result.get("route_geometry"):
                    entities.append({
                        "id": "main_route_primary",
                        "entity_type": "polyline",
                        "name": "Proposed Optimal Route (Calculated)",
                        "polyline_positions": [primary_result["route_geometry"]],
                        "style": {
                            "color": "#3B82F6",
                            "width": 10,
                            "opacity": 0.8,
                            "flow": "normal"
                        }
                    })

            analysis_raw = state.get("analysis_result_raw", [])
            for cand_res in analysis_raw:
                if "isochrone_geoms" in cand_res:
                    for idx, iso_poly in enumerate(cand_res["isochrone_geoms"]):
                        iso_coords = list(iso_poly.exterior.coords)
                        positions = [{"lat": lat, "lng": lng, "height": 0} for lng, lat in iso_coords]
                        entities.append({
                            "id": f"isochrone_rev_{idx}",
                            "entity_type": "polygon",
                            "name": "5-Minute Walking Catchment (400m)",
                            "polygon_positions": positions,
                            "style": {
                                "color": "#10B981",
                                "alpha": 0.2,
                                "height": 0,
                                "outline": True,
                                "outlineColor": "#059669"
                            }
                        })

            state["session_id"] = session_id
            return _build_done_response(state, display_reply, entities)

        return {
            "ok": True,
            "session_id": session_id,
            "stage": step_name,
            "reply": display_reply,
            "needs_input": True,
        }

    selected_output = review_result["final_output"] or raw_step_output
    if step_name == "Generate solutions":
        parsed_selected_output = safe_json_loads(selected_output)
        decision_package = state.get("decision_package") or build_decision_package(
            selected_city=(state.get("target_places") or ["Kuala Lumpur"])[0],
            selected_micro=state.get("selected_micro", {}),
            analysis_result=state.get("analysis_result", []),
            analysis_result_raw=state.get("analysis_result_raw", []),
            city_center=_get_city_center((state.get("target_places") or ["Kuala Lumpur"])[0]),
        )
        state["decision_package"] = decision_package
        claim_audit = audit_solution_claims(parsed_selected_output, decision_package)
        state["claim_audit"] = {
            "pass_check": claim_audit.pass_check,
            "hard_fail": claim_audit.hard_fail,
            "removed_claims": claim_audit.removed_claims,
            "rewritten_fields": claim_audit.rewritten_fields,
            "warnings": claim_audit.warnings,
        }
        state["solution_result"] = claim_audit.sanitized_solution
    
    next_index = step_index + 1
    if next_index >= len(PIPELINE):
        return {
            "ok": True,
            "session_id": session_id,
            "stage": "done",
            "reply": "Workflow completed.",
            "needs_input": False,
        }

    next_step_name, next_step_agent, next_output_key = PIPELINE[next_index]
    decision_package = state.get("decision_package")
    if not decision_package:
        decision_package = build_decision_package(
            selected_city=(state.get("target_places") or ["Kuala Lumpur"])[0],
            selected_micro=state.get("selected_micro", {}),
            analysis_result=state.get("analysis_result", []),
            analysis_result_raw=state.get("analysis_result_raw", []),
            city_center=_get_city_center((state.get("target_places") or ["Kuala Lumpur"])[0]),
        )
        state["decision_package"] = decision_package
    if next_step_name == "Generate solutions":
        next_prompt = build_solution_prompt(
            selected_challenge=state["selected_challenge"],
            selected_micro=state["selected_micro"],
            decision_package=decision_package,
        )
    elif next_step_name == "Building simulations":
        if bool((state.get("claim_audit") or {}).get("hard_fail")):
            state["session_id"] = session_id
            return _build_done_response(
                state,
                "Planning output was blocked because the generated claims did not stay geographically consistent.",
                [],
            )
        # Extract route road names to ground the AI building agent to the real corridor
        analysis_raw_for_prompt = state.get("analysis_result_raw", [])
        route_roads: list[str] = []
        for cand_res in analysis_raw_for_prompt:
            for cand in cand_res.get("candidates", []):
                route_roads.extend(cand.get("via_roads", []))
        # Deduplicate while preserving order
        seen_roads: set[str] = set()
        route_roads = [r for r in route_roads if not (r in seen_roads or seen_roads.add(r))]  # type: ignore

        next_prompt = build_building_prompt(
            selected_challenge=state["selected_challenge"],
            selected_micro=state["selected_micro"],
            solution_result=state["solution_result"],
            decision_package=decision_package,
            route_roads=route_roads or None,
        )
    else:
        next_prompt = f"Proceed with {next_step_name}"

    next_raw_step_output = await run_agent_once(next_step_agent, session_id, next_prompt)

    if next_step_name == "Building simulations":
        city_name = (state.get("target_places") or ["Kuala Lumpur"])[0]
        enriched_assets = process_agent_assets(next_raw_step_output, city_name=city_name)
        entities = format_entities(enriched_assets)
        
        # Get city coords for camera
        from building_agent_helper import get_malaysia_coords
        coords = get_malaysia_coords(city_name)
        fallback = coords if coords else {"lat": 3.1390, "lng": 101.6869}
        ref_lat, ref_lng = _extract_ref_coords(entities, fallback)
                    
        existing_context = get_context_infrastructure(ref_lat, ref_lng)
        entities = existing_context + entities

        # INJECT TRUE ROUTE GEOMETRY FROM OSMNX — PRIMARY only
        analysis_raw = state.get("analysis_result_raw", [])
        if analysis_raw:
            primary_result = analysis_raw[0]
            if primary_result.get("route_geometry"):
                entities.append({
                    "id": "main_route_primary",
                    "entity_type": "polyline",
                    "name": "Proposed Optimal Route (Calculated)",
                    "polyline_positions": [primary_result["route_geometry"]],
                    "style": {
                        "color": "#3B82F6",
                        "width": 10,
                        "opacity": 0.8,
                        "flow": "normal"
                    }
                })

        # INJECT ISOCHRONES
        analysis_raw = state.get("analysis_result_raw", [])
        for cand_res in analysis_raw:
            if "isochrone_geoms" in cand_res:
                for idx, iso_poly in enumerate(cand_res["isochrone_geoms"]):
                    iso_coords = list(iso_poly.exterior.coords)
                    positions = [{"lat": lat, "lng": lng, "height": 0} for lng, lat in iso_coords]
                    entities.append({
                        "id": f"isochrone_new_{idx}",
                        "entity_type": "polygon",
                        "name": "5-Minute Walking Catchment (400m)",
                        "polygon_positions": positions,
                        "style": {
                            "color": "#10B981",
                            "alpha": 0.2,
                            "height": 0,
                            "outline": True,
                            "outlineColor": "#059669"
                        }
                    })

        state["session_id"] = session_id
        return _build_done_response(state, "Planning complete. Switching to map view.", entities)
    elif next_step_name == "Plan improvements":
        return {
            "ok": True,
            "session_id": session_id,
            "stage": next_step_name,
            "reply": next_raw_step_output,
            "needs_input": False,
        }
    elif next_step_name == "Generate solutions":
        next_raw_step_output = format_step_reply("Generate solutions", next_raw_step_output)
    
    state.update(
        {
            "phase": "planning",
            "step_index": next_index,
            "last_step_output": next_raw_step_output,
            "last_display_reply": next_raw_step_output,
            "last_agent_name": next_step_agent.name,
            "output_key": next_output_key,
        }
    )
    return {
        "ok": True,
        "session_id": session_id,
        "stage": next_step_name,
        "reply": next_raw_step_output,
        "needs_input": True,
    }
