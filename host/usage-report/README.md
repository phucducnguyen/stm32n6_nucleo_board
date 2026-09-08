# usagereport — weekly occupancy usage report

Turn a log of occupancy events into the **weekly USAGE REPORT** a non-technical
space manager (a café owner, a clinic front desk) actually reads.

This is the **Step 2** buyer-facing deliverable for the edge occupancy sensor:
the wedge is *the buyer pays for the report, not the board*. It is a pure
**file-in → report-out** tool — no services, no database, no network, **stdlib
only**. You hand it a JSONL log; it prints a plain-language report.

```
events.jsonl  ──▶  python -m usagereport  ──▶  weekly report (text or Markdown)
```

## What it tells a space owner

- How busy the space is — peak, average-when-busy, and **utilization**
  (what share of open hours had someone in).
- The **busiest and quietest** times of the week (and a simple hourly bar).
- An **estimated visit count** for the week.
- **Queue / line** stats and how long people **dwell**, when that data is present.
- **Open-but-empty** stretches — the "paid for, not used" insight.
- **Data health** — how much of the week the sensor was actually reporting,
  so gaps are disclosed, not silently scored as "empty".

Anything it can't compute (no dwell data, no queue, no heartbeats) is **omitted
honestly** rather than faked.

## Input format

One JSON object per line (JSONL), the schema-v0 event envelope the occupancy
bridge emits:

```json
{"v":0,"node":"n6-occ-front-counter","event":"occupancy_count","confidence":0.88,"data":{"count":3,"space":"front-counter","sv":1},"ts":"2026-06-15T13:10:00+00:00"}
```

- `ts` may be ISO-8601 (with or without a trailing `Z`) or epoch seconds.
- Understood event verbs: `occupancy_count`, `zone_occupancy`, `queue_length`,
  `dwell_sample`, `heartbeat`.
- Unknown event verbs and malformed/blank lines are **skipped and counted**
  (a one-line input-quality summary is printed to stderr), never fatal.

This tool reads the JSONL independently and does **not** import the bridge — it
is a decoupled consumer of the wire format.

## Usage

```sh
# from a file, with operating hours, to a Markdown file
python -m usagereport --input events.jsonl --open "07:00-18:00" --format md --out report.md

# from stdin to stdout (plain text, the default)
cat events.jsonl | python -m usagereport --open "07:00-18:00"
```

Key options:

| Option | Meaning | Default |
|---|---|---|
| `--input, -i` | JSONL log path, or `-` for stdin | `-` |
| `--out, -o` | output path, or `-` for stdout | `-` |
| `--format, -f` | `md` or `txt` | `txt` |
| `--open` | operating-hours window `HH:MM-HH:MM` | all day |
| `--closed` | closed weekdays, e.g. `sun` or `sat,sun` | none |
| `--tz` | UTC offset for local wall-clock, e.g. `-07:00` | `+00:00` |
| `--node` | restrict to one node id | all |
| `--space` | restrict to one space label | all (dominant) |
| `--bucket` | time resolution in minutes | `30` |
| `--dwell-long` | "long dwell" threshold (seconds) | `300` |
| `--queue-threshold` | "queue too long" threshold | `3` |

## Demo data (no hardware needed)

`tools/synth.py` generates a deterministic, realistic week of café events
(morning / lunch / afternoon peaks, busy Saturday, quiet Sunday, a queue, dwell
samples, heartbeats):

```sh
python -m tools.synth > week.jsonl
python -m usagereport --input week.jsonl --open "07:00-18:00"
```

Use `--seed`, `--weeks`, and `--start` to vary it; the same seed always produces
the same bytes (so tests are stable).

## How a few numbers are defined (honesty notes)

- **Utilization** = occupied open-hours ÷ configured open-hours. Buckets with no
  data still count as available time (an offline sensor doesn't make the space
  busier); the separate *data-health* metric discloses missing data.
- **Estimated visits** sums the positive changes in count over time (arrivals).
  It's an estimate from periodic samples, not a turnstile.
- **Open-but-empty** blocks only count stretches with a confirmed `0` reading;
  a no-data gap is reported as "unknown", never as empty.

## Layout

```
usagereport/
  events.py    # JSONL -> typed OccEvent records (tolerant parsing)
  analyze.py   # pure analytics -> WeeklySummary (no I/O, no formatting)
  render.py    # WeeklySummary -> Markdown / plain text (no I/O)
  cli.py       # argument handling and wiring
tools/synth.py # deterministic demo-data generator
tests/         # unittest suite
```

The three stages are decoupled on purpose: parsing is independent of the wire
contract, analysis is pure, and rendering never touches I/O.

## Tests

```sh
python -m unittest discover -s tests
```

Requires Python ≥ 3.10. No third-party dependencies.
