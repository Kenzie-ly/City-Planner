"""
Test: Verify that duplicate entity IDs from overlapping context queries
are properly eliminated before being sent to Cesium.

This simulates the exact real-world scenario from the bug report:
  - data.map_layers.context contains bus stops from get_context_infrastructure(main_hub)
  - data.implementation_clusters[].context contains bus stops from get_context_infrastructure(feeder_hub)
  - The two circles overlap, so the same bus stop appears in BOTH
"""

import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

# ─────────────────────────────────────────────
# SIMULATE: The backend payload the frontend receives
# ─────────────────────────────────────────────

DUPLICATE_ID = "db_bus_stop_1.4752464_103.7479191"

fake_backend_payload = {
    "stage": "done",
    "show_map": True,
    "entities": [
        {"id": "proposal_lrt_1", "entity_type": "polyline", "name": "New LRT Line"},
    ],
    "map_layers": {
        "context": [
            # These come from get_context_infrastructure(main_hub)
            {"id": DUPLICATE_ID,           "entity_type": "point", "name": "Taman Jaya Bus Stop"},
            {"id": "db_bus_stop_1.476_103.748", "entity_type": "point", "name": "PJ Bus Stop"},
        ],
        "proposal": [
            {"id": "proposal_lrt_1", "entity_type": "polyline", "name": "New LRT Line"},
        ],
        "anchors": [],
        "analysis": [],
    },
    "implementation_clusters": [
        {
            "center": {"lat": 1.4752, "lon": 103.747, "LOCATION_LABEL": "Main Hub"},
            "label": "Main Hub",
            "intervention_type": "BUS",
            "intervention_rationale": "High demand area",
            "context": [
                # DUPLICATE! Also returned by get_context_infrastructure(feeder_hub) because circles overlap
                {"id": DUPLICATE_ID,           "entity_type": "point", "name": "Taman Jaya Bus Stop"},
                {"id": "db_bus_stop_1.480_103.750", "entity_type": "point", "name": "Feeder Area Stop"},
            ],
            "linked_feeder": None,
            "feeder_context": []
        }
    ]
}

# ─────────────────────────────────────────────
# SIMULATE: renderImplementationClusters() (Python equivalent of JS function)
# Returns POIs for the cluster center + its context nodes
# ─────────────────────────────────────────────

def simulate_render_implementation_clusters(clusters):
    """Mimics what renderImplementationClusters() does in cesiumRenderer logic."""
    entities = []
    for idx, cluster in enumerate(clusters):
        center = cluster["center"]
        entities.append({
            "entity_type": "poi",
            "id": f"center_{idx}",
            "label": cluster["label"],
            "lat": center["lat"],
            "lon": center["lon"],
        })
        for ctx in cluster.get("context", []):
            entities.append(ctx)  # These are the DUPLICATE bus stops!
    return entities


# ─────────────────────────────────────────────
# SIMULATE: The OLD buggy frontend merge logic (what caused the crash)
# ─────────────────────────────────────────────

def old_buggy_merge(data):
    cluster_entities = simulate_render_implementation_clusters(data["implementation_clusters"])
    if not isinstance(data.get("entities"), list):
        data["entities"] = []
    data["entities"].extend(cluster_entities)

    # Collect all entities that applyMapPayload would render
    all_entities = []
    for layer in ["proposal", "anchors", "context", "analysis"]:
        all_entities.extend(data["map_layers"].get(layer, []))
    all_entities.extend(data["entities"])
    return all_entities


# ─────────────────────────────────────────────
# SIMULATE: The NEW fixed frontend merge logic
# ─────────────────────────────────────────────

def new_fixed_merge(data):
    cluster_entities = simulate_render_implementation_clusters(data["implementation_clusters"])
    if cluster_entities:
        # Build set of ALL IDs already in any map layer
        existing_ids = set()
        for layer in data["map_layers"].values():
            if isinstance(layer, list):
                existing_ids.update(e["id"] for e in layer)
        # Also collect IDs already in data.entities
        existing_ids.update(e["id"] for e in (data.get("entities") or []))

        unique_cluster_entities = [e for e in cluster_entities if e["id"] not in existing_ids]
        duplicates_removed = len(cluster_entities) - len(unique_cluster_entities)
        print(f"   [DEDUP] Cluster entities: {len(cluster_entities)} total, {len(unique_cluster_entities)} unique, {duplicates_removed} duplicates removed")

        if not isinstance(data.get("entities"), list):
            data["entities"] = []
        data["entities"].extend(unique_cluster_entities)

    # Simulates applyMapPayload() — collect from map_layers first
    entities = []
    for layer in ["proposal", "anchors", "context", "analysis"]:
        entities.extend(data["map_layers"].get(layer, []))
    # Then merge data.entities (cluster POIs)
    if data.get("entities"):
        entities.extend(data["entities"])

    # ✅ Final dedup pass (mirrors the Set filter in applyMapPayload)
    seen = set()
    deduped = []
    for e in entities:
        if e["id"] not in seen:
            seen.add(e["id"])
            deduped.append(e)
    print(f"   [FINAL] Before dedup: {len(entities)}, after: {len(deduped)} ({len(entities)-len(deduped)} removed)")
    return deduped


# ─────────────────────────────────────────────
# SIMULATE: Cesium entity collection (raises on duplicate)
# ─────────────────────────────────────────────

def simulate_cesium_add_all(all_entities):
    cesium_collection = {}
    for entity in all_entities:
        eid = entity["id"]
        if eid in cesium_collection:
            raise RuntimeError(f"An entity with id {eid} already exists in this collection.")
        cesium_collection[eid] = entity
    return cesium_collection


# ─────────────────────────────────────────────
# RUN TESTS
# ─────────────────────────────────────────────

import copy

print("=" * 60)
print("TEST 1: OLD buggy logic (should CRASH)")
print("=" * 60)
try:
    data_old = copy.deepcopy(fake_backend_payload)
    all_entities_old = old_buggy_merge(data_old)
    cesium_old = simulate_cesium_add_all(all_entities_old)
    print("   RESULT: No crash (unexpected!)")
except RuntimeError as e:
    print(f"   RESULT: CRASH as expected! Error: {e}")

print()
print("=" * 60)
print("TEST 2: NEW fixed logic (should PASS without crash)")
print("=" * 60)
try:
    data_new = copy.deepcopy(fake_backend_payload)
    all_entities_new = new_fixed_merge(data_new)
    cesium_new = simulate_cesium_add_all(all_entities_new)
    print(f"   RESULT: SUCCESS! {len(cesium_new)} unique entities rendered:")
    for eid in cesium_new:
        print(f"     - {eid}")
except RuntimeError as e:
    print(f"   RESULT: CRASH (fix failed!): {e}")

print()
print("=" * 60)
print("TEST 3: Verify the duplicate bus stop is NOT double-rendered")
print("=" * 60)
data_check = copy.deepcopy(fake_backend_payload)
all_entities_check = new_fixed_merge(data_check)
ids = [e["id"] for e in all_entities_check]
has_duplicate = len(ids) != len(set(ids))
if has_duplicate:
    from collections import Counter
    dupes = [eid for eid, count in Counter(ids).items() if count > 1]
    print(f"   RESULT: FAIL - found duplicates: {dupes}")
else:
    print(f"   RESULT: PASS - all {len(ids)} entity IDs are unique!")
    print(f"   The duplicate bus stop ({DUPLICATE_ID}) appears exactly once.")
