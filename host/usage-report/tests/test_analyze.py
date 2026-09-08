"""Tests for usagereport.analyze — correctness on hand-built fixtures."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from usagereport.analyze import (
    AnalysisConfig,
    OpenHours,
    analyze,
    parse_days,
    parse_open_window,
    parse_tz_offset,
)
from usagereport.events import OccEvent

UTC = timezone.utc
# 2026-06-15 is a Monday (weekday 0).
DAY = lambda h, m=0: datetime(2026, 6, 15, h, m, tzinfo=UTC)  # noqa: E731


def occ(count, h, m=0, node="n6-occ-test", space="cafe"):
    return OccEvent(node, "occupancy_count", DAY(h, m), 0.9, {"count": count, "space": space})


# Open 09:00–12:00, hourly buckets, UTC.
CFG = AnalysisConfig(open_hours=OpenHours(540, 720), tz_offset_minutes=0, bucket_minutes=60)


class TestCoreOccupancy(unittest.TestCase):
    def setUp(self):
        # counts over the morning: 0, 2, 5(peak), 3, 0, 0
        self.events = [
            occ(0, 9, 0),
            occ(2, 9, 30),
            occ(5, 10, 0),
            occ(3, 10, 30),
            occ(0, 11, 0),
            occ(0, 11, 30),
            OccEvent("n6-occ-test", "heartbeat", DAY(9, 0), None, {"space": "cafe"}),
        ]
        self.s = analyze(self.events, CFG)

    def test_not_empty(self):
        self.assertFalse(self.s.is_empty)
        self.assertTrue(self.s.has_occupancy)

    def test_peak(self):
        self.assertEqual(self.s.peak_count, 5)
        self.assertEqual(self.s.peak_at.hour, 10)
        self.assertEqual(self.s.peak_at.minute, 0)

    def test_utilization(self):
        # 3 open hourly buckets; occupied in 09:00 (max2) and 10:00 (max5);
        # 11:00 bucket is empty -> 2/3 utilized.
        self.assertEqual(self.s.available_hours, 3.0)
        self.assertEqual(self.s.occupied_hours, 2.0)
        self.assertAlmostEqual(self.s.utilization, 2 / 3)

    def test_avg_when_busy(self):
        self.assertAlmostEqual(self.s.avg_when_busy, (2 + 5 + 3) / 3)

    def test_visits(self):
        # positive deltas: 0->2 (+2), 2->5 (+3); rest are drops.
        self.assertEqual(self.s.visits_estimate, 5)

    def test_busiest_hour(self):
        top = self.s.busiest_hours[0]
        self.assertEqual((top.weekday, top.hour), (0, 10))
        self.assertAlmostEqual(top.mean_count, 4.0)

    def test_quietest_open_hour(self):
        q = self.s.quietest_open_hours[0]
        self.assertEqual((q.weekday, q.hour), (0, 11))
        self.assertAlmostEqual(q.mean_count, 0.0)

    def test_busiest_day(self):
        d = self.s.busiest_days[0]
        self.assertEqual(d.weekday, 0)
        self.assertEqual(d.peak_count, 5)

    def test_longest_empty_block(self):
        b = self.s.longest_empty_open
        self.assertIsNotNone(b)
        self.assertEqual(b.minutes, 60.0)
        self.assertEqual(b.start.hour, 11)

    def test_coverage_full_and_heartbeat(self):
        self.assertIsNotNone(self.s.coverage)
        self.assertAlmostEqual(self.s.coverage.uptime, 1.0)
        self.assertTrue(self.s.coverage.has_heartbeat)
        self.assertIsNone(self.s.coverage.largest_gap)

    def test_hourly_profile(self):
        self.assertAlmostEqual(self.s.hourly_profile[10], 4.0)
        self.assertIsNone(self.s.hourly_profile[3])

    def test_event_counts(self):
        self.assertEqual(self.s.event_counts["occupancy_count"], 6)
        self.assertEqual(self.s.event_counts["heartbeat"], 1)


class TestEmptyAndSparse(unittest.TestCase):
    def test_no_events(self):
        s = analyze([], CFG)
        self.assertTrue(s.is_empty)
        self.assertEqual(s.peak_count, None)
        self.assertEqual(s.visits_estimate, 0)

    def test_only_heartbeats(self):
        evs = [OccEvent("n6-occ-test", "heartbeat", DAY(9, 0), None, {"space": "cafe"})]
        s = analyze(evs, CFG)
        self.assertTrue(s.is_empty)
        # coverage still computed from heartbeat presence
        self.assertIsNotNone(s.coverage)
        self.assertTrue(s.coverage.has_heartbeat)

    def test_single_sample(self):
        s = analyze([occ(4, 10, 0)], CFG)
        self.assertEqual(s.peak_count, 4)
        self.assertEqual(s.visits_estimate, 4)  # 0 -> 4


class TestCoverageGap(unittest.TestCase):
    def test_gap_detected(self):
        # Data at 09:00 and 11:30 only; the 10:00 hourly bucket has no data.
        evs = [occ(2, 9, 0), occ(2, 11, 30)]
        s = analyze(evs, CFG)
        self.assertLess(s.coverage.uptime, 1.0)
        self.assertIsNotNone(s.coverage.largest_gap)
        self.assertEqual(s.coverage.largest_gap.minutes, 60.0)
        self.assertFalse(s.coverage.has_heartbeat)


class TestOptionalSections(unittest.TestCase):
    def test_dwell(self):
        evs = [
            OccEvent("n", "dwell_sample", DAY(10), 0.7, {"dwell_s": 100, "zone": "a", "space": "c"}),
            OccEvent("n", "dwell_sample", DAY(10), 0.7, {"dwell_s": 200, "zone": "a", "space": "c"}),
            OccEvent("n", "dwell_sample", DAY(10), 0.7, {"dwell_s": 400, "zone": "a", "space": "c"}),
            OccEvent("n", "dwell_sample", DAY(10), 0.7, {"dwell_s": 600, "zone": "b", "space": "c"}),
        ]
        s = analyze(evs, AnalysisConfig(dwell_long_s=300))
        self.assertIsNotNone(s.dwell)
        zone_a = next(z for z in s.dwell.by_zone if z.zone == "a")
        self.assertAlmostEqual(zone_a.avg_s, (100 + 200 + 400) / 3)
        self.assertEqual(zone_a.median_s, 200.0)
        self.assertEqual(zone_a.long_count, 1)
        self.assertEqual(s.dwell.overall.samples, 4)

    def test_queue(self):
        evs = [
            OccEvent("n", "queue_length", DAY(12), 0.8, {"length": 1, "zone": "line", "space": "c"}),
            OccEvent("n", "queue_length", DAY(12, 5), 0.8, {"length": 4, "zone": "line", "space": "c"}),
            OccEvent("n", "queue_length", DAY(12, 10), 0.8, {"length": 5, "zone": "line", "space": "c"}),
        ]
        s = analyze(evs, AnalysisConfig(queue_threshold=3))
        self.assertIsNotNone(s.queue)
        q = s.queue.by_zone[0]
        self.assertEqual(q.peak_length, 5)
        self.assertAlmostEqual(q.avg_length, 10 / 3)
        self.assertEqual(q.over_threshold_samples, 2)

    def test_zone_occupancy(self):
        evs = [
            OccEvent("n", "zone_occupancy", DAY(12), 0.8, {"zones": {"seats": 3, "counter": 1}, "space": "c"}),
            OccEvent("n", "zone_occupancy", DAY(12, 1), 0.8, {"zones": {"seats": 1, "counter": 1}, "space": "c"}),
        ]
        s = analyze(evs)
        self.assertIsNotNone(s.zones)
        seats = next(z for z in s.zones.by_zone if z.zone == "seats")
        self.assertAlmostEqual(seats.mean_count, 2.0)
        self.assertAlmostEqual(seats.attention_share, 2 / 3)

    def test_absent_sections_are_none(self):
        s = analyze([occ(3, 10)], CFG)
        self.assertIsNone(s.dwell)
        self.assertIsNone(s.queue)
        self.assertIsNone(s.zones)


class TestSpaceFiltering(unittest.TestCase):
    def test_space_filter(self):
        evs = [occ(2, 10, space="cafe"), occ(9, 10, space="garage")]
        s = analyze(evs, CFG, space="cafe")
        self.assertEqual(s.space, "cafe")
        self.assertEqual(s.peak_count, 2)


class TestClosedDaysAndTz(unittest.TestCase):
    def test_closed_day_zero_available(self):
        # Monday closed -> no available hours that day.
        cfg = AnalysisConfig(open_hours=OpenHours(540, 720, frozenset({0})))
        s = analyze([occ(3, 10)], cfg)
        self.assertEqual(s.available_hours, 0.0)
        self.assertEqual(s.utilization, 0.0)

    def test_tz_offset_shifts_local_hour(self):
        # 10:00 UTC becomes 03:00 at -07:00.
        cfg = AnalysisConfig(tz_offset_minutes=-7 * 60)
        s = analyze([occ(3, 10)], cfg)
        self.assertEqual(s.peak_at.hour, 3)


class TestConfigParsers(unittest.TestCase):
    def test_open_window(self):
        self.assertEqual(parse_open_window("07:00-18:00"), (420, 1080))
        self.assertEqual(parse_open_window("00:00-24:00"), (0, 1440))

    def test_open_window_bad(self):
        for bad in ("18:00-07:00", "7-18", "07:00", "25:00-26:00"):
            with self.assertRaises(ValueError):
                parse_open_window(bad)

    def test_tz_offset(self):
        self.assertEqual(parse_tz_offset("+00:00"), 0)
        self.assertEqual(parse_tz_offset("-07:00"), -420)
        self.assertEqual(parse_tz_offset("+05:30"), 330)
        self.assertEqual(parse_tz_offset("Z"), 0)

    def test_days(self):
        self.assertEqual(parse_days("sun"), frozenset({6}))
        self.assertEqual(parse_days("sat,sun"), frozenset({5, 6}))
        with self.assertRaises(ValueError):
            parse_days("funday")


if __name__ == "__main__":
    unittest.main()
