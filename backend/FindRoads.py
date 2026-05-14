from __future__ import annotations

import json
import re
from dataclasses import dataclass

import networkx as nx
import osmnx as ox
import pandas as pd
from pyproj import CRS, Transformer
from shapely.geometry import LineString, MultiPolygon, Polygon, Point, MultiPoint
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, transform, unary_union
import pandas as pd

# =========================================================
# CONFIG
# =========================================================

@dataclass
class CorridorConfig:
    line_buffer_m: float = 1200
    road_buffer_m: float = 300
    simplify_tolerance_m: float = 20


# =========================================================
# REGION LOOKUP
# =========================================================

def find_region_for_city(city_name: str, regions_path: str = "regions.json") -> dict:
    city_norm = city_name.strip().lower()

    with open(regions_path, "r", encoding="utf-8") as f:
        regions = json.load(f)

    for region_name, cfg in regions.items():
        for city in cfg["cities"]:
            if city.lower() == city_norm:
                return {"region_name": region_name, **cfg}

    raise ValueError(f"No region mapping found for city: {city_name}")


# =========================================================
# GEOMETRY / PROJECTION HELPERS
# =========================================================

def _make_local_transformers(center_lon: float, center_lat: float):
    local_crs = CRS.from_proj4(
        f"+proj=aeqd +lat_0={center_lat} +lon_0={center_lon} "
        "+datum=WGS84 +units=m +no_defs"
    )
    to_local = Transformer.from_crs("EPSG:4326", local_crs, always_xy=True)
    to_wgs84 = Transformer.from_crs(local_crs, "EPSG:4326", always_xy=True)
    return to_local, to_wgs84


def _project_geom(geom: BaseGeometry, transformer: Transformer) -> BaseGeometry:
    return transform(transformer.transform, geom)


def buffer_wgs84_geometry_in_meters(geom: BaseGeometry, buffer_m: float) -> BaseGeometry:
    center = geom.centroid
    to_local, to_wgs84 = _make_local_transformers(center.x, center.y)

    geom_local = _project_geom(geom, to_local)
    buffered_local = geom_local.buffer(buffer_m)
    return _project_geom(buffered_local, to_wgs84)


# backward-compatible name if you already used it elsewhere
def buffer_wgs84_polygon_in_meters(geom: BaseGeometry, buffer_m: float) -> BaseGeometry:
    return buffer_wgs84_geometry_in_meters(geom, buffer_m)


# =========================================================
# GRAPH CLIPPING
# =========================================================

def clip_graph_to_polygon(G_region, polygon: BaseGeometry):
    nodes_gdf, edges_gdf = ox.graph_to_gdfs(G_region)

    candidate_idx = edges_gdf.sindex.query(polygon, predicate="intersects")
    edges_sub = edges_gdf.iloc[candidate_idx].copy()

    if edges_sub.empty:
        raise ValueError("No graph edges intersect the provided polygon.")

    used_u = edges_sub.index.get_level_values(0)
    used_v = edges_sub.index.get_level_values(1)
    used_node_ids = set(used_u).union(set(used_v))

    nodes_sub = nodes_gdf.loc[nodes_gdf.index.intersection(used_node_ids)].copy()

    if nodes_sub.empty:
        raise ValueError("No nodes remain after clipping.")

    G_sub = ox.graph_from_gdfs(nodes_sub, edges_sub)
    return G_sub, nodes_sub, edges_sub


# =========================================================
# ROAD MATCHING
# =========================================================

def normalize_text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).lower()
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _join_listlike(val):
    if isinstance(val, list):
        return " ".join(str(x) for x in val if pd.notna(x))
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    return str(val)

def prepare_edge_text_columns(edges: pd.DataFrame) -> pd.DataFrame:
    edges = edges.copy()

    candidate_cols = [
        "name",
        "name:en",
        "official_name",
        "alt_name",
        "short_name",
        "ref",
    ]

    for col in candidate_cols:
        if col not in edges.columns:
            edges[col] = ""

    # keep individual normalized fields if you still want them
    edges["name_norm"] = edges["name"].apply(normalize_text)
    edges["ref_norm"] = edges["ref"].apply(normalize_text)
    edges["name_en_norm"] = edges["name:en"].apply(normalize_text)
    edges["short_name_norm"] = edges["short_name"].apply(normalize_text)

    # combine all possible labels into one searchable field
    edges["search_text"] = edges.apply(
        lambda row: normalize_text(" ".join(
            _join_listlike(row[col]) for col in candidate_cols
        )),
        axis=1
    )

    return edges


GENERIC_ROAD_TOKENS = {
    "jalan", "jln", "road", "rd", "street", "st", "lebuhraya", "highway",
    "route", "lorong", "persiaran", "jalanraya", "jalanraya", "ft", "e",
    "interchange", "junction", "near", "station", "stesen"
}


def _meaningful_tokens(value: str) -> list[str]:
    parts = re.split(r"[^a-z0-9]+", normalize_text(value))
    return [p for p in parts if p and p not in GENERIC_ROAD_TOKENS and len(p) >= 2]


def token_match(q: str, text: str) -> bool:
    q_tokens = set(_meaningful_tokens(q))
    text_tokens = set(_meaningful_tokens(text))
    if not q_tokens:
        return False
    overlap = q_tokens & text_tokens
    if len(q_tokens) == 1:
        return len(overlap) == 1
    return len(overlap) >= 2 or overlap == q_tokens


def match_road_edges(edges: pd.DataFrame, queries: list[str]) -> pd.DataFrame:
    mask = pd.Series(False, index=edges.index)

    queries_norm = [normalize_text(q) for q in queries]

    for qn in queries_norm:
        if not qn:
            continue

        contains_mask = edges["search_text"].str.contains(re.escape(qn), na=False)
        if contains_mask.any():
            mask |= contains_mask
            continue

        meaningful = _meaningful_tokens(qn)
        if meaningful:
            mask |= edges["search_text"].apply(lambda x: token_match(qn, x))
            if not mask.any() and edges.get("ref_norm") is not None:
                for token in meaningful:
                    if token.isdigit() or re.fullmatch(r"[a-z]{1,3}\d+[a-z]*", token):
                        mask |= edges["ref_norm"].str.contains(re.escape(token), na=False)

    matched = edges[mask].copy()

    if matched.empty:
        raise ValueError(f"No roads matched for queries: {queries}. Search text sample: {edges['search_text'].head(5).tolist()}")

    return matched




QUERY_GENERIC_TOKENS = GENERIC_ROAD_TOKENS | {"access", "precinct", "area", "node", "hub"}

def _query_token_set(values: list[str]) -> set[str]:
    tokens: set[str] = set()
    for value in values or []:
        for token in re.split(r"[^a-z0-9]+", normalize_text(value)):
            if token and token not in QUERY_GENERIC_TOKENS and len(token) >= 2:
                tokens.add(token)
    return tokens


def query_sets_too_similar(road_a_queries: list[str], road_b_queries: list[str]) -> bool:
    a_tokens = _query_token_set(road_a_queries)
    b_tokens = _query_token_set(road_b_queries)
    if not a_tokens or not b_tokens:
        return False
    if a_tokens == b_tokens:
        return True
    overlap = a_tokens & b_tokens
    if not overlap:
        return False
    overlap_ratio = max(len(overlap) / max(len(a_tokens), 1), len(overlap) / max(len(b_tokens), 1))
    return overlap_ratio >= 0.8

def get_road_node_ids(road_edges: pd.DataFrame) -> set:
    u_nodes = set(road_edges.index.get_level_values(0))
    v_nodes = set(road_edges.index.get_level_values(1))
    return u_nodes.union(v_nodes)


# =========================================================
# GRAPH-BASED ROAD-TO-ROAD CONNECTION
# =========================================================

def find_best_connection_between_roads(
    G,
    road_a_nodes: set,
    road_b_nodes: set,
    weight: str = "length",
    routing_mode: str = "drive"
):
    print(f"Finding best connection (Mode: {routing_mode})...")

    custom_weight = weight

    # =========================
    # Weight adjustments
    # =========================
    if routing_mode == "transit":
        custom_weight = "transit_weight"
        for u, v, k, data in G.edges(data=True, keys=True):
            has_transit = any(
                data.get(tag) and data.get(tag) != "no"
                for tag in ["bus", "psv", "busway", "lanes:bus"]
            )
            data["transit_weight"] = data.get("length", 1.0) * (0.5 if has_transit else 1.5)

    elif routing_mode == "walk":
        custom_weight = "walk_weight"
        for u, v, k, data in G.edges(data=True, keys=True):
            hw = str(data.get("highway", "")).lower()

            is_pedestrian = any(x in hw for x in [
                "footway", "path", "pedestrian", "residential",
                "living_street", "steps"
            ])

            is_high_speed = any(x in hw for x in [
                "motorway", "trunk", "primary"
            ])

            penalty = 1.0
            if is_pedestrian:
                penalty = 0.4
            if is_high_speed:
                penalty = 5.0

            data["walk_weight"] = data.get("length", 1.0) * penalty

    try:
        # =========================
        # Multi-source Dijkstra
        # =========================
        lengths, paths = nx.multi_source_dijkstra(
            G,
            road_a_nodes,
            weight=custom_weight
        )

        best_target = None
        min_dist = float("inf")

        for b in road_b_nodes:
            if b in lengths and lengths[b] < min_dist:
                min_dist = lengths[b]
                best_target = b

        if best_target is None:
            print("No path found between road sets.")
            return None, None, None

        best_path = paths[best_target]
        best_pair = (best_path[0], best_target)

        # =========================
        # FIXED distance calculation
        # =========================
        actual_dist = lengths[best_target]

        if routing_mode != "drive":
            actual_dist = 0.0

            for i in range(len(best_path) - 1):
                u = best_path[i]
                v = best_path[i + 1]

                edge_data = G.get_edge_data(u, v)

                if not edge_data:
                    continue

                # MultiDiGraph → pick shortest edge
                min_length = float("inf")

                for key, data in edge_data.items():
                    length = data.get("length")
                    if length is not None:
                        min_length = min(min_length, length)

                if min_length != float("inf"):
                    actual_dist += min_length

            # 🔒 fallback safeguard (CRITICAL FIX)
            if actual_dist <= 0:
                actual_dist = lengths[best_target]

        # =========================
        # Collapse guard (safer)
        # =========================
        if len(best_path) < 2 or actual_dist <= 1.0:
            # Only treat as collapsed if BOTH are bad
            if lengths[best_target] <= 1.0:
                print("Collapsed connection found; treating as invalid route.")
                return best_pair, best_path, 0.0

        print(f"Connection found! Length: {actual_dist:.2f}m")
        return best_pair, best_path, actual_dist

    except Exception as e:
        print(f"Error in shortest path: {e}")
        return None, None, None


# =========================================================
# OPTIONAL CORRIDOR GEOMETRY & CATCHMENT
# =========================================================

def generate_isochrone_polygon(G, center_node, distance_m=400, weight='length'):
    try:
        subgraph = nx.ego_graph(G, center_node, radius=distance_m, distance=weight)
        node_points = [Point(data['x'], data['y']) for node, data in subgraph.nodes(data=True)]
        if len(node_points) < 3:
            return None
        return MultiPoint(node_points).convex_hull
    except Exception as e:
        print(f"Failed to generate isochrone: {e}")
        return None

def build_corridor_polygon_from_two_roads(
    road_a_geom: BaseGeometry,
    road_b_geom: BaseGeometry,
    config: CorridorConfig
):
    anchor_a, anchor_b = nearest_points(road_a_geom, road_b_geom)

    center_lon = (anchor_a.x + anchor_b.x) / 2
    center_lat = (anchor_a.y + anchor_b.y) / 2
    to_local, to_wgs84 = _make_local_transformers(center_lon, center_lat)

    road_a_local = _project_geom(road_a_geom, to_local)
    road_b_local = _project_geom(road_b_geom, to_local)
    anchor_a_local = _project_geom(anchor_a, to_local)
    anchor_b_local = _project_geom(anchor_b, to_local)

    baseline = LineString([anchor_a_local, anchor_b_local])

    line_buffer = baseline.buffer(config.line_buffer_m)
    road_a_buffer = road_a_local.buffer(config.road_buffer_m)
    road_b_buffer = road_b_local.buffer(config.road_buffer_m)

    corridor_local = unary_union([line_buffer, road_a_buffer, road_b_buffer])

    if config.simplify_tolerance_m > 0:
        corridor_local = corridor_local.simplify(
            config.simplify_tolerance_m,
            preserve_topology=True
        )

    corridor_wgs84 = _project_geom(corridor_local, to_wgs84)

    if not isinstance(corridor_wgs84, (Polygon, MultiPolygon)):
        raise TypeError("Invalid corridor geometry")

    return corridor_wgs84, anchor_a, anchor_b


# =========================================================
# ROUTE DISPLAY HELPERS
# =========================================================

def safe_route_to_gdf(G, path: list[int], weight: str = "length"):
    if path is None or len(path) <= 1:
        return pd.DataFrame(columns=["name", "ref", "length", "highway", "geometry"])

    try:
        return ox.routing.route_to_gdf(G, path, weight=weight)
    except Exception:
        return pd.DataFrame(columns=["name", "ref", "length", "highway", "geometry"])


def _build_route_result(
    *,
    candidates,
    isochrone_geoms,
    route_geometry,
    region,
    city_query,
    city_polygon,
    city_polygon_buffered,
    G_city,
    nodes_city,
    edges_city,
    road_a_edges,
    road_b_edges,
    road_a_geom,
    road_b_geom,
    mode,
    route_edges,
    route_length_m: float,
    anchor_points=None,
    route_valid: bool = True,
    route_status: str = "valid",
    route_error: str | None = None,
):
    return {
        "candidates": candidates,
        "isochrone_geoms": isochrone_geoms,
        "route_geometry": route_geometry,
        "region": region,
        "city_query": city_query,
        "city_polygon": city_polygon,
        "city_polygon_buffered": city_polygon_buffered,
        "G_city": G_city,
        "nodes_city": nodes_city,
        "edges_city": edges_city,
        "road_a_edges": road_a_edges,
        "road_b_edges": road_b_edges,
        "road_a_geom": road_a_geom,
        "road_b_geom": road_b_geom,
        "mode": mode,
        "route_edges": route_edges,
        "anchor_points": anchor_points or {},
        "route_valid": route_valid,
        "route_status": route_status,
        "route_length_m": float(route_length_m or 0.0),
        "route_error": route_error,
    }


def _collapsed_route_error() -> str:
    return "Anchors collapse to the same graph node or junction."


def print_route_summary(route_edges: pd.DataFrame):
    print("\nRoute columns:", route_edges.columns.tolist())

    cols_to_show = [c for c in ["name", "ref", "length", "highway"] if c in route_edges.columns]
    if cols_to_show:
        print(route_edges[cols_to_show])
    else:
        print(route_edges)

    total_length = route_edges["length"].sum() if "length" in route_edges.columns else None
    if total_length is not None:
        print(f"\nTotal route length: {total_length:.2f} m")


def edge_label(row):
    name = row.get("name")
    ref = row.get("ref")
    highway = row.get("highway")
    
    # Check for transit specific markers
    is_bus = False
    if any(tag in row for tag in ["bus", "psv", "busway", "lanes:bus"]):
        val = row.get("bus") or row.get("psv") or row.get("busway") or row.get("lanes:bus")
        if pd.notna(val) and val != "no":
            is_bus = True

    # Handle list-like values
    if isinstance(name, list):
        name = " / ".join(str(x) for x in name if pd.notna(x))
    elif pd.isna(name):
        name = None
    elif name is not None:
        name = str(name)

    if isinstance(ref, list):
        ref = " / ".join(str(x) for x in ref if pd.notna(x))
    elif pd.isna(ref):
        ref = None
    elif ref is not None:
        ref = str(ref)

    if isinstance(highway, list):
        highway = " / ".join(str(x) for x in highway if pd.notna(x))
    elif pd.isna(highway):
        highway = None
    elif highway is not None:
        highway = str(highway)

    # Build label
    label = "Unnamed road"
    if name and ref:
        if ref.lower() in name.lower() or name.lower() in ref.lower():
            label = name
        else:
            label = f"{name} (Route {ref})"
    elif name:
        label = name
    elif ref:
        label = ref
    elif highway:
        if "link" in highway.lower():
            label = f"Connector ({highway})"
        else:
            label = f"Unnamed road ({highway})"
    
    if is_bus:
        label = f"{label} [Transit/Bus Lane]"
        
    return label

def route_edges_to_raw_sequence(route_edges):
    sequence = []

    for _, row in route_edges.iterrows():
        label = edge_label(row)
        length = row["length"] if "length" in row and pd.notna(row["length"]) else None
        highway = row["highway"] if "highway" in row and pd.notna(row["highway"]) else None

        sequence.append({
            "label": label,
            "length": float(length) if length is not None else None,
            "highway": highway
        })

    return sequence

def clean_road_sequence(raw_sequence, min_length_for_unnamed=80):
    cleaned = []
    prev_label = None

    for item in raw_sequence:
        label = item["label"]
        length = item["length"]

        # Skip very short unnamed connectors
        if not label and length is not None and length < min_length_for_unnamed:
            continue

        # Give placeholder for unnamed but meaningful segments
        if not label:
            label = "[unnamed connector]"

        # Collapse consecutive duplicates
        if label == prev_label:
            continue

        cleaned.append(label)
        prev_label = label

    return cleaned

def make_grouped_road_sequence(route_edges, min_length_for_unnamed=80):
    grouped = []
    current_label = None
    current_length = 0.0
    current_highway = None

    for _, row in route_edges.iterrows():
        label = edge_label(row)
        length = row["length"] if "length" in row and pd.notna(row["length"]) else 0.0
        
        highway = row["highway"] if "highway" in row else None
        if isinstance(highway, list):
            highway = " / ".join(str(x) for x in highway if pd.notna(x))
        elif pd.isna(highway):
            highway = None
        elif highway is not None:
            highway = str(highway)

        if not label and length < min_length_for_unnamed:
            continue

        if not label:
            label = "Unnamed road"

        if label == current_label:
            current_length += float(length)
        else:
            if current_label is not None:
                grouped.append({
                    "road": current_label,
                    "length_m": round(current_length, 2),
                    "highway": current_highway
                })
            current_label = label
            current_length = float(length)
            current_highway = highway

    if current_label is not None:
        grouped.append({
            "road": current_label,
            "length_m": round(current_length, 2),
            "highway": current_highway
        })

    return grouped


def generate_k_alternative_paths(G, source, target, k=3, weight="length"):
    """
    Generate up to k shortest paths for an OSMnx MultiDiGraph.
    """
    try:
        return list(ox.routing.k_shortest_paths(G, source, target, k=k, weight=weight))
    except Exception:
        return []
    
def get_highest_class(highways):
    priority = {
        "motorway": 5,
        "trunk": 4,
        "primary": 3,
        "secondary": 2,
        "tertiary": 1
    }

    best = None
    best_score = -1

    for hw in highways:
        parts = str(hw).split(" / ")
        for p in parts:
            p = p.lower()
            if p in priority and priority[p] > best_score:
                best = p
                best_score = priority[p]

    return best if best else "unknown"

def build_candidate_from_grouped_sequence(
    grouped_sequence,
    total_length,
    road_a_name,
    road_b_name,
    candidate_id="candidate_1"
):
    via_roads = [seg["road"] for seg in grouped_sequence]
    segment_count = len(grouped_sequence)

    connector_count = sum(
        1 for seg in grouped_sequence
        if seg.get("highway") and "link" in str(seg["highway"]).lower()
    )

    road_classes = []
    for seg in grouped_sequence:
        hw = seg.get("highway")
        if hw is None:
            road_classes.append("unknown")
        else:
            road_classes.extend(str(hw).split(" / "))

    dominant_class = get_highest_class(road_classes)

    candidate = {
        "candidate_id": candidate_id,
        "from_road": road_a_name,
        "to_road": road_b_name,
        "via_roads": via_roads,
        "total_length_m": float(round(total_length, 2)),
        "segment_count": segment_count,
        "connector_count": connector_count,
        "road_classes": road_classes,
        "dominant_class": dominant_class
    }

    return candidate

def build_route_edges_list(G, paths, weight="length"):
    route_edges_list = []

    for i, path in enumerate(paths, start=1):
        if path is None or len(path) < 1:
            continue

        route_edges = safe_route_to_gdf(G, path, weight=weight)
        if route_edges.empty:
            continue
            
        route_edges_list.append({
            "candidate_id": f"candidate_{i}",
            "path": path,
            "route_edges": route_edges
        })

    return route_edges_list

import re
import pandas as pd




def _confidence_bucket(observation_count: int) -> str:
    if observation_count >= 5:
        return "high"
    if observation_count >= 2:
        return "medium"
    if observation_count >= 1:
        return "low"
    return "none"

def build_candidate_evidence(route_edges: pd.DataFrame) -> dict:
    def parse_lanes(val):
        if val is None:
            return None

        # 🔥 Handle list FIRST
        if isinstance(val, list):
            nums = []
            for v in val:
                if pd.notna(v) and str(v).isdigit():
                    nums.append(int(v))
            return max(nums) if nums else None

        # Now safe to use pd.isna
        if pd.isna(val):
            return None

        if isinstance(val, (int, float)):
            return int(val)

        if isinstance(val, str):
            parts = [p.strip() for p in val.split(";")]
            nums = [int(p) for p in parts if p.isdigit()]
            return max(nums) if nums else None

        return None

    def parse_maxspeed(val):
        if val is None:
            return None

        # Handle list FIRST
        if isinstance(val, list):
            nums = []
            for v in val:
                if pd.notna(v):
                    m = re.search(r"\d+", str(v))
                    if m:
                        nums.append(float(m.group()))
            return max(nums) if nums else None

        if pd.isna(val):
            return None

        if isinstance(val, (int, float)):
            return float(val)

        if isinstance(val, str):
            m = re.search(r"\d+", val)
            return float(m.group()) if m else None

        return None

    highway_sequence = []
    lane_values = []
    maxspeed_values = []
    ref_sequence = []
    contains_bridge = False
    contains_unnamed_segments = False
    contains_link = False
    has_transit_priority = False

    for _, row in route_edges.iterrows():
        # highway
        hw = row.get("highway")
        if isinstance(hw, list):
            hw_values = [str(x) for x in hw if pd.notna(x)]
        elif pd.notna(hw):
            hw_values = [str(hw)]
        else:
            hw_values = []

        highway_sequence.extend(hw_values)

        if any("link" in h.lower() for h in hw_values):
            contains_link = True

        # transit check
        for tag in ["bus", "psv", "busway", "lanes:bus"]:
            val = row.get(tag)
            if pd.notna(val) and val != "no":
                has_transit_priority = True
                break

        # lanes
        lanes = parse_lanes(row.get("lanes"))
        if lanes is not None:
            lane_values.append(lanes)

        # maxspeed
        speed = parse_maxspeed(row.get("maxspeed"))
        if speed is not None:
            maxspeed_values.append(speed)

        # ref
        ref = row.get("ref")
        if isinstance(ref, list):
            ref_values = [str(x) for x in ref if pd.notna(x)]
        elif pd.notna(ref):
            ref_values = [str(ref)]
        else:
            ref_values = []

        ref_sequence.extend(ref_values)

        # bridge
        bridge = row.get("bridge")
        if pd.notna(bridge):
            contains_bridge = True

        # unnamed
        name = row.get("name")
        if isinstance(name, list):
            if all(pd.isna(x) or str(x).strip() == "" for x in name):
                contains_unnamed_segments = True
        elif pd.isna(name) or str(name).strip() == "":
            contains_unnamed_segments = True

    unique_highways = list(dict.fromkeys(highway_sequence))
    unique_refs = list(dict.fromkeys(ref_sequence))

    evidence = {
        "edge_count": int(len(route_edges)),
        "highway_sequence": unique_highways,
        "lane_values": lane_values,
        "lane_observation_count": len(lane_values),
        "lane_data_confidence": _confidence_bucket(len(lane_values)),
        "avg_lanes": round(sum(lane_values) / len(lane_values), 2) if lane_values else None,
        "max_lanes": max(lane_values) if lane_values else None,
        "maxspeed_values": maxspeed_values,
        "speed_observation_count": len(maxspeed_values),
        "speed_data_confidence": _confidence_bucket(len(maxspeed_values)),
        "avg_maxspeed": round(sum(maxspeed_values) / len(maxspeed_values), 2) if maxspeed_values else None,
        "ref_sequence": unique_refs,
        "contains_bridge": contains_bridge,
        "contains_unnamed_segments": contains_unnamed_segments,
        "contains_link": contains_link,
        "has_transit_priority": has_transit_priority,
    }

    return evidence

def build_candidate_objects_from_routes(route_candidates, road_a_name, road_b_name):
    candidates = []

    for item in route_candidates:
        grouped_sequence = make_grouped_road_sequence(item["route_edges"])
        total_length = item["route_edges"]["length"].sum()

        candidate = build_candidate_from_grouped_sequence(
            grouped_sequence=grouped_sequence,
            total_length=total_length,
            road_a_name=road_a_name,
            road_b_name=road_b_name,
            candidate_id=item["candidate_id"]
        )

        candidate["path_nodes"] = item["path"]
        candidate["evidence"] = build_candidate_evidence(item["route_edges"])

        candidates.append(candidate)

    return candidates

NEIGHBOR_REGION_PRIORITY = {
    "kuala_lumpur": ["selangor"],
    "putrajaya": ["selangor"],
    "selangor": ["kuala_lumpur", "putrajaya"],
}


def nominatim_last_chance_with_region(
    edges_city: pd.DataFrame,
    queries: list[str],
    user_city: str,
    region: dict,
    buffer_m: float = 400,
) -> pd.DataFrame:
    """
    Last-chance fallback:
    - Try Nominatim with all cities in region
    - Use geometry to find edges ONLY inside edges_city
    """

    cities = region.get("cities", [])
    region_name = region["region_name"].replace("_", " ")

    search_queries = []

    for q in queries:
        search_queries.append(f"{q}, {user_city}, Malaysia")

    for city in cities:
        for q in queries:
            search_queries.append(f"{q}, {city}, Malaysia")

    for q in queries:
        search_queries.append(f"{q}, {region_name}, Malaysia")

    seen = set()
    geom = None

    for sq in search_queries:
        if sq in seen:
            continue
        seen.add(sq)

        try:
            gdf = ox.geocode_to_gdf(sq)
            if not gdf.empty:
                print(f"[Nominatim HIT] {sq}")
                geom = gdf.iloc[0].geometry
                break
        except Exception:
            continue

    if geom is None:
        raise ValueError(f"Nominatim failed for: {queries}")

    search_area = buffer_wgs84_geometry_in_meters(geom, buffer_m)
    idx = edges_city.sindex.query(search_area, predicate="intersects")
    matched = edges_city.iloc[idx].copy()

    if matched.empty:
        raise ValueError(
            f"Nominatim found location but no edges inside city graph: {queries}"
        )

    return matched

def are_same_road(road_a_edges: pd.DataFrame, road_b_edges: pd.DataFrame) -> bool:
    # exact edge overlap
    if not road_a_edges.index.intersection(road_b_edges.index).empty:
        return True

    # strong ref overlap
    a_refs = set(road_a_edges["ref_norm"].dropna()) if "ref_norm" in road_a_edges.columns else set()
    b_refs = set(road_b_edges["ref_norm"].dropna()) if "ref_norm" in road_b_edges.columns else set()
    if a_refs and b_refs and a_refs.intersection(b_refs):
        return True

    return False



GEOCODE_POINT_CACHE: dict[str, tuple[float, float]] = {}

def _geocode_query_point(query: str):
    if not query:
        return None
    
    q_norm = query.lower().strip()
    if q_norm in GEOCODE_POINT_CACHE:
        return GEOCODE_POINT_CACHE[q_norm]

    candidates = [query, f"{query}, Malaysia"]
    for q in candidates:
        try:
            gdf = ox.geocode_to_gdf(q)
            if gdf is not None and not gdf.empty:
                geom = gdf.iloc[0].geometry
                c = geom.centroid if hasattr(geom, "centroid") else geom
                pt = (float(c.y), float(c.x))
                GEOCODE_POINT_CACHE[q_norm] = pt
                return pt
        except Exception:
            continue
    return None


def _coerce_anchor_point(point_hint):
    if not isinstance(point_hint, dict):
        return None
    try:
        lat = float(point_hint.get("lat"))
        lng = float(point_hint.get("lng"))
    except Exception:
        return None
    return lat, lng


def _points_are_near(point_a, point_b, tolerance: float = 1e-6) -> bool:
    if not point_a or not point_b:
        return False
    ay, ax = point_a
    by, bx = point_b
    return abs(ax - bx) < tolerance and abs(ay - by) < tolerance


def _resolve_anchor_point(queries: list[str], user_city: str, point_hint=None):
    seen = set()
    attempts = []
    for q in queries or []:
        qn = normalize_text(q)
        if not qn or qn in seen:
            continue
        seen.add(qn)
        attempts.append(f"{q}, {user_city}, Malaysia")
        attempts.append(f"{q}, Malaysia")
        attempts.append(q)
    for q in attempts:
        pt = _geocode_query_point(q)
        if pt:
            return pt
    return _coerce_anchor_point(point_hint)


def _build_route_geometry_from_edges(route_edges: pd.DataFrame) -> list[dict]:
    route_geometry = []
    if not route_edges.empty:
        for _, row in route_edges.iterrows():
            geom = row.get("geometry")
            if geom is None:
                continue
            try:
                for lng, lat in geom.coords:
                    route_geometry.append({"lat": lat, "lng": lng, "height": 0})
            except Exception:
                continue
    seen = set()
    deduped_route = []
    for c in route_geometry:
        key = (round(c["lat"], 6), round(c["lng"], 6))
        if key not in seen:
            seen.add(key)
            deduped_route.append(c)
    return deduped_route


def _run_point_to_point_fallback(
    G_city,
    user_city: str,
    road_a_queries: list[str],
    road_b_queries: list[str],
    routing_mode: str,
    *,
    anchor_point_hints=None,
    city_query: str,
    region: dict,
    city_polygon,
    city_polygon_buffered,
    nodes_city,
    edges_city,
):
    anchor_point_hints = anchor_point_hints or {}
    a_hint = anchor_point_hints.get("a")
    b_hint = anchor_point_hints.get("b")
    a_pt = _resolve_anchor_point(road_a_queries, user_city, point_hint=a_hint)
    b_pt = _resolve_anchor_point(road_b_queries, user_city, point_hint=b_hint)
    if not a_pt or not b_pt:
        raise ValueError("Could not geocode distinct transit/walk anchors for point-to-point fallback.")

    hinted_pair = (_coerce_anchor_point(a_hint), _coerce_anchor_point(b_hint))
    if _points_are_near(a_pt, b_pt) and all(hinted_pair) and not _points_are_near(hinted_pair[0], hinted_pair[1]):
        a_pt, b_pt = hinted_pair
    elif _points_are_near(a_pt, b_pt):
        hinted_a, hinted_b = hinted_pair
        if hinted_a and not _points_are_near(hinted_a, b_pt):
            a_pt = hinted_a
        elif hinted_b and not _points_are_near(a_pt, hinted_b):
            b_pt = hinted_b

    ay, ax = a_pt
    by, bx = b_pt
    if _points_are_near(a_pt, b_pt):
        raise ValueError("The two anchor road sets resolve to the same road or corridor. Please choose a more specific hotspot with two distinct anchors.")

    try:
        source_node = ox.distance.nearest_nodes(G_city, X=ax, Y=ay)
        target_node = ox.distance.nearest_nodes(G_city, X=bx, Y=by)
    except Exception as exc:
        raise ValueError(f"Nearest-node lookup failed for point fallback: {exc}")

    if source_node == target_node:
        raise ValueError("The two anchor road sets resolve to the same graph node. Please choose a more specific hotspot with two distinct anchors.")

    weight_col = "length"
    if routing_mode == "transit":
        weight_col = "transit_weight"
    elif routing_mode == "walk":
        weight_col = "walk_weight"

    paths = generate_k_alternative_paths(G_city, source=source_node, target=target_node, k=3, weight=weight_col)
    if not paths:
        return _build_route_result(
            candidates=[],
            isochrone_geoms=[],
            route_geometry=[],
            region=region,
            city_query=city_query,
            city_polygon=city_polygon,
            city_polygon_buffered=city_polygon_buffered,
            G_city=G_city,
            nodes_city=nodes_city,
            edges_city=edges_city,
            road_a_edges=pd.DataFrame(),
            road_b_edges=pd.DataFrame(),
            road_a_geom=None,
            road_b_geom=None,
            mode="point_to_point_fallback",
            route_edges=pd.DataFrame(),
            route_length_m=0.0,
            anchor_points={
                "a": {"lat": ay, "lng": ax},
                "b": {"lat": by, "lng": bx},
            },
            route_valid=False,
            route_status="no_path",
            route_error="No alternative paths found for point-to-point fallback.",
        )

    route_candidates = build_route_edges_list(G_city, paths, weight=weight_col)
    if not route_candidates:
        return _build_route_result(
            candidates=[],
            isochrone_geoms=[],
            route_geometry=[],
            region=region,
            city_query=city_query,
            city_polygon=city_polygon,
            city_polygon_buffered=city_polygon_buffered,
            G_city=G_city,
            nodes_city=nodes_city,
            edges_city=edges_city,
            road_a_edges=pd.DataFrame(),
            road_b_edges=pd.DataFrame(),
            road_a_geom=None,
            road_b_geom=None,
            mode="point_to_point_fallback",
            route_edges=pd.DataFrame(),
            route_length_m=0.0,
            anchor_points={
                "a": {"lat": ay, "lng": ax},
                "b": {"lat": by, "lng": bx},
            },
            route_valid=False,
            route_status="collapsed",
            route_error="Point-to-point fallback produced no usable route edges.",
        )

    candidates = build_candidate_objects_from_routes(
        route_candidates,
        road_a_name=road_a_queries[0] if road_a_queries else "anchor_a",
        road_b_name=road_b_queries[0] if road_b_queries else "anchor_b",
    )
    route_edges = route_candidates[0]["route_edges"]
    route_geometry = _build_route_geometry_from_edges(route_edges)
    route_length_m = float(route_edges["length"].sum()) if "length" in route_edges.columns else 0.0
    if route_length_m <= 1.0 or not route_geometry:
        return _build_route_result(
            candidates=[],
            isochrone_geoms=[],
            route_geometry=[],
            region=region,
            city_query=city_query,
            city_polygon=city_polygon,
            city_polygon_buffered=city_polygon_buffered,
            G_city=G_city,
            nodes_city=nodes_city,
            edges_city=edges_city,
            road_a_edges=pd.DataFrame(),
            road_b_edges=pd.DataFrame(),
            road_a_geom=None,
            road_b_geom=None,
            mode="point_to_point_fallback",
            route_edges=route_edges,
            route_length_m=route_length_m,
            anchor_points={
                "a": {"lat": ay, "lng": ax},
                "b": {"lat": by, "lng": bx},
            },
            route_valid=False,
            route_status="collapsed",
            route_error=_collapsed_route_error(),
        )
    isochrone_geoms = []
    if routing_mode in ["walk", "transit"]:
        iso_src = generate_isochrone_polygon(G_city, source_node, distance_m=400, weight=weight_col)
        if iso_src:
            isochrone_geoms.append(iso_src)
        iso_tgt = generate_isochrone_polygon(G_city, target_node, distance_m=400, weight=weight_col)
        if iso_tgt:
            isochrone_geoms.append(iso_tgt)

    return _build_route_result(
        candidates=candidates,
        isochrone_geoms=isochrone_geoms,
        route_geometry=route_geometry,
        region=region,
        city_query=city_query,
        city_polygon=city_polygon,
        city_polygon_buffered=city_polygon_buffered,
        G_city=G_city,
        nodes_city=nodes_city,
        edges_city=edges_city,
        road_a_edges=pd.DataFrame(),
        road_b_edges=pd.DataFrame(),
        road_a_geom=None,
        road_b_geom=None,
        mode="point_to_point_fallback",
        route_edges=route_edges,
        route_length_m=route_length_m,
        anchor_points={
            "a": {"lat": ay, "lng": ax},
            "b": {"lat": by, "lng": bx},
        },
    )

# =========================================================
# MAIN PIPELINE
# =========================================================

GRAPH_CACHE = {}
CITY_GRAPH_CACHE = {}

def run_city_road_connection_analysis(
    user_city: str,
    road_a_queries: list[str],
    road_b_queries: list[str],
    regions_path: str = "regions.json",
    city_buffer_m: float = 2000,
    routing_mode: str = "drive",
    anchor_point_hints: dict | None = None,
):
    # 1. Find region and load regional graph
    region = find_region_for_city(user_city, regions_path=regions_path)
    
    # Use specific graph based on routing mode if available
    if routing_mode == "walk":
        graph_path = region.get("graphml_walk") or region.get("graphml")
    else:
        graph_path = region.get("graphml_drive") or region.get("graphml")
    
    if not graph_path:
        raise KeyError(f"No graph path (graphml, graphml_drive, or graphml_walk) found in regions.json for: {region.get('region_name')}")

    if graph_path in GRAPH_CACHE:
        G_region = GRAPH_CACHE[graph_path]
    else:
        print(f"Loading graph for region: {region['region_name']} from {graph_path}")
        G_region = ox.load_graphml(graph_path)
        GRAPH_CACHE[graph_path] = G_region
    
    print(f"Using graph for region: {region['region_name']}")

    # 2. Geocode city and clip regional graph to city
    if region["region_name"] in ["kuala_lumpur", "putrajaya", "labuan"]:
        city_query = f"{user_city}, Malaysia"
    else:
        city_query = f"{user_city}, {region['region_name'].replace('_', ' ')}, Malaysia"

    city_cache_key = (graph_path, city_query.lower(), int(city_buffer_m))
    if city_cache_key in CITY_GRAPH_CACHE:
        cached = CITY_GRAPH_CACHE[city_cache_key]
        city_polygon = cached["city_polygon"]
        city_polygon_buffered = cached["city_polygon_buffered"]
        G_city = cached["G_city"]
        nodes_city = cached["nodes_city"]
        edges_city = cached["edges_city"]
        print(f"Using cached city graph for: {city_query}")
    else:
        print(f"Geocoding city: {city_query}")
        city_gdf = ox.geocode_to_gdf(city_query)
        city_polygon = city_gdf.iloc[0].geometry
        city_polygon_buffered = buffer_wgs84_geometry_in_meters(city_polygon, city_buffer_m)

        print("Clipping graph to city polygon...")
        G_city, nodes_city, edges_city = clip_graph_to_polygon(G_region, city_polygon_buffered)
        CITY_GRAPH_CACHE[city_cache_key] = {
            "city_polygon": city_polygon,
            "city_polygon_buffered": city_polygon_buffered,
            "G_city": G_city,
            "nodes_city": nodes_city,
            "edges_city": edges_city,
        }
    print(f"City graph: {len(G_city.nodes)} nodes, {len(G_city.edges)} edges")

    # 3. Prepare and match roads
    edges_city = prepare_edge_text_columns(edges_city)

    # Station-access/pedestrian cases should not depend on brittle road-name matching.
    # Prefer geocoded anchor-to-anchor walking routes first; road-edge matching is only a fallback.
    if routing_mode == "walk":
        try:
            point_result = _run_point_to_point_fallback(
                G_city,
                user_city=user_city,
                road_a_queries=road_a_queries,
                road_b_queries=road_b_queries,
                routing_mode=routing_mode,
                anchor_point_hints=anchor_point_hints,
                city_query=city_query,
                region=region,
                city_polygon=city_polygon,
                city_polygon_buffered=city_polygon_buffered,
                nodes_city=nodes_city,
                edges_city=edges_city,
            )
            if point_result.get("route_valid", True):
                return point_result
            print(f"Walk point-to-point fallback failed first pass: {point_result.get('route_error')}. Trying road-edge matching...")
        except Exception as point_exc:
            print(f"Walk point-to-point fallback failed first pass: {point_exc}. Trying road-edge matching...")

    if query_sets_too_similar(road_a_queries, road_b_queries):
        if routing_mode == "walk":
            return _run_point_to_point_fallback(
                G_city,
                user_city=user_city,
                road_a_queries=road_a_queries,
                road_b_queries=road_b_queries,
                routing_mode=routing_mode,
                anchor_point_hints=anchor_point_hints,
                city_query=city_query,
                region=region,
                city_polygon=city_polygon,
                city_polygon_buffered=city_polygon_buffered,
                nodes_city=nodes_city,
                edges_city=edges_city,
            )
        return _build_route_result(
            candidates=[],
            isochrone_geoms=[],
            route_geometry=[],
            region=region,
            city_query=city_query,
            city_polygon=city_polygon,
            city_polygon_buffered=city_polygon_buffered,
            G_city=G_city,
            nodes_city=nodes_city,
            edges_city=edges_city,
            road_a_edges=pd.DataFrame(),
            road_b_edges=pd.DataFrame(),
            road_a_geom=None,
            road_b_geom=None,
            mode="same_anchor",
            route_edges=pd.DataFrame(),
            route_length_m=0.0,
            route_valid=False,
            route_status="same_anchor",
            route_error="The two anchor road sets resolve to the same road or corridor. Please choose a more specific hotspot with two distinct anchors.",
        )

    print(f"Matching road edges for A: {road_a_queries}")
    try:
        road_a_edges = match_road_edges(edges_city, road_a_queries)
    except Exception as e:
        print(f"Road A local match failed: {e}. Trying Nominatim fallback...")
        road_a_edges = nominatim_last_chance_with_region(edges_city, road_a_queries, user_city, region)

    print(f"Matching road edges for B: {road_b_queries}")
    try:
        road_b_edges = match_road_edges(edges_city, road_b_queries)
    except Exception as e:
        print(f"Road B local match failed: {e}. Trying Nominatim fallback...")
        road_b_edges = nominatim_last_chance_with_region(edges_city, road_b_queries, user_city, region)

    print(f"Matched road A edges: {len(road_a_edges)}")
    print(f"Matched road B edges: {len(road_b_edges)}")

    # 4. Merge road geometries (useful for optional corridor visualization)
    road_a_geom = unary_union(list(road_a_edges.geometry))
    road_b_geom = unary_union(list(road_b_edges.geometry))

    # 6. Graph-based road-to-road path search
    road_a_nodes = get_road_node_ids(road_a_edges)
    road_b_nodes = get_road_node_ids(road_b_edges)

    shared_nodes = road_a_nodes.intersection(road_b_nodes)

    best_pair, best_path, best_cost = find_best_connection_between_roads(
        G_city,
        road_a_nodes,
        road_b_nodes,
        weight="length",
        routing_mode=routing_mode
    )

    if best_path is None:
        return _build_route_result(
            candidates=[],
            isochrone_geoms=[],
            route_geometry=[],
            region=region,
            city_query=city_query,
            city_polygon=city_polygon,
            city_polygon_buffered=city_polygon_buffered,
            G_city=G_city,
            nodes_city=nodes_city,
            edges_city=edges_city,
            road_a_edges=road_a_edges,
            road_b_edges=road_b_edges,
            road_a_geom=road_a_geom,
            road_b_geom=road_b_geom,
            mode="road_edge_match",
            route_edges=pd.DataFrame(),
            route_length_m=0.0,
            route_valid=False,
            route_status="no_path",
            route_error=f"No path found between road A ({len(road_a_nodes)} nodes) and road B ({len(road_b_nodes)} nodes) in the city graph.",
        )

    same_road = are_same_road(road_a_edges, road_b_edges)

    num_candidate = 3
    if same_road:
        if routing_mode == "walk":
            return _run_point_to_point_fallback(
                G_city,
                user_city=user_city,
                road_a_queries=road_a_queries,
                road_b_queries=road_b_queries,
                routing_mode=routing_mode,
                anchor_point_hints=anchor_point_hints,
                city_query=city_query,
                region=region,
                city_polygon=city_polygon,
                city_polygon_buffered=city_polygon_buffered,
                nodes_city=nodes_city,
                edges_city=edges_city,
            )
        return _build_route_result(
            candidates=[],
            isochrone_geoms=[],
            route_geometry=[],
            region=region,
            city_query=city_query,
            city_polygon=city_polygon,
            city_polygon_buffered=city_polygon_buffered,
            G_city=G_city,
            nodes_city=nodes_city,
            edges_city=edges_city,
            road_a_edges=road_a_edges,
            road_b_edges=road_b_edges,
            road_a_geom=road_a_geom,
            road_b_geom=road_b_geom,
            mode="same_anchor",
            route_edges=pd.DataFrame(),
            route_length_m=0.0,
            route_valid=False,
            route_status="same_anchor",
            route_error="The two anchor road sets resolve to the same road or corridor. Please choose a more specific hotspot with two distinct anchors.",
        )
    else:
        shared_nodes = road_a_nodes.intersection(road_b_nodes)
        if shared_nodes:
            mode = "junction_or_direct_connection"
        elif best_cost < 50:
            mode = "junction_or_direct_connection"
        else:
            mode = "corridor"

    # print("Mode:", mode)
    # print("Best pair:", best_pair)
    # print("Best cost:", best_cost)
    # print("Best path:", best_path)

    # 8. Extract route edges from the graph-based path
    route_edges = safe_route_to_gdf(G_city, best_path, weight="length")
    route_length_m = float(route_edges["length"].sum()) if "length" in route_edges.columns else 0.0
    if len(best_path) < 2 or best_cost is None or best_cost <= 1.0 or route_length_m <= 1.0:
        return _build_route_result(
            candidates=[],
            isochrone_geoms=[],
            route_geometry=[],
            region=region,
            city_query=city_query,
            city_polygon=city_polygon,
            city_polygon_buffered=city_polygon_buffered,
            G_city=G_city,
            nodes_city=nodes_city,
            edges_city=edges_city,
            road_a_edges=road_a_edges,
            road_b_edges=road_b_edges,
            road_a_geom=road_a_geom,
            road_b_geom=road_b_geom,
            mode=mode,
            route_edges=route_edges,
            route_length_m=route_length_m,
            route_valid=False,
            route_status="collapsed",
            route_error=_collapsed_route_error(),
        )

    # #alternative paths
    source_node, target_node = best_pair    

    weight_col = "length"
    if routing_mode == "transit": weight_col = "transit_weight"
    if routing_mode == "walk": weight_col = "walk_weight"

    alternative_paths = generate_k_alternative_paths(
        G_city,
        source=source_node,
        target=target_node,
        k=num_candidate,
        weight=weight_col
    )

    if not alternative_paths:
        return _build_route_result(
            candidates=[],
            isochrone_geoms=[],
            route_geometry=[],
            region=region,
            city_query=city_query,
            city_polygon=city_polygon,
            city_polygon_buffered=city_polygon_buffered,
            G_city=G_city,
            nodes_city=nodes_city,
            edges_city=edges_city,
            road_a_edges=road_a_edges,
            road_b_edges=road_b_edges,
            road_a_geom=road_a_geom,
            road_b_geom=road_b_geom,
            mode=mode,
            route_edges=route_edges,
            route_length_m=route_length_m,
            route_valid=False,
            route_status="no_path",
            route_error="No alternative paths found.",
        )

    route_candidates = build_route_edges_list(G_city, alternative_paths)
    if not route_candidates:
        return _build_route_result(
            candidates=[],
            isochrone_geoms=[],
            route_geometry=[],
            region=region,
            city_query=city_query,
            city_polygon=city_polygon,
            city_polygon_buffered=city_polygon_buffered,
            G_city=G_city,
            nodes_city=nodes_city,
            edges_city=edges_city,
            road_a_edges=road_a_edges,
            road_b_edges=road_b_edges,
            road_a_geom=road_a_geom,
            road_b_geom=road_b_geom,
            mode=mode,
            route_edges=route_edges,
            route_length_m=route_length_m,
            route_valid=False,
            route_status="collapsed",
            route_error="Road-edge matching produced no usable route edges.",
        )

    candidates = build_candidate_objects_from_routes(
        route_candidates,
        road_a_name=road_a_queries[0],
        road_b_name=road_b_queries[0]
    )

    # Debug print for old return variables
    # print("Region:", region)
    # print("City query:", city_query)
    # print("Mode:", mode)
    # print("Best pair:", best_pair)
    # print("Best cost:", best_cost)
    # print("Best path:", best_path)
    # print("City graph nodes:", len(G_city.nodes))
    # print("City graph edges:", len(G_city.edges))
    # print("Road A edges:", len(road_a_edges))
    # print("Road B edges:", len(road_b_edges))

    # Generate isochrones (400m catchment) for walk/transit interventions
    isochrone_geoms = []
    if routing_mode in ["walk", "transit"]:
        iso_src = generate_isochrone_polygon(G_city, source_node, distance_m=400, weight=weight_col)
        if iso_src: isochrone_geoms.append(iso_src)
        iso_tgt = generate_isochrone_polygon(G_city, target_node, distance_m=400, weight=weight_col)
        if iso_tgt: isochrone_geoms.append(iso_tgt)

    # Extract exact polyline coordinates for the best route to render in Cesium
    deduped_route = _build_route_geometry_from_edges(route_edges)
    if not deduped_route:
        return _build_route_result(
            candidates=[],
            isochrone_geoms=[],
            route_geometry=[],
            region=region,
            city_query=city_query,
            city_polygon=city_polygon,
            city_polygon_buffered=city_polygon_buffered,
            G_city=G_city,
            nodes_city=nodes_city,
            edges_city=edges_city,
            road_a_edges=road_a_edges,
            road_b_edges=road_b_edges,
            road_a_geom=road_a_geom,
            road_b_geom=road_b_geom,
            mode=mode,
            route_edges=route_edges,
            route_length_m=route_length_m,
            route_valid=False,
            route_status="collapsed",
            route_error=_collapsed_route_error(),
        )

    return _build_route_result(
        candidates=candidates,
        isochrone_geoms=isochrone_geoms,
        route_geometry=deduped_route,
        region=region,
        city_query=city_query,
        city_polygon=city_polygon,
        city_polygon_buffered=city_polygon_buffered,
        G_city=G_city,
        nodes_city=nodes_city,
        edges_city=edges_city,
        road_a_edges=road_a_edges,
        road_b_edges=road_b_edges,
        road_a_geom=road_a_geom,
        road_b_geom=road_b_geom,
        mode=mode,
        route_edges=route_edges,
        route_length_m=route_length_m,
    )


# =========================================================
# EXAMPLE RUN
# =========================================================



if __name__ == "__main__":
    result = run_city_road_connection_analysis(
        user_city="kuala lumpur",
        road_a_queries=["Jalan 2/27e"],
        road_b_queries=["Jalan Rampai Niaga 1"],
        regions_path="regions.json",
        city_buffer_m=500,
    )

    for c in result["candidates"]:
        print("\n====================")
        print("Candidate ID:", c["candidate_id"])
        print("From:", c["from_road"])
        print("To:", c["to_road"])
        print("Via roads:", c["via_roads"])
        print("Total length:", c["total_length_m"])
        print("Segment count:", c["segment_count"])
        print("Connector count:", c["connector_count"])
        print("Road classes:", c["road_classes"])
        print("Dominant class:", c["dominant_class"])
        print("Evidence:", c["evidence"])
        
    print("mode result", result["mode"])

