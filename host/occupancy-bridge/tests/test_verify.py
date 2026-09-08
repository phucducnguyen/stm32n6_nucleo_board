"""Tests for the bring-up ``verify`` mode (pure analysis + capture + verdict)."""

import os
import unittest

from occbridge.protocol import Box, Frame
from occbridge.sources import FileReplaySource
from occbridge.verify import (
    REC_COUNT_ONLY,
    REC_DWELL,
    REC_FAIL_PREFIX,
    analyze,
    capture,
    format_report,
)

SAMPLE = os.path.join(os.path.dirname(__file__), "sample_verify.log")


def _frame(seq, ts, count, boxes=()):
    return Frame(seq=seq, ts_ms=ts, count=count, boxes=tuple(boxes))


def _stable_stream(n=10, *, step_ms=33):
    """Two ids (7, 9) that persist across every frame at ~30 fps."""
    return [
        _frame(
            i,
            1000 + i * step_ms,
            2,
            (Box(7, 500, 430, 120, 340, 88), Box(9, 300, 450, 110, 300, 80)),
        )
        for i in range(n)
    ]


def _churning_stream(n=10, *, step_ms=33):
    """A fresh, never-repeating id every frame (tracker churn)."""
    return [
        _frame(i, 1000 + i * step_ms, 1, (Box(100 + i, 500, 430, 120, 340, 88),))
        for i in range(n)
    ]


class TestAnalyzeMetrics(unittest.TestCase):
    def test_frame_rate_and_counts(self):
        frames = _stable_stream(10, step_ms=33)
        r = analyze(frames, lines_total=12, parse_drops=2)
        self.assertEqual(r.valid_frames, 10)
        self.assertEqual(r.lines_total, 12)
        self.assertEqual(r.parse_drops, 2)
        self.assertAlmostEqual(r.parse_drop_rate, 2 / 12)
        self.assertEqual(r.interframe_ms_median, 33.0)
        self.assertAlmostEqual(r.fps, 1000.0 / 33.0, places=3)
        self.assertEqual(r.count_min, 2)
        self.assertEqual(r.count_max, 2)
        self.assertEqual(r.count_mean, 2.0)
        self.assertEqual(r.frames_with_person, 10)

    def test_track_metrics_stable(self):
        frames = _stable_stream(10)
        r = analyze(frames, lines_total=10, parse_drops=0)
        self.assertEqual(r.unique_track_ids, 2)
        self.assertEqual(r.mean_track_lifetime, 10.0)  # each id in all 10 frames
        self.assertEqual(r.max_concurrent_ids, 2)

    def test_wrap_and_stall_excluded_from_fps(self):
        # A backwards ts (wrap) and a 30s gap must not poison the timing stats.
        frames = [
            _frame(0, 1000, 1, (Box(7, 1, 1, 1, 1, 80),)),
            _frame(1, 1033, 1, (Box(7, 1, 1, 1, 1, 80),)),
            _frame(2, 500, 1, (Box(7, 1, 1, 1, 1, 80),)),     # wrap (negative)
            _frame(3, 533, 1, (Box(7, 1, 1, 1, 1, 80),)),
            _frame(4, 60_000, 1, (Box(7, 1, 1, 1, 1, 80),)),  # 59.4s stall
        ]
        r = analyze(frames, lines_total=5, parse_drops=0)
        # Only the two clean 33ms deltas survive.
        self.assertEqual(r.interframe_ms_min, 33.0)
        self.assertEqual(r.interframe_ms_max, 33.0)

    def test_id_zero_is_not_a_real_track(self):
        frames = [
            _frame(i, 1000 + i * 33, 1, (Box(0, 1, 1, 1, 1, 80),)) for i in range(6)
        ]
        r = analyze(frames, lines_total=6, parse_drops=0)
        self.assertEqual(r.unique_ids, 1)        # the 0 sentinel is seen
        self.assertEqual(r.unique_track_ids, 0)  # ...but it is not a real track
        self.assertFalse(r.track_ids_present)
        self.assertIsNone(r.mean_track_lifetime)


class TestVerdict(unittest.TestCase):
    def test_stable_ids_recommend_dwell(self):
        r = analyze(_stable_stream(10), lines_total=10, parse_drops=0)
        self.assertTrue(r.detects_people)
        self.assertTrue(r.boxes_present)
        self.assertTrue(r.track_ids_present)
        self.assertTrue(r.track_ids_stable)
        self.assertTrue(r.frame_rate_healthy)
        self.assertEqual(r.recommendation, REC_DWELL)
        self.assertTrue(r.is_go)

    def test_churning_ids_recommend_count_only(self):
        r = analyze(_churning_stream(10), lines_total=10, parse_drops=0)
        self.assertTrue(r.detects_people)
        self.assertTrue(r.track_ids_present)
        self.assertFalse(r.track_ids_stable)  # mean lifetime == 1 frame
        self.assertEqual(r.mean_track_lifetime, 1.0)
        self.assertEqual(r.recommendation, REC_COUNT_ONLY)
        self.assertTrue(r.is_go)

    def test_no_people_fails(self):
        frames = [_frame(i, 1000 + i * 33, 0) for i in range(8)]
        r = analyze(frames, lines_total=8, parse_drops=0)
        self.assertFalse(r.detects_people)
        self.assertTrue(r.recommendation.startswith(REC_FAIL_PREFIX))
        self.assertFalse(r.is_go)

    def test_insufficient_data_fails(self):
        r = analyze([_frame(0, 1000, 1, (Box(7, 1, 1, 1, 1, 80),))],
                    lines_total=1, parse_drops=0)
        self.assertIsNone(r.fps)
        self.assertTrue(r.recommendation.startswith(REC_FAIL_PREFIX))
        self.assertFalse(r.is_go)

    def test_empty_capture_fails_gracefully(self):
        r = analyze([], lines_total=5, parse_drops=5)
        self.assertEqual(r.valid_frames, 0)
        self.assertTrue(r.recommendation.startswith(REC_FAIL_PREFIX))
        self.assertFalse(r.is_go)

    def test_low_frame_rate_fails(self):
        # 1 fps (1000ms deltas) with people present -> FAIL on rate.
        frames = [
            _frame(i, 1000 + i * 1000, 1, (Box(7, 1, 1, 1, 1, 80),)) for i in range(5)
        ]
        r = analyze(frames, lines_total=5, parse_drops=0, min_fps=5.0)
        self.assertFalse(r.frame_rate_healthy)
        self.assertTrue(r.recommendation.startswith(REC_FAIL_PREFIX))
        self.assertIn("frame rate", r.recommendation)


class TestCapture(unittest.TestCase):
    def test_capture_tallies_lines_frames_drops(self):
        with FileReplaySource(SAMPLE) as src:
            frames, lines_total, drops = capture(src)
        self.assertEqual(lines_total, 20)   # 17 DET + 2 boot + 1 warn
        self.assertEqual(len(frames), 17)
        self.assertEqual(drops, 3)

    def test_capture_time_budget_stops_early(self):
        # clock calls: [deadline-init, after line0, after line1 -> >= deadline]
        clock = iter([0.0, 0.0, 5.0, 5.0]).__next__
        lines = ["DET 0 0 0", "DET 1 33 0", "DET 2 66 0", "DET 3 99 0"]
        frames, total, drops = capture(lines, max_seconds=1.0, clock=clock)
        self.assertEqual(total, 2)  # stopped once the deadline passed

    def test_capture_should_stop(self):
        lines = ["DET 0 0 0", "DET 1 33 0", "DET 2 66 0"]
        seen = {"n": 0}

        def stop():
            seen["n"] += 1
            return seen["n"] > 1

        frames, total, drops = capture(lines, should_stop=stop)
        self.assertEqual(total, 1)


class TestReplayVerdict(unittest.TestCase):
    def test_sample_verify_log_is_dwell_feasible(self):
        with FileReplaySource(SAMPLE) as src:
            frames, lines_total, drops = capture(src)
        r = analyze(frames, lines_total=lines_total, parse_drops=drops)
        self.assertTrue(r.detects_people)
        self.assertTrue(r.track_ids_present)
        self.assertTrue(r.track_ids_stable)  # ids 7 & 9 persist many frames
        self.assertTrue(r.frame_rate_healthy)  # ~30 fps
        self.assertEqual(r.recommendation, REC_DWELL)

    def test_format_report_contains_verdict(self):
        r = analyze(_stable_stream(10), lines_total=10, parse_drops=0)
        text = format_report(r)
        self.assertIn("VERDICT", text)
        self.assertIn("DETECTS PEOPLE?", text)
        self.assertIn(REC_DWELL, text)


if __name__ == "__main__":
    unittest.main()
