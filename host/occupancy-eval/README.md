# occupancy-eval

Host-side checks for the occupancy build. Stdlib only; reuses the DET parser in
`../occupancy-bridge`. Plan, claims and accuracy target: `docs/OCCUPANCY-PATH.md`.

| Script | Question it answers | Exit |
|---|---|---|
| `privacy_judge.py --elf <image>` | Is any image egress path linked (USB device, UVC, network, display/JPEG, storage)? Is the DET emitter present? Reads `.config` beside a Zephyr ELF. | 0 pass, 1 fail, 2 could not check |
| `uart_log_judge.py [--count-only] <log>` | Did the console carry only DET lines and short status text? | 0 / 1 / 2 |
| `count_score.py <log> <tally.csv>` | Does the device count match a hand tally per minute? | 0 meet, 1 miss, 2 bad input, 3 too little data |
| `firmware/st-people-no-screen.patch` | Adds `SCR_LIB_SCREEN_ITF=NONE` to the st-people fork (apply after the det_emit patch), so the Nucleo build links no UVC. The build applies the copy in `patches/st-people-no-screen.patch`; keep the two identical. | |

Tests: `python3 -B -m unittest discover -s tests -v`. Two tests judge real builds read-only
(`build/uvc`, `../st-people/build/Project.elf`) and skip when those are not built.
