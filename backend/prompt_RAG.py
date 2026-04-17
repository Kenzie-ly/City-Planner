from RAG import personas, retrieve_policies

def build_prompt(plan, persona_key="sustainability"):
    persona = personas[persona_key]
    policies = retrieve_policies(plan)
    policy_text = "\n".join(f"- {p}" for p in policies)

    return f"""{persona}
Your name is City Planner AI.
Task: Evaluate the city infrastructure plan below against the rules.

Rules:
{policy_text}

Plan:
{plan}

Instructions:
- Check each rule one by one against the plan.
- Only flag violations explicitly stated in the plan.
- Do not assume or invent information not present in the plan.
- If any rule is violated, respond: REJECTED — [rule number]: [reason].
- If no rules are violated, respond: APPROVED — [brief reason].
- Be concise. Do not repeat the rules."""