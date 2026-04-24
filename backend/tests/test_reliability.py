import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from reliability import (
    audit_solution_claims,
    build_decision_package,
    match_official_services,
    validate_geo_consistency,
)


class ReliabilityTests(unittest.TestCase):
    def test_match_official_services_detects_overlap(self):
        selected_micro = {
            "location_label": "Sri Rampai access gap",
            "road_a_queries": ["LRT Sri Rampai", "Jalan 1/27A"],
            "road_b_queries": ["Jalan Wangsa Delima", "Wangsa Maju"],
            "road_a_label": "LRT Sri Rampai",
            "road_b_label": "Jalan Wangsa Delima",
        }
        analysis_raw = [
            {
                "candidates": [
                    {
                        "candidate_id": "candidate_1",
                        "via_roads": ["Jalan Wangsa Delima", "Jalan 1/27A"],
                        "total_length_m": 1850.0,
                    }
                ],
                "route_geometry": [{"lat": 3.1988, "lng": 101.7375}],
            }
        ]

        result = match_official_services("Kuala Lumpur", selected_micro, analysis_raw)

        self.assertTrue(result.official_data_used)
        self.assertIn("Rapid KL T251", " ".join(result.matched_services))
        self.assertIn(result.overlap_level, {"high", "partial"})
        self.assertEqual(result.recommendation_mode, "upgrade_existing_service")

    def test_match_official_services_handles_no_inventory(self):
        result = match_official_services(
            "Shah Alam",
            {"location_label": "Section 13", "road_a_queries": ["Section 13"], "road_b_queries": ["Section 7"]},
            [{"candidates": [{"via_roads": ["Section 13"]}], "route_geometry": []}],
        )
        self.assertFalse(result.official_data_used)
        self.assertEqual(result.overlap_level, "none")
        self.assertEqual(result.recommendation_mode, "new_service_candidate")

    def test_claim_audit_rewrites_unsupported_numbers(self):
        decision_package = {
            "allowed_numeric_facts": ["400m", "1850m"],
            "reliability_band": "medium",
            "geo_consistency": {"city_match_pass": True},
        }
        solution = {
            "solution_title": "Sri Rampai 2.5km Connector",
            "detailed_description": "This closes a 2.5km detour and cuts waits to 30 minutes.",
            "societal_impact": "Commuters save 25% travel time.",
            "expected_effect": ["Reduces a 2.5km detour", "Improves 30 min transfer experience"],
        }

        audit = audit_solution_claims(solution, decision_package)

        self.assertTrue(audit.pass_check)
        self.assertFalse(audit.hard_fail)
        self.assertTrue(audit.removed_claims)
        self.assertIn("Potential impact:", audit.sanitized_solution["societal_impact"])
        self.assertNotIn("2.5km", audit.sanitized_solution["detailed_description"])

    def test_geo_consistency_rejects_mixed_city_route(self):
        result = validate_geo_consistency(
            {"lat": 3.1390, "lng": 101.6869},
            {"location_label": "KL", "lat": 3.14, "lon": 101.68},
            [{"route_geometry": [{"lat": 5.399, "lng": 100.363}], "candidates": []}],
            entities=[{"position": {"lat": 5.399, "lng": 100.363}}],
        )

        self.assertFalse(result.pass_check)
        self.assertFalse(result.city_match_pass)

    def test_build_decision_package_sets_reliability_metadata(self):
        decision = build_decision_package(
            selected_city="Kuala Lumpur",
            selected_micro={
                "location_label": "Sri Rampai access gap",
                "road_a_queries": ["LRT Sri Rampai"],
                "road_b_queries": ["Jalan Wangsa Delima"],
                "road_a_label": "LRT Sri Rampai",
                "road_b_label": "Jalan Wangsa Delima",
                "lat": 3.1988,
                "lon": 101.7375,
                "confidence": "medium",
            },
            analysis_result=[{"candidates": [{"candidate_id": "candidate_1"}]}],
            analysis_result_raw=[
                {
                    "candidates": [
                        {
                            "candidate_id": "candidate_1",
                            "via_roads": ["Jalan Wangsa Delima"],
                            "total_length_m": 1850.0,
                        }
                    ],
                    "route_geometry": [{"lat": 3.1988, "lng": 101.7375}],
                    "mode": "corridor",
                }
            ],
            city_center={"lat": 3.1390, "lng": 101.6869},
        )

        self.assertIn("official_service_match", decision)
        self.assertIn("allowed_numeric_facts", decision)
        self.assertIn("reliability_band", decision)
        self.assertIn("evidence_basis", decision)
        self.assertIn("intervention_support", decision)
        self.assertIn("solution_eligibility", decision)
        self.assertTrue(decision["solution_eligibility"]["eligible"])

    def test_build_decision_package_marks_sparse_evidence_ineligible(self):
        decision = build_decision_package(
            selected_city="Kuala Lumpur",
            selected_micro={
                "location_label": "Generic access issue",
                "road_a_queries": ["Neighborhood A"],
                "road_b_queries": ["Neighborhood B"],
                "road_a_label": "Neighborhood A",
                "road_b_label": "Neighborhood B",
                "lat": 3.1390,
                "lon": 101.6869,
                "confidence": "low",
            },
            analysis_result=[{"candidates": []}],
            analysis_result_raw=[
                {
                    "candidates": [],
                    "route_geometry": [],
                    "mode": "corridor",
                }
            ],
            city_center={"lat": 3.1390, "lng": 101.6869},
        )

        self.assertFalse(decision["solution_eligibility"]["eligible"])
        self.assertTrue(decision["warnings"])


if __name__ == "__main__":
    unittest.main()
