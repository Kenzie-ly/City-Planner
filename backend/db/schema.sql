
Table "agent_runs" {
  "agent_run_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "agent_name" text [not null]
  "area_id" text
  "evidence_pack_id" uuid
  "input_json" jsonb
  "output_json" jsonb
  "status" text [default: `'success'::text`]
  "error_message" text
  "started_at" timestamp [default: `now()`]
  "completed_at" timestamp

  Indexes {
    agent_name [type: btree, name: "idx_agent_runs_agent"]
    area_id [type: btree, name: "idx_agent_runs_area"]
  }
}

Table "area_gtfs_routes" {
  "area_id" text [not null]
  "feed_id" text [not null]
  "route_id" text [not null]
  "stops_in_area" integer
  "trips_in_area" integer

  Indexes {
    (area_id, feed_id, route_id) [pk, name: "area_gtfs_routes_pkey"]
    area_id [type: btree, name: "idx_area_gtfs_routes_area"]
    (feed_id, route_id) [type: btree, name: "idx_area_gtfs_routes_route"]
  }
}

Table "area_gtfs_stops" {
  "area_id" text [not null]
  "feed_id" text [not null]
  "stop_id" text [not null]
  "distance_to_area_center_m" doubleprecision
  "inside_area" boolean [default: true]

  Indexes {
    (area_id, feed_id, stop_id) [pk, name: "area_gtfs_stops_pkey"]
    area_id [type: btree, name: "idx_area_gtfs_stops_area"]
    (feed_id, stop_id) [type: btree, name: "idx_area_gtfs_stops_stop"]
  }
}

Table "areas" {
  "area_id" text [pk, not null]
  "region_id" text
  "area_name" text [not null]
  "area_type" text
  "source" text
  "geom" public.geometry
  "centroid" public.geometry
  "created_at" timestamp [default: `now()`]
  "updated_at" timestamp [default: `now()`]

  Indexes {
    centroid [type: gist, name: "idx_areas_centroid"]
    geom [type: gist, name: "idx_areas_geom"]
    region_id [type: btree, name: "idx_areas_region"]
  }
}

Table "broad_challenge_cards" {
  "card_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "agent_run_id" uuid
  "problem_direction_id" uuid
  "area_id" text
  "challenge_type" text
  "title" text
  "description" text
  "confidence_tier" text
  "evidence_refs" jsonb
  "display_order" integer
  "created_at" timestamp [default: `now()`]

  Indexes {
    area_id [type: btree, name: "idx_broad_cards_area"]
  }
}

Table "candidate_hotspots" {
  "hotspot_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "area_id" text
  "zone_id" text
  "challenge_type" text [not null]
  "hotspot_name" text
  "hotspot_type" text
  "score" doubleprecision
  "confidence_tier" text
  "related_routes" jsonb
  "related_stops" jsonb
  "related_stations" jsonb
  "evidence_refs" jsonb
  "geometry_ref" text
  "geom" public.geometry
  "map_ready" boolean [default: false]
  "solution_ready" boolean [default: false]
  "generated_at" timestamp [default: `now()`]

  Indexes {
    area_id [type: btree, name: "idx_hotspots_area"]
    challenge_type [type: btree, name: "idx_hotspots_challenge"]
    geom [type: gist, name: "idx_hotspots_geom"]
    score [type: btree, name: "idx_hotspots_score"]
  }
}

Table "candidate_problem_directions" {
  "problem_direction_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "area_id" text
  "challenge_type" text [not null]
  "title" text
  "reason_hint" text
  "evidence_score" doubleprecision
  "confidence_tier" text
  "evidence_refs" jsonb
  "enabled" boolean [default: true]
  "generated_at" timestamp [default: `now()`]

  Indexes {
    area_id [type: btree, name: "idx_problem_directions_area"]
    evidence_score [type: btree, name: "idx_problem_directions_score"]
    challenge_type [type: btree, name: "idx_problem_directions_type"]
  }
}

Table "data_freshness" {
  "freshness_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "source_type" text
  "source_name" text
  "region_id" text
  "area_id" text
  "last_successful_update" timestamp
  "next_scheduled_update" timestamp
  "freshness_status" text
  "notes" text

  Indexes {
    area_id [type: btree, name: "idx_data_freshness_area"]
    source_type [type: btree, name: "idx_data_freshness_source"]
  }
}

Table "demand_proxy_summary" {
  "summary_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "area_id" text
  "zone_id" text
  "school_count" integer [default: 0]
  "hospital_count" integer [default: 0]
  "mall_count" integer [default: 0]
  "office_count" integer [default: 0]
  "university_count" integer [default: 0]
  "residential_building_count" integer [default: 0]
  "commercial_poi_count" integer [default: 0]
  "demand_proxy_score" doubleprecision
  "confidence_tier" text
  "generated_at" timestamp [default: `now()`]

  Indexes {
    area_id [type: btree, name: "idx_demand_proxy_area"]
    zone_id [type: btree, name: "idx_demand_proxy_zone"]
  }
}

Table "document_chunks" {
  "chunk_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "doc_id" uuid
  "chunk_index" integer
  "chunk_text" text [not null]
  "area_tags" "text[]"
  "topic_tags" "text[]"
  "station_tags" "text[]"
  "route_tags" "text[]"
  "challenge_type_tags" "text[]"
  "embedding" public.vector
  "metadata" jsonb
  "created_at" timestamp [default: `now()`]

  Indexes {
    area_tags [type: gin, name: "idx_document_chunks_area_tags"]
    challenge_type_tags [type: gin, name: "idx_document_chunks_challenge_tags"]
    doc_id [type: btree, name: "idx_document_chunks_doc"]
    topic_tags [type: gin, name: "idx_document_chunks_topic_tags"]
  }
}

Table "documents" {
  "doc_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "title" text
  "source_url" text
  "source_type" text
  "publisher" text
  "published_date" date
  "fetched_at" timestamp [default: `now()`]
  "raw_text_path" text
  "metadata" jsonb

  Indexes {
    published_date [type: btree, name: "idx_documents_published_date"]
    source_type [type: btree, name: "idx_documents_source_type"]
  }
}

Table "evidence_links" {
  "link_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "chunk_id" uuid
  "area_id" text
  "zone_id" text
  "feed_id" text
  "route_id" text
  "stop_id" text
  "station_name" text
  "challenge_type" text
  "link_type" text
  "relevance_score" doubleprecision
  "alignment_score" doubleprecision
  "created_at" timestamp [default: `now()`]

  Indexes {
    area_id [type: btree, name: "idx_evidence_links_area"]
    challenge_type [type: btree, name: "idx_evidence_links_challenge"]
    chunk_id [type: btree, name: "idx_evidence_links_chunk"]
    (feed_id, route_id) [type: btree, name: "idx_evidence_links_route"]
  }
}

Table "evidence_packs" {
  "evidence_pack_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "area_id" text
  "selected_challenge_type" text
  "selected_hotspot_id" uuid
  "pack_type" text
  "pack_json" jsonb [not null]
  "data_quality" jsonb
  "blocked_claims" jsonb
  "confidence_tier" text
  "created_at" timestamp [default: `now()`]

  Indexes {
    area_id [type: btree, name: "idx_evidence_packs_area"]
    selected_challenge_type [type: btree, name: "idx_evidence_packs_challenge"]
    pack_type [type: btree, name: "idx_evidence_packs_type"]
  }
}

Table "final_proposals" {
  "proposal_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "area_id" text
  "hotspot_id" uuid
  "agent_run_id" uuid
  "title" text
  "selected_problem" text
  "primary_recommendation" jsonb
  "supporting_recommendation" jsonb
  "implementation_phases" jsonb
  "evidence_used" jsonb
  "limitations" jsonb
  "confidence_tier" text
  "map_payload" jsonb
  "proposal_json" jsonb
  "created_at" timestamp [default: `now()`]

  Indexes {
    area_id [type: btree, name: "idx_final_proposals_area"]
    hotspot_id [type: btree, name: "idx_final_proposals_hotspot"]
  }
}

Table "gtfs_agency" {
  "feed_id" text [not null]
  "agency_id" text [not null]
  "agency_name" text
  "agency_url" text
  "agency_timezone" text
  "agency_lang" text
  "agency_phone" text
  "agency_fare_url" text

  Indexes {
    (feed_id, agency_id) [pk, name: "gtfs_agency_pkey"]
  }
}

Table "gtfs_calendar" {
  "feed_id" text [not null]
  "service_id" text [not null]
  "monday" integer
  "tuesday" integer
  "wednesday" integer
  "thursday" integer
  "friday" integer
  "saturday" integer
  "sunday" integer
  "start_date" date
  "end_date" date

  Indexes {
    (feed_id, service_id) [pk, name: "gtfs_calendar_pkey"]
  }
}

Table "gtfs_calendar_dates" {
  "feed_id" text [not null]
  "service_id" text [not null]
  "date" date [not null]
  "exception_type" integer

  Indexes {
    (feed_id, service_id, date) [pk, name: "gtfs_calendar_dates_pkey"]
  }
}

Table "gtfs_feeds" {
  "feed_id" text [pk, not null]
  "agency" text [not null]
  "category" text
  "source_url" text
  "downloaded_at" timestamp [default: `now()`]
  "valid_from" date
  "valid_to" date
  "raw_zip_path" text
  "active" boolean [default: true]
}

Table "gtfs_realtime_snapshots" {
  "snapshot_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "feed_id" text
  "agency" text
  "category" text
  "fetched_at" timestamp [default: `now()`]
  "raw_pb_path" text
  "status" text [default: `'success'::text`]
  "error_message" text
}

Table "gtfs_realtime_vehicle_positions" {
  "position_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "snapshot_id" uuid
  "feed_id" text
  "vehicle_id" text
  "trip_id" text
  "route_id" text
  "latitude" doubleprecision
  "longitude" doubleprecision
  "bearing" doubleprecision
  "speed" doubleprecision
  "current_stop_sequence" integer
  "current_status" text
  "timestamp_utc" timestamp
  "geom" public.geometry
  "created_at" timestamp [default: `now()`]

  Indexes {
    geom [type: gist, name: "idx_vehicle_positions_geom"]
    (feed_id, route_id) [type: btree, name: "idx_vehicle_positions_route"]
    timestamp_utc [type: btree, name: "idx_vehicle_positions_time"]
    (feed_id, trip_id) [type: btree, name: "idx_vehicle_positions_trip"]
  }
}

Table "gtfs_routes" {
  "feed_id" text [not null]
  "route_id" text [not null]
  "agency_id" text
  "route_short_name" text
  "route_long_name" text
  "route_desc" text
  "route_type" integer
  "route_url" text
  "route_color" text
  "route_text_color" text

  Indexes {
    (feed_id, route_id) [pk, name: "gtfs_routes_pkey"]
    route_short_name [type: btree, name: "idx_gtfs_routes_short_name"]
  }
}

Table "gtfs_shape_lines" {
  "feed_id" text [not null]
  "shape_id" text [not null]
  "geom" public.geometry

  Indexes {
    (feed_id, shape_id) [pk, name: "gtfs_shape_lines_pkey"]
    geom [type: gist, name: "idx_gtfs_shape_lines_geom"]
  }
}

Table "gtfs_shapes" {
  "feed_id" text [not null]
  "shape_id" text [not null]
  "shape_pt_lat" doubleprecision
  "shape_pt_lon" doubleprecision
  "shape_pt_sequence" integer [not null]
  "shape_dist_traveled" doubleprecision
  "geom" public.geometry

  Indexes {
    (feed_id, shape_id, shape_pt_sequence) [pk, name: "gtfs_shapes_pkey"]
    geom [type: gist, name: "idx_gtfs_shapes_geom"]
    (feed_id, shape_id) [type: btree, name: "idx_gtfs_shapes_shape"]
  }
}

Table "gtfs_stop_times" {
  "feed_id" text [not null]
  "trip_id" text [not null]
  "arrival_time" text
  "departure_time" text
  "stop_id" text
  "stop_sequence" integer [not null]
  "stop_headsign" text
  "pickup_type" integer
  "drop_off_type" integer
  "shape_dist_traveled" doubleprecision
  "timepoint" integer

  Indexes {
    (feed_id, trip_id, stop_sequence) [pk, name: "gtfs_stop_times_pkey"]
    (feed_id, stop_id) [type: btree, name: "idx_gtfs_stop_times_stop"]
    (feed_id, trip_id) [type: btree, name: "idx_gtfs_stop_times_trip"]
  }
}

Table "gtfs_stops" {
  "feed_id" text [not null]
  "stop_id" text [not null]
  "stop_code" text
  "stop_name" text
  "stop_desc" text
  "stop_lat" doubleprecision
  "stop_lon" doubleprecision
  "zone_id_raw" text
  "stop_url" text
  "location_type" integer
  "parent_station" text
  "wheelchair_boarding" integer
  "geom" public.geometry

  Indexes {
    (feed_id, stop_id) [pk, name: "gtfs_stops_pkey"]
    geom [type: gist, name: "idx_gtfs_stops_geom"]
    stop_name [type: btree, name: "idx_gtfs_stops_name"]
  }
}

Table "gtfs_trips" {
  "feed_id" text [not null]
  "route_id" text
  "service_id" text
  "trip_id" text [not null]
  "trip_headsign" text
  "trip_short_name" text
  "direction_id" integer
  "block_id" text
  "shape_id" text
  "wheelchair_accessible" integer
  "bikes_allowed" integer

  Indexes {
    (feed_id, trip_id) [pk, name: "gtfs_trips_pkey"]
    (feed_id, route_id) [type: btree, name: "idx_gtfs_trips_route"]
    (feed_id, shape_id) [type: btree, name: "idx_gtfs_trips_shape"]
  }
}

Table "ingestion_runs" {
  "ingestion_run_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "source_type" text
  "source_name" text
  "region_id" text
  "area_id" text
  "status" text [default: `'success'::text`]
  "records_inserted" integer [default: 0]
  "records_updated" integer [default: 0]
  "error_message" text
  "started_at" timestamp [default: `now()`]
  "completed_at" timestamp

  Indexes {
    region_id [type: btree, name: "idx_ingestion_runs_region"]
    source_type [type: btree, name: "idx_ingestion_runs_source"]
  }
}

Table "osm_edges" {
  "edge_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "region_id" text
  "network_type" text [not null, default: `'drive'::text`]
  "u" bigint
  "v" bigint
  "key" integer
  "osmid" text
  "highway" text
  "name" text
  "length_m" doubleprecision
  "one_way" boolean
  "maxspeed" text
  "geometry" public.geometry
  "tags" jsonb
  "created_at" timestamp [default: `now()`]

  Indexes {
    (region_id, network_type, u, v, key) [unique, name: "osm_edges_unique_network_edge"]
    geometry [type: gist, name: "idx_osm_edges_geom"]
    name [type: btree, name: "idx_osm_edges_name"]
    region_id [type: btree, name: "idx_osm_edges_region"]
  }
}

Table "osm_gtfs_stop_matches" {
  "match_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "osm_stop_id" text
  "feed_id" text
  "gtfs_stop_id" text
  "distance_m" doubleprecision
  "match_confidence" doubleprecision
  "created_at" timestamp [default: `now()`]

  Indexes {
    (osm_stop_id, feed_id, gtfs_stop_id) [unique, name: "osm_gtfs_stop_matches_unique"]
    (feed_id, gtfs_stop_id) [type: btree, name: "idx_osm_gtfs_stop_matches_gtfs"]
    osm_stop_id [type: btree, name: "idx_osm_gtfs_stop_matches_osm"]
  }
}

Table "osm_nodes" {
  "osm_node_id" bigint [not null]
  "region_id" text
  "network_type" text [not null, default: `'drive'::text`]
  "x" doubleprecision
  "y" doubleprecision
  "geom" public.geometry
  "tags" jsonb
  "created_at" timestamp [default: `now()`]

  Indexes {
    (osm_node_id, network_type) [pk, name: "osm_nodes_pkey"]
    geom [type: gist, name: "idx_osm_nodes_geom"]
    region_id [type: btree, name: "idx_osm_nodes_region"]
  }
}

Table "osm_pois" {
  "poi_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "area_id" text
  "region_id" text
  "osm_id" text
  "name" text
  "poi_category" text
  "amenity" text
  "shop" text
  "office" text
  "building" text
  "geom" public.geometry
  "tags" jsonb
  "created_at" timestamp [default: `now()`]

  Indexes {
    area_id [type: btree, name: "idx_osm_pois_area"]
    poi_category [type: btree, name: "idx_osm_pois_category"]
    geom [type: gist, name: "idx_osm_pois_geom"]
    region_id [type: btree, name: "idx_osm_pois_region"]
  }
}

Table "osm_transit_stops" {
  "osm_stop_id" text [pk, not null]
  "area_id" text
  "region_id" text
  "stop_name" text
  "stop_type" text
  "osm_id" text
  "source_tag" text
  "geom" public.geometry
  "tags" jsonb
  "created_at" timestamp [default: `now()`]

  Indexes {
    area_id [type: btree, name: "idx_osm_transit_stops_area"]
    geom [type: gist, name: "idx_osm_transit_stops_geom"]
    region_id [type: btree, name: "idx_osm_transit_stops_region"]
  }
}

Table "realtime_reliability_summary" {
  "summary_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "area_id" text
  "zone_id" text
  "feed_id" text
  "route_id" text
  "observation_start" timestamp
  "observation_end" timestamp
  "vehicle_count" integer
  "median_vehicle_gap_min" doubleprecision
  "gap_variability_score" doubleprecision
  "possible_bunching" boolean
  "reliability_score" doubleprecision
  "confidence_tier" text
  "generated_at" timestamp [default: `now()`]

  Indexes {
    area_id [type: btree, name: "idx_reliability_area"]
    (feed_id, route_id) [type: btree, name: "idx_reliability_route"]
  }
}

Table "regions" {
  "region_id" text [pk, not null]
  "region_name" text [not null]
  "country" text [default: `'Malaysia'::text`]
  "graphml_path" text
  "geom" public.geometry
  "created_at" timestamp [default: `now()`]
  "updated_at" timestamp [default: `now()`]

  Indexes {
    geom [type: gist, name: "idx_regions_geom"]
  }
}

Table "reviewed_solutions" {
  "review_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "solution_id" uuid
  "agent_run_id" uuid
  "verdict" text
  "balanced_score" doubleprecision
  "impact_score" doubleprecision
  "feasibility_score" doubleprecision
  "cost_efficiency_score" doubleprecision
  "implementation_speed_score" doubleprecision
  "safety_environment_score" doubleprecision
  "reason" text
  "created_at" timestamp [default: `now()`]

  Indexes {
    solution_id [type: btree, name: "idx_reviewed_solutions_solution"]
  }
}

Table "route_headway_summary" {
  "summary_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "area_id" text
  "zone_id" text
  "feed_id" text
  "route_id" text
  "route_name" text
  "mode" text
  "stops_in_area" integer
  "trips_per_day" integer
  "trips_per_peak_hour" doubleprecision
  "median_headway_min" doubleprecision
  "peak_headway_min" doubleprecision
  "offpeak_headway_min" doubleprecision
  "first_service_time" text
  "last_service_time" text
  "evidence_score" doubleprecision
  "confidence_tier" text
  "generated_at" timestamp [default: `now()`]

  Indexes {
    area_id [type: btree, name: "idx_route_headway_area"]
    (feed_id, route_id) [type: btree, name: "idx_route_headway_route"]
    evidence_score [type: btree, name: "idx_route_headway_score"]
    zone_id [type: btree, name: "idx_route_headway_zone"]
  }
}

Table "solution_options" {
  "solution_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "hotspot_id" uuid
  "agent_run_id" uuid
  "solution_type" text
  "title" text
  "description" text
  "expected_benefit" text
  "tradeoffs" jsonb
  "evidence_refs" jsonb
  "created_at" timestamp [default: `now()`]

  Indexes {
    hotspot_id [type: btree, name: "idx_solution_options_hotspot"]
  }
}

Table "specific_hotspot_cards" {
  "card_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "agent_run_id" uuid
  "hotspot_id" uuid
  "area_id" text
  "challenge_type" text
  "title" text
  "description" text
  "confidence_tier" text
  "evidence_refs" jsonb
  "display_order" integer
  "created_at" timestamp [default: `now()`]

  Indexes {
    area_id [type: btree, name: "idx_specific_cards_area"]
  }
}

Table "user_selections" {
  "selection_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "session_id" uuid
  "selection_type" text
  "selected_id" text
  "selected_json" jsonb
  "created_at" timestamp [default: `now()`]

  Indexes {
    session_id [type: btree, name: "idx_user_selections_session"]
  }
}

Table "user_sessions" {
  "session_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "user_id" text
  "area_id" text
  "status" text [default: `'active'::text`]
  "created_at" timestamp [default: `now()`]
  "updated_at" timestamp [default: `now()`]
}

Table "zone_accessibility_summary" {
  "summary_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "area_id" text
  "zone_id" text
  "nearest_bus_stop_id" text
  "nearest_rail_station_id" text
  "median_walk_to_bus_stop_m" doubleprecision
  "median_walk_to_station_m" doubleprecision
  "pedestrian_connectivity_score" doubleprecision
  "walking_detour_ratio" doubleprecision
  "coverage_400m_score" doubleprecision
  "coverage_800m_score" doubleprecision
  "evidence_score" doubleprecision
  "confidence_tier" text
  "generated_at" timestamp [default: `now()`]

  Indexes {
    area_id [type: btree, name: "idx_zone_accessibility_area"]
    evidence_score [type: btree, name: "idx_zone_accessibility_score"]
    zone_id [type: btree, name: "idx_zone_accessibility_zone"]
  }
}

Table "zone_gtfs_stops" {
  "zone_id" text [not null]
  "feed_id" text [not null]
  "stop_id" text [not null]
  "inside_zone" boolean [default: true]

  Indexes {
    (zone_id, feed_id, stop_id) [pk, name: "zone_gtfs_stops_pkey"]
    zone_id [type: btree, name: "idx_zone_gtfs_stops_zone"]
  }
}

Table "zone_transit_coverage_summary" {
  "summary_id" uuid [pk, not null, default: `gen_random_uuid()`]
  "area_id" text
  "zone_id" text
  "bus_stop_count" integer
  "rail_station_count" integer
  "route_count" integer
  "high_frequency_route_count" integer
  "low_frequency_route_count" integer
  "transit_coverage_score" doubleprecision
  "evidence_score" doubleprecision
  "confidence_tier" text
  "generated_at" timestamp [default: `now()`]

  Indexes {
    area_id [type: btree, name: "idx_zone_coverage_area"]
    zone_id [type: btree, name: "idx_zone_coverage_zone"]
  }
}

Table "zones" {
  "zone_id" text [pk, not null]
  "area_id" text
  "zone_name" text
  "zone_type" text
  "geom" public.geometry
  "centroid" public.geometry
  "created_at" timestamp [default: `now()`]

  Indexes {
    area_id [type: btree, name: "idx_zones_area"]
    geom [type: gist, name: "idx_zones_geom"]
  }
}

Ref "agent_runs_area_id_fkey":"areas"."area_id" < "agent_runs"."area_id"

Ref "agent_runs_evidence_pack_id_fkey":"evidence_packs"."evidence_pack_id" < "agent_runs"."evidence_pack_id"

Ref "area_gtfs_routes_area_id_fkey":"areas"."area_id" < "area_gtfs_routes"."area_id"

Ref "area_gtfs_stops_area_id_fkey":"areas"."area_id" < "area_gtfs_stops"."area_id"

Ref "areas_region_id_fkey":"regions"."region_id" < "areas"."region_id"

Ref "broad_challenge_cards_agent_run_id_fkey":"agent_runs"."agent_run_id" < "broad_challenge_cards"."agent_run_id"

Ref "broad_challenge_cards_area_id_fkey":"areas"."area_id" < "broad_challenge_cards"."area_id"

Ref "broad_challenge_cards_problem_direction_id_fkey":"candidate_problem_directions"."problem_direction_id" < "broad_challenge_cards"."problem_direction_id"

Ref "candidate_hotspots_area_id_fkey":"areas"."area_id" < "candidate_hotspots"."area_id"

Ref "candidate_hotspots_zone_id_fkey":"zones"."zone_id" < "candidate_hotspots"."zone_id"

Ref "candidate_problem_directions_area_id_fkey":"areas"."area_id" < "candidate_problem_directions"."area_id"

Ref "data_freshness_area_id_fkey":"areas"."area_id" < "data_freshness"."area_id"

Ref "data_freshness_region_id_fkey":"regions"."region_id" < "data_freshness"."region_id"

Ref "demand_proxy_summary_area_id_fkey":"areas"."area_id" < "demand_proxy_summary"."area_id"

Ref "demand_proxy_summary_zone_id_fkey":"zones"."zone_id" < "demand_proxy_summary"."zone_id"

Ref "document_chunks_doc_id_fkey":"documents"."doc_id" < "document_chunks"."doc_id"

Ref "evidence_links_area_id_fkey":"areas"."area_id" < "evidence_links"."area_id"

Ref "evidence_links_chunk_id_fkey":"document_chunks"."chunk_id" < "evidence_links"."chunk_id"

Ref "evidence_links_zone_id_fkey":"zones"."zone_id" < "evidence_links"."zone_id"

Ref "evidence_packs_area_id_fkey":"areas"."area_id" < "evidence_packs"."area_id"

Ref "evidence_packs_selected_hotspot_id_fkey":"candidate_hotspots"."hotspot_id" < "evidence_packs"."selected_hotspot_id"

Ref "final_proposals_agent_run_id_fkey":"agent_runs"."agent_run_id" < "final_proposals"."agent_run_id"

Ref "final_proposals_area_id_fkey":"areas"."area_id" < "final_proposals"."area_id"

Ref "final_proposals_hotspot_id_fkey":"candidate_hotspots"."hotspot_id" < "final_proposals"."hotspot_id"

Ref "gtfs_agency_feed_id_fkey":"gtfs_feeds"."feed_id" < "gtfs_agency"."feed_id"

Ref "gtfs_calendar_dates_feed_id_fkey":"gtfs_feeds"."feed_id" < "gtfs_calendar_dates"."feed_id"

Ref "gtfs_calendar_feed_id_fkey":"gtfs_feeds"."feed_id" < "gtfs_calendar"."feed_id"

Ref "gtfs_realtime_snapshots_feed_id_fkey":"gtfs_feeds"."feed_id" < "gtfs_realtime_snapshots"."feed_id"

Ref "gtfs_realtime_vehicle_positions_feed_id_fkey":"gtfs_feeds"."feed_id" < "gtfs_realtime_vehicle_positions"."feed_id"

Ref "gtfs_realtime_vehicle_positions_snapshot_id_fkey":"gtfs_realtime_snapshots"."snapshot_id" < "gtfs_realtime_vehicle_positions"."snapshot_id"

Ref "gtfs_routes_feed_id_fkey":"gtfs_feeds"."feed_id" < "gtfs_routes"."feed_id"

Ref "gtfs_shape_lines_feed_id_fkey":"gtfs_feeds"."feed_id" < "gtfs_shape_lines"."feed_id"

Ref "gtfs_shapes_feed_id_fkey":"gtfs_feeds"."feed_id" < "gtfs_shapes"."feed_id"

Ref "gtfs_stop_times_feed_id_fkey":"gtfs_feeds"."feed_id" < "gtfs_stop_times"."feed_id"

Ref "gtfs_stops_feed_id_fkey":"gtfs_feeds"."feed_id" < "gtfs_stops"."feed_id"

Ref "gtfs_trips_feed_id_fkey":"gtfs_feeds"."feed_id" < "gtfs_trips"."feed_id"

Ref "ingestion_runs_area_id_fkey":"areas"."area_id" < "ingestion_runs"."area_id"

Ref "ingestion_runs_region_id_fkey":"regions"."region_id" < "ingestion_runs"."region_id"

Ref "osm_edges_region_id_fkey":"regions"."region_id" < "osm_edges"."region_id"

Ref "osm_gtfs_stop_matches_osm_stop_id_fkey":"osm_transit_stops"."osm_stop_id" < "osm_gtfs_stop_matches"."osm_stop_id"

Ref "osm_nodes_region_id_fkey":"regions"."region_id" < "osm_nodes"."region_id"

Ref "osm_pois_area_id_fkey":"areas"."area_id" < "osm_pois"."area_id"

Ref "osm_pois_region_id_fkey":"regions"."region_id" < "osm_pois"."region_id"

Ref "osm_transit_stops_area_id_fkey":"areas"."area_id" < "osm_transit_stops"."area_id"

Ref "osm_transit_stops_region_id_fkey":"regions"."region_id" < "osm_transit_stops"."region_id"

Ref "realtime_reliability_summary_area_id_fkey":"areas"."area_id" < "realtime_reliability_summary"."area_id"

Ref "realtime_reliability_summary_feed_id_fkey":"gtfs_feeds"."feed_id" < "realtime_reliability_summary"."feed_id"

Ref "realtime_reliability_summary_zone_id_fkey":"zones"."zone_id" < "realtime_reliability_summary"."zone_id"

Ref "reviewed_solutions_agent_run_id_fkey":"agent_runs"."agent_run_id" < "reviewed_solutions"."agent_run_id"

Ref "reviewed_solutions_solution_id_fkey":"solution_options"."solution_id" < "reviewed_solutions"."solution_id"

Ref "route_headway_summary_area_id_fkey":"areas"."area_id" < "route_headway_summary"."area_id"

Ref "route_headway_summary_feed_id_fkey":"gtfs_feeds"."feed_id" < "route_headway_summary"."feed_id"

Ref "route_headway_summary_zone_id_fkey":"zones"."zone_id" < "route_headway_summary"."zone_id"

Ref "solution_options_agent_run_id_fkey":"agent_runs"."agent_run_id" < "solution_options"."agent_run_id"

Ref "solution_options_hotspot_id_fkey":"candidate_hotspots"."hotspot_id" < "solution_options"."hotspot_id"

Ref "specific_hotspot_cards_agent_run_id_fkey":"agent_runs"."agent_run_id" < "specific_hotspot_cards"."agent_run_id"

Ref "specific_hotspot_cards_area_id_fkey":"areas"."area_id" < "specific_hotspot_cards"."area_id"

Ref "specific_hotspot_cards_hotspot_id_fkey":"candidate_hotspots"."hotspot_id" < "specific_hotspot_cards"."hotspot_id"

Ref "user_selections_session_id_fkey":"user_sessions"."session_id" < "user_selections"."session_id"

Ref "user_sessions_area_id_fkey":"areas"."area_id" < "user_sessions"."area_id"

Ref "zone_accessibility_summary_area_id_fkey":"areas"."area_id" < "zone_accessibility_summary"."area_id"

Ref "zone_accessibility_summary_zone_id_fkey":"zones"."zone_id" < "zone_accessibility_summary"."zone_id"

Ref "zone_gtfs_stops_zone_id_fkey":"zones"."zone_id" < "zone_gtfs_stops"."zone_id"

Ref "zone_transit_coverage_summary_area_id_fkey":"areas"."area_id" < "zone_transit_coverage_summary"."area_id"

Ref "zone_transit_coverage_summary_zone_id_fkey":"zones"."zone_id" < "zone_transit_coverage_summary"."zone_id"

Ref "zones_area_id_fkey":"areas"."area_id" < "zones"."area_id"
