"""Line sources behind one iterator interface.

Every source is an :class:`LineSource` — an iterable of already-decoded ``str``
lines (newline-stripped). Concrete sources:

* :class:`SerialSource` — reads a serial device. Uses ``pyserial`` when it is
  installed; otherwise falls back to opening the device as a binary file and
  reading lines. The ``pyserial`` dependency is OPTIONAL.
* :class:`FileReplaySource` — replays a captured log, with optional real-time
  pacing derived from board ``ts_ms`` deltas (off by default).
* :class:`IterableSource` — wraps any iterable of strings (for tests).

All sources decode with ``errors="replace"`` and are robust to partial lines.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Iterator

from .protocol import parse_line

__all__ = ["LineSource", "IterableSource", "FileReplaySource", "SerialSource"]

log = logging.getLogger("occbridge.sources")

#: Ceiling on an unterminated line buffer in the file-fallback reader. A real
#: ``DET`` line is well under 1 KiB; anything past this without a newline is a
#: runaway/garbled stream, so the partial buffer is discarded rather than grown
#: without bound.
MAX_LINE_BYTES = 65536


class LineSource(Iterable[str]):
    """Abstract base: an iterable yielding decoded, newline-stripped lines."""

    def __iter__(self) -> Iterator[str]:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:
        """Release any underlying resource. Safe to call more than once."""

    def __enter__(self) -> "LineSource":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class IterableSource(LineSource):
    """Yield lines from any in-memory iterable of strings. For tests."""

    def __init__(self, lines: Iterable[str]) -> None:
        self._lines = lines

    def __iter__(self) -> Iterator[str]:
        for line in self._lines:
            yield line.rstrip("\r\n")


class FileReplaySource(LineSource):
    """Replay a captured log file.

    Args:
        path: log file to replay.
        realtime: if true, sleep between lines to reproduce the original timing
            using board ``ts_ms`` deltas (clamped to ``max_sleep_s``).
        max_sleep_s: ceiling on any single inter-line sleep, to bound gaps and
            ``ts_ms`` wraps.
        sleeper: injectable sleep function (default :func:`time.sleep`).
    """

    def __init__(
        self,
        path: str,
        *,
        realtime: bool = False,
        max_sleep_s: float = 5.0,
        sleeper=time.sleep,
    ) -> None:
        self._path = path
        self._realtime = realtime
        self._max_sleep_s = max_sleep_s
        self._sleeper = sleeper

    def __iter__(self) -> Iterator[str]:
        prev_ts: int | None = None
        with open(self._path, "rb") as fh:
            for raw in fh:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if self._realtime:
                    prev_ts = self._pace(line, prev_ts)
                yield line

    def _pace(self, line: str, prev_ts: int | None) -> int | None:
        """Sleep for the ts_ms delta vs the previous DET line; return new ts."""
        frame = parse_line(line)
        if frame is None:
            return prev_ts
        if prev_ts is not None:
            delta_ms = frame.ts_ms - prev_ts
            if 0 < delta_ms:  # ignore wraps / non-monotonic jumps
                self._sleeper(min(delta_ms / 1000.0, self._max_sleep_s))
        return frame.ts_ms


class SerialSource(LineSource):
    """Read decoded lines from a serial device.

    Prefers ``pyserial``; if importing it fails, falls back to reading the
    device as a binary file. Either way, lines are decoded with
    ``errors="replace"`` so a corrupt byte never crashes the bridge.
    """

    def __init__(self, device: str, baud: int = 115200, *, timeout: float = 1.0) -> None:
        self._device = device
        self._baud = baud
        self._timeout = timeout
        self._serial = None  # pyserial handle, if used
        self._fh = None  # binary file handle, fallback path

    @property
    def using_pyserial(self) -> bool:
        """True once iteration has opened the device via pyserial."""
        return self._serial is not None

    def __iter__(self) -> Iterator[str]:
        try:
            import serial  # type: ignore
        except ImportError:
            yield from self._iter_file()
        else:
            yield from self._iter_pyserial(serial)

    def _iter_pyserial(self, serial_mod) -> Iterator[str]:
        self._serial = serial_mod.Serial(
            self._device, self._baud, timeout=self._timeout
        )
        try:
            while True:
                raw = self._serial.readline()
                if not raw:
                    continue  # timeout tick, keep waiting
                yield raw.decode("utf-8", errors="replace").rstrip("\r\n")
        finally:
            self.close()

    def _iter_file(self) -> Iterator[str]:
        # Fallback: a tty exposed as a file. We cannot set baud here, so this
        # path assumes the port is already configured (e.g. via stty).
        self._fh = open(self._device, "rb", buffering=0)
        try:
            buf = bytearray()
            while True:
                chunk = self._fh.read(256)
                if not chunk:
                    if buf:
                        yield buf.decode("utf-8", errors="replace").rstrip("\r\n")
                        buf.clear()
                    break
                buf.extend(chunk)
                while b"\n" in buf:
                    line, _, rest = buf.partition(b"\n")
                    buf = bytearray(rest)
                    yield line.decode("utf-8", errors="replace").rstrip("\r\n")
                if len(buf) > MAX_LINE_BYTES:
                    # No newline in a buffer this large: a runaway/garbled
                    # stream. Drop it instead of growing memory without bound.
                    log.warning(
                        "discarding %d-byte unterminated line buffer", len(buf)
                    )
                    buf.clear()
        finally:
            self.close()

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None
        if self._fh is not None:
            try:
                self._fh.close()
            finally:
                self._fh = None
