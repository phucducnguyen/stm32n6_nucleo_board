#!/usr/bin/env python3
"""Score an on-device DET log against a hand tally (people-count accuracy).

Dataset protocol (privacy-safe, no images anywhere): a person sits in the space
with a phone timer and, at every minute mark, writes down how many people are in
the sensor's view. That tally is the ground truth. The device's own DET lines are
the prediction. No frame is stored or looked at.

Ground truth CSV (header required):
    minute,count
    2026-10-27T08:15,3          # local wall-clock minute mark, people in view
    2026-10-27T08:16,4

DET log, either form:
    host-stamped:  2026-10-27T08:15:00.412 DET 1422 38911 2 ...   (ISO) or
                   1793117700.412 DET 1422 38911 2 ...            (epoch seconds)
    raw board log: DET 1422 38911 2 ...  plus  --anchor 38911=2026-10-27T08:15:00
                   (one board ts_ms paired with the wall clock at that moment;
                    ts_ms wraps at 2^32 and is unwrapped here)

The firmware emits on a count change plus a 1 s keepalive, so the DET stream is
a step function: each line holds until the next. Per labelled minute mark M the
device value is the median of that held count sampled once a second over
[M - window, M + window] (default 10 s), rounded half up. A sample more than
--stale-s (default 3 s) after the last line is silent, not zero; a mark with
half or more of its samples silent is MISSING (counts against coverage, never
silently skipped).

Metrics: coverage, MAE (people per minute), exact-count rate, within-one rate,
mean signed bias, WAPE (sum |error| / sum truth), occupied-state accuracy
(count >= 1 agreement; this drives the report's utilisation number), and the
peak check: the hand tally at the device's busiest minute is within one person
of the tally's own peak (the report's "busiest time" is real).

Target (docs/OCCUPANCY-PATH.md, "Accuracy target"): proposed until the first
board session measures it.

Exit: 0 target met, 1 target missed, 2 bad input, 3 dataset too small to judge.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timedelta

from _det import parse_line

TARGET = {
    "coverage_min": 0.95,
    "mae_max": 0.5,
    "exact_min": 0.70,
    "within1_min": 0.95,
    "wape_max": 0.15,
    "occupied_acc_min": 0.95,
    "peak_shortfall_max": 1,
}
MIN_DATASET = {"minutes": 60, "minutes_truth_ge2": 20}

_WRAP = 1 << 32


def _parse_time(tok: str) -> float | None:
    try:
        return float(tok)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(tok).timestamp()
    except ValueError:
        return None


def load_frames(lines, anchor: tuple[int, float] | None) -> list[tuple[float, int]]:
    """Return (wall_seconds, count) for every valid DET line."""
    out: list[tuple[float, int]] = []
    prev_ts = None
    unwrapped = 0
    for raw in lines:
        line = raw.rstrip("\r\n")
        parts = line.split(None, 1)
        if not parts:
            continue
        wall = None
        if parts[0] != "DET" and len(parts) == 2 and parts[1].startswith("DET "):
            wall = _parse_time(parts[0])
            if wall is None:
                continue
            line = parts[1]
        frame = parse_line(line)
        if frame is None:
            continue
        if wall is None:
            if anchor is None:
                raise ValueError("raw DET line without a host stamp; pass --anchor TS_MS=ISO")
            if prev_ts is not None and frame.ts_ms < prev_ts:
                unwrapped += _WRAP
            prev_ts = frame.ts_ms
            a_ts, a_wall = anchor
            wall = a_wall + (frame.ts_ms + unwrapped - a_ts) / 1000.0
        out.append((wall, frame.count))
    out.sort()
    return out


def load_truth(path: str) -> list[tuple[float, int]]:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t = _parse_time(r["minute"].strip())
            if t is None:
                raise ValueError(f"bad minute {r['minute']!r}")
            c = int(r["count"])
            if c < 0:
                raise ValueError(f"negative count at {r['minute']}")
            rows.append((t, c))
    rows.sort()
    return rows


def _median_round(vals: list[int]) -> int:
    s = sorted(vals)
    n = len(s)
    m = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    return int(math.floor(m + 0.5))


def held_samples(frames, start: float, end: float, stale_s: float) -> list[int | None]:
    """Sample the device count once a second over [start, end] as a step function.

    The firmware emits on a count CHANGE plus a keepalive (DET_EMIT_KEEPALIVE_MS,
    1 s), not every frame, so a DET line holds until the next one. A sample whose
    last line is older than stale_s is None (the device was silent: not zero).
    """
    import bisect
    times = [t for t, _ in frames]
    out: list[int | None] = []
    k = 0
    while start + k <= end + 1e-9:
        s = start + k
        i = bisect.bisect_right(times, s) - 1
        out.append(frames[i][1] if i >= 0 and s - times[i] <= stale_s else None)
        k += 1
    return out


def score(frames, truth, window_s: float = 10.0, stale_s: float = 3.0) -> dict:
    pairs = []  # (mark, truth, device or None)
    for mark, tc in truth:
        samples = held_samples(frames, mark - window_s, mark + window_s, stale_s)
        valid = [v for v in samples if v is not None]
        dev = _median_round(valid) if len(valid) * 2 > len(samples) else None
        pairs.append((mark, tc, dev))
    scored = [(m, t, d) for m, t, d in pairs if d is not None]
    n, ns = len(pairs), len(scored)
    res = {"minutes_labelled": n, "minutes_scored": ns,
           "minutes_truth_ge2": sum(1 for _, t, _ in pairs if t >= 2),
           "coverage": ns / n if n else 0.0}
    if ns:
        errs = [d - t for _, t, d in scored]
        tsum = sum(t for _, t, _ in scored)
        res.update({
            "mae": sum(abs(e) for e in errs) / ns,
            "exact": sum(1 for e in errs if e == 0) / ns,
            "within1": sum(1 for e in errs if abs(e) <= 1) / ns,
            "bias": sum(errs) / ns,
            "wape": (sum(abs(e) for e in errs) / tsum) if tsum else (0.0 if not any(errs) else math.inf),
            "occupied_acc": sum(1 for _, t, d in scored if (t >= 1) == (d >= 1)) / ns,
        })
        tpk = max(scored, key=lambda p: (p[1], -p[0]))
        dpk = max(scored, key=lambda p: (p[2], -p[0]))
        res["peak_truth"] = {"minute": datetime.fromtimestamp(tpk[0]).isoformat(timespec="minutes"), "count": tpk[1]}
        res["peak_device"] = {"minute": datetime.fromtimestamp(dpk[0]).isoformat(timespec="minutes"), "count": dpk[2]}
        res["peak_offset_min"] = abs(tpk[0] - dpk[0]) / 60.0
        # Peaks repeat (every rush hits the same max), so gate on whether the
        # device's busiest minute was truly busy, not on which tie it picked.
        res["peak_shortfall"] = tpk[1] - dpk[1]
    return res


def verdict(res: dict) -> tuple[str, list[str]]:
    if res["minutes_labelled"] < MIN_DATASET["minutes"] or \
            res["minutes_truth_ge2"] < MIN_DATASET["minutes_truth_ge2"]:
        return "INSUFFICIENT", [f"need >= {MIN_DATASET['minutes']} labelled minutes and >= "
                                f"{MIN_DATASET['minutes_truth_ge2']} with truth >= 2"]
    misses = []
    if res["coverage"] < TARGET["coverage_min"]:
        misses.append(f"coverage {res['coverage']:.3f} < {TARGET['coverage_min']}")
    if res["minutes_scored"] == 0:
        return "MISS", misses + ["no scored minutes"]
    checks = [("mae", "mae_max", "<="), ("exact", "exact_min", ">="),
              ("within1", "within1_min", ">="), ("wape", "wape_max", "<="),
              ("occupied_acc", "occupied_acc_min", ">="),
              ("peak_shortfall", "peak_shortfall_max", "<=")]
    for key, tkey, op in checks:
        v, lim = res[key], TARGET[tkey]
        ok = v <= lim if op == "<=" else v >= lim
        if not ok:
            misses.append(f"{key} {v:.3f} not {op} {lim}")
    return ("MEET" if not misses else "MISS"), misses


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="score a DET log against a hand tally")
    ap.add_argument("det_log")
    ap.add_argument("truth_csv")
    ap.add_argument("--anchor", help="TS_MS=ISO wall time for a raw board log")
    ap.add_argument("--window-s", type=float, default=10.0)
    ap.add_argument("--stale-s", type=float, default=3.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        anchor = None
        if a.anchor:
            ts, iso = a.anchor.split("=", 1)
            w = _parse_time(iso)
            if w is None:
                raise ValueError(f"bad anchor time {iso!r}")
            anchor = (int(ts), w)
        with open(a.det_log, encoding="ascii", errors="replace") as f:
            frames = load_frames(f, anchor)
        truth = load_truth(a.truth_csv)
    except (OSError, ValueError, KeyError) as exc:
        print(f"BAD-INPUT: {exc}", file=sys.stderr)
        return 2
    if not truth:
        print("BAD-INPUT: empty ground truth", file=sys.stderr)
        return 2
    res = score(frames, truth, a.window_s, a.stale_s)
    v, misses = verdict(res)
    res["verdict"], res["misses"] = v, misses
    if a.json:
        print(json.dumps(res, indent=2, default=str))
    else:
        for k in ("minutes_labelled", "minutes_scored", "coverage", "mae", "exact",
                  "within1", "bias", "wape", "occupied_acc", "peak_offset_min",
                  "peak_shortfall"):
            if k in res:
                v_ = res[k]
                print(f"  {k:18s} {v_:.3f}" if isinstance(v_, float) else f"  {k:18s} {v_}")
        for m in misses:
            print("  MISS " + m)
        print(f"VERDICT: {v}")
    return {"MEET": 0, "MISS": 1, "INSUFFICIENT": 3}[v]


if __name__ == "__main__":
    sys.exit(main())
