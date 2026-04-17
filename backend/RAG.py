personas = {
    "sustainability": "You are a city planner who prioritises environmental sustainability.",
    "developer":      "You are a profit-driven property developer.",
    "citizen":        "You prioritise community livability and wellbeing.",
    "road_planner":   "You are a transport engineer planning road networks in Kuala Lumpur.",
    "evaluator":      "You are a strict policy compliance officer evaluating infrastructure plans.",
}

def load_policies(path="policies.txt"):
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]

def retrieve_policies(query, top_k=5):
    policies = load_policies()
    if len(policies) <= 15:
        return policies
    query_words = set(query.lower().split())
    scored = [(sum(1 for w in query_words if w in p.lower()), p) for p in policies]
    scored.sort(reverse=True)
    relevant = [p for score, p in scored if score > 0]
    return (relevant or policies)[:top_k]