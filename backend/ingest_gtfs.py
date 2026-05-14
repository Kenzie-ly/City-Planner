from __future__ import annotations

import argparse
import io
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from sqlalchemy import text

from ingestion_utils import (
    DEFAULT_HEADERS, GTFS_FEEDS, RunCounter, date_yyyymmdd_series, engine,
    ensure_cols, finish_ingestion_run, log, normalize_df,
    start_ingestion_run, temp_stage, to_float_series, to_int_series,
    update_freshness
)


def download_gtfs_zip(url: str) -> tuple[dict[str, pd.DataFrame], bytes]:
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=90)
    response.raise_for_status()
    content = response.content
    tables: dict[str, pd.DataFrame] = {}
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        for filename in z.namelist():
            if filename.endswith(".txt"):
                table_name = Path(filename).stem
                with z.open(filename) as f:
                    tables[table_name] = normalize_df(
                        pd.read_csv(f, encoding="utf-8-sig", encoding_errors="replace")
                    )
    return tables, content


def save_raw_zip(feed_id: str, content: bytes) -> str:
    raw_dir = Path("data/raw_gtfs")
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{feed_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.zip"
    path.write_bytes(content)
    return str(path)


def upsert_gtfs_feed(feed: dict[str, str], raw_zip_path: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO gtfs_feeds (feed_id, agency, category, source_url, downloaded_at, raw_zip_path, active)
            VALUES (:feed_id, :agency, :category, :source_url, NOW(), :raw_zip_path, TRUE)
            ON CONFLICT (feed_id)
            DO UPDATE SET
                agency = EXCLUDED.agency,
                category = EXCLUDED.category,
                source_url = EXCLUDED.source_url,
                downloaded_at = NOW(),
                raw_zip_path = EXCLUDED.raw_zip_path,
                active = TRUE;
        """), {**feed, "raw_zip_path": raw_zip_path})


def upsert_gtfs_agency(feed_id: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    df = ensure_cols(df, ["agency_id", "agency_name", "agency_url", "agency_timezone", "agency_lang", "agency_phone", "agency_fare_url"])
    if df["agency_id"].isna().all():
        df["agency_id"] = "default"
    df["feed_id"] = feed_id
    df = df[["feed_id", "agency_id", "agency_name", "agency_url", "agency_timezone", "agency_lang", "agency_phone", "agency_fare_url"]]
    temp_stage(df, "stage_gtfs_agency")
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO gtfs_agency
            SELECT * FROM stage_gtfs_agency
            ON CONFLICT (feed_id, agency_id)
            DO UPDATE SET
                agency_name = EXCLUDED.agency_name,
                agency_url = EXCLUDED.agency_url,
                agency_timezone = EXCLUDED.agency_timezone,
                agency_lang = EXCLUDED.agency_lang,
                agency_phone = EXCLUDED.agency_phone,
                agency_fare_url = EXCLUDED.agency_fare_url;
            DROP TABLE stage_gtfs_agency;
        """))
    return len(df)


def upsert_gtfs_stops(feed_id: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    cols = ["stop_id", "stop_code", "stop_name", "stop_desc", "stop_lat", "stop_lon", "zone_id", "stop_url", "location_type", "parent_station", "wheelchair_boarding"]
    df = ensure_cols(df, cols)
    df["feed_id"] = feed_id
    df["stop_lat"] = to_float_series(df["stop_lat"])
    df["stop_lon"] = to_float_series(df["stop_lon"])
    for col in ["location_type", "wheelchair_boarding"]:
        df[col] = to_int_series(df[col])
    df = df[df["stop_id"].notna() & df["stop_lat"].notna() & df["stop_lon"].notna()]
    df = df[["feed_id", "stop_id", "stop_code", "stop_name", "stop_desc", "stop_lat", "stop_lon", "zone_id", "stop_url", "location_type", "parent_station", "wheelchair_boarding"]]
    temp_stage(df, "stage_gtfs_stops")
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO gtfs_stops (
                feed_id, stop_id, stop_code, stop_name, stop_desc, stop_lat, stop_lon,
                zone_id_raw, stop_url, location_type, parent_station, wheelchair_boarding, geom
            )
            SELECT
                feed_id, stop_id, stop_code, stop_name, stop_desc, stop_lat, stop_lon,
                zone_id, stop_url, location_type::integer, parent_station, wheelchair_boarding::integer,
                ST_SetSRID(ST_MakePoint(stop_lon, stop_lat), 4326)
            FROM stage_gtfs_stops
            ON CONFLICT (feed_id, stop_id)
            DO UPDATE SET
                stop_code = EXCLUDED.stop_code,
                stop_name = EXCLUDED.stop_name,
                stop_desc = EXCLUDED.stop_desc,
                stop_lat = EXCLUDED.stop_lat,
                stop_lon = EXCLUDED.stop_lon,
                zone_id_raw = EXCLUDED.zone_id_raw,
                stop_url = EXCLUDED.stop_url,
                location_type = EXCLUDED.location_type,
                parent_station = EXCLUDED.parent_station,
                wheelchair_boarding = EXCLUDED.wheelchair_boarding,
                geom = EXCLUDED.geom;
            DROP TABLE stage_gtfs_stops;
        """))
    return len(df)


def upsert_gtfs_routes(feed_id: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    cols = ["route_id", "agency_id", "route_short_name", "route_long_name", "route_desc", "route_type", "route_url", "route_color", "route_text_color"]
    df = ensure_cols(df, cols)
    df["feed_id"] = feed_id
    df["route_type"] = to_int_series(df["route_type"])
    df = df[df["route_id"].notna()]
    df = df[["feed_id"] + cols]
    temp_stage(df, "stage_gtfs_routes")
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO gtfs_routes
            SELECT feed_id, route_id, agency_id, route_short_name, route_long_name, route_desc,
                   route_type::integer, route_url, route_color, route_text_color
            FROM stage_gtfs_routes
            ON CONFLICT (feed_id, route_id)
            DO UPDATE SET
                agency_id = EXCLUDED.agency_id,
                route_short_name = EXCLUDED.route_short_name,
                route_long_name = EXCLUDED.route_long_name,
                route_desc = EXCLUDED.route_desc,
                route_type = EXCLUDED.route_type,
                route_url = EXCLUDED.route_url,
                route_color = EXCLUDED.route_color,
                route_text_color = EXCLUDED.route_text_color;
            DROP TABLE stage_gtfs_routes;
        """))
    return len(df)


def upsert_gtfs_trips(feed_id: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    cols = ["route_id", "service_id", "trip_id", "trip_headsign", "trip_short_name", "direction_id", "block_id", "shape_id", "wheelchair_accessible", "bikes_allowed"]
    df = ensure_cols(df, cols)
    df["feed_id"] = feed_id
    for col in ["direction_id", "wheelchair_accessible", "bikes_allowed"]:
        df[col] = to_int_series(df[col])
    df = df[df["trip_id"].notna()]
    df = df[["feed_id"] + cols]
    temp_stage(df, "stage_gtfs_trips")
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO gtfs_trips
            SELECT feed_id, route_id, service_id, trip_id, trip_headsign, trip_short_name,
                   direction_id::integer, block_id, shape_id, wheelchair_accessible::integer, bikes_allowed::integer
            FROM stage_gtfs_trips
            ON CONFLICT (feed_id, trip_id)
            DO UPDATE SET
                route_id = EXCLUDED.route_id,
                service_id = EXCLUDED.service_id,
                trip_headsign = EXCLUDED.trip_headsign,
                trip_short_name = EXCLUDED.trip_short_name,
                direction_id = EXCLUDED.direction_id,
                block_id = EXCLUDED.block_id,
                shape_id = EXCLUDED.shape_id,
                wheelchair_accessible = EXCLUDED.wheelchair_accessible,
                bikes_allowed = EXCLUDED.bikes_allowed;
            DROP TABLE stage_gtfs_trips;
        """))
    return len(df)


def upsert_gtfs_calendar(feed_id: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    cols = ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "start_date", "end_date"]
    df = ensure_cols(df, cols)
    df["feed_id"] = feed_id
    for col in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]:
        df[col] = to_int_series(df[col])
    df["start_date"] = date_yyyymmdd_series(df["start_date"])
    df["end_date"] = date_yyyymmdd_series(df["end_date"])
    df = df[df["service_id"].notna()]
    df = df[["feed_id"] + cols]
    temp_stage(df, "stage_gtfs_calendar")
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO gtfs_calendar
            SELECT feed_id, service_id, monday::integer, tuesday::integer, wednesday::integer,
                   thursday::integer, friday::integer, saturday::integer, sunday::integer,
                   start_date::date, end_date::date
            FROM stage_gtfs_calendar
            ON CONFLICT (feed_id, service_id)
            DO UPDATE SET
                monday = EXCLUDED.monday, tuesday = EXCLUDED.tuesday, wednesday = EXCLUDED.wednesday,
                thursday = EXCLUDED.thursday, friday = EXCLUDED.friday, saturday = EXCLUDED.saturday,
                sunday = EXCLUDED.sunday, start_date = EXCLUDED.start_date, end_date = EXCLUDED.end_date;
            DROP TABLE stage_gtfs_calendar;
        """))
    return len(df)


def upsert_gtfs_calendar_dates(feed_id: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    cols = ["service_id", "date", "exception_type"]
    df = ensure_cols(df, cols)
    df["feed_id"] = feed_id
    df["date"] = date_yyyymmdd_series(df["date"])
    df["exception_type"] = to_int_series(df["exception_type"])
    df = df[df["service_id"].notna() & df["date"].notna()]
    df = df[["feed_id"] + cols]
    temp_stage(df, "stage_gtfs_calendar_dates")
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO gtfs_calendar_dates
            SELECT feed_id, service_id, date::date, exception_type::integer
            FROM stage_gtfs_calendar_dates
            ON CONFLICT (feed_id, service_id, date)
            DO UPDATE SET exception_type = EXCLUDED.exception_type;
            DROP TABLE stage_gtfs_calendar_dates;
        """))
    return len(df)


def upsert_gtfs_stop_times(feed_id: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    cols = ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence", "stop_headsign", "pickup_type", "drop_off_type", "shape_dist_traveled", "timepoint"]
    df = ensure_cols(df, cols)
    df["feed_id"] = feed_id
    for col in ["stop_sequence", "pickup_type", "drop_off_type", "timepoint"]:
        df[col] = to_int_series(df[col])
    df["shape_dist_traveled"] = to_float_series(df["shape_dist_traveled"])
    df = df[df["trip_id"].notna() & df["stop_id"].notna() & df["stop_sequence"].notna()]
    df = df[["feed_id"] + cols]
    temp_stage(df, "stage_gtfs_stop_times")
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO gtfs_stop_times
            SELECT feed_id, trip_id, arrival_time, departure_time, stop_id, stop_sequence::integer,
                   stop_headsign, pickup_type::integer, drop_off_type::integer,
                   shape_dist_traveled::double precision, timepoint::integer
            FROM stage_gtfs_stop_times
            ON CONFLICT (feed_id, trip_id, stop_sequence)
            DO UPDATE SET
                arrival_time = EXCLUDED.arrival_time,
                departure_time = EXCLUDED.departure_time,
                stop_id = EXCLUDED.stop_id,
                stop_headsign = EXCLUDED.stop_headsign,
                pickup_type = EXCLUDED.pickup_type,
                drop_off_type = EXCLUDED.drop_off_type,
                shape_dist_traveled = EXCLUDED.shape_dist_traveled,
                timepoint = EXCLUDED.timepoint;
            DROP TABLE stage_gtfs_stop_times;
        """))
    return len(df)


def upsert_gtfs_shapes(feed_id: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    cols = ["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence", "shape_dist_traveled"]
    df = ensure_cols(df, cols)
    df["feed_id"] = feed_id
    df["shape_pt_lat"] = to_float_series(df["shape_pt_lat"])
    df["shape_pt_lon"] = to_float_series(df["shape_pt_lon"])
    df["shape_pt_sequence"] = to_int_series(df["shape_pt_sequence"])
    df["shape_dist_traveled"] = to_float_series(df["shape_dist_traveled"])
    df = df[df["shape_id"].notna() & df["shape_pt_lat"].notna() & df["shape_pt_lon"].notna() & df["shape_pt_sequence"].notna()]
    df = df[["feed_id"] + cols]
    temp_stage(df, "stage_gtfs_shapes")
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO gtfs_shapes (feed_id, shape_id, shape_pt_lat, shape_pt_lon, shape_pt_sequence, shape_dist_traveled, geom)
            SELECT feed_id, shape_id, shape_pt_lat, shape_pt_lon, shape_pt_sequence::integer,
                   shape_dist_traveled::double precision,
                   ST_SetSRID(ST_MakePoint(shape_pt_lon, shape_pt_lat), 4326)
            FROM stage_gtfs_shapes
            ON CONFLICT (feed_id, shape_id, shape_pt_sequence)
            DO UPDATE SET
                shape_pt_lat = EXCLUDED.shape_pt_lat,
                shape_pt_lon = EXCLUDED.shape_pt_lon,
                shape_dist_traveled = EXCLUDED.shape_dist_traveled,
                geom = EXCLUDED.geom;
            DROP TABLE stage_gtfs_shapes;
        """))
    build_gtfs_shape_lines(feed_id)
    return len(df)


def build_gtfs_shape_lines(feed_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO gtfs_shape_lines (feed_id, shape_id, geom)
            SELECT feed_id, shape_id, ST_MakeLine(geom ORDER BY shape_pt_sequence)::geometry(LineString,4326)
            FROM gtfs_shapes
            WHERE feed_id = :feed_id
            GROUP BY feed_id, shape_id
            HAVING COUNT(*) >= 2
            ON CONFLICT (feed_id, shape_id)
            DO UPDATE SET geom = EXCLUDED.geom;
        """), {"feed_id": feed_id})


def ingest_gtfs_static(feed_key: str) -> None:
    if feed_key not in GTFS_FEEDS:
        raise ValueError(f"Unknown GTFS feed key: {feed_key}. Available: {list(GTFS_FEEDS)}")

    feed = GTFS_FEEDS[feed_key]
    run_id = start_ingestion_run("gtfs_static", feed["feed_id"])
    counter = RunCounter()
    try:
        log.info("Downloading GTFS feed %s", feed["feed_id"])
        tables, content = download_gtfs_zip(feed["source_url"])
        raw_zip_path = save_raw_zip(feed["feed_id"], content)
        upsert_gtfs_feed(feed, raw_zip_path)

        counter.inserted += upsert_gtfs_agency(feed["feed_id"], tables.get("agency", pd.DataFrame()))
        counter.inserted += upsert_gtfs_stops(feed["feed_id"], tables.get("stops", pd.DataFrame()))
        counter.inserted += upsert_gtfs_routes(feed["feed_id"], tables.get("routes", pd.DataFrame()))
        counter.inserted += upsert_gtfs_trips(feed["feed_id"], tables.get("trips", pd.DataFrame()))
        counter.inserted += upsert_gtfs_calendar(feed["feed_id"], tables.get("calendar", pd.DataFrame()))
        counter.inserted += upsert_gtfs_calendar_dates(feed["feed_id"], tables.get("calendar_dates", pd.DataFrame()))
        counter.inserted += upsert_gtfs_stop_times(feed["feed_id"], tables.get("stop_times", pd.DataFrame()))
        counter.inserted += upsert_gtfs_shapes(feed["feed_id"], tables.get("shapes", pd.DataFrame()))

        update_freshness("gtfs_static", feed["feed_id"], status="fresh", notes="GTFS static feed loaded", next_days=14)
        finish_ingestion_run(run_id, "success", counter)
        log.info("GTFS ingestion complete: %s rows processed", counter.inserted)
    except Exception as exc:
        finish_ingestion_run(run_id, "failed", counter, str(exc))
        raise

def main() -> None:
    parser = argparse.ArgumentParser(description="Production ingestion for GTFS")
    parser.add_argument("--gtfs", choices=list(GTFS_FEEDS.keys()), required=True, help="GTFS feed key to ingest")
    args = parser.parse_args()

    ingest_gtfs_static(args.gtfs)


if __name__ == "__main__":
    main()
