def calculate_gtfs_completeness(route_frequency: list[dict]) -> float:
    """Assess how much of the GTFS data has valid headway info."""
    if not route_frequency:
        return 0.0
    valid = sum(1 for r in route_frequency if r.get("median_headway_min") is not None)
    return round(valid / len(route_frequency), 2)


def calculate_osm_coverage_score(transit_coverage: dict | None) -> float:
    """Extract and normalize OSM coverage from summary."""
    if not transit_coverage:
        return 0.0
    return round(transit_coverage.get("coverage_score", 0.0), 2)


def calculate_route_signal_strength(routes: list[dict]) -> float:
    """Evaluate aggregate signal strength based on evidence scores."""
    if not routes:
        return 0.0
    scores = [r.get("evidence_score", 0.0) for r in routes]
    return round(sum(scores) / len(scores), 2)


def calculate_inference_confidence(signal: float, completeness: float) -> float:
    """
    Weighted confidence model.
    In the future, this can be expanded into a calibrated statistical model.
    """
    return round((signal * 0.7) + (completeness * 0.3), 2)
