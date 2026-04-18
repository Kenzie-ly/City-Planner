from google.adk.agents import LlmAgent, BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from typing import AsyncGenerator
from pydantic import ConfigDict
import asyncio
from dotenv import load_dotenv
from google.adk.tools.google_search_tool import GoogleSearchTool
import re
from FindRoads import run_city_road_connection_analysis
from building_agent_helper import process_agent_assets, format_entities
import json

# Load API key from .env file
load_dotenv()


# ── Step agents for later phases ──────────────────────────────────────────────

place_intake_agent = LlmAgent(
    name="place_intake_agent",
    model="gemini-3-flash-preview",
    description="Collects one or two Malaysian cities/towns from the user with a natural feedback loop.",
    instruction="""
You are the intake agent for an infrastructure planning assistant.

Your job:
- greet the user naturally
- ask for one or two Malaysian cities or towns to analyze
- inspect the user's latest message
- decide whether the message contains acceptable location inputs

Important rules:
- Accept any real city or town in Malaysia
- Accept at most two places
- Reject places that are too broad, such as a whole country or a state
- Reject places that are too specific, such as landmarks, stations, roads, buildings, or very small areas
- If part of the input is valid and part is not, return RETRY and explain naturally
- Keep the feedback concise and natural

Definition:
- Input that needs confirmation is an input that has acceptable input and unacceptable input


Examples of acceptable inputs:
- Kuala Lumpur
- Putrajaya
- Seri Kembangan
- Cyberjaya
- Johor Bahru

Examples of inputs that are too broad:
- Malaysia
- Selangor
- Johor

Examples of inputs that are too specific:
- Bukit Bintang
- KL Sentral
- Pavilion Kuala Lumpur

Output format exactly:

VERDICT: <SUCCESS or RETRY>
PLACES: <place1|place2 or empty>
FEEDBACK: <natural message to show to the user or can also be used for asking confirmation>
""",
)

find_needs_agent = LlmAgent(
    name="find_needs_agent",
    model="gemini-3-flash-preview",
    description="Analyzes selected cities/towns and identifies the top infrastructure-related challenges that need government attention.",
    instruction="""
        You are the Lead Transport Systems Analysis Supervisor for Malaysia.
        You will be given a TARGET PLACE by the user (for example: "Kuala Lumpur").
        Your task is to identify the top 3 most critical TRANSPORT-RELATED infrastructure problems in that place using real-world data.

        ## SCOPE (STRICT)

        You must ONLY focus on:
        * highway congestion assessment
        * urban corridor performance monitoring
        * junction bottleneck analysis
        * transit bottleneck detection (bus + rail)
        * bus network reliability and delays
        * freight corridor performance
        * road expansion screening
        * transport infrastructure needs

        Do NOT discuss unrelated infrastructure domains.


        ## TOOL USAGE (MANDATORY)

        You MUST use the Google Search tool before answering.
        Search for:
        * traffic congestion reports
        * transport studies
        * government transport data
        * transit reliability issues
        * freight/logistics corridor problems

        Use sources within the last 5 years.
        Prefer:
        1. government / official transport authorities
        2. credible news with specific locations
        3. transport research or analysis


        ## CORE TASK

        Identify the TOP 3 most severe TRANSPORT problems in the TARGET PLACE.
        Each challenge must include:
        * a MACRO ROOT CAUSE
        * two MICRO SYMPTOMS
        * each MICRO SYMPTOM must be usable for graph-based network analysis


        ## CRITICAL: GRAPH COMPATIBILITY RULE

        Your output will be consumed by a road network analysis system.
        Therefore, EVERY MICRO SYMPTOM must be **graph-ready**.
        ### MICRO SYMPTOM TYPES
        1. corridor
        2. junction
        3. freight_route
        4. transit_node
        If the TARGET PLACE is a city (for example: "Kuala Lumpur"), then all selected MICRO SYMPTOMS must be analyzable within a city-scale road graph for that city.


        ## ROUTING LABEL REQUIREMENT (VERY IMPORTANT)

        For EVERY road-based MICRO SYMPTOM, you MUST provide BOTH:

        1. HUMAN LABEL (readable)
        2. ROUTING LABELS (machine-usable)

        ### REQUIRED FIELDS

        For corridor / junction:

        * LOCATION_LABEL → human-readable (e.g., "MRR2 Kepong Segment")

        * ROAD_1 → readable name

        * ROAD_2 → readable name

        * ROUTING_LABEL_1 → most OSM-friendly version of ROAD_1

        * ROUTING_LABEL_2 → most OSM-friendly version of ROAD_2

        * ROUTING_ALIASES_1 → list of 1-3 alternative names

        * ROUTING_ALIASES_2 → list of 1-3 alternative names

        ### RULES

        * Prefer names commonly used in maps (OpenStreetMap, Google Maps)
        * If a road has an abbreviation, include it (e.g., MRR2, LDP)
        * If a route number exists, include it (e.g., FT2, E1)
        * Avoid vague names like:

        * "city corridor"
        * "downtown segment"
        * Do NOT invent unsupported road names


        ## FREIGHT ROUTE RULE

        For TYPE = freight_route:

        * PRIMARY_ROUTE → readable name

        * SECONDARY_ROUTE → optional

        * ROUTING_LABEL_1 → main freight route

        * ROUTING_ALIASES_1 → aliases (route number, Malay name, abbreviation)


        ## TRANSIT NODE RULE

        For TYPE = transit_node:
        * STATION_OR_LINE must be provided
        * Do NOT force road names


        ## CITY SCOPE RULE (CRITICAL)

        If TARGET PLACE is a city (e.g., Kuala Lumpur):

        * Prefer MICRO SYMPTOMS located WITHIN or DIRECTLY ADJACENT to the city boundary
        * Avoid selecting large regional corridors unless they are:

        * clearly connected to the city network
        * and still analyzable within the city graph


        ## BUS SYSTEM PRIORITY

        You MUST consider bus-related issues such as:
        * unreliable bus corridors
        * lack of bus lanes
        * poor last-mile connectivity
        * bus congestion


        ## HARD RULES

        * ONLY discuss TARGET PLACE
        * ONLY transport-related problems
        * NO solutions
        * NO invented roads
        * NO vague locations
        * EVERY MICRO must be graph-ready

        
        ## CANONICAL ROUTING NAME RULE (CRITICAL):

        For ROUTING_LABEL_1 and ROUTING_LABEL_2:
        - Output the SHORTEST widely used map-searchable road name.
        - Prefer the name that is most likely to appear directly in OpenStreetMap road data.
        - If a road is commonly known by an abbreviation, use that abbreviation as the routing label.
        - If the formal long name is less common than the short name, use the short name for ROUTING_LABEL and place the long form in ROUTING_ALIASES.
        Examples:
        - use "MRR2" instead of "Kuala Lumpur Middle Ring Road 2"
        - use "Lebuhraya Persekutuan" instead of "Federal Route 2" if that is the dominant map name
        - use "LDP" if that is the most recognizable search form, and include the full name in aliases
        ROUTING_LABEL must be the single best name for downstream matching.
        ROUTING_ALIASES should contain alternate official or common forms.


        ## OUTPUT FORMAT (STRICT — DO NOT DEVIATE)

        You MUST return exactly ONE JSON object with this top-level structure.
        Do NOT use ```json fences. Do NOT split into multiple blocks.
        Do NOT add any text before or after the JSON.

        {
        "CHALLENGE_1": {
            "CHALLENGE_THEME": "<text>",
            "MACRO_ROOT_CAUSE": "<text>",
            "PRIMARY_MICRO": {
            "SYMPTOM": "<text>",
            "TYPE": "<corridor | junction | freight_route | transit_node>",
            "LOCATION_LABEL": "<human-readable>",
            "ROAD_1": "<if applicable, else null>",
            "ROAD_2": "<if applicable, else null>",
            "ROUTING_LABEL_1": "<OSM-friendly name>",
            "ROUTING_LABEL_2": "<OSM-friendly name, else null>",
            "ROUTING_ALIASES_1": ["<alias1>", "<alias2>"],
            "ROUTING_ALIASES_2": ["<alias1>", "<alias2>"],
            "PRIMARY_ROUTE": "<if freight, else null>",
            "SECONDARY_ROUTE": "<if freight, else null>",
            "STATION_OR_LINE": "<if transit, else null>"
            },
            "SECONDARY_MICRO": {
            "SYMPTOM": "<text>",
            "TYPE": "<corridor | junction | freight_route | transit_node>",
            "LOCATION_LABEL": "<human-readable>",
            "ROAD_1": "<if applicable, else null>",
            "ROAD_2": "<if applicable, else null>",
            "ROUTING_LABEL_1": "<OSM-friendly name>",
            "ROUTING_LABEL_2": "<OSM-friendly name, else null>",
            "ROUTING_ALIASES_1": ["<alias1>", "<alias2>"],
            "ROUTING_ALIASES_2": ["<alias1>", "<alias2>"],
            "PRIMARY_ROUTE": "<if freight, else null>",
            "SECONDARY_ROUTE": "<if freight, else null>",
            "STATION_OR_LINE": "<if transit, else null>"
            }
        },
        "CHALLENGE_2": { <same structure as CHALLENGE_1> },
        "CHALLENGE_3": { <same structure as CHALLENGE_1> }
        }

        FINAL_QUESTION: Ask the user to select ONE challenge.
        """,
    tools=[
        GoogleSearchTool()
    ],
    output_key="top_challenges",
)

planning_agent = LlmAgent(
    name="planning_agent",
    model="gemini-3-flash-preview",
    description="Evaluates transport intervention candidates and selects the best improvement option for the selected problem.",
    instruction = """
        You are a Transport Planning Decision Agent for Malaysia.
        You will receive:

        A structured transport problem
        A list of candidate routes generated from a graph-based road analysis system
        Your task is to:

        Compare all candidates carefully
        Select the MOST appropriate candidate for addressing the problem
        Identify the most suitable intervention type
        Justify your decision using ONLY the provided data
        Explain tradeoffs between candidates
        Assign a realistic confidence level
        CRITICAL RULES (MUST FOLLOW)
        You MUST only choose from the provided candidates.
        You MUST NOT invent new roads, paths, or geometry.
        You MUST base your reasoning ONLY on:
        candidate attributes
        problem description
        You MUST NOT assume real-world conditions not present in the data.
        You MUST NOT overclaim engineering certainty.
        DECISION LOGIC GUIDELINES
        You MUST consider:

        Problem type (junction, corridor, etc.)
        Path length
        Number of segments
        Connector count
        Road class composition
        Whether the candidate reflects:
        a direct conflict point (junction)
        a meaningful corridor (multi-segment)
        a detour through lower-class roads
        INTERVENTION TYPE RULES
        You MUST select one of:

        junction_redesign
        corridor_improvement
        local_connector_upgrade
        bus_priority_corridor
        freight_access_improvement
        road_expansion_screening
        mixed_intervention
        Guidance:

        If the problem is "junction" and the candidate is short/direct → prefer junction_redesign
        If the candidate spans multiple segments → consider corridor-based interventions
        If the candidate relies heavily on tertiary roads → treat as weaker option
        CONFIDENCE RULES
        HIGH → one candidate clearly dominates
        MEDIUM → reasonable but not definitive
        LOW → ambiguity or weak evidence
        Do NOT assign HIGH unless strongly justified.
        OUTPUT FORMAT (STRICT JSON ONLY)
        {
        "selected_candidate_id": "<candidate_id>",
        "intervention_type": "",
        "decision_summary": "<1-2 sentence explanation>",
        "reasons": [
        "<reason 1 grounded in data>",
        "<reason 2 grounded in data>",
        "<reason 3 grounded in data>"
        ],
        "tradeoffs": [
        "<tradeoff 1>",
        "<tradeoff 2>"
        ],
        "priority_level": "<low | medium | high>",
        "confidence": "<low | medium | high>"
        }
        FINAL CHECK (MANDATORY)
        Before output:

        Ensure selected_candidate_id exists in input
        Ensure all reasoning refers to actual candidate data
        Ensure no hallucinated roads or features are introduced
    """,
    output_key="planning_result"
)

solution_agent = LlmAgent(
    name="solution_agent",
    model="gemini-3-flash-preview",
    description="Evaluates transport intervention candidates and selects the best improvement option for the selected problem.",
    instruction = """
        You are a Transport Infrastructure Solution Designer.

        You will receive:
        A selected transport problem
        The chosen candidate route
        The intervention type from a planning decision

        Your task is to:
        Translate the selected candidate into a REALISTIC and ACTIONABLE intervention
        Describe exactly WHAT should be changed, WHERE, and HOW
        Ensure all actions are grounded in the provided road structure

        
        CRITICAL RULES (MUST FOLLOW)
        You MUST base your solution ONLY on:
        selected candidate roads
        provided problem description
        You MUST NOT invent new roads or geometry.
        You MUST NOT assume unavailable data (e.g., signal timing details).
        You MUST NOT produce generic planning statements.


        ACTION QUALITY RULE (VERY IMPORTANT)
        Each proposed action MUST include:
        WHAT is changed (lane, merge, flow, restriction)
        WHERE it happens (specific road, connector, or movement)
        HOW it affects traffic movement

        DO NOT write:
        "improve traffic flow"
        "optimize road design"
        "reduce congestion"


        INSTEAD write:
        "Extend the merge section on by reallocating upstream lane space..."
        "Separate turning traffic from through-flow at ..."

        INTERVENTION-SPECIFIC GUIDANCE

        If intervention_type = junction_redesign:
        Focus on:
        merge/diverge behavior
        turning movement conflicts
        lane channelization
        connector structure

        If intervention_type = corridor_improvement:
        Focus on:
        lane allocation
        flow prioritization
        access control

        OUTPUT FORMAT (STRICT JSON ONLY)
        {
        "solution_title": "",

        "solution_type": "",

        "target_geometry": {

        "focus_type": "<junction_node | corridor_segment>",

        "location": "<LOCATION_LABEL>",

        "primary_roads": ["<road_1>", "<road_2>"],

        "affected_segments": [""]

        },

        "proposed_actions": [

        "<action 1: WHAT + WHERE + HOW>",

        "<action 2: WHAT + WHERE + HOW>",

        "<action 3: WHAT + WHERE + HOW>",

        "<action 4: WHAT + WHERE + HOW>"

        ],

        "expected_effect": [

        "<effect 1>",

        "<effect 2>",

        "<effect 3>"

        ],

        "implementation_complexity": "<low | medium | high>",

        "confidence": "<low | medium | high>"

        }

        FINAL CHECK (MANDATORY)

        Before output:
        Ensure all roads exist in candidate input
        Ensure all actions reference real elements from the candidate
        Ensure no vague or generic statements are present
        Ensure actions are physically meaningful
    """,
    output_key="solution_result"
)

building_agent = LlmAgent(
    name="building_agent",
    model="gemini-3-flash-preview",
    description="Converts a selected infrastructure option into structured map scene data for CesiumJS.",
    instruction="""
        You are a Transport Infrastructure Map Building Agent.

        You will receive:
        1. a selected transport problem
        2. a chosen candidate path
        3. a solution design describing the recommended intervention

        Your task is to convert that solution into map-build instructions for a 3D city map.

        You must output ONLY map instructions, not reasoning.


        ## CORE RESPONSIBILITY

        Your job is to identify the visual map elements needed to represent the solution clearly.

        These may include:

        * POINT → for intervention nodes, kiosks, stations, conflict points
        * POLYLINE → for corridors, routes, connectors, lane-priority segments
        * POLYGON → for zones, treatment areas, widened junction footprints, interchange treatment areas
        * LABEL → for important annotations when needed


        ## STRICT RULES

        1. You MUST use only locations, roads, corridors, junctions, and nodes explicitly present in the input.
        2. You MUST NOT invent new roads, new places, or unsupported geometry.
        3. You MUST keep the number of map objects minimal but meaningful.
        4. Each object must help explain the intervention visually.
        5. Use SEARCH_LOCATION as a human-readable place or road name that can be used later for geocoding or spatial lookup.
        6. STYLE_HINT must be short and practical.
        7. DESCRIPTION must explain what the object means on the map.


        ## OUTPUT FORMAT (STRICT)

        Return one line per map object using EXACTLY this format:

        [GEOMETRY_TYPE | COUNT | LABEL | SEARCH_LOCATION | STYLE_HINT | DESCRIPTION]

        Allowed GEOMETRY_TYPE values:

        * POINT
        * POLYLINE
        * POLYGON
        * LABEL

        COUNT rules:

        * Use x1, x2, x3, etc.
        * COUNT means how many similar objects of this type are needed

        STYLE_HINT examples:

        * color:red, width:thick
        * color:orange, size:large
        * color:blue, opacity:0.4
        * color:yellow, width:medium, dashed:true


        ## MAPPING GUIDELINES

        For junction_redesign:

        * usually include:

        * POINT for bottleneck node
        * POLYLINE for affected connector or road movement
        * POLYGON for intervention footprint if applicable

        For corridor_improvement:

        * usually include:

        * POLYLINE for improved corridor
        * POINT for key conflict points
        * POLYGON for treatment zone if needed

        For bus_priority_corridor:

        * usually include:

        * POLYLINE for bus-priority corridor
        * POINT for stops/interchange points if mentioned

        For freight_access_improvement:

        * usually include:

        * POLYLINE for freight movement corridor
        * POINT for freight conflict/bottleneck node
        * POLYGON for treatment zone if applicable


        ## QUALITY CHECK

        Before finalizing:

        * ensure every line is grounded in the input
        * ensure SEARCH_LOCATION is real and input-supported
        * ensure DESCRIPTION is specific
        * ensure output is concise and map-useful
    """,
    output_key="simulation_result",
)

activity_agent = LlmAgent(
    name="activity_agent",
    model="gemini-3-flash-preview",
    description="Simulates how public activity changes after the infrastructure improvements.",
    instruction="""
Simulate human activity based on session.state["simulation_result"].

If session.state["feedback"] exists, revise accordingly.

Show:
- pedestrian flow
- traffic conditions
- business activity
- accessibility improvements
- community/public usage changes
""",
    output_key="activity_simulation",
)

analysis_agent = LlmAgent(
    name="analysis_agent",
    model="gemini-3-flash-preview",
    description="Analyzes the final impact of the infrastructure proposal.",
    instruction="""
Analyze session.state["activity_simulation"].

If session.state["feedback"] exists, deepen the analysis accordingly.

Score from 1 to 10 for:
- safety
- economic growth
- environment
- accessibility
- wellbeing

Include justification for each score.
""",
    output_key="final_analysis",
)


# ── Review agent for later checkpoint-based pipeline ─────────────────────────

review_agent = LlmAgent(
    name="review_agent",
    model="gemini-3-flash-preview",
    description="Evaluates whether the user's response is a valid selection, approval, or revision request for the current step output.",
    instruction="""
        You are a review agent for a multi-step infrastructure planning workflow.

        You will receive:
        - STEP NAME
        - STEP OUTPUT
        - USER RESPONSE

        Your job:
        - Read the current step output carefully.
        - Read the user's latest response carefully.
        - Decide whether the user's response is:
        1. a valid selection or approval
        2. too vague / unrelated
        3. a request to revise the previous step output

        Rules:
        - Return PASS only if the user response can be confidently mapped to the current output.
        - Return REVISE if the user response is too vague, invalid, or unrelated.
        - Return REVISE_TOTAL if the user is asking to regenerate or modify the previous step output itself.
        - If the user says "1", "2", "the first one", etc., resolve it explicitly.
        - If the current step is "Find needs", prefer returning JSON_OUTPUT with exactly one selected challenge object.
        - Do not add any extra commentary outside the required format.

        Output format exactly:

        VERDICT: PASS
        REASON: <brief reason>
        RESOLVED_REFERENCE: <explicit resolved item>
        JSON_OUTPUT: <single JSON object if available, especially for Find needs>

        OR

        VERDICT: REVISE
        INSTRUCTION: <brief instruction to the user>
        RESOLVED_REFERENCE:

        OR

        VERDICT: REVISE_TOTAL
        INSTRUCTION: <brief instruction for regenerating the previous step>
        RESOLVED_REFERENCE:
    """,
)

# ── Intake agent for phase 1 ──────────────────────────────────────────────────

place_intake_agent = LlmAgent(
    name="place_intake_agent",
    model="gemini-3-flash-preview",
    description="Collects one or two Malaysian cities/towns from the user.",
    instruction="""
        You are the intake agent for an infrastructure planning assistant.

        Your job:
        - greet the user naturally
        - ask for one or two Malaysian cities/towns to analyze
        - inspect the user's latest message
        - decide whether the message contains acceptable inputs

        Important rules:
        - Accept only city/town-level places in Malaysia
        - Accept at most two places
        - Reject places that are too broad, such as a whole country or a state
        - Reject places that are too specific, such as landmarks, stations, or very small areas
        - If part of the input is valid and part is not, ask the user to try again
        - Keep the feedback concise and natural

        You may use the supported places listed in session.state["valid_places_text"].

        Output format exactly:

        VERDICT: <SUCCESS or RETRY>
        PLACES: <place1|place2 or empty>
        FEEDBACK: <natural message to show the user>
        """,
)


# ── Custom orchestrator for phase 1: intake loop ─────────────────────────────

class PlaceIntakeOrchestrator(BaseAgent):
    intake_agent: LlmAgent
    app_name: str
    session_svc: object

    model_config = ConfigDict(arbitrary_types_allowed=True)

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:

        greeting_result = await self._invoke_agent(
            self.intake_agent,
            ctx.session.id,
            """
            The conversation is starting.

            Greet the user naturally and ask them to provide one or two Malaysian cities or towns for infrastructure analysis.

            Return exactly in this format:

            VERDICT: RETRY
            PLACES:
            FEEDBACK: <your greeting and question>
            """.strip(),
        )

        greeting_parsed = self._parse_place_result(greeting_result)

        yield Event(
            author=self.intake_agent.name,
            content=types.Content(
                role="model",
                parts=[types.Part(text=greeting_parsed["feedback"])],
            ),
        )

        while not ctx.session.state.get("target_places"):
            user_response = await self._wait_for_user("Location selection")

            agent_result = await self._invoke_agent(
                self.intake_agent,
                ctx.session.id,
                f"""
                    User message:
                    {user_response}

                    Remember:
                    - Accept any real Malaysian city or town
                    - Accept at most two places
                    - Reject places that are too broad
                    - Reject places that are too specific
                    - Return structured output exactly
                    """.strip()
            )

            parsed = self._parse_place_result(agent_result)


            if parsed["verdict"] == "SUCCESS" and parsed["places"]:
                ctx.session.state["target_places"] = parsed["places"]
                break
            else:
                yield Event(
                    author=self.intake_agent.name,
                    content=types.Content(
                        role="model",
                        parts=[types.Part(text=parsed["feedback"])],
                    ),
                )

        yield Event(
            author=self.intake_agent.name,
            content=types.Content(
                role="model",
                parts=[types.Part(
                    text=f'Location confirmed: {", ".join(ctx.session.state["target_places"])}. Moving to the planning phase.'
                )],
            ),
        )

    async def _invoke_agent(
        self, agent: LlmAgent, session_id: str, prompt: str
    ) -> str:
        runner = Runner(
            agent=agent,
            app_name=self.app_name,
            session_service=self.session_svc,
        )

        message = types.Content(
            role="user",
            parts=[types.Part(text=prompt)],
        )

        response = ""
        async for event in runner.run_async(
            user_id="user_001",
            session_id=session_id,
            new_message=message,
        ):
            if event.is_final_response() and event.content:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        response += part.text

        return response.strip()

    @staticmethod
    def _parse_place_result(text: str) -> dict:
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
                    result["places"] = [
                        p.strip() for p in raw_places.split("|") if p.strip()
                    ]

            elif line.startswith("FEEDBACK:"):
                result["feedback"] = line.split(":", 1)[1].strip()

        return result

    async def _wait_for_user(self, step_name: str) -> str:
        print(f"\n[{step_name}] ", end="")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, input)


# ── Custom orchestrator for later planning phases ────────────────────────────

class InfrastructurePlannerOrchestrator(BaseAgent):
    pipeline: list
    reviewer: LlmAgent
    app_name: str
    session_svc: object

    model_config = ConfigDict(arbitrary_types_allowed=True)
    format_entities: dict = {}

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:

        for step_name, step_agent, output_key in self.pipeline:
            ctx.session.state["feedback"] = ""
            attempt = True

            while True:
                if attempt == True:
                    if step_name == "Find needs":
                        step_prompt = f'Identify and rank the top 3 infrastructure-related challenges for {ctx.session.state["target_places"]}'
                    elif step_name == "Plan improvements":
                        step_prompt = f'Here is the details = {ctx.session.state["analysis_result"]}'
                    elif step_name == "Generate solutions":
                        step_prompt = f'Here is the plan {ctx.session.state["planning_result"]} with other details {ctx.session.state["analysis_result"]}'
                    elif step_name == "Building simulations":
                        step_prompt = f'Here is the recommended intervention {ctx.session.state["solution_result"]}  with other details {ctx.session.state["planning_result"]} and {ctx.session.state["analysis_result"]}'
                else:
                    step_prompt = (
                        f"The previous {step_name} output was rejected. "
                        f'Follow this specific instruction to revise it: "{ctx.session.state["feedback"]}"'
                    )

                step_output = await self._invoke_agent(
                    step_agent, ctx.session.id, step_prompt
                )

                yield Event(
                    author=step_agent.name,
                    content=types.Content(
                        role="model",
                        parts=[types.Part(text=step_output)],
                    ),
                )

                
                #vague response
                while True:
                    user_response = await self._wait_for_user(step_name, step_output)

                    review_prompt = (
                        f"STEP NAME: {step_name}\n\n"
                        f"STEP OUTPUT:\n{step_output}\n\n"
                        f"USER RESPONSE: {user_response}"
                    )

                    review_text = await self._invoke_agent(
                        self.reviewer, ctx.session.id, review_prompt
                    )

                    review_result = self._parse_review(review_text)

                    if review_result["verdict"] == "REVISE":
                        ctx.session.state["feedback"] = review_result["detail"]
                        yield Event(
                            author=self.reviewer.name,
                            content=types.Content(
                                role="model",
                                parts=[types.Part(text=ctx.session.state["feedback"])],
                            ),
                        )
                    else:
                        break
                
                #satisfy response and unsatisfy response
                if review_result["verdict"] == "PASS":
                    ctx.session.state["feedback"] = ""

                    selected_output = review_result["final_output"]

                    if step_name == "Find needs":
                        if isinstance(selected_output, str):
                            json_output = json.loads(selected_output)

                        analysis_result = self.run_analysis_from_agent_output(
                            json_output,
                            ctx.session.state["target_places"][0]
                        )            

                        ctx.session.state["analysis_result"] = analysis_result           
                    elif step_name == "Plan improvements":
                        if isinstance(selected_output, str):
                            json_output = json.loads(selected_output)

                        ctx.session.state["planning_result"] = json_output
                    elif step_name == "Generate solutions":
                        if isinstance(selected_output, str):
                            json_output = json.loads(selected_output)

                        ctx.session.state["solution_result"] = json_output
                    elif step_name == "Building simulations":
                        enriched_assets = process_agent_assets(selected_output)
                        self.format_entities = format_entities(enriched_assets)
                        print(self.format_entities)

                    attempt = True
                    break
                else:
                    ctx.session.state["feedback"] = review_result["detail"]
                    attempt = False
                    

    async def _invoke_agent(
        self, agent: LlmAgent, session_id: str, prompt: str
    ) -> str:
        runner = Runner(
            agent=agent,
            app_name=self.app_name,
            session_service=self.session_svc,
        )

        message = types.Content(
            role="user",
            parts=[types.Part(text=prompt)],
        )

        response = ""
        async for event in runner.run_async(
            user_id="user_001",
            session_id=session_id,
            new_message=message,
        ):
            if event.is_final_response() and event.content:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        response += part.text

        return response.strip()

    async def _wait_for_user(
        self, ctx_step_name: str, output: str
    ) -> str:
        print(f"\n[{ctx_step_name}] Input: ", end="")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, input)


    @staticmethod
    def _parse_review(text: str) -> dict:
        result = {
            "verdict": "REVISE",
            "detail": "",
            "final_output": "",
        }

        # VERDICT
        verdict_match = re.search(r"VERDICT:\s*(PASS|REVISE|REVISE_TOTAL)", text, re.IGNORECASE)
        if verdict_match:
            result["verdict"] = verdict_match.group(1).strip().upper()
            print("result[verdict]: " + result["verdict"])

        # REASON or INSTRUCTION
        detail_match = re.search(r"(?:REASON|INSTRUCTION):\s*(.+)", text)
        if detail_match:
            result["detail"] = detail_match.group(1).strip()
            print("result[detail]: " + result["detail"])

        # Capture multi-line JSON_OUTPUT or OUTPUT block
        json_match = re.search(
            r"JSON_OUTPUT:\s*(\{.*\})",
            text,
            re.DOTALL
        )
        if json_match:
            result["final_output"] = json_match.group(1).strip()
            print("result[final_output]: " + result["final_output"])
            return result

        output_match = re.search(
            r"OUTPUT:\s*(.+)",
            text,
            re.DOTALL
        )
        if output_match:
            result["final_output"] = output_match.group(1).strip()
            print("result[final_output]: " + result["final_output"])

        return result
    
    @staticmethod
    def extract_field(text, field_name):
        """
        Extracts a field value from the agent output text.
        Example:
        field_name = "CHALLENGE_1_MICRO_SYMPTOM_A_ROAD_1"
        """
        pattern = rf"{field_name}:\s*(.+)"
        match = re.search(pattern, text)

        if match:
            return match.group(1).strip()
        
        return None
    
    @staticmethod
    def is_valid_field(value):
        return value is not None and str(value).strip().lower() != "n/a"

    def convert_micro_to_queries(self, micro):
        """
        Convert one micro symptom into routing queries using:
        - ROUTING_LABEL_1 / ROUTING_LABEL_2
        - ROUTING_ALIASES_1 / ROUTING_ALIASES_2
        - fallback to ROAD_1 / ROAD_2
        """

        def clean_value(value):
            if value is None:
                return None

            text = str(value).strip()
            if not text or text.lower() == "n/a":
                return None

            return text

        def clean_list(values):
            if values is None:
                return []

            if isinstance(values, list):
                raw = values
            else:
                raw = [values]

            result = []
            for v in raw:
                cleaned = clean_value(v)
                if cleaned:
                    result.append(cleaned)

            # remove duplicates while preserving order
            deduped = []
            seen = set()
            for item in result:
                key = item.lower()
                if key not in seen:
                    deduped.append(item)
                    seen.add(key)

            return deduped

        def build_query_list(primary_label, aliases, fallback_label):
            queries = []

            primary = clean_value(primary_label)
            fallback = clean_value(fallback_label)
            alias_list = clean_list(aliases)

            if primary:
                queries.append(primary.lower())

            for alias in alias_list:
                queries.append(alias.lower())

            if fallback:
                queries.append(fallback.lower())

            # dedupe again after lowercasing
            deduped = []
            seen = set()
            for q in queries:
                if q not in seen:
                    deduped.append(q)
                    seen.add(q)

            return deduped

        micro_type = str(micro.get("TYPE", "")).strip().lower()

        # -------- corridor / junction --------
        if micro_type in ["corridor", "junction"]:
            road_1 = micro.get("ROAD_1")
            road_2 = micro.get("ROAD_2")

            routing_label_1 = micro.get("ROUTING_LABEL_1")
            routing_label_2 = micro.get("ROUTING_LABEL_2")

            routing_aliases_1 = micro.get("ROUTING_ALIASES_1", [])
            routing_aliases_2 = micro.get("ROUTING_ALIASES_2", [])

            road_a_queries = build_query_list(
                primary_label=routing_label_1,
                aliases=routing_aliases_1,
                fallback_label=road_1
            )

            road_b_queries = build_query_list(
                primary_label=routing_label_2,
                aliases=routing_aliases_2,
                fallback_label=road_2
            )

            if road_a_queries:
                if not road_b_queries:
                    road_b_queries = road_a_queries.copy()

                return {
                    "type": micro_type,
                    "symptom": micro.get("SYMPTOM"),
                    "location_label": micro.get("LOCATION_LABEL"),
                    "road_a_queries": road_a_queries,
                    "road_b_queries": road_b_queries,
                    "road_a_label": clean_value(road_1) or clean_value(routing_label_1),
                    "road_b_label": clean_value(road_2) or clean_value(routing_label_2),
                }

        # -------- freight_route --------
        elif micro_type == "freight_route":
            primary_route = micro.get("PRIMARY_ROUTE")
            secondary_route = micro.get("SECONDARY_ROUTE")

            routing_label_1 = micro.get("ROUTING_LABEL_1")
            routing_label_2 = micro.get("ROUTING_LABEL_2")

            routing_aliases_1 = micro.get("ROUTING_ALIASES_1", [])
            routing_aliases_2 = micro.get("ROUTING_ALIASES_2", [])

            road_a_queries = build_query_list(
                primary_label=routing_label_1,
                aliases=routing_aliases_1,
                fallback_label=primary_route
            )

            # for freight, second route is optional
            road_b_queries = build_query_list(
                primary_label=routing_label_2,
                aliases=routing_aliases_2,
                fallback_label=secondary_route
            )

            if road_a_queries:
                # if no secondary route exists, reuse primary
                if not road_b_queries:
                    road_b_queries = road_a_queries.copy()

                return {
                    "type": micro_type,
                    "symptom": micro.get("SYMPTOM"),
                    "location_label": micro.get("LOCATION_LABEL"),
                    "road_a_queries": road_a_queries,
                    "road_b_queries": road_b_queries,
                    "road_a_label": clean_value(primary_route) or clean_value(routing_label_1),
                    "road_b_label": clean_value(secondary_route) or clean_value(routing_label_2) or (clean_value(primary_route) or clean_value(routing_label_1)),
                }

        # -------- transit_node --------
        elif micro_type == "transit_node":
            station_or_line = clean_value(micro.get("STATION_OR_LINE"))

            return {
                "type": micro_type,
                "symptom": micro.get("SYMPTOM"),
                "location_label": micro.get("LOCATION_LABEL"),
                "station_or_line": station_or_line,
            }

        return None
    
    def enrich_query_list(queries):
        enriched = []

        for q in queries:
            q = q.strip()
            if not q:
                continue

            enriched.append(q.lower())

            cleaned = re.sub(r"\(.*?\)", "", q).strip()
            if cleaned and cleaned.lower() not in enriched:
                enriched.append(cleaned.lower())

        # dedupe
        deduped = []
        seen = set()
        for item in enriched:
            if item not in seen:
                deduped.append(item)
                seen.add(item)

        return deduped
    
    def convert_agent_output_to_pipeline_input(self, agent_output):
        primary = agent_output.get("PRIMARY_MICRO", {})
        secondary = agent_output.get("SECONDARY_MICRO", {})

        primary_result = self.convert_micro_to_queries(primary)
        secondary_result = self.convert_micro_to_queries(secondary)

        return {
            "primary": primary_result,
            "secondary": secondary_result
        }
    
    def run_analysis_from_agent_output(self, agent_output, user_city, regions_path="regions.json", city_buffer_m=500):
        """
        agent_output is expected to be a dict like:
        {
            "PRIMARY_MICRO": {...},
            "SECONDARY_MICRO": {...}
        }
        """

        primary = agent_output.get("PRIMARY_MICRO", {})
        secondary = agent_output.get("SECONDARY_MICRO", {})

        primary_input = self.convert_micro_to_queries(primary)
        secondary_input = self.convert_micro_to_queries(secondary)

        print("PRIMARY parsed:", primary_input)
        print("SECONDARY parsed:", secondary_input)

        attempts = []

        if primary_input:
            attempts.append(("PRIMARY_MICRO", primary_input))

        if secondary_input:
            attempts.append(("SECONDARY_MICRO", secondary_input))

        if not attempts:
            raise ValueError("No usable routing input found in agent output.")

        results = []

        for source_name, parsed in attempts:
            if parsed["type"] == "transit_node":
                continue

            if parsed["type"] == "freight_route":
                label = (parsed.get("location_label") or "").lower()
                if "port klang" in label or "federal highway freight link" in label:
                    print(f"Skipping {source_name}: freight corridor is too broad for current city graph.")
                    continue

            try:
                result = run_city_road_connection_analysis(
                    user_city=user_city,
                    road_a_queries=parsed["road_a_queries"],
                    road_b_queries=parsed["road_b_queries"],
                    regions_path=regions_path,
                    city_buffer_m=city_buffer_m,
                )

                result["selected_micro_source"] = source_name
                result["selected_micro_type"] = parsed["type"]
                result["selected_micro_symptom"] = parsed.get("symptom")
                result["selected_micro_location_label"] = parsed.get("location_label")

                results.append(result)

            except Exception as e:
                print(f"{source_name} failed: {e}")

        if not results:
            raise ValueError("No successful routing results.")

        return results



# ── Wire everything up ────────────────────────────────────────────────────────

session_service = InMemorySessionService()

place_intake_root = PlaceIntakeOrchestrator(
    name="place_intake_orchestrator",
    description="Collects one or two Malaysian cities/towns before planning starts.",
    intake_agent=place_intake_agent,
    app_name="infrastructure_planner",
    session_svc=session_service,
)

planning_root = InfrastructurePlannerOrchestrator(
    name="infrastructure_planner_orchestrator",
    description="Orchestrates the infrastructure planner workflow with human checkpoints.",
    pipeline=[
        ("Find needs", find_needs_agent,  "top_challenges"),
        ("Plan improvements",   planning_agent,    "improvement_plan"),
        ("Generate solutions", solution_agent, "solution_plan"),
        ("Building simulations", building_agent,   "simulation_result")
        # ("Activity simulation", activity_agent,   "activity_simulation"),
        # ("Analysis",            analysis_agent,   "final_analysis"),
    ],
    reviewer=review_agent,
    app_name="infrastructure_planner",
    session_svc=session_service,
)


# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    session = await session_service.create_session(
        app_name="infrastructure_planner",
        user_id="user_001",
        state={
        "feedback": "",
        "valid_places_text": "",
        }
    )

    #Phase 1: collect location input
    intake_runner = Runner(
        agent=place_intake_root,
        app_name="infrastructure_planner",
        session_service=session_service,
    )

    async for event in intake_runner.run_async(
        user_id="user_001",
        session_id=session.id,
        new_message=types.Content(
            role="user",
            parts=[types.Part(text="Start the infrastructure planning workflow.")],
        ),
    ):
        if event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    print(f"\n[{event.author}] {part.text}")

    # Phase 2: run the planning workflow
    planning_runner = Runner(
        agent=planning_root,
        app_name="infrastructure_planner",
        session_service=session_service,
    )

    async for event in planning_runner.run_async(
        user_id="user_001",
        session_id=session.id,
        new_message=types.Content(
            role="user",
            parts=[types.Part(text="Proceed to the infrastructure planning phase.")],
        ),
    ):
        if event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    print(f"\n[{event.author}] {part.text}")


if __name__ == "__main__":
    asyncio.run(main())