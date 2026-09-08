"""Generate a realistic one-week JSONL of schema-v0 events for a café.

So the report tool is demoable without hardware. Deterministic given a seed, so
tests can assert on its output. Emits the same envelope shape the bridge does::

    {"v":0,"node":...,"event":...,"confidence":...,"data":{...},"ts":<iso8601 UTC>}

Usage::

    python -m tools.synth > week.jsonl
    python -m tools.synth --seed 7 --weeks 1 > week.jsonl

The simulated café:

* Open 07:00–18:00, busier on Saturday, nearly dead on Sunday.
* Three daily peaks: morning coffee (~8–9), lunch (~12–13), an afternoon bump.
* A register-line queue that builds at the lunch peak.
* Anonymous dwell samples in two zones (register-line, seating).
* A heartbeat every 5 minutes around the clock — with one deliberate off-hours
  gap so the report's data-health section has something honest to show.

All output is anonymous: counts and durations only, no identity, no PII.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone

NODE = "n6-occ-front-counter"
SPACE = "front-counter"
QUEUE_ZONE = "register-line"
DWELL_ZONES = ("register-line", "seating")

OPEN_HOUR = 7
CLOSE_HOUR = 18

# Expected average people-in-view by local hour of day (the café's shape).
_HOUR_PROFILE = {
    7: 1.0,
    8: 3.5,
    9: 4.0,
    10: 2.5,
    11: 2.5,
    12: 6.0,
    13: 6.5,
    14: 4.0,
    15: 2.0,
    16: 2.5,
    17: 3.0,
}

# Per-weekday multiplier (Mon=0 .. Sun=6): Saturday busy, Sunday sleepy.
_DAY_FACTOR = {0: 1.0, 1: 1.0, 2: 1.0, 3: 1.0, 4: 1.1, 5: 1.3, 6: 0.15}

SAMPLE_INTERVAL_S = 60       # occupancy/queue sample cadence
HEARTBEAT_INTERVAL_S = 300   # liveness cadence
AVG_DWELL_MIN = 8.0          # mean minutes a customer lingers (drives arrival rate)


def _ev(event, data, ts, confidence=None):
    return {
        "v": 0,
        "node": NODE,
        "event": event,
        "confidence": confidence,
        "data": dict(data, space=SPACE, sv=1),
        "ts": ts.isoformat(),
    }


def _poisson(rng: random.Random, lam: float) -> int:
    """Knuth's Poisson sampler (deterministic for a seeded ``rng``)."""
    if lam <= 0:
        return 0
    import math

    target = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= target:
            return k - 1


def _binomial(rng: random.Random, n: int, p: float) -> int:
    """Count of ``n`` Bernoulli(``p``) trials (small ``n`` here)."""
    return sum(1 for _ in range(n) if rng.random() < p)


def generate(seed: int = 1, weeks: int = 1, start_monday: str = "2026-06-15"):
    """Yield event dicts for ``weeks`` weeks starting on the given Monday (UTC).

    Timestamps are UTC; the café's hour profile is expressed in UTC so the demo
    works with ``--open "07:00-18:00"`` and the default UTC timezone.
    """
    rng = random.Random(seed)
    base = datetime.fromisoformat(start_monday).replace(tzinfo=timezone.utc)

    total_days = 7 * weeks
    # One deliberate off-hours heartbeat gap (Thu 02:10–02:45 of week 1).
    gap_start = base + timedelta(days=3, hours=2, minutes=10)
    gap_end = base + timedelta(days=3, hours=2, minutes=45)

    for day in range(total_days):
        day_start = base + timedelta(days=day)
        weekday = day_start.weekday()
        factor = _DAY_FACTOR.get(weekday, 1.0)

        # Heartbeats around the clock (liveness), minus the deliberate gap.
        t = day_start
        end_of_day = day_start + timedelta(days=1)
        while t < end_of_day:
            if not (gap_start <= t < gap_end):
                yield _ev("heartbeat", {"seq": int((t - base).total_seconds()) // HEARTBEAT_INTERVAL_S, "uptime_ms": int((t - base).total_seconds() * 1000), "frames": 0}, t, confidence=None)
            t += timedelta(seconds=HEARTBEAT_INTERVAL_S)

        # Occupancy during open hours, modelled as a smooth arrival/departure
        # process (M/M/inf-like): people arrive at a rate set by the hour, each
        # lingers ~AVG_DWELL_MIN, so the count is autocorrelated and realistic
        # (not independent per-minute noise). Each departure reports an
        # anonymous dwell sample.
        current = 0
        leave_p = 1.0 / AVG_DWELL_MIN
        t = day_start + timedelta(hours=OPEN_HOUR)
        open_end = day_start + timedelta(hours=CLOSE_HOUR)
        while t < open_end:
            hour = t.hour
            target = _HOUR_PROFILE.get(hour, 0.0) * factor
            arrivals = _poisson(rng, target * leave_p)  # rate that yields ~target occupancy
            departures = _binomial(rng, current, leave_p)
            departures = min(departures, current)
            current = max(0, current + arrivals - departures)

            yield _ev(
                "occupancy_count",
                {"count": current},
                t,
                confidence=round(rng.uniform(0.78, 0.95), 2),
            )

            # Queue builds when it's busy (mainly the peaks).
            if current >= 3:
                qlen = max(0, current - 2 + rng.randint(-1, 1))
                if qlen > 0:
                    yield _ev(
                        "queue_length",
                        {"length": qlen, "zone": QUEUE_ZONE},
                        t,
                        confidence=round(rng.uniform(0.72, 0.9), 2),
                    )

            # Each departing presence reports a completed, anonymous dwell.
            for _ in range(departures):
                zone = DWELL_ZONES[0] if rng.random() < 0.6 else DWELL_ZONES[1]
                mean = 150 if zone == DWELL_ZONES[0] else 360
                dwell = max(15, int(rng.gauss(mean, mean * 0.4)))
                yield _ev(
                    "dwell_sample",
                    {"dwell_s": dwell, "zone": zone},
                    t,
                    confidence=round(rng.uniform(0.65, 0.85), 2),
                )

            t += timedelta(seconds=SAMPLE_INTERVAL_S)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tools.synth", description="Generate demo café occupancy events (JSONL).")
    p.add_argument("--seed", type=int, default=1, help="RNG seed (deterministic output). Default 1.")
    p.add_argument("--weeks", type=int, default=1, help="Number of weeks to generate. Default 1.")
    p.add_argument("--start", default="2026-06-15", help="Start Monday (YYYY-MM-DD, UTC). Default 2026-06-15.")
    args = p.parse_args(argv)

    out = sys.stdout
    for ev in generate(seed=args.seed, weeks=args.weeks, start_monday=args.start):
        out.write(json.dumps(ev, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
