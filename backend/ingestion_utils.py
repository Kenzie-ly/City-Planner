from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("transport_ingestion")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing. Add it to .env or your environment variables.")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=1800,
    connect_args={
        "connect_timeout": 30,
        "options": "-c statement_timeout=0"
    }
)

GTFS_FEEDS = {
    "rapid_bus_kl": {
        "feed_id": "prasarana_rapid_bus_kl",
        "agency": "prasarana",
        "category": "rapid-bus-kl",
        "source_url": "https://api.data.gov.my/gtfs-static/prasarana?category=rapid-bus-kl",
    },
    "rapid_bus_mrtfeeder": {
        "feed_id": "prasarana_rapid_bus_mrtfeeder",
        "agency": "prasarana",
        "category": "rapid-bus-mrtfeeder",
        "source_url": "https://api.data.gov.my/gtfs-static/prasarana?category=rapid-bus-mrtfeeder",
    },
    "rapid_rail_kl": {
        "feed_id": "prasarana_rapid_rail_kl",
        "agency": "prasarana",
        "category": "rapid-rail-kl",
        "source_url": "https://api.data.gov.my/gtfs-static/prasarana?category=rapid-rail-kl",
    },
    "ktmb": {
        "feed_id": "ktmb",
        "agency": "ktmb",
        "category": None,
        "source_url": "https://api.data.gov.my/gtfs-static/ktmb",
    },
    "mybas_johor": {
        "feed_id": "mybas_johor",
        "agency": "mybas",
        "category": "johor",
        "source_url": "https://api.data.gov.my/gtfs-static/mybas-johor",
    },
}

DEFAULT_HEADERS = {
    "User-Agent": "TransportAIIngestion/1.0 (research prototype; contact: vxkenglyn@gmail.com)"
}

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

@dataclass
class RunCounter:
    inserted: int = 0
    updated: int = 0

def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")

def clean_nan(value: Any) -> Any:
    if pd.isna(value):
        return None
    return value

def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df.where(pd.notna(df), None)

def ensure_cols(df: pd.DataFrame, cols: Iterable[str]) -> pd.DataFrame:
    df = df.copy()
    for col in cols:
        if col not in df.columns:
            df[col] = None
    return df

def to_int_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").astype("Int64")

def to_float_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")

def date_yyyymmdd_series(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, format="%Y%m%d", errors="coerce").dt.date

def start_ingestion_run(source_type: str, source_name: str, region_id: str | None = None, area_id: str | None = None) -> str:
    with engine.begin() as conn:
        run_id = conn.execute(text("""
            INSERT INTO ingestion_runs (source_type, source_name, region_id, area_id, status, started_at)
            VALUES (:source_type, :source_name, :region_id, :area_id, 'running', NOW())
            RETURNING ingestion_run_id::text;
        """), {
            "source_type": source_type,
            "source_name": source_name,
            "region_id": region_id,
            "area_id": area_id,
        }).scalar_one()
    return run_id

def finish_ingestion_run(run_id: str, status: str, counter: RunCounter, error_message: str | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE ingestion_runs
            SET status = :status,
                records_inserted = :inserted,
                records_updated = :updated,
                error_message = :error_message,
                completed_at = NOW()
            WHERE ingestion_run_id = :run_id;
        """), {
            "run_id": run_id,
            "status": status,
            "inserted": counter.inserted,
            "updated": counter.updated,
            "error_message": error_message,
        })

def update_freshness(source_type: str, source_name: str, region_id: str | None = None, area_id: str | None = None,
                     status: str = "fresh", notes: str | None = None, next_days: int = 30) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            DELETE FROM data_freshness
            WHERE source_type IS NOT DISTINCT FROM :source_type
              AND source_name IS NOT DISTINCT FROM :source_name
              AND region_id IS NOT DISTINCT FROM :region_id
              AND area_id IS NOT DISTINCT FROM :area_id;
        """), {
            "source_type": source_type,
            "source_name": source_name,
            "region_id": region_id,
            "area_id": area_id,
        })
        conn.execute(text("""
            INSERT INTO data_freshness (
                source_type, source_name, region_id, area_id,
                last_successful_update, next_scheduled_update,
                freshness_status, notes
            )
            VALUES (
                :source_type, :source_name, :region_id, :area_id,
                NOW(), :next_update, :status, :notes
            );
        """), {
            "source_type": source_type,
            "source_name": source_name,
            "region_id": region_id,
            "area_id": area_id,
            "next_update": datetime.utcnow() + timedelta(days=next_days),
            "status": status,
            "notes": notes,
        })

def temp_stage(df: pd.DataFrame, table_name: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(f'DROP TABLE IF EXISTS "{table_name}";'))
    # Reduced chunksize to prevent database memory overload
    df.to_sql(table_name, engine, if_exists="replace", index=False, chunksize=500, method="multi")
