"""Emit event envelopes to the event spine over HTTP (stdlib only).

:class:`SpineClient` POSTs compact JSON via :mod:`urllib.request`. It is
deliberately fail-open: a network error is logged and counted, never raised, so
a spine outage can't crash the bridge. With ``dry_run=True`` or no URL it prints
the JSON to stdout instead of POSTing — the safe default.
"""

from __future__ import annotations

import http.client
import json
import logging
import urllib.error
import urllib.request
from typing import Any

from .events import to_json

__all__ = ["SpineClient"]

log = logging.getLogger("occbridge.spine")


class SpineClient:
    """Best-effort sink for event envelopes.

    Args:
        url: spine endpoint. ``None``/empty forces dry-run printing.
        dry_run: if true, print JSON to stdout instead of POSTing.
        timeout: per-request timeout in seconds.
    """

    def __init__(
        self,
        url: str | None = None,
        *,
        dry_run: bool = False,
        timeout: float = 5.0,
    ) -> None:
        self._url = url or None
        self._dry_run = dry_run or self._url is None
        self._timeout = timeout
        self.sent = 0
        self.failed = 0

    @property
    def dry_run(self) -> bool:
        """True when this client prints instead of POSTing."""
        return self._dry_run

    def emit(self, envelope: dict[str, Any]) -> bool:
        """Send one envelope. Returns True on success (or dry-run print).

        Never raises: validation/serialization/network errors are logged,
        counted in :attr:`failed`, and reported as ``False``.
        """
        try:
            payload = to_json(envelope)
        except ValueError as exc:
            self.failed += 1
            log.error("dropping malformed envelope: %s", exc)
            return False

        if self._dry_run:
            print(payload)
            self.sent += 1
            return True

        return self._post(payload)

    def _post(self, payload: str) -> bool:
        data = payload.encode("utf-8")
        req = urllib.request.Request(
            self._url,  # type: ignore[arg-type]  # non-None in non-dry-run
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
            if 200 <= int(status) < 300:
                self.sent += 1
                return True
            self.failed += 1
            log.warning("spine returned HTTP %s", status)
            return False
        except (
            urllib.error.URLError,
            http.client.HTTPException,  # e.g. BadStatusLine — NOT an OSError
            OSError,
            ValueError,
        ) as exc:
            # Fail-open: spine down or misbehaving must not crash the bridge.
            self.failed += 1
            log.warning("spine POST failed (fail-open): %s", exc)
            return False

    def stats(self) -> dict[str, int]:
        """Return ``{"sent", "failed"}`` counters."""
        return {"sent": self.sent, "failed": self.failed}
