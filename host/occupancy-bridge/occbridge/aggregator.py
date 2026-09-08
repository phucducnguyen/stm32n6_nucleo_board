"""Pure occupancy logic: frames in, debounced occupancy events out. No I/O.

The aggregator smooths the noisy per-frame person ``count`` into a stable
"confirmed" occupancy: a new raw count must persist for ``debounce_frames``
consecutive frames (or, optionally, for ``debounce_window_ms`` of wall time)
before it is accepted. It emits:

* an :class:`OccupancyEvent` of kind ``occupancy_count`` whenever the confirmed
  count CHANGES (including the first baseline confirmation), and
* a ``heartbeat`` event every ``heartbeat_s`` seconds regardless of change.

Time is injected via a ``now_ms`` callable so tests are fully deterministic.
Per-track first-seen/last-seen dwell records are accumulated as scaffolding for
a future v0.2 dwell event (not emitted in v0).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .protocol import Frame

__all__ = [
    "OccupancyEvent",
    "DwellRecord",
    "Aggregator",
    "EVENT_OCCUPANCY_COUNT",
    "EVENT_HEARTBEAT",
    "default_now_ms",
]

EVENT_OCCUPANCY_COUNT = "occupancy_count"
EVENT_HEARTBEAT = "heartbeat"


def default_now_ms() -> int:
    """Monotonic clock in integer milliseconds (immune to wall-clock jumps)."""
    return time.monotonic_ns() // 1_000_000


@dataclass(frozen=True)
class OccupancyEvent:
    """An event produced by the aggregator.

    ``count`` is set only for ``occupancy_count`` events. ``seq``/``uptime_ms``
    come from the triggering board frame; ``frames`` is the total number of
    frames the aggregator has processed; ``emitted_ms`` is the injected clock.
    """

    kind: str
    seq: int
    uptime_ms: int
    frames: int
    count: int | None = None
    emitted_ms: int = 0


@dataclass
class DwellRecord:
    """First/last-seen bookkeeping for a single track id (dwell scaffolding)."""

    track_id: int
    first_seen_ms: int
    last_seen_ms: int
    frames_seen: int = 1


@dataclass
class _Candidate:
    """A pending raw count awaiting debounce confirmation."""

    count: int
    since_ms: int
    frames_held: int


class Aggregator:
    """Debounce raw frame counts into stable occupancy + heartbeat events."""

    def __init__(
        self,
        *,
        debounce_frames: int = 3,
        debounce_window_ms: int | None = None,
        heartbeat_s: float = 60.0,
        now_ms: Callable[[], int] = default_now_ms,
        max_dwell_records: int = 1024,
    ) -> None:
        """Configure the aggregator.

        Args:
            debounce_frames: consecutive frames a new count must hold to confirm
                (must be >= 1).
            debounce_window_ms: optional alternative trigger — confirm once a
                candidate has been held at least this many ms. ``None`` disables
                the time-based path (frame-count only).
            heartbeat_s: emit a heartbeat at least this often, in seconds.
            now_ms: injectable monotonic clock returning integer milliseconds.
            max_dwell_records: hard cap on retained per-track dwell records (must
                be >= 1). The on-device tracker assigns ever-increasing ids, so
                without a cap this dict would grow without bound over a long run.
                When the cap is reached, the least-recently-seen record is
                evicted to make room for a new id.
        """
        if debounce_frames < 1:
            raise ValueError("debounce_frames must be >= 1")
        if max_dwell_records < 1:
            raise ValueError("max_dwell_records must be >= 1")
        self._debounce_frames = debounce_frames
        self._debounce_window_ms = debounce_window_ms
        self._heartbeat_ms = int(heartbeat_s * 1000)
        self._now_ms = now_ms
        self._max_dwell_records = max_dwell_records

        self._confirmed: int | None = None
        self._candidate: _Candidate | None = None
        self._frames = 0
        self._last_heartbeat_ms: int | None = None
        self._dwell: dict[int, DwellRecord] = {}

    # -- introspection (scaffolding / stats) --------------------------------

    @property
    def confirmed_count(self) -> int | None:
        """The current debounced occupancy, or ``None`` before first confirm."""
        return self._confirmed

    @property
    def frames_processed(self) -> int:
        """Total frames fed to :meth:`process`."""
        return self._frames

    @property
    def dwell_records(self) -> dict[int, DwellRecord]:
        """Live view of per-track dwell records (v0.2 scaffolding)."""
        return self._dwell

    # -- core ----------------------------------------------------------------

    def process(self, frame: Frame) -> list[OccupancyEvent]:
        """Feed one frame; return any events triggered (0, 1, or 2)."""
        now = self._now_ms()
        self._frames += 1
        self._update_dwell(frame, now)

        events: list[OccupancyEvent] = []

        change = self._debounce(frame, now)
        if change is not None:
            events.append(change)

        heartbeat = self._maybe_heartbeat(frame, now)
        if heartbeat is not None:
            events.append(heartbeat)

        return events

    # -- internals -----------------------------------------------------------

    def _update_dwell(self, frame: Frame, now: int) -> None:
        for box in frame.boxes:
            rec = self._dwell.get(box.id)
            if rec is None:
                if len(self._dwell) >= self._max_dwell_records:
                    self._evict_stalest_dwell()
                self._dwell[box.id] = DwellRecord(
                    track_id=box.id, first_seen_ms=now, last_seen_ms=now
                )
            else:
                rec.last_seen_ms = now
                rec.frames_seen += 1

    def _evict_stalest_dwell(self) -> None:
        """Drop the least-recently-seen dwell record to bound memory.

        Ties on ``last_seen_ms`` break on the smallest ``track_id`` so eviction
        is deterministic. This is O(n) but runs only when the cap is hit, and the
        cap keeps n small.
        """
        if not self._dwell:  # pragma: no cover - guarded by caller
            return
        stalest = min(
            self._dwell.values(), key=lambda r: (r.last_seen_ms, r.track_id)
        )
        del self._dwell[stalest.track_id]

    def _debounce(self, frame: Frame, now: int) -> OccupancyEvent | None:
        raw = frame.count

        # Already at the confirmed value: any pending candidate is a blip; drop.
        if raw == self._confirmed:
            self._candidate = None
            return None

        # Track / extend the candidate.
        if self._candidate is None or self._candidate.count != raw:
            self._candidate = _Candidate(count=raw, since_ms=now, frames_held=1)
        else:
            self._candidate.frames_held += 1

        if self._candidate_confirmed(now):
            self._confirmed = raw
            self._candidate = None
            return OccupancyEvent(
                kind=EVENT_OCCUPANCY_COUNT,
                seq=frame.seq,
                uptime_ms=frame.ts_ms,
                frames=self._frames,
                count=raw,
                emitted_ms=now,
            )
        return None

    def _candidate_confirmed(self, now: int) -> bool:
        cand = self._candidate
        if cand is None:  # pragma: no cover - guarded by caller
            return False
        if cand.frames_held >= self._debounce_frames:
            return True
        if (
            self._debounce_window_ms is not None
            and (now - cand.since_ms) >= self._debounce_window_ms
        ):
            return True
        return False

    def _maybe_heartbeat(self, frame: Frame, now: int) -> OccupancyEvent | None:
        if self._last_heartbeat_ms is None:
            # Anchor the cadence on the first frame; do not emit immediately.
            self._last_heartbeat_ms = now
            return None
        if (now - self._last_heartbeat_ms) >= self._heartbeat_ms:
            self._last_heartbeat_ms = now
            return OccupancyEvent(
                kind=EVENT_HEARTBEAT,
                seq=frame.seq,
                uptime_ms=frame.ts_ms,
                frames=self._frames,
                count=self._confirmed,
                emitted_ms=now,
            )
        return None
