"""Tests for line sources: file-fallback buffering + runaway-line bounding."""

import os
import tempfile
import unittest

from occbridge.sources import MAX_LINE_BYTES, FileReplaySource, SerialSource


class TestFileFallbackBuffering(unittest.TestCase):
    def _write(self, data: bytes) -> str:
        fd, path = tempfile.mkstemp(suffix=".bin")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        self.addCleanup(os.unlink, path)
        return path

    def test_partial_lines_reassembled_across_chunks(self):
        # Lines longer than the 256-byte read chunk must still come out whole.
        payload = b"DET 1 2 0\n" + b"x" * 600 + b"\nDET 3 4 0\n"
        src = SerialSource(self._write(payload))
        lines = list(src._iter_file())
        self.assertEqual(lines[0], "DET 1 2 0")
        self.assertEqual(lines[1], "x" * 600)
        self.assertEqual(lines[2], "DET 3 4 0")

    def test_runaway_unterminated_buffer_is_discarded(self):
        # A huge run with no newline must not be retained / yielded whole.
        payload = b"y" * (MAX_LINE_BYTES + 5000)
        src = SerialSource(self._write(payload))
        lines = list(src._iter_file())
        self.assertFalse(any(len(line) > MAX_LINE_BYTES for line in lines))

    def test_crlf_stripped(self):
        src = SerialSource(self._write(b"DET 1 2 0\r\nDET 3 4 0\r\n"))
        self.assertEqual(list(src._iter_file()), ["DET 1 2 0", "DET 3 4 0"])


class TestFileReplayPacing(unittest.TestCase):
    def test_realtime_pacing_sleeps_on_positive_delta_only(self):
        fd, path = tempfile.mkstemp(suffix=".log")
        with os.fdopen(fd, "w") as fh:
            fh.write("DET 0 1000 0\nDET 1 1100 0\nDET 2 900 0\n")  # last = wrap
        self.addCleanup(os.unlink, path)
        slept = []
        src = FileReplaySource(path, realtime=True, sleeper=slept.append)
        list(src)
        # One 100ms sleep for the +100ms step; the backwards step is skipped.
        self.assertEqual(slept, [0.1])


if __name__ == "__main__":
    unittest.main()
