# occupancy-bridge (`occbridge`)

Host-side bridge for an **STM32N6** edge vision sensor. The board runs on-device
person detection (TinyYOLOv2 + a track-ID tracker) and streams one line per
inference frame over USART1, which shows up on the host as `/dev/ttyACM*` at
115200 8N1. This package reads that stream, debounces the noisy per-frame person
count into stable occupancy events, and emits schema-v0 event envelopes to an
event spine or appends them to a local JSONL sink file.

Stdlib-only by default. No required third-party dependencies. `pyserial` is an
optional extra; without it the serial reader degrades to reading the device as a
plain file.

## Wire protocol

```
DET <seq> <ts_ms> <count> [<id>:<cx>:<cy>:<w>:<h>:<conf> ...]
```

See [`PROTOCOL.md`](PROTOCOL.md) for the authoritative specification. Example:

```
DET 1422 38911 2 7:512:430:120:340:88 9:300:455:110:300:81
DET 1423 38944 0
```

## Modules

| Module | Responsibility | I/O |
|---|---|---|
| `occbridge/protocol.py` | Pure parser → `Frame` / `Box` dataclasses; returns `None` on garbage. | none |
| `occbridge/aggregator.py` | Pure debounce of raw counts → occupancy + heartbeat events; dwell scaffolding; injectable clock. | none |
| `occbridge/events.py` | Schema-v0 envelope construction + validation + compact JSON. | none |
| `occbridge/sources.py` | `LineSource` iterators: `SerialSource`, `FileReplaySource`, `IterableSource`. | serial / file |
| `occbridge/spine_client.py` | Fail-open HTTP POST of envelopes via `urllib` (or dry-run print). | network |
| `occbridge/sink.py` | Fail-open append of stored records to a JSONL file under `flock`. | file |
| `occbridge/verify.py` | Bring-up GO/NO-GO: pure metric/verdict `analyze()` over frames + capture helper. | none (capture is impure) |
| `occbridge/cli.py` + `__main__.py` | Wire it together; signal handling; stats summary; `verify` subcommand. | argv / signals |

The three pure layers (`protocol`, `aggregator`, `events`) carry the bulk of the
test suite.

## Event schema (v0)

```json
{"v":0,"node":"n6-occ-front-door","event":"occupancy_count","confidence":null,"data":{"count":2},"ts":"2026-06-25T12:00:00+00:00"}
```

- `occupancy_count` — `data: {count}` — emitted on a **debounced** count change.
- `heartbeat` — `data: {seq, uptime_ms, frames}` — emitted every `heartbeat_s`.

## Configuration

All site config comes from CLI flags or env vars (safe, non-identifying
defaults):

| Flag | Env | Default |
|---|---|---|
| `--node NAME` | `OCC_NODE` | `n6-occ-unknown` |
| `--spine-url URL` | `OCC_SPINE_URL` | (unset → dry-run) |
| `--sink PATH` | `OCC_SINK` | (unset; replaces the spine POST) |
| `--device PATH` | — | `/dev/ttyACM0` |
| `--replay FILE` | — | — |
| `--heartbeat-s` | — | `60` |
| `--debounce-frames` | — | `3` |

## Run

Replay the bundled sample capture in dry-run mode (prints event JSON):

```bash
python -m occbridge --replay tests/sample_capture.log --dry-run
```

Read the live serial device and POST to a spine:

```bash
OCC_NODE=n6-occ-front-door OCC_SPINE_URL=http://spine.example/ingest \
  python -m occbridge --device /dev/ttyACM0
```

Append events to a local JSONL sink instead of POSTing (`--sink` cannot be
combined with `--spine-url` or `--dry-run`):

```bash
OCC_NODE=n6-occ-front-door \
  python -m occbridge --device /dev/ttyACM0 --sink ~/.local/share/edge-kit/events.jsonl
```

Each line is one stored record: the envelope with `ts` rewritten to canonical
RFC 3339 UTC, `Z`, millisecond precision (truncated, never rounded), plus
`received_at` in the same form. Appends hold an exclusive `flock` on an
`O_APPEND` file and issue one `write()` per line, so concurrent producers never
interleave; if the file does not end in a newline (a crash mid-write), a newline
is written first so the new record is not glued onto the fragment. A reader
should skip any line that is not a complete JSON object.

Install the optional native serial backend:

```bash
pip install -e '.[serial]'
```

## Bring-up: one-command GO/NO-GO

Before wiring the board into anything, run `verify`. It captures the stream for
a few seconds (or replays a log), then prints a report + verdict and exits **0
on GO, 1 on FAIL** so it can gate a script:

```bash
# live board, capture 20s
python -m occbridge verify --device /dev/ttyACM0 --seconds 20
# hardware-free, replay a capture
python -m occbridge verify --replay tests/sample_verify.log
```

The verdict answers: does it detect people, are boxes/track-ids present, are ids
**stable** (mean lifetime above a threshold, not churning every frame), and is
the frame rate healthy — recommending **v0 count-only**, **v0.2 dwell/queue
feasible**, or **FAIL — <reason>**. The metric/verdict core (`verify.analyze`)
is a pure function over parsed frames, so it is fully unit-tested.

## Test

```bash
python -m unittest discover -s tests
```

`tests/test_sink.py` can also check the sink output against a reference writer
(`emit.py` with `validate`/`normalise`/`read_sink`): set `EDGEKIT_EMIT_PY` to its
path; without it those two tests skip and print why.
