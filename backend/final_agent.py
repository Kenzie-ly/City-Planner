from google.adk.agents import LlmAgent, BaseAgent, SequentialAgent
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
from RAG import personas

persona = personas["sustainability"]

# Load API key from .env file (Google ADK will use this automatically)
load_dotenv()


# ── Step agents ───────────────────────────────────────────────────────────────

find_area_agent = LlmAgent(
    name="find_area_agent",
    model="gemini-2.5-pro",
    description="Finds an area that needs infrastructure improvement.",
    instruction="""Find one area in session.state["city"] that needs infrastructure improvement.
If session.state["feedback"] exists, revise accordingly.
Output: area name, current problems, selection reason.""",
    output_key="selected_area",
)

planner_agent = LlmAgent(
    name="planner_agent",
    model="gemini-2.5-pro",
    description="Gives ways to improve the infrastructure in a specific area.",
    instruction="""Plan improvements for session.state["selected_area"].
If session.state["feedback"] exists, revise accordingly.
Propose 3 approaches with costs and timelines, numbered.""",
    output_key="improvement_plan",
)

building_agent = LlmAgent(
    name="building_agent",
    model="gemini-2.5-pro",
    description="Simulates the result on the map based on the chosen approach.",
    instruction="""Simulate the area after applying session.state["improvement_plan"].
If session.state["feedback"] exists, adjust accordingly.
Describe: new/removed structures, road changes, green spaces.""",
    output_key="simulation_result",
)

activity_agent = LlmAgent(
    name="activity_agent",
    model="gemini-2.5-pro",
    description="Simulates how interactions or environment changes with new infrastructure.",
    instruction="""Simulate human activity in session.state["simulation_result"].
If session.state["feedback"] exists, revise accordingly.
Show: pedestrian flow, traffic, business activity, community events.""",
    output_key="activity_simulation",
)

analysis_agent = LlmAgent(
    name="analysis_agent",
    model="gemini-2.5-pro",
    description="Analyzes how the improvement changes key fields in the simulated infrastructure.",
    instruction="""Analyze session.state["activity_simulation"].
If session.state["feedback"] exists, deepen the analysis accordingly.
Score (1-10) for: safety, economic growth, environment, accessibility, wellbeing. Include justification.""",
    output_key="final_analysis",
)

# ── Review agent ──────────────────────────────────────────────────────────────
#
# Shared across all steps. Reads the step output + user's raw response,
# decides PASS or REVISE, and enriches vague feedback into a clear instruction.

review_agent = LlmAgent(
    name="review_agent",
    model="gemini-2.5-pro",
    description="Interprets user feedback on a pipeline step and decides PASS or REVISE.",
    instruction="""Decide PASS or REVISE from user feedback about a pipeline step.
PASS for approval (yes, ok, looks good, approved, fine, correct).
REVISE for changes needed.
Format:
VERDICT: PASS
REASON: <short reason>
OR
VERDICT: REVISE
INSTRUCTION: <specific actionable instruction>""",
)


# ── Custom orchestrator with checkpoints ──────────────────────────────────────
#
# Replaces SequentialAgent. Each step has its own while loop:
#   run step → collect output → send to review_agent with user's response
#   → PASS: advance   → REVISE: save enriched instruction, rerun same step

class InfrastructurePlannerOrchestrator(BaseAgent):
    """
    Runs each pipeline step with a human-in-the-loop checkpoint.
    The review_agent interprets the user's natural language response
    and decides whether to approve or request a revision.
    """

    # Pydantic requires these to be declared as fields on BaseAgent subclasses
    pipeline: list      # list of (step_name, agent, output_key)
    reviewer: LlmAgent
    app_name: str
    session_svc: object

    model_config = ConfigDict(arbitrary_types_allowed=True)

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:

        for step_name, step_agent, output_key in self.pipeline:

            ctx.session.state["feedback"] = ""
            attempt = 0

            while True:
                attempt += 1

                # ── 1. Build step prompt ──────────────────────────────────
                if attempt == 1:
                    step_prompt = f"Please run the {step_name} step now."
                else:
                    step_prompt = (
                        f"The previous {step_name} output was rejected. "
                        f"Follow this specific instruction to revise it: "
                        f"\"{ctx.session.state['feedback']}\""
                    )

                # ── 2. Run the step agent ─────────────────────────────────
                step_output = await self._invoke_agent(
                    step_agent, ctx.session.id, step_prompt
                )

                # 🔥 THIS LINE IS MISSING
                ctx.session.state[output_key] = step_output

                # ── 3. Yield the step result as an event so the caller
                #       (your web app) can display it to the user
                yield Event(
                    author=step_agent.name,
                    content=types.Content(
                        role="assistant",
                        parts=[types.Part(text=step_output)],
                    ),
                )

                # ── 4. Wait for the user's response.
                #       In a real web app you would await a websocket/HTTP
                #       message here instead of reading from stdin.
                user_response = await self._wait_for_user(
                    ctx, step_name, step_output
                )

                # ── 5. Review agent interprets the response ───────────────
                review_prompt = (
                    f"STEP NAME: {step_name}\n\n"
                    f"STEP OUTPUT:\n{step_output}\n\n"
                    f"USER RESPONSE: {user_response}"
                )
                review_text = await self._invoke_agent(
                    self.reviewer, ctx.session.id, review_prompt
                )
                verdict, detail = self._parse_review(review_text)

                if verdict == "PASS":
                    # Advance to the next step
                    ctx.session.state["feedback"] = ""
                    break
                else:
                    # Rerun only this step with the enriched instruction
                    ctx.session.state["feedback"] = detail
                    # loop continues — next step does NOT start

    # ── Helpers ───────────────────────────────────────────────────────────

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
        self, ctx: InvocationContext, step_name: str, output: str
    ) -> str:
        """
        In a CLI this reads from stdin.
        In a web app, replace this with:
          return await websocket.receive_text()
        or however your frontend sends messages back.
        """
        #the result of the output is printed here!
        print(f"\n[{step_name}] Your feedback (or 'ok' to approve): ", end="")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, input)

    @staticmethod
    def _parse_review(text: str) -> tuple[str, str]:
        verdict = "REVISE"
        detail  = ""
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("VERDICT:"):
                verdict = line.split(":", 1)[1].strip().upper()
            elif line.startswith("REASON:") or line.startswith("INSTRUCTION:"):
                detail = line.split(":", 1)[1].strip()
        return verdict, detail


# ── Wire everything up ────────────────────────────────────────────────────────

session_service = InMemorySessionService()

root_agent = InfrastructurePlannerOrchestrator(
    name="infrastructure_planner_orchestrator",
    description="Orchestrates the entire infrastructure planner workflow with human checkpoints.",
    pipeline=[
        ("Find area",           find_area_agent,  "selected_area"),
        ("Plan improvements",   planner_agent,    "improvement_plan"),
        ("Building simulation", building_agent,   "simulation_result"),
        ("Activity simulation", activity_agent,   "activity_simulation"),
        ("Analysis",            analysis_agent,   "final_analysis"),
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
    )

    runner = Runner(
        agent=root_agent,
        app_name="infrastructure_planner",
        session_service=session_service,
    )
    async for event in runner.run_async(
        user_id="user_001",
        session_id=session.id,
        new_message=types.Content(
            role="user",
            parts=[types.Part(text="Start the infrastructure planning workflow.")],
        ),
    ):
        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    print(f"\n[Final] {part.text}")


if __name__ == "__main__":
    asyncio.run(main())