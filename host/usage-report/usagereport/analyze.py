"""Pure analytics over occupancy events — no I/O, no formatting.

Everything here is a pure function of its inputs: given a list of
:class:`~usagereport.events.OccEvent` and a configuration, it returns a
:class:`WeeklySummary` dataclass. Rendering lives entirely in ``render.py``.

Design notes / honesty:

* Occupancy samples are *instantaneous* (the count at ``ts``), emitted on a low
  duty cycle. We reconstruct usage over time by bucketing into fixed windows
  (default 30 min) and taking the busiest sample in each window.
* "Utilization" = occupied open-hours ÷ configured open-hours. Buckets with no
  data still count as available time (an absent sensor doesn't make the café
  busier); the separate *coverage* metric discloses how much data was missing.
* "Visits" is an estimate: the sum of positive changes in count between
  consecutive samples per node (i.e. arrivals). It is an estimate, not a
  turnstile, and is labelled as such downstream.
* Anything we cannot compute (no dwell data, no queue data, no heartbeats) is
  left ``None`` so the renderer can omit it honestly rather than invent zeros.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .events import (
    EVENT_DWELL_SAMPLE,
    EVENT_HEARTBEAT,
    EVENT_OCCUPANCY_COUNT,
    EVENT_QUEUE_LENGTH,
    EVENT_ZONE_OCCUPANCY,
    OccEvent,
)

__all__ = [
    "OpenHours",
    "AnalysisConfig",
    "HourStat",
    "DayStat",
    "TimeBlock",
    "DwellZoneStat",
    "DwellReport",
    "QueueZoneStat",
    "QueueReport",
    "ZoneStat",
    "ZoneReport",
    "Coverage",
    "WeeklySummary",
    "analyze",
    "parse_open_window",
    "parse_tz_offset",
    "parse_days",
    "WEEKDAY_NAMES",
]

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_DAY_ALIASES = {name.lower(): i for i, name in enumerate(WEEKDAY_NAMES)}
_DAY_ALIASES.update(
    {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
)

_MINUTES_PER_DAY = 24 * 60


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class OpenHours:
    """Operating-hours window, in local minutes-of-day, for selected weekdays.

    ``start_min``/``end_min`` are minutes since local midnight (e.g. 07:00 = 420,
    18:00 = 1080, 24:00 = 1440). ``closed_days`` is a set of weekday ints
    (Mon=0 .. Sun=6) on which the space is closed (no available hours).
    """

    start_min: int = 0
    end_min: int = _MINUTES_PER_DAY
    closed_days: frozenset[int] = frozenset()

    def is_open(self, weekday: int, minute_of_day: int) -> bool:
        if weekday in self.closed_days:
            return False
        return self.start_min <= minute_of_day < self.end_min

    @property
    def daily_open_minutes(self) -> int:
        return max(0, self.end_min - self.start_min)


@dataclass(frozen=True)
class AnalysisConfig:
    """Knobs for :func:`analyze`. Sensible non-identifying defaults."""

    open_hours: OpenHours = field(default_factory=OpenHours)
    tz_offset_minutes: int = 0          # local = UTC + this offset
    bucket_minutes: int = 30            # resolution for utilization / blocks
    dwell_long_s: int = 300             # "long dwell" threshold (engagement)
    queue_threshold: int = 3            # "queue over N" threshold
    top_n: int = 3                      # how many busiest hours/days to keep


# --------------------------------------------------------------------------- #
# Result dataclasses
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class HourStat:
    weekday: int          # Mon=0 .. Sun=6
    hour: int             # 0..23 (local)
    mean_count: float
    samples: int


@dataclass(frozen=True)
class DayStat:
    weekday: int
    mean_count: float
    peak_count: int
    samples: int


@dataclass(frozen=True)
class TimeBlock:
    start: datetime       # local, aware
    end: datetime         # local, aware
    minutes: float


@dataclass(frozen=True)
class DwellZoneStat:
    zone: str
    samples: int
    avg_s: float
    median_s: float
    long_count: int


@dataclass(frozen=True)
class DwellReport:
    overall: DwellZoneStat
    by_zone: list[DwellZoneStat]


@dataclass(frozen=True)
class QueueZoneStat:
    zone: str
    samples: int
    avg_length: float
    peak_length: int
    peak_at: datetime | None
    over_threshold_samples: int
    threshold: int


@dataclass(frozen=True)
class QueueReport:
    by_zone: list[QueueZoneStat]


@dataclass(frozen=True)
class ZoneStat:
    zone: str
    samples: int
    mean_count: float
    attention_share: float   # 0..1, share of summed zone occupancy


@dataclass(frozen=True)
class ZoneReport:
    by_zone: list[ZoneStat]


@dataclass(frozen=True)
class Coverage:
    """How much of the open-hours week the node was actually reporting."""

    uptime: float                 # 0..1 of open-hours buckets with any data
    open_buckets: int
    reporting_buckets: int
    largest_gap: TimeBlock | None
    has_heartbeat: bool


@dataclass(frozen=True)
class WeeklySummary:
    """Everything the renderer needs. Formatting-free, JSON-friendly."""

    space: str
    nodes: list[str]
    start: datetime               # range start (local, aware)
    end: datetime                 # range end (local, aware)
    config: AnalysisConfig

    # event tallies (transparency)
    event_counts: dict[str, int]

    # occupancy_count metrics (None when no occupancy data)
    has_occupancy: bool
    available_hours: float
    occupied_hours: float
    utilization: float            # 0..1
    avg_when_busy: float | None
    peak_count: int | None
    peak_at: datetime | None
    occupancy_samples: int
    visits_estimate: int
    busiest_hours: list[HourStat]
    quietest_open_hours: list[HourStat]
    busiest_days: list[DayStat]
    hourly_profile: list[float | None]   # index 0..23 -> avg count or None
    longest_empty_open: TimeBlock | None

    # optional sections (None when absent)
    dwell: DwellReport | None
    queue: QueueReport | None
    zones: ZoneReport | None
    coverage: Coverage | None

    @property
    def is_empty(self) -> bool:
        return not self.has_occupancy


# --------------------------------------------------------------------------- #
# Config parsing helpers
# --------------------------------------------------------------------------- #
def parse_open_window(text: str) -> tuple[int, int]:
    """Parse ``"07:00-18:00"`` to ``(420, 1080)`` minutes-of-day.

    Accepts ``HH:MM-HH:MM``; ``24:00`` is allowed as the end (= 1440).
    Raises :class:`ValueError` on malformed input or a non-positive window.
    """
    try:
        lo, hi = text.split("-", 1)
        start = _parse_hhmm(lo)
        end = _parse_hhmm(hi)
    except ValueError as exc:
        raise ValueError(f"bad open window {text!r} (want HH:MM-HH:MM)") from exc
    if not 0 <= start < end <= _MINUTES_PER_DAY:
        raise ValueError(f"open window must be 00:00 <= start < end <= 24:00, got {text!r}")
    return start, end


def _parse_hhmm(text: str) -> int:
    h_str, m_str = text.strip().split(":", 1)
    h, m = int(h_str), int(m_str)
    if not (0 <= h <= 24 and 0 <= m < 60) or (h == 24 and m != 0):
        raise ValueError(f"bad time {text!r}")
    return h * 60 + m


def parse_tz_offset(text: str) -> int:
    """Parse a UTC offset like ``"+00:00"``, ``"-07:00"``, ``"Z"`` to minutes."""
    s = text.strip()
    if s in ("Z", "z", "+00:00", "00:00", "0"):
        return 0
    sign = 1
    if s[0] in "+-":
        sign = -1 if s[0] == "-" else 1
        s = s[1:]
    if ":" in s:
        h_str, m_str = s.split(":", 1)
        h, m = int(h_str), int(m_str)
    else:
        h, m = int(s), 0
    return sign * (h * 60 + m)


def parse_days(text: str) -> frozenset[int]:
    """Parse ``"sun,sat"`` (or full names) to a set of weekday ints."""
    out: set[int] = set()
    for token in text.split(","):
        token = token.strip().lower()
        if not token:
            continue
        if token not in _DAY_ALIASES:
            raise ValueError(f"unknown weekday {token!r}")
        out.add(_DAY_ALIASES[token])
    return frozenset(out)


# --------------------------------------------------------------------------- #
# The analysis
# --------------------------------------------------------------------------- #
def analyze(
    events: list[OccEvent],
    config: AnalysisConfig | None = None,
    *,
    space: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> WeeklySummary:
    """Compute a :class:`WeeklySummary` over ``events``.

    ``events`` may be unsorted and may span multiple nodes/spaces. If ``space``
    is given, only events for that space are considered; otherwise all events
    are used and the dominant space label is reported. ``start``/``end`` clip
    the analysis window (aware datetimes); by default the full data range is used.
    """
    cfg = config or AnalysisConfig()
    tz = timezone(timedelta(minutes=cfg.tz_offset_minutes))

    if space is not None:
        events = [e for e in events if e.space == space]

    # Clip to the requested window (inclusive). Compare in UTC-aware space.
    if start is not None:
        events = [e for e in events if e.ts >= start]
    if end is not None:
        events = [e for e in events if e.ts <= end]

    # Localise once, keep sorted by time.
    local = sorted(((e, e.ts.astimezone(tz)) for e in events), key=lambda p: p[1])

    nodes = sorted({e.node for e, _ in local})
    space_label = space or _dominant_space(local)
    event_counts = _count_events(local)

    if not local:
        now = (start or datetime.now(timezone.utc)).astimezone(tz)
        return _empty_summary(space_label, nodes, now, now, cfg, event_counts)

    range_start = (start.astimezone(tz) if start else local[0][1])
    range_end = (end.astimezone(tz) if end else local[-1][1])

    occ = [(e, lt) for e, lt in local if e.event == EVENT_OCCUPANCY_COUNT and e.count is not None]

    if not occ:
        summary = _empty_summary(space_label, nodes, range_start, range_end, cfg, event_counts)
        return _attach_optional(summary, local, cfg, range_start)

    # --- headline occupancy stats ---
    counts: list[int] = [e.count for e, _ in occ if e.count is not None]
    peak_event, peak_lt = max(occ, key=lambda p: (p[0].count, -p[1].timestamp()))  # earliest tie
    peak_count = peak_event.count
    busy = [c for c in counts if c >= 1]
    avg_when_busy = (sum(busy) / len(busy)) if busy else 0.0
    visits = _estimate_visits(occ)

    # --- bucketed time model (utilization, empty blocks, coverage) ---
    buckets = _build_buckets(local, occ, cfg, range_start, range_end)
    available_hours, occupied_hours, util, longest_empty = _utilization(buckets, cfg)

    # --- hour-of-week / day rollups ---
    busiest_hours, quietest_hours, busiest_days, hourly = _time_rollups(occ, cfg)

    summary = WeeklySummary(
        space=space_label,
        nodes=nodes,
        start=range_start,
        end=range_end,
        config=cfg,
        event_counts=event_counts,
        has_occupancy=True,
        available_hours=available_hours,
        occupied_hours=occupied_hours,
        utilization=util,
        avg_when_busy=avg_when_busy,
        peak_count=peak_count,
        peak_at=peak_lt,
        occupancy_samples=len(occ),
        visits_estimate=visits,
        busiest_hours=busiest_hours,
        quietest_open_hours=quietest_hours,
        busiest_days=busiest_days,
        hourly_profile=hourly,
        longest_empty_open=longest_empty,
        dwell=None,
        queue=None,
        zones=None,
        coverage=None,
    )
    return _attach_optional(summary, local, cfg, range_start)


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _dominant_space(local: list) -> str:
    tally: dict[str, int] = {}
    for e, _ in local:
        tally[e.space] = tally.get(e.space, 0) + 1
    return max(tally, key=lambda k: (tally[k], k)) if tally else "(unknown)"


def _count_events(local: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e, _ in local:
        counts[e.event] = counts.get(e.event, 0) + 1
    return counts


def _empty_summary(space, nodes, start, end, cfg, event_counts) -> WeeklySummary:
    return WeeklySummary(
        space=space,
        nodes=nodes,
        start=start,
        end=end,
        config=cfg,
        event_counts=event_counts,
        has_occupancy=False,
        available_hours=0.0,
        occupied_hours=0.0,
        utilization=0.0,
        avg_when_busy=None,
        peak_count=None,
        peak_at=None,
        occupancy_samples=0,
        visits_estimate=0,
        busiest_hours=[],
        quietest_open_hours=[],
        busiest_days=[],
        hourly_profile=[None] * 24,
        longest_empty_open=None,
        dwell=None,
        queue=None,
        zones=None,
        coverage=None,
    )


def _estimate_visits(occ: list) -> int:
    """Sum positive count deltas per node (arrivals). A coarse visit estimate."""
    by_node: dict[str, list] = {}
    for e, lt in occ:
        by_node.setdefault(e.node, []).append((lt, e.count))
    total = 0
    for series in by_node.values():
        series.sort(key=lambda p: p[0])
        prev = 0
        for _, c in series:
            if c > prev:
                total += c - prev
            prev = c
    return total


def _build_buckets(local, occ, cfg, range_start, range_end):
    """Bucket the week into fixed windows aligned to local midnight.

    Returns a list of dicts: {start, weekday, minute, occ (max count or None),
    has_data (bool)} covering range_start..range_end.
    """
    step = timedelta(minutes=cfg.bucket_minutes)
    day0 = range_start.replace(hour=0, minute=0, second=0, microsecond=0)

    def idx_of(lt: datetime) -> int:
        return int((lt - day0) / step)

    occ_max: dict[int, int] = {}
    for e, lt in occ:
        i = idx_of(lt)
        if e.count is not None and (i not in occ_max or e.count > occ_max[i]):
            occ_max[i] = e.count

    data_idx: set[int] = {idx_of(lt) for _, lt in local}

    last_idx = idx_of(range_end)
    buckets = []
    for i in range(0, last_idx + 1):
        bstart = day0 + i * step
        buckets.append(
            {
                "start": bstart,
                "weekday": bstart.weekday(),
                "minute": bstart.hour * 60 + bstart.minute,
                "occ": occ_max.get(i),
                "has_data": i in data_idx,
            }
        )
    return buckets


def _utilization(buckets, cfg):
    """Compute available/occupied hours, utilization, and longest empty block."""
    bucket_h = cfg.bucket_minutes / 60.0
    open_buckets = [b for b in buckets if cfg.open_hours.is_open(b["weekday"], b["minute"])]
    available_hours = len(open_buckets) * bucket_h
    occupied = [b for b in open_buckets if (b["occ"] or 0) >= 1]
    occupied_hours = len(occupied) * bucket_h
    util = (occupied_hours / available_hours) if available_hours > 0 else 0.0

    # Longest contiguous run of open buckets that are confirmed empty
    # (a 0-count sample present). No-data buckets break the run — we won't
    # claim a space was empty when the sensor was silent.
    longest = None
    run_start = None
    run_len = 0

    def close_run(end_bucket):
        nonlocal longest
        if run_start is None or run_len == 0:
            return
        start_dt = run_start["start"]
        end_dt = end_bucket["start"] + timedelta(minutes=cfg.bucket_minutes)
        minutes = run_len * cfg.bucket_minutes
        block = TimeBlock(start=start_dt, end=end_dt, minutes=float(minutes))
        if longest is None or block.minutes > longest.minutes:
            longest = block

    prev = None
    for b in open_buckets:
        confirmed_empty = b["has_data"] and (b["occ"] or 0) == 0
        if confirmed_empty:
            if run_start is None:
                run_start = b
                run_len = 1
            else:
                run_len += 1
            prev = b
        else:
            if prev is not None:
                close_run(prev)
            run_start = None
            run_len = 0
            prev = None
    if prev is not None:
        close_run(prev)

    return available_hours, occupied_hours, util, longest


def _time_rollups(occ, cfg):
    """Busiest/quietest hour-of-week, busiest day, and 24h hourly profile."""
    hour_counts: dict[tuple[int, int], list[int]] = {}
    day_counts: dict[int, list[int]] = {}
    hod: dict[int, list[int]] = {}
    for e, lt in occ:
        c = e.count
        hour_counts.setdefault((lt.weekday(), lt.hour), []).append(c)
        day_counts.setdefault(lt.weekday(), []).append(c)
        hod.setdefault(lt.hour, []).append(c)

    hour_stats = [
        HourStat(weekday=wd, hour=h, mean_count=sum(v) / len(v), samples=len(v))
        for (wd, h), v in hour_counts.items()
    ]
    busiest = sorted(hour_stats, key=lambda s: (-s.mean_count, s.weekday, s.hour))[: cfg.top_n]

    open_hour_stats = [
        s for s in hour_stats if cfg.open_hours.is_open(s.weekday, s.hour * 60)
    ]
    quietest = sorted(open_hour_stats, key=lambda s: (s.mean_count, s.weekday, s.hour))[: cfg.top_n]

    day_stats = [
        DayStat(
            weekday=wd,
            mean_count=sum(v) / len(v),
            peak_count=max(v),
            samples=len(v),
        )
        for wd, v in day_counts.items()
    ]
    busiest_days = sorted(day_stats, key=lambda s: (-s.mean_count, s.weekday))

    hourly: list[float | None] = [None] * 24
    for h, v in hod.items():
        hourly[h] = sum(v) / len(v)

    return busiest, quietest, busiest_days, hourly


def _attach_optional(summary, local, cfg, range_start):
    """Compute dwell / queue / zone / coverage sections and attach them."""
    dwell = _dwell_report([e for e, _ in local if e.event == EVENT_DWELL_SAMPLE], cfg)
    queue = _queue_report([(e, lt) for e, lt in local if e.event == EVENT_QUEUE_LENGTH], cfg)
    zones = _zone_report([e for e, _ in local if e.event == EVENT_ZONE_OCCUPANCY])
    coverage = _coverage(local, summary, cfg, range_start)

    # dataclass is frozen — rebuild with the optional fields filled in.
    from dataclasses import replace

    return replace(summary, dwell=dwell, queue=queue, zones=zones, coverage=coverage)


def _dwell_report(dwell_events, cfg) -> DwellReport | None:
    samples = [(e.zone or "(unzoned)", e.dwell_s) for e in dwell_events if e.dwell_s is not None]
    if not samples:
        return None

    def stat(zone: str, values: list[int]) -> DwellZoneStat:
        return DwellZoneStat(
            zone=zone,
            samples=len(values),
            avg_s=sum(values) / len(values),
            median_s=float(statistics.median(values)),
            long_count=sum(1 for v in values if v > cfg.dwell_long_s),
        )

    by_zone_vals: dict[str, list[int]] = {}
    for zone, v in samples:
        by_zone_vals.setdefault(zone, []).append(v)
    by_zone = [stat(z, vs) for z, vs in sorted(by_zone_vals.items())]
    overall = stat("(all zones)", [v for _, v in samples])
    return DwellReport(overall=overall, by_zone=by_zone)


def _queue_report(queue_pairs, cfg) -> QueueReport | None:
    valid = [(e, lt) for e, lt in queue_pairs if e.length is not None]
    if not valid:
        return None
    by_zone_pairs: dict[str, list] = {}
    for e, lt in valid:
        by_zone_pairs.setdefault(e.zone or "(queue)", []).append((e.length, lt))

    stats = []
    for zone, pairs in sorted(by_zone_pairs.items()):
        lengths = [p[0] for p in pairs]
        peak_len, peak_at = max(pairs, key=lambda p: (p[0], -p[1].timestamp()))
        stats.append(
            QueueZoneStat(
                zone=zone,
                samples=len(lengths),
                avg_length=sum(lengths) / len(lengths),
                peak_length=peak_len,
                peak_at=peak_at,
                over_threshold_samples=sum(1 for x in lengths if x > cfg.queue_threshold),
                threshold=cfg.queue_threshold,
            )
        )
    return QueueReport(by_zone=stats)


def _zone_report(zone_events) -> ZoneReport | None:
    vals: dict[str, list[int]] = {}
    for e in zone_events:
        for name, count in e.zones.items():
            vals.setdefault(name, []).append(count)
    if not vals:
        return None
    means = {z: (sum(v) / len(v)) for z, v in vals.items()}
    total = sum(means.values())
    stats = [
        ZoneStat(
            zone=z,
            samples=len(vals[z]),
            mean_count=means[z],
            attention_share=(means[z] / total) if total > 0 else 0.0,
        )
        for z in vals
    ]
    stats.sort(key=lambda s: (-s.attention_share, s.zone))
    return ZoneReport(by_zone=stats)


def _coverage(local, summary, cfg, range_start) -> Coverage | None:
    """Open-hours reporting uptime + largest data gap, from any events."""
    has_heartbeat = any(e.event == EVENT_HEARTBEAT for e, _ in local)
    step = timedelta(minutes=cfg.bucket_minutes)
    day0 = range_start.replace(hour=0, minute=0, second=0, microsecond=0)
    range_end = summary.end

    def idx_of(lt: datetime) -> int:
        return int((lt - day0) / step)

    data_idx = {idx_of(lt) for _, lt in local}
    last_idx = idx_of(range_end)

    open_buckets = 0
    reporting = 0
    largest = None
    run_start_dt = None
    run_len = 0

    def close_gap(end_idx):
        nonlocal largest
        if run_start_dt is None or run_len == 0:
            return
        end_dt = day0 + (end_idx + 1) * step
        minutes = run_len * cfg.bucket_minutes
        block = TimeBlock(start=run_start_dt, end=end_dt, minutes=float(minutes))
        if largest is None or block.minutes > largest.minutes:
            largest = block

    prev_gap_idx = None
    for i in range(0, last_idx + 1):
        bstart = day0 + i * step
        if not cfg.open_hours.is_open(bstart.weekday(), bstart.hour * 60 + bstart.minute):
            continue
        open_buckets += 1
        if i in data_idx:
            reporting += 1
            if prev_gap_idx is not None:
                close_gap(prev_gap_idx)
            run_start_dt = None
            run_len = 0
            prev_gap_idx = None
        else:
            if run_start_dt is None:
                run_start_dt = bstart
                run_len = 1
            else:
                run_len += 1
            prev_gap_idx = i
    if prev_gap_idx is not None:
        close_gap(prev_gap_idx)

    if open_buckets == 0:
        return Coverage(uptime=0.0, open_buckets=0, reporting_buckets=0, largest_gap=None, has_heartbeat=has_heartbeat)
    return Coverage(
        uptime=reporting / open_buckets,
        open_buckets=open_buckets,
        reporting_buckets=reporting,
        largest_gap=largest,
        has_heartbeat=has_heartbeat,
    )
