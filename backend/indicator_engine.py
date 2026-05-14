from sqlalchemy import text

from db.database import engine


def build_area_gtfs_links(area_id: str):
    with engine.begin() as conn:
        # Check if the area is large (like a state) or small (like a neighborhood)
        row = conn.execute(
            text("SELECT ST_Area(geom) as area FROM areas WHERE area_id = :area_id LIMIT 1;"),
            {"area_id": area_id},
        ).mappings().first()
        
        area_size = row['area'] if row and row['area'] is not None else 0.0
        print(f"-> Area size for {area_id}: {area_size}")
        
        # Lower the threshold so that cities like Shah Alam (0.002) use the fast query too!
        is_large = area_size > 0.001
        
        if is_large:
            # For large areas, use exact containment (fast)
            sql = """
                INSERT INTO area_gtfs_stops (area_id, feed_id, stop_id, inside_area)
                SELECT a.area_id, s.feed_id, s.stop_id, TRUE
                FROM areas a
                JOIN gtfs_stops s ON ST_Contains(a.geom, s.geom)
                WHERE a.area_id = :area_id
                ON CONFLICT (area_id, feed_id, stop_id) DO NOTHING;
            """
        else:
            # For small areas, use the 5km buffer (optimized to avoid join)
            sql = """
                INSERT INTO area_gtfs_stops (area_id, feed_id, stop_id, inside_area)
                SELECT :area_id, s.feed_id, s.stop_id, TRUE
                FROM gtfs_stops s
                WHERE ST_DWithin((SELECT geom FROM areas WHERE area_id = :area_id LIMIT 1), s.geom, 0.05)
                ON CONFLICT (area_id, feed_id, stop_id) DO NOTHING;
            """
            
        conn.execute(text(sql), {"area_id": area_id})

        conn.execute(
            text("""
                INSERT INTO area_gtfs_routes (
                    area_id,
                    feed_id,
                    route_id,
                    stops_in_area,
                    trips_in_area
                )
                SELECT
                    ags.area_id,
                    st.feed_id,
                    tr.route_id,
                    COUNT(DISTINCT ags.stop_id) AS stops_in_area,
                    COUNT(DISTINCT st.trip_id) AS trips_in_area
                FROM area_gtfs_stops ags
                JOIN gtfs_stop_times st
                    ON ags.feed_id = st.feed_id
                    AND ags.stop_id = st.stop_id
                JOIN gtfs_trips tr
                    ON st.feed_id = tr.feed_id
                    AND st.trip_id = tr.trip_id
                WHERE ags.area_id = :area_id
                GROUP BY ags.area_id, st.feed_id, tr.route_id
                ON CONFLICT (area_id, feed_id, route_id)
                DO UPDATE SET
                    stops_in_area = EXCLUDED.stops_in_area,
                    trips_in_area = EXCLUDED.trips_in_area;
            """),
            {"area_id": area_id},
        )


def build_route_frequency_summary(area_id: str):
    with engine.begin() as conn:
        conn.execute(
            text("""
                DELETE FROM route_headway_summary
                WHERE area_id = :area_id;
            """),
            {"area_id": area_id},
        )

        conn.execute(
            text("""
                INSERT INTO route_headway_summary (
                    area_id,
                    feed_id,
                    route_id,
                    route_name,
                    mode,
                    stops_in_area,
                    trips_per_day,
                    evidence_score,
                    confidence_tier
                )
                SELECT
                    agr.area_id,
                    agr.feed_id,
                    agr.route_id,
                    COALESCE(gr.route_short_name, gr.route_long_name, agr.route_id) AS route_name,

                    CASE
                        WHEN gr.route_type = 3 THEN 'bus'
                        WHEN gr.route_type IN (0, 1, 2) THEN 'rail'
                        ELSE 'other'
                    END AS mode,

                    agr.stops_in_area,
                    agr.trips_in_area AS trips_per_day,

                    CASE
                        WHEN agr.trips_in_area IS NULL THEN 0
                        WHEN agr.trips_in_area >= 100 THEN 1.0
                        ELSE agr.trips_in_area::DOUBLE PRECISION / 100.0
                    END AS evidence_score,

                    CASE
                        WHEN agr.stops_in_area >= 5 AND agr.trips_in_area >= 100 THEN 'strong'
                        WHEN agr.stops_in_area >= 2 AND agr.trips_in_area >= 20 THEN 'usable'
                        ELSE 'weak'
                    END AS confidence_tier

                FROM area_gtfs_routes agr
                LEFT JOIN gtfs_routes gr
                    ON agr.feed_id = gr.feed_id
                    AND agr.route_id = gr.route_id
                WHERE agr.area_id = :area_id;
            """),
            {"area_id": area_id},
        )


def build_transit_coverage_summary(area_id: str):
    with engine.begin() as conn:
        # Check if the area is large (like a state)
        row = conn.execute(
            text("SELECT ST_Area(geom) as area FROM areas WHERE area_id = :area_id LIMIT 1;"),
            {"area_id": area_id},
        ).mappings().first()
        
        # Use 0.01 threshold (Selangor is much larger, Shah Alam is 0.002)
        is_large = row['area'] > 0.01 if row and row['area'] is not None else False
        
        conn.execute(
            text("""
                DELETE FROM zone_transit_coverage_summary
                WHERE area_id = :area_id;
            """),
            {"area_id": area_id},
        )

        # Skip OSM heavy counts for large areas
        bus_count_query = "0" if is_large else """
            SELECT COUNT(*)
            FROM osm_transit_stops s
            WHERE ST_Contains((SELECT geom FROM areas WHERE area_id = :area_id LIMIT 1), s.geom)
            AND s.stop_type = 'bus_stop'
        """
        
        rail_count_query = "0" if is_large else """
            SELECT COUNT(*)
            FROM osm_transit_stops s
            WHERE ST_Contains((SELECT geom FROM areas WHERE area_id = :area_id LIMIT 1), s.geom)
            AND s.stop_type IN ('rail_station', 'public_transport')
        """
        
        total_count_query = "0" if is_large else """
            SELECT COUNT(*)
            FROM osm_transit_stops s
            WHERE ST_Contains((SELECT geom FROM areas WHERE area_id = :area_id LIMIT 1), s.geom)
        """

        conn.execute(
            text(f"""
                INSERT INTO zone_transit_coverage_summary (
                    area_id,
                    bus_stop_count,
                    rail_station_count,
                    route_count,
                    high_frequency_route_count,
                    low_frequency_route_count,
                    transit_coverage_score,
                    evidence_score,
                    confidence_tier
                )
                SELECT
                    :area_id AS area_id,

                    COALESCE(({bus_count_query}), 0) AS bus_stop_count,

                    COALESCE(({rail_count_query}), 0) AS rail_station_count,

                    COALESCE((
                        SELECT COUNT(DISTINCT route_id)
                        FROM area_gtfs_routes
                        WHERE area_id = :area_id
                    ), 0) AS route_count,

                    NULL AS high_frequency_route_count,
                    NULL AS low_frequency_route_count,

                    LEAST(1.0, COALESCE(({total_count_query}), 0)::DOUBLE PRECISION / 50.0) AS transit_coverage_score,

                    LEAST(1.0, COALESCE(({total_count_query}), 0)::DOUBLE PRECISION / 50.0) AS evidence_score,

                    CASE
                        WHEN COALESCE(({total_count_query}), 0) >= 50 THEN 'strong'
                        WHEN COALESCE(({total_count_query}), 0) >= 10 THEN 'usable'
                        ELSE 'weak'
                    END AS confidence_tier;
            """),
            {"area_id": area_id},
        )


def build_demand_proxy_summary(area_id: str):
    with engine.begin() as conn:
        conn.execute(
            text("""
                DELETE FROM demand_proxy_summary
                WHERE area_id = :area_id;
            """),
            {"area_id": area_id},
        )

        conn.execute(
            text("""
                INSERT INTO demand_proxy_summary (
                    area_id,
                    school_count,
                    hospital_count,
                    mall_count,
                    office_count,
                    university_count,
                    residential_building_count,
                    commercial_poi_count,
                    demand_proxy_score,
                    confidence_tier
                )
                SELECT
                    :area_id AS area_id,

                    COUNT(*) FILTER (WHERE p.poi_category = 'school') AS school_count,
                    COUNT(*) FILTER (WHERE p.poi_category = 'hospital') AS hospital_count,
                    COUNT(*) FILTER (WHERE p.poi_category = 'mall') AS mall_count,
                    COUNT(*) FILTER (WHERE p.poi_category = 'office') AS office_count,
                    COUNT(*) FILTER (WHERE p.poi_category = 'university') AS university_count,
                    COUNT(*) FILTER (WHERE p.poi_category = 'residential') AS residential_building_count,
                    COUNT(*) FILTER (WHERE p.poi_category = 'commercial') AS commercial_poi_count,

                    LEAST(1.0, COUNT(*)::DOUBLE PRECISION / 100.0) AS demand_proxy_score,

                    CASE
                        WHEN COUNT(*) >= 100 THEN 'strong'
                        WHEN COUNT(*) >= 30 THEN 'usable'
                        ELSE 'weak'
                    END AS confidence_tier

                FROM osm_pois p
                JOIN areas a
                    ON ST_Contains(a.geom, p.geom)
                WHERE a.area_id = :area_id;
            """),
            {"area_id": area_id},
        )


def build_candidate_problem_directions(area_id: str):
    with engine.begin() as conn:
        conn.execute(
            text("""
                DELETE FROM candidate_problem_directions
                WHERE area_id = :area_id;
            """),
            {"area_id": area_id},
        )

        # Bus frequency gap
        conn.execute(
            text("""
                INSERT INTO candidate_problem_directions (
                    area_id,
                    challenge_type,
                    title,
                    reason_hint,
                    evidence_score,
                    confidence_tier,
                    evidence_refs,
                    enabled
                )
                SELECT
                    :area_id,
                    'bus_frequency_gap',
                    'Possible long waiting time on bus services',
                    'Some routes serving this area may have limited trip frequency or weak service coverage.',
                    CASE
                        WHEN COUNT(*) = 0 THEN 0
                        ELSE AVG(1.0 - LEAST(1.0, COALESCE(trips_per_day, 0)::DOUBLE PRECISION / 100.0))
                    END,
                    CASE
                        WHEN COUNT(*) >= 3 THEN 'usable'
                        WHEN COUNT(*) >= 1 THEN 'weak'
                        ELSE 'invalid'
                    END,
                    jsonb_build_object(
                        'source_tables', ARRAY['route_headway_summary'],
                        'route_count', COUNT(*)
                    ),
                    CASE WHEN COUNT(*) > 0 THEN TRUE ELSE FALSE END
                FROM route_headway_summary
                WHERE area_id = :area_id
                AND mode = 'bus';
            """),
            {"area_id": area_id},
        )

        # Transit coverage gap
        conn.execute(
            text("""
                INSERT INTO candidate_problem_directions (
                    area_id,
                    challenge_type,
                    title,
                    reason_hint,
                    evidence_score,
                    confidence_tier,
                    evidence_refs,
                    enabled
                )
                SELECT
                    :area_id,
                    'transit_coverage_gap',
                    'Possible weak transit coverage',
                    'The number of transit stops or routes in the area may be limited.',
                    1.0 - COALESCE(MAX(transit_coverage_score), 0),
                    CASE
                        WHEN COALESCE(MAX(transit_coverage_score), 0) < 0.3 THEN 'usable'
                        WHEN COALESCE(MAX(transit_coverage_score), 0) < 0.6 THEN 'weak'
                        ELSE 'invalid'
                    END,
                    jsonb_build_object(
                        'source_tables', ARRAY['zone_transit_coverage_summary'],
                        'transit_coverage_score', MAX(transit_coverage_score)
                    ),
                    CASE
                        WHEN COALESCE(MAX(transit_coverage_score), 0) < 0.6 THEN TRUE
                        ELSE FALSE
                    END
                FROM zone_transit_coverage_summary
                WHERE area_id = :area_id;
            """),
            {"area_id": area_id},
        )

        # First-mile / last-mile gap
        conn.execute(
            text("""
                INSERT INTO candidate_problem_directions (
                    area_id,
                    challenge_type,
                    title,
                    reason_hint,
                    evidence_score,
                    confidence_tier,
                    evidence_refs,
                    enabled
                )
                SELECT
                    :area_id,
                    'first_mile_last_mile_gap',
                    'Possible first-mile / last-mile access issue',
                    'The area has demand-related POIs and transit supply, so access to transit may need further analysis.',
                    COALESCE(MAX(demand_proxy_score), 0),
                    CASE
                        WHEN COALESCE(MAX(demand_proxy_score), 0) >= 0.7 THEN 'usable'
                        WHEN COALESCE(MAX(demand_proxy_score), 0) >= 0.3 THEN 'weak'
                        ELSE 'invalid'
                    END,
                    jsonb_build_object(
                        'source_tables', ARRAY['demand_proxy_summary', 'osm_transit_stops'],
                        'demand_proxy_score', MAX(demand_proxy_score)
                    ),
                    CASE
                        WHEN COALESCE(MAX(demand_proxy_score), 0) >= 0.3 THEN TRUE
                        ELSE FALSE
                    END
                FROM demand_proxy_summary
                WHERE area_id = :area_id;
            """),
            {"area_id": area_id},
        )


### Indicator Engine ###
def run_indicator_engine(area_id: str) -> dict:
    build_area_gtfs_links(area_id)
    build_route_frequency_summary(area_id)
    build_transit_coverage_summary(area_id)
    build_demand_proxy_summary(area_id)
    build_candidate_problem_directions(area_id)

    return {
        "area_id": area_id,
        "status": "success",
        "message": "Indicators generated successfully.",
    }