import asyncio
import os
import sys
import unittest

# Add backend to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Import app but avoid running top-level code if possible, 
# though app.py has a lot of it. We'll rely on the fact that we're running it as a script.
try:
    from app import _run_micro_analysis_direct, _get_city_center
    from reliability import build_decision_package
except ImportError:
    # Fallback for different execution contexts
    sys.path.append(os.getcwd())
    from backend.app import _run_micro_analysis_direct, _get_city_center
    from backend.reliability import build_decision_package

class ReliabilityFixTests(unittest.TestCase):
    def test_placeholder_eligibility(self):
        # This test verifies that the placeholder returned when FindRoads is decommissioned
        # still allows the hotspot to be considered "eligible" for solutions.
        
        selected_micro = {
            "location_label": "Wangsa Maju Test Site",
            "lat": 3.205,
            "lon": 101.732,
            "road_a_label": "Jalan Genting Kelang",
            "road_b_label": "Jalan 1/27A",
            "symptom": "High traffic",
            "type": "corridor"
        }
        selected_city = "Kuala Lumpur"
        
        # 1. Run the direct analysis (the fixed function)
        analysis_result_raw = _run_micro_analysis_direct(selected_micro, selected_city)
        
        # Verify placeholder content
        self.assertEqual(len(analysis_result_raw), 1)
        res = analysis_result_raw[0]
        # Check that geometry is NOT [0,0]
        self.assertEqual(res["route_geometry"], [{"lat": 3.205, "lng": 101.732}])
        # Check that via_roads is NOT empty
        self.assertIn("Jalan Genting Kelang", res["candidates"][0]["via_roads"])
        self.assertIn("Jalan 1/27A", res["candidates"][0]["via_roads"])
        
        # 2. Build decision package
        decision_package = build_decision_package(
            selected_city=selected_city,
            selected_micro=selected_micro,
            analysis_result=[{"summary": "Mock"}],
            analysis_result_raw=analysis_result_raw,
            city_center=_get_city_center(selected_city),
        )
        
        # 3. Check eligibility
        eligibility = decision_package.get("solution_eligibility", {})
        self.assertTrue(eligibility.get("eligible"), f"Hotspot should be eligible. Reasons: {eligibility.get('reasons')}")
        self.assertEqual(len(eligibility.get("reasons", [])), 0)

if __name__ == "__main__":
    unittest.main()
