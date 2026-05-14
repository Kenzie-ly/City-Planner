import asyncio
import json
import os
from dotenv import load_dotenv

# Mocking parts of the system if needed
from backend.agent import InfrastructurePlannerOrchestrator, find_needs_agent, dummy_reviewer, session_service
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import Session

load_dotenv()

async def verify_gap_fix():
    print("--- Verification: Code Gap Fix ---")
    
    # 1. Create a session with target_places set
    session = await session_service.create_session(
        app_name="infrastructure_planner",
        user_id="verifier",
        state={
            "target_places": ["Kuala Lumpur"],
            "feedback": ""
        }
    )
    
    orchestrator = InfrastructurePlannerOrchestrator(
        name="test_orch",
        pipeline=[("Find needs", find_needs_agent, "top_challenges")],
        reviewer=dummy_reviewer,
        app_name="infrastructure_planner",
        session_svc=session_service
    )
    
    # Mocking InvocationContext is hard, let's just create a dummy one with required fields
    class DummyContext:
        def __init__(self, session):
            self.session = session
            self.session_service = session_service
            self.invocation_id = "test_inv"
            self.agent = orchestrator

    ctx = DummyContext(session=session)
    
    print("Simulating step_name == 'Find needs' logic...")
    
    # Manually execute the logic we added to agent.py
    # Since I've already modified agent.py, I can try to run the code directly here 
    # to see if it works without errors.
    
    from backend.area_resolver import resolve_area
    from backend.indicator_engine import run_indicator_engine
    from backend.evidence_pack_builder.evidence_pack_builder import build_general_evidence_pack

    target_places = ctx.session.state.get("target_places", [])
    main_city = target_places[0]
    
    print(f"Resolving area for: {main_city}")
    area_info = resolve_area(main_city)
    area_id = area_info["area_id"]
    ctx.session.state["area_id"] = area_id
    print(f"Area ID: {area_id}")

    print("Running indicator engine...")
    run_indicator_engine(area_id)

    print("Building General Evidence Pack...")
    evidence_pack = build_general_evidence_pack(area_id)
    pack_data = evidence_pack.to_dict()
    ctx.session.state["general_evidence_pack"] = pack_data
    
    print(f"Evidence Pack generated with {len(pack_data.get('indicators', {}))} indicators.")
    
    step_prompt = f"""
    Identify and rank the top 3 infrastructure-related challenges for {main_city}.
    
    Use the provided GENERAL EVIDENCE PACK as your primary ground truth.
    
    GENERAL EVIDENCE PACK:
    {json.dumps(pack_data, indent=2)}
    """.strip()
    
    print("SUCCESS: Prompt generated with Evidence Pack.")
    # print(f"Prompt snippet: {step_prompt[:200]}...")

if __name__ == "__main__":
    asyncio.run(verify_gap_fix())
