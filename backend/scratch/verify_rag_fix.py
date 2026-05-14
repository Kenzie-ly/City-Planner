import asyncio
import json
import os
from dotenv import load_dotenv

# Mocking parts of the system if needed
from backend.agent import InfrastructurePlannerOrchestrator, find_needs_agent, dummy_reviewer, session_service
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import Session

load_dotenv()

async def verify_rag_inclusion():
    print("--- Verification: RAG Inclusion in General Evidence Pack ---")
    
    # 1. Create a session with target_places set
    session = await session_service.create_session(
        app_name="infrastructure_planner",
        user_id="verifier",
        state={
            "target_places": ["Kuala Lumpur"],
            "feedback": ""
        }
    )
    
    from backend.area_resolver import resolve_area
    from backend.indicator_engine import run_indicator_engine
    from backend.evidence_pack_builder.evidence_pack_builder import build_general_evidence_pack

    target_places = session.state.get("target_places", [])
    main_city = target_places[0]
    
    print(f"Resolving area for: {main_city}")
    area_info = resolve_area(main_city)
    area_id = area_info["area_id"]
    
    print("Running indicator engine...")
    run_indicator_engine(area_id)

    print("Building General Evidence Pack...")
    evidence_pack = build_general_evidence_pack(area_id)
    pack_data = evidence_pack.to_dict()
    
    rag_support = pack_data.get("rag_support", {})
    print(f"RAG Support Enabled: {rag_support.get('enabled')}")
    print(f"RAG Message: {rag_support.get('message')}")
    
    challenges = rag_support.get("rag_by_challenge", [])
    if challenges:
        for ch in challenges:
            print(f"Challenge Category: {ch.get('challenge')}")
            chunks = ch.get("chunks", [])
            print(f"Number of RAG Chunks: {len(chunks)}")
            if chunks:
                print(f"Sample Chunk Text: {chunks[0].get('chunk_text')[:100]}...")
                print(f"Sample Source: {chunks[0].get('title')}")
    else:
        print("WARNING: No RAG challenges found in the pack.")

    if rag_support.get("enabled") and challenges and len(challenges[0].get("chunks", [])) > 0:
        print("\nSUCCESS: RAG content is correctly included in the General Evidence Pack.")
    else:
        print("\nFAILURE: RAG content is missing or empty.")

if __name__ == "__main__":
    asyncio.run(verify_rag_inclusion())
