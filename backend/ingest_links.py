from __future__ import annotations

import argparse

from sqlalchemy import text

from ingestion_utils import engine, log, slugify


def ensure_link_indexes() -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_gtfs_stops_geom ON gtfs_stops USING GIST(geom);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_gtfs_stops_feed_stop ON gtfs_stops(feed_id, stop_id);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_areas_geom ON areas USING GIST(geom);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_area_gtfs_stops_area_feed_stop ON area_gtfs_stops(area_id, feed_id, stop_id);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_gtfs_stop_times_feed_stop ON gtfs_stop_times(feed_id, stop_id);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_gtfs_trips_feed_trip ON gtfs_trips(feed_id, trip_id);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_osm_transit_stops_geom ON osm_transit_stops USING GIST(geom);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_osm_transit_stops_area ON osm_transit_stops(area_id);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_osm_pois_area ON osm_pois(area_id);"))


def build_area_gtfs_links(feed_id: str, area_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM area_gtfs_stops WHERE area_id = :area_id AND feed_id = :feed_id"), {"area_id": area_id, "feed_id": feed_id})
        conn.execute(text("""
            INSERT INTO area_gtfs_stops (area_id, feed_id, stop_id, distance_to_area_center_m, inside_area)
            SELECT
                a.area_id,
                s.feed_id,
                s.stop_id,
                ST_Distance(s.geom::geography, a.centroid::geography) AS distance_to_area_center_m,
                ST_Contains(a.geom, s.geom) AS inside_area
            FROM areas a
            JOIN gtfs_stops s ON ST_DWithin(a.geom::geography, s.geom::geography, 1000)
            WHERE a.area_id = :area_id AND s.feed_id = :feed_id
            ON CONFLICT (area_id, feed_id, stop_id)
            DO UPDATE SET
                distance_to_area_center_m = EXCLUDED.distance_to_area_center_m,
                inside_area = EXCLUDED.inside_area;
        """), {"area_id": area_id, "feed_id": feed_id})

        conn.execute(text("DELETE FROM area_gtfs_routes WHERE area_id = :area_id AND feed_id = :feed_id"), {"area_id": area_id, "feed_id": feed_id})
        conn.execute(text("""
            INSERT INTO area_gtfs_routes (area_id, feed_id, route_id, stops_in_area, trips_in_area)
            SELECT
                ags.area_id,
                t.feed_id,
                t.route_id,
                COUNT(DISTINCT ags.stop_id) AS stops_in_area,
                COUNT(DISTINCT st.trip_id) AS trips_in_area
            FROM area_gtfs_stops ags
            JOIN gtfs_stop_times st ON st.feed_id = ags.feed_id AND st.stop_id = ags.stop_id
            JOIN gtfs_trips t ON t.feed_id = st.feed_id AND t.trip_id = st.trip_id
            WHERE ags.area_id = :area_id AND ags.feed_id = :feed_id AND t.route_id IS NOT NULL
            GROUP BY ags.area_id, t.feed_id, t.route_id
            ON CONFLICT (area_id, feed_id, route_id)
            DO UPDATE SET
                stops_in_area = EXCLUDED.stops_in_area,
                trips_in_area = EXCLUDED.trips_in_area;
        """), {"area_id": area_id, "feed_id": feed_id})


def build_osm_gtfs_stop_matches(feed_id: str, area_id: str, max_distance_m: int = 80) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            DELETE FROM osm_gtfs_stop_matches
            WHERE feed_id = :feed_id
              AND osm_stop_id IN (SELECT osm_stop_id FROM osm_transit_stops WHERE area_id = :area_id);
        """), {"feed_id": feed_id, "area_id": area_id})
        conn.execute(text("""
            INSERT INTO osm_gtfs_stop_matches (osm_stop_id, feed_id, gtfs_stop_id, distance_m, match_confidence)
            SELECT DISTINCT ON (o.osm_stop_id)
                o.osm_stop_id,
                s.feed_id,
                s.stop_id,
                ST_Distance(o.geom::geography, s.geom::geography) AS distance_m,
                GREATEST(0, 1 - (ST_Distance(o.geom::geography, s.geom::geography) / :max_distance_m)) AS match_confidence
            FROM osm_transit_stops o
            JOIN gtfs_stops s ON s.feed_id = :feed_id
                             AND ST_DWithin(o.geom::geography, s.geom::geography, :max_distance_m)
            WHERE o.area_id = :area_id
            ORDER BY o.osm_stop_id, ST_Distance(o.geom::geography, s.geom::geography)
            ON CONFLICT (osm_stop_id, feed_id, gtfs_stop_id)
            DO UPDATE SET
                distance_m = EXCLUDED.distance_m,
                match_confidence = EXCLUDED.match_confidence;
        """), {"feed_id": feed_id, "area_id": area_id, "max_distance_m": max_distance_m})


def build_demand_proxy_summary(area_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM demand_proxy_summary WHERE area_id = :area_id AND zone_id IS NULL"), {"area_id": area_id})
        conn.execute(text("""
            INSERT INTO demand_proxy_summary (
                area_id, zone_id, school_count, hospital_count, mall_count, office_count,
                university_count, residential_building_count, commercial_poi_count,
                demand_proxy_score, confidence_tier
            )
            SELECT
                :area_id,
                NULL,
                COUNT(*) FILTER (WHERE poi_category = 'school')::integer,
                COUNT(*) FILTER (WHERE poi_category = 'hospital')::integer,
                COUNT(*) FILTER (WHERE poi_category = 'mall')::integer,
                COUNT(*) FILTER (WHERE poi_category IN ('office'))::integer,
                COUNT(*) FILTER (WHERE poi_category = 'university')::integer,
                COUNT(*) FILTER (WHERE poi_category = 'apartments')::integer,
                COUNT(*) FILTER (WHERE poi_category IN ('commercial','retail','supermarket'))::integer,
                (
                    COUNT(*) FILTER (WHERE poi_category = 'school') * 7.0 +
                    COUNT(*) FILTER (WHERE poi_category = 'hospital') * 8.0 +
                    COUNT(*) FILTER (WHERE poi_category = 'mall') * 10.0 +
                    COUNT(*) FILTER (WHERE poi_category = 'office') * 8.5 +
                    COUNT(*) FILTER (WHERE poi_category = 'university') * 9.0 +
                    COUNT(*) FILTER (WHERE poi_category = 'apartments') * 6.0 +
                    COUNT(*) FILTER (WHERE poi_category IN ('commercial','retail','supermarket')) * 7.0
                )::double precision AS demand_proxy_score,
                CASE
                    WHEN COUNT(*) >= 100 THEN 'high'
                    WHEN COUNT(*) >= 25 THEN 'medium'
                    ELSE 'low'
                END AS confidence_tier
            FROM osm_pois
            WHERE area_id = :area_id;
        """), {"area_id": area_id})


def build_route_headway_summary(feed_id: str, area_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM route_headway_summary WHERE area_id = :area_id AND feed_id = :feed_id AND zone_id IS NULL"), {"area_id": area_id, "feed_id": feed_id})
        conn.execute(text("""
            INSERT INTO route_headway_summary (
                area_id, zone_id, feed_id, route_id, route_name, mode,
                stops_in_area, trips_per_day, trips_per_peak_hour,
                median_headway_min, evidence_score, confidence_tier
            )
            WITH route_trips AS (
                SELECT
                    agr.area_id,
                    agr.feed_id,
                    agr.route_id,
                    COALESCE(r.route_short_name, r.route_long_name, agr.route_id) AS route_name,
                    CASE r.route_type
                        WHEN 0 THEN 'tram'
                        WHEN 1 THEN 'subway'
                        WHEN 2 THEN 'rail'
                        WHEN 3 THEN 'bus'
                        ELSE 'other'
                    END AS mode,
                    agr.stops_in_area,
                    COUNT(DISTINCT st.trip_id) AS trips_per_day
                FROM area_gtfs_routes agr
                JOIN gtfs_routes r ON r.feed_id = agr.feed_id AND r.route_id = agr.route_id
                JOIN gtfs_trips t ON t.feed_id = agr.feed_id AND t.route_id = agr.route_id
                JOIN gtfs_stop_times st ON st.feed_id = t.feed_id AND st.trip_id = t.trip_id
                JOIN area_gtfs_stops ags ON ags.feed_id = st.feed_id AND ags.stop_id = st.stop_id AND ags.area_id = agr.area_id
                WHERE agr.area_id = :area_id AND agr.feed_id = :feed_id
                GROUP BY agr.area_id, agr.feed_id, agr.route_id, route_name, mode, agr.stops_in_area
            )
            SELECT
                area_id, NULL, feed_id, route_id, route_name, mode, stops_in_area,
                trips_per_day::integer,
                ROUND((trips_per_day::numeric / 16.0), 2)::double precision AS trips_per_peak_hour,
                CASE WHEN trips_per_day > 0 THEN ROUND((960.0 / trips_per_day)::numeric, 2)::double precision ELSE NULL END AS median_headway_min,
                LEAST(1.0, trips_per_day / 100.0)::double precision AS evidence_score,
                CASE WHEN trips_per_day >= 80 THEN 'high' WHEN trips_per_day >= 25 THEN 'medium' ELSE 'low' END AS confidence_tier
            FROM route_trips;
        """), {"area_id": area_id, "feed_id": feed_id})

def build_area_grid_zones(area_id: str, cell_size_m: int = 1500) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            DELETE FROM zone_accessibility_summary
            WHERE zone_id IN (
                SELECT zone_id FROM zones WHERE area_id = :area_id
            )
        """), {"area_id": area_id})

        conn.execute(text("""
            DELETE FROM zone_transit_coverage_summary
            WHERE zone_id IN (
                SELECT zone_id FROM zones WHERE area_id = :area_id
            )
        """), {"area_id": area_id})

        conn.execute(text("""
            DELETE FROM zone_gtfs_stops
            WHERE zone_id IN (
                SELECT zone_id FROM zones WHERE area_id = :area_id
            )
        """), {"area_id": area_id})

        conn.execute(text("""
            DELETE FROM zones
            WHERE area_id = :area_id
        """), {"area_id": area_id})

        conn.execute(text("""
            INSERT INTO zones (zone_id, area_id, zone_name, zone_type, geom, centroid)
            WITH area AS (
                SELECT area_id, ST_Transform(geom, 3857) AS geom_3857
                FROM areas
                WHERE area_id = :area_id
            ),
            grid AS (
                SELECT
                    area.area_id,
                    (ST_SquareGrid(:cell_size_m, area.geom_3857)).geom AS cell_geom
                FROM area
            ),
            clipped AS (
                SELECT
                    area_id,
                    ST_Intersection(cell_geom, (SELECT geom_3857 FROM area)) AS geom_3857
                FROM grid
                WHERE ST_Intersects(cell_geom, (SELECT geom_3857 FROM area))
            )
            SELECT
                :area_id || '_zone_' || ROW_NUMBER() OVER () AS zone_id,
                :area_id,
                :area_id || ' Zone ' || ROW_NUMBER() OVER () AS zone_name,
                'grid',
                ST_Transform(geom_3857, 4326),
                ST_Centroid(ST_Transform(geom_3857, 4326))
            FROM clipped
            WHERE NOT ST_IsEmpty(geom_3857);
        """), {"area_id": area_id, "cell_size_m": cell_size_m})

def build_zone_gtfs_stops(feed_id: str, area_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            DELETE FROM zone_gtfs_stops
            WHERE zone_id IN (
                SELECT zone_id FROM zones WHERE area_id = :area_id
            )
            AND feed_id = :feed_id;
        """), {"area_id": area_id, "feed_id": feed_id})

        conn.execute(text("""
            INSERT INTO zone_gtfs_stops (zone_id, feed_id, stop_id, inside_zone)
            SELECT
                z.zone_id,
                s.feed_id,
                s.stop_id,
                ST_Contains(z.geom, s.geom) AS inside_zone
            FROM zones z
            JOIN gtfs_stops s
              ON s.feed_id = :feed_id
             AND ST_DWithin(z.geom::geography, s.geom::geography, 100)
            WHERE z.area_id = :area_id
            ON CONFLICT (zone_id, feed_id, stop_id)
            DO UPDATE SET inside_zone = EXCLUDED.inside_zone;
        """), {"area_id": area_id, "feed_id": feed_id})

def build_zone_transit_coverage_summary(feed_id: str, area_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            DELETE FROM zone_transit_coverage_summary
            WHERE area_id = :area_id;
        """), {"area_id": area_id})

        conn.execute(text("""
            INSERT INTO zone_transit_coverage_summary (
                area_id, zone_id, bus_stop_count, rail_station_count,
                route_count, high_frequency_route_count, low_frequency_route_count,
                transit_coverage_score, evidence_score, confidence_tier
            )
            SELECT
                z.area_id,
                z.zone_id,
                COUNT(DISTINCT s.stop_id) FILTER (
                    WHERE COALESCE(s.location_type, 0) = 0
                )::integer AS bus_stop_count,
                COUNT(DISTINCT s.stop_id) FILTER (
                    WHERE COALESCE(s.location_type, 0) IN (1, 2)
                )::integer AS rail_station_count,
                COUNT(DISTINCT t.route_id)::integer AS route_count,
                0,
                0,
                LEAST(1.0, COUNT(DISTINCT s.stop_id) / 20.0)::double precision,
                LEAST(1.0, COUNT(DISTINCT s.stop_id) / 20.0)::double precision,
                CASE
                    WHEN COUNT(DISTINCT s.stop_id) >= 15 THEN 'high'
                    WHEN COUNT(DISTINCT s.stop_id) >= 5 THEN 'medium'
                    ELSE 'low'
                END
            FROM zones z
            LEFT JOIN zone_gtfs_stops zgs ON zgs.zone_id = z.zone_id AND zgs.feed_id = :feed_id
            LEFT JOIN gtfs_stops s ON s.feed_id = zgs.feed_id AND s.stop_id = zgs.stop_id
            LEFT JOIN gtfs_stop_times st ON st.feed_id = s.feed_id AND st.stop_id = s.stop_id
            LEFT JOIN gtfs_trips t ON t.feed_id = st.feed_id AND t.trip_id = st.trip_id
            WHERE z.area_id = :area_id
            GROUP BY z.area_id, z.zone_id;
        """), {"area_id": area_id, "feed_id": feed_id})

def build_zone_accessibility_summary(feed_id: str, area_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            DELETE FROM zone_accessibility_summary
            WHERE area_id = :area_id;
        """), {"area_id": area_id})

        conn.execute(text("""
            INSERT INTO zone_accessibility_summary (
                area_id, zone_id,
                nearest_bus_stop_id, nearest_rail_station_id,
                median_walk_to_bus_stop_m, median_walk_to_station_m,
                pedestrian_connectivity_score, walking_detour_ratio,
                coverage_400m_score, coverage_800m_score,
                evidence_score, confidence_tier
            )
            SELECT
                z.area_id,
                z.zone_id,

                bus.stop_id AS nearest_bus_stop_id,
                rail.stop_id AS nearest_rail_station_id,

                bus.distance_m AS median_walk_to_bus_stop_m,
                rail.distance_m AS median_walk_to_station_m,

                NULL,
                NULL,

                CASE WHEN bus.distance_m <= 400 THEN 1.0 ELSE 0.0 END,
                CASE WHEN bus.distance_m <= 800 THEN 1.0 ELSE 0.0 END,

                CASE
                    WHEN bus.distance_m <= 400 THEN 1.0
                    WHEN bus.distance_m <= 800 THEN 0.6
                    WHEN bus.distance_m IS NOT NULL THEN 0.3
                    ELSE 0.0
                END,

                CASE
                    WHEN bus.distance_m <= 400 THEN 'high'
                    WHEN bus.distance_m <= 800 THEN 'medium'
                    ELSE 'low'
                END
            FROM zones z

            LEFT JOIN LATERAL (
                SELECT
                    s.stop_id,
                    ST_Distance(z.centroid::geography, s.geom::geography) AS distance_m
                FROM gtfs_stops s
                WHERE s.feed_id = :feed_id
                ORDER BY z.centroid <-> s.geom
                LIMIT 1
            ) bus ON TRUE

            LEFT JOIN LATERAL (
                SELECT
                    s.stop_id,
                    ST_Distance(z.centroid::geography, s.geom::geography) AS distance_m
                FROM gtfs_stops s
                WHERE s.feed_id = :feed_id
                  AND COALESCE(s.location_type, 0) IN (1, 2)
                ORDER BY z.centroid <-> s.geom
                LIMIT 1
            ) rail ON TRUE

            WHERE z.area_id = :area_id;
        """), {"area_id": area_id, "feed_id": feed_id})


def build_area_summaries(feed_id: str, area_id: str) -> None:
    ensure_link_indexes()
    log.info("Building links and summaries for area=%s feed=%s", area_id, feed_id)

    build_area_gtfs_links(feed_id, area_id)
    build_osm_gtfs_stop_matches(feed_id, area_id)
    build_demand_proxy_summary(area_id)
    build_route_headway_summary(feed_id, area_id)

    build_area_grid_zones(area_id)
    build_zone_gtfs_stops(feed_id, area_id)
    build_zone_transit_coverage_summary(feed_id, area_id)
    build_zone_accessibility_summary(feed_id, area_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Production ingestion for linking tables")
    parser.add_argument("--feed-id", required=True, help="GTFS Feed ID to use when building links")
    parser.add_argument("--area-id", help="Area ID to use when building links. If omitted, derived from --osm-area.")
    parser.add_argument("--osm-area", help="OSM Area string, used to derive area-id if area-id is omitted")
    parser.add_argument("--all-areas", action="store_true")
    parser.add_argument("--region-id")
    args = parser.parse_args()

    area_id = args.area_id
    if args.all_areas:
        with engine.begin() as conn:
            rows = conn.execute(text("""
                SELECT area_id
                FROM areas
                WHERE (:region_id IS NULL OR region_id = :region_id)
                ORDER BY area_id
            """), {"region_id": args.region_id}).fetchall()

        for row in rows:
            build_area_summaries(args.feed_id, row.area_id)
        return

if __name__ == "__main__":
    main()
