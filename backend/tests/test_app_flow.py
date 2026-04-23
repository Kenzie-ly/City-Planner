import asyncio
import copy
import json
import os
import sys
import types
import unittest
from unittest.mock import patch


def _install_test_stubs() -> None:
    dotenv_mod = types.ModuleType("dotenv")
    dotenv_mod.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv_mod

    requests_mod = types.ModuleType("requests")
    requests_mod.get = lambda *args, **kwargs: {"json": lambda: []}
    requests_mod.post = lambda *args, **kwargs: {"json": lambda: {"elements": []}}
    sys.modules["requests"] = requests_mod

    pydantic_mod = types.ModuleType("pydantic")

    class BaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    pydantic_mod.BaseModel = BaseModel
    sys.modules["pydantic"] = pydantic_mod

    # fastapi stub
    fastapi_mod = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: str):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class FastAPI:
        def __init__(self, *args, **kwargs):
            self.routes = {}

        def add_middleware(self, *args, **kwargs):
            return None

        def post(self, path):
            def _decorator(fn):
                self.routes[path] = fn
                return fn

            return _decorator

    fastapi_mod.FastAPI = FastAPI
    fastapi_mod.HTTPException = HTTPException
    class BackgroundTasks:
        def add_task(self, fn, *args, **kwargs):
            return None
    fastapi_mod.BackgroundTasks = BackgroundTasks
    sys.modules["fastapi"] = fastapi_mod

    cors_mod = types.ModuleType("fastapi.middleware.cors")

    class CORSMiddleware:
        pass

    cors_mod.CORSMiddleware = CORSMiddleware
    sys.modules["fastapi.middleware.cors"] = cors_mod

    # google runtime stubs used by app.run_agent_once
    runners_mod = types.ModuleType("google.adk.runners")

    class Runner:
        def __init__(self, *args, **kwargs):
            pass

        async def run_async(self, *args, **kwargs):
            if False:
                yield None

    runners_mod.Runner = Runner
    sys.modules["google.adk.runners"] = runners_mod

    sessions_mod = types.ModuleType("google.adk.sessions")

    class _Session:
        def __init__(self, session_id: str, state: dict):
            self.id = session_id
            self.state = state

    class InMemorySessionService:
        def __init__(self):
            self._sessions = {}

        async def create_session(self, app_name, user_id, session_id=None, state=None):
            sid = session_id or "session_1"
            sess = _Session(sid, state or {})
            self._sessions[sid] = sess
            return sess

        async def get_session(self, app_name, user_id, session_id):
            return self._sessions[session_id]

    sessions_mod.InMemorySessionService = InMemorySessionService
    sys.modules["google.adk.sessions"] = sessions_mod

    genai_types_mod = types.ModuleType("google.genai.types")

    class Part:
        def __init__(self, text=None):
            self.text = text

    class Content:
        def __init__(self, role=None, parts=None):
            self.role = role
            self.parts = parts or []

    genai_types_mod.Part = Part
    genai_types_mod.Content = Content

    genai_mod = types.ModuleType("google.genai")
    genai_mod.types = genai_types_mod
    sys.modules["google.genai"] = genai_mod
    sys.modules["google.genai.types"] = genai_types_mod

    # agent and helpers stubs
    agent_mod = types.ModuleType("agent")

    class _A:
        def __init__(self, name):
            self.name = name

    agent_mod.place_intake_agent = _A("place_intake_agent")
    agent_mod.find_needs_agent = _A("find_needs_agent")
    agent_mod.growth_signal_agent = _A("growth_signal_agent")
    agent_mod.planning_agent = _A("planning_agent")
    agent_mod.solution_agent = _A("solution_agent")
    agent_mod.building_agent = _A("building_agent")
    agent_mod.review_agent = _A("review_agent")
    agent_mod.find_hotspot_agent = _A("find_hotspot_agent")
    agent_mod.hallucination_audit_agent = _A("hallucination_audit_agent")

    class InfrastructurePlannerOrchestrator:
        def __init__(self, *args, **kwargs):
            pass

        def run_analysis_from_agent_output(self, *args, **kwargs):
            return []

    agent_mod.InfrastructurePlannerOrchestrator = InfrastructurePlannerOrchestrator
    sys.modules["agent"] = agent_mod

    helper_mod = types.ModuleType("building_agent_helper")
    helper_mod.process_agent_assets = lambda *args, **kwargs: []
    helper_mod.format_entities = lambda *args, **kwargs: []
    helper_mod.get_malaysia_coords = lambda city: {"lat": 3.1390, "lng": 101.6869}
    sys.modules["building_agent_helper"] = helper_mod

    roads_mod = types.ModuleType("FindRoads")
    roads_mod.run_city_road_connection_analysis = lambda **kwargs: {"candidates": [{"candidate_id": "c1"}]}
    sys.modules["FindRoads"] = roads_mod


_install_test_stubs()
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app as appmod  # noqa: E402
from evidence_pipeline import AuditResult  # noqa: E402


def _mock_challenges() -> str:
    return json.dumps(_mock_challenge_obj())


def _mock_challenge_obj() -> dict:
    base_sources = [
        {
            "publisher": "Malaysia Transport Ministry",
            "url": "https://mot.gov.my/report-a",
            "published_at": "2025-10-01",
            "source_tier": "government",
        },
        {
            "publisher": "Bernama",
            "url": "https://bernama.com/news-b",
            "published_at": "2026-01-15",
            "source_tier": "major_media",
        },
    ]
    return {
        "CHALLENGE_1": {
            "CHALLENGE_THEME": "Transit desert in worker housing cluster",
            "MACRO_ROOT_CAUSE": "Feeder mismatch with industrial shift timings",
            "WHY_IT_MATTERS": "Workers rely on costly private rides",
            "EVIDENCE_SUMMARY": "Multiple sources report commuting pain points.",
            "TITLE": "Worker Commute Access Deficit",
            "STATISTICS": {"Ridership Gap Index": 74, "First-Mile Delay (min)": 22},
            "BRIEF_DESCRIPTION": "Observed indicators show high first-mile delay and lower transit uptake. This signals a persistent access bottleneck for shift workers.",
            "SOURCES": copy.deepcopy(base_sources),
            "CHART_SPEC": {
                "chart_type": "bar",
                "labels": ["Ridership Gap Index", "First-Mile Delay (min)"],
                "values": [74, 22],
            },
        },
        "CHALLENGE_2": {
            "CHALLENGE_THEME": "Weak bus-rail interchange",
            "MACRO_ROOT_CAUSE": "Insufficient feeder routes",
            "WHY_IT_MATTERS": "Lower ridership conversion",
            "EVIDENCE_SUMMARY": "Observed low integration indicators.",
            "TITLE": "Interchange Reliability Deficit",
            "STATISTICS": {"Missed Transfer Rate (%)": 31, "Average Wait (min)": 18},
            "BRIEF_DESCRIPTION": "Transfer statistics indicate unstable interchange performance. Riders face longer waits and missed rail connections.",
            "SOURCES": [
                {
                    "publisher": "Prasarana",
                    "url": "https://rapidkl.com.my/ops-c",
                    "published_at": "2025-12-01",
                    "source_tier": "operator",
                },
                {
                    "publisher": "The Star",
                    "url": "https://thestar.com.my/transit-d",
                    "published_at": "2026-02-11",
                    "source_tier": "major_media",
                },
            ],
            "CHART_SPEC": {
                "chart_type": "bar",
                "labels": ["Missed Transfer Rate (%)", "Average Wait (min)"],
                "values": [31, 18],
            },
        },
        "CHALLENGE_3": {
            "CHALLENGE_THEME": "Pedestrian access gap near stations",
            "MACRO_ROOT_CAUSE": "Unsafe last-mile crossings",
            "WHY_IT_MATTERS": "Discourages transit usage",
            "EVIDENCE_SUMMARY": "Community safety concerns noted.",
            "TITLE": "Last-Mile Walkability Risk",
            "STATISTICS": {"Unsafe Crossing Points": 14, "Walk Detour Ratio": 1.6},
            "BRIEF_DESCRIPTION": "Walkability indicators suggest unsafe and indirect access to stations. This lowers effective transit catchment quality.",
            "SOURCES": [
                {
                    "publisher": "PlanMalaysia",
                    "url": "https://planmalaysia.gov.my/walk-e",
                    "published_at": "2025-08-20",
                    "source_tier": "government",
                },
                {
                    "publisher": "NST",
                    "url": "https://nst.com.my/mobility-f",
                    "published_at": "2026-01-02",
                    "source_tier": "major_media",
                },
            ],
            "CHART_SPEC": {
                "chart_type": "bar",
                "labels": ["Unsafe Crossing Points", "Walk Detour Ratio"],
                "values": [14, 1.6],
            },
        },
    }


def _mock_micro_result() -> dict:
    return {
        "PRIMARY_MICRO": {
            "location_label": "Section 13",
            "type": "corridor",
            "symptom": "Workers face long transfers",
            "road_a_queries": ["Section 13"],
            "road_b_queries": ["Section 13"],
            "road_a_label": "Section 13",
            "road_b_label": "Section 13",
            "confidence": "high",
        },
        "SECONDARY_MICRO": {
            "location_label": "Section 7",
            "type": "corridor",
            "symptom": "Limited feeder coverage",
            "road_a_queries": ["Section 7"],
            "road_b_queries": ["Section 7"],
            "road_a_label": "Section 7",
            "road_b_label": "Section 7",
            "confidence": "medium",
        },
        "CONFIDENCE": "high",
        "EVIDENCE_WINDOW": ["osm_spatial"],
        "attempts": [],
    }


class AppFlowTests(unittest.TestCase):
    def setUp(self):
        appmod.workflow_state.clear()

    def _start(self):
        return asyncio.run(appmod.start(appmod.StartRequest()))

    def _chat(self, sid: str, msg: str):
        return asyncio.run(appmod.chat(appmod.ChatRequest(session_id=sid, message=msg), appmod.BackgroundTasks()))

    def _patch_common_pipeline(self):
        async def fake_run_agent_once(agent, session_id: str, prompt: str) -> str:
            name = getattr(agent, "name", "")
            p = prompt.lower()
            if name == "place_intake_agent":
                if "conversation is starting" in p:
                    return "VERDICT: RETRY\nPLACES:\nFEEDBACK: Please share one or two Malaysian cities."
                return "VERDICT: SUCCESS\nPLACES: Shah Alam\nFEEDBACK: Great, proceeding."
            if name == "growth_signal_agent":
                if "industrial" in p:
                    return json.dumps(
                        [
                            {
                                "title": "Industrial expansion in Section 13",
                                "url": "https://gov.my/ind-13",
                                "snippet": "Factory jobs expected to rise.",
                                "published_at": "2026-02-10",
                                "source_tier": "government",
                                "claim_type": "industrial",
                                "area_label": "Section 13",
                                "claim_key": "sec13_ind",
                            }
                        ]
                    )
                return json.dumps(
                    [
                        {
                            "title": "Township growth in Section 7",
                            "url": "https://media.my/section-7",
                            "snippet": "Housing growth reported.",
                            "published_at": "2026-01-15",
                            "source_tier": "major_media",
                            "claim_type": "population",
                            "area_label": "Section 7",
                            "claim_key": "sec7_pop",
                        }
                    ]
                )
            if name == "find_needs_agent":
                return _mock_challenges()
            if name == "hallucination_audit_agent":
                return "VERDICT: PASS\nREASON: grounded in provided evidence."
            if name == "review_agent":
                if "step name: find needs" in p:
                    ch = json.loads(_mock_challenges())["CHALLENGE_1"]
                    return f"VERDICT: PASS\nREASON: clear choice\nRESOLVED_REFERENCE: CHALLENGE_1\nJSON_OUTPUT: {json.dumps(ch)}"
                if "step name: select micro-symptom" in p:
                    micro = _mock_micro_result()["PRIMARY_MICRO"]
                    return f"VERDICT: PASS\nREASON: clear choice\nRESOLVED_REFERENCE: PRIMARY_MICRO\nJSON_OUTPUT: {json.dumps(micro)}"
                if "step name: generate solutions" in p:
                    solution = {
                        "solution_title": "Section 13 Feeder Upgrade",
                        "solution_type": "feeder_bus_route",
                        "target_geometry": {
                            "focus_type": "corridor_segment",
                            "location": "Section 13",
                            "primary_roads": ["Section 13"],
                            "affected_segments": ["segment_1"],
                        },
                        "proposed_actions": [
                            "Add feeder lane on Section 13 corridor",
                            "Create synchronized feeder stop near station",
                            "Improve pedestrian crossing to bus boarding",
                            "Introduce peak-shift bus dispatch window",
                        ],
                        "expected_effect": ["Lower transfer time", "Higher feeder usage", "Safer access"],
                        "implementation_complexity": "medium",
                        "confidence": "high",
                        "societal_impact": "Workers gain more reliable public transport access.",
                    }
                    return f"VERDICT: PASS\nREASON: approved\nRESOLVED_REFERENCE:\nOUTPUT: {json.dumps(solution)}"
                return "VERDICT: PASS\nREASON: approved\nRESOLVED_REFERENCE:\n"
            if name == "planning_agent":
                return json.dumps(
                    {
                        "selected_candidate_id": "candidate_1",
                        "intervention_type": "feeder_bus_route",
                        "decision_summary": "Best direct feeder option.",
                        "reasons": ["Shortest viable route", "High worker demand", "Low complexity"],
                        "tradeoffs": ["Needs schedule coordination", "Requires stop upgrades"],
                        "priority_level": "high",
                        "confidence": "high",
                    }
                )
            if name == "solution_agent":
                return json.dumps(
                    {
                        "solution_title": "Section 13 Feeder Upgrade",
                        "solution_type": "feeder_bus_route",
                        "target_geometry": {
                            "focus_type": "corridor_segment",
                            "location": "Section 13",
                            "primary_roads": ["Section 13"],
                            "affected_segments": ["segment_1"],
                        },
                        "proposed_actions": [
                            "Add feeder lane on Section 13 corridor",
                            "Create synchronized feeder stop near station",
                            "Improve pedestrian crossing to bus boarding",
                            "Introduce peak-shift bus dispatch window",
                        ],
                        "expected_effect": ["Lower transfer time", "Higher feeder usage", "Safer access"],
                        "implementation_complexity": "medium",
                        "confidence": "high",
                        "societal_impact": "Workers gain more reliable public transport access.",
                    }
                )
            if name == "building_agent":
                return "[POINT | x1 | Feeder Node | Section 13 | color:blue | Feeder transfer node]"
            return ""

        return patch.object(appmod, "run_agent_once", side_effect=fake_run_agent_once)

    def test_happy_path_area_selection_to_done(self):
        with self._patch_common_pipeline(), patch.object(
            appmod, "audit_osm_transit_gap", return_value=AuditResult(0.9, 0.95, {"matched": True})
        ), patch.object(
            appmod, "_run_route_feasibility", return_value={"pass": True, "score": 1.0, "candidate_count": 1}
        ), patch.object(
            appmod, "run_hotspot_hypothesis_loop", return_value=_mock_micro_result()
        ), patch.object(
            appmod,
            "analyze_selected_micro",
            return_value=[
                {
                    "selected_micro_source": "PRIMARY_MICRO",
                    "selected_micro_type": "corridor",
                    "selected_micro_symptom": "Workers face long transfers",
                    "selected_micro_location_label": "Section 13",
                    "mode": "same_road",
                    "city_query": "Shah Alam, Malaysia",
                    "candidates": [{"candidate_id": "candidate_1", "via_roads": ["Section 13"]}],
                    "route_geometry": [{"lat": 3.07, "lng": 101.52, "height": 0}],
                    "isochrone_geoms": [],
                }
            ],
        ), patch.object(
            appmod, "process_agent_assets", return_value=[{"dummy": True}]
        ), patch.object(
            appmod, "format_entities", return_value=[{"id": "entity_1", "entity_type": "point"}]
        ), patch.object(
            appmod, "get_context_infrastructure", return_value=[]
        ):
            start = self._start()
            intake = self._chat(start["session_id"], "Shah Alam")
            self.assertEqual(intake["stage"], "Area selection")
            self.assertTrue(intake.get("area_options"))
            first_area = intake["area_options"][0]
            self.assertIn("description_paragraph", first_area)
            self.assertIn("micro_paragraph", first_area)
            self.assertIn("trusted_sources", first_area)
            self.assertGreaterEqual(len(first_area.get("trusted_sources", [])), 2)

            area_pick = self._chat(start["session_id"], "1")
            self.assertEqual(area_pick["stage"], "Find needs")
            self.assertIn("find_needs_options", area_pick)
            self.assertEqual(len(area_pick["find_needs_options"]), 3)

            challenge_pick = self._chat(start["session_id"], "1")
            self.assertEqual(challenge_pick["stage"], "Micro hotspot selection")

            micro_pick = self._chat(start["session_id"], "1")
            self.assertEqual(micro_pick["stage"], "Plan improvements")

            approve_plan = self._chat(start["session_id"], "ok")
            self.assertEqual(approve_plan["stage"], "Generate solutions")

            approve_solution = self._chat(start["session_id"], "ok")
            self.assertEqual(approve_solution["stage"], "done")
            self.assertTrue(approve_solution["show_map"])
            self.assertIn("entities", approve_solution)

    def test_gate_fail_low_completeness(self):
        with self._patch_common_pipeline(), patch.object(
            appmod, "audit_osm_transit_gap", return_value=AuditResult(0.4, 0.2, {"matched": False})
        ), patch.object(
            appmod, "_run_route_feasibility", return_value={"pass": False, "score": 0.0, "candidate_count": 0}
        ):
            start = self._start()
            intake = self._chat(start["session_id"], "Shah Alam")
            self.assertEqual(intake["stage"], "Area selection")

            area_pick = self._chat(start["session_id"], "1")
            self.assertEqual(area_pick["stage"], "Area selection")
            self.assertTrue(area_pick["needs_selection"])
            self.assertIn("Needs verification", area_pick["reply"])

    def test_high_report_low_gap_does_not_auto_build(self):
        with self._patch_common_pipeline(), patch.object(
            appmod, "audit_osm_transit_gap", return_value=AuditResult(0.05, 1.0, {"matched": True})
        ), patch.object(
            appmod, "_run_route_feasibility", return_value={"pass": True, "score": 1.0, "candidate_count": 1}
        ):
            start = self._start()
            intake = self._chat(start["session_id"], "Shah Alam")
            self.assertEqual(intake["stage"], "Area selection")

            area_pick = self._chat(start["session_id"], "1")
            self.assertEqual(area_pick["stage"], "Area selection")

    def test_user_reselection_then_gate_pass(self):
        def feasibility(city, selected_area):
            if selected_area.get("id") == "area_1":
                return {"pass": False, "score": 0.0, "candidate_count": 0}
            return {"pass": True, "score": 1.0, "candidate_count": 2}

        with self._patch_common_pipeline(), patch.object(
            appmod, "audit_osm_transit_gap", return_value=AuditResult(0.85, 0.95, {"matched": True})
        ), patch.object(
            appmod, "_run_route_feasibility", side_effect=feasibility
        ):
            start = self._start()
            intake = self._chat(start["session_id"], "Shah Alam")
            self.assertEqual(intake["stage"], "Area selection")

            first_pick = self._chat(start["session_id"], "1")
            self.assertEqual(first_pick["stage"], "Area selection")

            second_pick = self._chat(start["session_id"], "2")
            self.assertEqual(second_pick["stage"], "Find needs")

    def test_fallback_area_can_override_gate_and_reach_find_needs(self):
        async def fake_run_agent_once(agent, session_id: str, prompt: str) -> str:
            name = getattr(agent, "name", "")
            p = prompt.lower()
            if name == "place_intake_agent":
                if "conversation is starting" in p:
                    return "VERDICT: RETRY\nPLACES:\nFEEDBACK: Please share one or two Malaysian cities."
                return "VERDICT: SUCCESS\nPLACES: Kuala Lumpur\nFEEDBACK: Great, proceeding."
            if name == "growth_signal_agent":
                # Force fallback area options by returning empty findings.
                return "[]"
            if name == "find_needs_agent":
                return _mock_challenges()
            if name == "hallucination_audit_agent":
                return "VERDICT: PASS\nREASON: grounded in provided evidence."
            return "VERDICT: PASS\nREASON: ok\n"

        with patch.object(appmod, "run_agent_once", side_effect=fake_run_agent_once), patch.object(
            appmod, "audit_osm_transit_gap", return_value=AuditResult(0.2, 0.3, {"matched": False})
        ), patch.object(
            appmod, "_run_route_feasibility", return_value={"pass": False, "score": 0.0, "candidate_count": 0}
        ):
            start = self._start()
            intake = self._chat(start["session_id"], "Kuala Lumpur")
            self.assertEqual(intake["stage"], "Find needs")
            self.assertEqual(len(intake.get("find_needs_options", [])), 3)

    def test_regression_old_flow_when_feature_flag_off(self):
        async def old_flow_run_agent_once(agent, session_id: str, prompt: str) -> str:
            name = getattr(agent, "name", "")
            if name == "place_intake_agent":
                if "conversation is starting" in prompt.lower():
                    return "VERDICT: RETRY\nPLACES:\nFEEDBACK: Please share city."
                return "VERDICT: SUCCESS\nPLACES: Shah Alam\nFEEDBACK: ok"
            if name == "find_needs_agent":
                return _mock_challenges()
            return "VERDICT: PASS\nREASON: ok\n"

        old_flag = appmod.GROWTH_FLOW_ENABLED
        appmod.GROWTH_FLOW_ENABLED = False
        try:
            with patch.object(appmod, "run_agent_once", side_effect=old_flow_run_agent_once):
                start = self._start()
                intake = self._chat(start["session_id"], "Shah Alam")
                self.assertEqual(intake["stage"], "Find needs")
                self.assertNotIn("area_options", intake)
        finally:
            appmod.GROWTH_FLOW_ENABLED = old_flag

    def test_find_needs_validation_helpers(self):
        options, errors = appmod.build_find_needs_options(_mock_challenges())
        self.assertEqual(len(options), 3)
        self.assertFalse(errors)

        bad_sources = _mock_challenge_obj()
        bad_sources["CHALLENGE_1"]["SOURCES"][0]["source_tier"] = "community"
        options, errors = appmod.build_find_needs_options(json.dumps(bad_sources))
        self.assertLess(len(options), 3)
        self.assertTrue(any("valid sources" in e.lower() for e in errors))

        bad_chart = _mock_challenge_obj()
        bad_chart["CHALLENGE_2"]["CHART_SPEC"]["values"] = [1]
        options, errors = appmod.build_find_needs_options(json.dumps(bad_chart))
        self.assertFalse(options)
        self.assertTrue(any("chart_spec" in e.lower() for e in errors))

        bad_brief = _mock_challenge_obj()
        bad_brief["CHALLENGE_3"]["BRIEF_DESCRIPTION"] = "One. Two. Three. Four."
        options, errors = appmod.build_find_needs_options(json.dumps(bad_brief))
        self.assertFalse(options)
        self.assertTrue(any("brief_description" in e.lower() for e in errors))

    def test_find_needs_repair_path(self):
        async def fake_run_agent_once(agent, session_id: str, prompt: str) -> str:
            name = getattr(agent, "name", "")
            p = prompt.lower()
            if name == "place_intake_agent":
                if "conversation is starting" in p:
                    return "VERDICT: RETRY\nPLACES:\nFEEDBACK: Please share one or two Malaysian cities."
                return "VERDICT: SUCCESS\nPLACES: Shah Alam\nFEEDBACK: Great, proceeding."
            if name == "growth_signal_agent":
                return json.dumps(
                    [
                        {
                            "title": "Township growth in Section 7",
                            "url": "https://media.my/section-7",
                            "snippet": "Housing growth reported.",
                            "published_at": "2026-01-15",
                            "source_tier": "major_media",
                            "claim_type": "population",
                            "area_label": "Section 7",
                            "claim_key": "sec7_pop",
                        }
                    ]
                )
            if name == "find_needs_agent":
                if "previously returned invalid find-needs json" in p:
                    return _mock_challenges()
                invalid = _mock_challenge_obj()
                invalid["CHALLENGE_1"]["SOURCES"] = [
                    {
                        "publisher": "Invalid",
                        "url": "http://bad.local",
                        "published_at": "",
                        "source_tier": "community",
                    }
                ]
                return json.dumps(invalid)
            return "VERDICT: PASS\nREASON: ok\n"

        with patch.object(appmod, "run_agent_once", side_effect=fake_run_agent_once), patch.object(
            appmod, "audit_osm_transit_gap", return_value=AuditResult(0.9, 0.95, {"matched": True})
        ), patch.object(
            appmod, "_run_route_feasibility", return_value={"pass": True, "score": 1.0, "candidate_count": 1}
        ):
            start = self._start()
            intake = self._chat(start["session_id"], "Shah Alam")
            self.assertEqual(intake["stage"], "Area selection")
            area_pick = self._chat(start["session_id"], "1")
            self.assertEqual(area_pick["stage"], "Find needs")
            self.assertEqual(len(area_pick.get("find_needs_options", [])), 3)

    def test_find_needs_fallback_when_repair_still_invalid(self):
        async def fake_run_agent_once(agent, session_id: str, prompt: str) -> str:
            name = getattr(agent, "name", "")
            p = prompt.lower()
            if name == "place_intake_agent":
                if "conversation is starting" in p:
                    return "VERDICT: RETRY\nPLACES:\nFEEDBACK: Please share one or two Malaysian cities."
                return "VERDICT: SUCCESS\nPLACES: Shah Alam\nFEEDBACK: Great, proceeding."
            if name == "growth_signal_agent":
                return json.dumps(
                    [
                        {
                            "title": "Township growth in Section 7",
                            "url": "https://media.my/section-7",
                            "snippet": "Housing growth reported.",
                            "published_at": "2026-01-15",
                            "source_tier": "major_media",
                            "claim_type": "population",
                            "area_label": "Section 7",
                            "claim_key": "sec7_pop",
                        }
                    ]
                )
            if name == "find_needs_agent":
                invalid = _mock_challenge_obj()
                invalid["CHALLENGE_1"]["SOURCES"] = [
                    {
                        "publisher": "Invalid",
                        "url": "http://bad.local",
                        "published_at": "",
                        "source_tier": "community",
                    }
                ]
                return json.dumps(invalid)
            return "VERDICT: PASS\nREASON: ok\n"

        with patch.object(appmod, "run_agent_once", side_effect=fake_run_agent_once), patch.object(
            appmod, "audit_osm_transit_gap", return_value=AuditResult(0.9, 0.95, {"matched": True})
        ), patch.object(
            appmod, "_run_route_feasibility", return_value={"pass": True, "score": 1.0, "candidate_count": 1}
        ):
            start = self._start()
            intake = self._chat(start["session_id"], "Shah Alam")
            area_pick = self._chat(start["session_id"], "1")
            self.assertEqual(area_pick["stage"], "Find needs")
            self.assertEqual(len(area_pick.get("find_needs_options", [])), 3)
            self.assertIn("Review the 3 evidence cards below", area_pick["reply"])

    def test_area_card_audit_rewrite_then_pass(self):
        option = {
            "id": "area_1",
            "city": "Shah Alam",
            "area_label": "Section 13",
            "google_evidence": [
                {
                    "title": "Industrial expansion in Section 13",
                    "url": "https://mot.gov.my/ind-13",
                    "snippet": "Factory jobs expansion reported near Section 13.",
                    "published_at": "2026-02-10",
                    "source_tier": "government",
                    "claim_type": "industrial",
                    "area_label": "Section 13",
                },
                {
                    "title": "Township growth in Section 7",
                    "url": "https://bernama.com/sec-7",
                    "snippet": "Housing growth and commuting pressure observed in Section 7.",
                    "published_at": "2026-01-10",
                    "source_tier": "major_media",
                    "claim_type": "population",
                    "area_label": "Section 7",
                },
            ],
            "growth_signals": {"population": 1, "industrial": 1, "trip_generator": 0, "complaints": 0},
        }

        calls = {"n": 0}

        async def fake_run_agent_once(agent, session_id: str, prompt: str) -> str:
            if getattr(agent, "name", "") == "hallucination_audit_agent":
                calls["n"] += 1
                if calls["n"] == 1:
                    return "VERDICT: FAIL\nREASON: unsupported claim"
                return "VERDICT: PASS\nREASON: grounded"
            return "VERDICT: PASS\nREASON: ok"

        with patch.object(appmod, "run_agent_once", side_effect=fake_run_agent_once):
            enriched = asyncio.run(appmod._synthesize_area_card_content("sid", "Shah Alam", option))
            self.assertIsNotNone(enriched)
            self.assertEqual(calls["n"], 2)
            self.assertIn("description_paragraph", enriched)
            self.assertIn("micro_paragraph", enriched)

    def test_area_card_audit_rewrite_then_fail_drops_card(self):
        option = {
            "id": "area_1",
            "city": "Shah Alam",
            "area_label": "Section 13",
            "google_evidence": [
                {
                    "title": "Industrial expansion in Section 13",
                    "url": "https://mot.gov.my/ind-13",
                    "snippet": "Factory jobs expansion reported near Section 13.",
                    "published_at": "2026-02-10",
                    "source_tier": "government",
                    "claim_type": "industrial",
                    "area_label": "Section 13",
                },
                {
                    "title": "Township growth in Section 7",
                    "url": "https://bernama.com/sec-7",
                    "snippet": "Housing growth and commuting pressure observed in Section 7.",
                    "published_at": "2026-01-10",
                    "source_tier": "major_media",
                    "claim_type": "population",
                    "area_label": "Section 7",
                },
            ],
            "growth_signals": {"population": 1, "industrial": 1, "trip_generator": 0, "complaints": 0},
        }

        async def fake_run_agent_once(agent, session_id: str, prompt: str) -> str:
            if getattr(agent, "name", "") == "hallucination_audit_agent":
                return "VERDICT: FAIL\nREASON: unsupported claim"
            return "VERDICT: PASS\nREASON: ok"

        with patch.object(appmod, "run_agent_once", side_effect=fake_run_agent_once):
            enriched = asyncio.run(appmod._synthesize_area_card_content("sid", "Shah Alam", option))
            self.assertIsNone(enriched)


if __name__ == "__main__":
    unittest.main()
