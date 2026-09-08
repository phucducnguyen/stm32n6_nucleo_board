# STM32N6 occupancy wire protocol (source of truth)

The firmware emits ONE line per inference frame over USART1, which appears on
the host as a serial port `/dev/ttyACM*` at **115200 8N1**.

```
DET <seq> <ts_ms> <count> [<id>:<cx>:<cy>:<w>:<h>:<conf> ...]
```

- `DET` = literal line tag. Lines NOT starting with `"DET "` are board
  boot/status noise → ignore (optionally debug-log).
- `<seq>` = uint frame counter (may wrap at 32-bit).
- `<ts_ms>` = board uptime in ms (uint, from `HAL_GetTick`; wraps at 32-bit).
- `<count>` = number of person detections this frame (uint).
- then exactly `<count>` space-separated box tuples, each
  `id:cx:cy:w:h:conf` where:
  - `id` = track id (uint, `0` if tracking disabled),
  - `cx, cy, w, h` = box center/size in **PERMILLE** (integers `0..1000`),
  - `conf` = confidence **PERCENT** (integer `0..100`).
- Example: `DET 1422 38911 2 7:512:430:120:340:88 9:300:455:110:300:81`
- A zero-person frame is still emitted: `DET 1423 38944 0`

## Parsing rules (as implemented in `occbridge/protocol.py`)

- A line whose first token is not exactly `DET` returns `None` (noise).
- `<count>` MUST equal the number of box tuples present, or the line is dropped
  (`None`).
- Out-of-range coordinates (`> 1000`) or confidence (`> 100`), negative numbers,
  non-integer fields, and wrong-arity tuples all cause the line to be dropped.
- Trailing whitespace / CRLF is tolerated.
- The parser never raises; the caller counts `None` results as drops.

## Counter wrap note

`<seq>` and `<ts_ms>` are unsigned 32-bit board values and wrap. Treat them as
opaque counters; only compare via modular deltas. The aggregator's timing uses
an independent host monotonic clock, so a board `ts_ms` wrap cannot corrupt
debounce or heartbeat timing.
