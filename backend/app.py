from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
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
)
from building_agent_helper import process_agent_assets, format_entities

load_dotenv()

app = FastAPI(title="Infrastructure Planner API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
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
        "final_output": "",
    }

    verdict_match = re.search(r"VERDICT:\s*(PASS|REVISE|REVISE_TOTAL)", text, re.IGNORECASE)
    if verdict_match:
        result["verdict"] = verdict_match.group(1).strip().upper()

    detail_match = re.search(r"(?:REASON|INSTRUCTION):\s*(.+)", text)
    if detail_match:
        result["detail"] = detail_match.group(1).strip()

    json_match = re.search(r"JSON_OUTPUT:\s*(\{.*\})", text, re.DOTALL)
    if json_match:
        result["final_output"] = json_match.group(1).strip()
        return result

    output_match = re.search(r"OUTPUT:\s*(.+)", text, re.DOTALL)
    if output_match:
        result["final_output"] = output_match.group(1).strip()

    return result


def extract_challenge_json_blocks(text: str):
    matches = re.findall(
        r"JSON_OUTPUT\s*=\s*(\{.*?\})\s*(?=### CHALLENGE|\*\*FINAL_QUESTION|\Z)",
        text,
        re.DOTALL,
    )

    parsed = []
    for match in matches:
        try:
            parsed.append(json.loads(match))
        except json.JSONDecodeError as e:
            print("Failed to parse challenge JSON:", e)

    return parsed


def format_challenges(challenges):
    output = []

    for i, data in enumerate(challenges, start=1):
        theme = data.get("CHALLENGE_THEME", "")
        cause = data.get("MACRO_ROOT_CAUSE", "")

        primary = data.get("PRIMARY_MICRO", {})
        symptom = primary.get("SYMPTOM", "")
        location = primary.get("LOCATION_LABEL", "")
        road1 = primary.get("ROAD_1", "")
        road2 = primary.get("ROAD_2", "")

        roads = f"{road1} + {road2}" if road2 and road2 != "N/A" else road1

        block = f"""
Challenge {i}: {theme}

Macro root cause: {cause}

Primary location: {location}
Roads: {roads}
Symptom: {symptom}
""".strip()

        output.append(block)

    final_output = "\n\n".join(output)
    final_output += "\n\nWhich challenge would you like to explore further?"
    return final_output


async def start_planning_phase(session_id: str, current_session):
    step_name, step_agent, output_key = PIPELINE[0]
    prompt = f'Identify and rank the top 3 infrastructure-related challenges for {current_session.state["target_places"]}'

    step_output = await run_agent_once(step_agent, session_id, prompt)

    workflow_state[session_id] = {
        "phase": "planning",
        "step_index": 0,
        "last_step_output": step_output,
        "last_agent_name": step_agent.name,
        "output_key": output_key,
    }

    parsed_challenges = extract_challenge_json_blocks(step_output)

    return {
        "ok": True,
        "session_id": session_id,
        "stage": step_name,
        "reply": format_challenges(parsed_challenges) if parsed_challenges else step_output,
        "needs_input": True,
    }


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

            planning_intro = f'Location confirmed: {", ".join(parsed["places"])}. Moving to the planning phase.'

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
    step_output = state["last_step_output"]

    review_prompt = (
        f"STEP NAME: {step_name}\n\n"
        f"STEP OUTPUT:\n{step_output}\n\n"
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
        rerun_prompt = f'The previous {step_name} output was rejected. Follow this specific instruction to revise it: "{feedback}"'
        new_step_output = await run_agent_once(step_agent, session_id, rerun_prompt)

        workflow_state[session_id]["last_step_output"] = new_step_output

        return {
            "ok": True,
            "session_id": session_id,
            "stage": step_name,
            "reply": new_step_output,
            "needs_input": True,
        }

    selected_output = review_result["final_output"] or step_output

    if step_name == "Find needs":
        json_output = json.loads(selected_output) if isinstance(selected_output, str) else selected_output

        helper = InfrastructurePlannerOrchestrator(
            name="helper",
            description="helper",
            pipeline=[],
            reviewer=review_agent,
            app_name=APP_NAME,
            session_svc=session_service,
        )

        analysis_result = helper.run_analysis_from_agent_output(
            json_output,
            current_session.state["target_places"][0]
        )
        current_session.state["analysis_result"] = analysis_result

    elif step_name == "Plan improvements":
        current_session.state["planning_result"] = json.loads(selected_output)

    elif step_name == "Generate solutions":
        current_session.state["solution_result"] = json.loads(selected_output)

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
        next_prompt = f'Here is the details = {current_session.state["analysis_result"]}'
    elif next_step_name == "Generate solutions":
        next_prompt = f'Here is the plan {current_session.state["planning_result"]} with other details {current_session.state["analysis_result"]}'
    elif next_step_name == "Building simulations":
        next_prompt = f'Here is the recommended intervention {current_session.state["solution_result"]} with other details {current_session.state["planning_result"]} and {current_session.state["analysis_result"]}'
    else:
        next_prompt = f"Proceed with {next_step_name}"

    next_step_output = await run_agent_once(next_step_agent, session_id, next_prompt)

    workflow_state[session_id] = {
        "phase": "planning",
        "step_index": next_index,
        "last_step_output": next_step_output,
        "last_agent_name": next_step_agent.name,
        "output_key": next_output_key,
    }

    return {
        "ok": True,
        "session_id": session_id,
        "stage": next_step_name,
        "reply": next_step_output,
        "needs_input": True,
    }