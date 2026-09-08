"""Pure parser for the STM32N6 ``DET`` wire protocol.

Wire format (one line per inference frame)::

    DET <seq> <ts_ms> <count> [<id>:<cx>:<cy>:<w>:<h>:<conf> ...]

See ``PROTOCOL.md`` for the authoritative specification. This module is pure:
it has no I/O and never raises on malformed input — :func:`parse_line` returns
``None`` so the caller can count drops.
"""

from __future__ import annotations

from dataclasses import dataclass, field

LINE_TAG = "DET"

# Field bounds from the protocol spec.
_PERMILLE_MAX = 1000
_CONF_PCT_MAX = 100
_UINT32_WRAP = 1 << 32  # seq / ts_ms wrap at 32-bit; documented for callers.

__all__ = ["Box", "Frame", "parse_line", "LINE_TAG"]


@dataclass(frozen=True)
class Box:
    """A single person detection.

    Coordinates are in PERMILLE (0..1000) relative to the frame; ``conf`` is a
    confidence PERCENT (0..100). ``id`` is the tracker's persistent track id
    (0 when tracking is disabled).
    """

    id: int
    cx: int
    cy: int
    w: int
    h: int
    conf: int


@dataclass(frozen=True)
class Frame:
    """One parsed inference frame.

    ``seq`` and ``ts_ms`` are unsigned 32-bit board values and may wrap; treat
    them as opaque counters and only ever compare via modular deltas.
    """

    seq: int
    ts_ms: int
    count: int
    boxes: tuple[Box, ...] = field(default_factory=tuple)


def _parse_uint(token: str) -> int | None:
    """Parse a non-negative base-10 ASCII integer, or ``None`` if not one.

    The protocol is ASCII. We require ``isascii() and isdigit()`` rather than a
    bare ``isdigit()``: ``str.isdigit()`` is ``True`` for non-ASCII forms such
    as full-width digits ('１') — which ``int()`` would happily accept — and for
    superscripts ('²') — which ``int()`` would reject with ``ValueError``. The
    ASCII gate rejects both, so this never raises and never accepts a non-ASCII
    digit that the firmware would never emit. Empty strings, signs ('-1'), and
    radix prefixes ('0x..') are rejected too.
    """
    if not (token.isascii() and token.isdigit()):
        return None
    return int(token)  # guaranteed [0-9]+ and non-negative; cannot raise


def _parse_box(token: str) -> Box | None:
    """Parse one ``id:cx:cy:w:h:conf`` tuple, validating field ranges."""
    parts = token.split(":")
    if len(parts) != 6:
        return None
    nums: list[int] = []
    for p in parts:
        n = _parse_uint(p)
        if n is None:
            return None
        nums.append(n)
    bid, cx, cy, w, h, conf = nums
    for coord in (cx, cy, w, h):
        if coord > _PERMILLE_MAX:
            return None
    if conf > _CONF_PCT_MAX:
        return None
    return Box(id=bid, cx=cx, cy=cy, w=w, h=h, conf=conf)


def parse_line(line: str) -> Frame | None:
    """Parse one wire line into a :class:`Frame`.

    Returns ``None`` for board boot/status noise (any line not starting with
    ``"DET "``) and for malformed ``DET`` lines (bad ints, range violations, or
    a ``count`` that disagrees with the number of box tuples). Trailing
    whitespace is tolerated. Never raises.
    """
    if line is None:
        return None
    stripped = line.strip()
    if not stripped:
        return None

    tokens = stripped.split()
    if tokens[0] != LINE_TAG:
        return None  # boot/status noise

    # Need at least: DET seq ts_ms count
    if len(tokens) < 4:
        return None

    seq = _parse_uint(tokens[1])
    ts_ms = _parse_uint(tokens[2])
    count = _parse_uint(tokens[3])
    if seq is None or ts_ms is None or count is None:
        return None

    box_tokens = tokens[4:]
    if len(box_tokens) != count:
        return None  # count must match the number of tuples present

    boxes: list[Box] = []
    for tok in box_tokens:
        box = _parse_box(tok)
        if box is None:
            return None
        boxes.append(box)

    return Frame(seq=seq, ts_ms=ts_ms, count=count, boxes=tuple(boxes))
