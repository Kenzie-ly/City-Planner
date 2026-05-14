from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

try:
    import geopandas as gpd
    import osmnx as ox
    from shapely.geometry import MultiPolygon, box
except Exception:
    gpd = None
    ox = None
    MultiPolygon = None
    box = None

from ingestion_utils import (
    RunCounter, clean_nan, engine, finish_ingestion_run, log, slugify,
    start_ingestion_run, temp_stage, update_freshness
)


def require_osm_libs() -> None:
    if ox is None or gpd is None or MultiPolygon is None or box is None:
        raise RuntimeError("OSM ingestion requires osmnx, geopandas, shapely, and geoalchemy2.")


def geocode_polygon(place: str, fallback_dist_m: int = 3000):
    """Use Nominatim polygon. If no polygon exists, fallback to point buffer."""
    require_osm_libs()

    try:
        gdf = ox.geocode_to_gdf(place)
        geom = gdf.geometry.iloc[0]

        if geom.geom_type == "Polygon":
            return MultiPolygon([geom])

        if geom.geom_type == "MultiPolygon":
            return geom

        center = geom.centroid

    except Exception as exc:
        log.warning("Polygon geocode failed for %s: %s. Trying point fallback.", place, exc)
        lat, lon = ox.geocode(place)

        from shapely.geometry import Point
        center = Point(lon, lat)

    point_gdf = gpd.GeoDataFrame(geometry=[center], crs="EPSG:4326")
    buffered = point_gdf.to_crs(epsg=3857).buffer(fallback_dist_m).to_crs(epsg=4326).iloc[0]

    return MultiPolygon([buffered])


def approx_region_polygon(center_lat: float, center_lon: float, dist_m: int):
    """Fast approximate bbox polygon for region metadata when using graph_from_point."""
    require_osm_libs()
    lat_delta = dist_m / 111_320
    lon_delta = dist_m / (111_320 * max(0.2, abs(__import__("math").cos(__import__("math").radians(center_lat)))))
    geom = box(center_lon - lon_delta, center_lat - lat_delta, center_lon + lon_delta, center_lat + lat_delta)
    return MultiPolygon([geom])


def upsert_region(region_id: str, region_name: str, geom) -> str:
    wkt = geom.wkt
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO regions (region_id, region_name, country, geom, updated_at)
            VALUES (:region_id, :region_name, 'Malaysia', ST_Multi(ST_GeomFromText(:wkt, 4326)), NOW())
            ON CONFLICT (region_id)
            DO UPDATE SET region_name = EXCLUDED.region_name, geom = EXCLUDED.geom, updated_at = NOW();
        """), {"region_id": region_id, "region_name": region_name, "wkt": wkt})
    return region_id


def upsert_area(area_name: str, region_id: str, geom, area_type: str = "city") -> str:
    area_id = slugify(area_name.replace(", Malaysia", ""))
    wkt = geom.wkt
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO areas (area_id, region_id, area_name, area_type, source, geom, centroid, updated_at)
            VALUES (
                :area_id, :region_id, :area_name, :area_type, 'osm_nominatim',
                ST_Multi(ST_GeomFromText(:wkt, 4326)),
                ST_Centroid(ST_Multi(ST_GeomFromText(:wkt, 4326))),
                NOW()
            )
            ON CONFLICT (area_id)
            DO UPDATE SET region_id = EXCLUDED.region_id, area_name = EXCLUDED.area_name,
                          area_type = EXCLUDED.area_type, source = EXCLUDED.source,
                          geom = EXCLUDED.geom, centroid = EXCLUDED.centroid, updated_at = NOW();
        """), {"area_id": area_id, "region_id": region_id, "area_name": area_name, "area_type": area_type, "wkt": wkt})
    return area_id


def jsonable_tags(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items() if v is not None}


def ensure_osm_graph_indexes() -> None:
    """Non-concurrent because this usually runs during ingestion setup, not during production traffic."""
    with engine.begin() as conn:
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_osm_nodes_region ON osm_nodes(region_id);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_osm_edges_region ON osm_edges(region_id);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_osm_pois_area ON osm_pois(area_id);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_osm_transit_stops_area ON osm_transit_stops(area_id);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_osm_transit_stops_geom ON osm_transit_stops USING GIST(geom);"))

def get_osm_filter(network_type: str) -> str:
    DRIVE_FILTER = (
        '["highway"~"motorway|trunk|primary|secondary|tertiary|'
        'motorway_link|trunk_link|primary_link|secondary_link|tertiary_link"]'
    )

    WALK_FILTER = (
        '["highway"~"primary|secondary|tertiary|residential|'
        'pedestrian|footway|steps"]'
    )

    if network_type == "drive":
        return DRIVE_FILTER

    if network_type == "walk":
        return WALK_FILTER

    raise ValueError(f"Unsupported network_type: {network_type}")

def ingest_osm_graph(
    region_id: str,
    center_lat: float,
    center_lon: float,
    dist_m: int,
    graphml_path: str | None = None,
    refresh_graph: bool = False,
    network_type: str = "drive",
    download_only: bool = False,
) -> int:
    """Download/load the OSM graph once per region and network type."""
    require_osm_libs()
    ensure_osm_graph_indexes()

    with engine.begin() as conn:
        last_status = conn.execute(text("""
            SELECT status
            FROM ingestion_runs
            WHERE source_type = 'osm_graph'
              AND region_id = :region_id
            ORDER BY started_at DESC
            LIMIT 1
        """), {"region_id": region_id}).scalar()

        edge_count = conn.execute(text("""
            SELECT COUNT(*)
            FROM osm_edges
            WHERE region_id = :region_id
              AND network_type = :network_type
        """), {
            "region_id": region_id,
            "network_type": network_type,
        }).scalar()

    if not refresh_graph and last_status == "success" and edge_count and edge_count > 0:
        log.info(
            "OSM %s graph already successfully ingested for %s. Skipping.",
            network_type,
            region_id,
        )
        return 0

    if refresh_graph or last_status != "success":
        log.info(
            "Refreshing/inserting OSM %s graph for %s. Clearing old partial rows.",
            network_type,
            region_id,
        )

        with engine.begin() as conn:
            conn.execute(text("""
                DELETE FROM osm_edges
                WHERE region_id = :region_id
                  AND network_type = :network_type
            """), {
                "region_id": region_id,
                "network_type": network_type,
            })

            conn.execute(text("""
                DELETE FROM osm_nodes
                WHERE region_id = :region_id
                  AND network_type = :network_type
            """), {
                "region_id": region_id,
                "network_type": network_type,
            })

    G = None

    if graphml_path:
        path = Path(graphml_path)
        if path.exists():
            log.info(
                "Loading cached %s GraphML for %s from %s",
                network_type,
                region_id,
                path,
            )
            G = ox.load_graphml(path)

    if G is None:
        log.info(
            "Downloading OSM %s graph for region=%s from point dist=%sm",
            network_type,
            region_id,
            dist_m,
        )

        custom_filter = get_osm_filter(network_type)
        G = ox.graph_from_point(
            (center_lat, center_lon),
            custom_filter=custom_filter,
            network_type=network_type,
            dist=dist_m,
            simplify=True,
        )

        if graphml_path:
            path = Path(graphml_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            ox.save_graphml(G, path)
            log.info("Saved %s GraphML cache to %s", network_type, path)

    
    #to stop if it's download only
    if download_only:
        log.info("Download-only mode enabled. Skipping DB ingestion.")
        return 0

    nodes, edges = ox.graph_to_gdfs(G, nodes=True, edges=True)

    node_rows = []

    for osm_node_id, row in nodes.iterrows():
        geom = row.geometry

        node_rows.append({
            "osm_node_id": int(osm_node_id),
            "region_id": region_id,
            "network_type": network_type,
            "x": float(row.get("x")) if row.get("x") is not None else None,
            "y": float(row.get("y")) if row.get("y") is not None else None,
            "wkt": geom.wkt,
            "tags": json.dumps({
                k: str(v)
                for k, v in row.drop(labels=["geometry"], errors="ignore").to_dict().items()
                if v is not None
            }),
        })

    if node_rows:
        chunk_size = 10000

        with engine.begin() as conn:
            for i in range(0, len(node_rows), chunk_size):
                conn.execute(text("""
                    INSERT INTO osm_nodes (
                        osm_node_id, region_id, network_type, x, y, geom, tags
                    )
                    VALUES (
                        :osm_node_id, :region_id, :network_type, :x, :y,
                        ST_SetSRID(ST_GeomFromText(:wkt), 4326),
                        CAST(:tags AS jsonb)
                    )
                    ON CONFLICT (osm_node_id, network_type)
                    DO UPDATE SET
                        region_id = EXCLUDED.region_id,
                        x = EXCLUDED.x,
                        y = EXCLUDED.y,
                        geom = EXCLUDED.geom,
                        tags = EXCLUDED.tags;
                """), node_rows[i:i + chunk_size])

    edge_rows = []

    for (u, v, key), row in edges.iterrows():
        geom = row.geometry

        if geom is None:
            continue

        name = row.get("name")
        highway = row.get("highway")
        if network_type == "walk":
            allowed_walk_highways = {
                "footway",
                "path",
                "pedestrian",
                "steps",
                "living_street",
                "residential",
                "service",
            }

            highway_values = highway if isinstance(highway, list) else [highway]
            highway_values = [h for h in highway_values if h is not None]

            if not any(h in allowed_walk_highways for h in highway_values):
                continue

        osmid = row.get("osmid")

        #tags
        allowed_tags = {
            "highway",
            "name",
            "ref",
            "foot",
            "sidewalk",
            "crossing",
            "surface",
            "lit",
            "access",
            "oneway",
            "bridge",
            "tunnel",
        }

        edge_tags = {
            k: str(vv)
            for k, vv in row.drop(labels=["geometry"], errors="ignore").to_dict().items()
            if k in allowed_tags and vv is not None
        }

        edge_rows.append({
            "region_id": region_id,
            "network_type": network_type,
            "u": int(u),
            "v": int(v),
            "key": int(key),
            "osmid": json.dumps(osmid) if isinstance(osmid, list) else str(osmid),
            "highway": json.dumps(highway) if isinstance(highway, list) else str(highway) if highway is not None else None,
            "name": json.dumps(name) if isinstance(name, list) else str(name) if name is not None else None,
            "length_m": float(row.get("length", 0)) if row.get("length") is not None else None,
            "one_way": bool(row.get("oneway")) if row.get("oneway") is not None else None,
            "maxspeed": str(row.get("maxspeed")) if row.get("maxspeed") is not None else None,
            "wkt": geom.wkt,
            "tags": json.dumps({
                "street_count": str(row.get("street_count"))
            })
        })

    if edge_rows:
        chunk_size = 25000

        with engine.begin() as conn:
            for i in range(0, len(edge_rows), chunk_size):
                conn.execute(text("""
                    INSERT INTO osm_edges (
                        region_id, network_type, u, v, key, osmid, highway, name,
                        length_m, one_way, maxspeed, geometry, tags
                    )
                    VALUES (
                        :region_id, :network_type, :u, :v, :key, :osmid, :highway, :name,
                        :length_m, :one_way, :maxspeed,
                        ST_SetSRID(ST_GeomFromText(:wkt), 4326),
                        CAST(:tags AS jsonb)
                    )
                    ON CONFLICT (region_id, network_type, u, v, key)
                    DO UPDATE SET
                        osmid = EXCLUDED.osmid,
                        highway = EXCLUDED.highway,
                        name = EXCLUDED.name,
                        length_m = EXCLUDED.length_m,
                        one_way = EXCLUDED.one_way,
                        maxspeed = EXCLUDED.maxspeed,
                        geometry = EXCLUDED.geometry,
                        tags = EXCLUDED.tags;
                """), edge_rows[i:i + chunk_size])

    return len(node_rows) + len(edge_rows)


def classify_poi(tags: dict[str, Any]) -> str:
    if tags.get("shop") in {"mall", "supermarket"}:
        return str(tags["shop"])
    if tags.get("amenity") in {"university", "school", "hospital", "bus_station", "ferry_terminal"}:
        return str(tags["amenity"])
    if tags.get("office"):
        return "office"
    if tags.get("building") in {"apartments", "office", "commercial", "industrial", "retail"}:
        return str(tags["building"])
    if tags.get("public_transport"):
        return str(tags["public_transport"])
    if tags.get("railway") in {"station", "halt", "tram_stop"}:
        return "rail_station"
    if tags.get("highway") == "bus_stop":
        return "bus_stop"
    return "other"


def ingest_osm_pois_and_stops(area_id: str, region_id: str, polygon) -> int:
    require_osm_libs()
    tags = {
        "amenity": ["university", "school", "hospital", "bus_station", "ferry_terminal"],
        "building": ["apartments", "office", "commercial", "industrial", "retail"],
        "shop": ["mall", "supermarket"],
        "office": True,
        "public_transport": ["station", "stop_position", "platform"],
        "railway": ["station", "halt", "tram_stop"],
        "highway": ["bus_stop"],
    }
    log.info("Downloading OSM POIs/stops for %s", area_id)
    try:
        features = ox.features_from_polygon(polygon, tags)
    except Exception as exc:
        log.warning("No OSM features for %s: %s", area_id, exc)
        return 0
    if features.empty:
        return 0

    poi_rows = []
    stop_rows = []
    for idx, row in features.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        tags_dict = {k: clean_nan(v) for k, v in row.drop(labels=["geometry"], errors="ignore").to_dict().items() if clean_nan(v) is not None}
        osm_type = idx[0] if isinstance(idx, tuple) else "feature"
        osm_id = str(idx[1]) if isinstance(idx, tuple) and len(idx) > 1 else str(idx)
        name = tags_dict.get("name") or tags_dict.get("name:en") or "Unnamed"
        poi_category = classify_poi(tags_dict)
        poi_rows.append({
            "area_id": area_id,
            "region_id": region_id,
            "osm_id": f"{osm_type}/{osm_id}",
            "name": str(name),
            "poi_category": poi_category,
            "amenity": tags_dict.get("amenity"),
            "shop": tags_dict.get("shop"),
            "office": tags_dict.get("office"),
            "building": tags_dict.get("building"),
            "wkt": geom.wkt,
            "tags": json.dumps(jsonable_tags(tags_dict)),
        })
        if poi_category in {"bus_stop", "bus_station", "stop_position", "platform", "station", "rail_station"}:
            point_geom = geom.centroid if geom.geom_type != "Point" else geom
            stop_rows.append({
                "osm_stop_id": f"{osm_type}/{osm_id}",
                "area_id": area_id,
                "region_id": region_id,
                "stop_name": str(name),
                "stop_type": poi_category,
                "osm_id": f"{osm_type}/{osm_id}",
                "source_tag": "osm",
                "wkt": point_geom.wkt,
                "tags": json.dumps(jsonable_tags(tags_dict)),
            })

    if poi_rows:
        temp_stage(pd.DataFrame(poi_rows), "stage_osm_pois")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM osm_pois WHERE area_id = :area_id"), {"area_id": area_id})
            conn.execute(text("""
                INSERT INTO osm_pois (area_id, region_id, osm_id, name, poi_category, amenity, shop, office, building, geom, tags)
                SELECT area_id, region_id, osm_id, name, poi_category, amenity, shop, office, building,
                       ST_SetSRID(ST_GeomFromText(wkt),4326), CAST(tags AS jsonb)
                FROM stage_osm_pois;
                DROP TABLE stage_osm_pois;
            """))
    if stop_rows:
        temp_stage(pd.DataFrame(stop_rows), "stage_osm_transit_stops")
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO osm_transit_stops (osm_stop_id, area_id, region_id, stop_name, stop_type, osm_id, source_tag, geom, tags)
                SELECT osm_stop_id, area_id, region_id, stop_name, stop_type, osm_id, source_tag,
                       ST_SetSRID(ST_GeomFromText(wkt),4326), CAST(tags AS jsonb)
                FROM stage_osm_transit_stops
                ON CONFLICT (osm_stop_id)
                DO UPDATE SET area_id = EXCLUDED.area_id, region_id = EXCLUDED.region_id,
                              stop_name = EXCLUDED.stop_name, stop_type = EXCLUDED.stop_type,
                              osm_id = EXCLUDED.osm_id, source_tag = EXCLUDED.source_tag,
                              geom = EXCLUDED.geom, tags = EXCLUDED.tags;
                DROP TABLE stage_osm_transit_stops;
            """))
    return len(poi_rows) + len(stop_rows)


def ingest_region_graph_from_config(region_key: str, data: dict[str, Any], args) -> str:
    region_id = slugify(region_key)
    region_name = region_key.replace("_", " ").title()
    geom = approx_region_polygon(float(data["center_lat"]), float(data["center_lon"]), int(data["dist_m"]))
    upsert_region(region_id, region_name, geom)
    run_id = start_ingestion_run("osm_graph", region_name, region_id=region_id)
    counter = RunCounter()
    try:
        counter.inserted += ingest_osm_graph(
            region_id,
            float(data["center_lat"]),
            float(data["center_lon"]),
            int(data["dist_m"]),
            data.get(f"graphml_{args.network_type}"),
            refresh_graph=args.refresh_graph,
            network_type=args.network_type,
            download_only=args.download_only,
        )
        update_freshness("osm_graph", region_name, region_id=region_id, status="fresh", notes="OSM road graph loaded once per region", next_days=30)
        finish_ingestion_run(run_id, "success", counter)
        return region_id
    except Exception as exc:
        finish_ingestion_run(run_id, "failed", counter, str(exc))
        raise


def ingest_city_pois_only(city: str, region_id: str) -> str:
    place = city if city.lower().endswith("malaysia") else f"{city}, Malaysia"
    area_geom = geocode_polygon(place)
    area_id = upsert_area(city, region_id, area_geom)
    run_id = start_ingestion_run("osm_poi", place, region_id=region_id, area_id=area_id)
    counter = RunCounter()
    try:
        counter.inserted += ingest_osm_pois_and_stops(area_id, region_id, area_geom)
        update_freshness("osm_poi", place, region_id=region_id, area_id=area_id, status="fresh", notes="OSM POIs and transit stops loaded", next_days=30)
        finish_ingestion_run(run_id, "success", counter)
        return area_id
    except Exception as exc:
        finish_ingestion_run(run_id, "failed", counter, str(exc))
        raise


def ingest_osm_area(place: str, region_name: str | None = None, skip_graph: bool = False) -> tuple[str, str]:
    """Backward-compatible single-area mode."""
    area_geom = geocode_polygon(place)
    if region_name is None:
        region_name = place
    region_geom = geocode_polygon(region_name)
    region_id = slugify(region_name.replace(", Malaysia", ""))
    upsert_region(region_id, region_name, region_geom)
    area_id = upsert_area(place, region_id, area_geom)

    run_id = start_ingestion_run("osm", place, region_id=region_id, area_id=area_id)
    counter = RunCounter()
    try:
        if not skip_graph:
            # Safer default for direct mode: region polygon may be big and slow. Prefer config mode for production.
            counter.inserted += ingest_osm_graph(region_id, float(area_geom.centroid.y), float(area_geom.centroid.x), 15_000, None)
        counter.inserted += ingest_osm_pois_and_stops(area_id, region_id, area_geom)
        update_freshness("osm", place, region_id=region_id, area_id=area_id, status="fresh", notes="OSM loaded", next_days=30)
        finish_ingestion_run(run_id, "success", counter)
        return region_id, area_id
    except Exception as exc:
        finish_ingestion_run(run_id, "failed", counter, str(exc))
        raise

def main() -> None:
    parser = argparse.ArgumentParser(description="Production ingestion for OSM")
    parser.add_argument("--config", help="regions.json path. If provided, use region-based ingestion.")
    parser.add_argument("--region", help="Region key inside regions.json, e.g. kuala_lumpur. Use 'all' for every region.")
    parser.add_argument("--only-graph", action="store_true", help="Only load region graph when using --config.")
    parser.add_argument("--only-pois", action="store_true", help="Only load city POIs/stops when using --config.")
    parser.add_argument("--osm-area", help="Backward-compatible single OSM/Nominatim area name.")
    parser.add_argument("--osm-region", help="Backward-compatible single OSM/Nominatim region name.")
    parser.add_argument("--skip-graph", action="store_true", help="Single-area mode: skip road graph and only ingest POIs/stops.")
    parser.add_argument("--network-type", default="drive", choices=["drive", "walk"])
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--refresh-graph", action="store_true")
    args = parser.parse_args()

    if args.config:
        with open(args.config, "r", encoding="utf-8") as f:
            regions = json.load(f)
        selected = regions.keys() if args.region in (None, "all") else [args.region]
        for region_key in selected:
            if region_key not in regions:
                raise ValueError(f"Region key not found in config: {region_key}")
            data = regions[region_key]
            region_id = slugify(region_key)
            if not args.only_pois:
                region_id = ingest_region_graph_from_config(region_key, data, args)
            else:
                geom = approx_region_polygon(float(data["center_lat"]), float(data["center_lon"]), int(data["dist_m"]))
                upsert_region(region_id, region_key.replace("_", " ").title(), geom)
            if not args.only_graph:
                for city in data.get("cities", []):
                    ingest_city_pois_only(city, region_id)
        return

    if not args.osm_area:
        raise ValueError("Provide either --config or --osm-area")
    ingest_osm_area(args.osm_area, args.osm_region, skip_graph=args.skip_graph)


if __name__ == "__main__":
    main()
