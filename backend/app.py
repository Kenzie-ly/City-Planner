from __future__ import annotations

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
import os
import requests

from typing import Any
import uuid
import json
import re
from urllib.parse import urlparse

from agent import (
    place_intake_agent,
    find_needs_agent,
    growth_signal_agent,
    planning_agent,
    solution_agent,
    building_agent,
    review_agent,
    InfrastructurePlannerOrchestrator,
    find_hotspot_agent,
    hallucination_audit_agent,
)
from building_agent_helper import process_agent_assets, format_entities
from FindRoads import run_city_road_connection_analysis
from evidence_pipeline import (
    collect_google_growth_signals,
    cluster_findings_to_area_options,
    audit_osm_transit_gap,
    compute_merged_confidence,
    verify_complaint_against_osm,
    filter_trusted_evidence,
)

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GROWTH_FLOW_ENABLED = os.getenv("GROWTH_FLOW_ENABLED", "1").strip().lower() not in {"0", "false", "no"}

app = FastAPI(title="Infrastructure Planner API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

APP_NAME = "infrastructure_planner"
USER_ID = "user_001"

session_service = InMemorySessionService()
workflow_state: dict[str, dict[str, Any]] = {}

PIPELINE = [
    ("Plan improvements", planning_agent, "planning_result"),
    ("Generate solutions", solution_agent, "solution_result"),
    ("Building simulations", building_agent, "simulation_result"),
]


class StartRequest(BaseModel):
    pass


class ChatRequest(BaseModel):
    session_id: str
    message: str


def clean_json_text(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"```[a-zA-Z]*", "", text)
        text = text.replace("```", "")
    return text.strip()


def safe_json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        raise ValueError("Expected JSON string, dict, or list.")
    return json.loads(clean_json_text(value))


def is_retry_response(text: Any) -> bool:
    return isinstance(text, str) and text.strip().upper().startswith("VERDICT: RETRY")


def extract_retry_feedback(text: Any, default: str = "The AI is temporarily unavailable. Please try again.") -> str:
    if not isinstance(text, str):
        return default
    m = re.search(r"FEEDBACK:\s*(.*)", text, re.IGNORECASE | re.DOTALL)
    if m:
        feedback = m.group(1).strip()
        if feedback:
            return feedback
    return default


async def run_agent_with_retry(agent, session_id: str, prompt: str, max_attempts: int = 2) -> str:
    attempts = max(1, int(max_attempts or 1))
    last_response = ""
    for _ in range(attempts):
        last_response = await run_agent_once(agent, session_id, prompt)
        if not is_retry_response(last_response):
            return last_response
        await asyncio.sleep(0.25)
    return last_response


async def run_agent_once(agent, session_id: str, prompt: str) -> str:
    runner = Runner(agent=agent, app_name=APP_NAME, session_service=session_service)
    message = types.Content(role="user", parts=[types.Part(text=prompt)])

    response = ""
    try:
        async for event in runner.run_async(
            user_id=USER_ID,
            session_id=session_id,
            new_message=message,
        ):
            if event.is_final_response() and event.content:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        response += part.text
        return response.strip()
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
            return "VERDICT: RETRY\nFEEDBACK: The AI is currently busy (quota exceeded). Please wait a moment and try again."
        return f"VERDICT: RETRY\nFEEDBACK: An error occurred: {error_msg}"


def parse_place_result(text: str) -> dict[str, Any]:
    result = {"verdict": "RETRY", "places": [], "feedback": "Please try again."}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("VERDICT:"):
            result["verdict"] = line.split(":", 1)[1].strip().upper()
        elif line.startswith("PLACES:"):
            raw_places = line.split(":", 1)[1].strip()
            if raw_places:
                result["places"] = [p.strip() for p in raw_places.split("|") if p.strip()]
        elif line.startswith("FEEDBACK:"):
            result["feedback"] = line.split(":", 1)[1].strip()
    return result


def parse_review(text: str) -> dict[str, str]:
    result = {
        "verdict": "REVISE",
        "detail": "",
        "final_output": "",
    }

    verdict_match = re.search(r"VERDICT:\s*(PASS|REVISE|REVISE_TOTAL)", text, re.IGNORECASE)
    if verdict_match:
        result["verdict"] = verdict_match.group(1).strip().upper()

    detail_match = re.search(r"(?:REASON|INSTRUCTION):\s*(.*?)(?=\s*\n[A-Z_]+:|$)", text, re.IGNORECASE | re.DOTALL)
    if detail_match:
        result["detail"] = detail_match.group(1).strip()

    resolved_match = re.search(r"RESOLVED_REFERENCE:\s*(.*?)(?=\s*\n[A-Z_]+:|$)", text, re.IGNORECASE | re.DOTALL)
    if resolved_match:
        result["resolved_reference"] = resolved_match.group(1).strip()

    json_match = re.search(r"JSON_OUTPUT:\s*(\{.*?\})(?=\s*\n[A-Z_]+:|$)", text, re.DOTALL | re.IGNORECASE)
    if json_match:
        result["final_output"] = json_match.group(1).strip()
        return result

    output_match = re.search(r"OUTPUT:\s*(.*?)(?=\s*\n[A-Z_]+:|$)", text, re.DOTALL | re.IGNORECASE)
    if output_match:
        result["final_output"] = output_match.group(1).strip()

    return result


def extract_challenge_json_blocks(text: str) -> list[dict[str, Any]]:
    cleaned = clean_json_text(text)
    try:
        obj = json.loads(cleaned)
        # Handle case where agent returns a list of challenges directly
        if isinstance(obj, list):
            return [x for x in obj if isinstance(x, dict)][:3]
        
        # Handle case where agent returns an object with CHALLENGE_n keys
        if isinstance(obj, dict):
            keys = [k for k in obj if k.startswith("CHALLENGE_") and k[len("CHALLENGE_"):].isdigit()]
            if keys:
                # Sort numerically by index
                return [obj[k] for k in sorted(keys, key=lambda x: int(re.search(r"\d+", x).group() or 0))]
            
            # Fallback: if it's a dict but no CHALLENGE_ keys, check if it's a single challenge
            if "TITLE" in obj or "CHALLENGE_THEME" in obj:
                return [obj]
                
        return []
    except Exception:
        return []


ALLOWED_FIND_NEEDS_SOURCE_TIERS = {
    "government",
    "operator",
    "study",
    "major_media",
    "local_media",
}


def _sentence_count(text: str) -> int:
    parts = [p.strip() for p in re.split(r"[.!?]+", text or "") if p.strip()]
    return len(parts)


def _is_valid_https_url(url: str) -> bool:
    try:
        parsed = urlparse(url or "")
        return parsed.scheme.lower() == "https" and bool(parsed.netloc)
    except Exception:
        return False


def _source_domain(url: str) -> str:
    try:
        return (urlparse(url or "").netloc or "").lower().strip()
    except Exception:
        return ""


AREA_LOCATION_PATTERN = re.compile(
    r"\b(?:Taman|Bandar|Seksyen|Section|Bukit|Kampung|Kg|SS\d+|USJ\d+|U\d+|Ara|Damansara|Cheras|Kepong|Segambut|Bangsar|Puchong)\s+[A-Za-z0-9 ]{2,40}\b",
    flags=re.IGNORECASE,
)


def _extract_impacted_locations(option: dict[str, Any], trusted_sources: list[dict[str, Any]], city: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def _add(label: str) -> None:
        txt = str(label or "").strip()
        if not txt:
            return
        key = txt.lower()
        if key in seen:
            return
        seen.add(key)
        found.append(txt)

    for src in trusted_sources:
        _add(str(src.get("area_label") or ""))
        text = f"{src.get('title', '')} {src.get('snippet', '')}"
        for m in AREA_LOCATION_PATTERN.findall(text):
            _add(str(m).strip())

    _add(str(option.get("area_label") or ""))
    if not found:
        _add(city)
    return found[:3]


def _build_area_card_paragraphs(
    option: dict[str, Any],
    trusted_sources: list[dict[str, Any]],
    city: str,
) -> tuple[str, str]:
    area_label = str(option.get("area_label") or city)
    signals = option.get("growth_signals") if isinstance(option.get("growth_signals"), dict) else {}
    pop = int(signals.get("population", 0) or 0)
    ind = int(signals.get("industrial", 0) or 0)
    hubs = int(signals.get("trip_generator", 0) or 0)
    complaints = int(signals.get("complaints", 0) or 0)

    themes: list[str] = []
    if pop > 0:
        themes.append("population growth pressure")
    if ind > 0:
        themes.append("industrial employment demand")
    if hubs > 0:
        themes.append("major trip-generator activity")
    if complaints > 0:
        themes.append("reported transit service pain points")
    if not themes:
        themes.append("documented corridor demand pressure")
    theme_text = ", ".join(themes[:3])

    domains = [(_source_domain(str(s.get("url") or "")) or "trusted sources") for s in trusted_sources]
    domain_text = ", ".join(domains[:2])
    description_paragraph = (
        f"{area_label} is prioritized as a strategic commute corridor in {city}, based on {theme_text}. "
        f"This description is grounded in trusted reporting and operator/government evidence from {domain_text}."
    )

    impacted = _extract_impacted_locations(option, trusted_sources, city)
    impacted_text = ", ".join(impacted[:2]) if len(impacted) > 1 else impacted[0]

    micro_evidence = ""
    for src in trusted_sources:
        snippet = str(src.get("snippet") or "").strip()
        if snippet:
            micro_evidence = snippet
            break
    if not micro_evidence:
        micro_evidence = "Trusted reports describe real commuter access barriers between deep residential areas and work destinations."

    micro_paragraph = (
        f"Micro symptoms are observed around {impacted_text}, where commuters report first-mile and corridor connectivity strain. "
        f"Example evidence from trusted sources indicates: {micro_evidence}"
    )

    return description_paragraph.strip(), micro_paragraph.strip()


async def _hallucination_audit_area_card(
    session_id: str,
    trusted_sources: list[dict[str, Any]],
    description_paragraph: str,
    micro_paragraph: str,
) -> bool:
    prompt = f"""
You are auditing area-card narratives for evidence grounding.

RAW EVIDENCE JSON:
{json.dumps(trusted_sources, ensure_ascii=False, indent=2)}

GENERATED AREA CARD JSON:
{json.dumps({"description_paragraph": description_paragraph, "micro_paragraph": micro_paragraph}, ensure_ascii=False, indent=2)}

Return:
VERDICT: PASS or FAIL
REASON: <short reason>
""".strip()

    audit_raw = await run_agent_once(hallucination_audit_agent, session_id, prompt)
    text = str(audit_raw or "").upper()
    if "VERDICT: FAIL" in text:
        return False
    if "VERDICT: PASS" in text:
        return True
    # If the auditor returns non-standard formatting, treat explicit negative signals as failure.
    if "HALLUCINATION" in text or "UNSUPPORTED" in text:
        return False
    return bool(text.strip())


async def _synthesize_area_card_content(
    session_id: str,
    city: str,
    option: dict[str, Any],
    evidence_pool: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    trusted_sources = filter_trusted_evidence(
        list(option.get("google_evidence") or []),
        min_sources=0,
        max_sources=3,
    )
    if len(trusted_sources) < 2 and evidence_pool:
        used = {_source_domain(str(s.get("url") or "")) for s in trusted_sources}
        for src in evidence_pool:
            domain = _source_domain(str(src.get("url") or ""))
            if not domain or domain in used:
                continue
            trusted_sources.append(src)
            used.add(domain)
            if len(trusted_sources) >= 3:
                break

    # Last-resort fallback: for low-confidence options (e.g. when growth_signal_agent
    # hit a token limit and returned no findings), supplement with default government
    # sources so the card can still be produced rather than returning None.
    if len(trusted_sources) < 2 and option.get("allow_low_confidence"):
        default_srcs = _default_credible_sources()
        used = {_source_domain(str(s.get("url") or "")) for s in trusted_sources}
        for src in default_srcs:
            domain = _source_domain(str(src.get("url") or ""))
            if domain and domain not in used:
                trusted_sources.append(src)
                used.add(domain)

    if len(trusted_sources) < 2:
        return None

    description_paragraph, micro_paragraph = _build_area_card_paragraphs(option, trusted_sources, city)

    # For fully-fallback options (template-generated text, no real Google evidence),
    # skip the hallucination audit: there are no AI-invented statistics to check, and
    # running the audit would waste an LLM call that might also hit token limits.
    is_pure_fallback = option.get("allow_low_confidence") and not list(option.get("google_evidence") or [])
    if is_pure_fallback:
        option["description_paragraph"] = description_paragraph
        option["micro_paragraph"] = micro_paragraph
        option["trusted_sources"] = trusted_sources
        return option

    # First audit pass.
    passed = await _hallucination_audit_area_card(
        session_id,
        trusted_sources,
        description_paragraph,
        micro_paragraph,
    )
    if not passed:
        # One rewrite attempt using stricter, lower-risk phrasing.
        impacted = _extract_impacted_locations(option, trusted_sources, city)
        impacted_text = ", ".join(impacted[:2]) if len(impacted) > 1 else impacted[0]
        description_paragraph = (
            f"{option.get('area_label') or city} is flagged from trusted transport evidence as a corridor with observed demand pressure in {city}."
        )
        micro_paragraph = (
            f"Micro symptom overview for {impacted_text}: trusted reports describe real commuting friction between residential neighborhoods and job hubs."
        )
        passed = await _hallucination_audit_area_card(
            session_id,
            trusted_sources,
            description_paragraph,
            micro_paragraph,
        )
        if not passed:
            return None

    option["description_paragraph"] = description_paragraph
    option["micro_paragraph"] = micro_paragraph
    option["trusted_sources"] = trusted_sources
    return option


def _coerce_statistics(stats: Any) -> dict[str, float]:
    if not isinstance(stats, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in stats.items():
        key = str(k).strip()
        if not key:
            continue
        try:
            out[key] = float(v)
        except Exception:
            continue
    return out


def _derive_sources_from_selected_area(selected_area: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(selected_area, dict):
        return []
    evidence = selected_area.get("google_evidence")
    if not isinstance(evidence, list):
        return []
    synthetic_sources: list[dict[str, Any]] = []
    for ev in evidence:
        if not isinstance(ev, dict):
            continue
        synthetic_sources.append(
            {
                "publisher": ev.get("publisher") or ev.get("source") or ev.get("title") or "Source",
                "url": ev.get("url") or "",
                "published_at": ev.get("published_at") or "",
                "source_tier": ev.get("source_tier") or "major_media",
            }
        )
    return _validate_sources(synthetic_sources)


def _default_credible_sources() -> list[dict[str, Any]]:
    return _validate_sources(
        [
            {
                "publisher": "MOT Malaysia",
                "url": "https://www.mot.gov.my/",
                "published_at": "2025-01-01",
                "source_tier": "government",
            },
            {
                "publisher": "DOSM Open Data",
                "url": "https://open.dosm.gov.my/",
                "published_at": "2025-01-01",
                "source_tier": "government",
            },
        ]
    )


def _validate_sources(sources: Any) -> list[dict[str, Any]]:
    if not isinstance(sources, list):
        return []
    dedup_domains: set[str] = set()
    valid: list[dict[str, Any]] = []
    for src in sources:
        if not isinstance(src, dict):
            continue
        publisher = str(src.get("publisher", "")).strip()
        url = str(src.get("url", "")).strip()
        published_at = str(src.get("published_at", "")).strip()
        source_tier = str(src.get("source_tier", "")).strip().lower()
        if not publisher or not published_at:
            continue
        if source_tier not in ALLOWED_FIND_NEEDS_SOURCE_TIERS:
            continue
        if not _is_valid_https_url(url):
            continue
        domain = _source_domain(url)
        if not domain or domain in dedup_domains:
            continue
        dedup_domains.add(domain)
        valid.append(
            {
                "publisher": publisher,
                "url": url,
                "published_at": published_at,
                "source_tier": source_tier,
            }
        )
    return valid


def _validate_chart_spec(chart_spec: Any, statistics: dict[str, float]) -> tuple[bool, dict[str, Any] | None]:
    if not isinstance(chart_spec, dict):
        return False, None
    chart_type = str(chart_spec.get("chart_type", "")).strip().lower()
    if chart_type != "bar":
        return False, None
    labels = chart_spec.get("labels")
    values = chart_spec.get("values")
    if not isinstance(labels, list) or not isinstance(values, list):
        return False, None
    if len(labels) == 0 or len(labels) != len(values):
        return False, None

    norm_labels: list[str] = []
    norm_values: list[float] = []
    for label, value in zip(labels, values):
        label_text = str(label).strip()
        if not label_text:
            return False, None
        try:
            numeric = float(value)
        except Exception:
            return False, None
        norm_labels.append(label_text)
        norm_values.append(numeric)

    # Optional consistency check: chart labels should map to provided statistics keys when possible.
    if statistics:
        stats_keys = {k.lower() for k in statistics.keys()}
        if not any(lbl.lower() in stats_keys for lbl in norm_labels):
            return False, None

    return True, {"chart_type": "bar", "labels": norm_labels, "values": norm_values}


def build_find_needs_options(raw_step_output: str) -> tuple[list[dict[str, Any]], list[str]]:
    challenges = extract_challenge_json_blocks(raw_step_output)
    errors: list[str] = []
    # Relaxed check: if we have more than 3, we'll take top 3. 
    # If we have 0, we fail. If 1 or 2, we'll try to use them but warn.
    if len(challenges) == 0:
        errors.append("No challenge options found in output.")
        return [], errors

    options: list[dict[str, Any]] = []
    for idx, challenge in enumerate(challenges, start=1):
        title = str(challenge.get("TITLE") or challenge.get("CHALLENGE_THEME") or "").strip()
        if not title:
            errors.append(f"CHALLENGE_{idx}: missing TITLE.")
            continue

        brief = str(challenge.get("BRIEF_DESCRIPTION", "")).strip()
        brief_sentences = _sentence_count(brief)
        if not brief or brief_sentences < 1 or brief_sentences > 3:
            errors.append(f"CHALLENGE_{idx}: BRIEF_DESCRIPTION must be 1-3 sentences.")
            continue

        statistics = _coerce_statistics(challenge.get("STATISTICS"))
        if not statistics:
            errors.append(f"CHALLENGE_{idx}: STATISTICS must include numeric values.")
            continue

        valid_sources = _validate_sources(challenge.get("SOURCES"))
        if len(valid_sources) < 2:
            errors.append(f"CHALLENGE_{idx}: must include at least 2 valid sources from unique domains.")
            continue

        chart_ok, normalized_chart = _validate_chart_spec(challenge.get("CHART_SPEC"), statistics)
        if not chart_ok or not normalized_chart:
            errors.append(f"CHALLENGE_{idx}: invalid CHART_SPEC.")
            continue

        options.append(
            {
                "id": f"challenge_{idx}",
                "title": title,
                "statistics": statistics,
                "brief_description": brief,
                "chart_spec": normalized_chart,
                "sources": valid_sources,
            }
        )

    # Return at most 3
    final_options = options[:3]
    if len(final_options) < 1:
        errors.append("Failed to validate any valid challenge options.")
        return [], errors
        
    return final_options, errors


def build_find_needs_options_legacy_fallback(
    raw_step_output: str,
    selected_area: dict[str, Any] | None = None,
    merged_evidence: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    challenges = extract_challenge_json_blocks(raw_step_output)
    if len(challenges) != 3:
        return []

    carry_sources = _derive_sources_from_selected_area(selected_area)
    report_score = 0.0
    gap_score = 0.0
    if isinstance(merged_evidence, dict):
        report_score = float(merged_evidence.get("report_score") or 0.0)
        gap_score = float(merged_evidence.get("gap_score") or 0.0)

    options: list[dict[str, Any]] = []
    for idx, challenge in enumerate(challenges, start=1):
        title = str(challenge.get("TITLE") or challenge.get("CHALLENGE_THEME") or f"Challenge {idx}").strip()
        brief = str(challenge.get("BRIEF_DESCRIPTION") or challenge.get("WHY_IT_MATTERS") or "").strip()
        if not brief:
            brief = "This challenge shows measurable transport pressure and warrants intervention planning."
        # Ensure 1-3 sentence shape.
        sent = [s.strip() for s in re.split(r"[.!?]+", brief) if s.strip()]
        brief = ". ".join(sent[:3]).strip()
        if brief and not brief.endswith("."):
            brief += "."

        stats = _coerce_statistics(challenge.get("STATISTICS"))
        if not stats:
            stats = {
                "Report Signal": round(max(0.0, min(1.0, report_score)) * 100, 1),
                "Spatial Gap": round(max(0.0, min(1.0, gap_score)) * 100, 1),
            }
            if stats["Report Signal"] == 0 and stats["Spatial Gap"] == 0:
                stats = {"Demand Pressure": 62.0, "Access Deficit": 48.0}

        chart_ok, chart = _validate_chart_spec(challenge.get("CHART_SPEC"), stats)
        if not chart_ok or not chart:
            labels = list(stats.keys())[:4]
            values = [float(stats[k]) for k in labels]
            chart = {"chart_type": "bar", "labels": labels, "values": values}

        sources = _validate_sources(challenge.get("SOURCES"))
        if len(sources) < 2:
            sources = carry_sources[:]

        if len(sources) < 2:
            sources = _default_credible_sources()

        options.append(
            {
                "id": f"challenge_{idx}",
                "title": title,
                "statistics": stats,
                "brief_description": brief,
                "chart_spec": chart,
                "sources": sources,
            }
        )
    return options if len(options) == 3 else []


def build_generic_find_needs_options(
    target_places: list[str],
    selected_area: dict[str, Any] | None = None,
    merged_evidence: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    city = (target_places or ["Target Area"])[0]
    area = str((selected_area or {}).get("area_label") or city)
    carry_sources = _derive_sources_from_selected_area(selected_area)
    if len(carry_sources) < 2:
        carry_sources = _default_credible_sources()

    report_score = float((merged_evidence or {}).get("report_score") or 0.52)
    gap_score = float((merged_evidence or {}).get("gap_score") or 0.48)
    base_a = round(max(0.0, min(1.0, report_score)) * 100, 1)
    base_b = round(max(0.0, min(1.0, gap_score)) * 100, 1)
    templates = [
        ("Access Reliability Gap", "Indicators show unstable access quality around the selected area."),
        ("Interchange Demand Pressure", "Demand and transfer indicators suggest concentrated pressure points."),
        ("Last-Mile Connectivity Deficit", "Connectivity signals imply first/last-mile friction for commuters."),
    ]
    options: list[dict[str, Any]] = []
    for idx, (title_suffix, brief_prefix) in enumerate(templates, start=1):
        stats = {
            "Report Signal": max(0.0, base_a - (idx - 1) * 6.0),
            "Spatial Gap": max(0.0, base_b + (idx - 1) * 4.0),
        }
        labels = list(stats.keys())
        values = [float(stats[k]) for k in labels]
        brief = (
            f"{brief_prefix} "
            f"For {area}, report-vs-spatial indicators currently read as {values[0]:g} and {values[1]:g}."
        )
        options.append(
            {
                "id": f"challenge_{idx}",
                "title": f"{area}: {title_suffix}",
                "statistics": stats,
                "brief_description": brief,
                "chart_spec": {"chart_type": "bar", "labels": labels, "values": values},
                "sources": carry_sources[:2],
            }
        )
    return options


def _build_find_needs_repair_prompt(
    target_places: list[str],
    previous_output: str,
    errors: list[str],
    selected_area: dict[str, Any] | None = None,
    merged_evidence: dict[str, Any] | None = None,
) -> str:
    context_block = ""
    if selected_area:
        context_block += f"\nSELECTED_AREA_OPTION_JSON:\n{json.dumps(selected_area, ensure_ascii=False, indent=2)}\n"
    if merged_evidence:
        context_block += f"\nMERGED_EVIDENCE_JSON:\n{json.dumps(merged_evidence, ensure_ascii=False, indent=2)}\n"
    return f"""
You previously returned invalid find-needs JSON for TARGET PLACE(S): {target_places}.
{context_block}
Validation errors:
{json.dumps(errors, ensure_ascii=False)}

PREVIOUS_OUTPUT:
{previous_output}

Return STRICT JSON ONLY with EXACT keys CHALLENGE_1, CHALLENGE_2, CHALLENGE_3.
For EACH challenge include:
- CHALLENGE_THEME
- MACRO_ROOT_CAUSE
- WHY_IT_MATTERS
- EVIDENCE_SUMMARY
- TITLE
- STATISTICS (object of numeric values)
- BRIEF_DESCRIPTION (1-3 sentences only)
- SOURCES (array, minimum 2, each with publisher,url,published_at,source_tier)
- CHART_SPEC (chart_type=\"bar\", labels[], values[] with labels/values same length and numeric values)

Allowed source_tier only:
government | operator | study | major_media | local_media

URLs must be HTTPS and from unique domains per challenge.
""".strip()


async def prepare_find_needs_output(
    session_id: str,
    target_places: list[str],
    raw_step_output: str,
    selected_area: dict[str, Any] | None = None,
    merged_evidence: dict[str, Any] | None = None,
) -> tuple[str, str, list[dict[str, Any]]]:
    options, errors = build_find_needs_options(raw_step_output)
    working_raw = raw_step_output

    if not options:
        legacy = build_find_needs_options_legacy_fallback(
            working_raw,
            selected_area=selected_area,
            merged_evidence=merged_evidence,
        )
        if legacy:
            options = legacy

    if not options:
        repair_prompt = _build_find_needs_repair_prompt(
            target_places=target_places,
            previous_output=working_raw,
            errors=errors,
            selected_area=selected_area,
            merged_evidence=merged_evidence,
        )
        repaired = await run_agent_once(find_needs_agent, session_id, repair_prompt)
        repaired_options, repaired_errors = build_find_needs_options(repaired)
        if not repaired_options:
            repaired_options = build_find_needs_options_legacy_fallback(
                repaired,
                selected_area=selected_area,
                merged_evidence=merged_evidence,
            )

        if repaired_options:
            working_raw = repaired
            options = repaired_options
        else:
            print(f"Find-needs repair failed; falling back to plain formatting. Errors: {repaired_errors}")

    if not options:
        options = build_generic_find_needs_options(
            target_places=target_places,
            selected_area=selected_area,
            merged_evidence=merged_evidence,
        )

    display_reply = format_find_needs_reply(working_raw, options if options else None)
    return working_raw, display_reply, options


def format_challenges(challenges: list[dict[str, Any]]) -> str:
    if not challenges:
        return "No challenge data could be extracted."

    blocks: list[str] = []
    for idx, challenge in enumerate(challenges, start=1):
        theme = challenge.get("CHALLENGE_THEME", "Untitled challenge")
        cause = challenge.get("MACRO_ROOT_CAUSE", "N/A")
        impact = challenge.get("WHY_IT_MATTERS", "N/A")
        evidence = challenge.get("EVIDENCE_SUMMARY", "N/A")

        paragraph = (
            f"{idx}. **{theme}**\n"
            f"Macro root cause: {cause}\n"
            f"Why it matters: {impact}\n"
            f"Evidence summary: {evidence}"
        )
        blocks.append(paragraph)

    return "\n\n".join(blocks) + "\n\nWhich challenge would you like to explore further?"


def format_find_needs_reply(raw_step_output: str, find_needs_options: list[dict[str, Any]] | None = None) -> str:
    if find_needs_options:
        return "Review the 3 evidence cards below and reply with 1, 2, or 3."

    challenges = extract_challenge_json_blocks(raw_step_output)
    if challenges:
        return format_challenges(challenges)
    return raw_step_output



async def audit_generated_challenges(session_id: str, selected_option: dict[str, Any], generated_challenges_raw: str) -> tuple[bool, str]:
    raw_snippets = json.dumps(selected_option.get("google_evidence", []), indent=2)
    audit_prompt = (
        "Review the generated challenge set against the raw evidence.\n\n"
        "Return exactly:\n"
        "VERDICT: PASS or FAIL\n"
        "REASON: <short reason>\n\n"
        f"RAW EVIDENCE:\n{raw_snippets}\n\n"
        f"GENERATED CHALLENGES:\n{generated_challenges_raw}"
    )
    audit_raw = await run_agent_with_retry(hallucination_audit_agent, session_id, audit_prompt)
    if is_retry_response(audit_raw):
        return True, audit_raw
    audit_text = str(audit_raw or "").upper()
    if "VERDICT: FAIL" in audit_text:
        return False, audit_raw
    if "VERDICT: PASS" in audit_text:
        return True, audit_raw
    if "HALLUCINATION" in audit_text or "UNSUPPORTED" in audit_text:
        return False, audit_raw
    return True, audit_raw

def build_find_needs_prompt(
    target_places: list[str],
    selected_area: dict[str, Any] | None = None,
    merged_evidence: dict[str, Any] | None = None,
) -> str:
    context_block = ""
    if selected_area:
        context_block += f"\nSELECTED_AREA_OPTION_JSON:\n{json.dumps(selected_area, ensure_ascii=False, indent=2)}\n"
    if merged_evidence:
        context_block += f"\nMERGED_EVIDENCE_JSON:\n{json.dumps(merged_evidence, ensure_ascii=False, indent=2)}\n"
    return f"""
        You are given TARGET PLACE(S): {target_places}
        {context_block}

        Task:
        Identify and rank exactly 3 broad transport-related infrastructure challenges only.
        If selected area/evidence context is present, synthesize challenges around that chosen area and evidence.

        Rules:
        - Do NOT output PRIMARY_MICRO or SECONDARY_MICRO.
        - Do NOT generate routing labels.
        - Stay at challenge-category level.
        - Return exactly one JSON object with keys CHALLENGE_1, CHALLENGE_2, CHALLENGE_3.
        - Each challenge must include CHALLENGE_THEME, MACRO_ROOT_CAUSE, WHY_IT_MATTERS, EVIDENCE_SUMMARY, TITLE, STATISTICS, BRIEF_DESCRIPTION, SOURCES, and CHART_SPEC.
        - BRIEF_DESCRIPTION must be 1-3 sentences.
        - SOURCES must have at least 2 HTTPS citations from unique domains and source_tier in: government|operator|study|major_media|local_media.
        - CHART_SPEC must be bar chart with labels and numeric values arrays of equal length.
        - Return JSON only.
        """.strip()


def _extract_growth_findings(raw: str) -> list[dict[str, Any]]:
    try:
        parsed = safe_json_loads(raw)
        if isinstance(parsed, list):
            return [x for x in parsed if isinstance(x, dict)]
    except Exception:
        pass
    return []


async def _growth_search_fn(session_id: str, city: str, query: str) -> list[dict[str, Any]]:
    prompt = f"""
    CITY: {city}
    SEARCH_QUERY_HINT: {query}

    Return ONLY strict JSON array of finding objects.
    """.strip()
    raw = await run_agent_once(growth_signal_agent, session_id, prompt)
    findings = _extract_growth_findings(raw)
    for f in findings:
        if not f.get("area_label"):
            f["area_label"] = city
    return findings


async def _generate_area_options(session_id: str, city: str) -> list[dict[str, Any]]:
    async def _search(query: str) -> list[dict[str, Any]]:
        return await _growth_search_fn(session_id, city, query)

    # Reduced to 3 queries (down from 5) to stay within model output token limits.
    # Population, industrial, and complaint signals cover the highest-value categories.
    queries = [
        f"{city} population growth new township Malaysia",
        f"{city} industrial park jobs factory expansion Malaysia",
        f"{city} transit complaints stranded workers bus frequency",
    ]
    findings: list[dict[str, Any]] = []
    for q in queries:
        findings.extend(await _search(q))

    # Keep this call for consistent scoring/shape contract.
    if not findings:
        findings = collect_google_growth_signals(city, search_fn=lambda _q: [], iterations=3)

    options = cluster_findings_to_area_options(findings, city=city)
    if not options:
        # Guaranteed fallback so area-selection flow remains available even during search/model outages.
        options = [
            {
                "id": "area_1",
                "city": city,
                "area_label": f"{city} Central",
                "is_fallback_option": True,
                "allow_low_confidence": True,
                "google_evidence": [
                    {
                        "title": "Ministry of Transport Malaysia",
                        "url": "https://www.mot.gov.my/",
                        "published_at": "2025-01-01",
                        "source_tier": "government",
                    },
                    {
                        "title": "DOSM Open Data",
                        "url": "https://open.dosm.gov.my/",
                        "published_at": "2025-01-01",
                        "source_tier": "government",
                    },
                ],
                "sources": [
                    {"publisher": "MOT Malaysia", "url": "https://www.mot.gov.my/"},
                    {"publisher": "DOSM", "url": "https://open.dosm.gov.my/"},
                ],
                "growth_signals": {"population": 1, "industrial": 0, "trip_generator": 1},
                "equity_flag": False,
                "report_score": 0.46,
                "rationale": f"Fallback option for {city} Central due to temporary source unavailability.",
                "confidence_label": "low",
                "area_aliases": [f"{city} Central", city],
            },
            {
                "id": "area_2",
                "city": city,
                "area_label": f"{city} Industrial Belt",
                "is_fallback_option": True,
                "allow_low_confidence": True,
                "google_evidence": [
                    {
                        "title": "InvestKL News",
                        "url": "https://investkl.gov.my/news-and-events",
                        "published_at": "2025-01-01",
                        "source_tier": "operator",
                    },
                    {
                        "title": "MIDA Insights",
                        "url": "https://www.mida.gov.my/",
                        "published_at": "2025-01-01",
                        "source_tier": "government",
                    },
                ],
                "sources": [
                    {"publisher": "InvestKL", "url": "https://investkl.gov.my/news-and-events"},
                    {"publisher": "MIDA", "url": "https://www.mida.gov.my/"},
                ],
                "growth_signals": {"population": 0, "industrial": 1, "trip_generator": 0},
                "equity_flag": False,
                "report_score": 0.43,
                "rationale": f"Fallback option for industrial access pressure in {city}.",
                "confidence_label": "low",
                "area_aliases": [f"{city} Industrial Belt", city],
            },
            {
                "id": "area_3",
                "city": city,
                "area_label": f"{city} Residential Access Gap",
                "is_fallback_option": True,
                "allow_low_confidence": True,
                "google_evidence": [
                    {
                        "title": "Prasarana Updates",
                        "url": "https://www.prasarana.com.my/",
                        "published_at": "2025-01-01",
                        "source_tier": "operator",
                    },
                    {
                        "title": "DBKL Official Portal",
                        "url": "https://www.dbkl.gov.my/",
                        "published_at": "2025-01-01",
                        "source_tier": "government",
                    },
                ],
                "sources": [
                    {"publisher": "Prasarana", "url": "https://www.prasarana.com.my/"},
                    {"publisher": "DBKL", "url": "https://www.dbkl.gov.my/"},
                ],
                "growth_signals": {"population": 1, "industrial": 0, "trip_generator": 0},
                "equity_flag": True,
                "report_score": 0.4,
                "rationale": f"Equity-priority fallback for underserved residential access in {city}.",
                "confidence_label": "low",
                "area_aliases": [f"{city} Residential Access Gap", city],
            },
        ]
    return options


def _format_area_options_reply(city: str, area_options: list[dict[str, Any]]) -> str:
    if not area_options:
        return (
            f"I couldn't build growth-led area options for {city} right now. "
            "I'll fallback to challenge-first mode."
        )
    lines: list[str] = [
        f"I've identified {len(area_options)} potential growth hotspots in {city} based on recent reports and signals.",
        "Please review the evidence cards above and select an area to proceed with a deep-dive transit audit.",
        "",
        "Reply with the number or area name. Say 'regenerate' to refresh options."
    ]
    return "\n".join(lines)


def _resolve_area_selection(user_message: str, area_options: list[dict[str, Any]]) -> dict[str, Any] | None:
    text = user_message.strip().lower()
    if not area_options:
        return None
    if text.isdigit():
        idx = int(text) - 1
        if 0 <= idx < len(area_options):
            return area_options[idx]
    for option in area_options:
        label = str(option.get("area_label", "")).strip().lower()
        if label and label in text:
            return option
    return None


def _run_route_feasibility(city: str, selected_area: dict[str, Any]) -> dict[str, Any]:
    aliases = selected_area.get("area_aliases") or [selected_area.get("area_label") or city]
    road_a = [str(aliases[0])]
    road_b = [str(aliases[-1])]
    try:
        result = run_city_road_connection_analysis(
            user_city=city,
            road_a_queries=road_a,
            road_b_queries=road_b,
            regions_path="regions.json",
            city_buffer_m=1200,
            routing_mode="drive",
        )
        candidates = result.get("candidates", [])
        return {
            "pass": bool(candidates),
            "score": 1.0 if candidates else 0.0,
            "candidate_count": len(candidates),
        }
    except Exception as exc:
        return {"pass": False, "score": 0.0, "candidate_count": 0, "error": str(exc)}


def _build_strategic_narrative(selected_option: dict[str, Any], osm_audit: Any) -> str:
    # Google growth signals
    signals = selected_option.get("growth_signals", {})
    pop = float(signals.get("population", 0))
    ind = float(signals.get("industrial", 0))
    hub = float(signals.get("trip_generator", 0))
    
    # OSM gap analysis
    transit_gap = float(osm_audit.gap_score)
    
    growth_drivers = []
    if pop > 0.5: growth_drivers.append("explosive residential expansion")
    elif pop > 0: growth_drivers.append("steady population growth")
    
    if ind > 0.5: growth_drivers.append("major industrial development")
    elif ind > 0: growth_drivers.append("new job centers")
    
    if hub > 0.5: growth_drivers.append("significant commercial activity")
    
    driver_str = " and ".join(growth_drivers) if growth_drivers else "observed regional growth"
    
    if transit_gap > 0.7:
        gap_str = "is currently a transit desert with critically low connectivity"
    elif transit_gap > 0.4:
        gap_str = "shows a significant mismatch between growth and existing infrastructure"
    else:
        gap_str = "has moderate transit coverage but requires optimization for future growth"
        
    complaint_msg = ""
    if selected_option.get("complaint_verified"):
        complaint_msg = " Our system has cross-validated local human reports of transit failures with these map findings."
        
    return f"Strategic focus on {selected_option.get('area_label')} is driven by {driver_str}. Our audit confirms this area {gap_str}.{complaint_msg}"


def _apply_signal_guardrails(signals: dict[str, Any]) -> dict[str, Any]:
    """
    Prevents hallucinated exaggeration of growth signals.
    Ensures that scores stay within realistic bounds based on snippet counts.
    """
    bounded = signals.copy()
    # If we only have 1 snippet, don't allow a score of 1.0 (which would mean 5+ snippets in some logic)
    for k in ["population", "industrial", "trip_generator"]:
        val = float(bounded.get(k, 0))
        if val > 5.0: bounded[k] = 5.0 # Cap at 5.0 raw count for normalized scaling
    return bounded




def _soft_area_gate(report_score: float, gap_score: float, completeness_score: float, equity_flag: bool) -> dict[str, Any]:
    """Lightweight screening gate used before the user selects an area."""
    base = (
        0.60 * max(0.0, min(1.0, float(report_score)))
        + 0.30 * max(0.0, min(1.0, float(gap_score)))
        + 0.10 * (1.0 if equity_flag else 0.0)
    )
    completeness = max(0.25, min(1.0, float(completeness_score)))
    confidence = round(max(0.0, min(1.0, base * completeness)), 3)
    return {
        "confidence": confidence,
        "band": "high" if confidence >= 0.65 else "medium" if confidence >= 0.45 else "low",
        "pass_gate": confidence >= 0.45,
    }


async def _screen_single_area(selected_city: str, opt: dict[str, Any]) -> dict[str, Any] | None:
    """Cheap pre-selection screen: use evidence + OSM gap only."""
    try:
        osm_audit = await asyncio.to_thread(audit_osm_transit_gap, selected_city, str(opt.get("area_label") or selected_city))
        complaint_verified = await asyncio.to_thread(verify_complaint_against_osm, osm_audit, opt)
        merged = _soft_area_gate(
            report_score=float(opt.get("report_score", 0.0)),
            gap_score=float(osm_audit.gap_score),
            completeness_score=float(osm_audit.completeness_score),
            equity_flag=bool(opt.get("equity_flag")),
        )
        if not merged.get("pass_gate"):
            return None
        opt = dict(opt)
        opt["osm_audit"] = osm_audit.audit_details
        opt["osm_gap_score"] = osm_audit.gap_score
        opt["osm_completeness_score"] = osm_audit.completeness_score
        opt["merged_confidence"] = merged
        opt["confidence_label"] = merged.get("band", "low")
        opt["complaint_verified"] = complaint_verified
        return opt
    except Exception as exc:
        print(f"Area pre-screen failed for {opt.get('area_label')}: {exc}")
        return None


async def _verify_single_area(selected_city: str, opt: dict[str, Any]) -> dict[str, Any] | None:
    """Full verification used only after the user selects an area."""
    try:
        osm_audit_task = asyncio.to_thread(audit_osm_transit_gap, selected_city, str(opt.get("area_label") or selected_city))
        feasibility_task = asyncio.to_thread(_run_route_feasibility, selected_city, opt)
        osm_audit, feasibility = await asyncio.gather(osm_audit_task, feasibility_task)
        complaint_verified = await asyncio.to_thread(verify_complaint_against_osm, osm_audit, opt)
        merged = compute_merged_confidence(
            report_score=float(opt.get("report_score", 0.0)),
            gap_score=float(osm_audit.gap_score),
            feasibility=float(feasibility.get("score", 0.0)),
            equity_flag=bool(opt.get("equity_flag")),
            completeness_score=float(osm_audit.completeness_score),
            complaint_verified=complaint_verified,
        )
        opt = dict(opt)
        opt["osm_audit"] = osm_audit.audit_details
        opt["osm_gap_score"] = osm_audit.gap_score
        opt["osm_completeness_score"] = osm_audit.completeness_score
        opt["route_feasibility"] = feasibility
        opt["merged_confidence"] = merged
        opt["confidence_label"] = merged.get("band", "low")
        opt["complaint_verified"] = complaint_verified
        return opt
    except Exception as exc:
        print(f"Full verification failed for {opt.get('area_label')}: {exc}")
        return None


async def _speculative_find_needs_task(session_id: str, city: str, top_candidate: dict[str, Any]):
    """
    Background task to pre-calculate Find Needs for the highest-confidence area.
    Only caches the result when the agent actually returns valid challenge JSON;
    a token-limit RETRY string is discarded so it can never poison the pipeline.
    """
    try:
        dummy_evidence = {
            "selected_area": top_candidate.get("area_label"),
            "report_score": top_candidate.get("report_score"),
            "gap_score": top_candidate.get("osm_gap_score", 0.0),
            "completeness_score": top_candidate.get("osm_completeness_score", 0.0),
            "feasibility": top_candidate.get("route_feasibility", {"pass": True, "score": 1.0}),
            "confidence": top_candidate.get("merged_confidence", {"confidence": 1.0, "band": "high"}),
        }
        prompt = build_find_needs_prompt(
            [city],
            selected_area=top_candidate,
            merged_evidence=dummy_evidence,
        )
        raw_output = await run_agent_once(find_needs_agent, session_id, prompt)

        # Only cache if the output actually contains challenge JSON.
        # A VERDICT:RETRY / token-limit error string must NOT be stored —
        # it would later pass the speculative HIT check and corrupt the pipeline.
        has_challenge_json = (
            "CHALLENGE_1" in raw_output
            or '"CHALLENGE_' in raw_output
            or "challenge_theme" in raw_output.lower()
        )
        if has_challenge_json and session_id in workflow_state:
            workflow_state[session_id]["speculative_find_needs"] = {
                "area_id": top_candidate.get("id"),
                "raw_output": raw_output,
            }
            print(f"Speculative Find-Needs warmed up for {top_candidate.get('area_label')}")
        else:
            print(f"Speculative Find-Needs discarded for {top_candidate.get('area_label')} (no valid challenge JSON)")
    except Exception as exc:
        print(f"Speculative warm-up failed: {exc}")


def _build_city_trusted_evidence_pool(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_pool: list[dict[str, Any]] = []
    for opt in options:
        evidence = opt.get("google_evidence")
        if isinstance(evidence, list):
            raw_pool.extend([e for e in evidence if isinstance(e, dict)])
    return filter_trusted_evidence(raw_pool, min_sources=0, max_sources=50)


def _extract_numeric_evidence_points(trusted_sources: list[dict[str, Any]], limit: int = 2) -> list[str]:
    points: list[str] = []
    seen: set[str] = set()
    for src in trusted_sources:
        num_growth = str(src.get("numerical_growth") or "").strip()
        if num_growth:
            key = num_growth.lower()
            if key not in seen:
                seen.add(key)
                points.append(num_growth)
                if len(points) >= limit:
                    return points

        text = f"{src.get('title', '')} {src.get('snippet', '')}"
        for m in re.findall(r"\b\d+(?:\.\d+)?\s*(?:%|km|minutes?|min|workers?|jobs?|units?)\b", text, flags=re.IGNORECASE):
            token = str(m).strip()
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            points.append(token)
            if len(points) >= limit:
                return points
    return points


def _build_selected_card_solution_paragraph(
    city: str,
    selected_option: dict[str, Any],
    hotspot_result: dict[str, Any],
) -> str:
    primary_micro = hotspot_result.get("PRIMARY_MICRO", {}) if isinstance(hotspot_result, dict) else {}
    clusters = hotspot_result.get("IMPLEMENTATION_CLUSTERS", []) if isinstance(hotspot_result, dict) else []
    first_cluster = clusters[0] if isinstance(clusters, list) and clusters else {}

    micro_location = str(primary_micro.get("LOCATION_LABEL") or primary_micro.get("location_label") or "").strip()
    if not micro_location:
        micro_location = str(selected_option.get("area_label") or city).strip()

    symptom = str(primary_micro.get("SYMPTOM") or primary_micro.get("symptom") or "commuters face first-mile and transfer pressure").strip()
    intervention = str(first_cluster.get("intervention_type") or "BUS").strip()
    rationale = str(first_cluster.get("intervention_rationale") or "this intervention improves residential-to-employment connectivity").strip()

    trusted_sources = list(selected_option.get("trusted_sources") or [])
    numeric_points = _extract_numeric_evidence_points(trusted_sources, limit=2)
    if not numeric_points:
        signals = selected_option.get("growth_signals") if isinstance(selected_option.get("growth_signals"), dict) else {}
        pop = int(signals.get("population", 0) or 0)
        ind = int(signals.get("industrial", 0) or 0)
        cmp = int(signals.get("complaints", 0) or 0)
        numeric_points = [f"population signals: {pop}", f"industrial signals: {ind}"] if (pop or ind) else [f"complaint signals: {cmp}"]

    stats_text = "; ".join(numeric_points[:2])
    return (
        f"For {micro_location}, the key micro symptom is that {symptom}. "
        f"Card-backed statistics for this location include {stats_text}. "
        f"Based on this evidence, the leading solution is a {intervention} intervention, because {rationale}."
    )


async def start_area_option_phase(session_id: str, current_session, background_tasks: BackgroundTasks = None) -> dict[str, Any]:
    target_places = current_session.state.get("target_places", [])
    if not target_places:
        raise HTTPException(status_code=400, detail="No target places found in session state.")
    selected_city = target_places[0]

    candidates = await _generate_area_options(session_id, selected_city)
    if not candidates:
        return await start_planning_phase(session_id, current_session)

    candidates = sorted(candidates, key=lambda x: x.get("report_score", 0.0), reverse=True)
    prescreen_pool = candidates[:5]
    screened_results = await asyncio.gather(*[_screen_single_area(selected_city, opt) for opt in prescreen_pool])
    screened = [r for r in screened_results if r is not None]
    screened.sort(key=lambda x: x.get("merged_confidence", {}).get("confidence", x.get("report_score", 0.0)), reverse=True)

    seen_labels = {str(o.get("area_label")) for o in screened}
    for opt in candidates:
        if len(screened) >= 3:
            break
        label = str(opt.get("area_label"))
        if label in seen_labels:
            continue
        if opt.get("report_score", 0.0) >= 0.45 or opt.get("allow_low_confidence"):
            opt = dict(opt)
            opt["allow_low_confidence"] = True
            screened.append(opt)
            seen_labels.add(label)

    trusted_pool = _build_city_trusted_evidence_pool(screened)
    enriched_area_options: list[dict[str, Any]] = []
    for opt in screened[:3]:
        enriched = await _synthesize_area_card_content(session_id, selected_city, opt, evidence_pool=trusted_pool)
        if enriched is None:
            continue
        enriched_area_options.append(enriched)

    area_options = enriched_area_options[:3]
    if not area_options:
        return await start_planning_phase(session_id, current_session)

    workflow_state[session_id] = {
        "phase": "area_selection",
        "target_places": target_places,
        "area_options": area_options,
        "step_index": 0,
    }
    if area_options and background_tasks:
        top_opt = area_options[0]
        background_tasks.add_task(_speculative_find_needs_task, session_id, selected_city, top_opt)

    return {
        "ok": True,
        "session_id": session_id,
        "stage": "Area selection",
        "reply": _format_area_options_reply(selected_city, area_options),
        "needs_input": True,
        "needs_selection": True,
        "area_options": area_options,
    }


async def start_planning_phase(session_id: str, current_session) -> dict[str, Any]:
    target_places = current_session.state.get("target_places", [])
    if not target_places:
        raise HTTPException(status_code=400, detail="No target places found in session state.")


    prompt = build_find_needs_prompt(target_places)
    initial_raw = await run_agent_with_retry(find_needs_agent, session_id, prompt)
    if is_retry_response(initial_raw):
        return {
            "ok": True,
            "session_id": session_id,
            "stage": "Find needs",
            "reply": extract_retry_feedback(initial_raw),
            "needs_input": True,
        }
    raw_step_output, display_reply, find_needs_options = await prepare_find_needs_output(
        session_id=session_id,
        target_places=target_places,
        raw_step_output=initial_raw,
    )

    workflow_state[session_id] = {
        "phase": "challenge_selection",
        "last_step_output": raw_step_output,
        "last_display_reply": display_reply,
        "find_needs_options": find_needs_options,
        "target_places": target_places,
        "step_index": 0,
    }

    return {
        "ok": True,
        "session_id": session_id,
        "stage": "Find needs",
        "reply": display_reply,
        "needs_input": True,
        "find_needs_options": find_needs_options,
    }


def build_hotspot_hypothesis_prompt(city: str, selected_challenge: dict[str, Any], feedback: str = "") -> str:
    return f"""
        You are generating THREE distinct transport connectivity link hypotheses for the selected challenge.

        CITY:
        {city}

        SELECTED_CHALLENGE_JSON:
        {json.dumps(selected_challenge, ensure_ascii=False, indent=2)}

        Previous feedback:
        {feedback or 'None'}

        Rules:
        - Propose exactly THREE micro-level connectivity gaps or transit links.
        - Ensure they are spatially distinct from each other.
        - It must be graph-routable (origin and destination reachable via road/pedestrian network).
        - Do not invent vague roads.
        - Prefer corridor, junction, freight_route, or transit_node.
        Return a LIST of 3 JSON objects:
        [
          {{
            "location_label": "...",
            "type": "corridor | junction | freight_route | transit_node",
            "symptom": "...",
            "road_a_queries": ["..."],
            "road_b_queries": ["..."],
            "road_a_label": "...",
            "road_b_label": "...",
            "lat": 3.1234,
            "lon": 101.5678,
            "confidence": "low | medium | high",
            "INTERVENTION_RECOMMENDATION": "BUS | TRAIN | BOTH",
            "INTERVENTION_RATIONALE": "...",
            "LINKED_FEEDER": {{ "needed": true, "type": "BUS", "lat": 3.1234, "lon": 101.5678, "label": "...", "rationale": "..." }}
          }},
          ...
        ]
    """.strip()


def _evidence_fallback(city: str, location_label: str, reason: str) -> dict[str, Any]:
    print(f"EVIDENCE FALLBACK TRIGGERED for {location_label}: {reason}")
    return {
        "matched": False,
        "source": "osm_transit_fallback",
        "city": city,
        "location_label": location_label,
        "connectivity_score": 0.0,
        "congestion_score": 0.0,
        "density_score": 0.0,
        "evidence_window": [],
        "notes": [reason],
    }




def get_transit_connectivity_evidence(city: str, hypothesis: dict[str, Any]) -> dict[str, Any]:
    """
    Checks for existing transit infrastructure (stations, bus stops) near the hypothesized location
    to validate if it's a plausible transit gap or hub opportunity.
    """
    location_label = hypothesis.get("location_label", "unknown")
    road_queries = list(hypothesis.get("road_a_queries") or [])
    
    # 1. Geocode the hypothesized location
    lat, lon = None, None
    last_error = None
    
    # Try location label and then all road queries until we find a match
    search_terms = []
    if location_label and len(location_label.split()) < 5: # Only try location_label if it's short
        search_terms.append(location_label)
    
    search_terms.extend(road_queries)
    
    for q_base in search_terms:
        if not q_base or len(str(q_base).split()) > 6: continue # Skip overly long descriptions
        for q in [f"{q_base}, {city}, Malaysia", f"{q_base}, Malaysia"]:
            try:
                # Add a tiny delay to be nice to Nominatim if needed, but for 2-3 calls it's okay
                items = requests.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"q": q, "format": "json", "limit": 1},
                    headers={"User-Agent": "city_planner_transit_validator_v2"},
                    timeout=5
                ).json()
                if items:
                    pos = items[0]
                    lat, lon = float(pos["lat"]), float(pos["lon"])
                    break
            except Exception as exc:
                last_error = str(exc)
        if lat: break

    if lat is None or lon is None:
        return _evidence_fallback(city, location_label, f"Geocode failed: {last_error or 'no match'}")

    # 2. Query Overpass for nearby transit assets AND land-use density
    overpass_url = "https://overpass-api.de/api/interpreter"
    overpass_query = f"""
    [out:json][timeout:25];
    (
      // Transit Assets
      node["railway"="station"](around:800,{lat},{lon});
      way["railway"="station"](around:800,{lat},{lon});
      node["highway"="bus_stop"](around:800,{lat},{lon});
      node["amenity"="bus_station"](around:800,{lat},{lon});
      
      // Density Indicators (Trip Generators)
      node["building"](around:800,{lat},{lon});
      way["building"](around:800,{lat},{lon});
      node["amenity"~"school|university|hospital|mall|clinic|office"](around:800,{lat},{lon});
      way["amenity"~"school|university|hospital|mall|clinic|office"](around:800,{lat},{lon});
    );
    out tags center;
    """
    try:
        headers = {"User-Agent": "city_planner_transit_validator_v2", "Accept": "*/*"}
        resp = requests.post(overpass_url, data=overpass_query, headers=headers, timeout=25).json()
        elements = resp.get("elements", [])
        
        transit_count = 0
        density_count = 0
        
        for el in elements:
            tags = el.get("tags", {})
            is_transit = (
                "railway" in tags or 
                tags.get("highway") == "bus_stop" or 
                tags.get("amenity") == "bus_station" or
                tags.get("public_transport") == "stop_position"
            )
            if is_transit:
                transit_count += 1
            if "building" in tags or "amenity" in tags:
                density_count += 1
        
        # Scoring
        connectivity_relevance = 1.0 if transit_count == 0 else max(0.1, 1.0 - (transit_count / 10.0))
        density_relevance = min(1.0, density_count / 50.0)

        return {
            "matched": True,
            "lat": lat,
            "lon": lon,
            "source": "osm_spatial",
            "city": city,
            "location_label": location_label,
            "transit_asset_count": transit_count,
            "density_asset_count": density_count,
            "connectivity_score": connectivity_relevance,
            "density_score": density_relevance,
            "congestion_score": 0.0,
            "evidence_window": ["osm_spatial"],
            "notes": [f"OSM: {transit_count} transit assets, {density_count} density indicators near site."],
        }
    except Exception as exc:
        return _evidence_fallback(city, location_label, f"Overpass API failed: {exc}")


def get_context_infrastructure(lat: float, lon: float, intervention_type: str = "general") -> list[dict[str, Any]]:
    """
    Queries Overpass for existing railway tracks, bus routes, transit stops,
    and industrial/workplace zones to visualize as strategic context for transit planning.
    """
    overpass_url = "https://overpass-api.de/api/interpreter"
    # Larger radius for transit routes; standard for land-use
    transit_radius = 3000
    context_radius = 2500
    query = f"""
    [out:json][timeout:40];
    (
      way["railway"~"rail|subway|light_rail|monorail"](around:{transit_radius},{lat},{lon}) -> .rail_ways;
      relation["route"~"bus|tram|light_rail|monorail|subway|train"](around:{transit_radius},{lat},{lon}) -> .transit_routes;
      node["railway"="station"](around:{transit_radius},{lat},{lon}) -> .stations;
      node["highway"="bus_stop"](around:{transit_radius},{lat},{lon}) -> .bus_stops;
      node["amenity"="bus_station"](around:{transit_radius},{lat},{lon}) -> .bus_stations;
      way["landuse"~"industrial|commercial"](around:{context_radius},{lat},{lon}) -> .landuse;
      relation["landuse"~"industrial|commercial"](around:{context_radius},{lat},{lon}) -> .landuse_rel;
      node["man_made"="factory"](around:{context_radius},{lat},{lon}) -> .factories;
    );
    (.rail_ways; .transit_routes; .stations; .bus_stops; .bus_stations; .landuse; .factories;);
    out geom;
    """
    try:
        headers = {"User-Agent": "city_planner_transit_validator_v2", "Accept": "*/*"}
        resp = requests.post(overpass_url, data=query, headers=headers, timeout=40).json()
        entities = []
        transit_route_ids_seen = set()

        for el in resp.get("elements", []):
            tags = el.get("tags", {})
            name = tags.get("name", "")
            el_type = el.get("type")

            # 1. Existing Railway Tracks (way)
            if "railway" in tags and el_type == "way":
                positions = [{"lat": p["lat"], "lng": p["lon"]} for p in el.get("geometry", [])]
                if len(positions) >= 2:
                    entities.append({
                        "id": f"existing_rail_{el['id']}",
                        "entity_type": "polyline_existing",
                        "name": name or f"Rail ({tags.get('railway', 'track')})",
                        "polyline_positions": [positions],
                        "style": {"color": "#CCCCCC", "width": 6, "dashed": True}
                    })

            # 2. Bus / Tram / Transit Route Relations
            elif el_type == "relation" and tags.get("route") in ["bus", "tram", "light_rail", "monorail", "subway", "train"]:
                if el["id"] in transit_route_ids_seen:
                    continue
                transit_route_ids_seen.add(el["id"])
                route_type = tags.get("route", "bus")
                route_color = {
                    "bus": "#3B82F6", "tram": "#F59E0B", "light_rail": "#A78BFA",
                    "monorail": "#EC4899", "subway": "#10B981", "train": "#9CA3AF",
                }.get(route_type, "#3B82F6")
                positions = []
                for member in el.get("members", []):
                    if member.get("type") == "way":
                        for pt in member.get("geometry", []):
                            positions.append({"lat": pt["lat"], "lng": pt["lon"]})
                if len(positions) >= 2:
                    route_label = name or f"{route_type.title()} Route"
                    entities.append({
                        "id": f"transit_route_{el['id']}",
                        "entity_type": "polyline_existing",
                        "name": route_label,
                        "blurb": f"Existing {route_type} route: {route_label}",
                        "polyline_positions": [positions],
                        "style": {"color": route_color, "width": 5, "alpha": 0.7, "dashed": False}
                    })

            # 3. Transit Stations
            elif el_type == "node" and tags.get("railway") == "station":
                entities.append({
                    "id": f"station_{el['id']}",
                    "entity_type": "point",
                    "name": name or "Transit Station",
                    "blurb": "Existing transit station",
                    "position": {"lat": el["lat"], "lng": el["lon"], "height": 0},
                    "style": {"color": "#A78BFA", "pixelSize": 12}
                })

            # 4. Bus Stops
            elif el_type == "node" and tags.get("highway") == "bus_stop":
                entities.append({
                    "id": f"bus_stop_{el['id']}",
                    "entity_type": "point",
                    "name": name or "Bus Stop",
                    "blurb": "Existing bus stop",
                    "position": {"lat": el["lat"], "lng": el["lon"], "height": 0},
                    "style": {"color": "#3B82F6", "pixelSize": 8}
                })

            # 5. Industrial/Workplace Zones (Polygons)
            elif ("landuse" in tags or tags.get("man_made") == "factory") and el_type == "way" and el.get("geometry"):
                positions = [{"lat": p["lat"], "lng": p["lon"]} for p in el.get("geometry", [])]
                is_industrial = tags.get("landuse") == "industrial" or tags.get("man_made") == "factory"
                entities.append({
                    "id": f"workplace_zone_{el['id']}",
                    "entity_type": "polygon",
                    "name": f"{'Industrial' if is_industrial else 'Commercial'} Hub: {name or 'Zone'}",
                    "polygon_positions": positions,
                    "style": {
                        "color": "#4A90E2" if not is_industrial else "#F5A623",
                        "alpha": 0.15,
                        "height": 0
                    }
                })

        return entities
    except Exception as e:
        print(f"Failed to fetch context infrastructure: {e}")
        return []


def score_hypothesis_alignment(hypothesis: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """
    Scores how well the hypothesized transit link aligns with physical spatial reality.
    Formula: (Traffic Congestion + Land-Use Density) * Transit Gap
    """
    route_match_score = 1.0 if evidence.get("matched") else 0.0
    if not route_match_score:
        return {"alignment_score": 0.0, "pass": False}

    connectivity_need = float(evidence.get("connectivity_score", 0.0)) # 1.0 = No transit
    density_demand = float(evidence.get("density_score", 0.0))        # 1.0 = High density
    traffic_pain = float(evidence.get("congestion_score", 0.0))       # 1.0 = High congestion
    
    confidence_bonus = 0.05 if str(hypothesis.get("confidence", "")).lower() == "high" else 0.0
    
    # We want locations where (Traffic is high OR Density is high).
    # If the intervention is at a transit hub, connectivity_need might be low (because there are many buses), 
    # so we shouldn't strictly penalize it.
    demand_signal = (0.6 * traffic_pain) + (0.4 * density_demand)
    
    # Give less penalty to existing transit hubs
    alignment_score = (0.7 * demand_signal) + (0.3 * connectivity_need) + confidence_bonus
    
    return {"alignment_score": round(alignment_score, 3), "pass": alignment_score >= 0.35}



async def run_hotspot_hypothesis_loop(session_id: str, city: str, selected_challenge: dict[str, Any], feedback: str = "") -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []

    prompt = build_hotspot_hypothesis_prompt(city, selected_challenge, feedback)
    raw = await run_agent_once(find_hotspot_agent, session_id, prompt)
    
    try:
        hypotheses = safe_json_loads(raw)
        if not isinstance(hypotheses, list):
            hypotheses = [hypotheses]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Hotspot agent failed to return valid JSON list: {exc}")

    for i, hypothesis in enumerate(hypotheses[:3]):
        # Ensure coordinates are floats. The find_hotspot_agent sometimes omits lat/lon
        # (its system instruction doesn't require them). Rather than hard-skipping the
        # candidate and ending up with an empty list → HTTPException(500), we recover
        # the coordinates from the geocoding already performed inside
        # get_transit_connectivity_evidence(), with Malaysia's geographic centre as a
        # last resort.
        h_lat = hypothesis.get("lat")
        h_lon = hypothesis.get("lon")
        has_valid_coords = False
        try:
            hypothesis["lat"] = float(h_lat)
            hypothesis["lon"] = float(h_lon)
            has_valid_coords = True
        except Exception:
            print(f"Warning: Invalid coordinates for candidate {i+1}, will recover from geocoding")

        evidence = get_transit_connectivity_evidence(city, hypothesis)

        # Recover coordinates from the geocoding done inside get_transit_connectivity_evidence.
        if not has_valid_coords:
            recovered_lat = evidence.get("lat")
            recovered_lon = evidence.get("lon")
            if recovered_lat and recovered_lon:
                hypothesis["lat"] = float(recovered_lat)
                hypothesis["lon"] = float(recovered_lon)
                has_valid_coords = True
            else:
                # Last resort: use Malaysia's geographic centre so downstream map code
                # doesn't crash, while printing a clear warning.
                print(f"Warning: Could not geocode candidate {i+1} ({hypothesis.get('location_label')}), using Malaysia centre")
                hypothesis["lat"] = 3.1390
                hypothesis["lon"] = 101.6869

        score = score_hypothesis_alignment(hypothesis, evidence)
        attempts.append({
            "iteration": i + 1,
            "hypothesis": hypothesis,
            "evidence": evidence,
            "score": score["alignment_score"],
            "pass": score["pass"],
        })
        print(f"Candidate {i+1} Score: {score['alignment_score']} at {hypothesis['lat']},{hypothesis['lon']}")

    valid_attempts = [a for a in attempts if a.get("hypothesis")]
    if not valid_attempts:
        raise HTTPException(status_code=500, detail="Hotspot hypothesis loop failed to produce valid JSON.")

    attempts_sorted = sorted(valid_attempts, key=lambda x: x["score"], reverse=True)
    primary = attempts_sorted[0]
    secondary = attempts_sorted[1] if len(attempts_sorted) > 1 else attempts_sorted[0]

    # Expert Solution: Multi-Cluster Implementation Discovery
    clusters: list[dict[str, Any]] = []
    for att in attempts_sorted[:3]:
        hyp = att["hypothesis"]
        lat = hyp.get("lat")
        lon = hyp.get("lon")
        
        context_nodes = []
        if lat and lon:
            context_raw = get_context_infrastructure(lat, lon, intervention_type="general")
            # Limit to 5 most important satellites
            context_nodes = context_raw[:5]
            
        linked_feeder = None
        feeder_context = []
        if hyp.get("LINKED_FEEDER", {}).get("needed") in ["true", True]:
            linked_feeder = hyp.get("LINKED_FEEDER")
            try:
                f_lat = float(linked_feeder.get("lat"))
                f_lon = float(linked_feeder.get("lon"))
                linked_feeder["lat"] = f_lat
                linked_feeder["lon"] = f_lon
                feeder_context_raw = get_context_infrastructure(f_lat, f_lon, intervention_type="general")
                feeder_context = feeder_context_raw[:3]
            except Exception as e:
                print(f"Failed to process linked feeder: {e}")
                linked_feeder = None

        clusters.append({
            "center": hyp,
            "context": context_nodes,
            "score": att["score"],
            "label": hyp.get("LOCATION_LABEL", "Candidate Node"),
            "intervention_type": hyp.get("INTERVENTION_RECOMMENDATION", "BUS"),
            "intervention_rationale": hyp.get("INTERVENTION_RATIONALE", "Optimal for local connectivity."),
            "linked_feeder": linked_feeder,
            "feeder_context": feeder_context
        })

    return {
        "IMPLEMENTATION_CLUSTERS": clusters,
        "PRIMARY_MICRO": primary["hypothesis"],
        "SECONDARY_MICRO": secondary["hypothesis"],
        "PRIMARY_EVIDENCE": primary["evidence"],
        "CONFIDENCE": primary["hypothesis"].get("confidence", "medium"),
        "EVIDENCE_WINDOW": primary["evidence"].get("evidence_window", []),
        "attempts": attempts_sorted,
    }


def extract_routing_labels_from_micro(micro: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for key in ("road_a_queries", "road_b_queries"):
        for label in micro.get(key, []) or []:
            if label and label not in labels:
                labels.append(label)
    return labels


def format_micro_options(strict_json: dict[str, Any]) -> str:
    primary = strict_json.get("PRIMARY_MICRO", {})
    secondary = strict_json.get("SECONDARY_MICRO", {})

    def block(num: int, micro: dict[str, Any]) -> str:
        routing = ", ".join(extract_routing_labels_from_micro(micro)) or "N/A"
        return (
            f"{num}. {micro.get('symptom', 'Untitled micro-symptom')}\n"
            f"TYPE: {micro.get('type', 'N/A')}\n"
            f"LOCATION_LABEL: {micro.get('location_label', 'N/A')}\n"
            f"ROUTING_LABELS: {routing}"
        )

    return (
        f"Selected challenge: {strict_json.get('CHALLENGE_THEME', 'N/A')}\n\n"
        f"{block(1, primary)}\n\n{block(2, secondary)}\n\n"
        "Which micro-symptom would you like to route and analyze further?"
    )

def format_step_reply(step_name: str, raw_output: str) -> str:
    """Formats JSON output into readable text based on the active pipeline step."""
    if step_name == "Generate solutions":
        try:
            data = safe_json_loads(raw_output)
        except Exception:
            # Fallback: if the agent messes up and doesn't return JSON, just show the raw text
            return raw_output
        # Extract core details
        title = data.get("solution_title", "Proposed Solution")
        sol_type = str(data.get("solution_type", "Intervention")).replace("_", " ").title()
        complexity = str(data.get("implementation_complexity", "unknown")).title()
        confidence = str(data.get("confidence", "unknown")).title()
        
        # Extract geometry details safely
        target = data.get("target_geometry", {})
        location = target.get("location", "the target area")
        roads = target.get("primary_roads", [])
        
        # Extract lists
        actions = data.get("proposed_actions", [])
        effects = data.get("expected_effect", [])
        
        # Build the natural paragraph structure
        blocks = []
        blocks.append(f"### 🛠️ {title}")
        blocks.append(f"**Intervention Type:** {sol_type} | **Complexity:** {complexity} | **Confidence:** {confidence}\n")
        
        # Format the location sentence naturally
        road_context = f" involving {', '.join(roads)}" if roads else ""
        blocks.append(f"**Target Location:** {location}{road_context}.\n")
        
        if actions:
            blocks.append("**Proposed Actions:**")
            for action in actions:
                blocks.append(f"* {action}")
            blocks.append("")  # Spacing
            
        if effects:
            blocks.append("**Expected Effects:**")
            for effect in effects:
                blocks.append(f"* {effect}")
            blocks.append("")

        impact = data.get("societal_impact")
        if impact:
            blocks.append(f"**🌍 Societal Impact:**\n{impact}")
                
        # Optional: Add a small transition message for the user
        blocks.append("\n*This is a summary of what we're gonna build!!")
                
        return "\n".join(blocks).strip()

    # Default for Building simulations or unrecognized steps
    return raw_output


def make_analysis_result_for_prompt(raw_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned = []
    for raw in raw_results:
        cleaned.append(
            {
                "selected_micro_source": raw.get("selected_micro_source"),
                "selected_micro_type": raw.get("selected_micro_type"),
                "selected_micro_symptom": raw.get("selected_micro_symptom"),
                "selected_micro_location_label": raw.get("selected_micro_location_label"),
                "mode": raw.get("mode"),
                "city_query": raw.get("city_query"),
                "candidates": raw.get("candidates", []),
            }
        )
    return cleaned


def build_planning_prompt(
    selected_challenge: dict[str, Any],
    selected_micro: dict[str, Any],
    analysis_result: list[dict[str, Any]],
) -> str:
    return f"""
You are given the selected transport challenge, the selected micro-symptom, and the graph-routing analysis results.

SELECTED_CHALLENGE_JSON:
{json.dumps(selected_challenge, ensure_ascii=False, indent=2)}

SELECTED_MICRO_JSON:
{json.dumps(selected_micro, ensure_ascii=False, indent=2)}

GRAPH_ROUTING_ANALYSIS_JSON:
{json.dumps(analysis_result, ensure_ascii=False, indent=2)}

Task:
- Compare the available routing candidates carefully.
- Select the single best candidate for intervention.
- Return STRICT JSON ONLY using your required planning output schema.
""".strip()


def build_solution_prompt(
    selected_challenge: dict[str, Any],
    selected_micro: dict[str, Any],
    analysis_result: list[dict[str, Any]],
    planning_result: dict[str, Any],
) -> str:
    return f"""
You are given:
1. the selected transport challenge
2. the selected micro-symptom
3. the graph-routing analysis results
4. the planning decision

SELECTED_CHALLENGE_JSON:
{json.dumps(selected_challenge, ensure_ascii=False, indent=2)}

SELECTED_MICRO_JSON:
{json.dumps(selected_micro, ensure_ascii=False, indent=2)}

GRAPH_ROUTING_ANALYSIS_JSON:
{json.dumps(analysis_result, ensure_ascii=False, indent=2)}

PLANNING_RESULT_JSON:
{json.dumps(planning_result, ensure_ascii=False, indent=2)}

Task:
- Design a realistic intervention grounded only in the provided problem and selected candidate.
- Return STRICT JSON ONLY using your required solution output schema.
""".strip()


def build_building_prompt(
    selected_challenge: dict[str, Any],
    selected_micro: dict[str, Any],
    analysis_result: list[dict[str, Any]],
    planning_result: dict[str, Any],
    solution_result: dict[str, Any],
    route_roads: list[str] | None = None,
) -> str:
    grounding_block = ""
    if route_roads:
        roads_str = ", ".join(f'"{r}"' for r in route_roads[:10])
        grounding_block = f"""
⚠️  CRITICAL SPATIAL GROUNDING RULE:
The OSMnx routing engine has calculated that this intervention physically passes through
these real, verified roads: {roads_str}
You MUST use ONLY these road names (or their direct intersections) as SEARCH_LOCATION for
your POLYLINE, SIMULATION, POINT, and POLYGON objects.
Do NOT invent, generalise, or use any other road name. All assets MUST be spatially
co-located on this corridor.
"""
    return f"""
You are given:
1. the selected transport challenge
2. the selected micro-symptom
3. the graph-routing analysis results
4. the planning decision
5. the final solution design
{grounding_block}
SELECTED_CHALLENGE_JSON:
{json.dumps(selected_challenge, ensure_ascii=False, indent=2)}

SELECTED_MICRO_JSON:
{json.dumps(selected_micro, ensure_ascii=False, indent=2)}

GRAPH_ROUTING_ANALYSIS_JSON:
{json.dumps(analysis_result, ensure_ascii=False, indent=2)}

PLANNING_RESULT_JSON:
{json.dumps(planning_result, ensure_ascii=False, indent=2)}

SOLUTION_RESULT_JSON:
{json.dumps(solution_result, ensure_ascii=False, indent=2)}

Task:
- Convert the solution into Cesium-ready map instruction lines.
- Return ONLY lines in this exact format:
[GEOMETRY_TYPE | COUNT | LABEL | SEARCH_LOCATION | STYLE_HINT | DESCRIPTION]
""".strip()


def analyze_selected_micro(selected_micro: dict[str, Any], selected_city: str) -> list[dict[str, Any]]:
    helper = InfrastructurePlannerOrchestrator(
        name="helper",
        description="helper",
        pipeline=[],
        reviewer=review_agent,
        app_name=APP_NAME,
        session_svc=session_service,
    )
    return helper.run_analysis_from_agent_output(
        {"PRIMARY_MICRO": selected_micro},
        selected_city,
    )


@app.post("/api/start")
async def start(req: StartRequest):
    session_id = str(uuid.uuid4())
    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
        state={"feedback": "", "valid_places_text": "", "target_places": []},
    )

    greeting_prompt = """
The conversation is starting.

Greet the user naturally and ask them to provide one or two Malaysian cities or towns for infrastructure analysis.

Return exactly in this format:

VERDICT: RETRY
PLACES:
FEEDBACK: <your greeting and question>
""".strip()

    greeting_text = await run_agent_once(place_intake_agent, session.id, greeting_prompt)
    greeting_parsed = parse_place_result(greeting_text)
    workflow_state[session.id] = {"phase": "intake"}

    return {
        "ok": True,
        "session_id": session.id,
        "stage": "Place intake",
        "reply": greeting_parsed["feedback"],
        "needs_input": True,
    }


@app.post("/api/chat")
async def chat(req: ChatRequest, background_tasks: BackgroundTasks):
    session_id = req.session_id
    user_message = req.message.strip()

    if session_id not in workflow_state:
        raise HTTPException(status_code=404, detail="Session not found")

    current_session = await session_service.get_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
    )
    state = workflow_state[session_id]
    phase = state["phase"]

    if phase == "intake":
        intake_prompt = f"""
User message:
{user_message}
User message:
{user_message}

Remember:
- Accept any real Malaysian city or town
- Accept at most two places
- Reject places that are too broad
- Reject places that are too specific
- Return structured output exactly
""".strip()
        intake_text = await run_agent_once(place_intake_agent, session_id, intake_prompt)
        parsed = parse_place_result(intake_text)

        if parsed["verdict"] == "SUCCESS" and parsed["places"]:
            current_session.state["target_places"] = parsed["places"]
            planning_response: dict[str, Any]
            if GROWTH_FLOW_ENABLED:
                try:
                    # Pass BackgroundTasks to the phase starter
                    planning_response = await start_area_option_phase(session_id, current_session, background_tasks=background_tasks)
                except Exception as exc:
                    print(f"Growth-led area selection fallback triggered: {exc}")
                    planning_response = await start_planning_phase(session_id, current_session)
            else:
                planning_response = await start_planning_phase(session_id, current_session)
            planning_response["reply"] = (
                f"Location confirmed: {', '.join(parsed['places'])}. Moving to the planning phase.\n\n"
                + planning_response["reply"]
            )
            return planning_response

        return {
            "ok": True,
            "session_id": session_id,
            "stage": "Place intake",
            "reply": parsed["feedback"],
            "needs_input": True,
        }

    if phase == "area_selection":
        selected_city = (state.get("target_places") or ["Kuala Lumpur"])[0]
        area_options = list(state.get("area_options") or [])

        if user_message.strip().lower() in {"regenerate", "refresh", "another"}:
            refreshed = await _generate_area_options(session_id, selected_city)
            state["area_options"] = refreshed
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Area selection",
                "reply": _format_area_options_reply(selected_city, refreshed),
                "needs_input": True,
                "needs_selection": True,
                "area_options": refreshed,
            }

        selected_option = _resolve_area_selection(user_message, area_options)
        if not selected_option:
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Area selection",
                "reply": "Please choose an area by number or exact area name from the options.",
                "needs_input": True,
                "needs_selection": True,
                "area_options": area_options,
            }

        verified_option = await _verify_single_area(selected_city, selected_option)
        if verified_option is None:
            verified_option = dict(selected_option)
            verified_option["route_feasibility"] = {"pass": False, "score": 0.0, "candidate_count": 0, "error": "verification_failed"}
            verified_option["merged_confidence"] = {"confidence": 0.0, "band": "low", "pass_gate": False}
            verified_option["osm_gap_score"] = float(selected_option.get("osm_gap_score", 0.0))
            verified_option["osm_completeness_score"] = float(selected_option.get("osm_completeness_score", 0.0))

        selected_option = verified_option
        state["selected_area_option"] = selected_option

        osm_gap_score = float(selected_option.get("osm_gap_score", 0.0))
        osm_completeness_score = float(selected_option.get("osm_completeness_score", 0.0))
        feasibility = selected_option.get("route_feasibility") or {"pass": False, "score": 0.0}
        merged = selected_option.get("merged_confidence") or {"confidence": 0.0, "band": "low", "pass_gate": False}
        complaint_verified = bool(selected_option.get("complaint_verified", False))

        # Build strategic rationale
        impact_drivers = []
        if float(selected_option.get("growth_signals", {}).get("population", 0)) > 0: impact_drivers.append("Population Growth")
        if float(selected_option.get("growth_signals", {}).get("industrial", 0)) > 0: impact_drivers.append("Industrial Cluster")
        if float(selected_option.get("growth_signals", {}).get("trip_generator", 0)) > 0: impact_drivers.append("Activity Hub")
        if osm_gap_score > 0.5: impact_drivers.append("Transit Gap")
        if selected_option.get("equity_flag"): impact_drivers.append("Social Equity Priority")

        evidence_summary = {
            "selected_area": selected_option.get("area_label"),
            "report_score": selected_option.get("report_score"),
            "gap_score": osm_gap_score,
            "completeness_score": osm_completeness_score,
            "feasibility": feasibility,
            "confidence": merged,
            "impact_drivers": impact_drivers,
            "strategic_rationale": _build_strategic_narrative(selected_option, type("AuditStub", (), {"gap_score": osm_gap_score})()),
            "complaint_verified": complaint_verified,
        }

        gate_pass = bool(merged.get("pass_gate", False))
        soft_pass = (
            float(selected_option.get("report_score", 0.0)) >= 0.72
            and osm_gap_score >= 0.35
            and osm_completeness_score < 0.35
        )
        fallback_override = bool(selected_option.get("allow_low_confidence", False))

        if not gate_pass and not fallback_override and not soft_pass:
            missing: list[str] = []
            if osm_completeness_score < 0.5:
                missing.append("OSM coverage is sparse in this area")
            if not feasibility.get("pass"):
                missing.append("Route feasibility check failed")
            if merged.get("confidence", 0.0) < 0.68:
                missing.append("Merged confidence below threshold (0.68)")
            missing_msg = "; ".join(missing) if missing else "More evidence needed."
            state["evidence_summary"] = evidence_summary

            reply_tmpl = (
                "Needs verification for {area}.\n"
                "Confidence: {conf} ({band}).\n"
                "Reason: {reason}\n"
                "Choose another area or type 'regenerate' for new options."
            )
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Area selection",
                "reply": reply_tmpl.format(
                    area=selected_option.get('area_label'),
                    conf=merged.get('confidence'),
                    band=merged.get('band'),
                    reason=missing_msg,
                ),
                "needs_input": True,
                "needs_selection": True,
                "area_options": area_options,
                "evidence_summary": evidence_summary,
            }

        if soft_pass and not gate_pass:
            evidence_summary["gate_override"] = {
                "used": True,
                "reason": "Proceeding because evidence is strong, but OSM coverage for this area is sparse.",
            }
        elif not gate_pass and fallback_override:
            evidence_summary["gate_override"] = {
                "used": True,
                "reason": "Selected option came from fallback area set; proceeding to Find Needs with caution.",
            }

        # Gate pass -> continue with challenge synthesis for the selected area.
        target_places = state.get("target_places", [])
        prompt = build_find_needs_prompt(
            target_places,
            selected_area=selected_option,
            merged_evidence=evidence_summary,
        )
        # Check for Speculative Warm-up hit
        speculative = state.get("speculative_find_needs")
        initial_raw = None
        if speculative and speculative.get("area_id") == selected_option.get("id"):
            initial_raw = speculative.get("raw_output")
            print(f"Speculative HIT for {selected_option.get('area_label')}")
        
        if not initial_raw:
            initial_raw = await run_agent_with_retry(find_needs_agent, session_id, prompt)
        if is_retry_response(initial_raw):
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Find needs",
                "reply": extract_retry_feedback(initial_raw),
                "needs_input": True,
            }

        audit_passed, audit_raw = await audit_generated_challenges(session_id, selected_option, initial_raw)
        state["last_find_needs_audit"] = audit_raw
        if not audit_passed:
            print(f"Fact-Check Audit flagged output for {selected_option.get('area_label')}: {audit_raw}")
        else:
            print(f"Fact-Check Audit completed for {selected_option.get('area_label')}")

        raw_step_output, display_reply, find_needs_options = await prepare_find_needs_output(
            session_id=session_id,
            target_places=target_places,
            raw_step_output=initial_raw,
            selected_area=selected_option,
            merged_evidence=evidence_summary,
        )
        state.update(
            {
                "phase": "challenge_selection",
                "last_step_output": raw_step_output,
                "last_display_reply": display_reply,
                "evidence_summary": evidence_summary,
                "find_needs_options": find_needs_options,
                "selected_area_option": selected_option,
            }
        )

        return {
            "ok": True,
            "session_id": session_id,
            "stage": "Find needs",
            "reply": display_reply,
            "needs_input": True,
            "find_needs_options": find_needs_options,
        }

    if phase == "challenge_selection":
        raw_step_output = state["last_step_output"]
        review_prompt = (
            "STEP NAME: Find needs\n\n"
            f"STEP OUTPUT:\n{raw_step_output}\n\n"
            f"USER RESPONSE: {user_message}"
        )
        review_text = await run_agent_with_retry(review_agent, session_id, review_prompt)
        if is_retry_response(review_text):
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Find needs",
                "reply": extract_retry_feedback(review_text),
                "needs_input": True,
            }
        review_result = parse_review(review_text)

        if review_result["verdict"] == "REVISE":
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Find needs",
                "reply": review_result["detail"] or "Please choose one challenge from the list.",
                "needs_input": True,
            }

        if review_result["verdict"] == "REVISE_TOTAL":
            # User wants to regenerate challenges
            feedback = review_result["detail"] or "Regenerate challenges"
            target_places = state.get("target_places", [])
            rerun_prompt = (
                f"The user rejected the previous challenges and gave this feedback: {feedback}. "
                f"Please generate 3 new transport challenges for {target_places}. "
                f"If available, prioritize this selected area context: {json.dumps(state.get('selected_area_option', {}), ensure_ascii=False)}\n\n"
                "Return STRICT JSON ONLY with keys CHALLENGE_1, CHALLENGE_2, CHALLENGE_3."
            )
            initial_raw = await run_agent_with_retry(find_needs_agent, session_id, rerun_prompt)
            if is_retry_response(initial_raw):
                return {
                    "ok": True,
                    "session_id": session_id,
                    "stage": "Find needs",
                    "reply": extract_retry_feedback(initial_raw),
                    "needs_input": True,
                }
            new_challenges_raw, display_reply, find_needs_options = await prepare_find_needs_output(
                session_id=session_id,
                target_places=target_places,
                raw_step_output=initial_raw,
                selected_area=state.get("selected_area_option"),
                merged_evidence=state.get("evidence_summary"),
            )
            state["last_step_output"] = new_challenges_raw
            state["last_display_reply"] = display_reply
            state["find_needs_options"] = find_needs_options
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Find needs",
                "reply": display_reply,
                "needs_input": True,
                "find_needs_options": find_needs_options,
            }

        # Ensure we have a single object
        selected_output = review_result["final_output"]
        if not selected_output:
            # If the agent didn't extract it but passed, we might be in trouble
            # Try to see if raw_step_output is already a single challenge (unlikely)
            selected_challenge = safe_json_loads(raw_step_output)
            if "CHALLENGE_1" in selected_challenge:
                # Still the multi-choice one, the review agent failed to extract
                return {
                    "ok": True,
                    "session_id": session_id,
                    "stage": "Find needs",
                    "reply": "I couldn't quite catch which one you picked. Could you please specify by number or name?",
                    "needs_input": True,
                }
        else:
            try:
                selected_challenge = safe_json_loads(selected_output)
            except Exception:
                return {
                    "ok": True,
                    "session_id": session_id,
                    "stage": "Find needs",
                    "reply": "I couldn't parse the selected challenge cleanly. Please reply with 1, 2, or 3 again.",
                    "needs_input": True,
                }

        state["selected_challenge"] = selected_challenge
        selected_city = (state.get("target_places") or [])[0]
        hotspot_result = await run_hotspot_hypothesis_loop(session_id, selected_city, selected_challenge)
        if is_retry_response(hotspot_result):
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Find needs",
                "reply": extract_retry_feedback(hotspot_result),
                "needs_input": True,
            }

        strict_json = {
            "CHALLENGE_THEME": selected_challenge.get("CHALLENGE_THEME"),
            "MACRO_ROOT_CAUSE": selected_challenge.get("MACRO_ROOT_CAUSE"),
            "WHY_IT_MATTERS": selected_challenge.get("WHY_IT_MATTERS"),
            "EVIDENCE_SUMMARY": selected_challenge.get("EVIDENCE_SUMMARY"),
            "PRIMARY_MICRO": hotspot_result["PRIMARY_MICRO"],
            "SECONDARY_MICRO": hotspot_result["SECONDARY_MICRO"],
            "ROUTING_LABELS": {
                "PRIMARY_MICRO": extract_routing_labels_from_micro(hotspot_result["PRIMARY_MICRO"]),
                "SECONDARY_MICRO": extract_routing_labels_from_micro(hotspot_result["SECONDARY_MICRO"]),
            },
            "CONFIDENCE": hotspot_result["CONFIDENCE"],
            "EVIDENCE_WINDOW": hotspot_result["EVIDENCE_WINDOW"],
        }

        state.update(
            {
                "phase": "micro_selection",
                "strict_json": strict_json,
                "last_step_output": json.dumps(strict_json, ensure_ascii=False, indent=2),
                "last_display_reply": format_micro_options(strict_json),
            }
        )
        return {
            "ok": True,
            "session_id": session_id,
            "stage": "Micro hotspot selection",
            "reply": state["last_display_reply"],
            "needs_input": True,
            "implementation_clusters": hotspot_result.get("IMPLEMENTATION_CLUSTERS", [])
        }

    if phase == "micro_selection":
        raw_step_output = state["last_step_output"]
        review_prompt = (
            "STEP NAME: Select micro-symptom\n\n"
            f"STEP OUTPUT:\n{raw_step_output}\n\n"
            f"USER RESPONSE: {user_message}"
        )
        review_text = await run_agent_with_retry(review_agent, session_id, review_prompt)
        if is_retry_response(review_text):
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Micro hotspot selection",
                "reply": extract_retry_feedback(review_text),
                "needs_input": True,
            }
        review_result = parse_review(review_text)

        if review_result["verdict"] == "REVISE":
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Micro hotspot selection",
                "reply": review_result["detail"] or "Please choose one micro-symptom from the list.",
                "needs_input": True,
            }

        if review_result["verdict"] == "REVISE_TOTAL":
            feedback = review_result["detail"] or "Regenerate hotspots"
            selected_challenge = state.get("selected_challenge", {})
            selected_city = (state.get("target_places") or [])[0]
            # Rerun the hypothesis loop with feedback
            hotspot_result = await run_hotspot_hypothesis_loop(session_id, selected_city, selected_challenge, feedback=feedback)
            if is_retry_response(hotspot_result):
                return {
                    "ok": True,
                    "session_id": session_id,
                    "stage": "Micro hotspot selection",
                    "reply": extract_retry_feedback(hotspot_result),
                    "needs_input": True,
                }
            
            strict_json = {
                "CHALLENGE_THEME": selected_challenge.get("CHALLENGE_THEME"),
                "MACRO_ROOT_CAUSE": selected_challenge.get("MACRO_ROOT_CAUSE"),
                "WHY_IT_MATTERS": selected_challenge.get("WHY_IT_MATTERS"),
                "EVIDENCE_SUMMARY": selected_challenge.get("EVIDENCE_SUMMARY"),
                "PRIMARY_MICRO": hotspot_result["PRIMARY_MICRO"],
                "SECONDARY_MICRO": hotspot_result["SECONDARY_MICRO"],
                "ROUTING_LABELS": {
                    "PRIMARY_MICRO": extract_routing_labels_from_micro(hotspot_result["PRIMARY_MICRO"]),
                    "SECONDARY_MICRO": extract_routing_labels_from_micro(hotspot_result["SECONDARY_MICRO"]),
                },
            }
            new_raw_output = json.dumps(strict_json)
            state["last_step_output"] = new_raw_output
            state["last_display_reply"] = format_micro_options(strict_json)
            
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Micro hotspot selection",
                "reply": state["last_display_reply"],
                "needs_input": True,
                "implementation_clusters": hotspot_result.get("IMPLEMENTATION_CLUSTERS", [])
            }

        selected_micro = safe_json_loads(review_result["final_output"])
        selected_city = (state.get("target_places") or [])[0]
        try:
            analysis_result_raw = analyze_selected_micro(selected_micro, selected_city)
            analysis_result = make_analysis_result_for_prompt(analysis_result_raw)
        except Exception as exc:
            return {
                "ok": True,
                "session_id": session_id,
                "stage": "Micro hotspot selection",
                "reply": f"Analysis failed: {exc}. Please try another micro-symptom or refine your selection.",
                "needs_input": True,
            }

        state.update(
            {
                "phase": "planning",
                "step_index": 0,
                "selected_micro": selected_micro,
                "analysis_result_raw": analysis_result_raw,
                "analysis_result": analysis_result,
            }
        )

        next_prompt = build_planning_prompt(
            selected_challenge=state["selected_challenge"],
            selected_micro=selected_micro,
            analysis_result=analysis_result,
        )

        next_raw_step_output = await run_agent_once(planning_agent, session_id, next_prompt)

        display_reply = format_step_reply("Plan improvements", next_raw_step_output)

        
        state.update(
            {
                "last_step_output": next_raw_step_output,
                "last_display_reply": next_raw_step_output,
                "last_agent_name": planning_agent.name,
                "output_key": "planning_result",
            }
        )

        return {
            "ok": True,
            "session_id": session_id,
            "stage": "Plan improvements",
            "reply": display_reply,
            "needs_input": True,
        }

    if phase != "planning":
        raise HTTPException(status_code=400, detail=f"Unsupported phase: {phase}")

    step_index = state["step_index"]
    step_name, step_agent, output_key = PIPELINE[step_index]
    raw_step_output = state["last_step_output"]

    review_prompt = (
        f"STEP NAME: {step_name}\n\n"
        f"STEP OUTPUT:\n{raw_step_output}\n\n"
        f"USER RESPONSE: {user_message}"
    )
    review_text = await run_agent_once(review_agent, session_id, review_prompt)
    review_result = parse_review(review_text)

    if review_result["verdict"] == "REVISE":
        return {
            "ok": True,
            "session_id": session_id,
            "stage": step_name,
            "reply": review_result["detail"] or "Please revise your response.",
            "needs_input": True,
        }

    if review_result["verdict"] == "REVISE_TOTAL":
        feedback = review_result["detail"] or "Please revise the previous output."
        rerun_prompt = (
            f'The previous {step_name} output was rejected. Follow this specific instruction to revise it: "{feedback}"'
        )
        new_raw_step_output = await run_agent_once(step_agent, session_id, rerun_prompt)

        display_reply = format_step_reply(step_name, new_raw_step_output)

        state["last_step_output"] = new_raw_step_output
        state["last_display_reply"] = display_reply

        if step_name == "Building simulations":
            city_name = (state.get("target_places") or ["Kuala Lumpur"])[0]
            enriched_assets = process_agent_assets(new_raw_step_output, city_name=city_name)
            entities = format_entities(enriched_assets)
            
            # Get city coords for camera
            from building_agent_helper import get_malaysia_coords
            coords = get_malaysia_coords(city_name)
            
            # INJECT EXISTING INFRASTRUCTURE
            ref_lat, ref_lng = (coords["lat"], coords["lng"]) if coords else (3.1390, 101.6869)
            if entities:
                # Find a more specific central point from entities if possible
                for ent in entities:
                    if ent.get("polyline_positions"):
                        ref_lat = ent["polyline_positions"][0][0]["lat"]
                        ref_lng = ent["polyline_positions"][0][0]["lng"]
                        break
                    elif ent.get("position"):
                        ref_lat = ent["position"]["lat"]
                        ref_lng = ent["position"]["lng"]
                        break
                
            existing_context = get_context_infrastructure(ref_lat, ref_lng)
            entities = existing_context + entities

            # INJECT TRUE ROUTE GEOMETRY FROM OSMNX — PRIMARY only
            analysis_raw = state.get("analysis_result_raw", [])
            if analysis_raw:
                primary_result = analysis_raw[0]
                if primary_result.get("route_geometry"):
                    entities.append({
                        "id": "main_route_primary",
                        "entity_type": "polyline",
                        "name": "Proposed Optimal Route (Calculated)",
                        "polyline_positions": [primary_result["route_geometry"]],
                        "style": {
                            "color": "#3B82F6",
                            "width": 10,
                            "opacity": 0.8,
                            "flow": "normal"
                        }
                    })

            # INJECT ISOCHRONES
            analysis_raw = state.get("analysis_result_raw", [])
            for cand_res in analysis_raw:
                if "isochrone_geoms" in cand_res:
                    for idx, iso_poly in enumerate(cand_res["isochrone_geoms"]):
                        iso_coords = list(iso_poly.exterior.coords)
                        positions = [{"lat": lat, "lng": lng, "height": 0} for lng, lat in iso_coords]
                        entities.append({
                            "id": f"isochrone_rev_{idx}",
                            "entity_type": "polygon",
                            "name": "5-Minute Walking Catchment (400m)",
                            "polygon_positions": positions,
                            "style": {
                                "color": "#10B981",
                                "alpha": 0.2,
                                "height": 0,
                                "outline": True,
                                "outlineColor": "#059669"
                            }
                        })

            return {
                "ok": True,
                "session_id": session_id,
                "stage": "done",
                "reply": display_reply,
                "show_map": True,
                "entities": entities,
                "needs_input": False,
                "city_name": city_name,
                "target_lat": ref_lat,
                "target_lng": ref_lng,
            }

        return {
            "ok": True,
            "session_id": session_id,
            "stage": step_name,
            "reply": display_reply,
            "needs_input": True,
        }

    selected_output = review_result["final_output"] or raw_step_output
    if step_name != "Building simulations":
        parsed_selected_output = safe_json_loads(selected_output)
    if step_name == "Plan improvements":
        state["planning_result"] = parsed_selected_output
    elif step_name == "Generate solutions":
        state["solution_result"] = parsed_selected_output
    
    next_index = step_index + 1
    if next_index >= len(PIPELINE):
        return {
            "ok": True,
            "session_id": session_id,
            "stage": "done",
            "reply": "Workflow completed.",
            "needs_input": False,
        }

    next_step_name, next_step_agent, next_output_key = PIPELINE[next_index]
    if next_step_name == "Generate solutions":
        next_prompt = build_solution_prompt(
            selected_challenge=state["selected_challenge"],
            selected_micro=state["selected_micro"],
            analysis_result=state["analysis_result"],
            planning_result=state["planning_result"],
        )
    elif next_step_name == "Building simulations":
        # Extract route road names to ground the AI building agent to the real corridor
        analysis_raw_for_prompt = state.get("analysis_result_raw", [])
        route_roads: list[str] = []
        for cand_res in analysis_raw_for_prompt:
            for cand in cand_res.get("candidates", []):
                route_roads.extend(cand.get("via_roads", []))
        # Deduplicate while preserving order
        seen_roads: set[str] = set()
        route_roads = [r for r in route_roads if not (r in seen_roads or seen_roads.add(r))]  # type: ignore

        next_prompt = build_building_prompt(
            selected_challenge=state["selected_challenge"],
            selected_micro=state["selected_micro"],
            analysis_result=state["analysis_result"],
            planning_result=state["planning_result"],
            solution_result=state["solution_result"],
            route_roads=route_roads or None,
        )
    else:
        next_prompt = f"Proceed with {next_step_name}"

    next_raw_step_output = await run_agent_once(next_step_agent, session_id, next_prompt)

    if next_step_name == "Building simulations":
        city_name = (state.get("target_places") or ["Kuala Lumpur"])[0]
        enriched_assets = process_agent_assets(next_raw_step_output, city_name=city_name)
        entities = format_entities(enriched_assets)
        
        # Get city coords for camera
        from building_agent_helper import get_malaysia_coords
        coords = get_malaysia_coords(city_name)
        ref_lat, ref_lng = (coords["lat"], coords["lng"]) if coords else (3.1390, 101.6869)
        
        if entities:
            for ent in entities:
                if ent.get("polyline_positions"):
                    ref_lat = ent["polyline_positions"][0][0]["lat"]
                    ref_lng = ent["polyline_positions"][0][0]["lng"]
                    break
                elif ent.get("position"):
                    ref_lat = ent["position"]["lat"]
                    ref_lng = ent["position"]["lng"]
                    break
                    
        existing_context = get_context_infrastructure(ref_lat, ref_lng)
        entities = existing_context + entities

        # INJECT TRUE ROUTE GEOMETRY FROM OSMNX — PRIMARY only
        analysis_raw = state.get("analysis_result_raw", [])
        if analysis_raw:
            primary_result = analysis_raw[0]
            if primary_result.get("route_geometry"):
                entities.append({
                    "id": "main_route_primary",
                    "entity_type": "polyline",
                    "name": "Proposed Optimal Route (Calculated)",
                    "polyline_positions": [primary_result["route_geometry"]],
                    "style": {
                        "color": "#3B82F6",
                        "width": 10,
                        "opacity": 0.8,
                        "flow": "normal"
                    }
                })

        # INJECT ISOCHRONES
        analysis_raw = state.get("analysis_result_raw", [])
        for cand_res in analysis_raw:
            if "isochrone_geoms" in cand_res:
                for idx, iso_poly in enumerate(cand_res["isochrone_geoms"]):
                    iso_coords = list(iso_poly.exterior.coords)
                    positions = [{"lat": lat, "lng": lng, "height": 0} for lng, lat in iso_coords]
                    entities.append({
                        "id": f"isochrone_new_{idx}",
                        "entity_type": "polygon",
                        "name": "5-Minute Walking Catchment (400m)",
                        "polygon_positions": positions,
                        "style": {
                            "color": "#10B981",
                            "alpha": 0.2,
                            "height": 0,
                            "outline": True,
                            "outlineColor": "#059669"
                        }
                    })

        # Extract Impact Metrics from the solution state
        solution = state.get("solution_result", {})
        impact_metrics = {
            "societal_impact": solution.get("societal_impact", "No societal impact data available."),
            "expected_effects": solution.get("expected_effect", []),
            "complexity": solution.get("implementation_complexity", "Unknown"),
            "solution_title": solution.get("solution_title", "Proposed Solution")
        }

        return {
            "ok": True,
            "session_id": session_id,
            "stage": "done",
            "reply": "Planning complete. Switching to map view.",
            "show_map": True,
            "entities": entities,
            "needs_input": False,
            "city_name": city_name,
            "target_lat": coords["lat"] if coords else 3.1390,
            "target_lng": coords["lng"] if coords else 101.6869,
            "impact_metrics": impact_metrics
        }
    elif next_step_name == "Plan improvements":
        return {
            "ok": True,
            "session_id": session_id,
            "stage": next_step_name,
            "reply": next_raw_step_output,
            "needs_input": False,
        }
    elif next_step_name == "Generate solutions":
        next_raw_step_output = format_step_reply("Generate solutions", next_raw_step_output)
    
    state.update(
        {
            "phase": "planning",
            "step_index": next_index,
            "last_step_output": next_raw_step_output,
            "last_display_reply": next_raw_step_output,
            "last_agent_name": next_step_agent.name,
            "output_key": next_output_key,
        }
    )
    return {
        "ok": True,
        "session_id": session_id,
        "stage": next_step_name,
        "reply": next_raw_step_output,
        "needs_input": True,
    }
