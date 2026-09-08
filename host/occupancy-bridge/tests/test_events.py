"""Tests for schema-v0 envelope construction, validation, and serialization."""

import json
import unittest
from datetime import datetime

from occbridge import events as ev


class TestEnvelope(unittest.TestCase):
    def test_occupancy_count_shape(self):
        env = ev.occupancy_count_event(3, node="n6-occ-lab")
        self.assertEqual(env["v"], 0)
        self.assertEqual(env["node"], "n6-occ-lab")
        self.assertEqual(env["event"], "occupancy_count")
        self.assertIsNone(env["confidence"])
        self.assertEqual(env["data"], {"count": 3})
        self.assertIn("ts", env)

    def test_heartbeat_shape(self):
        env = ev.heartbeat_event(42, 99999, 1000, node="n6-occ-lab")
        self.assertEqual(env["event"], "heartbeat")
        self.assertEqual(env["data"], {"seq": 42, "uptime_ms": 99999, "frames": 1000})

    def test_default_node(self):
        env = ev.occupancy_count_event(0)
        self.assertEqual(env["node"], ev.DEFAULT_NODE)
        self.assertEqual(env["node"], "n6-occ-unknown")

    def test_ts_is_iso8601(self):
        env = ev.occupancy_count_event(1)
        # Must round-trip through fromisoformat without raising.
        parsed = datetime.fromisoformat(env["ts"])
        self.assertIsNotNone(parsed.tzinfo)

    def test_to_json_under_1kb_and_compact(self):
        env = ev.heartbeat_event(123456, 4294967295, 999999, node="n6-occ-front-door")
        text = ev.to_json(env)
        self.assertLess(len(text.encode("utf-8")), 1024)
        self.assertNotIn(", ", text)  # compact separators
        self.assertEqual(json.loads(text)["data"]["seq"], 123456)

    def test_validate_rejects_missing_keys(self):
        with self.assertRaises(ValueError):
            ev.validate({"v": 0})

    def test_validate_rejects_bad_version(self):
        env = ev.occupancy_count_event(1)
        env["v"] = 1
        with self.assertRaises(ValueError):
            ev.validate(env)

    def test_validate_rejects_unknown_event(self):
        env = ev.occupancy_count_event(1)
        env["event"] = "mystery"
        with self.assertRaises(ValueError):
            ev.validate(env)

    def test_validate_rejects_empty_node(self):
        env = ev.occupancy_count_event(1)
        env["node"] = ""
        with self.assertRaises(ValueError):
            ev.validate(env)

    def test_to_json_rejects_oversize(self):
        env = ev.occupancy_count_event(1)
        env["data"]["junk"] = "x" * 2000
        with self.assertRaises(ValueError):
            ev.to_json(env)

    def test_explicit_ts_preserved(self):
        env = ev.occupancy_count_event(1, ts="2026-06-25T00:00:00+00:00")
        self.assertEqual(env["ts"], "2026-06-25T00:00:00+00:00")

    def test_confidence_passthrough(self):
        env = ev.occupancy_count_event(1, confidence=0.91)
        self.assertEqual(env["confidence"], 0.91)
        ev.validate(env)


if __name__ == "__main__":
    unittest.main()
