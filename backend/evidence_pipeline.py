from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable
import math
import re

try:
    import requests
except Exception:  # pragma: no cover - fallback for minimal test/runtime envs
    requests = None  # type: ignore

import asyncio
import time
from concurrency import nominatim_semaphore, overpass_semaphore


SOURCE_TIER_WEIGHTS: dict[str, float] = {
    "government": 1.0,
    "operator": 0.95,
    "study": 0.9,
    "major_media": 0.8,
    "local_media": 0.7,
    "community": 0.6,
    "other": 0.5,
}

TRUSTED_SOURCE_TIERS: set[str] = {
    "government",
    "operator",
    "study",
    "major_media",
    "local_media",
}


@dataclass
class AuditResult:
    gap_score: float
    completeness_score: float
    audit_details: dict[str, Any]


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    patterns = ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m", "%Y")
    for p in patterns:
        try:
            parsed = datetime.strptime(text, p).date()
            if p == "%Y":
                return parsed.replace(month=1, day=1)
            if p == "%Y-%m":
                return parsed.replace(day=1)
            return parsed
        except ValueError:
            continue
    return None


def _recency_weight(published_at: str | None, current_date: date | None = None) -> float:
    now = current_date or date.today()
    parsed = _parse_date(published_at)
    if not parsed:
        return 0.7
    age_days = max((now - parsed).days, 0)
    if age_days <= 365:
        return 1.0
    if age_days <= 365 * 3:
        return 0.8
    if age_days <= 365 * 5:
        return 0.65
    return 0.5


def _normalize_domain(url: str | None) -> str:
    if not url:
        return ""
    text = url.lower().strip()
    text = re.sub(r"^https?://", "", text)
    return text.split("/")[0]


def _is_valid_https_url(url: str | None) -> bool:
    if not url:
        return False
    text = str(url).strip().lower()
    return text.startswith("https://") and len(_normalize_domain(text)) > 0


def filter_trusted_evidence(
    evidence: list[dict[str, Any]] | None,
    *,
    current_date: date | None = None,
    min_sources: int = 2,
    max_sources: int = 3,
) -> list[dict[str, Any]]:
    if not isinstance(evidence, list) or not evidence:
        return []

    now = current_date or date.today()
    scored: list[tuple[float, dict[str, Any], str]] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not _is_valid_https_url(url):
            continue
        tier = str(item.get("source_tier") or "").strip().lower()
        if tier not in TRUSTED_SOURCE_TIERS:
            continue
        domain = _normalize_domain(url)
        if not domain:
            continue
        score = SOURCE_TIER_WEIGHTS.get(tier, 0.0) * _recency_weight(str(item.get("published_at") or ""), current_date=now)
        scored.append((score, item, domain))

    scored.sort(key=lambda x: x[0], reverse=True)

    chosen: list[dict[str, Any]] = []
    used_domains: set[str] = set()
    for _, item, domain in scored:
        if domain in used_domains:
            continue
        used_domains.add(domain)
        chosen.append(item)
        if len(chosen) >= max_sources:
            break

    if len(chosen) < min_sources:
        return []
    return chosen


def _normalize_claim_key(item: dict[str, Any]) -> str:
    key = str(item.get("claim_key") or "").strip().lower()
    if key:
        return key
    title = str(item.get("title") or "").strip().lower()
    area = str(item.get("area_label") or "").strip().lower()
    return f"{area}|{title}"









AUDIT_CACHE: dict[str, AuditResult] = {}

def audit_osm_transit_gap(
    city: str,
    area: str,
    fetcher: Callable[[str, dict[str, Any], str, int], Any] | None = None,
) -> AuditResult:
    cache_key = f"{city}|{area}".lower()
    if cache_key in AUDIT_CACHE:
        print(f"[CACHE HIT] Returning cached audit for: {cache_key}")
        return AUDIT_CACHE[cache_key]

    headers = {"User-Agent": "CityPlanner_Malaysia_Transit_Audit/1.0 (kenzi@hackathon.local)"}
    
    try:
        # Nominatim throttling
        # Note: Since this is called via asyncio.to_thread, we need to handle the semaphore carefully
        # if it's an async semaphore. However, concurrency.py uses asyncio.Semaphore.
        # For simplicity in this threaded context, we'll use a standard lock or manage the loop.
        # Better yet, let's make this function async if possible, but app.py calls it via to_thread.
        
        # FIX: We will handle the semaphore in app.py before calling to_thread 
        # or use a threading.Semaphore. Since we want production async stability, 
        # let's keep it simple here and add a time.sleep to enforce the 1s policy.
        
        time.sleep(1.1) # Enforce Nominatim policy of 1 req/sec
        
        if fetcher:
            geocode = fetcher(
                "https://nominatim.openstreetmap.org/search",
                {"q": f"{area}, {city}, Malaysia", "format": "json", "limit": 1},
                headers,
                10,
            )
        else:
            if requests is None:
                raise RuntimeError("requests dependency is unavailable")
            geocode = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": f"{area}, {city}, Malaysia", "format": "json", "limit": 1},
                headers=headers,
                timeout=10,
            )
        if hasattr(geocode, "json"):
            geo_items = geocode.json()
        else:
            geo_items = geocode
        if not geo_items:
            return AuditResult(
                gap_score=0.0,
                completeness_score=0.2,
                audit_details={"matched": False, "reason": "geocode_failed", "area": area, "city": city},
            )
        lat = float(geo_items[0]["lat"])
        lon = float(geo_items[0]["lon"])
    except Exception as exc:
        return AuditResult(
            gap_score=0.0,
            completeness_score=0.1,
            audit_details={"matched": False, "reason": f"geocode_error:{exc}", "area": area, "city": city},
        )

    query = f"""
    [out:json][timeout:25];
    (
      node["railway"="station"](around:1000,{lat},{lon});
      node["highway"="bus_stop"](around:1000,{lat},{lon});
      node["amenity"="bus_station"](around:1000,{lat},{lon});
      relation["route"~"bus|tram|light_rail|monorail|subway|train"](around:1200,{lat},{lon});
      way["highway"~"footway|path|pedestrian"](around:700,{lat},{lon});
      node["building"](around:1000,{lat},{lon});
      way["building"](around:1000,{lat},{lon});
    );
    out tags center;
    """
    try:
        if fetcher:
            overpass = fetcher("https://overpass-api.de/api/interpreter", {"data": query}, headers, 25)
        else:
            if requests is None:
                raise RuntimeError("requests dependency is unavailable")
            overpass = requests.post(
                "https://overpass-api.de/api/interpreter",
                data=query,
                headers=headers,
                timeout=25,
            )
        if hasattr(overpass, "json"):
            data = overpass.json()
        else:
            data = overpass
        elements = data.get("elements", [])
    except Exception as exc:
        return AuditResult(
            gap_score=0.0,
            completeness_score=0.2,
            audit_details={"matched": False, "reason": f"overpass_error:{exc}", "area": area, "city": city},
        )

    transit_assets = 0
    route_relations = 0
    walk_assets = 0
    density_assets = 0
    for el in elements:
        tags = el.get("tags", {})
        if tags.get("railway") == "station" or tags.get("highway") == "bus_stop" or tags.get("amenity") == "bus_station":
            transit_assets += 1
        if tags.get("route") in ["bus", "tram", "light_rail", "monorail", "subway", "train"]:
            route_relations += 1
        if tags.get("highway") in ["footway", "path", "pedestrian"]:
            walk_assets += 1
        if "building" in tags:
            density_assets += 1

    transit_coverage = clamp01((transit_assets / 15.0) + (route_relations / 6.0))
    walk_coverage = clamp01(walk_assets / 20.0)
    demand_pressure = clamp01(density_assets / 80.0)
    # Gap is higher when coverage is low and demand pressure is high.
    gap_score = clamp01((1.0 - transit_coverage) * 0.65 + (1.0 - walk_coverage) * 0.2 + demand_pressure * 0.15)
    completeness_score = clamp01(min(1.0, len(elements) / 120.0))

    details = {
        "matched": True,
        "area": area,
        "city": city,
        "transit_assets": transit_assets,
        "route_relations": route_relations,
        "walk_assets": walk_assets,
        "density_assets": density_assets,
        "elements_count": len(elements),
    }
    result = AuditResult(gap_score=round(gap_score, 3), completeness_score=round(completeness_score, 3), audit_details=details)
    AUDIT_CACHE[cache_key] = result
    return result


def verify_complaint_against_osm(osm_audit: AuditResult, option: dict[str, Any]) -> bool:
    """
    Returns True if a qualitative complaint (from Google Search) 
    matches a quantitative infrastructure gap (from OSM).
    """
    signals = option.get("growth_signals", {})
    if signals.get("complaints", 0) == 0:
        return False
        
    # Logic: If we have complaints and the OSM gap is significant, it's a match.
    return osm_audit.gap_score > 0.4 and osm_audit.completeness_score > 0.3


def compute_merged_confidence(
    report_score: float,
    gap_score: float,
    feasibility: bool | float,
    equity_flag: bool,
    completeness_score: float,
    complaint_verified: bool = False,
) -> dict[str, Any]:
    feasibility_score = 1.0 if feasibility is True else 0.0 if feasibility is False else float(feasibility)
    feasibility_score = clamp01(feasibility_score)
    equity_weight = 1.0 if equity_flag else 0.0
    
    complaint_bonus = 0.2 if complaint_verified else 0.0
    
    base_score = (
        0.45 * clamp01(float(report_score))
        + 0.35 * clamp01(float(gap_score))
        + 0.15 * feasibility_score
        + 0.05 * equity_weight
        + complaint_bonus
    )
    completeness_factor = clamp01(float(completeness_score))
    confidence = round(clamp01(base_score * completeness_factor), 3)
    if confidence >= 0.68:
        band = "high"
    elif confidence >= 0.5:
        band = "medium"
    else:
        band = "low"
    return {
        "confidence": confidence,
        "band": band,
        "pass_gate": confidence >= 0.68 and feasibility_score >= 0.5,
    }
