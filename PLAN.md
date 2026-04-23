## Growth-Led, User-Selected, Dual-Evidence Transit Planning Flow

### Summary
Refactor the current pipeline into a **growth-first, user-selection, evidence-gated** flow:
1) system discovers candidate growth hotspots from Google evidence,  
2) user selects area(s),  
3) system runs OSM transit-gap audit,  
4) system merges both signals into confidence,  
5) solutions are generated only if confidence + feasibility pass thresholds.

This keeps users in control while reducing hallucinations and weak recommendations.

### Implementation Changes
1. **Add new pre-planning stages in orchestrator**
- Insert stages between intake and current planning:
  - `area_option_generation (one title + one chart (if the source has) + the (3 sentences)' explanation)`
  - `area_selection`
  - `evidence_merge_and_gate`
- Keep existing downstream stages (`planning -> solution -> building`) but only enter them after gate pass.

2. **Introduce a structured “Area Option” contract**
- Add internal object for each candidate area:
  - `area_label`, `city`, `growth_signals`, `google_evidence[]`, `report_score`, `equity_flag`
- Return options to UI in a machine-readable payload (not only plain text), with citations and confidence label.

3. **Google evidence pipeline (A)**
- Run 3 search iterations per city with fixed query families:
  - population/housing growth,
  - industrial/job cluster growth,
  - major trip generators (education/health/commercial).
- Parse and deduplicate results into area clusters.
- Score `report_score` by:
  - source tier (gov/statutory/operator > major media > others),
  - corroboration count across independent sources,
  - recency decay.
- Always include one `equity_flag=true` option even if not top growth.

4. **OSM transit audit pipeline (B)**
- For selected area(s), compute:
  - nearby station count,
  - bus-stop density,
  - transit route presence,
  - walkability proxy near transit (where available),
  - data completeness signal.
- Produce `gap_score` (higher = bigger transit gap) and `completeness_score`.

5. **Merge + gate logic**
- Default merged score:
  - `0.45 * report_score + 0.35 * gap_score + 0.15 * routing_feasibility + 0.05 * equity_weight`
  - multiply by `completeness_factor` from OSM data quality.
- Confidence bands:
  - `>=0.68 High`, `0.50-0.67 Medium`, `<0.50 Low`.
- Gate:
  - build solution only when `confidence >= 0.68` and routing feasibility passes.
  - otherwise return “needs verification” with top missing evidence.

6. **Integrate with current backend**
- Update orchestration/state handling in [app.py](D:/hackathon/backend/app.py).
- Adjust prompt behavior for [agent.py](D:/hackathon/backend/agent.py):
  - `find_needs_agent` shifts from “top-3 broad challenges first” to “challenge synthesis from selected area + merged evidence.”
- Add a new evidence/scoring helper module (for separation of concerns) and keep current routing analysis reuse.

### Public Interface / API Additions
- `/api/chat` response gains new optional fields during selection stages:
  - `area_options: [{ id, area_label, rationale, evidence_links[], confidence_label, equity_flag }]`
  - `needs_selection: true/false`
  - `evidence_summary` after selection
- Existing fields remain for backward compatibility (`stage`, `reply`, `needs_input`).

### Test Plan
1. **Unit tests**
- Source-tier and recency scoring behavior.
- Dedup clustering of Google findings.
- OSM audit scoring and completeness penalties.
- Merge score + threshold gating (pass/fail boundaries).

2. **Integration tests (mocked external calls)**
- Full happy path: intake -> options -> selection -> gate pass -> planning/solution/building.
- Low-completeness path: selected area returns uncertain OSM data -> “needs verification”.
- Single-source spike path: strong report signal but weak OSM -> should not auto-build.
- Equity option presence guaranteed in every option list.

3. **Regression tests**
- Existing pipeline still works when new stage flags are disabled/fallback mode is used.

### Assumptions and Defaults
- Google findings are treated as **signal**, not ground truth.
- External calls (Google/OSM) are mocked in tests; no live-network dependency in CI.
- Default selection supports 1–2 areas; first selected area drives primary downstream planning.
- Malaysia-only scope remains.
- If new structured option payload fails, system falls back to current text-based challenge flow.
