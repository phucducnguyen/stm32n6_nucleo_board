# st-people UART detection emitter (det_emit)

Patch: `st-people-uart-det-emit.patch` — ADD-only change to the
x-cube-n6-ai-people-detection-tracking fork (NUCLEO-N657X0-Q).

**What it does:** emits one line per processed inference frame on the existing
console UART (USART1 / ST-Link VCP, 115200 8N1) via the project's existing
`printf` redirection — no new UART init. A host can parse structured occupancy.

**Wire protocol (integers only, no %f):**
`DET <seq> <ts_ms> <count> [<id>:<cx>:<cy>:<w>:<h>:<conf> ...]`
- `seq` uint32 frame counter; `ts_ms` = `HAL_GetTick()`; `count` = tuples emitted.
- `cx,cy,w,h` permille 0..1000; `conf` percent 0..100. Tracking on → real `id`,
  `conf=0` (tracked boxes carry no confidence). Tracking off → `id=0`, real conf.
- Zero detections still emits a header line. Tuples capped at 16 to bound TX.

**Files:** new `Src/det_emit.c` + `Inc/det_emit.h`; `Src/app.c` (1 include, moved
`tbox_info` typedef into the header, 1 call after `disp.lock` release); `Makefile`
(+`Src/det_emit.c`, since sources are listed explicitly, not globbed).

**Toggle:** `APP_DET_EMIT` in `det_emit.h` (default `1`); set `0` → emitter and
call site compile to a no-op.

**Status: UNBUILT / UNTESTED — pending arm-none-eabi toolchain + flash.**
