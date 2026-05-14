from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from enum import Enum
import json

class StageStatus(str, Enum):
    """Enforce strongly-typed status for pipeline stages."""
    COMPLETED = "completed"
    FAILED = "failed"
    NO_DATA = "no_data"
    SKIPPED = "skipped"
    DEGRADED = "degraded"

class ConfidenceMetrics(BaseModel):
    """Standardized taxonomy for evidence quality."""
    signal_strength: float
    completeness: float
    reliability: float

class IndicatorMetadata(BaseModel):
    source_table: str
    record_count: Optional[int] = None
    pipeline_version: str
    signal_type: Optional[str] = None

class IndicatorSection(BaseModel):
    summary: Any
    metrics: ConfidenceMetrics
    metadata: IndicatorMetadata

class ConfidenceObject(BaseModel):
    """Decomposed confidence for challenge directions."""
    signal_strength: float
    data_completeness: float
    inference_confidence: float

class ProblemDirection(BaseModel):
    problem_direction_id: str
    challenge_type: str
    title: str
    reason_hint: str
    confidence: ConfidenceObject

class EvidenceProvenance(BaseModel):
    pipeline_version: str
    schema_version: str
    gtfs_feed_version: str
    osm_extract_date: str
    poi_snapshot_date: str
    rag_index_version: str
    data_age_days: Optional[int] = None

class ValidationPolicy(BaseModel):
    allowed_geography: Dict[str, List[str]]
    enforce_claim_hedging: bool

class EvidencePack(BaseModel):
    evidence_pack_id: Optional[str] = None
    pack_version: str
    generated_at: str
    area: Dict[str, Any]
    provenance: EvidenceProvenance
    data_quality: Dict[str, float]
    statistics: Dict[str, int]
    processing_status: Dict[str, StageStatus]
    indicators: Dict[str, IndicatorSection]
    candidate_problem_directions: Dict[str, Any]
    rag_support: Dict[str, Any]
    validation_policy: ValidationPolicy
    blocked_claims: List[str]
    limitations: List[str]
    evidence_rules: Dict[str, Any]
    audit: Dict[str, Any] = Field(description="Audit trail: environment, user, and execution context")
    runtime_metrics: Optional[Dict[str, Any]] = None

    def to_json(self) -> str:
        """Safe serialization using Pydantic's optimized JSON generator."""
        return self.model_dump_json()

    def to_dict(self) -> dict:
        """Export as a primitive dictionary for database drivers."""
        return self.model_dump()
