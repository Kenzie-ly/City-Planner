import uuid
from datetime import datetime

def build_evidence_pack(
    area_id: str,
    indicators: dict,
    rag_chunks: list[dict],
    custom_rules: list[str] | None = None,
    blocked_claims: list[str] | None = None
) -> dict:
    """
    Step 4 of the Pipeline: Evidence Pack Builder.
    Combines structured indicators + RAG support and adds guardrails for the AI.
    """
    # Generate a unique ID for this evidence pack
    pack_id = f"ev_pack_{uuid.uuid4().hex[:8]}"
    
    # Default rules to ensure factuality
    rules = [
        "Structured data (SQL) is the PRIMARY evidence and must be trusted over RAG news.",
        "You must cite at least one specific RAG source by title in your summary.",
        "If RAG evidence contradicts SQL data, prioritize the SQL data.",
    ]
    if custom_rules:
        rules.extend(custom_rules)
        
    # Default blocked claims (to prevent hallucinations we saw earlier!)
    blocked = [
        "Do not invent exact percentage growth numbers unless explicitly stated in the evidence.",
        "Do not claim a service is 'excellent' if there are active complaints in the pack.",
    ]
    if blocked_claims:
        blocked.extend(blocked_claims)
        
    # Build the final pack
    evidence_pack = {
        "evidence_pack_id": pack_id,
        "area_id": area_id,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "structured_indicators": indicators,
        "rag_support": rag_chunks,
        "rules": rules,
        "blocked_claims": blocked
    }
    
    # In a real scenario, you might save this pack to Redis or a DB here.
    # For now, we return it directly so the next agent can read it.
    
    return evidence_pack
