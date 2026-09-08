"""usagereport — turn occupancy event logs into a plain-language weekly report.

A standalone, file-in/report-out tool. It reads a JSONL log of schema-v0
occupancy events (the shape the occupancy bridge emits) and renders the weekly
USAGE REPORT a non-technical space manager actually reads.

No services, no database, no network. Pure ``stdlib``.

Pipeline::

    events.read_events(jsonl)  ->  analyze.analyze(...)  ->  render.render_*(...)

The three stages are decoupled on purpose: parsing is independent of the wire
contract (we do NOT import the bridge), analysis is pure and formatting-free,
and rendering never touches I/O.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
