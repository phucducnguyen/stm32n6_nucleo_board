"""Integration test: replay the sample log end-to-end through the pipeline."""

import os
import unittest

from occbridge.aggregator import EVENT_OCCUPANCY_COUNT, Aggregator
from occbridge.cli import BridgeStats, run_bridge
from occbridge.sources import FileReplaySource, IterableSource
from occbridge.spine_client import SpineClient

SAMPLE = os.path.join(os.path.dirname(__file__), "sample_capture.log")


class RecordingClient(SpineClient):
    """Spine client that records the envelopes it would emit."""

    def __init__(self):
        super().__init__(dry_run=True)
        self.envelopes = []

    def emit(self, envelope):
        self.envelopes.append(envelope)
        self.sent += 1
        return True


class TestReplayIntegration(unittest.TestCase):
    def _run(self, lines):
        agg = Aggregator(debounce_frames=3, heartbeat_s=10_000.0)
        client = RecordingClient()
        stats = run_bridge(lines, agg, client, node="n6-occ-test")
        return stats, client.envelopes

    def test_expected_event_sequence(self):
        with FileReplaySource(SAMPLE) as src:
            stats, envelopes = self._run(src)

        # All emitted events are occupancy_count (heartbeat disabled).
        counts = [
            e["data"]["count"]
            for e in envelopes
            if e["event"] == EVENT_OCCUPANCY_COUNT
        ]
        # baseline 0 -> 1 -> (2 blip absorbed) -> 2 -> 0
        self.assertEqual(counts, [0, 1, 2, 0])

    def test_stats_count_frames_and_drops(self):
        with FileReplaySource(SAMPLE) as src:
            stats, envelopes = self._run(src)
        self.assertIsInstance(stats, BridgeStats)
        self.assertEqual(stats.frames_parsed, 15)  # 15 DET lines
        self.assertEqual(stats.lines_dropped, 3)  # 2 boot + 1 warn noise line
        self.assertEqual(stats.events_emitted, 4)

    def test_iterable_source_equivalence(self):
        with open(SAMPLE, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        stats, envelopes = self._run(IterableSource(lines))
        counts = [e["data"]["count"] for e in envelopes]
        self.assertEqual(counts, [0, 1, 2, 0])

    def test_should_stop_halts_early(self):
        with open(SAMPLE, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        agg = Aggregator(debounce_frames=3, heartbeat_s=10_000.0)
        client = RecordingClient()
        seen = {"n": 0}

        def stop():
            seen["n"] += 1
            return seen["n"] > 5

        run_bridge(lines, agg, client, node="n6-occ-test", should_stop=stop)
        # Stopped well before the whole file was consumed.
        self.assertLessEqual(len(client.envelopes), 1)


if __name__ == "__main__":
    unittest.main()
