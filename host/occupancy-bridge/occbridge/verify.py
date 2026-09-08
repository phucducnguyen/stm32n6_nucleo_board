"""Bring-up GO/NO-GO for the STM32N6 occupancy sensor.

``python -m occbridge verify`` captures the ``DET`` stream for a few seconds
(or replays a captured log) and prints a single honest report + verdict that
answers the one question that gates the next step: *does this board emit data
good enough to build on, and if so, count-only (v0) or dwell/queue (v0.2)?*

The metric/verdict core (:func:`analyze`) is a **pure function over a list of
parsed** :class:`~occbridge.protocol.Frame` **objects** (plus the two line-level
tallies the caller measured), so the entire decision is unit-testable without
hardware. The capture side (:func:`capture`) is the only impure part.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Iterable
from dataclasses import dataclass, field

from .protocol import Frame, parse_line

__all__ = [
    "VerifyReport",
    "analyze",
    "capture",
    "format_report",
    "DEFAULT_MIN_FPS",
    "DEFAULT_MIN_TRACK_LIFETIME",
    "REC_COUNT_ONLY",
    "REC_DWELL",
    "REC_FAIL_PREFIX",
]

#: A frame rate at/above this (frames per second) is considered healthy.
DEFAULT_MIN_FPS = 5.0
#: Mean track lifetime (in frames) at/above this means ids are not churning.
DEFAULT_MIN_TRACK_LIFETIME = 3.0
#: An inter-frame gap longer than this (ms) is treated as a stall/wrap and is
#: excluded from the frame-rate statistics (it is not a real frame interval).
_MAX_SANE_GAP_MS = 10_000

#: ``id == 0`` is the protocol's "tracking disabled" sentinel, not a real track.
_NO_TRACK_ID = 0

REC_COUNT_ONLY = "v0 count-only"
REC_DWELL = "v0.2 dwell/queue feasible"
REC_FAIL_PREFIX = "FAIL"


@dataclass(frozen=True)
class VerifyReport:
    """Everything the verify run measured, plus the verdict it implies.

    All fields are plain numbers/bools so the report is trivially serializable
    and assertable in tests.
    """

    # -- line / parse tallies ------------------------------------------------
    lines_total: int
    valid_frames: int
    parse_drops: int

    # -- frame timing (from ts_ms deltas) ------------------------------------
    interframe_ms_min: float | None
    interframe_ms_median: float | None
    interframe_ms_max: float | None
    fps: float | None

    # -- count distribution --------------------------------------------------
    count_min: int | None
    count_max: int | None
    count_mean: float | None
    frames_with_person: int

    # -- track ids -----------------------------------------------------------
    unique_ids: int          # distinct ids incl. the 0 "disabled" sentinel
    unique_track_ids: int    # distinct non-zero ids
    mean_track_lifetime: float | None  # mean frames a non-zero id persists
    max_concurrent_ids: int  # max distinct non-zero ids in a single frame

    # -- verdict booleans + recommendation -----------------------------------
    detects_people: bool
    boxes_present: bool
    track_ids_present: bool
    track_ids_stable: bool
    frame_rate_healthy: bool
    recommendation: str

    # thresholds used (echoed for an honest report)
    min_fps: float = DEFAULT_MIN_FPS
    min_track_lifetime: float = DEFAULT_MIN_TRACK_LIFETIME

    @property
    def is_go(self) -> bool:
        """True when the recommendation is not a FAIL (a usable board)."""
        return not self.recommendation.startswith(REC_FAIL_PREFIX)

    @property
    def parse_drop_rate(self) -> float:
        """Fraction of total lines that failed to parse (0.0 if no lines)."""
        return self.parse_drops / self.lines_total if self.lines_total else 0.0


def _interframe_deltas(frames: list[Frame]) -> list[int]:
    """Positive, sane ts_ms deltas between consecutive frames.

    Non-positive deltas (counter wraps / out-of-order) and implausibly large
    gaps (stalls) are excluded — they are not real inter-frame intervals.
    """
    deltas: list[int] = []
    for prev, cur in zip(frames, frames[1:]):
        d = cur.ts_ms - prev.ts_ms
        if 0 < d <= _MAX_SANE_GAP_MS:
            deltas.append(d)
    return deltas


def _track_metrics(frames: list[Frame]) -> tuple[int, int, float | None, int]:
    """(unique_ids, unique_track_ids, mean_lifetime, max_concurrent_track_ids).

    Lifetime = number of frames a given non-zero id appears in; the mean is over
    non-zero ids only (id 0 means tracking disabled, so it is not a real track).
    """
    all_ids: set[int] = set()
    appearances: dict[int, int] = {}
    max_concurrent = 0
    for frame in frames:
        present_tracks: set[int] = set()
        for box in frame.boxes:
            all_ids.add(box.id)
            if box.id != _NO_TRACK_ID:
                appearances[box.id] = appearances.get(box.id, 0) + 1
                present_tracks.add(box.id)
        max_concurrent = max(max_concurrent, len(present_tracks))

    mean_lifetime = (
        statistics.fmean(appearances.values()) if appearances else None
    )
    return len(all_ids), len(appearances), mean_lifetime, max_concurrent


def analyze(
    frames: list[Frame],
    *,
    lines_total: int,
    parse_drops: int,
    min_fps: float = DEFAULT_MIN_FPS,
    min_track_lifetime: float = DEFAULT_MIN_TRACK_LIFETIME,
) -> VerifyReport:
    """Pure verdict over captured frames + the caller's line-level tallies.

    Args:
        frames: every successfully parsed frame, in capture order.
        lines_total: total lines read (DET + noise + garbage).
        parse_drops: lines that did not parse to a frame.
        min_fps / min_track_lifetime: verdict thresholds.
    """
    valid = len(frames)

    deltas = _interframe_deltas(frames)
    if deltas:
        ifm_min = float(min(deltas))
        ifm_med = float(statistics.median(deltas))
        ifm_max = float(max(deltas))
        fps = 1000.0 / ifm_med if ifm_med > 0 else None
    else:
        ifm_min = ifm_med = ifm_max = fps = None

    counts = [f.count for f in frames]
    if counts:
        c_min: int | None = min(counts)
        c_max: int | None = max(counts)
        c_mean: float | None = statistics.fmean(counts)
    else:
        c_min = c_max = c_mean = None
    frames_with_person = sum(1 for c in counts if c >= 1)
    total_boxes = sum(len(f.boxes) for f in frames)

    unique_ids, unique_track_ids, mean_lifetime, max_concurrent = _track_metrics(
        frames
    )

    # -- verdict booleans ----------------------------------------------------
    detects_people = frames_with_person >= 1
    boxes_present = total_boxes >= 1
    track_ids_present = unique_track_ids >= 1
    frame_rate_healthy = fps is not None and fps >= min_fps
    track_ids_stable = (
        track_ids_present
        and mean_lifetime is not None
        and mean_lifetime >= min_track_lifetime
    )

    # -- recommendation ladder (deterministic, honest) -----------------------
    if valid < 2 or fps is None:
        recommendation = (
            f"{REC_FAIL_PREFIX} — insufficient data "
            f"(captured {valid} valid DET frame(s); need a steady stream)"
        )
    elif not detects_people:
        recommendation = (
            f"{REC_FAIL_PREFIX} — no person ever detected "
            "(count never >= 1); check camera framing / model"
        )
    elif not frame_rate_healthy:
        recommendation = (
            f"{REC_FAIL_PREFIX} — frame rate too low "
            f"({fps:.1f} fps < {min_fps:.0f} fps)"
        )
    elif track_ids_stable:
        recommendation = REC_DWELL
    else:
        recommendation = REC_COUNT_ONLY

    return VerifyReport(
        lines_total=lines_total,
        valid_frames=valid,
        parse_drops=parse_drops,
        interframe_ms_min=ifm_min,
        interframe_ms_median=ifm_med,
        interframe_ms_max=ifm_max,
        fps=fps,
        count_min=c_min,
        count_max=c_max,
        count_mean=c_mean,
        frames_with_person=frames_with_person,
        unique_ids=unique_ids,
        unique_track_ids=unique_track_ids,
        mean_track_lifetime=mean_lifetime,
        max_concurrent_ids=max_concurrent,
        detects_people=detects_people,
        boxes_present=boxes_present,
        track_ids_present=track_ids_present,
        track_ids_stable=track_ids_stable,
        frame_rate_healthy=frame_rate_healthy,
        recommendation=recommendation,
        min_fps=min_fps,
        min_track_lifetime=min_track_lifetime,
    )


def capture(
    lines: Iterable[str],
    *,
    max_seconds: float | None = None,
    clock=time.monotonic,
    should_stop=lambda: False,
) -> tuple[list[Frame], int, int]:
    """Drain ``lines`` into (frames, lines_total, parse_drops). Impure.

    Stops at EOF, when ``should_stop()`` is true, or once ``max_seconds`` of
    wall time has elapsed (``None`` = no time bound; used for replay-to-EOF).
    The time check happens between lines, so a blocking serial read may overrun
    the budget by at most one read timeout — acceptable for bring-up.
    """
    frames: list[Frame] = []
    lines_total = 0
    parse_drops = 0
    deadline = None if max_seconds is None else clock() + max_seconds

    for line in lines:
        if should_stop():
            break
        lines_total += 1
        frame = parse_line(line)
        if frame is None:
            parse_drops += 1
        else:
            frames.append(frame)
        if deadline is not None and clock() >= deadline:
            break

    return frames, lines_total, parse_drops


def _yn(flag: bool) -> str:
    return "YES" if flag else "NO"


def _ms(value: float | None) -> str:
    return f"{value:.0f} ms" if value is not None else "n/a"


def format_report(report: VerifyReport) -> str:
    """Render a :class:`VerifyReport` as a concise human-readable block."""
    r = report
    lines = [
        "occbridge verify — STM32N6 occupancy bring-up",
        "=" * 46,
        f"lines total      : {r.lines_total}",
        f"valid DET frames : {r.valid_frames}",
        f"parse drops      : {r.parse_drops} ({r.parse_drop_rate * 100:.1f}%)",
        "",
        "frame timing (inter-frame, from ts_ms):",
        f"  min/median/max : {_ms(r.interframe_ms_min)} / "
        f"{_ms(r.interframe_ms_median)} / {_ms(r.interframe_ms_max)}",
        f"  effective fps  : {f'{r.fps:.1f}' if r.fps is not None else 'n/a'}",
        "",
        "count distribution:",
        f"  min/max/mean   : "
        f"{r.count_min if r.count_min is not None else 'n/a'} / "
        f"{r.count_max if r.count_max is not None else 'n/a'} / "
        f"{f'{r.count_mean:.2f}' if r.count_mean is not None else 'n/a'}",
        f"  frames w/person: {r.frames_with_person}",
        "",
        "track ids:",
        f"  unique ids     : {r.unique_ids} "
        f"({r.unique_track_ids} non-zero / real tracks)",
        f"  mean lifetime  : "
        f"{f'{r.mean_track_lifetime:.2f} frames' if r.mean_track_lifetime is not None else 'n/a'}",
        f"  max concurrent : {r.max_concurrent_ids}",
        "",
        "VERDICT",
        "-" * 46,
        f"  DETECTS PEOPLE?   {_yn(r.detects_people)}",
        f"  BOXES PRESENT?    {_yn(r.boxes_present)}",
        f"  TRACK IDS PRESENT?{_yn(r.track_ids_present)}",
        f"  TRACK IDS STABLE? {_yn(r.track_ids_stable)} "
        f"(mean lifetime >= {r.min_track_lifetime:.0f} frames)",
        f"  FRAME RATE OK?    {_yn(r.frame_rate_healthy)} "
        f"(>= {r.min_fps:.0f} fps)",
        "",
        f"  => {r.recommendation}",
    ]
    return "\n".join(lines)
