import osmnx as ox
import pandas as pd
import re
import json
import sys
import os

# Mock the functions from FindRoads.py to test them
def normalize_text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).lower()
    replacements = ["jalan", "jln", "lebuhraya", "jalan raya", "persiaran"]
    for r in replacements:
        text = text.replace(r, "")
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r'[^a-z0-9 ]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def _join_listlike(val):
    if isinstance(val, list):
        return " ".join(str(x) for x in val if pd.notna(x))
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    return str(val)

def prepare_edge_text_columns(edges: pd.DataFrame) -> pd.DataFrame:
    edges = edges.copy()
    candidate_cols = ["name", "name:en", "official_name", "alt_name", "short_name", "ref"]
    for col in candidate_cols:
        if col not in edges.columns:
            edges[col] = ""
    edges["search_text"] = edges.apply(
        lambda row: normalize_text(" ".join(_join_listlike(row[col]) for col in candidate_cols)),
        axis=1
    )
    return edges

def token_match(q: str, text: str) -> bool:
    q_tokens = set(q.split())
    text_tokens = set(text.split())
    return len(q_tokens & text_tokens) >= 1

def match_road_edges(edges: pd.DataFrame, queries: list[str]) -> pd.DataFrame:
    mask = pd.Series(False, index=edges.index)
    queries_norm = [normalize_text(q) for q in queries]
    for qn in queries_norm:
        if not qn: continue
        mask |= edges["search_text"].str.contains(re.escape(qn), na=False)
        mask |= edges["search_text"].apply(lambda x: token_match(qn, x))
    return edges[mask].copy()

def debug_kl():
    graph_path = "backend/data/graphs/kuala_lumpur.graphml"
    if not os.path.exists(graph_path):
        # try without backend prefix if running from inside backend
        graph_path = "data/graphs/kuala_lumpur.graphml"
    
    print(f"Loading {graph_path}...")
    G = ox.load_graphml(graph_path)
    nodes, edges = ox.graph_to_gdfs(G)
    edges = prepare_edge_text_columns(edges)
    
    test_queries = ["Jalan Tun Razak", "Jalan Ampang"]
    for q in test_queries:
        matched = match_road_edges(edges, [q])
        print(f"Query: {q} -> Matched: {len(matched)} edges")
        if not matched.empty:
            print("Sample names:", matched["name"].unique()[:5])

if __name__ == "__main__":
    debug_kl()
