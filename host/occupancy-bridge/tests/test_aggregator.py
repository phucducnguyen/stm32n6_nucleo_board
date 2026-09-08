"""Tests for the pure occupancy aggregator (deterministic injected clock)."""

import unittest

from occbridge.aggregator import (
    EVENT_HEARTBEAT,
    EVENT_OCCUPANCY_COUNT,
    Aggregator,
)
from occbridge.protocol import Box, Frame


class FakeClock:
    """A manually advanced monotonic clock in milliseconds."""

    def __init__(self, start: int = 0) -> None:
        self.t = start

    def __call__(self) -> int:
        return self.t

    def advance(self, ms: int) -> None:
        self.t += ms


def frame(seq, count, *, ts=None, boxes=()):
    return Frame(seq=seq, ts_ms=ts if ts is not None else seq, count=count, boxes=boxes)


class TestDebounce(unittest.TestCase):
    def test_stable_change_emits_once(self):
        clk = FakeClock()
        agg = Aggregator(debounce_frames=3, heartbeat_s=10_000, now_ms=clk)
        events = []
        for seq in range(3):
            clk.advance(33)
            events += agg.process(frame(seq, 1))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, EVENT_OCCUPANCY_COUNT)
        self.assertEqual(events[0].count, 1)
        self.assertEqual(agg.confirmed_count, 1)

    def test_no_event_before_threshold(self):
        clk = FakeClock()
        agg = Aggregator(debounce_frames=3, heartbeat_s=10_000, now_ms=clk)
        out = agg.process(frame(0, 1)) + agg.process(frame(1, 1))
        self.assertEqual(out, [])
        self.assertIsNone(agg.confirmed_count)

    def test_transient_blip_is_absorbed(self):
        clk = FakeClock()
        agg = Aggregator(debounce_frames=3, heartbeat_s=10_000, now_ms=clk)
        # Confirm baseline 1.
        for seq in range(3):
            agg.process(frame(seq, 1))
        self.assertEqual(agg.confirmed_count, 1)
        # A single-frame jump to 2, then back to 1 -> no change emitted.
        e1 = agg.process(frame(3, 2))
        e2 = agg.process(frame(4, 1))
        self.assertEqual(e1, [])
        self.assertEqual(e2, [])
        self.assertEqual(agg.confirmed_count, 1)

    def test_window_trigger_confirms_before_frame_count(self):
        clk = FakeClock()
        # 5 frames needed by count, but a 100ms window also confirms.
        agg = Aggregator(
            debounce_frames=5,
            debounce_window_ms=100,
            heartbeat_s=10_000,
            now_ms=clk,
        )
        out = agg.process(frame(0, 2))  # held=1, t=0
        self.assertEqual(out, [])
        clk.advance(120)
        out = agg.process(frame(1, 2))  # held=2 but window elapsed -> confirm
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].count, 2)

    def test_first_baseline_zero_is_emitted(self):
        clk = FakeClock()
        agg = Aggregator(debounce_frames=2, heartbeat_s=10_000, now_ms=clk)
        out = agg.process(frame(0, 0)) + agg.process(frame(1, 0))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].count, 0)

    def test_changing_candidate_resets_hold(self):
        clk = FakeClock()
        agg = Aggregator(debounce_frames=3, heartbeat_s=10_000, now_ms=clk)
        agg.process(frame(0, 1))  # cand 1 held1
        agg.process(frame(1, 2))  # cand switches to 2 held1
        out = agg.process(frame(2, 2))  # held2 -> not yet
        self.assertEqual(out, [])
        out = agg.process(frame(3, 2))  # held3 -> confirm 2
        self.assertEqual(out[0].count, 2)

    def test_debounce_frames_must_be_positive(self):
        with self.assertRaises(ValueError):
            Aggregator(debounce_frames=0)

    def test_flapping_count_never_confirms_spurious_change(self):
        clk = FakeClock()
        agg = Aggregator(debounce_frames=3, heartbeat_s=10_000, now_ms=clk)
        # Confirm a baseline of 0 first.
        for seq in range(3):
            agg.process(frame(seq, 0))
        self.assertEqual(agg.confirmed_count, 0)
        # Now flap 2,0,2,0,... — never holds 3 frames -> no spurious event.
        events = []
        for i, raw in enumerate([2, 0, 2, 0, 2, 0, 2, 0]):
            events += agg.process(frame(10 + i, raw))
        self.assertEqual(events, [])
        self.assertEqual(agg.confirmed_count, 0)


class TestDwellBounding(unittest.TestCase):
    def test_dwell_dict_is_bounded_and_evicts_stalest(self):
        clk = FakeClock()
        agg = Aggregator(
            debounce_frames=1, heartbeat_s=10_000, now_ms=clk, max_dwell_records=2
        )
        # id 1 seen first (will be the stalest), then 2, then 3 evicts 1.
        agg.process(frame(0, 1, boxes=(Box(1, 1, 1, 1, 1, 80),)))
        clk.advance(100)
        agg.process(frame(1, 1, boxes=(Box(2, 1, 1, 1, 1, 80),)))
        clk.advance(100)
        agg.process(frame(2, 1, boxes=(Box(3, 1, 1, 1, 1, 80),)))
        self.assertEqual(len(agg.dwell_records), 2)
        self.assertNotIn(1, agg.dwell_records)  # stalest evicted
        self.assertIn(2, agg.dwell_records)
        self.assertIn(3, agg.dwell_records)

    def test_max_dwell_records_must_be_positive(self):
        with self.assertRaises(ValueError):
            Aggregator(max_dwell_records=0)


class TestHeartbeat(unittest.TestCase):
    def test_heartbeat_cadence(self):
        clk = FakeClock()
        agg = Aggregator(debounce_frames=1, heartbeat_s=1.0, now_ms=clk)
        # First frame anchors the heartbeat clock (no heartbeat yet).
        agg.process(frame(0, 0))
        heartbeats = 0
        for seq in range(1, 6):
            clk.advance(600)  # 600ms steps
            for e in agg.process(frame(seq, 0)):
                if e.kind == EVENT_HEARTBEAT:
                    heartbeats += 1
        # Heartbeat re-anchors to its own emit time (drift-on-emit, no catch-up
        # bursts). Fires at 1200ms (anchor->1200) and 2400ms (anchor->2400).
        self.assertEqual(heartbeats, 2)

    def test_heartbeat_carries_frame_metadata(self):
        clk = FakeClock()
        agg = Aggregator(debounce_frames=1, heartbeat_s=1.0, now_ms=clk)
        agg.process(frame(10, 0, ts=5000))
        clk.advance(1500)
        out = agg.process(frame(11, 0, ts=6500))
        hb = [e for e in out if e.kind == EVENT_HEARTBEAT]
        self.assertEqual(len(hb), 1)
        self.assertEqual(hb[0].seq, 11)
        self.assertEqual(hb[0].uptime_ms, 6500)
        self.assertEqual(hb[0].frames, 2)


class TestDwell(unittest.TestCase):
    def test_first_and_last_seen_tracked(self):
        clk = FakeClock()
        agg = Aggregator(debounce_frames=1, heartbeat_s=10_000, now_ms=clk)
        agg.process(frame(0, 1, boxes=(Box(7, 1, 1, 1, 1, 80),)))
        clk.advance(100)
        agg.process(frame(1, 1, boxes=(Box(7, 1, 1, 1, 1, 80),)))
        clk.advance(100)
        agg.process(frame(2, 2, boxes=(Box(7, 1, 1, 1, 1, 80), Box(9, 2, 2, 2, 2, 70))))
        rec7 = agg.dwell_records[7]
        rec9 = agg.dwell_records[9]
        self.assertEqual(rec7.first_seen_ms, 0)
        self.assertEqual(rec7.last_seen_ms, 200)
        self.assertEqual(rec7.frames_seen, 3)
        self.assertEqual(rec9.first_seen_ms, 200)
        self.assertEqual(rec9.frames_seen, 1)


if __name__ == "__main__":
    unittest.main()
