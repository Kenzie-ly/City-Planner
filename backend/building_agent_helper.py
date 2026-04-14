import requests
import re
import time
import math
import json
from geopy.geocoders import Nominatim
from flask import Flask, jsonify, request
import json

app = Flask(__name__)
model = None


geolocator = Nominatim(user_agent="my_hackathon_kl_mapper_v2")


# ─────────────────────────────────────────────────────────────
# BUG FIX: Centralised location cleaner used by BOTH Nominatim
#           and the Overpass street-name extractor.
#
# Original bugs:
#  1. re.sub(r'(corner|...)') matched INSIDE 'corners', turning
#     "four corners" → "four s" — garbage Nominatim query.
#     FIX: use \b word-boundaries and match corners? so both
#          "corner" and "corners" are caught cleanly.
#  2. "Located at the base of the Menara IMC perimeter" was never
#     cleaned because "Located" wasn't in the prefix list.
#     FIX: expanded prefix pattern covers "located at/near/in".
#  3. Dangling filler words ("of", "the", "four", "base of") left
#     after stripping prefixes polluted every Nominatim query.
#     FIX: second-pass removal of those residual stop-words.
# ─────────────────────────────────────────────────────────────
def _clean_location_string(location_string):
    """Return the best short place-name to feed into a geocoder."""

    # 1. Standardise transit-station prefixes
    clean = location_string.replace("LRT", "Stesen LRT").replace("MRT", "Stesen MRT")

    # 2. Chop at the first directional / connector word
    clean = re.split(
        r"(?i)\s+(northbound|southbound|eastbound|westbound|approaches|and|or|from|to|between)",
        clean
    )[0].strip()

    # 3. Strip leading filler phrases (expanded to cover "Located at …" etc.)
    clean = re.sub(
        r"(?i)^(located\s+at\s+the|located\s+at|located\s+near|located\s+in|"
        r"along|at\s+the|at|near|in|the)\s+",
        "", clean
    ).strip()

    # 4. Remove noise words using WORD BOUNDARIES so "corners" is not
    #    mangled into "s".  Also removes "base of the", "of the", etc.
    clean = re.sub(
        r"(?i)\b(intersection|corners?|junction|approaches?|perimeter|"
        r"base\s+of\s+the|base\s+of|base|of\s+the|four|of)\b",
        "", clean
    ).strip()

    # 5. Remove any stray "the" articles that survived earlier passes
    clean = re.sub(r"(?i)\bthe\b", "", clean).strip()

    # 6. Collapse multiple spaces introduced by the removals above
    clean = re.sub(r"\s+", " ", clean).strip()

    return clean


def get_malaysia_coords(location_string):
    clean_query = _clean_location_string(location_string)

    queries_to_try = [
        f"{clean_query}, Malaysia",
        f"{location_string}, Malaysia",
        f"{clean_query}, Kuala Lumpur",
        f"{clean_query}, Selangor",
    ]

    for q in queries_to_try:
        try:
            time.sleep(1)  # Respect Nominatim's rate limit
            location = geolocator.geocode(q, timeout=10)
            if location:
                print(f"  -> Found via Nominatim query: '{q}'")
                return {"lat": location.latitude, "lng": location.longitude}
        except Exception:
            pass

    print(f"  -> Nominatim failed to find: {location_string} (Tried: '{clean_query}')")
    return None


def deduplicate_coords(coords):
    """Removes duplicate coordinate points to keep lines clean."""
    seen = set()
    result = []
    for c in coords:
        key = (round(c["lat"], 6), round(c["lng"], 6))
        if key not in seen:
            seen.add(key)
            result.append(c)
    return result


def generate_polygon_around_point(lat, lng, radius_km=0.5):
    lat_offset = radius_km / 111.0
    lng_offset = radius_km / (111.0 * math.cos(math.radians(lat)))
    return [
        {"lat": lat + lat_offset, "lng": lng - lng_offset, "height": 0},
        {"lat": lat + lat_offset, "lng": lng + lng_offset, "height": 0},
        {"lat": lat - lat_offset, "lng": lng + lng_offset, "height": 0},
        {"lat": lat - lat_offset, "lng": lng - lng_offset, "height": 0},
        {"lat": lat + lat_offset, "lng": lng - lng_offset, "height": 0},
    ]


def get_road_geometry(street_name):
    servers = [
        "https://overpass-api.de/api/interpreter",
        "https://lz4.overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ]
    klang_valley_bbox = "3.0000,101.5000,3.3000,101.9000"

    overpass_query = f"""
    [out:json][timeout:25];
    (
      way["name"="{street_name}"]["highway"]({klang_valley_bbox});
    );
    out geom qt;
    """

    for server in servers:
        try:
            response = requests.post(server, data={"data": overpass_query}, timeout=30)
            if response.status_code == 200:
                data = response.json()
                segments = []
                if "elements" in data:
                    for element in data["elements"]:
                        if "geometry" in element and len(element["geometry"]) >= 2:
                            seg = [
                                {"lat": node["lat"], "lng": node["lon"], "height": 0}
                                for node in element["geometry"]
                            ]
                            segments.append(seg)
                if segments:
                    return segments
            else:
                print(f"Server {server} returned {response.status_code}. Trying next...")
        except requests.exceptions.RequestException:
            print(f"Timeout on {server}. Trying next...")
            time.sleep(2)

    print(f"All Overpass servers failed. Falling back to Nominatim for: {street_name}")
    pt = get_malaysia_coords(street_name)
    if pt:
        # ─────────────────────────────────────────────────────────
        # BUG FIX: The fallback segment used to be:
        #   [[pt, {"lat": pt["lat"]+0.001, "lng": pt["lng"]+0.001, "height": 0}]]
        # pt comes from get_malaysia_coords which returns {lat, lng} with NO
        # "height" key.  Downstream code that iterated this segment and
        # accessed node["height"] would raise a KeyError.
        # FIX: Explicitly add "height": 0 to the anchor point as well.
        # ─────────────────────────────────────────────────────────
        anchor = {"lat": pt["lat"], "lng": pt["lng"], "height": 0}
        offset = {"lat": pt["lat"] + 0.001, "lng": pt["lng"] + 0.001, "height": 0}
        return [[anchor, offset]]
    return []


def process_agent_assets(planning_agent_output):
    enriched_assets = []

    matches = re.findall(r"\[(.*?)\]", planning_agent_output)

    for match in matches:
        parts = [p.strip() for p in match.split("|")]

        if len(parts) < 5:
            print(f"⚠️ Skipping malformed asset string: [{match}]")
            continue

        geom_type     = parts[0].upper()
        count         = parts[1]
        label         = parts[2]
        location_desc = parts[3]
        style         = parts[4]
        blurb         = parts[5] if len(parts) > 5 else ""

        asset_data = {
            "type": geom_type,
            "count": count,
            "label": label,
            "location_description": location_desc,
            "style": style,
            "blurb": blurb,
            "coordinates": None,
        }

        print(f"\nFetching spatial data for: {geom_type} - {label}")

        if geom_type in ["POINT", "BOX"]:
            asset_data["coordinates"] = get_malaysia_coords(location_desc)

        elif geom_type in ["POLYLINE", "POLYLINE_EXISTING"]:
            # Use the shared cleaner so the same fixes apply here too
            clean_street = _clean_location_string(location_desc)
            segments = get_road_geometry(clean_street)
            if not segments:
                print(f"  -> All spatial routing failed for: {clean_street}")
            asset_data["coordinates"] = segments

        elif geom_type == "POLYLINE_NEW":
            waypoints  = location_desc.split(",")
            path_coords = []

            for wp in waypoints:
                clean_wp = re.sub(r"(?i)^(start|waypoint|end):\s*", "", wp.strip()).strip()
                coord = get_malaysia_coords(clean_wp)
                if coord:
                    path_coords.append(coord)

            if len(path_coords) >= 2:
                asset_data["coordinates"] = [path_coords]
            else:
                print(f"  -> Failed to geocode enough points for POLYLINE_NEW: {location_desc}")
                asset_data["coordinates"] = []

        elif geom_type == "POLYGON":
            clean_zone = _clean_location_string(location_desc)
            center = get_malaysia_coords(clean_zone)
            if center:
                asset_data["coordinates"] = generate_polygon_around_point(
                    center["lat"], center["lng"]
                )
            else:
                asset_data["coordinates"] = []

        enriched_assets.append(asset_data)

    return enriched_assets


def parse_style(style_str):
    style = {
        "color":     "#FFD700",
        "width":     6,
        "pixelSize": 18,
        "opacity":   0.4,
    }

    if " | " in style_str:
        style_str = style_str.split(" | ")[0]

    for part in style_str.split(","):
        part = part.strip()
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        key   = key.strip().lower()
        value = value.strip().lower()

        if key == "color":
            color_map = {
                "orange": "#FFA500", "blue":   "#2563EB", "green":  "#16A34A",
                "red":    "#DC2626", "yellow": "#EAB308", "white":  "#FFFFFF",
                "black":  "#000000", "grey":   "#6B7280", "gray":   "#6B7280",
                "purple": "#9333EA",
            }
            style["color"] = color_map.get(value, value)

        elif key == "width":
            width_map = {"small": 4, "medium": 8, "wide": 12, "large": 14}
            style["width"] = width_map.get(value, int(value) if value.isdigit() else 8)

        elif key == "size":
            size_map = {"small": 10, "medium": 16, "large": 22}
            style["pixelSize"] = size_map.get(value, int(value) if value.isdigit() else 16)

        elif key == "opacity":
            opacity_map = {"low": 0.2, "medium": 0.5, "high": 0.8}
            style["opacity"] = opacity_map.get(
                value, float(value) if value.replace(".", "").isdigit() else 0.4
            )

        elif key == "height":
            # ─────────────────────────────────────────────────────
            # BUG FIX: "small" was not in the height_map, so the
            # fallback int(value) would raise a ValueError because
            # "small".isdigit() is False — silently dropping the
            # height entirely and leaving the building flat.
            # FIX: Added "small" → 20 to the map (mirrors the
            # size convention: small/medium/large = 20/40/80).
            # ─────────────────────────────────────────────────────
            height_map = {"small": 20, "medium": 40, "large": 80, "high": 80, "low": 20}
            style["height"] = height_map.get(
                value, int(value) if value.isdigit() else 40
            )

    return style


def format_entities(enriched_assets):
    entities = []

    for i, asset in enumerate(enriched_assets):
        entity_type = asset["type"].lower()
        style       = parse_style(asset["style"])
        blurb       = asset.get("blurb", "")

        base = {
            "id":          f"entity_{i}",
            "name":        asset["label"],
            "entity_type": entity_type,
            "city":        asset.get("city", ""),
            "blurb":       blurb,
            "style":       style,
        }

        coords = asset.get("coordinates")
        if coords is None:
            coord_list = []
        elif isinstance(coords, dict):
            coord_list = [coords]
        else:
            coord_list = coords

        if entity_type == "point":
            if coord_list:
                c = coord_list[0]
                base["position"] = {
                    "lat":    c["lat"],
                    "lng":    c["lng"],
                    "height": c.get("height", 0),
                }
            else:
                print(f"⚠️ Skipping point '{asset['label']}' — no coordinates")
                continue

        elif entity_type in ["polyline", "polyline_existing", "polyline_new"]:
            segments = coord_list
            if not segments:
                print(f"⚠️ Skipping {entity_type} '{asset['label']}' — no coordinates")
                continue
            if isinstance(segments[0], dict):
                segments = [segments]

            target_height = style.get("height", 0) if entity_type == "polyline_new" else 0

            for j, seg in enumerate(segments):
                if len(seg) < 2:
                    continue
                mid = seg[len(seg) // 2]
                seg_entity = {
                    **base,
                    "id":   f"entity_{i}_seg_{j}",
                    "name": asset["label"] if j == 0 else "",
                    "polyline_positions": [
                        {"lat": c["lat"], "lng": c["lng"], "height": target_height}
                        for c in seg
                    ],
                    "position": {"lat": mid["lat"], "lng": mid["lng"], "height": 80},
                }
                entities.append(seg_entity)
            continue

        elif entity_type == "polygon":
            if coord_list:
                base["polygon_positions"] = [
                    {"lat": c["lat"], "lng": c["lng"], "height": c.get("height", 0)}
                    for c in coord_list
                ]
                avg_lat = sum(c["lat"] for c in coord_list) / len(coord_list)
                avg_lng = sum(c["lng"] for c in coord_list) / len(coord_list)
                base["position"] = {"lat": avg_lat, "lng": avg_lng, "height": 0}
            else:
                print(f"⚠️ Skipping polygon '{asset['label']}' — no coordinates")
                continue

        elif entity_type == "box":
            if coord_list:
                c = coord_list[0]
                base["position"] = {
                    "lat":    c["lat"],
                    "lng":    c["lng"],
                    "height": c.get("height", 0),
                }
            else:
                print(f"⚠️ Skipping box '{asset['label']}' — coordinates is None")
                continue
            base["building"] = {
                "length": asset.get("building", {}).get("length", 50),
                "width":  asset.get("building", {}).get("width",  50),
                "height": asset.get("building", {}).get("height", 40),
            }

        entities.append(base)

    return entities


@app.route("/buildingAgentHelper", methods=["POST"])
def buildingAgentHelper():
    input_text = """
        [POLYLINE_NEW | x1 | Elevated Micromobility Spine | Start: Ampang Park MRT, Waypoint: Jalan Ampang, End: Jalan Tun Razak | color:green, height:high | This is a 2.5km modular steel bridge built 6 meters above the street level. It provides a dedicated, signal-free path for bicycles and scooters to bypass street-level congestion.]
        [POINT | x1 | Mobility Pod North | Ampang Park MRT | color:blue, size:large | This is a 50-square-meter modular structure for vehicle docking and charging. It serves as the primary gateway for commuters transitioning from rail to the micromobility spine.]
        [POINT | x1 | Mobility Pod South | Jalan Tun Razak | color:blue, size:large | This is a 50-square-meter modular structure for vehicle docking and charging. It serves as the primary gateway for commuters transitioning from rail to the micromobility spine.]
        [BOX | x1 | Corridor Management Station | Jalan Ampang | color:black, height:medium | This is a two-story facility housing the technical support and security team for the elevated path. It ensures the physical integrity and safety of the multi-modal corridor.]
        [POLYLINE_EXISTING | x1 | Green Buffer Zone | Jalan Tun Razak | color:cyan, width:medium | This is a 1.5km stretch of road where the curb has been extended with planters and bollards. It protects pedestrians and provides a landing area for users descending from the elevated spine.]
        [POINT | x1 | Information Kiosk | Intermark Mall | color:yellow, size:small | This is a digital touchscreen pillar providing real-time transit and corridor data. It helps users navigate the multi-modal connections at the heart of the district.]   
    """

    enriched_assets = process_agent_assets(input_text)
    entities        = format_entities(enriched_assets)

    if not entities:
        return jsonify({
            "status": "error",
            "message": entities 
        }), 500
    else:
        return jsonify({
                "status": "ok",
                "message": entities
        })
