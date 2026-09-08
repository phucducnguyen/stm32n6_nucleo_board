"""Tests for usagereport.events — parsing, tolerance, and timestamp handling."""

from __future__ import annotations

import json
import unittest
from datetime import timezone

from usagereport.events import (
    OccEvent,
    parse_line,
    parse_ts,
    read_events,
)


def _line(**kw) -> str:
    obj = {
        "v": 0,
        "node": "n6-occ-test",
        "event": "occupancy_count",
        "confidence": 0.9,
        "data": {"count": 3, "space": "test"},
        "ts": "2026-06-15T09:00:00+00:00",
    }
    obj.update(kw)
    return json.dumps(obj)


class TestTimestamps(unittest.TestCase):
    def test_iso_with_offset(self):
        dt = parse_ts("2026-06-15T09:00:00+00:00")
        self.assertEqual(dt.year, 2026)
        self.assertIsNotNone(dt.tzinfo)

    def test_iso_with_z(self):
        dt = parse_ts("2026-06-15T09:00:00Z")
        self.assertEqual(dt.utcoffset().total_seconds(), 0)

    def test_naive_iso_assumed_utc(self):
        dt = parse_ts("2026-06-15T09:00:00")
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_epoch_int(self):
        dt = parse_ts(1781020163)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_epoch_numeric_string(self):
        dt = parse_ts("1781020163")
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_bad_string_raises(self):
        with self.assertRaises(ValueError):
            parse_ts("not-a-time")

    def test_bool_rejected(self):
        with self.assertRaises(ValueError):
            parse_ts(True)


class TestParseLine(unittest.TestCase):
    def test_valid(self):
        ev = parse_line(_line())
        self.assertIsInstance(ev, OccEvent)
        self.assertEqual(ev.event, "occupancy_count")
        self.assertEqual(ev.count, 3)
        self.assertEqual(ev.space, "test")

    def test_blank(self):
        self.assertIsNone(parse_line(""))
        self.assertIsNone(parse_line("   \n"))

    def test_malformed_json(self):
        self.assertIsNone(parse_line("{not json"))
        self.assertIsNone(parse_line("42"))            # not an object
        self.assertIsNone(parse_line("[1,2,3]"))

    def test_unknown_event(self):
        self.assertIsNone(parse_line(_line(event="seated_standing_split")))

    def test_missing_node(self):
        self.assertIsNone(parse_line(_line(node="")))
        self.assertIsNone(parse_line(_line(node=None)))

    def test_missing_ts(self):
        obj = json.loads(_line())
        del obj["ts"]
        self.assertIsNone(parse_line(json.dumps(obj)))

    def test_bad_data_type(self):
        self.assertIsNone(parse_line(_line(data="oops")))

    def test_confidence_null(self):
        ev = parse_line(_line(confidence=None))
        self.assertIsNone(ev.confidence)


class TestProperties(unittest.TestCase):
    def test_space_falls_back_to_node(self):
        ev = parse_line(_line(data={"count": 1}))
        self.assertEqual(ev.space, "n6-occ-test")

    def test_negative_count_is_none(self):
        ev = parse_line(_line(data={"count": -2, "space": "x"}))
        self.assertIsNone(ev.count)

    def test_queue_and_dwell_fields(self):
        q = parse_line(_line(event="queue_length", data={"length": 5, "zone": "line", "space": "s"}))
        self.assertEqual(q.length, 5)
        self.assertEqual(q.zone, "line")
        d = parse_line(_line(event="dwell_sample", data={"dwell_s": 120, "zone": "z", "space": "s"}))
        self.assertEqual(d.dwell_s, 120)

    def test_zones_filters_bad_values(self):
        z = parse_line(_line(event="zone_occupancy", data={"zones": {"a": 3, "b": -1, "c": "x"}, "space": "s"}))
        self.assertEqual(z.zones, {"a": 3})

    def test_integral_float_count(self):
        ev = parse_line(_line(data={"count": 4.0, "space": "x"}))
        self.assertEqual(ev.count, 4)

    def test_fractional_float_count_is_none(self):
        ev = parse_line(_line(data={"count": 4.5, "space": "x"}))
        self.assertIsNone(ev.count)


class TestReadEvents(unittest.TestCase):
    def test_classification(self):
        lines = [
            _line(),                                   # kept
            "",                                        # blank
            "   ",                                     # blank
            "{garbage",                                # malformed
            "99",                                      # malformed (not object)
            _line(event="future_verb"),                # unknown event
            _line(node=""),                            # malformed (bad node)
            _line(ts="nope"),                          # malformed (bad ts)
            _line(event="heartbeat", data={"seq": 1, "uptime_ms": 10, "frames": 0, "space": "s"}),  # kept
        ]
        res = read_events(lines)
        self.assertEqual(res.total_lines, 9)
        self.assertEqual(res.kept, 2)
        self.assertEqual(res.blank_lines, 2)
        self.assertEqual(res.unknown, 1)
        self.assertEqual(res.malformed, 4)
        self.assertEqual(res.dropped, res.malformed + res.unknown)

    def test_order_preserved(self):
        res = read_events([_line(ts="2026-06-15T09:00:00Z"), _line(ts="2026-06-15T08:00:00Z")])
        self.assertEqual(res.events[0].ts.hour, 9)
        self.assertEqual(res.events[1].ts.hour, 8)


if __name__ == "__main__":
    unittest.main()
