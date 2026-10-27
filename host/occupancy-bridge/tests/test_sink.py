"""Tests for the JSONL sink: record shape, write discipline, CLI wiring.

The optional cross-check against the Edge Kit reference ``emit.py`` runs only
when ``EDGEKIT_EMIT_PY`` names that file; otherwise it skips with the reason.
"""

import fcntl
import importlib.util
import io
import json
import multiprocessing
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr

from occbridge import events as ev
from occbridge.cli import main
from occbridge.sink import JsonlSink, canonical_ts, to_record

SAMPLE = os.path.join(os.path.dirname(__file__), "sample_capture.log")
CANON = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
REQUIRED = {"v", "node", "event", "ts", "received_at"}


def _lines(path):
    with open(path, "rb") as fh:
        return fh.read().split(b"\n")


def _writer(path, wid, n, pad):
    sink = JsonlSink(path)
    for i in range(n):
        env = ev.make_envelope(
            "occupancy_count", {"count": i, "w": wid, "pad": pad}, node=f"n6-occ-w{wid}"
        )
        assert sink.emit(env)


class TestCanonicalTs(unittest.TestCase):
    def test_forms_store_identically(self):
        want = "2026-10-25T23:59:59.999Z"
        for raw in (
            "2026-10-25T23:59:59.999999+00:00",
            "2026-10-25T16:59:59.9999-07:00",
            "2026-10-25T23:59:59.999Z",
            1792972799.999,
        ):
            self.assertEqual(canonical_ts(raw), want, raw)

    def test_truncates_never_rounds(self):
        self.assertEqual(canonical_ts("2026-01-01T00:00:00.9999999Z"), "2026-01-01T00:00:00.999Z")
        self.assertEqual(canonical_ts(0), "1970-01-01T00:00:00.000Z")

    def test_rejects(self):
        for raw in (True, float("nan"), float("inf"), "1781020163",
                    "2026-10-25T23:59:59", "2026-10-25", None):
            with self.assertRaises(ValueError, msg=repr(raw)):
                canonical_ts(raw)

    def test_rejects_trailing_newline_and_non_ascii_digits(self):
        # `$` matches before a final "\n"; `\d` without re.ASCII matches U+0662.
        for raw in ("2026-10-25T23:59:59Z\n", "\u0662026-10-25T23:59:59Z"):
            with self.assertRaises(ValueError, msg=repr(raw)):
                canonical_ts(raw)

    def test_record_overwrites_received_at(self):
        env = ev.heartbeat_event(1, 2, 3, node="n6-occ-test")
        env["received_at"] = "bogus"
        rec = to_record(env, received_at="2026-01-01T00:00:00.000Z")
        self.assertEqual(rec["received_at"], "2026-01-01T00:00:00.000Z")
        self.assertTrue(CANON.match(rec["ts"]))


class TestSinkWrites(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "sub", "events.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_replay_cli_to_sink(self):
        err = io.StringIO()
        with redirect_stderr(err):
            rc = main(["--replay", SAMPLE, "--sink", self.path, "--node", "n6-occ-test"])
        self.assertEqual(rc, 0, err.getvalue())
        raw = _lines(self.path)
        self.assertEqual(raw[-1], b"", "file must end in a newline")
        recs = [json.loads(b) for b in raw[:-1]]
        counts = [r["data"]["count"] for r in recs if r["event"] == "occupancy_count"]
        self.assertEqual(counts, [0, 1, 2, 0])
        for r in recs:
            self.assertTrue(REQUIRED <= r.keys(), r)
            self.assertEqual(r["v"], 0)
            self.assertEqual(r["node"], "n6-occ-test")
            self.assertTrue(CANON.match(r["ts"]), r["ts"])
            self.assertTrue(CANON.match(r["received_at"]), r["received_at"])
        self.assertIn(f"events_emitted={len(recs)}", err.getvalue())
        self.assertIn("spine_failed=0", err.getvalue())

    def test_sink_excludes_spine_and_dry_run(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--replay", SAMPLE, "--sink", self.path, "--dry-run"]), 2)
            self.assertEqual(
                main(["--replay", SAMPLE, "--sink", self.path, "--spine-url", "http://x/"]), 2
            )
        self.assertFalse(os.path.exists(self.path))

    def test_repairs_missing_trailing_newline(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "wb") as fh:
            fh.write(b'{"v":0,"node":"n6-occ-x","ev')  # crash mid-write
        sink = JsonlSink(self.path)
        self.assertTrue(sink.emit(ev.heartbeat_event(1, 2, 3, node="n6-occ-test")))
        raw = _lines(self.path)
        self.assertEqual(raw[0], b'{"v":0,"node":"n6-occ-x","ev')
        self.assertEqual(json.loads(raw[1])["event"], "heartbeat")
        self.assertEqual(raw[2:], [b""])

    def test_no_extra_newline_on_clean_file(self):
        sink = JsonlSink(self.path)
        for i in range(3):
            sink.emit(ev.occupancy_count_event(i, node="n6-occ-test"))
        raw = _lines(self.path)
        self.assertEqual(len(raw), 4)
        self.assertNotIn(b"", raw[:-1])

    def test_waits_for_exclusive_lock(self):
        os.makedirs(os.path.dirname(self.path))
        open(self.path, "wb").close()
        holder = os.open(self.path, os.O_RDWR)
        fcntl.flock(holder, fcntl.LOCK_EX)
        done = threading.Event()
        sink = JsonlSink(self.path)

        def _go():
            sink.emit(ev.heartbeat_event(1, 2, 3, node="n6-occ-test"))
            done.set()

        t = threading.Thread(target=_go, daemon=True)
        try:
            t.start()
            time.sleep(0.3)
            self.assertFalse(done.is_set(), "append ran while another holder had the flock")
            self.assertEqual(os.path.getsize(self.path), 0)
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
            os.close(holder)
        t.join(5)
        self.assertTrue(done.is_set())
        self.assertEqual(len(_lines(self.path)), 2)

    def test_one_write_per_line(self):
        calls = []
        real = os.write

        def spy(fd, b):
            calls.append(bytes(b))
            return real(fd, b)

        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "wb") as fh:
            fh.write(b"fragment")
        sink = JsonlSink(self.path)
        os.write = spy
        try:
            sink.emit(ev.heartbeat_event(1, 2, 3, node="n6-occ-test"))
        finally:
            os.write = real
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith(b"\n{") and calls[0].endswith(b"}\n"))

    def test_concurrent_processes_do_not_interleave(self):
        writers, per, pad = 6, 150, "x" * 600
        ctx = multiprocessing.get_context("fork")
        procs = [ctx.Process(target=_writer, args=(self.path, w, per, pad)) for w in range(writers)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(60)
            self.assertEqual(p.exitcode, 0)
        raw = _lines(self.path)
        self.assertEqual(raw[-1], b"")
        seen = {w: [] for w in range(writers)}
        for b in raw[:-1]:
            r = json.loads(b)  # an interleaved or glued line fails here
            seen[r["data"]["w"]].append(r["data"]["count"])
        for w in range(writers):
            self.assertEqual(seen[w], list(range(per)))

    def test_bad_envelope_and_disk_error_fail_open(self):
        sink = JsonlSink(self.path)
        self.assertFalse(sink.emit({"v": 0}))
        bad_ts = ev.heartbeat_event(1, 2, 3, node="n6-occ-test", ts="2026-01-01T00:00:00")
        self.assertFalse(sink.emit(bad_ts))
        nan = ev.occupancy_count_event(1, node="n6-occ-test")
        nan["data"]["x"] = float("nan")
        self.assertFalse(sink.emit(nan))
        dirsink = JsonlSink(self.tmp.name)  # a directory: open() fails
        self.assertFalse(dirsink.emit(ev.heartbeat_event(1, 2, 3, node="n6-occ-test")))
        self.assertEqual(sink.stats(), {"sent": 0, "failed": 3})
        self.assertEqual(dirsink.stats(), {"sent": 0, "failed": 1})


class TestAgainstReferenceEmit(unittest.TestCase):
    """Sink output must be what the reference writer would have stored."""

    def setUp(self):
        path = os.environ.get("EDGEKIT_EMIT_PY")
        if not path:
            reason = "EDGEKIT_EMIT_PY not set (path to the Edge Kit emit.py)"
        elif not os.path.isfile(path):
            reason = f"EDGEKIT_EMIT_PY={path} is not a file"
        else:
            reason = None
            try:
                spec = importlib.util.spec_from_file_location("edgekit_emit", path)
                self.emit = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(self.emit)
            except Exception as exc:  # noqa: BLE001 - any import failure is a skip
                reason = f"cannot import {path}: {exc!r}"
        if reason:
            print(f"\nSKIP reference emit.py check: {reason}", file=sys.stderr)
            self.skipTest(reason)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "events.jsonl")

    def test_replay_sink_matches_reference(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--replay", SAMPLE, "--sink", self.path,
                                   "--node", "n6-occ-test", "--heartbeat-s", "0.0001"]), 0)
        raw = _lines(self.path)[:-1]
        self.assertTrue(raw)
        for b in raw:
            rec = json.loads(b)
            self.emit.validate(rec)
            again = self.emit.normalise(rec, received_at=rec["received_at"])
            self.assertEqual(again, rec)
            self.assertEqual(self.emit._encode(again), b)  # byte-identical line
        recs, stats = self.emit.read_sink(self.path)
        self.assertEqual(stats["skipped"], 0)
        self.assertEqual(len(recs) + stats["duplicates"], len(raw))

    def test_shared_file_with_reference_writer(self):
        sink = JsonlSink(self.path)
        sink.emit(ev.occupancy_count_event(1, node="n6-occ-test"))
        self.emit.append(self.emit.build_event("nano-01", "heartbeat"), self.path)
        with open(self.path, "ab") as fh:
            fh.write(b'{"trunc')
        sink.emit(ev.occupancy_count_event(2, node="n6-occ-test"))
        recs, stats = self.emit.read_sink(self.path)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual([r["node"] for r in recs], ["n6-occ-test", "nano-01", "n6-occ-test"])


if __name__ == "__main__":
    unittest.main()
