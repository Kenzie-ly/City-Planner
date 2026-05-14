-- =========================================================
-- 0. EXTENSIONS
-- =========================================================

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

-- =========================================================
-- 1. AREA / REGION TABLES
-- =========================================================

CREATE TABLE regions (
    region_id TEXT PRIMARY KEY,
    region_name TEXT NOT NULL,
    country TEXT DEFAULT 'Malaysia',
    graphml_path TEXT,
    geom GEOMETRY(MULTIPOLYGON, 4326),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_regions_geom
ON regions
USING GIST (geom);


CREATE TABLE areas (
    area_id TEXT PRIMARY KEY,
    region_id TEXT REFERENCES regions(region_id),
    area_name TEXT NOT NULL,
    area_type TEXT, -- city, district, neighbourhood, station_area, grid_zone
    source TEXT, -- user_defined, osm, admin_boundary, grid
    geom GEOMETRY(MULTIPOLYGON, 4326),
    centroid GEOMETRY(POINT, 4326),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_areas_region
ON areas(region_id);

CREATE INDEX idx_areas_geom
ON areas
USING GIST (geom);

CREATE INDEX idx_areas_centroid
ON areas
USING GIST (centroid);


CREATE TABLE zones (
    zone_id TEXT PRIMARY KEY,
    area_id TEXT REFERENCES areas(area_id),
    zone_name TEXT,
    zone_type TEXT, -- grid, neighbourhood, station_buffer, corridor
    geom GEOMETRY(MULTIPOLYGON, 4326),
    centroid GEOMETRY(POINT, 4326),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_zones_area
ON zones(area_id);

CREATE INDEX idx_zones_geom
ON zones
USING GIST (geom);


-- =========================================================
-- 2. OSM / OSMNX RAW + PROCESSED TABLES
-- =========================================================

CREATE TABLE osm_nodes (
    osm_node_id BIGINT PRIMARY KEY,
    region_id TEXT REFERENCES regions(region_id),
    x DOUBLE PRECISION,
    y DOUBLE PRECISION,
    geom GEOMETRY(POINT, 4326),
    tags JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_osm_nodes_region
ON osm_nodes(region_id);

CREATE INDEX idx_osm_nodes_geom
ON osm_nodes
USING GIST (geom);


CREATE TABLE osm_edges (
    edge_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    region_id TEXT REFERENCES regions(region_id),
    u BIGINT,
    v BIGINT,
    key INTEGER,
    osmid TEXT,
    highway TEXT,
    name TEXT,
    length_m DOUBLE PRECISION,
    one_way BOOLEAN,
    maxspeed TEXT,
    geometry GEOMETRY(LINESTRING, 4326),
    tags JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_osm_edges_region
ON osm_edges(region_id);

CREATE INDEX idx_osm_edges_geom
ON osm_edges
USING GIST (geometry);

CREATE INDEX idx_osm_edges_name
ON osm_edges(name);


CREATE TABLE osm_transit_stops (
    osm_stop_id TEXT PRIMARY KEY,
    area_id TEXT REFERENCES areas(area_id),
    region_id TEXT REFERENCES regions(region_id),
    stop_name TEXT,
    stop_type TEXT, -- bus_stop, rail_station, mrt_station, lrt_station
    osm_id TEXT,
    source_tag TEXT,
    geom GEOMETRY(POINT, 4326),
    tags JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_osm_transit_stops_area
ON osm_transit_stops(area_id);

CREATE INDEX idx_osm_transit_stops_region
ON osm_transit_stops(region_id);

CREATE INDEX idx_osm_transit_stops_geom
ON osm_transit_stops
USING GIST (geom);


CREATE TABLE osm_pois (
    poi_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    area_id TEXT REFERENCES areas(area_id),
    region_id TEXT REFERENCES regions(region_id),
    osm_id TEXT,
    name TEXT,
    poi_category TEXT, -- school, hospital, mall, office, residential, commercial
    amenity TEXT,
    shop TEXT,
    office TEXT,
    building TEXT,
    geom GEOMETRY(GEOMETRY, 4326),
    tags JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_osm_pois_area
ON osm_pois(area_id);

CREATE INDEX idx_osm_pois_region
ON osm_pois(region_id);

CREATE INDEX idx_osm_pois_category
ON osm_pois(poi_category);

CREATE INDEX idx_osm_pois_geom
ON osm_pois
USING GIST (geom);


-- =========================================================
-- 3. GTFS STATIC RAW TABLES
-- =========================================================

CREATE TABLE gtfs_feeds (
    feed_id TEXT PRIMARY KEY,
    agency TEXT NOT NULL, -- prasarana, ktmb, mybas-johor
    category TEXT, -- rapid-bus-kl, mrt, lrt, etc.
    source_url TEXT,
    downloaded_at TIMESTAMP DEFAULT NOW(),
    valid_from DATE,
    valid_to DATE,
    raw_zip_path TEXT,
    active BOOLEAN DEFAULT TRUE
);


CREATE TABLE gtfs_agency (
    feed_id TEXT REFERENCES gtfs_feeds(feed_id),
    agency_id TEXT,
    agency_name TEXT,
    agency_url TEXT,
    agency_timezone TEXT,
    agency_lang TEXT,
    agency_phone TEXT,
    agency_fare_url TEXT,
    PRIMARY KEY(feed_id, agency_id)
);


CREATE TABLE gtfs_stops (
    feed_id TEXT REFERENCES gtfs_feeds(feed_id),
    stop_id TEXT,
    stop_code TEXT,
    stop_name TEXT,
    stop_desc TEXT,
    stop_lat DOUBLE PRECISION,
    stop_lon DOUBLE PRECISION,
    zone_id_raw TEXT,
    stop_url TEXT,
    location_type INTEGER,
    parent_station TEXT,
    wheelchair_boarding INTEGER,
    geom GEOMETRY(POINT, 4326),
    PRIMARY KEY(feed_id, stop_id)
);

CREATE INDEX idx_gtfs_stops_geom
ON gtfs_stops
USING GIST (geom);

CREATE INDEX idx_gtfs_stops_name
ON gtfs_stops(stop_name);


CREATE TABLE gtfs_routes (
    feed_id TEXT REFERENCES gtfs_feeds(feed_id),
    route_id TEXT,
    agency_id TEXT,
    route_short_name TEXT,
    route_long_name TEXT,
    route_desc TEXT,
    route_type INTEGER,
    route_url TEXT,
    route_color TEXT,
    route_text_color TEXT,
    PRIMARY KEY(feed_id, route_id)
);

CREATE INDEX idx_gtfs_routes_short_name
ON gtfs_routes(route_short_name);


CREATE TABLE gtfs_trips (
    feed_id TEXT REFERENCES gtfs_feeds(feed_id),
    route_id TEXT,
    service_id TEXT,
    trip_id TEXT,
    trip_headsign TEXT,
    trip_short_name TEXT,
    direction_id INTEGER,
    block_id TEXT,
    shape_id TEXT,
    wheelchair_accessible INTEGER,
    bikes_allowed INTEGER,
    PRIMARY KEY(feed_id, trip_id)
);

CREATE INDEX idx_gtfs_trips_route
ON gtfs_trips(feed_id, route_id);

CREATE INDEX idx_gtfs_trips_shape
ON gtfs_trips(feed_id, shape_id);


CREATE TABLE gtfs_stop_times (
    feed_id TEXT REFERENCES gtfs_feeds(feed_id),
    trip_id TEXT,
    arrival_time TEXT,
    departure_time TEXT,
    stop_id TEXT,
    stop_sequence INTEGER,
    stop_headsign TEXT,
    pickup_type INTEGER,
    drop_off_type INTEGER,
    shape_dist_traveled DOUBLE PRECISION,
    timepoint INTEGER,
    PRIMARY KEY(feed_id, trip_id, stop_sequence)
);

CREATE INDEX idx_gtfs_stop_times_stop
ON gtfs_stop_times(feed_id, stop_id);

CREATE INDEX idx_gtfs_stop_times_trip
ON gtfs_stop_times(feed_id, trip_id);


CREATE TABLE gtfs_calendar (
    feed_id TEXT REFERENCES gtfs_feeds(feed_id),
    service_id TEXT,
    monday INTEGER,
    tuesday INTEGER,
    wednesday INTEGER,
    thursday INTEGER,
    friday INTEGER,
    saturday INTEGER,
    sunday INTEGER,
    start_date DATE,
    end_date DATE,
    PRIMARY KEY(feed_id, service_id)
);


CREATE TABLE gtfs_calendar_dates (
    feed_id TEXT REFERENCES gtfs_feeds(feed_id),
    service_id TEXT,
    date DATE,
    exception_type INTEGER,
    PRIMARY KEY(feed_id, service_id, date)
);


CREATE TABLE gtfs_shapes (
    feed_id TEXT REFERENCES gtfs_feeds(feed_id),
    shape_id TEXT,
    shape_pt_lat DOUBLE PRECISION,
    shape_pt_lon DOUBLE PRECISION,
    shape_pt_sequence INTEGER,
    shape_dist_traveled DOUBLE PRECISION,
    geom GEOMETRY(POINT, 4326),
    PRIMARY KEY(feed_id, shape_id, shape_pt_sequence)
);

CREATE INDEX idx_gtfs_shapes_geom
ON gtfs_shapes
USING GIST (geom);

CREATE INDEX idx_gtfs_shapes_shape
ON gtfs_shapes(feed_id, shape_id);


CREATE TABLE gtfs_shape_lines (
    feed_id TEXT REFERENCES gtfs_feeds(feed_id),
    shape_id TEXT,
    geom GEOMETRY(LINESTRING, 4326),
    PRIMARY KEY(feed_id, shape_id)
);

CREATE INDEX idx_gtfs_shape_lines_geom
ON gtfs_shape_lines
USING GIST (geom);


-- =========================================================
-- 4. GTFS REALTIME TABLES
-- =========================================================

CREATE TABLE gtfs_realtime_snapshots (
    snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    feed_id TEXT REFERENCES gtfs_feeds(feed_id),
    agency TEXT,
    category TEXT,
    fetched_at TIMESTAMP DEFAULT NOW(),
    raw_pb_path TEXT,
    status TEXT DEFAULT 'success',
    error_message TEXT
);


CREATE TABLE gtfs_realtime_vehicle_positions (
    position_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_id UUID REFERENCES gtfs_realtime_snapshots(snapshot_id),
    feed_id TEXT REFERENCES gtfs_feeds(feed_id),
    vehicle_id TEXT,
    trip_id TEXT,
    route_id TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    bearing DOUBLE PRECISION,
    speed DOUBLE PRECISION,
    current_stop_sequence INTEGER,
    current_status TEXT,
    timestamp_utc TIMESTAMP,
    geom GEOMETRY(POINT, 4326),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_vehicle_positions_route
ON gtfs_realtime_vehicle_positions(feed_id, route_id);

CREATE INDEX idx_vehicle_positions_trip
ON gtfs_realtime_vehicle_positions(feed_id, trip_id);

CREATE INDEX idx_vehicle_positions_time
ON gtfs_realtime_vehicle_positions(timestamp_utc);

CREATE INDEX idx_vehicle_positions_geom
ON gtfs_realtime_vehicle_positions
USING GIST (geom);


-- =========================================================
-- 5. LINKING TABLES BETWEEN OSM / GTFS / AREAS / ZONES
-- =========================================================

CREATE TABLE area_gtfs_stops (
    area_id TEXT REFERENCES areas(area_id),
    feed_id TEXT,
    stop_id TEXT,
    distance_to_area_center_m DOUBLE PRECISION,
    inside_area BOOLEAN DEFAULT TRUE,
    PRIMARY KEY(area_id, feed_id, stop_id)
);

CREATE INDEX idx_area_gtfs_stops_area
ON area_gtfs_stops(area_id);

CREATE INDEX idx_area_gtfs_stops_stop
ON area_gtfs_stops(feed_id, stop_id);


CREATE TABLE zone_gtfs_stops (
    zone_id TEXT REFERENCES zones(zone_id),
    feed_id TEXT,
    stop_id TEXT,
    inside_zone BOOLEAN DEFAULT TRUE,
    PRIMARY KEY(zone_id, feed_id, stop_id)
);

CREATE INDEX idx_zone_gtfs_stops_zone
ON zone_gtfs_stops(zone_id);


CREATE TABLE area_gtfs_routes (
    area_id TEXT REFERENCES areas(area_id),
    feed_id TEXT,
    route_id TEXT,
    stops_in_area INTEGER,
    trips_in_area INTEGER,
    PRIMARY KEY(area_id, feed_id, route_id)
);

CREATE INDEX idx_area_gtfs_routes_area
ON area_gtfs_routes(area_id);

CREATE INDEX idx_area_gtfs_routes_route
ON area_gtfs_routes(feed_id, route_id);


CREATE TABLE osm_gtfs_stop_matches (
    match_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    osm_stop_id TEXT REFERENCES osm_transit_stops(osm_stop_id),
    feed_id TEXT,
    gtfs_stop_id TEXT,
    distance_m DOUBLE PRECISION,
    match_confidence DOUBLE PRECISION,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_osm_gtfs_stop_matches_osm
ON osm_gtfs_stop_matches(osm_stop_id);

CREATE INDEX idx_osm_gtfs_stop_matches_gtfs
ON osm_gtfs_stop_matches(feed_id, gtfs_stop_id);


-- =========================================================
-- 6. PROCESSED SUMMARY TABLES
-- =========================================================

CREATE TABLE route_headway_summary (
    summary_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    area_id TEXT REFERENCES areas(area_id),
    zone_id TEXT REFERENCES zones(zone_id),
    feed_id TEXT REFERENCES gtfs_feeds(feed_id),
    route_id TEXT,
    route_name TEXT,
    mode TEXT,
    stops_in_area INTEGER,
    trips_per_day INTEGER,
    trips_per_peak_hour DOUBLE PRECISION,
    median_headway_min DOUBLE PRECISION,
    peak_headway_min DOUBLE PRECISION,
    offpeak_headway_min DOUBLE PRECISION,
    first_service_time TEXT,
    last_service_time TEXT,
    evidence_score DOUBLE PRECISION,
    confidence_tier TEXT, -- strong, usable, weak, invalid
    generated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_route_headway_area
ON route_headway_summary(area_id);

CREATE INDEX idx_route_headway_zone
ON route_headway_summary(zone_id);

CREATE INDEX idx_route_headway_route
ON route_headway_summary(feed_id, route_id);

CREATE INDEX idx_route_headway_score
ON route_headway_summary(evidence_score DESC);


CREATE TABLE zone_accessibility_summary (
    summary_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    area_id TEXT REFERENCES areas(area_id),
    zone_id TEXT REFERENCES zones(zone_id),
    nearest_bus_stop_id TEXT,
    nearest_rail_station_id TEXT,
    median_walk_to_bus_stop_m DOUBLE PRECISION,
    median_walk_to_station_m DOUBLE PRECISION,
    pedestrian_connectivity_score DOUBLE PRECISION,
    walking_detour_ratio DOUBLE PRECISION,
    coverage_400m_score DOUBLE PRECISION,
    coverage_800m_score DOUBLE PRECISION,
    evidence_score DOUBLE PRECISION,
    confidence_tier TEXT,
    generated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_zone_accessibility_area
ON zone_accessibility_summary(area_id);

CREATE INDEX idx_zone_accessibility_zone
ON zone_accessibility_summary(zone_id);

CREATE INDEX idx_zone_accessibility_score
ON zone_accessibility_summary(evidence_score DESC);


CREATE TABLE zone_transit_coverage_summary (
    summary_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    area_id TEXT REFERENCES areas(area_id),
    zone_id TEXT REFERENCES zones(zone_id),
    bus_stop_count INTEGER,
    rail_station_count INTEGER,
    route_count INTEGER,
    high_frequency_route_count INTEGER,
    low_frequency_route_count INTEGER,
    transit_coverage_score DOUBLE PRECISION,
    evidence_score DOUBLE PRECISION,
    confidence_tier TEXT,
    generated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_zone_coverage_area
ON zone_transit_coverage_summary(area_id);

CREATE INDEX idx_zone_coverage_zone
ON zone_transit_coverage_summary(zone_id);


CREATE TABLE demand_proxy_summary (
    summary_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    area_id TEXT REFERENCES areas(area_id),
    zone_id TEXT REFERENCES zones(zone_id),
    school_count INTEGER DEFAULT 0,
    hospital_count INTEGER DEFAULT 0,
    mall_count INTEGER DEFAULT 0,
    office_count INTEGER DEFAULT 0,
    university_count INTEGER DEFAULT 0,
    residential_building_count INTEGER DEFAULT 0,
    commercial_poi_count INTEGER DEFAULT 0,
    demand_proxy_score DOUBLE PRECISION,
    confidence_tier TEXT,
    generated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_demand_proxy_area
ON demand_proxy_summary(area_id);

CREATE INDEX idx_demand_proxy_zone
ON demand_proxy_summary(zone_id);


CREATE TABLE realtime_reliability_summary (
    summary_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    area_id TEXT REFERENCES areas(area_id),
    zone_id TEXT REFERENCES zones(zone_id),
    feed_id TEXT REFERENCES gtfs_feeds(feed_id),
    route_id TEXT,
    observation_start TIMESTAMP,
    observation_end TIMESTAMP,
    vehicle_count INTEGER,
    median_vehicle_gap_min DOUBLE PRECISION,
    gap_variability_score DOUBLE PRECISION,
    possible_bunching BOOLEAN,
    reliability_score DOUBLE PRECISION,
    confidence_tier TEXT,
    generated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_reliability_area
ON realtime_reliability_summary(area_id);

CREATE INDEX idx_reliability_route
ON realtime_reliability_summary(feed_id, route_id);


-- =========================================================
-- 7. RAG / DOCUMENT TABLES
-- =========================================================

CREATE TABLE documents (
    doc_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT,
    source_url TEXT,
    source_type TEXT, -- news, report, planning_document, complaint, research
    publisher TEXT,
    published_date DATE,
    fetched_at TIMESTAMP DEFAULT NOW(),
    raw_text_path TEXT,
    metadata JSONB
);

CREATE INDEX idx_documents_source_type
ON documents(source_type);

CREATE INDEX idx_documents_published_date
ON documents(published_date);


CREATE TABLE document_chunks (
    chunk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_id UUID REFERENCES documents(doc_id),
    chunk_index INTEGER,
    chunk_text TEXT NOT NULL,
    area_tags TEXT[],
    topic_tags TEXT[],
    station_tags TEXT[],
    route_tags TEXT[],
    challenge_type_tags TEXT[],
    embedding VECTOR, -- only works if pgvector is installed
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_document_chunks_doc
ON document_chunks(doc_id);

CREATE INDEX idx_document_chunks_area_tags
ON document_chunks
USING GIN (area_tags);

CREATE INDEX idx_document_chunks_topic_tags
ON document_chunks
USING GIN (topic_tags);

CREATE INDEX idx_document_chunks_challenge_tags
ON document_chunks
USING GIN (challenge_type_tags);


CREATE TABLE evidence_links (
    link_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chunk_id UUID REFERENCES document_chunks(chunk_id),
    area_id TEXT REFERENCES areas(area_id),
    zone_id TEXT REFERENCES zones(zone_id),
    feed_id TEXT,
    route_id TEXT,
    stop_id TEXT,
    station_name TEXT,
    challenge_type TEXT,
    link_type TEXT, -- area, route, stop, station, issue
    relevance_score DOUBLE PRECISION,
    alignment_score DOUBLE PRECISION,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_evidence_links_area
ON evidence_links(area_id);

CREATE INDEX idx_evidence_links_challenge
ON evidence_links(challenge_type);

CREATE INDEX idx_evidence_links_route
ON evidence_links(feed_id, route_id);

CREATE INDEX idx_evidence_links_chunk
ON evidence_links(chunk_id);


-- =========================================================
-- 8. PROBLEM DIRECTIONS / HOTSPOTS
-- =========================================================

CREATE TABLE candidate_problem_directions (
    problem_direction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    area_id TEXT REFERENCES areas(area_id),
    challenge_type TEXT NOT NULL,
    title TEXT,
    reason_hint TEXT,
    evidence_score DOUBLE PRECISION,
    confidence_tier TEXT, -- strong, usable, weak, invalid
    evidence_refs JSONB,
    enabled BOOLEAN DEFAULT TRUE,
    generated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_problem_directions_area
ON candidate_problem_directions(area_id);

CREATE INDEX idx_problem_directions_type
ON candidate_problem_directions(challenge_type);

CREATE INDEX idx_problem_directions_score
ON candidate_problem_directions(evidence_score DESC);


CREATE TABLE candidate_hotspots (
    hotspot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    area_id TEXT REFERENCES areas(area_id),
    zone_id TEXT REFERENCES zones(zone_id),
    challenge_type TEXT NOT NULL,
    hotspot_name TEXT,
    hotspot_type TEXT, -- corridor, station_area, zone, route_segment
    score DOUBLE PRECISION,
    confidence_tier TEXT,
    related_routes JSONB,
    related_stops JSONB,
    related_stations JSONB,
    evidence_refs JSONB,
    geometry_ref TEXT,
    geom GEOMETRY(GEOMETRY, 4326),
    map_ready BOOLEAN DEFAULT FALSE,
    solution_ready BOOLEAN DEFAULT FALSE,
    generated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_hotspots_area
ON candidate_hotspots(area_id);

CREATE INDEX idx_hotspots_challenge
ON candidate_hotspots(challenge_type);

CREATE INDEX idx_hotspots_score
ON candidate_hotspots(score DESC);

CREATE INDEX idx_hotspots_geom
ON candidate_hotspots
USING GIST (geom);


-- =========================================================
-- 9. EVIDENCE PACKS
-- =========================================================

CREATE TABLE evidence_packs (
    evidence_pack_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    area_id TEXT REFERENCES areas(area_id),
    selected_challenge_type TEXT,
    selected_hotspot_id UUID REFERENCES candidate_hotspots(hotspot_id),
    pack_type TEXT, -- general, focused, solution_readiness
    pack_json JSONB NOT NULL,
    data_quality JSONB,
    blocked_claims JSONB,
    confidence_tier TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_evidence_packs_area
ON evidence_packs(area_id);

CREATE INDEX idx_evidence_packs_type
ON evidence_packs(pack_type);

CREATE INDEX idx_evidence_packs_challenge
ON evidence_packs(selected_challenge_type);


-- =========================================================
-- 10. AGENT RUNS / OUTPUTS
-- =========================================================

CREATE TABLE agent_runs (
    agent_run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_name TEXT NOT NULL,
    area_id TEXT REFERENCES areas(area_id),
    evidence_pack_id UUID REFERENCES evidence_packs(evidence_pack_id),
    input_json JSONB,
    output_json JSONB,
    status TEXT DEFAULT 'success', -- success, failed, repaired
    error_message TEXT,
    started_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP
);

CREATE INDEX idx_agent_runs_agent
ON agent_runs(agent_name);

CREATE INDEX idx_agent_runs_area
ON agent_runs(area_id);


CREATE TABLE broad_challenge_cards (
    card_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_run_id UUID REFERENCES agent_runs(agent_run_id),
    problem_direction_id UUID REFERENCES candidate_problem_directions(problem_direction_id),
    area_id TEXT REFERENCES areas(area_id),
    challenge_type TEXT,
    title TEXT,
    description TEXT,
    confidence_tier TEXT,
    evidence_refs JSONB,
    display_order INTEGER,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_broad_cards_area
ON broad_challenge_cards(area_id);


CREATE TABLE specific_hotspot_cards (
    card_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_run_id UUID REFERENCES agent_runs(agent_run_id),
    hotspot_id UUID REFERENCES candidate_hotspots(hotspot_id),
    area_id TEXT REFERENCES areas(area_id),
    challenge_type TEXT,
    title TEXT,
    description TEXT,
    confidence_tier TEXT,
    evidence_refs JSONB,
    display_order INTEGER,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_specific_cards_area
ON specific_hotspot_cards(area_id);


CREATE TABLE solution_options (
    solution_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hotspot_id UUID REFERENCES candidate_hotspots(hotspot_id),
    agent_run_id UUID REFERENCES agent_runs(agent_run_id),
    solution_type TEXT,
    title TEXT,
    description TEXT,
    expected_benefit TEXT,
    tradeoffs JSONB,
    evidence_refs JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_solution_options_hotspot
ON solution_options(hotspot_id);


CREATE TABLE reviewed_solutions (
    review_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    solution_id UUID REFERENCES solution_options(solution_id),
    agent_run_id UUID REFERENCES agent_runs(agent_run_id),
    verdict TEXT, -- recommended_primary, recommended_supporting, conditional, rejected
    balanced_score DOUBLE PRECISION,
    impact_score DOUBLE PRECISION,
    feasibility_score DOUBLE PRECISION,
    cost_efficiency_score DOUBLE PRECISION,
    implementation_speed_score DOUBLE PRECISION,
    safety_environment_score DOUBLE PRECISION,
    reason TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_reviewed_solutions_solution
ON reviewed_solutions(solution_id);


CREATE TABLE final_proposals (
    proposal_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    area_id TEXT REFERENCES areas(area_id),
    hotspot_id UUID REFERENCES candidate_hotspots(hotspot_id),
    agent_run_id UUID REFERENCES agent_runs(agent_run_id),
    title TEXT,
    selected_problem TEXT,
    primary_recommendation JSONB,
    supporting_recommendation JSONB,
    implementation_phases JSONB,
    evidence_used JSONB,
    limitations JSONB,
    confidence_tier TEXT,
    map_payload JSONB,
    proposal_json JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_final_proposals_area
ON final_proposals(area_id);

CREATE INDEX idx_final_proposals_hotspot
ON final_proposals(hotspot_id);


-- =========================================================
-- 11. USER SESSION / SELECTION TABLES
-- =========================================================

CREATE TABLE user_sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT,
    area_id TEXT REFERENCES areas(area_id),
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);


CREATE TABLE user_selections (
    selection_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES user_sessions(session_id),
    selection_type TEXT, -- area, broad_challenge, hotspot
    selected_id TEXT,
    selected_json JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_user_selections_session
ON user_selections(session_id);


-- =========================================================
-- 12. DATA FRESHNESS / INGESTION LOGS
-- =========================================================

CREATE TABLE ingestion_runs (
    ingestion_run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type TEXT, -- osm, gtfs_static, gtfs_realtime, rag, poi
    source_name TEXT,
    region_id TEXT REFERENCES regions(region_id),
    area_id TEXT REFERENCES areas(area_id),
    status TEXT DEFAULT 'success',
    records_inserted INTEGER DEFAULT 0,
    records_updated INTEGER DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP
);

CREATE INDEX idx_ingestion_runs_source
ON ingestion_runs(source_type);

CREATE INDEX idx_ingestion_runs_region
ON ingestion_runs(region_id);


CREATE TABLE data_freshness (
    freshness_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type TEXT,
    source_name TEXT,
    region_id TEXT REFERENCES regions(region_id),
    area_id TEXT REFERENCES areas(area_id),
    last_successful_update TIMESTAMP,
    next_scheduled_update TIMESTAMP,
    freshness_status TEXT, -- fresh, stale, missing
    notes TEXT
);

CREATE INDEX idx_data_freshness_source
ON data_freshness(source_type);

CREATE INDEX idx_data_freshness_area
ON data_freshness(area_id);





-- FIXING
ALTER TABLE osm_gtfs_stop_matches
ADD CONSTRAINT osm_gtfs_stop_matches_unique
UNIQUE (osm_stop_id, feed_id, gtfs_stop_id);

-- add network type
ALTER TABLE osm_edges
ADD COLUMN IF NOT EXISTS network_type text DEFAULT 'drive';

ALTER TABLE osm_nodes
ADD COLUMN IF NOT EXISTS network_type text DEFAULT 'drive';

ALTER TABLE osm_nodes
DROP CONSTRAINT IF EXISTS osm_nodes_pkey;

ALTER TABLE osm_nodes
ADD CONSTRAINT osm_nodes_pkey
PRIMARY KEY (osm_node_id, network_type);

ALTER TABLE osm_edges
ADD CONSTRAINT osm_edges_unique_graph_edge
UNIQUE (region_id, network_type, u, v, key);

CREATE INDEX IF NOT EXISTS idx_osm_edges_region_network
ON osm_edges(region_id, network_type);

CREATE INDEX IF NOT EXISTS idx_osm_nodes_region_network
ON osm_nodes(region_id, network_type);