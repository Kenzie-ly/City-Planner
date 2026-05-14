import json
import re
import unicodedata
from pathlib import Path

import requests
from shapely.geometry import shape, Point
from sqlalchemy import text

from db.database import engine

REGIONS_PATH = Path(__file__).resolve().parent / "regions.json"


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def load_regions() -> dict:
    with open(REGIONS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def detect_region(user_input: str) -> dict:
    regions = load_regions()
    user_norm = user_input.strip().lower()

    for region_id, cfg in regions.items():
        cities_data = cfg.get("cities", [])
        
        # If cities is a dictionary (the new classified format)
        if isinstance(cities_data, dict):
            for city, areas in cities_data.items():
                # Check if user typed the city name
                if city.lower() == user_norm:
                    return {
                        "region_id": region_id,
                        "region_name": region_id.replace("_", " ").title(),
                        "graphml": cfg.get("graphml") or cfg.get("graphml_drive") or cfg.get("graphml_walk"),
                        "graphml_drive": cfg.get("graphml_drive"),
                        "graphml_walk": cfg.get("graphml_walk"),
                        "center_lat": cfg.get("center_lat"),
                        "center_lon": cfg.get("center_lon"),
                        "dist_m": cfg.get("dist_m"),
                    }
                # Check if user typed an area name inside that city
                for area in areas:
                    if area.lower() == user_norm:
                        return {
                            "region_id": region_id,
                            "region_name": region_id.replace("_", " ").title(),
                            "graphml": cfg.get("graphml") or cfg.get("graphml_drive") or cfg.get("graphml_walk"),
                            "graphml_drive": cfg.get("graphml_drive"),
                            "graphml_walk": cfg.get("graphml_walk"),
                            "center_lat": cfg.get("center_lat"),
                            "center_lon": cfg.get("center_lon"),
                            "dist_m": cfg.get("dist_m"),
                        }
        # If cities is still a list (old format)
        else:
            for city in cities_data:
                if city.lower() == user_norm:
                    return {
                        "region_id": region_id,
                        "region_name": region_id.replace("_", " ").title(),
                        "graphml": cfg.get("graphml") or cfg.get("graphml_drive") or cfg.get("graphml_walk"),
                        "graphml_drive": cfg.get("graphml_drive"),
                        "graphml_walk": cfg.get("graphml_walk"),
                        "center_lat": cfg.get("center_lat"),
                        "center_lon": cfg.get("center_lon"),
                        "dist_m": cfg.get("dist_m"),
                    }

    # fallback: try Kuala Lumpur first for your current project scope
    if "kuala" in user_norm or "kl" in user_norm:
        cfg = regions["kuala_lumpur"]
        return {
            "region_id": "kuala_lumpur",
            "region_name": "Kuala Lumpur",
            "graphml": cfg.get("graphml") or cfg.get("graphml_drive") or cfg.get("graphml_walk"),
            "graphml_drive": cfg.get("graphml_drive"),
            "graphml_walk": cfg.get("graphml_walk"),
            "center_lat": cfg.get("center_lat"),
            "center_lon": cfg.get("center_lon"),
            "dist_m": cfg.get("dist_m"),
        }

    raise ValueError(f"Could not detect region for user input: {user_input}")


def get_existing_area(area_id: str):
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT 
                    area_id,
                    region_id,
                    area_name,
                    area_type,
                    geom IS NOT NULL AS has_geometry
                FROM areas
                WHERE area_id = :area_id
            """),
            {"area_id": area_id},
        ).mappings().first()

    return dict(row) if row else None


def geocode_area(user_input: str):
    place_query = f"{user_input}, Malaysia"
    headers = {"User-Agent": "CityPlannerApp/1.0"}
    params = {
        "q": place_query,
        "format": "json",
        "limit": 1,
        "polygon_geojson": 1,
    }
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params=params,
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json()
        if not results:
            raise ValueError(f"Nominatim returned no results for: {place_query}")

        result = results[0]
        geojson = result.get("geojson")
        if geojson and geojson.get("type") not in (None, "Point"):
            geom = shape(geojson)
        else:
            lat = float(result["lat"])
            lon = float(result["lon"])
            geom = Point(lon, lat).buffer(0.02)

        centroid = geom.centroid
        return geom.wkt, centroid.wkt

    except Exception as e:
        raise ValueError(f"Could not geocode area '{place_query}': {e}")


def upsert_region(region: dict):
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO regions (
                    region_id,
                    region_name,
                    country,
                    graphml_path
                )
                VALUES (
                    :region_id,
                    :region_name,
                    'Malaysia',
                    :graphml_path
                )
                ON CONFLICT (region_id)
                DO UPDATE SET
                    region_name = EXCLUDED.region_name,
                    graphml_path = EXCLUDED.graphml_path,
                    updated_at = NOW();
            """),
            {
                "region_id": region["region_id"],
                "region_name": region["region_name"],
                "graphml_path": region.get("graphml"),
            },
        )


def upsert_area(area_id: str, user_input: str, region: dict, geom_wkt: str, centroid_wkt: str):
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO areas (
                    area_id,
                    region_id,
                    area_name,
                    area_type,
                    source,
                    geom,
                    centroid
                )
                VALUES (
                    :area_id,
                    :region_id,
                    :area_name,
                    :area_type,
                    'osmnx_geocode',
                    ST_Multi(ST_GeomFromText(:geom_wkt, 4326)),
                    ST_GeomFromText(:centroid_wkt, 4326)
                )
                ON CONFLICT (area_id)
                DO UPDATE SET
                    region_id = EXCLUDED.region_id,
                    area_name = EXCLUDED.area_name,
                    source = EXCLUDED.source,
                    geom = EXCLUDED.geom,
                    centroid = EXCLUDED.centroid,
                    updated_at = NOW();
            """),
            {
                "area_id": area_id,
                "region_id": region["region_id"],
                "area_name": user_input.strip(),
                "area_type": "dynamic_area",
                "geom_wkt": geom_wkt,
                "centroid_wkt": centroid_wkt,
            },
        )


def resolve_area(user_input: str) -> dict:
    area_id = slugify(user_input)
    existing = get_existing_area(area_id)

    if existing and existing["has_geometry"]:
        return {
            "area_id": existing["area_id"],
            "area_name": existing["area_name"],
            "region_id": existing["region_id"],
            "has_geometry": True,
            "source": "database_cache",
        }

    region = detect_region(user_input)
    upsert_region(region)

    geom_wkt, centroid_wkt = geocode_area(user_input)
    upsert_area(area_id, user_input, region, geom_wkt, centroid_wkt)

    return {
        "area_id": area_id,
        "area_name": user_input.strip(),
        "region_id": region["region_id"],
        "graphml": region.get("graphml") or region.get("graphml_drive") or region.get("graphml_walk"),
        "graphml_drive": region.get("graphml_drive"),
        "graphml_walk": region.get("graphml_walk"),
        "has_geometry": True,
        "source": "newly_resolved",
    }