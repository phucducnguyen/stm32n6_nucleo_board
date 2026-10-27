#!/usr/bin/env python3
"""Privacy judge for the STM32N6 occupancy build.

Claim under test: "no image bytes can leave the MCU in the occupancy build".
The judge does not trust the source tree or a README. It inspects what was
actually linked (the ELF symbol table, via `nm`) and, for a Zephyr build, the
resolved Kconfig (`.config`). Every check is named so a failure says which
egress path is still compiled in.

Static checks (ELF symbols; only defined symbols count, so weak default IRQ
aliases such as `ETH1_IRQHandler` in the vector table never trip a check):

  P1 no-usb-device    no USB device controller / stack is linked
                      (HAL_PCD_*, Zephyr usbd_* / udc_*, USBX ux_device_* / _ux_dcd*)
  P2 no-uvc           no USB video class (UVCL_*, uvc_*, *_uvc_*)
  P3 no-network       no network or Ethernet transmit path
                      (net_if_*, net_context_*, ethernet_send, eth_stm32_*, HAL_ETH_Transmit*)
  P4 no-pixel-out     no display, JPEG encoder or pixel-pushing screen driver
                      (HAL_LTDC_*, SCRL_*, ili9341*, HAL_JPEG_*, display_write)
  P5 no-storage       no filesystem or SD card write path (f_write, fs_write, HAL_SD_*)
  P6 det-emitter      POSITIVE check: DetEmit_Frame is linked, so an unrelated
                      clean image (hello world) cannot pass as the occupancy build

Kconfig checks (only when a Zephyr `.config` is given or found next to the ELF):

  K1 CONFIG_UDC_DRIVER, CONFIG_USB_DEVICE_STACK*, CONFIG_USBD_VIDEO_CLASS,
     CONFIG_NETWORKING, CONFIG_DISPLAY, CONFIG_FILE_SYSTEM, CONFIG_SHELL are not =y
     (the shell is an interactive command channel on the console UART).

The camera input path (CONFIG_VIDEO, DCMIPP, the sensor driver) is deliberately
NOT banned: the sensor needs it, and it moves pixels into the MCU, not out.

What this does not prove (stated in docs/OCCUPANCY-PATH.md): it cannot see a
deliberate leak through the console UART (`printf` of a frame buffer). That is
what `uart_log_judge.py` checks at runtime, and the SWD debug port is a separate
claim that needs the board.

Usage:
  privacy_judge.py --elf build/<dir>/zephyr/zephyr.elf      # .config found beside it
  privacy_judge.py --elf ../st-people/build/Project.elf     # ST Makefile build, no .config
  privacy_judge.py --symbols syms.txt [--config .config]    # nm text, for tests
Exit 0 = every claim holds; 1 = at least one claim fails; 2 = could not check.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

# nm types that mean "defined in this image". U (undefined), w/W/v/V (weak),
# A (absolute; Zephyr exports every Kconfig value as an A symbol) are ignored.
_DEFINED_TYPES = set("TtDdBbRrGgSs")

SYMBOL_CHECKS: list[tuple[str, str, re.Pattern[str]]] = [
    ("P1", "no-usb-device", re.compile(
        r"^(HAL_PCD_|HAL_PCDEx_|usbd_|udc_|ux_device|_ux_device|_ux_dcd|ux_dcd)")),
    ("P2", "no-uvc", re.compile(r"(^UVCL_|^uvc_|_uvc_|^update_uvc)", re.IGNORECASE)),
    ("P3", "no-network", re.compile(
        r"^(net_if_|net_context_|net_send|ethernet_send|eth_stm32_|HAL_ETH_Transmit)")),
    ("P4", "no-pixel-out", re.compile(
        r"^(HAL_LTDC_|SCRL_|ili9341|ILI9341|HAL_JPEG_|display_write)")),
    ("P5", "no-storage", re.compile(r"^(f_write|fs_write|HAL_SD_|disk_access_write)")),
]

POSITIVE_SYMBOL = ("P6", "det-emitter", "DetEmit_Frame")

KCONFIG_BANNED = re.compile(
    r"^(CONFIG_UDC_DRIVER|CONFIG_USB_DEVICE_STACK\w*|CONFIG_USBD_VIDEO_CLASS"
    r"|CONFIG_NETWORKING|CONFIG_DISPLAY|CONFIG_FILE_SYSTEM|CONFIG_SHELL)=y$")


def parse_nm(text: str) -> set[str]:
    """Return defined symbol names from `nm` output lines `[addr] TYPE name`."""
    names: set[str] = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 3:
            _addr, typ, name = parts
        elif len(parts) == 2:
            typ, name = parts
        else:
            continue
        if len(typ) == 1 and typ in _DEFINED_TYPES:
            names.add(name)
    return names


def judge(symbols: set[str], config_text: str | None) -> list[dict]:
    """Run every claim; return one result dict per claim."""
    results = []
    for cid, name, pat in SYMBOL_CHECKS:
        hits = sorted(s for s in symbols if pat.search(s))
        results.append({"id": cid, "claim": name, "pass": not hits,
                        "evidence": hits[:8], "hits": len(hits)})
    pid, pname, psym = POSITIVE_SYMBOL
    present = psym in symbols
    results.append({"id": pid, "claim": pname, "pass": present,
                    "evidence": [psym] if present else [f"{psym} not linked"],
                    "hits": int(present)})
    if config_text is not None:
        bad = sorted(l.strip() for l in config_text.splitlines()
                     if KCONFIG_BANNED.match(l.strip()))
        results.append({"id": "K1", "claim": "kconfig-no-egress", "pass": not bad,
                        "evidence": bad[:8], "hits": len(bad)})
    return results


def _run_nm(elf: str) -> str:
    tool = shutil.which("arm-none-eabi-nm") or shutil.which("nm")
    if tool is None:
        raise RuntimeError("no nm tool on PATH")
    out = subprocess.run([tool, elf], capture_output=True, text=True, check=False)
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError(f"nm failed on {elf}: {out.stderr.strip()[:200]}")
    return out.stdout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--elf", help="linked firmware ELF (read only)")
    src.add_argument("--symbols", help="nm text output instead of an ELF")
    ap.add_argument("--config", help="Zephyr .config (default: beside the ELF, if present)")
    ap.add_argument("--json", action="store_true", help="print JSON results")
    args = ap.parse_args(argv)

    try:
        if args.elf:
            if not os.path.isfile(args.elf):
                print(f"COULD-NOT-CHECK: no ELF at {args.elf}", file=sys.stderr)
                return 2
            sym_text = _run_nm(args.elf)
        else:
            with open(args.symbols, encoding="utf-8") as f:
                sym_text = f.read()
    except (OSError, RuntimeError) as exc:
        print(f"COULD-NOT-CHECK: {exc}", file=sys.stderr)
        return 2

    symbols = parse_nm(sym_text)
    if not symbols:
        print("COULD-NOT-CHECK: no defined symbols parsed", file=sys.stderr)
        return 2

    cfg_path = args.config
    if cfg_path is None and args.elf:
        cand = os.path.join(os.path.dirname(args.elf), ".config")
        cfg_path = cand if os.path.isfile(cand) else None
    config_text = None
    if cfg_path:
        with open(cfg_path, encoding="utf-8") as f:
            config_text = f.read()

    results = judge(symbols, config_text)
    ok = all(r["pass"] for r in results)
    if args.json:
        print(json.dumps({"target": args.elf or args.symbols, "pass": ok,
                          "results": results}, indent=2))
    else:
        print(f"target: {args.elf or args.symbols}  ({len(symbols)} defined symbols, "
              f"kconfig: {'yes' if config_text is not None else 'no'})")
        for r in results:
            mark = "PASS" if r["pass"] else "FAIL"
            ev = "" if r["pass"] and r["id"] != "P6" else "  " + ", ".join(r["evidence"])
            print(f"  {mark} {r['id']} {r['claim']}{ev}")
        print("VERDICT: " + ("PASS no image egress path linked" if ok
                             else "FAIL image egress path or emitter check failed"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
