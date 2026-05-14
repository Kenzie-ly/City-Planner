from google.adk.agents import LlmAgent, BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from typing import AsyncGenerator
from pydantic import ConfigDict
import asyncio
import os
from dotenv import load_dotenv
from google.adk.tools.google_search_tool import GoogleSearchTool
import re
from FindRoads import run_city_road_connection_analysis
from building_agent_helper import process_agent_assets, format_entities
import json

# Load API key from .env file
load_dotenv()
PLANNER_MODEL = os.getenv("PLANNER_MODEL", "gemini-flash-latest").strip() or "gemini-3.1-flash-lite-preview"


# ── Step agents for later phases ──────────────────────────────────────────────

place_intake_agent = LlmAgent(
    name="place_intake_agent",
    model=PLANNER_MODEL,
    description="Collects 1-2 Malaysian cities/towns from the user.",
    instruction="""
        You are the intake agent for an infrastructure planning assistant.

        Your job:
        - greet the user naturally
        - ask for one or two Malaysian cities or towns to analyze
        - inspect the user's latest message
        - decide whether the message contains acceptable location inputs

        Important rules:
        - Accept any real city or town in Malaysia.
        - Accept at most two places.
        - Reject places that are too broad, such as a whole country or a whole state (e.g. "Selangor", "Johor", "Malaysia").
        - HOWEVER, always accept major cities even if they are large (e.g. "Kuala Lumpur", "George Town", "Ipoh", "Shah Alam", "Petaling Jaya", "Klang").
        - Reject places that are too specific, such as landmarks, stations, roads, buildings, or very small neighborhoods.
        - If part of the input is valid and part is not, return RETRY and explain naturally.
        - Keep the feedback concise and natural.

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
    model=PLANNER_MODEL,
    description="Identifies the top 3 broad transport-related challenges for the selected Malaysian city or town.",
    instruction="""
        You are the Lead Transport Systems Analysis Supervisor for Malaysia.

        You will be given one or two Malaysian target places and optional geospatial evidence.
                Your task is to identify 1 to 3 critical BROAD transport-related infrastructure challenges that are DIRECTLY supported by the evidence.

        EVIDENCE-FIRST POLICY:
        - Do NOT invent statistics. Only use numbers found in the provided evidence snippets.
        - Do NOT hallucinate sources. Only cite provided URLs.
        - If the evidence only supports 1 or 2 challenges, return ONLY those. Do NOT fill the quota of 3 if the data is thin.
        - Prioritize "Verified Complaints" (where human reports match infrastructure gaps).

        SOURCE HIERARCHY & PRIORITY (STRICT):
        1. CLOUDSQL_INDICATORS: MANDATORY QUANTITATIVE TRUTH. You MUST use these IDs to verify the existence of infrastructure gaps. If the DB shows 0 transit POIs in a sector, ignore any search result claiming it is "well-served."
        2. RAG_CONTEXT: MANDATORY QUALITATIVE TRUTH. Use these chunks to explain the "why" and "impact." These are the only authorized qualitative sources.
        3. GOOGLE SEARCH: FORBIDDEN unless the CLOUDSQL/RAG variables are empty for the target location. If internal data exists, search results that conflict with it MUST be discarded.
        4. ANCHORING: Every CHALLENGE must be anchored to a specific indicator_id. Challenges without an ID are considered hallucinations.

        EVIDENCE SYNTHESIS LOGIC:
        - DB-FIRST: Always audit the Database indicators before using any other tool.
        - RAG-CONTEXTUALIZATION: Use RAG evidence to explain the human impact of the DB indicators.
        - NARRATIVE RULE: Do NOT say "A news article reports X." Say "Infrastructure indicators show X, which correlates with policy/report evidence regarding..."
        - DISCREPANCY HANDLING: If a search result (if used) conflicts with a DB indicator, you MUST flag it as a "Data Conflict" in the Limitations field.

        SCOPE:
        - first-mile/last-mile connectivity gaps for B40/M40 commuters
        - INDUSTRIAL zone accessibility (connecting housing to factory clusters)
        - transit deserts (lack of nearby LRT/MRT/KTM stations)
        - lack of feeder bus routes for essential workers
        - poor pedestrian accessibility to transit hubs
        - transit bottleneck detection near workplaces
        - bus reliability issues
        - micromobility integration for industrial zones
        - BRT (Bus Rapid Transit) corridor opportunities for workforce commuting

        HARD RULES:
        - ONLY discuss the target place(s)
        - ONLY transport-related problems
        - NO solutions
        - NO PRIMARY_MICRO or SECONDARY_MICRO yet
        - NO routing labels yet
        - stay at challenge-category level
        
        CORRIDOR FUSION POLICY (EXPERT):
        - Do NOT treat residential and industrial needs as isolated.
        - Prioritize identifying STRATEGIC CORRIDORS that connect O-D (Origin-Destination) pairs (e.g. 'Cheras-Shah Alam Workforce Corridor').
        - Every challenge should explain how it links residential hubs to workforce destinations.

        TOOL USAGE:
        - Google Search is a RESTRICTED fallback. Use it ONLY if the CLOUDSQL_INDICATORS are completely empty or if searching for a highly specific real-time event (e.g. "flood today").
        - If CLOUDSQL_INDICATORS provides enough evidence to form a challenge, you MUST NOT use the search tool.

        FEEDBACK LOOP:
        - If you are provided with feedback or a rejection reason from a previous attempt, you MUST address that feedback explicitly by generating DIFFERENT challenges or focusing on the requested aspects.
        - If SELECTED_AREA_OPTION_JSON / MERGED_EVIDENCE_JSON is provided, anchor challenge ranking to that selected area and evidence.
        - If you are asked to repair invalid output, strictly fix the schema and source validity requirements.

        OUTPUT FORMAT (STRICT JSON ONLY):
        Return exactly one JSON object and nothing else.

        {
          "CHALLENGE_1": {
            "CHALLENGE_TYPE": "<one of the scope items above, e.g., transit_desert>",
            "TITLE": "<user-friendly challenge title>",
            "BRIEF_DESCRIPTION": "<1-3 sentences synthesizing the indicators and RAG context for the user>",
            "SOURCES": [
              {
                "publisher": "<publisher name>",
                "url": "https://...",
                "published_at": "YYYY-MM-DD or YYYY-MM or YYYY",
                "source_tier": "government | operator | study | major_media | local_media"
              }
            ],
            "EVIDENCE_AUDIT": [
              {
                "claim": "<Specific claim made in the description>",
                "evidence_id": "<Exact ID from CLOUDSQL_INDICATORS or RAG_CONTEXT>",
                "evidence_type": "indicator | rag",
                "fact_used": "<The specific number or policy snippet that proves the claim>"
              }
            ],
            "CONFIDENCE_LEVEL": "high | usable | low",
            "LIMITATIONS": ["<Describe any data gaps or missing real-time evidence>"]
          },
          "CHALLENGE_2": { "..." : "..." },
          "CHALLENGE_3": { "..." : "..." }
        }
        
        Return ONLY strict JSON with keys: CHALLENGE_1, CHALLENGE_2, CHALLENGE_3 (if available).
        """,
    tools=[GoogleSearchTool()],
    output_key="top_challenges",
)

find_hotspot_agent = LlmAgent(
    name="find_hotspot_agent",
    model=PLANNER_MODEL,
    description="Generates one graph-routable hotspot hypothesis for a selected transport challenge.",
    instruction="""
        You are a hotspot hypothesis agent.

        You are given:
        - a city
        - one selected transport challenge
        - optional failure feedback from previous attempts

        FEEDBACK LOOP:
        - If the user or the review system provides feedback (e.g., "too generic", "wrong road", "focus on X"), you MUST adjust your hypothesis to address it.

        Your task:
        - produce specific, graph-routable hotspot hypotheses
        - it must be specific enough for downstream road matching
        - prioritise public-transport improvement logic: feeder buses, bus-priority corridors, train station access, interchange access, and walk-to-transit links
        - output STRICT JSON only (a single object or a list of objects as requested)
        
        ENHANCED DESCRIPTION POLICY:
        - You MUST provide a 3-paragraph RATIONALE that explains:
            1. Why this hotspot was chosen (connecting to verified human complaints).
            2. The expected impact on the B40/M40 workforce.
            3. The technical justification for the chosen intervention type.

        OSM ROAD MATCHING RULES (CRITICAL):
        - Provide clean OpenStreetMap (OSM) road names in `road_a_queries` and `road_b_queries`.
        - DO NOT append the city, region, or country name to the queries (e.g., use "Jalan Tun Razak", NEVER "Jalan Tun Razak, Kuala Lumpur").
        - Provide an array of multiple name aliases and highway reference codes to maximize matching chances (e.g., ["Jalan Ampang", "Ampang Road", "B31"]).
        - `road_a_label` and `road_b_label` should be the clean primary display name.

        Required JSON Schema (Single object or List):
        {
        "location_label": "<Concise geocodable neighborhood, landmark, or station name (e.g., 'Taman Maluri', 'KL Sentral')>",
        "type": "transit_node | feeder_route | pedestrian_link | brt_corridor",
        "symptom": "...",
        "road_a_queries": ["alias 1", "alias 2", "ref code"],
        "road_b_queries": ["alias 1", "alias 2", "ref code"],
        "road_a_label": "...",
        "road_b_label": "...",
        "confidence": "low | medium | high"
        }
        """,
    output_key="hotspot_hypothesis",
)

# planning_agent = LlmAgent(
#     name="planning_agent",
#     model=PLANNER_MODEL,
#     description="Evaluates transport intervention candidates and selects the best improvement option for the selected problem.",
#     instruction = """
#         You are a Transport Planning Decision Agent for Malaysia.
#         You will receive:

#         A structured transport problem
#         A list of candidate routes generated from a graph-based road analysis system
        
#         FEEDBACK LOOP:
#         - If the previous plan was rejected, follow the user's instructions to select a different candidate or intervention type.
        
#         Compare all candidates carefully
#         Select the MOST appropriate candidate for addressing the problem
#         Identify the most suitable intervention type
#         Justify your decision using ONLY the provided data
#         Explain tradeoffs between candidates
#         Assign a realistic confidence level
#         CRITICAL RULES (MUST FOLLOW)
#         - You MUST NOT invent exact distances, lane counts, travel-time savings, percentages, economic values, or any other numeric impact metric unless that exact value already appears in the provided input JSON.
#         - If the provided input does not contain a required number, use qualitative wording such as "short connector", "limited road width", or "likely moderate benefit" instead of fabricating precision.
#         You MUST only choose from the provided candidates.
#         You MUST NOT invent new roads, paths, or geometry.
#         You MUST base your reasoning ONLY on:
#         candidate attributes
#         problem description
#         You MUST NOT assume real-world conditions not present in the data.
#         You MUST NOT overclaim engineering certainty.
#         DECISION LOGIC GUIDELINES
#         You MUST consider:

#         Problem type (junction, corridor, etc.)
#         Path length
#         Number of segments
#         Connector count
#         Road class composition
#         Whether the candidate reflects:
#         a direct conflict point (junction)
#         a meaningful corridor (multi-segment)
#         a detour through lower-class roads
#         INTERVENTION TYPE RULES
#         You MUST select one of:

#         transit_hub_upgrade
#         feeder_bus_route
#         brt_corridor
#         pedestrian_walkway
#         micromobility_station
#         transit_priority_lane
#         Guidance:

#         If the problem relates to industrial worker mobility → prefer feeder_bus_route or brt_corridor connecting to industrial landuse
#         If the problem is "transit_node" and the candidate connects to low-to-middle income (B40/M40) residential areas → prefer feeder_bus_route or pedestrian_walkway
#         If the candidate spans a major arterial → consider brt_corridor or transit_priority_lane
#         If the candidate relies on local roads near LRT/MRT → consider micromobility_station or pedestrian_walkway
#         If the input indicates official service overlap or duplication risk → prefer transit_hub_upgrade, pedestrian_walkway, or transit_priority_lane over inventing a brand-new feeder route
#         CONFIDENCE RULES
#         HIGH → one candidate clearly dominates
#         MEDIUM → reasonable but not definitive
#         LOW → ambiguity or weak evidence
#         Do NOT assign HIGH unless strongly justified.
#         OUTPUT FORMAT (STRICT JSON ONLY)
#         {
#         "selected_candidate_id": "<candidate_id>",
#         "intervention_type": "",
#         "decision_summary": "<1-2 sentence explanation>",
#         "description": "<A 2-3 paragraph strategic rationale that connects the selected candidate to verified human complaints and explains the expected public-transport benefit for B40/M40 commuters without inventing unsupported numbers.>",
#         "reasons": [
#         "<reason 1 grounded in data>",
#         "<reason 2 grounded in data>",
#         "<reason 3 grounded in data>"
#         ],
#         "tradeoffs": [
#         "<tradeoff 1>",
#         "<tradeoff 2>"
#         ],
#         "priority_level": "<low | medium | high>",
#         "confidence": "<low | medium | high>"
#         }

#         JSON SAFETY RULE:
#         - You MUST produce a single, valid JSON object.
#         - You MUST NOT include any conversational text before or after the JSON.
#         - You MUST escape all newlines as \n within JSON string values.
#         - Do NOT include literal newlines in strings.
#         FINAL CHECK (MANDATORY)
#         Before output:

#         Ensure selected_candidate_id exists in input
#         Ensure all reasoning refers to actual candidate data
#         Ensure no hallucinated roads or features are introduced
#     """,
#     output_key="planning_result"
# )

solution_agent = LlmAgent(
    name="solution_agent",
    model=PLANNER_MODEL,
    description="Translates transport planning decisions into detailed, actionable engineering solutions.",
    instruction = """
        You are a Transport Infrastructure Solution Designer.

        You will receive:
        A selected transport problem
        The chosen candidate route
        The intervention type from a planning decision
        
        FEEDBACK LOOP:
        - If the previous solution was rejected, incorporate the user's feedback into the new design.
        
        Your task is to:
        Translate the selected candidate into a REALISTIC and ACTIONABLE intervention
        Describe exactly WHAT should be changed, WHERE, and HOW
        Ensure all actions are grounded in the provided road structure
        
        CORRIDOR-CENTRIC DESIGN:
        - Design solutions that facilitate seamless movement across the entire STRATEGIC CORRIDOR identified in the planning phase.
        - Ensure first-mile (residential) and last-mile (destination) elements are integrated into a single cohesive engineering plan.

        
        CRITICAL RULES (MUST FOLLOW)
        You MUST base your solution ONLY on:
        selected candidate roads
        provided problem description
        You MUST NOT invent new roads or geometry.
        You MUST NOT assume unavailable data (e.g., signal timing details).
        You MUST prioritise solutions that strengthen bus routes, feeder services, station access, interchange quality, or rail-supportive access.
        If the input indicates official service overlap or existing operator coverage, you MUST frame the intervention as an upgrade, reroute, retime, access treatment, or interchange improvement instead of inventing a new route.
        You MUST NOT invent exact distances, lane counts, speed changes, time savings, ridership changes, or percentages unless they are explicitly present in the provided input.
        You MUST prefer grounded qualitative language over fake precision.
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

        If intervention_type = transit_hub_upgrade:
        Focus on:
        station accessibility
        feeder bus integration
        pedestrian safety around station

        If intervention_type = brt_corridor or transit_priority_lane:
        Focus on:
        dedicated lane allocation
        bus stop placements
        transit signal priority

        OUTPUT FORMAT (STRICT JSON ONLY)
        {
        "solution_title": "",

        "solution_type": "",

        "detailed_description": "<A technical 2-3 paragraph walkthrough of the intervention, explaining precisely how the proposed actions address the identified transit failures and validated human reports.>",

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
        "uncertainties": [
            "<uncertainty 1>",
            "<uncertainty 2>"
        ],
        "implementation_complexity": "<low | medium | high>",
        "confidence": "<low | medium | high>",
        "societal_impact": "<A natural, 1-2 sentence paragraph explaining the positive impact on daily commuters, local residents, and the general public (e.g., focus on ridership increases, accessibility radius, transit time savings, or carbon emission reductions).>"
        }

        JSON SAFETY RULE:
        - You MUST produce a single, valid JSON object.
        - You MUST NOT include any conversational text before or after the JSON.
        - You MUST escape all newlines as \n within JSON string values.
        - Do NOT include literal newlines in strings.

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
    model=PLANNER_MODEL,
    description="Converts a selected infrastructure option into structured map scene data for CesiumJS.",
    instruction="""
        You are a Transport Infrastructure Map Building Agent.

        You will receive:
        1. a selected transport problem
        2. a chosen candidate path
        3. a solution design describing the recommended intervention
        
        FEEDBACK LOOP:
        - If the previous map scene was rejected, adjust the visualization as requested.
        
        Your task is to convert that solution into map-build instructions for a 3D city map.

        You must output ONLY map instructions, not reasoning.


        ## CORE RESPONSIBILITY

        Your job is to identify the visual map elements needed to represent the solution clearly.

        These MUST include:

        * POINT → for intervention nodes, kiosks, stations, bus stops, conflict points
        * POLYLINE → for corridors, routes, connectors, lane-priority segments, feeder alignments
        * POLYGON → for zones, treatment areas, station forecourts, widened junction footprints
        * SIMULATION → MANDATORY for transport/road projects. Use this to illustrate traffic flow. Place vehicles (x10 to x20) along the improved corridor or through the redesigned junction to show movement.
        * LABEL → for important annotations when needed


        ## STRICT RULES

        1. You MUST use only locations, roads, corridors, junctions, and nodes explicitly present in the input.
        2. You MUST NOT invent new roads, new places, or unsupported geometry.
        3. For every transport intervention, you MUST include at least one SIMULATION object to show how buses, trains-supporting access traffic, or general traffic use the new infrastructure.
        4. Use SEARCH_LOCATION as a real, geocodable place or road name (e.g. "Jalan Ampang", "Jalan Tun Razak"). NEVER use generic/invented names like "Connector", "New Lane", or "Junction". If referring to an intersection, use the name of the main road explicitly.
        5. STYLE_HINT must be short. For SIMULATION, you can specify speed (e.g., speed:40).
        6. DESCRIPTION must explain what the object means on the map.


        ## OUTPUT FORMAT (STRICT)

        Return one line per map object using EXACTLY this bracketed format (DO NOT OMIT THE BRACKETS):

        [GEOMETRY_TYPE | COUNT | LABEL | SEARCH_LOCATION | STYLE_HINT | DESCRIPTION]

        Allowed GEOMETRY_TYPE values:

        * POINT
        * POLYLINE
        * POLYGON
        * SIMULATION
        * LABEL

        COUNT rules:

        * Use x1, x2, x3, etc.
        * COUNT means how many similar objects of this type are needed. For SIMULATION, this is the number of vehicles to simulate.

        STYLE_HINT examples:
        * style:transit, color:blue (MANDATORY for bus routes, BRT, LRT to trigger Bus 3D models)
        * style:freight, color:brown (MANDATORY for logistics/freight corridors to trigger Truck 3D models)
        * style:pedestrian, color:green (MANDATORY for active mobility, walkways, cycle lanes)
        * color:red, width:thick (Used for standard conflict points or car bottlenecks)
        * speed:40 (Used for SIMULATION to set velocity)
        * speed:20, flow:congested (Used for SIMULATION on existing roads to show current problem)
        * speed:70, flow:optimized, height:medium (Used for SIMULATION on your NEW roads to show the improvement)

        ## HIGH-IMPACT SIMULATION TIP:
        To show traffic on a NEW road you just proposed:
        1. Give your [POLYLINE_NEW] a unique label (e.g., "North Bypass").
        2. Create a [SIMULATION] and set its SEARCH_LOCATION to "North Bypass". The system will automatically place the cars on your new road.
        3. ALWAYS provide both a "congested" simulation (current state) and an "optimized" one (new state) for maximum visual impact.
        * color:yellow, width:medium, dashed:true (Used for temporary or secondary lanes)


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

# activity_agent = LlmAgent(
#     name="activity_agent",
#     model=PLANNER_MODEL,
#     description="Simulates how public activity changes after the infrastructure improvements.",
#     instruction="""
# Simulate human activity based on session.state["simulation_result"].

# If session.state["feedback"] exists, revise accordingly.

# Show:
# - pedestrian flow
# - traffic conditions
# - business activity
# - accessibility improvements
# - community/public usage changes
# """,
#     output_key="activity_simulation",
# )

# analysis_agent = LlmAgent(
#     name="analysis_agent",
#     model=PLANNER_MODEL,
#     description="Analyzes the final impact of the infrastructure proposal.",
#     instruction="""
# Analyze session.state["activity_simulation"].

# If session.state["feedback"] exists, deepen the analysis accordingly.

# Score from 1 to 10 for:
# - safety
# - economic growth
# - environment
# - accessibility
# - wellbeing

# Include justification for each score.
# """,
#     output_key="final_analysis",
# )


# ── Review agent for later checkpoint-based pipeline ─────────────────────────

# review_agent = LlmAgent(
#     name="review_agent",
#     model=PLANNER_MODEL,
#     description="Evaluates whether the user's selection response from, or revision requests to the current step output.",
#     instruction="""
#         You are a review agent for a multi-step infrastructure planning workflow.

#         You will be receiving either challanges selection at run time.

#         Your job:
#         - decide whether the user response is a valid selection or approval
#         - detect vague, unrelated, or invalid responses
#         - detect when the user wants the previous step regenerated or revised
#         - when the user selects one item from a JSON structure, resolve it into the exact selected JSON object

#         NATURAL COMMUNICATION:
#         - When returning REVISE, provide a natural, polite, and helpful message to the user asking them to clarify. Avoid sounding like a machine.

#         RULES:
#         - Return PASS only if the user response can be confidently mapped to the current output.
#         - Return REVISE if the response is too vague, invalid, or unrelated.
#         - Return REVISE_TOTAL if the user is asking to regenerate, rebuild, redo, or modify the previous step output itself.
#         - Keywords like "try again", "rebuild", "redo", "generate another", "don't like this" should trigger REVISE_TOTAL.
#         - If the user says "1", "2", "the first one", etc., resolve it explicitly.
#         - For challenge selection, return the selected CHALLENGE_n object as JSON_OUTPUT.
#         - For micro selection, return the selected PRIMARY_MICRO or SECONDARY_MICRO object as JSON_OUTPUT.
#         - For approval steps such as planning/solution/building, you may return the current JSON or text as OUTPUT.
#         - Do not add extra commentary outside the required format.

#         Output format exactly:

#         VERDICT: PASS
#         REASON: <brief reason>
#         RESOLVED_REFERENCE: <explicit resolved item>
#         JSON_OUTPUT: <single JSON object if available>

#         OR

#         VERDICT: REVISE
#         INSTRUCTION: <A natural, polite, and helpful message asking the user for clarification or a valid selection.>
#         RESOLVED_REFERENCE:

#         OR

#         VERDICT: REVISE_TOTAL
#         INSTRUCTION: <specific actionable instruction based on the user's response>
#         RESOLVED_REFERENCE:
#     """,
# )


# ── Intake agent for phase 1 ──────────────────────────────────────────────────


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
                        analysis = ctx.session.state.get("analysis_result")
                        if not analysis:
                            raise RuntimeError("analysis_result missing — 'Find needs' must complete before 'Plan improvements'.")
                        step_prompt = f'Here is the routing analysis details = {json.dumps(analysis)}'

                    elif step_name == "Generate solutions":
                        analysis = ctx.session.state.get("analysis_result")
                        planning = ctx.session.state.get("planning_result")
                        if not analysis or not planning:
                            raise RuntimeError("analysis_result or planning_result missing from session state.")
                        step_prompt = f'Here is the plan {json.dumps(planning)} with routing analysis {json.dumps(analysis)}'

                    elif step_name == "Building simulations":
                        analysis = ctx.session.state.get("analysis_result")
                        planning = ctx.session.state.get("planning_result")
                        solution = ctx.session.state.get("solution_result")
                        if not analysis or not planning or not solution:
                            raise RuntimeError("Missing one of analysis_result / planning_result / solution_result in session state.")
                        step_prompt = f'Here is the recommended intervention {json.dumps(solution)} with planning {json.dumps(planning)} and routing analysis {json.dumps(analysis)}'
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

                    selected_output = review_result["final_output"] or step_output

                    if step_name == "Find needs":
                        json_output = json.loads(selected_output) if isinstance(selected_output, str) else selected_output
                        ctx.session.state["selected_challenge"] = json_output
                        # CLI orchestrator is legacy; app.py now performs hotspot loop and routing.
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
        if micro.get("road_a_queries") and micro.get("road_b_queries"):
            return {
                "type": micro.get("type", ""),
                "symptom": micro.get("symptom"),
                "location_label": micro.get("location_label"),
                "road_a_queries": micro.get("road_a_queries", []),
                "road_b_queries": micro.get("road_b_queries", []),
                "road_a_label": micro.get("road_a_label"),
                "road_b_label": micro.get("road_b_label"),
            }

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
            road_a_queries = build_query_list(
                primary_label=micro.get("road_a_label") or micro.get("ROAD_A_LABEL"),
                aliases=micro.get("road_a_queries") or micro.get("ROAD_A_QUERIES") or [],
                fallback_label=micro.get("PRIMARY_ROUTE") or micro.get("ROUTING_LABEL_1") or station_or_line,
            )
            road_b_queries = build_query_list(
                primary_label=micro.get("road_b_label") or micro.get("ROAD_B_LABEL"),
                aliases=micro.get("road_b_queries") or micro.get("ROAD_B_QUERIES") or [],
                fallback_label=micro.get("SECONDARY_ROUTE") or micro.get("ROUTING_LABEL_2") or clean_value(micro.get("LOCATION_LABEL")),
            )
            if not road_b_queries:
                road_b_queries = [q for q in road_a_queries if q != station_or_line.lower()][:1]
            return {
                "type": micro_type,
                "symptom": micro.get("SYMPTOM"),
                "location_label": micro.get("LOCATION_LABEL"),
                "station_or_line": station_or_line,
                "road_a_queries": road_a_queries,
                "road_b_queries": road_b_queries,
                "road_a_label": clean_value(micro.get("road_a_label") or micro.get("ROAD_A_LABEL")) or (road_a_queries[0] if road_a_queries else station_or_line),
                "road_b_label": clean_value(micro.get("road_b_label") or micro.get("ROAD_B_LABEL")) or (road_b_queries[0] if road_b_queries else clean_value(micro.get("LOCATION_LABEL"))),
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
    
    def run_analysis_from_agent_output(self, agent_output, user_city, regions_path="regions.json", city_buffer_m=1000):
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
        attempts_errors = []

        for source_name, parsed in attempts:
            if parsed["type"] == "freight_route":
                label = (parsed.get("location_label") or "").lower()
                if "port klang" in label or "federal highway freight link" in label:
                    print(f"Skipping {source_name}: freight corridor is too broad for current city graph.")
                    continue

            routing_mode = "drive"
            if parsed.get("type") in ["feeder_route", "brt_corridor"]:
                routing_mode = "transit"
            elif parsed.get("type") in ["pedestrian_link", "transit_node"]:
                routing_mode = "walk"

            try:
                result = run_city_road_connection_analysis(
                    user_city=user_city,
                    road_a_queries=parsed["road_a_queries"],
                    road_b_queries=parsed["road_b_queries"],
                    regions_path=regions_path,
                    city_buffer_m=city_buffer_m,
                    routing_mode=routing_mode,
                )

                # GEOMETRIC SELF-CORRECTION LAYER
                best_cand = result["candidates"][0]
                dominant = best_cand.get("dominant_class", "unknown")
                
                if routing_mode == "walk" and dominant in ["motorway", "trunk"]:
                    print(f"Safety Conflict: Pedestrian link planned on {dominant}. Retrying with feedback.")
                    raise ValueError(f"The proposed pedestrian link is on a high-speed {dominant}. Please suggest a route that uses residential roads or specific crossings.")
                
                if routing_mode == "transit" and dominant == "motorway" and parsed.get("type") == "feeder_route":
                    print(f"Operational Conflict: Feeder bus planned on motorway. Retrying.")
                    raise ValueError(f"Feeder buses should serve local/collector roads, not motorways. Please re-route via residential or secondary roads.")

                result["selected_micro_source"] = source_name
                result["selected_micro_type"] = parsed["type"]
                result["selected_micro_symptom"] = parsed.get("symptom")
                result["selected_micro_location_label"] = parsed.get("location_label")

                results.append(result)

            except Exception as e:
                print(f"{source_name} failed: {e}")
                attempts_errors.append(f"{source_name}: {str(e)}")

        if not results:
            error_msgs = "; ".join(attempts_errors)
            raise ValueError(f"Analysis failed for all attempts. Details: {error_msgs}")

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
dummy_reviewer = LlmAgent(
    name="dummy_reviewer",
    model=PLANNER_MODEL,
    description="Dummy reviewer to satisfy Pydantic.",
    instruction="Always return VERDICT: PASS"
)

planning_root = InfrastructurePlannerOrchestrator(
    name="infrastructure_planner_orchestrator",
    description="Orchestrates the infrastructure planner workflow with human checkpoints.",
    pipeline=[
        ("Find needs", find_needs_agent,  "top_challenges"),
        ("Generate solutions", solution_agent, "plan_solution"),
        ("Building simulations", building_agent,   "simulation_result")
        # ("Activity simulation", activity_agent,   "activity_simulation"),
        # ("Analysis",            analysis_agent,   "final_analysis"),
    ],
    app_name="infrastructure_planner",
    session_svc=session_service,
    reviewer=dummy_reviewer,
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

