import asyncio
import json
import sys
import os

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from reliability import build_decision_package

async def verify_reliability_fix():
    print("--- Verifying Reliability Logic Fix ---")
    
    from app import _run_micro_analysis_direct, _get_city_center
    
    # Mock micro hotspot
    selected_micro = {
        "location_label": "Wangsa Maju Section 2",
        "lat": 3.205,
        "lon": 101.732,
        "road_a_label": "Jalan Genting Kelang",
        "road_b_label": "Jalan 1/27A",
        "symptom": "High traffic during peak hours",
        "type": "corridor"
    }
    selected_city = "Kuala Lumpur"
    
    print("Running _run_micro_analysis_direct...")
    analysis_result_raw = _run_micro_analysis_direct(selected_micro, selected_city)
    
    print("Building decision package...")
    # We need a mock analysis_result (formatted for prompt)
    analysis_result = [{"summary": "Mock summary"}]
    
    decision_package = build_decision_package(
        selected_city=selected_city,
        selected_micro=selected_micro,
        analysis_result=analysis_result,
        analysis_result_raw=analysis_result_raw,
        city_center=_get_city_center(selected_city),
    )
    
    eligibility = decision_package.get("solution_eligibility", {})
    print("Eligible:", eligibility.get("eligible"))
    print("Reasons:", eligibility.get("reasons"))
    
    if eligibility.get("eligible"):
        print("\nSUCCESS: The hotspot is now considered eligible for recommendation!")
    else:
        print("\nFAILURE: The hotspot is still blocked by reliability checks.")

if __name__ == "__main__":
    asyncio.run(verify_reliability_fix())
