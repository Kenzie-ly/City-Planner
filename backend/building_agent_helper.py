import requests
import re
import time
import math


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
    """Fallback-heavy geocoder for Malaysian cities and roads."""
    if not location_string:
        return None

    # Hardcoded fallback for common cities to avoid rate limits
    city_map = {
        "kuala lumpur": {"lat": 3.1390, "lng": 101.6869},
        "kl": {"lat": 3.1390, "lng": 101.6869},
        "shah alam": {"lat": 3.0738, "lng": 101.5183},
        "petaling jaya": {"lat": 3.1073, "lng": 101.6067},
        "pj": {"lat": 3.1073, "lng": 101.6067},
        "penang": {"lat": 5.4141, "lng": 100.3288},
        "george town": {"lat": 5.4141, "lng": 100.3288},
        "johor bahru": {"lat": 1.4927, "lng": 103.7414},
        "jb": {"lat": 1.4927, "lng": 103.7414},
        "malacca": {"lat": 2.1896, "lng": 102.2501},
        "melaka": {"lat": 2.1896, "lng": 102.2501},
        "ipoh": {"lat": 4.5975, "lng": 101.0901},
        "kuching": {"lat": 1.5533, "lng": 110.3592},
        "kota kinabalu": {"lat": 5.9804, "lng": 116.0735},
        "kk": {"lat": 5.9804, "lng": 116.0735},
        "cyberjaya": {"lat": 2.9213, "lng": 101.6559},
        "putrajaya": {"lat": 2.9264, "lng": 101.6964},
        "subang jaya": {"lat": 3.0449, "lng": 101.5859},
        "klang": {"lat": 3.0449, "lng": 101.4456},
        "kajang": {"lat": 2.9896, "lng": 101.7884},
    }
    
    query_lower = location_string.lower().strip()
    for city, coords in city_map.items():
        if city in query_lower:
            return coords

    # If not in hardcoded list, try Nominatim
    clean_query = _clean_location_string(location_string)

    queries_to_try = []

    # If it looks like an intersection, try that first
    if '/' in location_string or ' and ' in location_string.lower():
        intersect = location_string.replace('/', ' and ')
        queries_to_try.append(f"{intersect}, Malaysia")

    queries_to_try.extend([
        f"{clean_query}, Malaysia",
        f"{location_string}, Malaysia",
        f"{clean_query}, Kuala Lumpur",
        f"{clean_query}, Selangor",
    ])

    # If we have two clear parts, try the first part alone
    if '/' in clean_query:
        parts = [p.strip() for p in clean_query.split('/') if p.strip()]
        if parts:
            queries_to_try.append(f"{parts[0]}, Malaysia")

    headers = {'User-Agent': 'CityPlannerSimulation/1.0 (kenzi@hackathon.local)'}

    for q in queries_to_try:
        try:
            params = {"q": q, "format": "json", "limit": 1}
            response = requests.get("https://nominatim.openstreetmap.org/search", params=params, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data:
                    print(f"  -> Found via Nominatim query: '{q}'")
                    return {"lat": float(data[0]["lat"]), "lng": float(data[0]["lon"])}
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


def generate_polygon_around_point(lat, lng, radius_km=0.05):
    lat_offset = radius_km / 111.0
    lng_offset = radius_km / (111.0 * math.cos(math.radians(lat)))
    return [
        {"lat": lat + lat_offset, "lng": lng - lng_offset, "height": 0},
        {"lat": lat + lat_offset, "lng": lng + lng_offset, "height": 0},
        {"lat": lat - lat_offset, "lng": lng + lng_offset, "height": 0},
        {"lat": lat - lat_offset, "lng": lng - lng_offset, "height": 0},
        {"lat": lat + lat_offset, "lng": lng - lng_offset, "height": 0},
    ]


def get_road_geometry(street_name, city_name=None):
    # If it's an interchange/intersection name like "A / B", try A first
    if '/' in street_name or ' and ' in street_name.lower():
        parts = [p.strip() for p in re.split(r'/| and ', street_name, flags=re.I) if p.strip()]
        if parts:
            # Try both components if one fails? Or just the first one. 
            # Usually the first one is the major highway.
            major_road = parts[0]
            print(f"  -> Interchange detected: '{street_name}'. Trying major road: '{major_road}'")
            res = get_road_geometry(major_road, city_name)
            if res: return res

    servers = [
        "https://overpass-api.de/api/interpreter",
        "https://lz4.overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ]
    import random
    random.shuffle(servers)
    # Get reference coordinates for proximity filtering
    # First try the street itself, fallback to city center
    ref_coords = get_malaysia_coords(street_name) or get_malaysia_coords(city_name if city_name else "Kuala Lumpur")
    
    # Calculate a tighter bounding box (approx 1km around the reference point)
    # This prevents parallel carriageways stretching across the city from creating massive loops
    if ref_coords:
        lat, lng = ref_coords["lat"], ref_coords["lng"]
        bbox = f"{lat-0.01},{lng-0.01},{lat+0.01},{lng+0.01}"
    else:
        bbox = "3.0000,101.5000,3.3000,101.9000" # Fallback

    overpass_query = f"""
    [out:json][timeout:25];
    (
      way["name"="{street_name}"]({bbox});
      node["name"="{street_name}"]["railway"="station"]({bbox});
      way["name"="{street_name}"]["railway"="station"]({bbox});
      relation["name"="{street_name}"]["route"="bus"]({bbox});
    );
    out geom qt;
    """

    headers = {
        'User-Agent': 'CityPlannerSimulation/1.0 (kenzi@hackathon.local)',
        'Referer': 'https://hackathon.local'
    }

    for server in servers:
        try:
            response = requests.post(server, data={"data": overpass_query}, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                segments = []
                if "elements" in data:
                    all_paths = []
                    for element in data["elements"]:
                        if "geometry" in element and len(element["geometry"]) >= 2:
                            seg = [{"lat": g["lat"], "lng": g["lon"], "height": 0} for g in element["geometry"]]
                            all_paths.append(seg)
                    
                    if not all_paths: return []

                    # STITCHING LOGIC (Fuzzy matching to bridge data gaps)
                    merged = []
                    while all_paths:
                        curr = all_paths.pop(0)
                        extended = True
                        while extended:
                            extended = False
                            for i, other in enumerate(all_paths):
                                # Fuzzy threshold: ~30 meters to bridge OSM data gaps
                                threshold = 0.0003 
                                # Check if other connects to end of curr
                                if (abs(other[0]["lat"] - curr[-1]["lat"]) < threshold and 
                                    abs(other[0]["lng"] - curr[-1]["lng"]) < threshold):
                                    curr.extend(other[1:])
                                    all_paths.pop(i)
                                    extended = True
                                    break
                                # Check if other connects to start of curr
                                elif (abs(other[-1]["lat"] - curr[0]["lat"]) < threshold and 
                                      abs(other[-1]["lng"] - curr[0]["lng"]) < threshold):
                                    curr = other[:-1] + curr
                                    all_paths.pop(i)
                                    extended = True
                                    break
                        if len(curr) > 3: # Filter out noise/tiny fragments
                            merged.append(curr)
                    
                    # CORRIDOR CONSOLIDATION: Return only the longest continuous path
                    # This prevents parallel ways (lanes/sidewalks) from creating a "messy" look.
                    if merged:
                        merged.sort(key=len, reverse=True)
                        return [merged[0]] # Return only the main corridor
                    return []
            else:
                print(f"Server {server} returned {response.status_code}. Trying next...")
        except requests.exceptions.RequestException:
            print(f"Timeout on {server}. Trying next...")
            time.sleep(2)

    print(f"All Overpass servers failed. Falling back to Nominatim for: {street_name}")
    pt = get_malaysia_coords(street_name)
    
    if not pt and city_name:
        print(f"  -> Nominatim failed for specific road. Using city center fallback: {city_name}")
        pt = get_malaysia_coords(city_name)

    if pt:
        anchor = {"lat": pt["lat"], "lng": pt["lng"], "height": 0}
        offset = {"lat": pt["lat"] + 0.001, "lng": pt["lng"] + 0.001, "height": 0}
        return [[anchor, offset]]
    
    return []


def process_agent_assets(planning_agent_output, city_name=None):
    enriched_assets = []

    # Use DOTALL so that [ ... ] can span multiple lines if the LLM adds them
    matches = re.findall(r"\[(.*?)\s*\]", planning_agent_output, re.DOTALL)

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
            res = get_malaysia_coords(location_desc)
            if not res:
                print(f"  -> {geom_type} geocoding failed. Skipping entity: {label}")
            asset_data["coordinates"] = res

        elif geom_type in ["POLYLINE", "POLYLINE_EXISTING", "SIMULATION"]:
            # Use the shared cleaner so the same fixes apply here too
            clean_street = _clean_location_string(location_desc)
            segments = get_road_geometry(clean_street, city_name=city_name)
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
                else:
                    print(f"  -> POLYLINE_NEW waypoint geocoding failed, skipping point: {clean_wp}")

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
                print(f"  -> POLYGON geocoding failed. Skipping entity: {label}")
                asset_data["coordinates"] = []

        enriched_assets.append(asset_data)

    # --- SPATIAL LINKING PASS ---
    # If a polygon and a polyline share a similar context, anchor the polygon to the polyline center
    for asset in enriched_assets:
        if asset["type"] == "POLYGON":
            for other in enriched_assets:
                if "POLYLINE" in other["type"]:
                    # Check if they share similar location description
                    if (other["location_description"].lower() in asset["location_description"].lower() or
                        asset["location_description"].lower() in other["location_description"].lower()):
                        
                        if other["coordinates"] and len(other["coordinates"]) > 0:
                            # Use the middle segment of the polyline as anchor
                            main_seg = other["coordinates"][0]
                            mid_idx = len(main_seg) // 2
                            anchor = main_seg[mid_idx]
                            
                            print(f"  -> Spatially linking Polygon '{asset['label']}' to Polyline '{other['label']}' at node {mid_idx}")
                            asset["coordinates"] = generate_polygon_around_point(anchor["lat"], anchor["lng"])
                            break

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
        
        elif key == "speed":
            style["speed"] = int(value) if value.isdigit() else 60

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

            target_height = style.get("height", 40) if entity_type == "polyline_new" else 0
            
            # MERGE SEGMENTS into one list of positions
            all_positions = []
            for seg in segments:
                if len(seg) >= 2:
                    all_positions.extend([
                        {"lat": c["lat"], "lng": c["lng"], "height": target_height}
                        for c in seg
                    ])
            
            if not all_positions:
                continue

            mid = all_positions[len(all_positions) // 2]
            seg_entity = {
                **base,
                "id":   f"entity_{i}",
                "name": asset["label"],
                "polyline_positions": all_positions,
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

        elif entity_type == "simulation":
            segments = []
            
            # GEOMETRY REDIRECT: Check if search_location matches any OTHER asset label
            # This allows simulating traffic on NEWLY proposed roads
            found_redirect = False
            for other_asset in enriched_assets:
                if other_asset.get("label", "").lower() == asset.get("search_location", "").lower():
                    if "coordinates" in other_asset and other_asset["coordinates"]:
                        segments = other_asset["coordinates"]
                        # Ensure segments is list of lists
                        if isinstance(segments[0], dict):
                            segments = [segments]
                        print(f"  -> Redirecting simulation '{asset['label']}' to use geometry of '{other_asset['label']}'")
                        found_redirect = True
                        break
            
            if not found_redirect:
                segments = coord_list
                
            if not segments:
                print(f"⚠️ Skipping simulation '{asset['label']}' — no coordinates")
                continue
            if isinstance(segments[0], dict):
                segments = [segments]

            # Simulation settings from style
            count_str = asset.get("count", "x10")
            try:
                # Use regex to find the first number in the count string
                count_match = re.search(r"(\d+)", count_str)
                vehicle_count = int(count_match.group(1)) if count_match else 10
            except:
                vehicle_count = 10
                
            speed_kmh = style.get("speed", 60) # km/h
            flow_type = style.get("flow", "normal") # congested, optimized, normal
            sim_height = style.get("height", 0)
            
            for j, seg in enumerate(segments):
                if len(seg) < 2:
                    continue
                
                # Create multiple vehicles for this segment
                for v in range(vehicle_count):
                    # LANE OFFSET LOGIC: 
                    # Shift the path laterally to simulate multiple lanes (3 lanes total)
                    lane = (v % 3) - 1 # -1, 0, 1
                    lane_offset_meters = lane * 3.5 # Standard lane width ~3.5m
                    
                    # Convert meters to approx degrees (crude but effective for local viz)
                    # 1 degree lat approx 111,000 meters. 1 degree lng approx 111,000 * cos(lat)
                    offset_lat_deg = 0
                    offset_lng_deg = 0
                    
                    # Calculate direction of the first segment to determine perpendicular
                    p1, p2 = seg[0], seg[1]
                    d_lat = p2["lat"] - p1["lat"]
                    d_lng = p2["lng"] - p1["lng"]
                    
                    # Perpendicular vector (rotate 90 deg)
                    perp_lat = -d_lng
                    perp_lng = d_lat
                    
                    # Normalize
                    mag = (perp_lat**2 + perp_lng**2)**0.5
                    if mag > 0:
                        # Approx 1m in degrees at 3deg latitude (Malaysia)
                        m_to_deg = 1.0 / 111000.0
                        cos_lat = math.cos(math.radians(p1["lat"]))
                        offset_lat_deg = (perp_lat / mag) * lane_offset_meters * m_to_deg
                        offset_lng_deg = (perp_lng / mag) * lane_offset_meters * m_to_deg / cos_lat

                    # Create offset path
                    offset_path = [
                        {"lat": p["lat"] + offset_lat_deg, "lng": p["lng"] + offset_lng_deg, "height": p.get("height", sim_height)}
                        for p in seg
                    ]

                    vehicle_entity = {
                        **base,
                        "id": f"sim_{i}_seg_{j}_v_{v}",
                        "entity_type": "simulation_vehicle",
                        "name": f"{asset['label']} Vehicle {v+1}",
                        "path": offset_path,
                        "speed": speed_kmh,
                        "flow": flow_type,
                        "startTimeOffset": v * 3 + (v % 2) * 1.5, # Staggered entry
                    }
                    entities.append(vehicle_entity)
            continue

        entities.append(base)

    return entities
