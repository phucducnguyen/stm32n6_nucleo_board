"""Command-line entrypoint: source -> parse -> aggregate -> emit.

Wires the pure layers to real I/O, counts parse drops, maps aggregator events
to schema-v0 envelopes, and shuts down cleanly on SIGINT/SIGTERM with a small
stats summary. Site config (node name, spine URL) comes only from CLI args or
env vars — no identifying defaults are baked in.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from collections.abc import Iterable
from dataclasses import dataclass

from . import events as ev
from .aggregator import (
    EVENT_HEARTBEAT,
    EVENT_OCCUPANCY_COUNT,
    Aggregator,
    OccupancyEvent,
)
from .protocol import parse_line
from .sources import FileReplaySource, LineSource, SerialSource
from .spine_client import SpineClient
from .verify import (
    DEFAULT_MIN_FPS,
    DEFAULT_MIN_TRACK_LIFETIME,
    analyze,
    capture,
    format_report,
)

log = logging.getLogger("occbridge.cli")

DEFAULT_DEVICE = "/dev/ttyACM0"
ENV_NODE = "OCC_NODE"
ENV_SPINE_URL = "OCC_SPINE_URL"

__all__ = ["BridgeStats", "run_bridge", "build_parser", "build_verify_parser", "main"]


@dataclass
class BridgeStats:
    """Run counters surfaced in the shutdown summary."""

    frames_parsed: int = 0
    lines_dropped: int = 0
    events_emitted: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "frames_parsed": self.frames_parsed,
            "lines_dropped": self.lines_dropped,
            "events_emitted": self.events_emitted,
        }


def _to_envelope(event: OccupancyEvent, node: str) -> dict:
    """Map an aggregator event onto a schema-v0 envelope."""
    if event.kind == EVENT_OCCUPANCY_COUNT:
        return ev.occupancy_count_event(event.count or 0, node=node)
    if event.kind == EVENT_HEARTBEAT:
        return ev.heartbeat_event(
            event.seq, event.uptime_ms, event.frames, node=node
        )
    raise ValueError(f"unknown aggregator event kind: {event.kind!r}")  # pragma: no cover


def run_bridge(
    lines: Iterable[str],
    aggregator: Aggregator,
    client: SpineClient,
    node: str,
    *,
    should_stop=lambda: False,
    stats: BridgeStats | None = None,
) -> BridgeStats:
    """Drive the pipeline over ``lines`` until exhausted or ``should_stop()``.

    Returns the populated :class:`BridgeStats`. Pure-ish: all I/O is delegated
    to ``client`` and the ``lines`` iterable, so this is unit-testable.
    """
    stats = stats or BridgeStats()
    for line in lines:
        if should_stop():
            break
        frame = parse_line(line)
        if frame is None:
            stats.lines_dropped += 1
            log.debug("dropped non-DET/garbage line: %r", line)
            continue
        stats.frames_parsed += 1
        for event in aggregator.process(frame):
            envelope = _to_envelope(event, node)
            if client.emit(envelope):
                stats.events_emitted += 1
    return stats


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    p = argparse.ArgumentParser(
        prog="occbridge",
        description="Host-side occupancy bridge for an STM32N6 vision sensor.",
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument(
        "--device",
        default=DEFAULT_DEVICE,
        help=f"serial device to read (default: {DEFAULT_DEVICE})",
    )
    src.add_argument(
        "--replay",
        metavar="FILE",
        help="replay a captured log file instead of reading the serial device",
    )
    p.add_argument(
        "--realtime",
        action="store_true",
        help="when replaying, pace lines using board ts_ms deltas",
    )
    p.add_argument("--baud", type=int, default=115200, help="serial baud (default: 115200)")
    p.add_argument(
        "--node",
        default=os.environ.get(ENV_NODE, ev.DEFAULT_NODE),
        help=f"node name for event envelopes (env {ENV_NODE})",
    )
    p.add_argument(
        "--spine-url",
        default=os.environ.get(ENV_SPINE_URL),
        help=f"event spine URL (env {ENV_SPINE_URL}); omit for dry-run",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print event JSON to stdout instead of POSTing",
    )
    p.add_argument(
        "--heartbeat-s",
        type=float,
        default=60.0,
        help="heartbeat cadence in seconds (default: 60)",
    )
    p.add_argument(
        "--debounce-frames",
        type=int,
        default=3,
        help="consecutive frames a new count must hold to confirm (default: 3)",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="increase log verbosity (-v info, -vv debug)",
    )
    return p


def build_verify_parser() -> argparse.ArgumentParser:
    """Construct the argument parser for the ``verify`` subcommand."""
    p = argparse.ArgumentParser(
        prog="occbridge verify",
        description=(
            "Capture the DET stream briefly and print a bring-up GO/NO-GO "
            "report. Exit code 0 = GO (usable), 1 = FAIL."
        ),
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument(
        "--device",
        default=DEFAULT_DEVICE,
        help=f"serial device to read (default: {DEFAULT_DEVICE})",
    )
    src.add_argument(
        "--replay",
        metavar="FILE",
        help="replay a captured log file instead of reading the serial device",
    )
    p.add_argument("--baud", type=int, default=115200, help="serial baud (default: 115200)")
    p.add_argument(
        "--seconds",
        type=float,
        default=20.0,
        help="how long to capture from a live device (default: 20; ignored for --replay)",
    )
    p.add_argument(
        "--min-fps",
        type=float,
        default=DEFAULT_MIN_FPS,
        help=f"healthy frame-rate threshold (default: {DEFAULT_MIN_FPS:g})",
    )
    p.add_argument(
        "--min-track-lifetime",
        type=float,
        default=DEFAULT_MIN_TRACK_LIFETIME,
        help=(
            "mean track lifetime (frames) above which ids count as stable "
            f"(default: {DEFAULT_MIN_TRACK_LIFETIME:g})"
        ),
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="increase log verbosity (-v info, -vv debug)",
    )
    return p


def verify_main(argv: list[str]) -> int:
    """Entry for ``occbridge verify``. Returns 0 on GO, 1 on FAIL, 2 on I/O."""
    args = build_verify_parser().parse_args(argv)
    _configure_logging(args.verbose)

    if args.replay:
        source: LineSource = FileReplaySource(args.replay)
        max_seconds: float | None = None  # replay drains to EOF
    else:
        source = SerialSource(args.device, baud=args.baud)
        max_seconds = args.seconds

    stop = {"flag": False}

    def _handle(signum, _frame):
        stop["flag"] = True

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    try:
        with source:
            frames, lines_total, parse_drops = capture(
                source,
                max_seconds=max_seconds,
                should_stop=lambda: stop["flag"],
            )
    except FileNotFoundError as exc:
        log.error("source not found: %s", exc)
        return 2
    except OSError as exc:
        log.error("source I/O error: %s", exc)
        return 2

    report = analyze(
        frames,
        lines_total=lines_total,
        parse_drops=parse_drops,
        min_fps=args.min_fps,
        min_track_lifetime=args.min_track_lifetime,
    )
    print(format_report(report))
    return 0 if report.is_go else 1


def _configure_logging(verbose: int) -> None:
    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _make_source(args: argparse.Namespace) -> LineSource:
    if args.replay:
        return FileReplaySource(args.replay, realtime=args.realtime)
    return SerialSource(args.device, baud=args.baud)


def main(argv: list[str] | None = None) -> int:
    """Program entrypoint. Returns a process exit code.

    A leading ``verify`` token dispatches to the bring-up GO/NO-GO mode; any
    other invocation is the normal source -> aggregate -> emit bridge.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "verify":
        return verify_main(argv[1:])

    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)

    aggregator = Aggregator(
        debounce_frames=args.debounce_frames,
        heartbeat_s=args.heartbeat_s,
    )
    client = SpineClient(url=args.spine_url, dry_run=args.dry_run)
    if client.dry_run:
        log.info("running in dry-run mode (events printed to stdout)")

    stop = {"flag": False}

    def _handle(signum, _frame):
        log.info("received signal %s, shutting down", signum)
        stop["flag"] = True

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    source = _make_source(args)
    stats = BridgeStats()
    try:
        with source:
            run_bridge(
                source,
                aggregator,
                client,
                args.node,
                should_stop=lambda: stop["flag"],
                stats=stats,
            )
    except FileNotFoundError as exc:
        log.error("source not found: %s", exc)
        return 2
    except OSError as exc:
        log.error("source I/O error: %s", exc)
        return 2
    finally:
        spine = client.stats()
        summary = (
            f"frames_parsed={stats.frames_parsed} "
            f"lines_dropped={stats.lines_dropped} "
            f"events_emitted={stats.events_emitted} "
            f"spine_sent={spine['sent']} spine_failed={spine['failed']}"
        )
        log.info("shutdown stats: %s", summary)
        print(f"[occbridge] {summary}", file=sys.stderr)

    return 0
