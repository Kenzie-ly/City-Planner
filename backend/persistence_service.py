import uuid
import json
from datetime import datetime
from sqlalchemy import text
# from db.database import engine # Defer import to runtime

def log_agent_start(session_id: str, agent_name: str, area_id: str = None, evidence_pack_id: str = None, input_json: dict = None) -> str:
    from db.database import engine
    agent_run_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO agent_runs (agent_run_id, session_id, agent_name, area_id, evidence_pack_id, input_json, status, started_at)
                VALUES (:run_id, :sess_id, :name, :area, :pack, :input, 'running', NOW())
            """),
            {
                "run_id": agent_run_id,
                "sess_id": session_id,
                "name": agent_name,
                "area": area_id,
                "pack": evidence_pack_id,
                "input": json.dumps(input_json) if input_json else None
            }
        )
    return agent_run_id

def log_agent_completion(agent_run_id: str, output_json: dict, status: str = "success", error_message: str = None):
    from db.database import engine
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE agent_runs 
                SET output_json = :output, status = :status, error_message = :error, completed_at = NOW()
                WHERE agent_run_id = :run_id
            """),
            {
                "run_id": agent_run_id,
                "output": json.dumps(output_json) if output_json else None,
                "status": status,
                "error": error_message
            }
        )

def save_broad_challenge_cards(agent_run_id: str, area_id: str, challenges: list[dict]):
    from db.database import engine
    with engine.begin() as conn:
        for idx, card in enumerate(challenges):
            # Try to find a matching problem direction if it exists
            pd_result = conn.execute(
                text("SELECT problem_direction_id FROM candidate_problem_directions WHERE area_id = :area AND challenge_type = :type LIMIT 1"),
                {"area": area_id, "type": card.get("CHALLENGE_TYPE")}
            ).fetchone()
            
            pd_id = pd_result[0] if pd_result else None
            
            conn.execute(
                text("""
                    INSERT INTO broad_challenge_cards (agent_run_id, problem_direction_id, area_id, challenge_type, title, description, confidence_tier, evidence_refs, display_order)
                    VALUES (:run_id, :pd_id, :area, :type, :title, :desc, :conf, :refs, :order)
                """),
                {
                    "run_id": agent_run_id,
                    "pd_id": pd_id,
                    "area": area_id,
                    "type": card.get("CHALLENGE_TYPE"),
                    "title": card.get("TITLE"),
                    "desc": card.get("BRIEF_DESCRIPTION"),
                    "conf": card.get("CONFIDENCE_LEVEL"),
                    "refs": json.dumps(card.get("SOURCES", [])),
                    "order": idx
                }
            )

def save_hotspot_cards(agent_run_id: str, area_id: str, hotspots: list[dict], challenge_type: str = None):
    from db.database import engine
    with engine.begin() as conn:
        for idx, card in enumerate(hotspots):
            hotspot_id = str(uuid.uuid4())
            # Use provided challenge_type or try to find it in the card
            c_type = challenge_type or card.get("selected_challenge_type") or "general_transit"
            
            # 1. Save to candidate_hotspots (the data entity)
            conn.execute(
                text("""
                    INSERT INTO candidate_hotspots (hotspot_id, area_id, challenge_type, hotspot_name, hotspot_type, score, confidence_tier, related_routes, generated_at)
                    VALUES (:h_id, :area, :type, :name, :h_type, :score, :conf, :routes, NOW())
                """),
                {
                    "h_id": hotspot_id,
                    "area": area_id,
                    "type": c_type,
                    "name": card.get("location_label") or card.get("hypothesis", {}).get("location_label"),
                    "h_type": card.get("type") or card.get("hypothesis", {}).get("type"),
                    "score": card.get("score") or 0.0,
                    "conf": card.get("confidence") or card.get("hypothesis", {}).get("confidence"),
                    "routes": json.dumps({"road_a": card.get("road_a_queries"), "road_b": card.get("road_b_queries")})
                }
            )
            # 2. Save to specific_hotspot_cards (the UI entity)
            conn.execute(
                text("""
                    INSERT INTO specific_hotspot_cards (agent_run_id, hotspot_id, area_id, challenge_type, title, description, confidence_tier, display_order)
                    VALUES (:run_id, :h_id, :area, :type, :title, :desc, :conf, :order)
                """),
                {
                    "run_id": agent_run_id,
                    "h_id": hotspot_id,
                    "area": area_id,
                    "type": c_type,
                    "title": card.get("location_label") or card.get("hypothesis", {}).get("location_label"),
                    "desc": card.get("symptom") or card.get("hypothesis", {}).get("symptom") or "Identified hotspot for intervention",
                    "conf": card.get("confidence") or card.get("hypothesis", {}).get("confidence"),
                    "order": idx
                }
            )

def save_solution_options(agent_run_id: str, hotspot_id: str, solutions: list[dict]):
    from db.database import engine
    with engine.begin() as conn:
        for idx, sol in enumerate(solutions):
            conn.execute(
                text("""
                    INSERT INTO solution_options (solution_id, hotspot_id, agent_run_id, solution_type, title, description, expected_benefit, tradeoffs, created_at)
                    VALUES (:sol_id, :h_id, :run_id, :type, :title, :desc, :benefit, :tradeoffs, NOW())
                """),
                {
                    "sol_id": str(uuid.uuid4()),
                    "h_id": hotspot_id,
                    "run_id": agent_run_id,
                    "type": sol.get("solution_type"),
                    "title": sol.get("solution_title"),
                    "desc": sol.get("detailed_description"),
                    "benefit": json.dumps(sol.get("expected_effect", [])),
                    "tradeoffs": json.dumps(sol.get("uncertainties", []))
                }
            )

def save_session_state(session_id: str, state: dict):
    from db.database import engine
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE user_sessions SET state = :state, updated_at = NOW() WHERE session_id = :session_id"),
            {"session_id": session_id, "state": json.dumps(state)}
        )

def load_session_state(session_id: str) -> dict:
    from db.database import engine
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT state FROM user_sessions WHERE session_id = :session_id"),
            {"session_id": session_id}
        ).fetchone()
        if result and result[0]:
            return result[0] if isinstance(result[0], dict) else json.loads(result[0])
    return None

