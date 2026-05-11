import logging
from sqlalchemy import text
from backend.db.database import engine

logger = logging.getLogger(__name__)

def get_area_profile(area_id: str) -> dict | None:
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT area_id, area_name, region, country, geometry FROM areas WHERE area_id = :area_id LIMIT 1;"),
                {"area_id": area_id},
            ).mappings().first()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Fetcher Error: area_profile for {area_id}: {e}")
        return None

def get_route_frequency_summary(area_id: str) -> list[dict]:
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT route_id, route_short_name, avg_headway_minutes, 
                           service_span_hours, frequency_tier, evidence_score
                    FROM gtfs_route_frequency_summary
                    WHERE area_id = :area_id
                    ORDER BY evidence_score DESC;
                """),
                {"area_id": area_id},
            ).mappings().all()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Fetcher Error: route_frequency for {area_id}: {e}")
        return []

def get_transit_coverage_summary(area_id: str) -> dict | None:
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT area_id, stop_count, route_count, avg_distance_to_stop_meters,
                           coverage_score, underserved_zone_ratio
                    FROM transit_coverage_summary
                    WHERE area_id = :area_id LIMIT 1;
                """),
                {"area_id": area_id},
            ).mappings().first()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Fetcher Error: transit_coverage for {area_id}: {e}")
        return None

def get_demand_proxy_summary(area_id: str) -> dict | None:
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT area_id, residential_poi_count, commercial_poi_count,
                           education_poi_count, healthcare_poi_count, estimated_activity_score
                    FROM demand_proxy_summary
                    WHERE area_id = :area_id LIMIT 1;
                """),
                {"area_id": area_id},
            ).mappings().first()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Fetcher Error: demand_proxy for {area_id}: {e}")
        return None

def get_candidate_problem_directions(area_id: str) -> list[dict]:
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT problem_direction_id, challenge_type, title,
                           reason_hint, evidence_score, confidence_tier
                    FROM candidate_problem_directions
                    WHERE area_id = :area_id AND enabled = TRUE
                    ORDER BY evidence_score DESC;
                """),
                {"area_id": area_id},
            ).mappings().all()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Fetcher Error: problem_directions for {area_id}: {e}")
        return []
