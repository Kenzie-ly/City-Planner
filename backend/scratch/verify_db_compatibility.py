import sys
import os
import uuid
import json

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from persistence_service import (
    log_agent_start, 
    log_agent_completion, 
    save_broad_challenge_cards, 
    save_hotspot_cards, 
    save_solution_options
)
from db.database import engine
from sqlalchemy import text

def verify_db_compatibility():
    print("--- Verifying Database Compatibility ---")
    
    # Use a dummy session and area
    session_id = str(uuid.uuid4())
    area_id = "shah_alam"
    
    # 0. Ensure user_sessions entry exists for FK constraints
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO user_sessions (session_id, status) VALUES (:sess_id, 'active') ON CONFLICT DO NOTHING"),
                {"sess_id": session_id}
            )
        print("✓ Session entry verified.")
    except Exception as e:
        print(f"✗ Failed to create session entry: {e}")
        return

    # 1. Test log_agent_start
    try:
        run_id = log_agent_start(
            session_id=session_id,
            agent_name="test_agent",
            area_id=area_id,
            input_json={"test": "input"}
        )
        print(f"✓ log_agent_start works. Run ID: {run_id}")
    except Exception as e:
        print(f"✗ log_agent_start FAILED: {e}")
        return

    # 2. Test save_broad_challenge_cards
    try:
        challenges = [{
            "CHALLENGE_TYPE": "transit_desert",
            "TITLE": "Worker Commute Access",
            "BRIEF_DESCRIPTION": "Test description",
            "CONFIDENCE_LEVEL": "high",
            "SOURCES": [{"publisher": "Test"}]
        }]
        save_broad_challenge_cards(run_id, area_id, challenges)
        print("✓ save_broad_challenge_cards works.")
    except Exception as e:
        print(f"✗ save_broad_challenge_cards FAILED: {e}")

    # 3. Test save_hotspot_cards
    try:
        hotspots = [{
            "location_label": "Section 13 KTM",
            "type": "transit_node",
            "confidence": "high",
            "symptom": "Test symptom",
            "road_a_queries": ["Road A"],
            "road_b_queries": ["Road B"],
            "selected_challenge_type": "transit_desert"
        }]
        save_hotspot_cards(run_id, area_id, hotspots)
        print("✓ save_hotspot_cards works.")
    except Exception as e:
        print(f"✗ save_hotspot_cards FAILED: {e}")

    # 4. Test log_agent_completion
    try:
        log_agent_completion(run_id, output_json={"test": "output"})
        print("✓ log_agent_completion works.")
    except Exception as e:
        print(f"✗ log_agent_completion FAILED: {e}")

    print("\nCompatibility verification complete.")

if __name__ == "__main__":
    verify_db_compatibility()
