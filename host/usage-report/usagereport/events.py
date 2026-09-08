"""Read a JSONL occupancy-event log into typed records.

This module is deliberately decoupled from the occupancy bridge: it does NOT
import ``occbridge``. It only understands the schema-v0 envelope on the wire::

    {"v":0,"node":<str>,"event":<str>,"confidence":<float|null>,
     "data":<dict>,"ts":<iso8601 str | epoch seconds>}

It is forgiving by design — a real capture log will contain blank lines, the
occasional truncated/garbage line, and event verbs this tool does not model.
None of those should crash a report. Bad lines are skipped and counted so the
report can disclose how much input it had to drop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

__all__ = [
    "OccEvent",
    "ParseResult",
    "KNOWN_EVENTS",
    "EVENT_OCCUPANCY_COUNT",
    "EVENT_ZONE_OCCUPANCY",
    "EVENT_QUEUE_LENGTH",
    "EVENT_DWELL_SAMPLE",
    "EVENT_HEARTBEAT",
    "parse_ts",
    "parse_line",
    "read_events",
    "read_events_file",
]

EVENT_OCCUPANCY_COUNT = "occupancy_count"
EVENT_ZONE_OCCUPANCY = "zone_occupancy"
EVENT_QUEUE_LENGTH = "queue_length"
EVENT_DWELL_SAMPLE = "dwell_sample"
EVENT_HEARTBEAT = "heartbeat"

#: Event verbs this tool understands. Anything else is "unknown" — skipped and
#: counted, never an error (forward-compat per the spine contract).
KNOWN_EVENTS = frozenset(
    {
        EVENT_OCCUPANCY_COUNT,
        EVENT_ZONE_OCCUPANCY,
        EVENT_QUEUE_LENGTH,
        EVENT_DWELL_SAMPLE,
        EVENT_HEARTBEAT,
    }
)


@dataclass(frozen=True)
class OccEvent:
    """One well-formed, known occupancy event.

    ``ts`` is always a timezone-aware :class:`datetime`. ``data`` is the raw
    payload object as parsed; helpers below read individual fields defensively.
    """

    node: str
    event: str
    ts: datetime
    confidence: float | None
    data: dict[str, Any]

    @property
    def space(self) -> str:
        """The human area label, falling back to the node id if absent."""
        space = self.data.get("space")
        return space if isinstance(space, str) and space else self.node

    @property
    def count(self) -> int | None:
        """``occupancy_count`` people-in-view, or ``None`` if not applicable."""
        return _as_nonneg_int(self.data.get("count"))

    @property
    def length(self) -> int | None:
        """``queue_length`` people-waiting, or ``None``."""
        return _as_nonneg_int(self.data.get("length"))

    @property
    def dwell_s(self) -> int | None:
        """``dwell_sample`` duration in seconds, or ``None``."""
        return _as_nonneg_int(self.data.get("dwell_s"))

    @property
    def zone(self) -> str | None:
        """Optional zone label carried by queue/dwell events."""
        zone = self.data.get("zone")
        return zone if isinstance(zone, str) and zone else None

    @property
    def zones(self) -> dict[str, int]:
        """``zone_occupancy`` per-zone counts (only valid non-negative ints)."""
        raw = self.data.get("zones")
        if not isinstance(raw, dict):
            return {}
        out: dict[str, int] = {}
        for name, value in raw.items():
            n = _as_nonneg_int(value)
            if isinstance(name, str) and n is not None:
                out[name] = n
        return out


@dataclass
class ParseResult:
    """Outcome of reading a log: the good events plus drop bookkeeping."""

    events: list[OccEvent] = field(default_factory=list)
    total_lines: int = 0
    blank_lines: int = 0
    malformed: int = 0
    unknown: int = 0

    @property
    def kept(self) -> int:
        return len(self.events)

    @property
    def dropped(self) -> int:
        return self.malformed + self.unknown


def _as_nonneg_int(value: Any) -> int | None:
    """Coerce to a non-negative int, or ``None`` if it cannot be one.

    Accepts ints and integral floats (e.g. ``3.0``); rejects bools, negatives,
    fractional floats, and anything non-numeric.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if value < 0 or value != int(value):
            return None
        return int(value)
    return None


def parse_ts(raw: Any) -> datetime:
    """Parse a timestamp to an aware UTC-anchored :class:`datetime`.

    Accepts an ISO-8601 string (with or without a trailing ``Z``) or epoch
    seconds (int/float, or a numeric string). A naive ISO string is assumed to
    be UTC. Raises :class:`ValueError` on anything else.
    """
    if isinstance(raw, bool):  # bool is an int subclass — reject explicitly
        raise ValueError("timestamp must not be a boolean")
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            raise ValueError("empty timestamp")
        iso = s[:-1] + "+00:00" if s.endswith("Z") else s
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            # Not ISO — maybe a bare epoch like "1781020163".
            try:
                return datetime.fromtimestamp(float(s), tz=timezone.utc)
            except (ValueError, OverflowError, OSError):
                raise ValueError(f"unparseable timestamp: {raw!r}") from None
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    raise ValueError(f"unsupported timestamp type: {type(raw).__name__}")


def parse_line(line: str) -> OccEvent | None:
    """Parse a single JSONL line into an :class:`OccEvent`.

    Returns ``None`` for blank lines, malformed JSON, structurally invalid
    envelopes, and unknown event verbs. The caller distinguishes these via
    :func:`read_events`; this function intentionally never raises.
    """
    if not line or not line.strip():
        return None
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None

    node = obj.get("node")
    event = obj.get("event")
    if not isinstance(node, str) or not node:
        return None
    if not isinstance(event, str) or not event:
        return None
    if event not in KNOWN_EVENTS:
        return None  # known-shape but unmodeled verb — caller counts as unknown

    if "ts" not in obj:
        return None
    try:
        ts = parse_ts(obj["ts"])
    except ValueError:
        return None

    data = obj.get("data", {})
    if not isinstance(data, dict):
        return None

    conf = obj.get("confidence")
    confidence = float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else None

    return OccEvent(node=node, event=event, ts=ts, confidence=confidence, data=data)


def read_events(lines: Iterable[str]) -> ParseResult:
    """Read JSONL ``lines`` into a :class:`ParseResult`.

    Classifies each line as kept / blank / malformed / unknown-event so the
    report can honestly disclose dropped input. Lines are kept in input order;
    callers that need chronological order should sort by ``OccEvent.ts``.
    """
    result = ParseResult()
    for line in lines:
        result.total_lines += 1
        stripped = line.strip()
        if not stripped:
            result.blank_lines += 1
            continue
        # Re-derive the classification (parse_line collapses all failures to None).
        try:
            obj = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            result.malformed += 1
            continue
        if isinstance(obj, dict) and isinstance(obj.get("event"), str) and obj["event"] not in KNOWN_EVENTS:
            result.unknown += 1
            continue
        ev = parse_line(stripped)
        if ev is None:
            result.malformed += 1
            continue
        result.events.append(ev)
    return result


def read_events_file(path: str) -> ParseResult:
    """Read JSONL from a file path. Use ``-`` for stdin."""
    import sys

    if path == "-":
        return read_events(sys.stdin)
    with open(path, "r", encoding="utf-8") as fh:
        return read_events(fh)
