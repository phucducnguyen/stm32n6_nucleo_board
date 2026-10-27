#!/usr/bin/env python3
"""Runtime privacy judge for a captured console-UART log of the occupancy build.

The static judge (privacy_judge.py) proves no USB, network, display or storage
egress is linked. The console UART is the one egress the product needs, so this
judge checks what actually came out of it: only DET lines and short status text.

Rules (any violation = FAIL):
  U1 printable    every byte is printable ASCII, tab, CR or LF (no binary dump)
  U2 det-valid    every line whose first token is DET parses under PROTOCOL.md
                  (a malformed DET line is a line nobody validated)
  U3 det-bounded  no DET line is longer than MAX_DET_BYTES (16 boxes, the
                  firmware's cap, fit in ~420 bytes)
  U4 noise-short  no non-DET line is longer than MAX_NOISE_BYTES
  U5 no-blob      no line holds a run of >= BLOB_RUN hex/base64 characters
                  without a space (the signature of an encoded pixel dump)
  U6 noise-budget after the first DET line, non-DET bytes stay under
                  max(NOISE_FLOOR, 1% of DET bytes)
  U7 has-det      at least one valid DET line (an empty log proves nothing)
  U8 count-only   (with --count-only) no DET line carries box tuples. Boxes are
                  a covert channel: 16 boxes x 5 fields is ~100 bytes a frame,
                  enough to leak a thumbnail in seconds. The shipped privacy
                  claim is made on the count-only build.

Usage: uart_log_judge.py [--count-only] <capture.log>   (exit 0 PASS, 1 FAIL, 2 could not read)
Host-stamped captures (`<timestamp> DET ...`) are accepted: a leading token
before DET is stripped when the second token is DET.
"""

from __future__ import annotations

import re
import sys

from _det import parse_line

MAX_DET_BYTES = 420
MAX_NOISE_BYTES = 160
BLOB_RUN = 48
NOISE_FLOOR = 4096
_BLOB = re.compile(r"[A-Za-z0-9+/=]{%d,}" % BLOB_RUN)
_PRINTABLE = set(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}


def _strip_host_stamp(line: str) -> str:
    parts = line.split(None, 1)
    if len(parts) == 2 and parts[1].startswith("DET ") and parts[0] != "DET":
        return parts[1]
    return line


def judge_bytes(data: bytes, count_only: bool = False) -> tuple[bool, list[str]]:
    failures: list[str] = []
    bad = [i for i, b in enumerate(data) if b not in _PRINTABLE]
    if bad:
        failures.append(f"U1 printable: {len(bad)} non-printable bytes, first at offset {bad[0]}")
    text = data.decode("ascii", errors="replace")
    det_bytes = 0
    noise_after_det = 0
    seen_det = False
    n_det = 0
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = _strip_host_stamp(raw.rstrip("\r"))
        if _BLOB.search(line):
            failures.append(f"U5 no-blob: line {lineno} has a {BLOB_RUN}+ char encoded run")
        if line.split(" ", 1)[0] == "DET":
            frame = parse_line(line)
            if frame is None:
                failures.append(f"U2 det-valid: line {lineno} is a malformed DET line")
            elif count_only and frame.boxes:
                failures.append(f"U8 count-only: line {lineno} carries {len(frame.boxes)} box tuples")
            elif len(line) > MAX_DET_BYTES:
                failures.append(f"U3 det-bounded: line {lineno} is {len(line)} bytes")
            else:
                n_det += 1
                seen_det = True
            det_bytes += len(line) + 1
        else:
            if len(line) > MAX_NOISE_BYTES:
                failures.append(f"U4 noise-short: line {lineno} is {len(line)} bytes")
            if seen_det:
                noise_after_det += len(line) + 1
    budget = max(NOISE_FLOOR, det_bytes // 100)
    if noise_after_det > budget:
        failures.append(f"U6 noise-budget: {noise_after_det} non-DET bytes after first DET > {budget}")
    if n_det == 0:
        failures.append("U7 has-det: no valid DET line")
    return (not failures), failures


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    count_only = "--count-only" in argv
    if count_only:
        argv.remove("--count-only")
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print("usage: uart_log_judge.py [--count-only] <capture.log>", file=sys.stderr)
        return 2
    try:
        with open(argv[0], "rb") as f:
            data = f.read()
    except OSError as exc:
        print(f"COULD-NOT-CHECK: {exc}", file=sys.stderr)
        return 2
    ok, failures = judge_bytes(data, count_only)
    for fl in failures[:20]:
        print("  FAIL " + fl)
    if len(failures) > 20:
        print(f"  ... {len(failures) - 20} more")
    print(f"VERDICT: {'PASS only DET lines and short status text' if ok else 'FAIL'} ({argv[0]})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
