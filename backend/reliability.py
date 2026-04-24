from __future__ import annotations

from dataclasses import dataclass, asdict
import math
import re
from typing import Any


@dataclass
class OfficialServiceRecord:
    service_id: str
    label: str
    operator: str
    city: str
    service_kind: str
    mode: str
    anchors: list[str]
    corridor_roads: list[str]
    notes: str = ""


@dataclass
class ServiceMatchResult:
    official_data_used: bool
    matched_services: list[str]
    overlap_level: str
    recommendation_mode: str
    duplication_risk: bool
    top_match_score: float
    matched_records: list[dict[str, Any]]
    warnings: list[str]


@dataclass
class GeoConsistencyResult:
    pass_check: bool
    city_match_pass: bool
    route_match_pass: bool
    entity_match_pass: bool
    warnings: list[str]
    checked_points: int


@dataclass
class ClaimAuditResult:
    pass_check: bool
    hard_fail: bool
    removed_claims: list[str]
    rewritten_fields: list[str]
    warnings: list[str]
    sanitized_solution: dict[str, Any]


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _tokens(*values: Any) -> set[str]:
    text = " ".join(_norm(v) for v in values if v)
    raw = set(re.split(r"[^a-z0-9]+", text))
    generic = {
        "",
        "jalan",
        "jln",
        "road",
        "station",
        "stesen",
        "lrt",
        "mrt",
        "ktm",
        "bus",
        "route",
        "line",
        "loop",
        "and",
        "the",
        "to",
        "of",
        "in",
        "kuala",
        "lumpur",
        "malaysia",
        "section",
        "sek",
        "stesen",
        "rapid",
        "kl",
    }
    return {item for item in raw if len(item) >= 2 and item not in generic}


OFFICIAL_SERVICE_INVENTORY: list[OfficialServiceRecord] = [
    OfficialServiceRecord(
        service_id="T250",
        label="Rapid KL T250: Stesen LRT Wangsa Maju - Setapak Sentral",
        operator="Rapid KL",
        city="Kuala Lumpur",
        service_kind="fixed_route",
        mode="bus",
        anchors=["Wangsa Maju", "Setapak Sentral", "Jalan Genting Kelang"],
        corridor_roads=["Jalan Genting Kelang"],
        notes="Official Rapid KL feeder coverage in the Wangsa Maju / Setapak corridor.",
    ),
    OfficialServiceRecord(
        service_id="T251",
        label="Rapid KL T251: Stesen LRT Sri Rampai - Sek 10 Wangsa Maju",
        operator="Rapid KL",
        city="Kuala Lumpur",
        service_kind="fixed_route",
        mode="bus",
        anchors=["Sri Rampai", "Sek 10 Wangsa Maju", "Wangsa Maju", "Jalan Wangsa Delima"],
        corridor_roads=["Jalan Wangsa Delima", "Jalan 1/27A"],
        notes="Official Rapid KL feeder service from Sri Rampai into Wangsa Maju housing districts.",
    ),
    OfficialServiceRecord(
        service_id="T222",
        label="Rapid KL T222: Stesen LRT Sri Rampai - Ukay Perdana",
        operator="Rapid KL",
        city="Kuala Lumpur",
        service_kind="fixed_route",
        mode="bus",
        anchors=["Sri Rampai", "Ukay Perdana", "Jalan Wangsa Perdana"],
        corridor_roads=["Jalan Wangsa Perdana"],
        notes="Official Sri Rampai feeder route serving adjacent access corridors.",
    ),
    OfficialServiceRecord(
        service_id="T253B",
        label="Rapid KL On-Demand T253B: LRT Wangsa Maju - Sri Rampai",
        operator="Rapid KL",
        city="Kuala Lumpur",
        service_kind="on_demand",
        mode="bus_on_demand",
        anchors=["Wangsa Maju", "Sri Rampai", "Jalan Genting Kelang"],
        corridor_roads=["Jalan Genting Kelang"],
        notes="Official Rapid KL On-Demand zone linking Wangsa Maju and Sri Rampai.",
    ),
]


def get_official_service_inventory(city: str) -> list[OfficialServiceRecord]:
    city_norm = _norm(city)
    records: list[OfficialServiceRecord] = []
    for record in OFFICIAL_SERVICE_INVENTORY:
        record_city = _norm(record.city)
        if record_city in city_norm or city_norm in record_city:
            records.append(record)
    return records


def summarize_route_roads(analysis_result_raw: list[dict[str, Any]]) -> list[str]:
    route_roads: list[str] = []
    seen: set[str] = set()
    for raw in analysis_result_raw or []:
        for cand in raw.get("candidates", []) or []:
            for road in cand.get("via_roads", []) or []:
                clean = str(road or "").strip()
                if clean and clean not in seen:
                    seen.add(clean)
                    route_roads.append(clean)
    return route_roads


def match_official_services(
    city: str,
    selected_micro: dict[str, Any],
    analysis_result_raw: list[dict[str, Any]],
) -> ServiceMatchResult:
    inventory = get_official_service_inventory(city)
    if not inventory:
        return ServiceMatchResult(
            official_data_used=False,
            matched_services=[],
            overlap_level="none",
            recommendation_mode="new_service_candidate",
            duplication_risk=False,
            top_match_score=0.0,
            matched_records=[],
            warnings=[f"No official service inventory is configured for {city} yet."],
        )

    route_roads = summarize_route_roads(analysis_result_raw)
    micro_tokens = _tokens(
        selected_micro.get("location_label"),
        selected_micro.get("road_a_label"),
        selected_micro.get("road_b_label"),
        " ".join(selected_micro.get("road_a_queries", []) or []),
        " ".join(selected_micro.get("road_b_queries", []) or []),
        " ".join(route_roads),
    )

    matches: list[dict[str, Any]] = []
    for record in inventory:
        anchor_tokens = _tokens(" ".join(record.anchors), " ".join(record.corridor_roads))
        shared = sorted(micro_tokens & anchor_tokens)
        if not shared:
            continue
        score = round(len(shared) / max(len(anchor_tokens), 1), 3)
        matches.append(
            {
                "service_id": record.service_id,
                "label": record.label,
                "service_kind": record.service_kind,
                "mode": record.mode,
                "match_score": score,
                "matched_tokens": shared,
                "notes": record.notes,
            }
        )

    matches.sort(key=lambda item: item["match_score"], reverse=True)
    top_score = float(matches[0]["match_score"]) if matches else 0.0
    if top_score >= 0.5:
        overlap_level = "high"
    elif top_score >= 0.26:
        overlap_level = "partial"
    elif top_score > 0:
        overlap_level = "nearby"
    else:
        overlap_level = "none"

    duplication_risk = overlap_level in {"high", "partial"}
    if overlap_level == "high":
        recommendation_mode = "upgrade_existing_service"
    elif overlap_level == "partial":
        recommendation_mode = "close_access_gap"
    else:
        recommendation_mode = "new_service_candidate"

    warnings: list[str] = []
    if duplication_risk and matches:
        warnings.append(
            f"Official service overlap detected with {matches[0]['label']}; prefer upgrade or access-improvement framing."
        )
    elif matches:
        warnings.append("Nearby official service exists; verify whether the issue is coverage, access quality, or scheduling.")

    return ServiceMatchResult(
        official_data_used=True,
        matched_services=[item["label"] for item in matches],
        overlap_level=overlap_level,
        recommendation_mode=recommendation_mode,
        duplication_risk=duplication_risk,
        top_match_score=top_score,
        matched_records=matches,
        warnings=warnings,
    )


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _nearest_distance_m(point: dict[str, float], line: list[dict[str, float]]) -> float:
    if not line:
        return float("inf")
    return min(_haversine_m(point["lat"], point["lng"], other["lat"], other["lng"]) for other in line)


def _collect_entity_points(entities: list[dict[str, Any]]) -> list[dict[str, float]]:
    points: list[dict[str, float]] = []
    for entity in entities or []:
        position = entity.get("position")
        if isinstance(position, dict) and {"lat", "lng"} <= set(position.keys()):
            points.append({"lat": float(position["lat"]), "lng": float(position["lng"])})
        polyline = entity.get("polyline_positions")
        if isinstance(polyline, list):
            if polyline and isinstance(polyline[0], list):
                segments = polyline
            else:
                segments = [polyline]
            for segment in segments:
                for item in segment[:25]:
                    if isinstance(item, dict) and {"lat", "lng"} <= set(item.keys()):
                        points.append({"lat": float(item["lat"]), "lng": float(item["lng"])})
        polygon = entity.get("polygon_positions")
        if isinstance(polygon, list):
            for item in polygon[:25]:
                if isinstance(item, dict) and {"lat", "lng"} <= set(item.keys()):
                    points.append({"lat": float(item["lat"]), "lng": float(item["lng"])})
    return points


def validate_geo_consistency(
    city_center: dict[str, float] | None,
    selected_micro: dict[str, Any],
    analysis_result_raw: list[dict[str, Any]],
    *,
    entities: list[dict[str, Any]] | None = None,
    city_radius_m: float = 120000.0,
    route_radius_m: float = 25000.0,
) -> GeoConsistencyResult:
    warnings: list[str] = []
    checked_points = 0
    route_points: list[dict[str, float]] = []
    for raw in analysis_result_raw or []:
        for point in raw.get("route_geometry", []) or []:
            if isinstance(point, dict) and {"lat", "lng"} <= set(point.keys()):
                route_points.append({"lat": float(point["lat"]), "lng": float(point["lng"])})

    city_match_pass = True
    route_match_pass = True
    entity_match_pass = True

    micro_lat = selected_micro.get("lat")
    micro_lon = selected_micro.get("lon")
    if city_center and micro_lat is not None and micro_lon is not None:
        checked_points += 1
        dist = _haversine_m(float(micro_lat), float(micro_lon), city_center["lat"], city_center["lng"])
        if dist > city_radius_m:
            city_match_pass = False
            warnings.append("Selected hotspot sits far outside the target city center; geography may be inconsistent.")

    if city_center and route_points:
        far_points = 0
        for point in route_points[:100]:
            checked_points += 1
            if _haversine_m(point["lat"], point["lng"], city_center["lat"], city_center["lng"]) > city_radius_m:
                far_points += 1
        if far_points:
            city_match_pass = False
            warnings.append("Route geometry extends outside the expected city radius.")

    if route_points and entities:
        for point in _collect_entity_points(entities)[:150]:
            checked_points += 1
            if _nearest_distance_m(point, route_points) > route_radius_m:
                entity_match_pass = False
                warnings.append("One or more rendered map assets fall far outside the routed corridor.")
                break

    if not route_points:
        route_match_pass = False
        warnings.append("No routed corridor geometry was available for geo validation.")

    return GeoConsistencyResult(
        pass_check=city_match_pass and route_match_pass and entity_match_pass,
        city_match_pass=city_match_pass,
        route_match_pass=route_match_pass,
        entity_match_pass=entity_match_pass,
        warnings=warnings,
        checked_points=checked_points,
    )


def extract_allowed_numeric_facts(
    analysis_result_raw: list[dict[str, Any]],
    service_match: ServiceMatchResult,
) -> list[str]:
    facts = ["400m"]
    seen = {facts[0]}
    for raw in analysis_result_raw or []:
        for cand in raw.get("candidates", []) or []:
            total_length = cand.get("total_length_m")
            if total_length is not None:
                token = f"{int(round(float(total_length)))}m"
                if token not in seen:
                    seen.add(token)
                    facts.append(token)
    for record in service_match.matched_records[:3]:
        service_id = str(record.get("service_id", "")).strip()
        if service_id and service_id not in seen:
            seen.add(service_id)
            facts.append(service_id)
    return facts


def _station_like_anchor(selected_micro: dict[str, Any]) -> bool:
    return any(
        token in _norm(" ".join(selected_micro.get(key, []) or []))
        for key in ("road_a_queries", "road_b_queries")
        for token in ("lrt", "mrt", "ktm", "station", "stesen", "interchange", "terminal")
    )


def infer_primary_intervention_family(
    selected_micro: dict[str, Any],
    service_match: ServiceMatchResult,
    analysis_result_raw: list[dict[str, Any]],
) -> str:
    micro_text = " ".join(
        [
            str(selected_micro.get("location_label") or ""),
            str(selected_micro.get("symptom") or ""),
            " ".join(selected_micro.get("road_a_queries", []) or []),
            " ".join(selected_micro.get("road_b_queries", []) or []),
        ]
    ).lower()
    if any(token in micro_text for token in ["walk", "walking", "pedestrian", "foot", "access", "crossing", "station approach"]):
        return "pedestrian_access_upgrade"
    if service_match.duplication_risk or service_match.overlap_level in {"high", "partial"}:
        return "transit_hub_upgrade"
    route_roads = summarize_route_roads(analysis_result_raw)
    if len(route_roads) >= 2:
        return "bus_interface_upgrade"
    return "access_improvement"


def build_evidence_basis(
    selected_micro: dict[str, Any],
    service_match: ServiceMatchResult,
    route_roads: list[str],
) -> str:
    location = str(selected_micro.get("location_label") or selected_micro.get("LOCATION_LABEL") or "the selected hotspot")
    if service_match.matched_services:
        return (
            f"Selected for {location} because the hotspot is route-grounded on "
            f"{', '.join(route_roads[:2]) or 'the mapped corridor'} and overlaps existing service "
            f"{service_match.matched_services[0]}, indicating an upgrade or access-improvement need."
        )
    if route_roads:
        return (
            f"Selected for {location} because the hotspot has a routable corridor on "
            f"{', '.join(route_roads[:2])} with no strong official service overlap, supporting a targeted new intervention."
        )
    return f"Selected for {location} because the hotspot is spatially specific, but the route evidence remains weak."


def assess_solution_eligibility(
    selected_micro: dict[str, Any],
    analysis_result_raw: list[dict[str, Any]],
    route_roads: list[str],
    service_match: ServiceMatchResult,
    geo_consistency: GeoConsistencyResult,
) -> dict[str, Any]:
    candidate_count = sum(len(raw.get("candidates", []) or []) for raw in analysis_result_raw or [])
    station_like_anchor = _station_like_anchor(selected_micro)
    reasons: list[str] = []
    if candidate_count < 1:
        reasons.append("No reliable routed candidate was found for the selected hotspot.")
    if len(route_roads) < 1:
        reasons.append("No stable corridor roads were recovered from the routing analysis.")
    if not geo_consistency.pass_check:
        reasons.append("Geo consistency checks did not fully pass for this hotspot.")
    if not station_like_anchor and service_match.overlap_level == "none" and candidate_count < 2:
        reasons.append("The hotspot lacks both strong station anchors and strong network evidence.")
    eligible = not reasons
    confidence_floor = "high" if eligible and candidate_count >= 2 and len(route_roads) >= 2 else ("medium" if eligible else "low")
    return {
        "eligible": eligible,
        "reasons": reasons,
        "candidate_count": candidate_count,
        "route_road_count": len(route_roads),
        "station_like_anchor": station_like_anchor,
        "confidence_floor": confidence_floor,
    }


def build_decision_package(
    selected_city: str,
    selected_micro: dict[str, Any],
    analysis_result: list[dict[str, Any]],
    analysis_result_raw: list[dict[str, Any]],
    city_center: dict[str, float] | None,
) -> dict[str, Any]:
    service_match = match_official_services(selected_city, selected_micro, analysis_result_raw)
    geo_consistency = validate_geo_consistency(city_center, selected_micro, analysis_result_raw)
    route_roads = summarize_route_roads(analysis_result_raw)
    allowed_numeric_facts = extract_allowed_numeric_facts(analysis_result_raw, service_match)
    intervention_family = infer_primary_intervention_family(selected_micro, service_match, analysis_result_raw)
    evidence_basis = build_evidence_basis(selected_micro, service_match, route_roads)
    solution_eligibility = assess_solution_eligibility(
        selected_micro,
        analysis_result_raw,
        route_roads,
        service_match,
        geo_consistency,
    )

    warnings = list(service_match.warnings) + list(geo_consistency.warnings)
    reliability_flags = {
        "official_data_used": service_match.official_data_used,
        "duplication_risk": service_match.duplication_risk,
        "geo_consistency_pass": geo_consistency.pass_check,
        "route_match_pass": geo_consistency.route_match_pass,
        "station_like_anchor": solution_eligibility["station_like_anchor"],
        "solution_eligible": solution_eligibility["eligible"],
    }

    candidate_count = solution_eligibility["candidate_count"]
    reliability_band = "medium"
    if not solution_eligibility["eligible"] or not geo_consistency.city_match_pass:
        reliability_band = "low"
    elif geo_consistency.pass_check and candidate_count >= 2 and len(route_roads) >= 2 and service_match.overlap_level != "partial":
        reliability_band = "high"
    elif service_match.overlap_level == "partial" or len(route_roads) < 2 or candidate_count < 2:
        reliability_band = "medium"

    if not solution_eligibility["eligible"]:
        warnings.append("Evidence sufficiency is weak; ask the user to pick a better hotspot instead of presenting a polished plan.")

    return {
        "selected_city": selected_city,
        "selected_micro": selected_micro,
        "routed_candidates": analysis_result,
        "routed_candidate_summary": {
            "candidate_count": sum(len(raw.get("candidates", []) or []) for raw in analysis_result_raw or []),
            "route_roads": route_roads,
            "routing_modes": sorted({str(raw.get("mode", "unknown")) for raw in analysis_result_raw or []}),
        },
        "official_service_match": asdict(service_match),
        "corridor_demand_context": {
            "micro_confidence": str(selected_micro.get("confidence") or "medium"),
            "location_label": selected_micro.get("location_label") or selected_micro.get("LOCATION_LABEL"),
            "station_like_anchor": solution_eligibility["station_like_anchor"],
        },
        "geo_consistency": asdict(geo_consistency),
        "allowed_numeric_facts": allowed_numeric_facts,
        "evidence_basis": evidence_basis,
        "intervention_support": {
            "primary_intervention_family": intervention_family,
            "target_corridor_anchor_roads": route_roads[:5],
            "official_service_overlap_rationale": (
                service_match.warnings[0]
                if service_match.warnings
                else "No official overlap warning was triggered."
            ),
            "one_primary_family_only": True,
        },
        "solution_eligibility": solution_eligibility,
        "warnings": warnings,
        "reliability_flags": reliability_flags,
        "reliability_band": reliability_band,
    }


NUMERIC_CLAIM_PATTERN = re.compile(r"\b\d+(?:\.\d+)?\s*(?:km|m|minutes?|mins?|hours?|%|percent)\b", re.IGNORECASE)


def _rewrite_unsupported_numeric_claims(text: str, allowed_facts: list[str], removed_claims: list[str]) -> str:
    allowed = {_norm(item) for item in allowed_facts}

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if _norm(token) in allowed:
            return token
        removed_claims.append(token)
        lowered = token.lower()
        if "km" in lowered or re.search(r"\bm\b", lowered):
            return "a short corridor segment"
        if "min" in lowered or "hour" in lowered:
            return "a shorter transfer window"
        if "%" in lowered or "percent" in lowered:
            return "a meaningful share"
        return "a measured value"

    return NUMERIC_CLAIM_PATTERN.sub(_replace, text)


def audit_solution_claims(solution_result: dict[str, Any], decision_package: dict[str, Any]) -> ClaimAuditResult:
    sanitized = dict(solution_result or {})
    removed_claims: list[str] = []
    rewritten_fields: list[str] = []
    warnings: list[str] = []
    allowed_facts = list(decision_package.get("allowed_numeric_facts") or [])
    reliability_band = str(decision_package.get("reliability_band") or "medium").lower()

    text_fields = [
        "solution_title",
        "detailed_description",
        "societal_impact",
    ]
    list_fields = ["expected_effect", "proposed_actions"]

    for field in text_fields:
        original = str(sanitized.get(field) or "")
        rewritten = _rewrite_unsupported_numeric_claims(original, allowed_facts, removed_claims)
        if field == "societal_impact" and rewritten:
            cautious = rewritten.lower().startswith(("potential impact:", "likely impact:", "this could"))
            if reliability_band != "high" and not cautious:
                rewritten = f"Potential impact: {rewritten}"
        if rewritten != original:
            rewritten_fields.append(field)
            sanitized[field] = rewritten

    for field in list_fields:
        values = list(sanitized.get(field) or [])
        new_values: list[str] = []
        field_changed = False
        for item in values:
            original = str(item or "")
            rewritten = _rewrite_unsupported_numeric_claims(original, allowed_facts, removed_claims)
            if rewritten != original:
                field_changed = True
            new_values.append(rewritten)
        if field_changed:
            rewritten_fields.append(field)
            sanitized[field] = new_values

    if not sanitized.get("evidence_basis"):
        sanitized["evidence_basis"] = str(decision_package.get("evidence_basis") or "").strip()

    if not sanitized.get("primary_intervention_family"):
        sanitized["primary_intervention_family"] = (
            decision_package.get("intervention_support") or {}
        ).get("primary_intervention_family", "access_improvement")

    uncertainties = list(sanitized.get("uncertainties") or [])
    if reliability_band != "high":
        uncertainties.append("This recommendation still needs field validation before implementation.")
    if removed_claims:
        uncertainties.append("Some unsupported numeric claims were rewritten into qualitative wording.")
    if decision_package.get("official_service_match", {}).get("overlap_level") == "partial":
        uncertainties.append("Existing service overlap is partial, so the upgrade scope should be validated against operator operations data.")
    if uncertainties:
        sanitized["uncertainties"] = list(dict.fromkeys(str(item) for item in uncertainties if str(item).strip()))

    if removed_claims:
        warnings.append("Unsupported numeric claims were rewritten to qualitative wording.")

    geo = decision_package.get("geo_consistency") or {}
    hard_fail = (
        not bool(geo.get("city_match_pass", True))
        or not bool((decision_package.get("solution_eligibility") or {}).get("eligible", True))
    )
    pass_check = not hard_fail

    return ClaimAuditResult(
        pass_check=pass_check,
        hard_fail=hard_fail,
        removed_claims=removed_claims,
        rewritten_fields=rewritten_fields,
        warnings=warnings,
        sanitized_solution=sanitized,
    )
