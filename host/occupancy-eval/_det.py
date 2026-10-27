"""Shared access to the DET parser in host/occupancy-bridge (one parser, not two)."""

from __future__ import annotations

import os
import sys

_BRIDGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "occupancy-bridge")
if _BRIDGE not in sys.path:
    sys.path.insert(0, os.path.normpath(_BRIDGE))

from occbridge.protocol import Frame, parse_line  # noqa: E402

__all__ = ["Frame", "parse_line"]
