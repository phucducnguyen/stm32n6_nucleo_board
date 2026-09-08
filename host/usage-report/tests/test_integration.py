"""End-to-end: synth -> parse -> analyze -> render, plus the CLI."""

from __future__ import annotations

import io
import json
import unittest

from tools import synth
from usagereport.analyze import AnalysisConfig, OpenHours, analyze, parse_open_window
from usagereport.cli import main as cli_main
from usagereport.events import read_events
from usagereport.render import render_markdown, render_text


def _synth_lines(seed=1):
    return [json.dumps(ev, separators=(",", ":")) for ev in synth.generate(seed=seed, weeks=1)]


class TestSynthDeterministic(unittest.TestCase):
    def test_same_seed_same_output(self):
        self.assertEqual(_synth_lines(7), _synth_lines(7))

    def test_different_seed_differs(self):
        self.assertNotEqual(_synth_lines(1), _synth_lines(2))

    def test_all_lines_valid_events(self):
        res = read_events(_synth_lines())
        self.assertEqual(res.malformed, 0)
        self.assertEqual(res.unknown, 0)
        self.assertGreater(res.kept, 1000)

    def test_envelope_shape(self):
        ev = next(synth.generate())
        self.assertEqual(ev["v"], 0)
        self.assertIn("node", ev)
        self.assertIn("event", ev)
        self.assertIn("data", ev)
        self.assertIn("space", ev["data"])


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        res = read_events(_synth_lines())
        start, end = parse_open_window("07:00-18:00")
        cfg = AnalysisConfig(open_hours=OpenHours(start, end), bucket_minutes=30)
        self.summary = analyze(res.events, cfg)

    def test_sensible_metrics(self):
        s = self.summary
        self.assertFalse(s.is_empty)
        self.assertGreater(s.peak_count, 3)        # a busy café peaks well above 3
        self.assertGreater(s.utilization, 0.5)     # open most of its hours
        self.assertGreater(s.visits_estimate, 100) # a week of footfall
        self.assertIsNotNone(s.dwell)
        self.assertIsNotNone(s.queue)
        self.assertIsNotNone(s.coverage)

    def test_saturday_is_busiest_day(self):
        # synth makes Saturday (weekday 5) the busiest.
        self.assertEqual(self.summary.busiest_days[0].weekday, 5)

    def test_open_hours_coverage_full(self):
        # the deliberate synth gap is off-hours, so open-hours uptime is 100%.
        self.assertAlmostEqual(self.summary.coverage.uptime, 1.0)

    def test_renders_both_formats(self):
        txt = render_text(self.summary)
        md = render_markdown(self.summary)
        self.assertIn("Busiest:", txt)
        self.assertIn("# SPACE USAGE REPORT", md)
        self.assertGreater(len(txt), 500)


class TestCli(unittest.TestCase):
    def test_cli_stdin_to_stdout(self):
        import sys

        data = "\n".join(_synth_lines())
        old_in, old_out = sys.stdin, sys.stdout
        sys.stdin = io.StringIO(data)
        sys.stdout = io.StringIO()
        try:
            rc = cli_main(["--input", "-", "--open", "07:00-18:00", "--format", "txt", "--quiet"])
            out = sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = old_in, old_out
        self.assertEqual(rc, 0)
        self.assertIn("SPACE USAGE REPORT", out)

    def test_cli_bad_window(self):
        rc = cli_main(["--input", "-", "--open", "garbage", "--quiet"])
        self.assertEqual(rc, 2)

    def test_cli_missing_file(self):
        rc = cli_main(["--input", "/no/such/file.jsonl"])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
