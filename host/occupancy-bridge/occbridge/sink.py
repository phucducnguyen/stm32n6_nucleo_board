"""Append event envelopes to a local JSONL sink file (stdlib only).

:class:`JsonlSink` has the same ``emit()``/``stats()`` shape as
:class:`~occbridge.spine_client.SpineClient`, so the CLI can use either. Each
event becomes one stored record:

* the validated envelope with ``ts`` rewritten to canonical RFC 3339 UTC,
  ``Z`` suffix, millisecond precision, truncated toward the past (never
  rounded, so an instant cannot move into the next window);
* plus ``received_at`` in the same form (host clock at append; any
  envelope-supplied value is overwritten).

Write discipline: the file is opened ``O_APPEND``, an exclusive ``flock`` is
held for the whole append, and each record goes out in exactly one ``write()``.
Concurrent producers therefore never interleave. If the file does not end in a
newline (a crash mid-write left a fragment), a newline is written first, in the
same ``write()``, so the new record is never glued onto the fragment.

Like the spine client it is fail-open: a bad envelope or a disk error is
logged and counted, never raised.
"""

from __future__ import annotations

import fcntl
import json
import logging
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from decimal import ROUND_FLOOR, Decimal, localcontext
from typing import Any

from .events import validate

__all__ = ["JsonlSink", "canonical_ts", "to_record"]

log = logging.getLogger("occbridge.sink")

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_RFC3339 = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?"
    r"([Zz]|[+-]\d{2}:\d{2})\Z",
    re.ASCII,  # \d must not match non-ASCII digits; \Z, not $, so no trailing newline
)


def _fmt_ms(ms: int) -> str:
    try:
        dt = _EPOCH + timedelta(milliseconds=ms)
    except OverflowError:
        raise ValueError("ts out of range") from None
    return (
        f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}T{dt.hour:02d}:{dt.minute:02d}:"
        f"{dt.second:02d}.{dt.microsecond // 1000:03d}Z"
    )


def canonical_ts(raw: Any) -> str:
    """Return ``raw`` (unix seconds, or RFC 3339 with ``Z``/offset) as
    ``YYYY-MM-DDTHH:MM:SS.mmmZ``, truncated to the millisecond.

    Raises :class:`ValueError` for a bool, NaN/inf, a numeric string, a naive
    time or any other shape.
    """
    if isinstance(raw, bool):
        raise ValueError("ts must not be a bool")
    if isinstance(raw, int):
        return _fmt_ms(raw * 1000)
    if isinstance(raw, float):
        if not math.isfinite(raw):
            raise ValueError("ts must be finite")
        # repr() keeps the float as written; only ROUND_FLOOR truncates.
        with localcontext() as ctx:
            ctx.prec = 80
            ms = int((Decimal(repr(raw)) * 1000).to_integral_value(rounding=ROUND_FLOOR))
        return _fmt_ms(ms)
    if not isinstance(raw, str):
        raise ValueError(f"ts must be unix seconds or an RFC 3339 string, got {type(raw).__name__}")
    m = _RFC3339.match(raw)
    if not m:
        raise ValueError(f"ts string is not RFC 3339 with Z or offset: {raw!r}")
    y, mo, d, hh, mi, ss, frac, off = m.groups()
    if off in ("Z", "z"):
        tz = timezone.utc
    else:
        sign = 1 if off[0] == "+" else -1
        oh, om = int(off[1:3]), int(off[4:6])
        if om > 59:
            raise ValueError(f"ts offset minutes out of range: {raw!r}")
        tz = timezone(sign * timedelta(hours=oh, minutes=om))
    try:
        td = datetime(int(y), int(mo), int(d), int(hh), int(mi), int(ss), tzinfo=tz) - _EPOCH
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"ts not a valid time: {raw!r} ({exc})") from None
    # Truncate the fraction as text: arithmetic could round .9999 up a second.
    ms = (td.days * 86400 + td.seconds) * 1000 + int(((frac or "") + "000")[:3])
    return _fmt_ms(ms)


def _now_ms() -> str:
    return _fmt_ms(time.time_ns() // 1_000_000)


def to_record(envelope: dict[str, Any], received_at: str | None = None) -> dict[str, Any]:
    """Validated envelope -> stored record (canonical ``ts`` + ``received_at``)."""
    validate(envelope)
    rec = dict(envelope)
    rec["ts"] = canonical_ts(envelope["ts"])
    rec["received_at"] = received_at or _now_ms()
    return rec


def _encode(rec: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            rec, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except UnicodeEncodeError:  # lone surrogate
        raise ValueError("record holds a string that is not valid Unicode") from None
    except (TypeError, RecursionError) as exc:
        raise ValueError(f"record cannot be written as JSON: {exc}") from None


class JsonlSink:
    """Append-only JSONL sink. ``path`` is created (with parents) on first use."""

    def __init__(self, path: str) -> None:
        if not path:
            raise ValueError("sink path must be non-empty")
        self.path = os.path.expanduser(path)
        self.sent = 0
        self.failed = 0

    @property
    def dry_run(self) -> bool:
        return False

    def emit(self, envelope: dict[str, Any]) -> bool:
        """Append one record. Returns True on success; never raises."""
        try:
            line = _encode(to_record(envelope)) + b"\n"
        except ValueError as exc:
            self.failed += 1
            log.error("dropping malformed envelope: %s", exc)
            return False
        try:
            self._append(line)
        except OSError as exc:
            self.failed += 1
            log.warning("sink append failed (fail-open): %s", exc)
            return False
        self.sent += 1
        return True

    def _append(self, line: bytes) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o640)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                # The tail check must sit under the lock, or two writers can both
                # see the fragment and both prepend a newline.
                size = os.fstat(fd).st_size
                if size and os.pread(fd, 1, size - 1) != b"\n":
                    line = b"\n" + line
                n = os.write(fd, line)
                if n != len(line):  # short write on a regular file = disk full
                    raise OSError(f"short write to {self.path}: {n}/{len(line)} bytes")
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def stats(self) -> dict[str, int]:
        """Return ``{"sent", "failed"}`` counters."""
        return {"sent": self.sent, "failed": self.failed}
