"""Tests for usagereport.render — output shape, plain language, graceful gaps."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from usagereport.analyze import AnalysisConfig, OpenHours, analyze
from usagereport.events import OccEvent
from usagereport.render import render, render_markdown, render_text

UTC = timezone.utc
DAY = lambda h, m=0: datetime(2026, 6, 15, h, m, tzinfo=UTC)  # noqa: E731


def occ(count, h, m=0):
    return OccEvent("n6-occ-cafe", "occupancy_count", DAY(h, m), 0.9, {"count": count, "space": "cafe"})


CFG = AnalysisConfig(open_hours=OpenHours(540, 720), bucket_minutes=60)


class TestPopulatedReport(unittest.TestCase):
    def setUp(self):
        self.s = analyze([occ(0, 9), occ(2, 9, 30), occ(5, 10), occ(3, 10, 30), occ(0, 11)], CFG)

    def test_text_has_headline_and_takeaway(self):
        out = render_text(self.s)
        self.assertIn("SPACE USAGE REPORT", out)
        self.assertIn("Busiest:", out)
        self.assertIn("busiest window", out)  # takeaway line
        self.assertIn("HOW BUSY", out)

    def test_markdown_structure(self):
        out = render_markdown(self.s)
        self.assertTrue(out.startswith("# SPACE USAGE REPORT"))
        self.assertIn("## HOW BUSY", out)
        self.assertIn("**Takeaway:**", out)
        self.assertIn("```", out)  # hourly bar code block

    def test_no_jargon(self):
        out = render_text(self.s).lower()
        for jargon in ("utilization%", "deflated", "p50", "stddev", "percentile"):
            self.assertNotIn(jargon, out)

    def test_peak_people_pluralization(self):
        out = render_text(self.s)
        self.assertIn("5 people", out)

    def test_dispatch(self):
        self.assertEqual(render(self.s, "txt"), render_text(self.s))
        self.assertEqual(render(self.s, "md"), render_markdown(self.s))
        with self.assertRaises(ValueError):
            render(self.s, "pdf")


class TestEmptyReport(unittest.TestCase):
    def test_empty_does_not_crash(self):
        s = analyze([], CFG)
        txt = render_text(s)
        md = render_markdown(s)
        self.assertIn("No occupancy", txt)
        self.assertIn("No occupancy", md)

    def test_only_heartbeats(self):
        s = analyze([OccEvent("n", "heartbeat", DAY(9), None, {"space": "cafe"})], CFG)
        # should render without raising and disclose no occupancy
        self.assertIn("No occupancy", render_text(s))


class TestDwellAbsentPath(unittest.TestCase):
    def test_dwell_section_omitted_when_absent(self):
        s = analyze([occ(3, 10)], CFG)
        out = render_text(s)
        self.assertNotIn("DWELL", out)
        self.assertNotIn("QUEUE", out)

    def test_dwell_section_present_when_data(self):
        evs = [
            occ(3, 10),
            OccEvent("n", "dwell_sample", DAY(10), 0.7, {"dwell_s": 120, "zone": "z", "space": "cafe"}),
        ]
        s = analyze(evs, CFG)
        out = render_text(s)
        self.assertIn("DWELL", out)


class TestSingleSample(unittest.TestCase):
    def test_sparse_renders(self):
        s = analyze([occ(1, 10)], CFG)
        out = render_text(s)
        self.assertIn("1 person", out)  # singular


if __name__ == "__main__":
    unittest.main()
