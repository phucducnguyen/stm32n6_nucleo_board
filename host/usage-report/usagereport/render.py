"""Render a :class:`~usagereport.analyze.WeeklySummary` for a human.

Two output formats, one structure: an internal ``_Report`` (headline lines,
named sections, takeaway) is built once, then formatted as plain text or
Markdown. Language is for a non-engineer who runs a café — no jargon, headline
takeaways first, honest about gaps.

No I/O here: these functions return strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .analyze import WeeklySummary, WEEKDAY_NAMES

__all__ = ["render_text", "render_markdown", "render"]

_BAR_WIDTH = 24


@dataclass
class _Section:
    title: str
    lines: list[str]
    mono: bool = False  # render as a code block in Markdown (e.g. the bar)


@dataclass
class _Report:
    title: str
    subtitle: list[str]
    headline: list[str]
    sections: list[_Section]
    takeaway: str


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #
def render(summary: WeeklySummary, fmt: str = "md") -> str:
    """Render ``summary`` as ``"md"`` (default) or ``"txt"``."""
    if fmt == "txt":
        return render_text(summary)
    if fmt == "md":
        return render_markdown(summary)
    raise ValueError(f"unknown format {fmt!r} (want 'md' or 'txt')")


def render_text(summary: WeeklySummary) -> str:
    r = _build(summary)
    width = 66
    out: list[str] = []
    out.append("=" * width)
    out.append(f"  {r.title}")
    for s in r.subtitle:
        out.append(f"  {s}")
    out.append("=" * width)
    out.append("")
    for line in r.headline:
        out.append(f"  {line}")
    out.append("")
    for sec in r.sections:
        out.append(f"  {sec.title}")
        for line in sec.lines:
            out.append(f"    {line}")
        out.append("")
    out.append("-" * width)
    for line in _wrap(r.takeaway, width - 4):
        out.append(f"  {line}")
    out.append("=" * width)
    return "\n".join(out) + "\n"


def render_markdown(summary: WeeklySummary) -> str:
    r = _build(summary)
    out: list[str] = []
    out.append(f"# {r.title}")
    out.append("")
    for s in r.subtitle:
        out.append(f"*{s}*  ")
    out.append("")
    for line in r.headline:
        out.append(f"- {line}")
    out.append("")
    for sec in r.sections:
        out.append(f"## {sec.title}")
        out.append("")
        if sec.mono:
            out.append("```")
            out.extend(sec.lines)
            out.append("```")
        else:
            for line in sec.lines:
                out.append(f"- {line}")
        out.append("")
    out.append("---")
    out.append("")
    out.append(f"**Takeaway:** {r.takeaway}")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Build the format-neutral report
# --------------------------------------------------------------------------- #
def _build(summary: WeeklySummary) -> _Report:
    title = f'SPACE USAGE REPORT — "{_pretty(summary.space)}"'
    subtitle = [
        f"{_fmt_date(summary.start)} – {_fmt_date(summary.end)}",
        f"node(s): {', '.join(summary.nodes) or 'none'}",
        _hours_line(summary),
    ]

    if summary.is_empty:
        return _Report(
            title=title,
            subtitle=subtitle,
            headline=["No occupancy was recorded for this space in this period."],
            sections=[_data_health_section(summary)] if summary.coverage else [],
            takeaway=(
                "No usable occupancy data — check that the sensor was powered and "
                "reporting, then re-run."
            ),
        )

    headline = _headline(summary)
    sections: list[_Section] = []
    sections.append(_utilization_section(summary))
    bar = _hourly_bar_section(summary)
    if bar:
        sections.append(bar)
    busy = _busiest_section(summary)
    if busy:
        sections.append(busy)
    queue = _queue_section(summary)
    if queue:
        sections.append(queue)
    dwell = _dwell_section(summary)
    if dwell:
        sections.append(dwell)
    zones = _zone_section(summary)
    if zones:
        sections.append(zones)
    sections.append(_reserved_empty_section(summary))
    if summary.coverage:
        sections.append(_data_health_section(summary))

    return _Report(
        title=title,
        subtitle=subtitle,
        headline=headline,
        sections=sections,
        takeaway=_takeaway(summary),
    )


def _headline(s: WeeklySummary) -> list[str]:
    lines: list[str] = []
    if s.busiest_hours:
        top = s.busiest_hours[0]
        lines.append(
            f"Busiest: {WEEKDAY_NAMES[top.weekday]} {_fmt_hour_range(top.hour)}, "
            f"about {_round_people(top.mean_count)} on average"
        )
    if s.peak_count is not None and s.peak_at is not None:
        lines.append(
            f"Fullest moment: {s.peak_count} "
            f"{_people(s.peak_count)} on {_fmt_datetime(s.peak_at)}"
        )
    lines.append(
        f"Open about {_pct(s.utilization)} of the time "
        f"({_fmt_hours(s.occupied_hours)} of {_fmt_hours(s.available_hours)} open hours had someone in)"
    )
    if s.avg_when_busy is not None:
        lines.append(f"When in use, typically {_round_people(s.avg_when_busy)} present")
    lines.append(f"Estimated visits this week: about {s.visits_estimate}")
    return lines


def _utilization_section(s: WeeklySummary) -> _Section:
    lines = [
        f"Someone present: {_fmt_hours(s.occupied_hours)} of {_fmt_hours(s.available_hours)} open hours  ({_pct(s.utilization)})",
    ]
    if s.avg_when_busy is not None:
        lines.append(f"Average when in use: {_round_people(s.avg_when_busy)}")
    if s.peak_count is not None and s.peak_at is not None:
        lines.append(f"Peak: {s.peak_count} {_people(s.peak_count)}  ({_fmt_datetime(s.peak_at)})")
    return _Section("HOW BUSY", lines)


def _hourly_bar_section(s: WeeklySummary) -> _Section | None:
    profile = s.hourly_profile
    present = [v for v in profile if v is not None]
    if not present:
        return None
    peak = max(present) or 1.0
    lines: list[str] = []
    for h in range(24):
        v = profile[h]
        if v is None:
            continue
        filled = int(round((v / peak) * _BAR_WIDTH)) if peak > 0 else 0
        bar = "#" * filled
        lines.append(f"{h:02d}:00  {bar:<{_BAR_WIDTH}}  {v:4.1f}")
    if not lines:
        return None
    lines.append("(average people in view, by hour of day)")
    return _Section("TYPICAL DAY (HOURLY)", lines, mono=True)


def _busiest_section(s: WeeklySummary) -> _Section | None:
    if not s.busiest_hours and not s.busiest_days:
        return None
    lines: list[str] = []
    for hs in s.busiest_hours:
        lines.append(
            f"{WEEKDAY_NAMES[hs.weekday]} {_fmt_hour_range(hs.hour)} — "
            f"avg {hs.mean_count:.1f}"
        )
    if s.busiest_days:
        top_day = s.busiest_days[0]
        quiet_day = s.busiest_days[-1]
        lines.append(
            f"Busiest day: {WEEKDAY_NAMES[top_day.weekday]} (avg {top_day.mean_count:.1f}, "
            f"peak {top_day.peak_count}); quietest: {WEEKDAY_NAMES[quiet_day.weekday]} "
            f"(avg {quiet_day.mean_count:.1f})"
        )
    if s.quietest_open_hours:
        qs = s.quietest_open_hours[0]
        lines.append(
            f"Reliable lull: {WEEKDAY_NAMES[qs.weekday]} {_fmt_hour_range(qs.hour)} "
            f"(avg {qs.mean_count:.1f})"
        )
    return _Section("BUSIEST & QUIETEST TIMES", lines)


def _queue_section(s: WeeklySummary) -> _Section | None:
    if not s.queue:
        return None
    lines: list[str] = []
    for q in s.queue.by_zone:
        peak_when = f"  ({_fmt_datetime(q.peak_at)})" if q.peak_at else ""
        lines.append(
            f"{q.zone}: avg {q.avg_length:.1f}, peak {q.peak_length}{peak_when}"
        )
        lines.append(
            f"  readings over {q.threshold}: {q.over_threshold_samples} of {q.samples}"
        )
    return _Section("QUEUE / LINE", lines)


def _dwell_section(s: WeeklySummary) -> _Section | None:
    if not s.dwell:
        return None
    lines: list[str] = []
    for d in s.dwell.by_zone:
        lines.append(
            f"{d.zone}: avg {_fmt_dur(d.avg_s)}, median {_fmt_dur(d.median_s)}, "
            f"long stays (>{_fmt_dur(s.config.dwell_long_s)}): {d.long_count}"
        )
    return _Section("HOW LONG PEOPLE STAY (DWELL)", lines)


def _zone_section(s: WeeklySummary) -> _Section | None:
    if not s.zones:
        return None
    lines = [
        f"{z.zone} ...... {_pct(z.attention_share)} of attention (avg {z.mean_count:.1f})"
        for z in s.zones.by_zone
    ]
    return _Section("ZONE ENGAGEMENT", lines)


def _reserved_empty_section(s: WeeklySummary) -> _Section:
    if s.longest_empty_open is None:
        lines = ["No long empty stretches during open hours."]
    else:
        b = s.longest_empty_open
        lines = [
            f"Longest empty open block: {_fmt_block(b.start, b.end)}  "
            f"({_fmt_minutes(b.minutes)}, nobody in)"
        ]
    return _Section("OPEN BUT EMPTY", lines)


def _data_health_section(s: WeeklySummary) -> _Section:
    c = s.coverage
    assert c is not None
    lines = [f"Sensor reporting uptime (open hours): {_pct(c.uptime)}"]
    if not c.has_heartbeat:
        lines.append("No heartbeat events — uptime is inferred from data presence only.")
    if c.largest_gap is not None and c.largest_gap.minutes > 0:
        g = c.largest_gap
        lines.append(
            f"Largest gap: {_fmt_minutes(g.minutes)} ({_fmt_block(g.start, g.end)}) "
            f"— treated as 'unknown', not empty."
        )
    else:
        lines.append("No reporting gaps during open hours.")
    return _Section("DATA HEALTH", lines)


def _takeaway(s: WeeklySummary) -> str:
    parts: list[str] = []
    if s.busiest_hours:
        top = s.busiest_hours[0]
        parts.append(
            f"{WEEKDAY_NAMES[top.weekday]} {_fmt_hour_range(top.hour)} is your busiest window"
            f" (avg {top.mean_count:.0f})"
        )
    if s.quietest_open_hours:
        qs = s.quietest_open_hours[0]
        parts.append(
            f"{WEEKDAY_NAMES[qs.weekday]} around {_fmt_hour_range(qs.hour)} is reliably quiet"
        )
    if not parts:
        return f"This space was in use about {_pct(s.utilization)} of its open hours."
    sentence = "; ".join(parts) + "."
    return sentence[0].upper() + sentence[1:]


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
def _pretty(space: str) -> str:
    return space.replace("-", " ").replace("_", " ").title()


def _people(n: int) -> str:
    return "person" if n == 1 else "people"


def _round_people(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".") + " people" if x != 1 else "1 person"


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def _fmt_hours(h: float) -> str:
    return f"{h:.1f}h"


def _fmt_date(dt: datetime) -> str:
    return dt.strftime("%a %b %d, %Y")


def _fmt_datetime(dt: datetime) -> str:
    return dt.strftime("%a %b %d, ") + _fmt_clock(dt)


def _fmt_block(start: datetime, end: datetime) -> str:
    if start.date() == end.date():
        return start.strftime("%a ") + f"{_fmt_clock(start)}–{_fmt_clock(end)}"
    return f"{_fmt_datetime(start)} → {_fmt_datetime(end)}"


def _fmt_clock(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    ampm = "am" if dt.hour < 12 else "pm"
    if dt.minute:
        return f"{h}:{dt.minute:02d}{ampm}"
    return f"{h}{ampm}"


def _fmt_hour_range(hour: int) -> str:
    start_h = hour % 12 or 12
    end_hour = (hour + 1) % 24
    end_h = end_hour % 12 or 12
    start_ampm = "am" if hour < 12 else "pm"
    end_ampm = "am" if end_hour < 12 else "pm"
    if start_ampm == end_ampm:
        return f"{start_h}–{end_h}{end_ampm}"
    return f"{start_h}{start_ampm}–{end_h}{end_ampm}"


def _fmt_dur(seconds: float) -> str:
    seconds = int(round(seconds))
    m, sec = divmod(seconds, 60)
    if m and sec:
        return f"{m}m {sec}s"
    if m:
        return f"{m}m"
    return f"{sec}s"


def _fmt_minutes(minutes: float) -> str:
    total = int(round(minutes))
    h, m = divmod(total, 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def _hours_line(s: WeeklySummary) -> str:
    oh = s.config.open_hours
    if oh.start_min == 0 and oh.end_min >= 24 * 60 and not oh.closed_days:
        return "open hours: all day, every day (no operating window set)"
    start = f"{oh.start_min // 60:02d}:{oh.start_min % 60:02d}"
    end = f"{oh.end_min // 60:02d}:{oh.end_min % 60:02d}"
    days = ""
    if oh.closed_days:
        closed = ", ".join(WEEKDAY_NAMES[d] for d in sorted(oh.closed_days))
        days = f" (closed {closed})"
    return f"open hours: {start}–{end}{days}"
