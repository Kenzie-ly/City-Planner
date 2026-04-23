import unittest
from datetime import date
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from evidence_pipeline import (
    AuditResult,
    audit_osm_transit_gap,
    cluster_findings_to_area_options,
    compute_merged_confidence,
    filter_trusted_evidence,
    score_report_signal,
)


class EvidencePipelineUnitTests(unittest.TestCase):
    def test_score_report_signal_tier_recency_dedup(self):
        option = {
            "google_evidence": [
                {
                    "title": "Industrial expansion announced",
                    "url": "https://gov.my/report-1",
                    "published_at": "2026-01-10",
                    "source_tier": "government",
                    "claim_key": "ind_expansion",
                    "area_label": "Section 13",
                },
                {
                    "title": "Industrial expansion announced duplicate coverage",
                    "url": "https://majornews.my/article",
                    "published_at": "2026-01-20",
                    "source_tier": "major_media",
                    "claim_key": "ind_expansion",
                    "area_label": "Section 13",
                },
                {
                    "title": "New housing township phase opens",
                    "url": "https://operator.my/notice",
                    "published_at": "2025-08-01",
                    "source_tier": "operator",
                    "claim_key": "township_phase",
                    "area_label": "Section 13",
                },
            ]
        }
        score = score_report_signal(option, current_date=date(2026, 4, 23))
        self.assertGreaterEqual(score, 0.6)
        self.assertLessEqual(score, 1.0)

    def test_audit_osm_transit_gap_scoring_and_completeness(self):
        def fake_fetcher(url, payload, headers, timeout):
            if "nominatim" in url:
                return [{"lat": "3.07", "lon": "101.52"}]
            return {
                "elements": [
                    {"tags": {"railway": "station"}},
                    {"tags": {"highway": "bus_stop"}},
                    {"tags": {"route": "bus"}},
                    {"tags": {"highway": "footway"}},
                    {"tags": {"building": "yes"}},
                    {"tags": {"building": "yes"}},
                ]
            }

        res = audit_osm_transit_gap("Shah Alam", "Section 13", fetcher=fake_fetcher)
        self.assertIsInstance(res, AuditResult)
        self.assertGreaterEqual(res.gap_score, 0.0)
        self.assertLessEqual(res.gap_score, 1.0)
        self.assertGreater(res.completeness_score, 0.0)
        self.assertTrue(res.audit_details["matched"])

    def test_compute_merged_confidence_boundaries(self):
        low = compute_merged_confidence(
            report_score=0.5,
            gap_score=0.5,
            feasibility=False,
            equity_flag=False,
            completeness_score=0.5,
        )
        high = compute_merged_confidence(
            report_score=0.95,
            gap_score=0.9,
            feasibility=True,
            equity_flag=True,
            completeness_score=0.95,
        )
        self.assertLess(low["confidence"], 0.68)
        self.assertFalse(low["pass_gate"])
        self.assertGreaterEqual(high["confidence"], 0.68)
        self.assertTrue(high["pass_gate"])

    def test_equity_option_enforcement(self):
        findings = [
            {
                "title": "New township growth in Section 7",
                "url": "https://majornews.my/1",
                "snippet": "Population increase expected.",
                "published_at": "2026-01-01",
                "source_tier": "major_media",
                "claim_type": "population",
                "area_label": "Section 7",
            },
            {
                "title": "Industrial park expansion in Section 13",
                "url": "https://majornews.my/2",
                "snippet": "More jobs incoming.",
                "published_at": "2026-02-01",
                "source_tier": "major_media",
                "claim_type": "industrial",
                "area_label": "Section 13",
            },
        ]
        options = cluster_findings_to_area_options(findings, city="Shah Alam", current_date=date(2026, 4, 23))
        self.assertTrue(options)
        self.assertTrue(any(o.get("equity_flag") for o in options))

    def test_filter_trusted_evidence_https_unique_domains(self):
        evidence = [
            {
                "title": "Gov update",
                "url": "https://mot.gov.my/a",
                "published_at": "2026-03-01",
                "source_tier": "government",
            },
            {
                "title": "Same domain duplicate",
                "url": "https://mot.gov.my/b",
                "published_at": "2026-03-02",
                "source_tier": "government",
            },
            {
                "title": "Operator update",
                "url": "https://rapidkl.com.my/c",
                "published_at": "2026-02-01",
                "source_tier": "operator",
            },
            {
                "title": "Untrusted source",
                "url": "https://forum.example.com/d",
                "published_at": "2026-02-05",
                "source_tier": "community",
            },
            {
                "title": "Non-https source",
                "url": "http://media.my/e",
                "published_at": "2026-02-05",
                "source_tier": "major_media",
            },
        ]
        trusted = filter_trusted_evidence(evidence, current_date=date(2026, 4, 23), min_sources=2, max_sources=3)
        self.assertEqual(len(trusted), 2)
        self.assertTrue(all(str(x.get("url", "")).startswith("https://") for x in trusted))
        self.assertTrue(all(str(x.get("source_tier", "")).lower() in {"government", "operator", "study", "major_media", "local_media"} for x in trusted))


if __name__ == "__main__":
    unittest.main()
