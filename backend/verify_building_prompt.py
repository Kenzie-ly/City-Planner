import json
import sys
import os

# Ensure UTF-8 output for Windows console (to handle emojis like ⚠️)
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Add current dir to path to import app
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from app import build_building_prompt, _build_done_response
    from building_agent_helper import process_agent_assets, format_entities
except ImportError as e:
    print(f"Import Error: {e}")
    sys.exit(1)

def test_building_prompt_integrity():
    # 1. Mock the data that normally lives in session state
    mock_challenge = {
        "CHALLENGE_TYPE": "transit_desert",
        "TITLE": "First-Mile Gap in Cheras"
    }
    
    mock_micro = {
        "location_label": "Taman Midah",
        "hotspot_id": "82e4835d-a38b-491d-afa3-c577c472f694", 
        "road_a_label": "Jalan Cheras",
        "road_b_label": "Jalan Midah 1"
    }
    
    mock_solution = {
        "solution_title": "Cheras-Midah Feeder Optimization",
        "proposed_actions": [
            "Add dedicated bus lane on Jalan Cheras",
            "Relocate bus stop closer to MRT Taman Midah"
        ]
    }
    
    mock_decision_package = {
        "primary_intervention_family": "bus_priority_corridor",
        "solution_eligibility": "high",
        "official_service_match": {"status": "partial_overlap"}
    }
    
    mock_route_roads = ["Jalan Cheras", "Jalan Loke Yew"]

    # 2. Generate the prompt
    print("--- GENERATING BUILDING AGENT PROMPT ---")
    prompt = build_building_prompt(
        selected_challenge=mock_challenge,
        selected_micro=mock_micro,
        solution_result=mock_solution,
        decision_package=mock_decision_package,
        route_roads=mock_route_roads
    )
    
    print(prompt)
    print("\n--- INTEGRITY CHECK ---")
    
    # 3. Verify requirements
    failed = False
    
    # Check for nulls
    if ": null" in prompt:
        print("❌ FAIL: Found 'null' values in prompt JSON blocks!")
        failed = True
    else:
        print("✅ PASS: No 'null' values found in prompt.")

    if "PLANNING_RESULT_JSON" in prompt:
        print("❌ FAIL: Old PLANNING_RESULT_JSON reference still exists!")
        failed = True
    else:
        print("✅ PASS: Deprecated PLANNING_RESULT_JSON removed.")
        
    if "DECISION_PACKAGE_JSON" in prompt and "primary_intervention_family" in prompt:
        print("✅ PASS: Decision package is present and populated.")
    else:
        print("❌ FAIL: Decision package missing or malformed.")
        
    if "SOLUTION_RESULT_JSON" in prompt:
        print("✅ PASS: Solution result is present.")
    else:
        print("❌ FAIL: Solution result missing.")
        
    if "CRITICAL SPATIAL GROUNDING RULE" in prompt:
        print("✅ PASS: Spatial grounding rules (OSM roads) are injected.")
    else:
        print("❌ FAIL: Spatial grounding missing.")

    if not failed:
        print("\n✨ PROMPT VERIFICATION SUCCESS")
    else:
        print("\n⚠️ PROMPT VERIFICATION FAILED")
    return not failed

def test_frontend_data_pipeline():
    # 1. Mock raw output from the Building LLM
    mock_llm_output = """
    Here is the map scene for the Cheras project:
    [POINT | x1 | MRT Taman Midah | Taman Midah, Cheras | color:blue, size:large | Primary Hub]
    [POLYLINE | x1 | Feeder Corridor | Jalan Cheras | color:orange, width:wide | Main Bus Route]
    [SIMULATION | x15 | Active Traffic | Jalan Cheras | speed:40 | Current Flow]
    """

    # 2. Run the processing pipeline
    print("\n--- TESTING FRONTEND DATA PIPELINE ---")
    # Using 'Kuala Lumpur' as hint for hardcoded coordinate fallbacks in helper
    enriched = process_agent_assets(mock_llm_output, city_name="Kuala Lumpur")
    entities = format_entities(enriched)

    # 3. Audit the final "Entities" JSON sent to frontend
    print(f"Processed {len(entities)} entities.")
    
    all_ok = True
    for ent in entities:
        print(f"  -> Entity: {ent['name']} ({ent['entity_type']})")
        
        # Verify mandatory keys for CesiumJS
        mandatory = ["id", "position", "style", "entity_type"]
        for key in mandatory:
            if key not in ent:
                print(f"     ❌ MISSING KEY: {key}")
                all_ok = False
            else:
                # Special check for position data
                if key == "position":
                    pos = ent[key]
                    if not pos or "lat" not in pos or "lng" not in pos:
                        print(f"     ⚠️ WARNING: Position is unpopulated/invalid (Expected for un-geocoded roads without Nominatim access)")
                    else:
                        print(f"     ✅ Position: {pos['lat']}, {pos['lng']}")
    
    if len(entities) >= 3 and all_ok:
        print("\n✨ FRONTEND VERIFICATION SUCCESS: Data is correctly structured for the map.")
    else:
        print("\n⚠️ FRONTEND VERIFICATION CAUTION: Check missing keys or empty entity list.")

def test_state_preservation():
    print("\n--- TESTING STATE PRESERVATION ---")
    
    # 1. Setup a clean session state
    original_solution = {
        "solution_title": "Original Clean JSON",
        "detailed_description": "Initial design."
    }
    mock_state = {
        "target_places": ["Kuala Lumpur"],
        "solution_result": original_solution.copy(),
        "decision_package": {"reliability_band": "high"},
        "analysis_result_raw": [],
        "selected_challenge": {"TITLE": "Test Challenge"},
        "selected_micro": {"location_label": "Test Hotspot"}
    }
    
    # 2. Call the build response (which used to mutate state)
    print("Preparing final map response...")
    try:
        # Mocking entities as empty list
        _build_done_response(mock_state, "Finished.", entities=[])
        
        # 3. Check if the original state was preserved
        current_solution = mock_state["solution_result"]
        
        # In the old code, _compose_solution_display would add markers or change the dict
        # The key check is whether it's still exactly equal to our mock original
        if current_solution == original_solution:
            print("✅ PASS: Raw session state preserved (Clean JSON remains for future revisions).")
        else:
            print("❌ FAIL: State was mutated by the display logic!")
            print(f"   Original: {original_solution}")
            print(f"   Current:  {current_solution}")
    except Exception as e:
        print(f"❌ FAIL: Exception during state preservation test: {e}")

if __name__ == "__main__":
    p_ok = test_building_prompt_integrity()
    test_frontend_data_pipeline()
    test_state_preservation()
