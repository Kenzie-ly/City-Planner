from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from typing import Any
import uuid
import json
import re

from agent import (
    place_intake_agent,
    find_needs_agent,
    planning_agent,
    solution_agent,
    building_agent,
    review_agent,
    InfrastructurePlannerOrchestrator,
    find_hotspot_agent,
)
from building_agent_helper import process_agent_assets, format_entities

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
HERE_API_KEY = os.getenv("HERE_API_KEY", "")

app = FastAPI(title="Infrastructure Planner API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

APP_NAME = "infrastructure_planner"
USER_ID = "user_001"

session_service = InMemorySessionService()
workflow_state: dict[str, dict[str, Any]] = {}

PIPELINE = [
    ("Plan improvements", planning_agent, "planning_result"),
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
    if text.startswith("```"):
        text = re.sub(r"```[a-zA-Z]*", "", text)
        text = text.replace("```", "")
    return text.strip()


def safe_json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        raise ValueError("Expected JSON string, dict, or list.")
    return json.loads(clean_json_text(value))


async def run_agent_once(agent, session_id: str, prompt: str) -> str:
    runner = Runner(agent=agent, app_name=APP_NAME, session_service=session_service)
    message = types.Content(role="user", parts=[types.Part(text=prompt)])

    response = ""
    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session_id,
        new_message=message,
    ):
        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    response += part.text
    return response.strip()


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
    verdict_match = re.search(r"VERDICT:\s*(PASS|REVISE|REVISE_TOTAL)", text, re.IGNORECASE)
    if verdict_match:
        result["verdict"] = verdict_match.group(1).strip().upper()

    detail_match = re.search(r"(?:REASON|INSTRUCTION):\s*(.+)", text, re.IGNORECASE | re.DOTALL)
    if detail_match:
        result["detail"] = detail_match.group(1).strip()

    resolved_match = re.search(r"RESOLVED_REFERENCE:\s*(.*)", text, re.IGNORECASE)
    if resolved_match:
        result["resolved_reference"] = resolved_match.group(1).strip()

    json_match = re.search(r"JSON_OUTPUT:\s*(\{.*\})", text, re.DOTALL | re.IGNORECASE)
    if json_match:
        result["final_output"] = json_match.group(1).strip()
        return result

    output_match = re.search(r"OUTPUT:\s*(.+)", text, re.DOTALL | re.IGNORECASE)
    if output_match:
        result["final_output"] = output_match.group(1).strip()

    return result


def extract_challenge_json_blocks(text: str) -> list[dict[str, Any]]:
    cleaned = clean_json_text(text)
    try:
        obj = json.loads(cleaned)
        keys = [k for k in obj if k.startswith("CHALLENGE_") and k[len("CHALLENGE_"):].isdigit()]
        return [obj[k] for k in sorted(keys)]
    except Exception:
        return []


def format_challenges(challenges: list[dict[str, Any]]) -> str:
    if not challenges:
        return "No challenge data could be extracted."

    blocks: list[str] = []
    for idx, challenge in enumerate(challenges, start=1):
        theme = challenge.get('CHALLENGE_THEME', 'Untitled challenge')
        cause = challenge.get('MACRO_ROOT_CAUSE', 'N/A')
        impact = challenge.get('WHY_IT_MATTERS', 'N/A')
        evidence = challenge.get('EVIDENCE_SUMMARY', 'N/A')
        
        # Concatenate into one natural-sounding paragraph
        paragraph = f"{idx}. **{theme}**: {cause} {impact} {evidence}"
        blocks.append(paragraph)
        
    return "\n\n".join(blocks) + "\n\nWhich challenge would you like to explore further?"


def format_find_needs_reply(raw_step_output: str) -> str:
    challenges = extract_challenge_json_blocks(raw_step_output)
    if challenges:
        return format_challenges(challenges)
    return raw_step_output

def build_find_needs_prompt(target_places: list[str]) -> str:
    return f"""
        You are given TARGET PLACE(S): {target_places}

        Task:
        Identify and rank exactly 3 broad transport-related infrastructure challenges only.

        Rules:
        - Do NOT output PRIMARY_MICRO or SECONDARY_MICRO.
        - Do NOT generate routing labels.
        - Stay at challenge-category level.
        - Return exactly one JSON object with keys CHALLENGE_1, CHALLENGE_2, CHALLENGE_3.
        - Each challenge must include CHALLENGE_THEME, MACRO_ROOT_CAUSE, WHY_IT_MATTERS, and EVIDENCE_SUMMARY.
        - Return JSON only.
        """.strip()


async def start_planning_phase(session_id: str, current_session) -> dict[str, Any]:
    target_places = current_session.state.get("target_places", [])
    if not target_places:
        raise HTTPException(status_code=400, detail="No target places found in session state.")


    prompt = build_find_needs_prompt(target_places)
    raw_step_output = await run_agent_once(find_needs_agent, session_id, prompt)
    display_reply = format_find_needs_reply(raw_step_output)

    workflow_state[session_id] = {
        "phase": "challenge_selection",
        "last_step_output": raw_step_output,
        "last_display_reply": display_reply,
        "target_places": target_places,
        "step_index": 0,
    }

    return {
        "ok": True,
        "session_id": session_id,
        "stage": "Find needs",
        "reply": display_reply,
        "needs_input": True,
    }


def build_hotspot_hypothesis_prompt(city: str, selected_challenge: dict[str, Any], feedback: str = "") -> str:
    return f"""
        You are generating ONE transport hotspot hypothesis for the selected challenge.

        CITY:
        {city}

        SELECTED_CHALLENGE_JSON:
        {json.dumps(selected_challenge, ensure_ascii=False, indent=2)}

        Previous feedback:
        {feedback or 'None'}

        Rules:
        - Propose exactly ONE micro-level hotspot.
        - It must be graph-routable.
        - Do not invent vague roads.
        - Prefer corridor, junction, freight_route, or transit_node.
        - Return STRICT JSON only.

        {{
        "location_label": "...",
        "type": "corridor | junction | freight_route | transit_node",
        "symptom": "...",
        "road_a_queries": ["..."],
        "road_b_queries": ["..."],
        "road_a_label": "...",
        "road_b_label": "...",
        "confidence": "low | medium | high"
        }}
    """.strip()


def _evidence_fallback(city: str, location_label: str, reason: str) -> dict[str, Any]:
    return {
        "matched": False,
        "source": "here_traffic",
        "city": city,
        "location_label": location_label,
        "congestion_score": 0.0,
        "speed_confidence": "low",
        "evidence_window": [],
        "notes": [reason],
    }


def get_here_traffic_evidence(city: str, hypothesis: dict[str, Any]) -> dict[str, Any]:
    location_label = hypothesis.get("location_label", "")
    road_queries = list(hypothesis.get("road_a_queries") or [])
    if hypothesis.get("road_b_queries"):
        road_queries.extend(hypothesis["road_b_queries"])

    if not HERE_API_KEY:
        return _evidence_fallback(city, location_label, "HERE_API_KEY not configured.")
    if not road_queries:
        return _evidence_fallback(city, location_label, "No routing labels available.")

    geocode_url = "https://geocode.search.hereapi.com/v1/geocode"
    last_error = None
    lat = lon = None

    for query in road_queries[:4]:
        try:
            geo_resp = requests.get(
                geocode_url,
                params={"q": f"{query}, {city}, Malaysia", "apiKey": HERE_API_KEY},
                timeout=8,
            ).json()
            items = geo_resp.get("items", [])
            if items:
                pos = items[0]["position"]
                lat, lon = pos["lat"], pos["lng"]
                break
        except Exception as exc:
            last_error = str(exc)

    if lat is None or lon is None:
        return _evidence_fallback(city, location_label, f"Geocode failed: {last_error or 'no match'}")

    analytics_url = "https://traffic.ls.hereapi.com/traffic/6.3/flow.json"
    try:
        resp = requests.get(
            analytics_url,
            params={
                "prox": f"{lat},{lon},500",
                "apiKey": HERE_API_KEY,
                "responseattributes": "sh,fc",
            },
            timeout=8,
        ).json()
        road_items = resp.get("RWS", [{}])[0].get("RW", [{}])[0].get("FIS", [{}])[0].get("FI", [])
        congestion_scores = []
        for item in road_items:
            cf = item.get("CF", [{}])[0]
            speed = cf.get("SP")
            free_flow = cf.get("FF")
            if speed is not None and free_flow and free_flow > 0:
                congestion_scores.append(max(0.0, min(1.0, 1.0 - (speed / free_flow))))

        congestion_score = round(sum(congestion_scores) / len(congestion_scores), 3) if congestion_scores else 0.35
        return {
            "matched": bool(road_items),
            "source": "here_traffic",
            "city": city,
            "location_label": location_label,
            "congestion_score": congestion_score,
            "speed_confidence": "high" if len(congestion_scores) >= 3 else "medium",
            "evidence_window": ["historical_7day_avg"],
            "notes": [],
        }
    except Exception as exc:
        return _evidence_fallback(city, location_label, f"Analytics API failed: {exc}")


def score_hypothesis_alignment(hypothesis: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    route_match_score = 1.0 if evidence.get("matched") else 0.0
    congestion_score = float(evidence.get("congestion_score", 0.0))
    confidence_bonus = 0.1 if str(hypothesis.get("confidence", "")).lower() == "high" else 0.0
    alignment_score = (0.55 * congestion_score) + (0.35 * route_match_score) + confidence_bonus
    return {"alignment_score": round(alignment_score, 3), "pass": alignment_score >= 0.55}


async def run_hotspot_hypothesis_loop(session_id: str, city: str, selected_challenge: dict[str, Any], feedback: str = "") -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []

    for i in range(3):
        prompt = build_hotspot_hypothesis_prompt(city, selected_challenge, feedback)
        raw = await run_agent_once(find_hotspot_agent, session_id, prompt)
        try:
            hypothesis = safe_json_loads(raw)
        except Exception as exc:
            attempts.append(
                {
                    "iteration": i + 1,
                    "hypothesis": {},
                    "evidence": _evidence_fallback(city, "", f"Invalid JSON from hypothesis agent: {exc}"),
                    "score": 0.0,
                    "pass": False,
                }
            )
            feedback = "Previous attempt was not valid JSON. Return one clean JSON object only."
            continue

        evidence = get_here_traffic_evidence(city, hypothesis)
        score = score_hypothesis_alignment(hypothesis, evidence)
        attempts.append(
            {
                "iteration": i + 1,
                "hypothesis": hypothesis,
                "evidence": evidence,
                "score": score["alignment_score"],
                "pass": score["pass"],
            }
        )
        if score["pass"]:
            break

        feedback = (
            f"Previous hypothesis was weak. alignment_score={score['alignment_score']}. "
            "Revise the location or routing labels and make the hotspot more specific."
        )

    valid_attempts = [a for a in attempts if a.get("hypothesis")]
    if not valid_attempts:
        raise HTTPException(status_code=500, detail="Hotspot hypothesis loop failed to produce valid JSON.")

    attempts_sorted = sorted(valid_attempts, key=lambda x: x["score"], reverse=True)
    primary = attempts_sorted[0]
    secondary = attempts_sorted[1] if len(attempts_sorted) > 1 else attempts_sorted[0]

    return {
        "PRIMARY_MICRO": primary["hypothesis"],
        "SECONDARY_MICRO": secondary["hypothesis"],
        "CONFIDENCE": primary["hypothesis"].get("confidence", "medium"),
        "EVIDENCE_WINDOW": primary["evidence"].get("evidence_window", []),
        "attempts": attempts_sorted,
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
    """Formats JSON output into readable text based on the active pipeline step."""
    if step_name == "Generate solutions":
        try:
            data = safe_json_loads(raw_output)
        except Exception:
            # Fallback: if the agent messes up and doesn't return JSON, just show the raw text
            return raw_output
        # Extract core details
        title = data.get("solution_title", "Proposed Solution")
        sol_type = str(data.get("solution_type", "Intervention")).replace("_", " ").title()
        complexity = str(data.get("implementation_complexity", "unknown")).title()
        confidence = str(data.get("confidence", "unknown")).title()
        
        # Extract geometry details safely
        target = data.get("target_geometry", {})
        location = target.get("location", "the target area")
        roads = target.get("primary_roads", [])
        
        # Extract lists
        actions = data.get("proposed_actions", [])
        effects = data.get("expected_effect", [])
        
        # Build the natural paragraph structure
        blocks = []
        blocks.append(f"### 🛠️ {title}")
        blocks.append(f"**Intervention Type:** {sol_type} | **Complexity:** {complexity} | **Confidence:** {confidence}\n")
        
        # Format the location sentence naturally
        road_context = f" involving {', '.join(roads)}" if roads else ""
        blocks.append(f"**Target Location:** {location}{road_context}.\n")
        
        if actions:
            blocks.append("**Proposed Actions:**")
            for action in actions:
                blocks.append(f"* {action}")
            blocks.append("")  # Spacing
            
        if effects:
            blocks.append("**Expected Effects:**")
            for effect in effects:
                blocks.append(f"* {effect}")
            blocks.append("")

        impact = data.get("societal_impact")
        if impact:
            blocks.append(f"**🌍 Societal Impact:**\n{impact}")
                
        # Optional: Add a small transition message for the user
        blocks.append("\n*This is a summary of what we're gonna build!!")
                
        return "\n".join(blocks).strip()

    # Default for Building simulations or unrecognized steps
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


def build_planning_prompt(
    selected_challenge: dict[str, Any],
    selected_micro: dict[str, Any],
    analysis_result: list[dict[str, Any]],
) -> str:
    return f"""
You are given the selected transport challenge, the selected micro-symptom, and the graph-routing analysis results.

SELECTED_CHALLENGE_JSON:
{json.dumps(selected_challenge, ensure_ascii=False, indent=2)}

SELECTED_MICRO_JSON:
{json.dumps(selected_micro, ensure_ascii=False, indent=2)}

GRAPH_ROUTING_ANALYSIS_JSON:
{json.dumps(analysis_result, ensure_ascii=False, indent=2)}

Task:
- Compare the available routing candidates carefully.
- Select the single best candidate for intervention.
- Return STRICT JSON ONLY using your required planning output schema.
""".strip()


def build_solution_prompt(
    selected_challenge: dict[str, Any],
    selected_micro: dict[str, Any],
    analysis_result: list[dict[str, Any]],
    planning_result: dict[str, Any],
) -> str:
    return f"""
You are given:
1. the selected transport challenge
2. the selected micro-symptom
3. the graph-routing analysis results
4. the planning decision

SELECTED_CHALLENGE_JSON:
{json.dumps(selected_challenge, ensure_ascii=False, indent=2)}

SELECTED_MICRO_JSON:
{json.dumps(selected_micro, ensure_ascii=False, indent=2)}

GRAPH_ROUTING_ANALYSIS_JSON:
{json.dumps(analysis_result, ensure_ascii=False, indent=2)}

PLANNING_RESULT_JSON:
{json.dumps(planning_result, ensure_ascii=False, indent=2)}

Task:
- Design a realistic intervention grounded only in the provided problem and selected candidate.
- Return STRICT JSON ONLY using your required solution output schema.
""".strip()


def build_building_prompt(
    selected_challenge: dict[str, Any],
    selected_micro: dict[str, Any],
    analysis_result: list[dict[str, Any]],
    planning_result: dict[str, Any],
    solution_result: dict[str, Any],
) -> str:
    return f"""
You are given:
1. the selected transport challenge
2. the selected micro-symptom
3. the graph-routing analysis results
4. the planning decision
5. the final solution design

SELECTED_CHALLENGE_JSON:
{json.dumps(selected_challenge, ensure_ascii=False, indent=2)}

SELECTED_MICRO_JSON:
{json.dumps(selected_micro, ensure_ascii=False, indent=2)}

GRAPH_ROUTING_ANALYSIS_JSON:
{json.dumps(analysis_result, ensure_ascii=False, indent=2)}

PLANNING_RESULT_JSON:
{json.dumps(planning_result, ensure_ascii=False, indent=2)}

SOLUTION_RESULT_JSON:
{json.dumps(solution_result, ensure_ascii=False, indent=2)}

Task:
- Convert the solution into Cesium-ready map instruction lines.
- Return ONLY lines in this exact format:
[GEOMETRY_TYPE | COUNT | LABEL | SEARCH_LOCATION | STYLE_HINT | DESCRIPTION]
""".strip()


def analyze_selected_micro(selected_micro: dict[str, Any], selected_city: str) -> list[dict[str, Any]]:
    helper = InfrastructurePlannerOrchestrator(
        name="helper",
        description="helper",
        pipeline=[],
        reviewer=review_agent,
        app_name=APP_NAME,
        session_svc=session_service,
    )
    return helper.run_analysis_from_agent_output(
        {"PRIMARY_MICRO": selected_micro},
        selected_city,
    )


@app.post("/api/start")
async def start(req: StartRequest):
    session_id = str(uuid.uuid4())
    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
        state={"feedback": "", "valid_places_text": "", "target_places": []},
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

    return {
        "ok": True,
        "session_id": session.id,
        "stage": "Place intake",
        "reply": greeting_parsed["feedback"],
        "needs_input": True,
    }


@app.post("/api/chat")
async def chat(req: ChatRequest):
    session_id = req.session_id
    user_message = req.message.strip()

    if session_id not in workflow_state:
        raise HTTPException(status_code=404, detail="Session not found")

    current_session = await session_service.get_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
    )
    state = workflow_state[session_id]
    phase = state["phase"]

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

    if phase == "challenge_selection":
        raw_step_output = state["last_step_output"]
        review_prompt = (
            "STEP NAME: Find needs\n\n"
            f"STEP OUTPUT:\n{raw_step_output}\n\n"
            f"USER RESPONSE: {user_message}"
        )
        review_text = await run_agent_once(review_agent, session_id, review_prompt)
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
            # User wants to regenerate challenges
            feedback = review_result["detail"] or "Regenerate challenges"
            target_places = state.get("target_places", [])
            rerun_prompt = f"The user rejected the previous challenges and gave this feedback: {feedback}. Please generate 3 new transport challenges for {target_places}."
            new_challenges_raw = await run_agent_once(find_needs_agent, session_id, rerun_prompt)
            state["last_step_output"] = new_challenges_raw
            state["last_display_reply"] = format_find_needs_reply(new_challenges_raw)
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Find needs",
                "reply": state["last_display_reply"],
                "needs_input": True,
            }

        # Ensure we have a single object
        selected_output = review_result["final_output"]
        if not selected_output:
            # If the agent didn't extract it but passed, we might be in trouble
            # Try to see if raw_step_output is already a single challenge (unlikely)
            selected_challenge = safe_json_loads(raw_step_output)
            if "CHALLENGE_1" in selected_challenge:
                # Still the multi-choice one, the review agent failed to extract
                return {
                    "ok": True,
                    "session_id": session_id,
                    "stage": "Find needs",
                    "reply": "I couldn't quite catch which one you picked. Could you please specify by number or name?",
                    "needs_input": True,
                }
        else:
            selected_challenge = safe_json_loads(selected_output)

        state["selected_challenge"] = selected_challenge
        selected_city = (state.get("target_places") or [])[0]
        hotspot_result = await run_hotspot_hypothesis_loop(session_id, selected_city, selected_challenge)

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
                "phase": "micro_selection",
                "strict_json": strict_json,
                "last_step_output": json.dumps(strict_json, ensure_ascii=False, indent=2),
                "last_display_reply": format_micro_options(strict_json),
            }
        )
        return {
            "ok": True,
            "session_id": session_id,
            "stage": "Micro hotspot selection",
            "reply": state["last_display_reply"],
            "needs_input": True,
        }

    if phase == "micro_selection":
        raw_step_output = state["last_step_output"]
        review_prompt = (
            "STEP NAME: Select micro-symptom\n\n"
            f"STEP OUTPUT:\n{raw_step_output}\n\n"
            f"USER RESPONSE: {user_message}"
        )
        review_text = await run_agent_once(review_agent, session_id, review_prompt)
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
            # Rerun the hypothesis loop with feedback
            hotspot_result = await run_hotspot_hypothesis_loop(session_id, selected_city, selected_challenge, feedback=feedback)
            
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
            
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Micro hotspot selection",
                "reply": state["last_display_reply"],
                "needs_input": True,
            }

        selected_micro = safe_json_loads(review_result["final_output"])
        selected_city = (state.get("target_places") or [])[0]
        try:
            analysis_result_raw = analyze_selected_micro(selected_micro, selected_city)
            analysis_result = make_analysis_result_for_prompt(analysis_result_raw)
        except Exception as exc:
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

        next_prompt = build_planning_prompt(
            selected_challenge=state["selected_challenge"],
            selected_micro=selected_micro,
            analysis_result=analysis_result,
        )

        next_raw_step_output = await run_agent_once(planning_agent, session_id, next_prompt)

        display_reply = format_step_reply("Plan improvements", next_raw_step_output)

        
        state.update(
            {
                "last_step_output": next_raw_step_output,
                "last_display_reply": next_raw_step_output,
                "last_agent_name": planning_agent.name,
                "output_key": "planning_result",
            }
        )

        return {
            "ok": True,
            "session_id": session_id,
            "stage": "Plan improvements",
            "reply": display_reply,
            "needs_input": True,
        }

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

        display_reply = format_step_reply(step_name, new_raw_step_output)

        state["last_step_output"] = new_raw_step_output
        state["last_display_reply"] = display_reply
        return {
            "ok": True,
            "session_id": session_id,
            "stage": step_name,
            "reply": display_reply,
            "needs_input": True,
        }

    selected_output = review_result["final_output"] or raw_step_output
    if step_name != "Building simulations":
        parsed_selected_output = safe_json_loads(selected_output)
    if step_name == "Plan improvements":
        state["planning_result"] = parsed_selected_output
    elif step_name == "Generate solutions":
        state["solution_result"] = parsed_selected_output
    
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
    if next_step_name == "Generate solutions":
        next_prompt = build_solution_prompt(
            selected_challenge=state["selected_challenge"],
            selected_micro=state["selected_micro"],
            analysis_result=state["analysis_result"],
            planning_result=state["planning_result"],
        )
    elif next_step_name == "Building simulations":
        next_prompt = build_building_prompt(
            selected_challenge=state["selected_challenge"],
            selected_micro=state["selected_micro"],
            analysis_result=state["analysis_result"],
            planning_result=state["planning_result"],
            solution_result=state["solution_result"],
        )
    else:
        next_prompt = f"Proceed with {next_step_name}"

    next_raw_step_output = await run_agent_once(next_step_agent, session_id, next_prompt)

    if next_step_name == "Building simulations":
        enriched_assets = process_agent_assets(next_raw_step_output)
        entities = format_entities(enriched_assets)
        return {
            "ok": True,
            "session_id": session_id,
            "stage": "done",
            "reply": "Planning complete. Switching to map view.",
            "show_map": True,
            "entities": entities,
            "needs_input": False,
        }
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
