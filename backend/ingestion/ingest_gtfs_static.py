import io
import os
import zipfile
import requests
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)

GTFS_STATIC_URL = "https://api.data.gov.my/gtfs-static/prasarana?category=rapid-bus-kl"
FEED_ID = "prasarana_rapid_bus_kl"


def download_gtfs_zip(url: str) -> dict[str, pd.DataFrame]:
    response = requests.get(url, timeout=60)
    response.raise_for_status()

    tables = {}

    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        for filename in z.namelist():
            if filename.endswith(".txt"):
                table_name = filename.replace(".txt", "")
                with z.open(filename) as f:
                    tables[table_name] = pd.read_csv(f)

    return tables


def insert_feed_record():
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO gtfs_feeds (
                    feed_id, agency, category, source_url, downloaded_at, active
                )
                VALUES (
                    :feed_id, :agency, :category, :source_url, NOW(), TRUE
                )
                ON CONFLICT (feed_id)
                DO UPDATE SET
                    source_url = EXCLUDED.source_url,
                    downloaded_at = NOW(),
                    active = TRUE;
            """),
            {
                "feed_id": FEED_ID,
                "agency": "prasarana",
                "category": "rapid-bus-kl",
                "source_url": GTFS_STATIC_URL,
            },
        )


def safe_col(df: pd.DataFrame, col: str, default=None):
    if col not in df.columns:
        df[col] = default
    return df


def insert_gtfs_stops(stops: pd.DataFrame):
    if stops.empty:
        return

    stops = stops.copy()
    stops["feed_id"] = FEED_ID

    for col in [
        "stop_code", "stop_desc", "zone_id", "stop_url",
        "location_type", "parent_station", "wheelchair_boarding"
    ]:
        safe_col(stops, col)

    rows = stops.to_dict("records")

    with engine.begin() as conn:
        for r in rows:
            conn.execute(
                text("""
                    INSERT INTO gtfs_stops (
                        feed_id, stop_id, stop_code, stop_name, stop_desc,
                        stop_lat, stop_lon, zone_id_raw, stop_url,
                        location_type, parent_station, wheelchair_boarding,
                        geom
                    )
                    VALUES (
                        :feed_id, :stop_id, :stop_code, :stop_name, :stop_desc,
                        :stop_lat, :stop_lon, :zone_id, :stop_url,
                        :location_type, :parent_station, :wheelchair_boarding,
                        ST_SetSRID(ST_MakePoint(:stop_lon, :stop_lat), 4326)
                    )
                    ON CONFLICT (feed_id, stop_id)
                    DO UPDATE SET
                        stop_name = EXCLUDED.stop_name,
                        stop_lat = EXCLUDED.stop_lat,
                        stop_lon = EXCLUDED.stop_lon,
                        geom = EXCLUDED.geom;
                """),
                r,
            )


def insert_gtfs_routes(routes: pd.DataFrame):
    if routes.empty:
        return

    routes = routes.copy()
    routes["feed_id"] = FEED_ID

    for col in [
        "agency_id", "route_short_name", "route_long_name",
        "route_desc", "route_url", "route_color", "route_text_color"
    ]:
        safe_col(routes, col)

    rows = routes.to_dict("records")

    with engine.begin() as conn:
        for r in rows:
            conn.execute(
                text("""
                    INSERT INTO gtfs_routes (
                        feed_id, route_id, agency_id, route_short_name,
                        route_long_name, route_desc, route_type,
                        route_url, route_color, route_text_color
                    )
                    VALUES (
                        :feed_id, :route_id, :agency_id, :route_short_name,
                        :route_long_name, :route_desc, :route_type,
                        :route_url, :route_color, :route_text_color
                    )
                    ON CONFLICT (feed_id, route_id)
                    DO UPDATE SET
                        route_short_name = EXCLUDED.route_short_name,
                        route_long_name = EXCLUDED.route_long_name,
                        route_type = EXCLUDED.route_type;
                """),
                r,
            )


def insert_gtfs_trips(trips: pd.DataFrame):
    if trips.empty:
        return

    trips = trips.copy()
    trips["feed_id"] = FEED_ID

    for col in [
        "trip_headsign", "trip_short_name", "direction_id",
        "block_id", "shape_id", "wheelchair_accessible", "bikes_allowed"
    ]:
        safe_col(trips, col)

    rows = trips.to_dict("records")

    with engine.begin() as conn:
        for r in rows:
            conn.execute(
                text("""
                    INSERT INTO gtfs_trips (
                        feed_id, route_id, service_id, trip_id,
                        trip_headsign, trip_short_name, direction_id,
                        block_id, shape_id, wheelchair_accessible, bikes_allowed
                    )
                    VALUES (
                        :feed_id, :route_id, :service_id, :trip_id,
                        :trip_headsign, :trip_short_name, :direction_id,
                        :block_id, :shape_id, :wheelchair_accessible, :bikes_allowed
                    )
                    ON CONFLICT (feed_id, trip_id)
                    DO UPDATE SET
                        route_id = EXCLUDED.route_id,
                        service_id = EXCLUDED.service_id,
                        shape_id = EXCLUDED.shape_id;
                """),
                r,
            )


def insert_gtfs_stop_times(stop_times: pd.DataFrame, chunksize: int = 5000):
    if stop_times.empty:
        return

    stop_times = stop_times.copy()
    stop_times["feed_id"] = FEED_ID

    for col in [
        "stop_headsign",
        "pickup_type",
        "drop_off_type",
        "shape_dist_traveled",
        "timepoint"
    ]:
        safe_col(stop_times, col)

    columns = [
        "feed_id",
        "trip_id",
        "arrival_time",
        "departure_time",
        "stop_id",
        "stop_sequence",
        "stop_headsign",
        "pickup_type",
        "drop_off_type",
        "shape_dist_traveled",
        "timepoint"
    ]

    stop_times = stop_times[columns]

    # Clean numeric columns before inserting into staging table
    int_cols = ["stop_sequence", "pickup_type", "drop_off_type", "timepoint"]
    float_cols = ["shape_dist_traveled"]

    for col in int_cols:
        stop_times[col] = pd.to_numeric(stop_times[col], errors="coerce").astype("Int64")

    for col in float_cols:
        stop_times[col] = pd.to_numeric(stop_times[col], errors="coerce")

    # Drop old staging table if it exists
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS gtfs_stop_times_stage;"))

    # Insert into temporary staging table
    stop_times.to_sql(
        "gtfs_stop_times_stage",
        engine,
        if_exists="replace",
        index=False,
        chunksize=chunksize,
        method="multi",
    )

    # Move from staging table into real table
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO gtfs_stop_times (
                feed_id, trip_id, arrival_time, departure_time,
                stop_id, stop_sequence, stop_headsign, pickup_type,
                drop_off_type, shape_dist_traveled, timepoint
            )
            SELECT
                feed_id,
                trip_id,
                arrival_time,
                departure_time,
                stop_id,
                stop_sequence::INTEGER,
                stop_headsign,
                pickup_type::INTEGER,
                drop_off_type::INTEGER,
                shape_dist_traveled::DOUBLE PRECISION,
                timepoint::INTEGER
            FROM gtfs_stop_times_stage
            ON CONFLICT (feed_id, trip_id, stop_sequence)
            DO UPDATE SET
                arrival_time = EXCLUDED.arrival_time,
                departure_time = EXCLUDED.departure_time,
                stop_id = EXCLUDED.stop_id,
                pickup_type = EXCLUDED.pickup_type,
                drop_off_type = EXCLUDED.drop_off_type,
                shape_dist_traveled = EXCLUDED.shape_dist_traveled,
                timepoint = EXCLUDED.timepoint;
        """))

        conn.execute(text("DROP TABLE IF EXISTS gtfs_stop_times_stage;"))


def main():
    print("Downloading GTFS Static...")
    tables = download_gtfs_zip(GTFS_STATIC_URL)

    print("Inserting feed record...")
    insert_feed_record()

    print("Inserting stops...")
    insert_gtfs_stops(tables.get("stops", pd.DataFrame()))

    print("Inserting routes...")
    insert_gtfs_routes(tables.get("routes", pd.DataFrame()))

    print("Inserting trips...")
    insert_gtfs_trips(tables.get("trips", pd.DataFrame()))

    print("Inserting stop_times...")
    insert_gtfs_stop_times(tables.get("stop_times", pd.DataFrame()))

    print("Done.")


if __name__ == "__main__":
    main()