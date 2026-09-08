"""Schema-v0 event envelope construction + validation (pure, no I/O).

Envelope shape::

    {
        "v": 0,
        "node": "<str>",
        "event": "<str>",
        "confidence": <float|null>,
        "data": {...},
        "ts": "<iso8601 str>"
    }

Event types emitted in v0:

* ``occupancy_count`` — ``data: {"count": int}``
* ``heartbeat``       — ``data: {"seq": int, "uptime_ms": int, "frames": int}``
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 0
DEFAULT_NODE = "n6-occ-unknown"

EVENT_OCCUPANCY_COUNT = "occupancy_count"
EVENT_HEARTBEAT = "heartbeat"
_KNOWN_EVENTS = frozenset({EVENT_OCCUPANCY_COUNT, EVENT_HEARTBEAT})

#: Target serialized size; envelopes are tiny by construction.
MAX_ENVELOPE_BYTES = 1024

Envelope = dict[str, Any]

__all__ = [
    "SCHEMA_VERSION",
    "DEFAULT_NODE",
    "EVENT_OCCUPANCY_COUNT",
    "EVENT_HEARTBEAT",
    "MAX_ENVELOPE_BYTES",
    "make_envelope",
    "occupancy_count_event",
    "heartbeat_event",
    "validate",
    "to_json",
]


def _now_iso() -> str:
    """Current UTC time as an ISO-8601 string with a ``Z``-style offset."""
    return datetime.now(timezone.utc).isoformat()


def make_envelope(
    event: str,
    data: dict[str, Any],
    *,
    node: str = DEFAULT_NODE,
    confidence: float | None = None,
    ts: str | None = None,
) -> Envelope:
    """Build a schema-v0 envelope. ``ts`` defaults to now (UTC, ISO-8601)."""
    envelope: Envelope = {
        "v": SCHEMA_VERSION,
        "node": node,
        "event": event,
        "confidence": confidence,
        "data": dict(data),
        "ts": ts if ts is not None else _now_iso(),
    }
    return envelope


def occupancy_count_event(
    count: int,
    *,
    node: str = DEFAULT_NODE,
    confidence: float | None = None,
    ts: str | None = None,
) -> Envelope:
    """Build an ``occupancy_count`` envelope."""
    return make_envelope(
        EVENT_OCCUPANCY_COUNT,
        {"count": int(count)},
        node=node,
        confidence=confidence,
        ts=ts,
    )


def heartbeat_event(
    seq: int,
    uptime_ms: int,
    frames: int,
    *,
    node: str = DEFAULT_NODE,
    confidence: float | None = None,
    ts: str | None = None,
) -> Envelope:
    """Build a ``heartbeat`` envelope."""
    return make_envelope(
        EVENT_HEARTBEAT,
        {"seq": int(seq), "uptime_ms": int(uptime_ms), "frames": int(frames)},
        node=node,
        confidence=confidence,
        ts=ts,
    )


def validate(envelope: Envelope) -> None:
    """Raise :class:`ValueError` if ``envelope`` is not a well-formed v0 event."""
    required = {"v", "node", "event", "confidence", "data", "ts"}
    missing = required - envelope.keys()
    if missing:
        raise ValueError(f"envelope missing keys: {sorted(missing)}")
    if envelope["v"] != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema version: {envelope['v']!r}")
    if not isinstance(envelope["node"], str) or not envelope["node"]:
        raise ValueError("node must be a non-empty string")
    if envelope["event"] not in _KNOWN_EVENTS:
        raise ValueError(f"unknown event type: {envelope['event']!r}")
    conf = envelope["confidence"]
    if conf is not None and not isinstance(conf, (int, float)):
        raise ValueError("confidence must be a number or null")
    if not isinstance(envelope["data"], dict):
        raise ValueError("data must be an object")
    if not isinstance(envelope["ts"], str) or not envelope["ts"]:
        raise ValueError("ts must be a non-empty ISO-8601 string")


def to_json(envelope: Envelope) -> str:
    """Serialize an envelope to compact JSON, validating it first.

    Raises :class:`ValueError` if the envelope is malformed or exceeds
    :data:`MAX_ENVELOPE_BYTES` when UTF-8 encoded.
    """
    validate(envelope)
    text = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False)
    size = len(text.encode("utf-8"))
    if size > MAX_ENVELOPE_BYTES:
        raise ValueError(f"envelope too large: {size} > {MAX_ENVELOPE_BYTES} bytes")
    return text
