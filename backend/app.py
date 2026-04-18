from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
import google.generativeai as genai

from typing import Any
import uuid
import json
import re
import os
import requests

from agent import (
    place_intake_agent,
    find_needs_agent,
    planning_agent,
    solution_agent,
    building_agent,
    review_agent,
    InfrastructurePlannerOrchestrator,
)
from building_agent_helper import process_agent_assets, format_entities

load_dotenv()

# configure Gemini
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
model = genai.GenerativeModel("gemini-1.5-pro")
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

app = FastAPI(title="Infrastructure Planner API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

APP_NAME = "infrastructure_planner"
USER_ID = "user_001"

session_service = InMemorySessionService()
workflow_state = {}


class StartRequest(BaseModel):
    pass


class ChatRequest(BaseModel):
    session_id: str
    message: str


PIPELINE = [
    ("Find needs", find_needs_agent, "top_challenges"),
    ("Plan improvements", planning_agent, "planning_result"),
    ("Generate solutions", solution_agent, "solution_result"),
    ("Building simulations", building_agent, "simulation_result"),
]

def build_find_needs_prompt(target_places: list[str], geospatial_summary: dict) -> str:
    """
    Convert geospatial discovery output into a prompt for find_needs_agent.
    Keeps your existing strict JSON-output behavior intact.
    """
    return f"""
        You are given:
        - TARGET PLACE(S): {target_places}

        GEOSPATIAL EVIDENCE SUMMARY:
        {json.dumps(geospatial_summary, ensure_ascii=False, indent=2)}

        Task:
        Using the geospatial evidence summary above, identify and rank the top 3 most critical transport-related infrastructure challenges for the target place.

        Important:
        - Use the geospatial evidence as the primary grounding source.
        - Keep the output strictly graph-ready and city-compatible.
        - Return the exact JSON structure required by your instructions.
        """.strip()

def build_planning_prompt(analysis_result: list, selected_challenge: dict) -> str:
    return f"""
        You are given the selected transport challenge and the graph-routing analysis results.

        SELECTED_CHALLENGE_JSON:
        {json.dumps(selected_challenge, ensure_ascii=False, indent=2)}

        GRAPH_ROUTING_ANALYSIS_JSON:
        {json.dumps(analysis_result, ensure_ascii=False, indent=2)}

        Task:
        - Compare the available routing candidates carefully.
        - Select the single best candidate for intervention.
        - Return STRICT JSON ONLY using your required planning output schema.
        """.strip()


def build_solution_prompt(
    selected_challenge: dict,
    analysis_result: list,
    planning_result: dict,
) -> str:
    return f"""
        You are given:
        1. the selected transport challenge
        2. the graph-routing analysis results
        3. the planning decision

        SELECTED_CHALLENGE_JSON:
        {json.dumps(selected_challenge, ensure_ascii=False, indent=2)}

        GRAPH_ROUTING_ANALYSIS_JSON:
        {json.dumps(analysis_result, ensure_ascii=False, indent=2)}

        PLANNING_RESULT_JSON:
        {json.dumps(planning_result, ensure_ascii=False, indent=2)}

        Task:
        - Design a realistic intervention grounded only in the provided problem and selected candidate.
        - Return STRICT JSON ONLY using your required solution output schema.
        """.strip()


def build_building_prompt(
    selected_challenge: dict,
    analysis_result: list,
    planning_result: dict,
    solution_result: dict,
) -> str:
    return f"""
        You are given:
        1. the selected transport challenge
        2. the graph-routing analysis results
        3. the planning decision
        4. the final solution design

        SELECTED_CHALLENGE_JSON:
        {json.dumps(selected_challenge, ensure_ascii=False, indent=2)}

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

async def run_agent_once(agent, session_id: str, prompt: str) -> str:
    runner = Runner(
        agent=agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    message = types.Content(
        role="user",
        parts=[types.Part(text=prompt)],
    )

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


def parse_place_result(text: str) -> dict:
    result = {
        "verdict": "RETRY",
        "places": [],
        "feedback": "Please try again.",
    }

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


def parse_review(text: str) -> dict:
    result = {
        "verdict": "REVISE",
        "detail": "",
        "resolved_reference": "",
        "final_output": "",
    }

    verdict_match = re.search(
        r"VERDICT:\s*(PASS|REVISE|REVISE_TOTAL)",
        text,
        re.IGNORECASE,
    )
    if verdict_match:
        result["verdict"] = verdict_match.group(1).strip().upper()

    detail_match = re.search(
        r"(?:REASON|INSTRUCTION):\s*(.+)",
        text,
        re.IGNORECASE,
    )
    if detail_match:
        result["detail"] = detail_match.group(1).strip()

    resolved_match = re.search(
        r"RESOLVED_REFERENCE:\s*(.*)",
        text,
        re.IGNORECASE,
    )
    if resolved_match:
        result["resolved_reference"] = resolved_match.group(1).strip()

    json_match = re.search(
        r"JSON_OUTPUT:\s*(\{.*\})",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if json_match:
        result["final_output"] = json_match.group(1).strip()
        return result

    output_match = re.search(
        r"OUTPUT:\s*(.+)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if output_match:
        result["final_output"] = output_match.group(1).strip()

    return result


def _clean_text(value, fallback="N/A"):
    if value is None:
        return fallback
    if isinstance(value, str):
        value = value.strip()
        return value if value else fallback
    return str(value)


def extract_challenge_json_blocks(text: str):
    """
    Extract challenge dicts from find_needs_agent output.

    Primary path:
    - Look for fenced ```json ... ``` first.

    Fallback path:
    - Scan all JSON objects in the text and detect either:
      A) wrapper object with CHALLENGE_1 / CHALLENGE_2 / CHALLENGE_3
      B) standalone challenge dicts containing CHALLENGE_THEME
    """

    fenced_match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced_match:
        try:
            obj = json.loads(fenced_match.group(1))
            wrapper_keys = [
                k for k in obj
                if k.startswith("CHALLENGE_") and k[len("CHALLENGE_"):].isdigit()
            ]
            if wrapper_keys:
                return [
                    obj[k]
                    for k in sorted(wrapper_keys)
                    if isinstance(obj[k], dict) and "CHALLENGE_THEME" in obj[k]
                ]
        except json.JSONDecodeError:
            pass

    def iter_json_objects(src):
        i = 0
        while i < len(src):
            if src[i] == "{":
                depth = 0
                in_str = False
                esc = False
                j = i

                while j < len(src):
                    ch = src[j]

                    if esc:
                        esc = False
                    elif ch == "\\" and in_str:
                        esc = True
                    elif ch == '"':
                        in_str = not in_str
                    elif not in_str:
                        if ch == "{":
                            depth += 1
                        elif ch == "}":
                            depth -= 1
                            if depth == 0:
                                try:
                                    yield json.loads(src[i:j + 1])
                                except json.JSONDecodeError:
                                    pass
                                i = j
                                break
                    j += 1
            i += 1

    all_objects = list(iter_json_objects(text))

    for obj in all_objects:
        if not isinstance(obj, dict):
            continue

        wrapper_keys = [
            k for k in obj
            if k.startswith("CHALLENGE_") and k[len("CHALLENGE_"):].isdigit()
        ]
        if wrapper_keys:
            return [
                obj[k]
                for k in sorted(wrapper_keys)
                if isinstance(obj[k], dict) and "CHALLENGE_THEME" in obj[k]
            ]

    return [
        obj for obj in all_objects
        if isinstance(obj, dict) and "CHALLENGE_THEME" in obj
    ][:3]


def _extract_micro_summary(micro: dict) -> str:
    if not isinstance(micro, dict):
        return "N/A"

    symptom = _clean_text(micro.get("SYMPTOM"), "")
    if symptom:
        return symptom

    micro_type = _clean_text(micro.get("TYPE"), "").lower()

    if micro_type == "transit_node":
        station = _clean_text(micro.get("STATION_OR_LINE"), "")
        location = _clean_text(micro.get("LOCATION_LABEL"), "")
        if station and location:
            return f"Critical transit-node issue centered on {station} at {location}."
        if station:
            return f"Critical transit-node issue centered on {station}."
        if location:
            return f"Critical transit-node issue at {location}."

    if micro_type == "freight_route":
        primary_route = _clean_text(micro.get("PRIMARY_ROUTE"), "")
        secondary_route = _clean_text(micro.get("SECONDARY_ROUTE"), "")
        location = _clean_text(micro.get("LOCATION_LABEL"), "")
        parts = [p for p in [primary_route, secondary_route, location] if p]
        if parts:
            return f"Freight corridor issue involving {' | '.join(parts)}."

    road_1 = _clean_text(micro.get("ROAD_1"), "")
    road_2 = _clean_text(micro.get("ROAD_2"), "")
    location = _clean_text(micro.get("LOCATION_LABEL"), "")

    if road_1 and road_2 and location:
        return f"Issue around {location}, affecting connectivity between {road_1} and {road_2}."
    if road_1 and road_2:
        return f"Issue affecting connectivity between {road_1} and {road_2}."
    if location:
        return f"Issue centered on {location}."

    return "N/A"


def format_challenges(challenges):
    if not challenges:
        return "No challenge data could be extracted."

    output = []

    for i, data in enumerate(challenges, start=1):
        if not isinstance(data, dict):
            continue

        theme = _clean_text(data.get("CHALLENGE_THEME"))
        cause = _clean_text(data.get("MACRO_ROOT_CAUSE"))

        primary = data.get("PRIMARY_MICRO", {})
        secondary = data.get("SECONDARY_MICRO", {})

        primary_summary = _extract_micro_summary(primary)
        secondary_summary = _extract_micro_summary(secondary)

        block = (
            f"{i}. {theme}\n"
            f"{cause}\n\n"
            f"MACRO_ROOT_CAUSE = {cause}\n"
            f"PRIMARY_MICRO = {primary_summary}\n"
            f"SECONDARY_MICRO = {secondary_summary}"
        )

        output.append(block)

    final_output = "\n\n".join(output)
    final_output += "\n\nWhich challenge would you like to explore further?"
    return final_output


def format_find_needs_reply(raw_step_output: str) -> str:
    parsed_challenges = extract_challenge_json_blocks(raw_step_output)
    if parsed_challenges:
        return format_challenges(parsed_challenges)
    return raw_step_output


def safe_json_loads(value):
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        raise ValueError("Expected JSON string, dict, or list.")
    return json.loads(value)

async def start_planning_phase(session_id: str, current_session):
    step_name, step_agent, output_key = PIPELINE[0]

    target_places = current_session.state.get("target_places", [])
    if not target_places:
        raise HTTPException(status_code=400, detail="No target places found in session state.")

    geospatial_results = []
    for city in target_places:
        try:
            summary = await run_geospatial_discovery(city)
        except Exception as e:
            summary = {
                "target_place": city,
                "analysis_scope": "city-scale transport hotspot discovery",
                "hotspots": [],
                "data_sources": [],
                "notes": [f"Geospatial discovery failed: {str(e)}"],
            }
        geospatial_results.append(summary)

    geospatial_bundle = {
        "target_places": target_places,
        "city_count": len(target_places),
        "city_summaries": geospatial_results,
    }

    prompt = build_find_needs_prompt(target_places, geospatial_bundle)

    raw_step_output = await run_agent_once(step_agent, session_id, prompt)
    display_reply = format_find_needs_reply(raw_step_output)

    workflow_state[session_id] = {
        "phase": "planning",
        "step_index": 0,
        "last_step_output": raw_step_output,
        "last_display_reply": display_reply,
        "last_agent_name": step_agent.name,
        "output_key": output_key,
        "geospatial_summary": geospatial_bundle,
        "find_needs_prompt": prompt,
        "target_places": target_places
    }

    current_session.state["geospatial_summary"] = geospatial_bundle

    return {
        "ok": True,
        "session_id": session_id,
        "stage": step_name,
        "reply": display_reply,
        "needs_input": True,
        "debug_geospatial_summary": geospatial_bundle,
    }

def get_places_context(city: str) -> dict:
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"

    queries = [
        f"traffic congestion {city}",
        f"major roads {city}",
        f"transport hubs {city}"
    ]

    results = []

    for q in queries:
        params = {
            "query": q,
            "key": GOOGLE_MAPS_API_KEY
        }

        try:
            response = requests.get(url, params=params).json()
            for r in response.get("results", [])[:3]:
                results.append({
                    "name": r.get("name"),
                    "address": r.get("formatted_address")
                })
        except Exception:
            continue

    return {
        "city": city,
        "places": results[:10]
    }

def build_geospatial_prompt(city: str, places_context: dict | None = None) -> str:
    context_block = ""
    if places_context:
        context_block = f"\nPlaces Data:\n{json.dumps(places_context, indent=2)}\n"

    return f"""
        You are a geospatial transport hotspot discovery system.

        Target place: {city}

        {context_block}

        Task:
        Identify up to 5 transport-related hotspots within or directly adjacent to the city boundary.

        Focus only on:
        - congestion corridors
        - junction bottlenecks
        - bus reliability corridors
        - freight routes
        - transit nodes

        Rules:
        - Do NOT invent locations
        - Use realistic, known roads or areas
        - Stay within the city scope
        - Do NOT propose solutions
        - Do NOT generate routing labels

        Return STRICT JSON ONLY:

        {{
        "target_place": "{city}",
        "analysis_scope": "city-scale transport hotspot discovery",
        "hotspots": [
            {{
            "hotspot_type": "corridor | junction | freight_route | transit_node",
            "location_label": "...",
            "evidence_summary": "...",
            "supporting_signals": ["...", "..."],
            "priority_hint": "low | medium | high",
            "confidence": "low | medium | high"
            }}
        ],
        "data_sources": ["gemini"],
        "notes": []
        }}
        """.strip()

def clean_json_text(text: str) -> str:
    text = text.strip()

    # remove ```json blocks
    if text.startswith("```"):
        text = re.sub(r"```[a-zA-Z]*", "", text)
        text = text.replace("```", "")

    return text.strip()

async def run_geospatial_discovery(city: str) -> dict[str, Any]:
    """
    Hybrid geospatial discovery using:
    - Gemini reasoning
    - optional Google Maps grounding
    """

    # -------------------------
    # Step 1: get map context
    # -------------------------
    try:
        places_context = get_places_context(city)
    except Exception:
        places_context = None

    # -------------------------
    # Step 2: build prompt
    # -------------------------
    prompt = build_geospatial_prompt(city, places_context)

    # -------------------------
    # Step 3: call Gemini
    # -------------------------
    try:
        response = model.generate_content(prompt)
        raw_text = response.text

    except Exception as e:
        return {
            "target_place": city,
            "analysis_scope": "city-scale transport hotspot discovery",
            "hotspots": [],
            "data_sources": ["fallback"],
            "notes": [f"Gemini call failed: {str(e)}"]
        }

    # -------------------------
    # Step 4: parse JSON
    # -------------------------
    cleaned = clean_json_text(raw_text)

    try:
        parsed = json.loads(cleaned)
        return parsed

    except Exception:
        return {
            "target_place": city,
            "analysis_scope": "city-scale transport hotspot discovery",
            "hotspots": [],
            "data_sources": ["gemini"],
            "notes": [
                "Failed to parse Gemini output",
                f"Raw output: {cleaned[:300]}"
            ]
        }
    
def make_analysis_result_for_prompt(raw_results):
    cleaned = []
    for raw in raw_results:
        cleaned.append({
            "selected_micro_source": raw.get("selected_micro_source"),
            "selected_micro_type": raw.get("selected_micro_type"),
            "selected_micro_symptom": raw.get("selected_micro_symptom"),
            "selected_micro_location_label": raw.get("selected_micro_location_label"),
            "mode": raw.get("mode"),
            "city_query": raw.get("city_query"),
            "candidates": raw.get("candidates", []),
        })
    return cleaned


@app.post("/api/start")
async def start(req: StartRequest):
    session_id = str(uuid.uuid4())

    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
        state={
            "feedback": "",
            "valid_places_text": "",
            "target_places": [],
        },
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

    workflow_state[session.id] = {
        "phase": "intake",
    }

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

    phase = workflow_state[session_id]["phase"]

    # ===== Phase 1: intake =====
    if phase == "intake":
        intake_prompt = f"""
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

            planning_intro = (
                f'Location confirmed: {", ".join(parsed["places"])}. '
                f'Moving to the planning phase.'
            )

            planning_response = await start_planning_phase(session_id, current_session)
            planning_response["reply"] = planning_intro + "\n\n" + planning_response["reply"]
            return planning_response

        return {
            "ok": True,
            "session_id": session_id,
            "stage": "Place intake",
            "reply": parsed["feedback"],
            "needs_input": True,
        }

    # ===== Phase 2: planning =====
    state = workflow_state[session_id]
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
            f'The previous {step_name} output was rejected. '
            f'Follow this specific instruction to revise it: "{feedback}"'
        )

        new_raw_step_output = await run_agent_once(step_agent, session_id, rerun_prompt)

        display_reply = new_raw_step_output
        if step_name == "Find needs":
            display_reply = format_find_needs_reply(new_raw_step_output)

        workflow_state[session_id]["last_step_output"] = new_raw_step_output
        workflow_state[session_id]["last_display_reply"] = display_reply

        return {
            "ok": True,
            "session_id": session_id,
            "stage": step_name,
            "reply": display_reply,
            "needs_input": True,
        }

    selected_output = review_result["final_output"] or raw_step_output

    if step_name == "Find needs":
        try:
            json_output = safe_json_loads(selected_output)
        except Exception:
            parsed_blocks = extract_challenge_json_blocks(selected_output)
            if len(parsed_blocks) == 1:
                json_output = parsed_blocks[0]
            else:
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Could not parse selected challenge into a single JSON object. "
                        "Make sure review_agent returns one selected challenge in JSON_OUTPUT."
                    ),
                )

        helper = InfrastructurePlannerOrchestrator(
            name="helper",
            description="helper",
            pipeline=[],
            reviewer=review_agent,
            app_name=APP_NAME,
            session_svc=session_service,
        )

        workflow_state[session_id]["selected_challenge"] = json_output
        target_places = workflow_state[session_id].get("target_places") or []

        if not target_places:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No target places found in session state during routing analysis. "
                    "Please restart the workflow and select a city again."
                ),
            )

        selected_city = target_places[0]

        analysis_result_raw = helper.run_analysis_from_agent_output(json_output, selected_city)
        workflow_state[session_id]["analysis_result_raw"] = analysis_result_raw
        workflow_state[session_id]["analysis_result"] = make_analysis_result_for_prompt(analysis_result_raw)

    elif step_name == "Plan improvements":
        workflow_state[session_id]["planning_result"] = safe_json_loads(selected_output)

    elif step_name == "Generate solutions":
        workflow_state[session_id]["solution_result"] = safe_json_loads(selected_output)

    elif step_name == "Building simulations":
        enriched_assets = process_agent_assets(selected_output)
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

    if next_step_name == "Plan improvements":
        next_prompt = build_planning_prompt(
            analysis_result=workflow_state[session_id]["analysis_result"],
            selected_challenge=workflow_state[session_id]["selected_challenge"],
        )

    elif next_step_name == "Generate solutions":
        next_prompt = build_solution_prompt(
            selected_challenge=workflow_state[session_id]["selected_challenge"],
            analysis_result=workflow_state[session_id]["analysis_result"],
            planning_result=workflow_state[session_id]["planning_result"],
        )

    elif next_step_name == "Building simulations":
        next_prompt = build_building_prompt(
            selected_challenge=workflow_state[session_id]["selected_challenge"],
            analysis_result=workflow_state[session_id]["analysis_result"],
            planning_result=workflow_state[session_id]["planning_result"],
            solution_result=workflow_state[session_id]["solution_result"],
        )
    else:
        next_prompt = f"Proceed with {next_step_name}"

    next_raw_step_output = await run_agent_once(next_step_agent, session_id, next_prompt)

    next_display_reply = next_raw_step_output
    if next_step_name == "Find needs":
        next_display_reply = format_find_needs_reply(next_raw_step_output)

    workflow_state[session_id].update({
        "phase": "planning",
        "step_index": next_index,
        "last_step_output": next_raw_step_output,     
        "last_display_reply": next_display_reply,     
        "last_agent_name": next_step_agent.name,
        "output_key": next_output_key,
    })

    return {
        "ok": True,
        "session_id": session_id,
        "stage": next_step_name,
        "reply": next_display_reply,
        "needs_input": True,
    }