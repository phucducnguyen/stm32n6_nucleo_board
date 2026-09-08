"""Tests for the fail-open spine client (dry-run + error handling)."""

import http.client
import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from occbridge import events as ev
from occbridge.spine_client import SpineClient


class TestSpineClient(unittest.TestCase):
    def test_dry_run_prints_json(self):
        client = SpineClient(dry_run=True)
        env = ev.occupancy_count_event(2, node="n6-occ-test")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ok = client.emit(env)
        self.assertTrue(ok)
        self.assertIn('"occupancy_count"', buf.getvalue())
        self.assertEqual(client.stats(), {"sent": 1, "failed": 0})

    def test_no_url_forces_dry_run(self):
        client = SpineClient(url=None)
        self.assertTrue(client.dry_run)

    def test_malformed_envelope_counted_not_raised(self):
        client = SpineClient(dry_run=True)
        ok = client.emit({"v": 0})  # invalid envelope
        self.assertFalse(ok)
        self.assertEqual(client.failed, 1)

    def test_network_error_is_fail_open(self):
        # Point at an unroutable URL; emit must return False, not raise.
        client = SpineClient(url="http://127.0.0.1:9/never", timeout=0.2)
        self.assertFalse(client.dry_run)
        env = ev.heartbeat_event(1, 2, 3, node="n6-occ-test")
        ok = client.emit(env)
        self.assertFalse(ok)
        self.assertEqual(client.failed, 1)
        self.assertEqual(client.sent, 0)

    def test_http_exception_is_fail_open(self):
        # http.client.HTTPException (e.g. BadStatusLine) is NOT an OSError;
        # the client must still swallow it rather than crash the bridge.
        client = SpineClient(url="http://example.invalid/ingest")
        env = ev.heartbeat_event(1, 2, 3, node="n6-occ-test")
        with mock.patch(
            "occbridge.spine_client.urllib.request.urlopen",
            side_effect=http.client.BadStatusLine("garbage"),
        ):
            ok = client.emit(env)
        self.assertFalse(ok)
        self.assertEqual(client.failed, 1)


if __name__ == "__main__":
    unittest.main()
