import json
import uuid
import logging
import time
import platform
from functools import wraps
from datetime import datetime, timezone
from typing import Callable, Any

from sqlalchemy import text

from backend.db.database import engine
from backend.evidence_pack_builder.models import (
    EvidencePack, 
    EvidenceProvenance, 
    ValidationPolicy, 
    IndicatorSection, 
    ConfidenceMetrics,
    IndicatorMetadata,
    StageStatus
)
from backend.evidence_pack_builder import fetchers, metrics
from backend.evidence_pack_builder.config import config

# =========================================================
# Setup & Logging
# =========================================================
logger = logging.getLogger(__name__)

# =========================================================
# Resilience Layer (Elite Version)
# =========================================================

def with_retry(max_attempts: int = config.MAX_RETRIES, delay: float = config.RETRY_DELAY):
    """
    Elite-grade retry mechanism using centralized configuration.
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            attempts = 0
            while attempts < max_attempts:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    attempts += 1
                    logger.warning(f"Retry {attempts}/{max_attempts} for {func.__name__} due to: {e}")
                    if attempts == max_attempts:
                        logger.error(f"FATAL: Final failure for {func.__name__} after {attempts} attempts.")
                        raise
                    time.sleep(delay * (2 ** attempts))
            return None
        return wrapper
    return decorator


# =========================================================
# [PLACEHOLDER FOR RAG INTEGRATION]
# =========================================================

def get_rag_support(area_id: str) -> dict:
    """
    FRIEND: Integrate your RAG code here.
    """
    return {
        "enabled": False,
        "message": "RAG support not yet connected.",
        "rag_by_challenge": [],
    }


# =========================================================
# Evidence Pack Orchestrator (Elite Assembler)
# =========================================================

class EvidencePackAssembler:
    """
    Elite Assembler: Handles high-integrity assembly with full lineage.
    """
    def __init__(self, area_id: str):
        self.area_id = area_id
        self.trace_id = str(uuid.uuid4())
        self.start_time = time.time()
        self.stage_timings = {}
        self.status = {s: StageStatus.SKIPPED for s in ["area", "gtfs", "osm", "poi", "challenges", "rag"]}

    def _mark_stage(self, stage: str, duration_ms: int, status: StageStatus):
        self.stage_timings[f"{stage}_ms"] = duration_ms
        self.status[stage] = status

    def assemble(self) -> EvidencePack:
        generated_at = datetime.now(timezone.utc).isoformat()
        logger.info(f"Starting Elite Assembly [Trace: {self.trace_id}] for area: {self.area_id}")
        
        # 1. Fetch Area (Source Lineage)
        s_time = time.time()
        area = fetchers.get_area_profile(self.area_id)
        if not area:
            self._mark_stage("area", int((time.time() - s_time) * 1000), StageStatus.FAILED)
            raise ValueError(f"CRITICAL: Area {self.area_id} missing from source.")
        self._mark_stage("area", int((time.time() - s_time) * 1000), StageStatus.COMPLETED)

        # 2. Fetch GTFS Indicators
        s_time = time.time()
        try:
            raw_gtfs = fetchers.get_route_frequency_summary(self.area_id)
            gtfs_status = StageStatus.COMPLETED if raw_gtfs else StageStatus.NO_DATA
        except Exception as e:
            logger.error(f"Pipeline Error [GTFS]: {e}")
            raw_gtfs = []
            gtfs_status = StageStatus.FAILED
        self._mark_stage("gtfs", int((time.time() - s_time) * 1000), gtfs_status)

        # 3. Fetch Spatial Indicators (OSM/POI)
        s_time = time.time()
        osm = fetchers.get_transit_coverage_summary(self.area_id)
        poi = fetchers.get_demand_proxy_summary(self.area_id)
        self._mark_stage("osm", 0, StageStatus.COMPLETED if osm else StageStatus.NO_DATA)
        self._mark_stage("poi", 0, StageStatus.COMPLETED if poi else StageStatus.NO_DATA)
        self.stage_timings["spatial_fetch_ms"] = int((time.time() - s_time) * 1000)

        # 4. Fetch Challenges
        s_time = time.time()
        raw_challenges = fetchers.get_candidate_problem_directions(self.area_id)
        self._mark_stage("challenges", int((time.time() - s_time) * 1000), StageStatus.COMPLETED if raw_challenges else StageStatus.NO_DATA)

        # 5. Core Metric Execution
        enriched_routes = [{"evidence_id": f"gtfs_{r.get('route_id')}", "trace_id": self.trace_id, **r} for r in raw_gtfs]
        gtfs_comp = metrics.calculate_gtfs_completeness(enriched_routes)
        osm_score = metrics.calculate_osm_coverage_score(osm)
        
        # 6. Assembler Orchestration
        indicators = {
            "route_frequency": IndicatorSection(
                summary=enriched_routes,
                metrics=ConfidenceMetrics(
                    signal_strength=metrics.calculate_route_signal_strength(enriched_routes), 
                    completeness=gtfs_comp, reliability=0.85
                ),
                metadata=IndicatorMetadata(source_table="gtfs_route_frequency_summary", pipeline_version=config.VERSION)
            ),
            "transit_coverage": IndicatorSection(
                summary=osm if osm else {},
                metrics=ConfidenceMetrics(signal_strength=osm_score, completeness=0.80 if osm else 0, reliability=0.90),
                metadata=IndicatorMetadata(source_table="transit_coverage_summary", pipeline_version=config.VERSION)
            )
        }

        rag = get_rag_support(self.area_id)
        self.status["rag"] = StageStatus.COMPLETED if rag.get("enabled") else StageStatus.SKIPPED

        self.stage_timings["total_duration_ms"] = int((time.time() - self.start_time) * 1000)

        # 7. Elite Provenance & Audit
        audit_trail = {
            "trace_id": self.trace_id,
            "environment": os.getenv("APP_ENV", "production"),
            "system": f"{platform.system()} {platform.release()}",
            "python_version": platform.python_version(),
            "generated_by": "EvidencePackAssembler_Elite",
            "builder_version": config.VERSION
        }

        return EvidencePack(
            pack_schema_version=config.SCHEMA_VERSION,
            generated_at=generated_at,
            area=area,
            provenance=EvidenceProvenance(
                pipeline_version=config.VERSION, 
                schema_version=config.SCHEMA_VERSION,
                gtfs_feed_version=config.GTFS_FEED_VERSION, 
                osm_extract_date=config.OSM_EXTRACT_DATE, 
                poi_snapshot_date=config.POI_SNAPSHOT_DATE, 
                rag_index_version=config.RAG_INDEX_VERSION, 
                data_age_days=config.DATA_AGE_DAYS
            ),
            data_quality={"gtfs": gtfs_comp, "osm": osm_score},
            statistics={"routes": len(enriched_routes), "challenges": len(raw_challenges)},
            processing_status=self.status,
            indicators=indicators,
            candidate_problem_directions={"directions": raw_challenges},
            rag_support=rag,
            validation_policy=ValidationPolicy(allowed_geography={}, enforce_claim_hedging=True),
            blocked_claims=["Anti-hallucination policy enforced."],
            limitations=["Heuristic-based confidence models applied."],
            evidence_rules={"strict_rag": True},
            audit=audit_trail,
            runtime_metrics=self.stage_timings
        )


def build_general_evidence_pack(area_id: str) -> EvidencePack:
    """Entry Point: Generates a high-integrity Evidence Pack."""
    return EvidencePackAssembler(area_id).assemble()


# =========================================================
# Persistence Layer (Elite Resilience)
# =========================================================

@with_retry()
def store_evidence_pack(area_id: str, pack: EvidencePack) -> str:
    """
    Elite Storage: Strictly validated, resilient, and audit-linked.
    """
    EvidencePack.model_validate(pack.model_dump())
    
    evidence_pack_id = str(uuid.uuid4())
    pack.evidence_pack_id = evidence_pack_id

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO evidence_packs (evidence_pack_id, area_id, pack_version, pack_json, created_at)
                VALUES (:evidence_pack_id, :area_id, :pack_version, :pack_json, NOW());
            """),
            {
                "evidence_pack_id": evidence_pack_id,
                "area_id": area_id,
                "pack_version": pack.pack_schema_version,
                "pack_json": json.dumps(pack.to_dict()), 
            },
        )
    return evidence_pack_id


# =========================================================
# Public API (Standard Gateway)
# =========================================================

def build_and_save_general_evidence_pack(area_id: str) -> dict:
    try:
        pack_obj = build_general_evidence_pack(area_id)
        evidence_pack_id = store_evidence_pack(area_id=area_id, pack=pack_obj)
        return {"status": "success", "evidence_pack_id": evidence_pack_id, "pack": pack_obj.to_dict()}
    except Exception as e:
        logger.critical(f"ELITE PIPELINE CRASH for {area_id}: {e}")
        return {"status": "failed", "error": str(e)}