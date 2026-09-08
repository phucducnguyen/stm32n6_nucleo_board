"""Command-line entry point: JSONL log -> weekly usage report.

    python -m usagereport --input events.jsonl --open "07:00-18:00" --out report.md

Reads a JSONL occupancy log, analyzes it, and writes a plain-language report.
Defaults are non-identifying. Errors are reported cleanly (no tracebacks).
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .analyze import (
    AnalysisConfig,
    OpenHours,
    analyze,
    parse_days,
    parse_open_window,
    parse_tz_offset,
)
from .events import read_events_file
from .render import render


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="usagereport",
        description="Turn an occupancy event log (JSONL) into a weekly usage report.",
    )
    p.add_argument("--input", "-i", default="-", help="JSONL event log, or '-' for stdin (default).")
    p.add_argument("--out", "-o", default="-", help="Output file, or '-' for stdout (default).")
    p.add_argument("--format", "-f", choices=["md", "txt"], default="txt", help="Report format (default txt).")
    p.add_argument(
        "--open",
        dest="open_window",
        default=None,
        metavar="HH:MM-HH:MM",
        help="Operating hours window, e.g. '07:00-18:00'. Default: all day.",
    )
    p.add_argument("--closed", default="", metavar="DAYS", help="Closed weekdays, e.g. 'sun' or 'sat,sun'.")
    p.add_argument("--tz", default="+00:00", metavar="OFFSET", help="UTC offset for local wall-clock, e.g. '-07:00'. Default UTC.")
    p.add_argument("--node", default=None, help="Restrict to one node id.")
    p.add_argument("--space", default=None, help="Restrict to one space label.")
    p.add_argument("--bucket", type=int, default=30, metavar="MIN", help="Time resolution in minutes (default 30).")
    p.add_argument("--dwell-long", type=int, default=300, metavar="SEC", help="Long-dwell threshold in seconds (default 300).")
    p.add_argument("--queue-threshold", type=int, default=3, metavar="N", help="Queue 'too long' threshold (default 3).")
    p.add_argument("--quiet", action="store_true", help="Suppress the input-quality summary on stderr.")
    p.add_argument("--version", action="version", version=f"usagereport {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.open_window:
            start_min, end_min = parse_open_window(args.open_window)
        else:
            start_min, end_min = 0, 24 * 60
        closed = parse_days(args.closed) if args.closed else frozenset()
        tz_offset = parse_tz_offset(args.tz)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.bucket <= 0:
        print("error: --bucket must be a positive number of minutes", file=sys.stderr)
        return 2

    try:
        parsed = read_events_file(args.input)
    except FileNotFoundError:
        print(f"error: input file not found: {args.input}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: cannot read input: {exc}", file=sys.stderr)
        return 1

    events = parsed.events
    if args.node:
        events = [e for e in events if e.node == args.node]

    config = AnalysisConfig(
        open_hours=OpenHours(start_min=start_min, end_min=end_min, closed_days=closed),
        tz_offset_minutes=tz_offset,
        bucket_minutes=args.bucket,
        dwell_long_s=args.dwell_long,
        queue_threshold=args.queue_threshold,
    )

    summary = analyze(events, config, space=args.space)
    report = render(summary, fmt=args.format)

    try:
        if args.out == "-":
            sys.stdout.write(report)
            if not report.endswith("\n"):
                sys.stdout.write("\n")
        else:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(report)
    except OSError as exc:
        print(f"error: cannot write output: {exc}", file=sys.stderr)
        return 1

    if not args.quiet:
        _input_summary(parsed, len(events), args)

    return 0


def _input_summary(parsed, kept_after_filter: int, args) -> None:
    bits = [f"read {parsed.total_lines} lines", f"kept {parsed.kept} events"]
    if args.node:
        bits.append(f"{kept_after_filter} after --node {args.node}")
    if parsed.blank_lines:
        bits.append(f"{parsed.blank_lines} blank")
    if parsed.malformed:
        bits.append(f"{parsed.malformed} malformed")
    if parsed.unknown:
        bits.append(f"{parsed.unknown} unknown-event")
    print("usagereport: " + ", ".join(bits) + ".", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
