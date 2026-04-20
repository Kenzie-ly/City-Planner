from __future__ import annotations

import json
import re
from dataclasses import dataclass

import networkx as nx
import osmnx as ox
import pandas as pd
from pyproj import CRS, Transformer
from shapely.geometry import LineString, MultiPolygon, Polygon
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

    # 🔥 remove road prefixes (CRITICAL)
    replacements = [
        "jalan", "jln", "lebuhraya",
        "jalan raya", "persiaran"
    ]

    for r in replacements:
        text = text.replace(r, "")

    # normalize symbols
    text = text.replace("–", "-").replace("—", "-")

    # remove non-alphanumeric
    text = re.sub(r'[^a-z0-9 ]', ' ', text)

    # collapse spaces
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


def token_match(q: str, text: str) -> bool:
    q_tokens = set(q.split())
    text_tokens = set(text.split())
    # Match if at least one token overlaps (e.g. "Tun Razak" matches "Jalan Tun Razak")
    return len(q_tokens & text_tokens) >= 1

def match_road_edges(edges: pd.DataFrame, queries: list[str]) -> pd.DataFrame:
    mask = pd.Series(False, index=edges.index)

    queries_norm = [normalize_text(q) for q in queries]

    for qn in queries_norm:
        if not qn:
            continue

        mask |= edges["search_text"].str.contains(re.escape(qn), na=False)
        mask |= edges["search_text"].apply(lambda x: token_match(qn, x))

    matched = edges[mask].copy()

    if matched.empty:
        raise ValueError(f"No roads matched for queries: {queries}. Search text sample: {edges['search_text'].head(5).tolist()}")

    return matched


def get_road_node_ids(road_edges: pd.DataFrame) -> set:
    u_nodes = set(road_edges.index.get_level_values(0))
    v_nodes = set(road_edges.index.get_level_values(1))
    return u_nodes.union(v_nodes)


# =========================================================
# GRAPH-BASED ROAD-TO-ROAD CONNECTION
# =========================================================

def find_best_connection_between_roads(G, road_a_nodes: set, road_b_nodes: set, weight: str = "length"):
    print(f"Finding best connection between {len(road_a_nodes)} and {len(road_b_nodes)} nodes...")
    try:
        # Use multi-source Dijkstra to find shortest paths from all nodes in A to all other nodes
        lengths, paths = nx.multi_source_dijkstra(G, road_a_nodes, weight=weight)
        
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
        
        print(f"Connection found! Length: {min_dist:.2f}m")
        return best_pair, best_path, min_dist
    except Exception as e:
        print(f"Error in shortest path: {e}")
        return None, None, None


# =========================================================
# OPTIONAL CORRIDOR GEOMETRY
# =========================================================

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
    if path is None or len(path) == 0:
        return pd.DataFrame(columns=["name", "ref", "length", "highway", "geometry"])

    if len(path) == 1:
        node = path[0]
        try:
            # For a single node, return all incident edges so we have something to show/analyze
            # We filter the edges GDF for this node as either source or target
            _, edges_gdf = ox.graph_to_gdfs(G, nodes=True, edges=True)
            mask = (edges_gdf.index.get_level_values(0) == node) | (edges_gdf.index.get_level_values(1) == node)
            matched_edges = edges_gdf[mask]
            if matched_edges.empty:
                return pd.DataFrame(columns=["name", "ref", "length", "highway", "geometry"])
            return matched_edges
        except Exception:
            return pd.DataFrame(columns=["name", "ref", "length", "highway", "geometry"])

    try:
        return ox.routing.route_to_gdf(G, path, weight=weight)
    except Exception:
        return pd.DataFrame(columns=["name", "ref", "length", "highway", "geometry"])


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
        "avg_lanes": round(sum(lane_values) / len(lane_values), 2) if lane_values else None,
        "max_lanes": max(lane_values) if lane_values else None,
        "maxspeed_values": maxspeed_values,
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

# =========================================================
# MAIN PIPELINE
# =========================================================

GRAPH_CACHE = {}

def run_city_road_connection_analysis(
    user_city: str,
    road_a_queries: list[str],
    road_b_queries: list[str],
    regions_path: str = "regions.json",
    city_buffer_m: float = 2000,
):
    # 1. Find region and load regional graph
    region = find_region_for_city(user_city, regions_path=regions_path)
    
    graph_path = region["graphml"]
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

    print(f"Geocoding city: {city_query}")
    city_gdf = ox.geocode_to_gdf(city_query)
    city_polygon = city_gdf.iloc[0].geometry
    city_polygon_buffered = buffer_wgs84_geometry_in_meters(city_polygon, city_buffer_m)

    print("Clipping graph to city polygon...")
    G_city, nodes_city, edges_city = clip_graph_to_polygon(G_region, city_polygon_buffered)
    print(f"City graph: {len(G_city.nodes)} nodes, {len(G_city.edges)} edges")

    # 3. Prepare and match roads
    edges_city = prepare_edge_text_columns(edges_city)


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
        weight="length"
    )

    if best_path is None:
        raise ValueError(f"No path found between road A ({len(road_a_nodes)} nodes) and road B ({len(road_b_nodes)} nodes) in the city graph.")

    same_road = are_same_road(road_a_edges, road_b_edges)

    num_candidate = 3
    if same_road:
        mode = "same_road"
        num_candidate = 1
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

    # #alternative paths
    source_node, target_node = best_pair    

    alternative_paths = generate_k_alternative_paths(
        G_city,
        source=source_node,
        target=target_node,
        k=num_candidate,
        weight="length"
    )

    if not alternative_paths:
        raise ValueError("No alternative paths found.")

    route_candidates = build_route_edges_list(G_city, alternative_paths)

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

    return {
        "candidates": candidates,
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
    }


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

