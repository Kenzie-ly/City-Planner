import logging
from sqlalchemy import text
from db.database import engine

logger = logging.getLogger(__name__)

def get_area_profile(area_id: str) -> dict | None:
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT area_id, area_name, region_id FROM areas WHERE area_id = :area_id LIMIT 1;"),
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
                    SELECT route_id, route_name as route_short_name, trips_per_day, 
                           median_headway_min,
                           confidence_tier as frequency_tier, evidence_score
                    FROM route_headway_summary
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
                    SELECT 
                        COUNT(s.stop_id) as stop_count,
                        LEAST(1.0, COUNT(s.stop_id)::DOUBLE PRECISION / 50.0) as coverage_score
                    FROM gtfs_stops s
                    JOIN areas a ON (ST_Area(a.geom) > 0.1 AND ST_Contains(a.geom, s.geom))
                                 OR (ST_Area(a.geom) <= 0.1 AND ST_DWithin(a.geom, s.geom, 0.05))
                    WHERE a.area_id = :area_id;
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
                    SELECT demand_proxy_score as estimated_activity_score
                    FROM demand_proxy_summary
                    WHERE area_id = :area_id
                    LIMIT 1;
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
                    SELECT challenge_type, title,
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
