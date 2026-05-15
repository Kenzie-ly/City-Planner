import asyncio
import json
import uuid
import sys
import os
from unittest.mock import AsyncMock, patch, MagicMock

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

import app
import persistence_service

async def simulate_hotspot_loop():
    print("--- Simulating run_hotspot_hypothesis_loop ---")
    
    session_id = str(uuid.uuid4())
    city = "Kuala Lumpur"
    selected_challenge = {
        "CHALLENGE_TYPE": "transit_desert",
        "TITLE": "Worker Commute Access"
    }
    
    # Mock Agent Response (LLM Output)
    mock_llm_response = json.dumps([
        {
            "location_label": "Section 13 Shah Alam",
            "lat": 3.0738,
            "lon": 101.5183,
            "type": "industrial_zone",
            "symptom": "High employee density, low bus frequency",
            "road_a_queries": ["Persiaran Kerjaya"],
            "road_b_queries": ["Jalan Kontraktor"],
            "confidence": "high"
        },
        {
            "location_label": "Glenmarie LRT Area",
            "lat": 3.0921,
            "lon": 101.5794,
            "type": "transit_node",
            "symptom": "Last-mile gap from station to offices",
            "road_a_queries": ["Jalan Kerjaya"],
            "road_b_queries": ["Jalan Pengaturcara"],
            "confidence": "medium"
        }
    ])

    # Mock DB Engine to prevent real connection errors
    mock_engine = MagicMock()
    
    # We use patches to bypass real agent calls and real DB writes
    with patch("app.run_agent_once", AsyncMock(return_value=mock_llm_response)), \
         patch("persistence_service.log_agent_start", MagicMock(return_value="mock_run_id")), \
         patch("persistence_service.log_agent_completion", MagicMock()), \
         patch("persistence_service.save_hotspot_cards", MagicMock()), \
         patch("area_resolver.resolve_area", MagicMock(return_value={"area_id": "kuala_lumpur"})), \
         patch("app._is_hotspot_routable", MagicMock(return_value=(True, "ok", {}))), \
         patch("db.database.engine", mock_engine):
        
        print(f"Running loop for city: {city}...")
        try:
            # We don't need to actually run the loop if we just want to verify the integration,
            # but let's try to run a subset of the logic or the whole function if possible.
            result = await app.run_hotspot_hypothesis_loop(session_id, city, selected_challenge)
            
            print("\n--- LOOP RESULT ---")
            print(json.dumps(result, indent=2))
            
            # Verify Persistence calls were made
            print("\n--- VERIFYING PERSISTENCE INTEGRATION ---")
            if persistence_service.log_agent_start.called:
                print("OK: log_agent_start was called.")
            if persistence_service.save_hotspot_cards.called:
                print("OK: save_hotspot_cards was called.")
            if persistence_service.log_agent_completion.called:
                print("OK: log_agent_completion was called.")
                
        except Exception as e:
            print(f"Loop failed: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(simulate_hotspot_loop())
