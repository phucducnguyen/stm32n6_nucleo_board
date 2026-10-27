"""Tests for the occupancy privacy judges and the count scorer (stdlib unittest).

Run from host/occupancy-eval:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
REPO = os.path.normpath(os.path.join(PKG, os.pardir, os.pardir))
sys.path.insert(0, PKG)

import count_score  # noqa: E402
import privacy_judge  # noqa: E402
import uart_log_judge  # noqa: E402

# A clean occupancy image: camera input path + the emitter, nothing that exports.
CLEAN_SYMS = """\
34002855 T DetEmit_Frame
34003175 T HAL_DCMIPP_Init
34021561 W ETH1_IRQHandler
34021562 W OTG1_IRQHandler
340100aa T HAL_UART_Transmit
00000001 A CONFIG_DT_HAS_ZEPHYR_UVC_DEVICE_ENABLED
         U HAL_PCD_Init
34000010 T main
"""

CLEAN_CONFIG = "CONFIG_VIDEO=y\nCONFIG_VIDEO_STM32_DCMIPP=y\nCONFIG_UART_CONSOLE=y\n# CONFIG_SHELL is not set\n"


def _run(fn, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fn(argv)
    return rc, out.getvalue() + err.getvalue()


def _write(tmp, name, text, mode="w"):
    p = os.path.join(tmp, name)
    with open(p, mode) as f:
        f.write(text)
    return p


class PrivacyJudgeSynthetic(unittest.TestCase):
    def results(self, syms, cfg=None):
        return {r["id"]: r for r in privacy_judge.judge(privacy_judge.parse_nm(syms), cfg)}

    def test_clean_image_passes_every_claim(self):
        r = self.results(CLEAN_SYMS, CLEAN_CONFIG)
        self.assertTrue(all(x["pass"] for x in r.values()), r)

    def test_weak_undefined_and_absolute_symbols_never_count(self):
        names = privacy_judge.parse_nm(CLEAN_SYMS)
        self.assertNotIn("ETH1_IRQHandler", names)
        self.assertNotIn("HAL_PCD_Init", names)
        self.assertNotIn("CONFIG_DT_HAS_ZEPHYR_UVC_DEVICE_ENABLED", names)

    def test_each_egress_family_fails_its_own_claim(self):
        cases = {
            "P1": "3400ed1d T HAL_PCD_Init",
            "P2": "340204c1 T UVCL_Init",
            "P3": "34193bb5 T eth_stm32_hal_init",
            "P4": "3401cf35 T HAL_LTDC_Init",
            "P5": "34000100 T f_write",
        }
        for cid, line in cases.items():
            with self.subTest(cid):
                r = self.results(CLEAN_SYMS + line + "\n")
                self.assertFalse(r[cid]["pass"])
                others = [k for k, v in r.items() if k != cid and not v["pass"]]
                self.assertEqual(others, [], f"{line} tripped {others}")

    def test_spi_panel_backend_fails_pixel_out(self):
        r = self.results(CLEAN_SYMS + "34010000 T ili9341_Init\n")
        self.assertFalse(r["P4"]["pass"])

    def test_screen_api_stubs_alone_pass(self):
        r = self.results(CLEAN_SYMS + "3401cf35 T SCRL_Init\n3401d1c9 T SCRL_ReloadLayer\n")
        self.assertTrue(all(x["pass"] for x in r.values()), r)

    def test_lowercase_uvc_sample_symbols_fail(self):
        r = self.results(CLEAN_SYMS + "34181145 t uvc_assign_desc\n")
        self.assertFalse(r["P2"]["pass"])

    def test_missing_emitter_fails(self):
        r = self.results(CLEAN_SYMS.replace("DetEmit_Frame", "OtherFn"))
        self.assertFalse(r["P6"]["pass"])

    def test_kconfig_usb_video_fails(self):
        r = self.results(CLEAN_SYMS, CLEAN_CONFIG + "CONFIG_USBD_VIDEO_CLASS=y\n")
        self.assertFalse(r["K1"]["pass"])

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = _write(tmp, "good.txt", CLEAN_SYMS)
            bad = _write(tmp, "bad.txt", CLEAN_SYMS + "340204c1 T UVCL_Init\n")
            self.assertEqual(_run(privacy_judge.main, ["--symbols", good])[0], 0)
            self.assertEqual(_run(privacy_judge.main, ["--symbols", bad])[0], 1)
            self.assertEqual(_run(privacy_judge.main, ["--elf", os.path.join(tmp, "nope.elf")])[0], 2)
            empty = _write(tmp, "empty.txt", "")
            self.assertEqual(_run(privacy_judge.main, ["--symbols", empty])[0], 2)


WEBCAM_ELF = os.path.join(REPO, "build", "uvc", "zephyr", "zephyr.elf")
PEOPLE_ELF = os.path.join(REPO, os.pardir, "st-people", "build", "Project.elf")


class PrivacyJudgeRealBuilds(unittest.TestCase):
    """Known-bads on real linked images (read only). Skipped when not built."""

    @unittest.skipUnless(os.path.isfile(WEBCAM_ELF), "build/uvc not built")
    def test_webcam_build_fails(self):
        rc, out = _run(privacy_judge.main, ["--elf", WEBCAM_ELF])
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL P2 no-uvc", out)
        self.assertIn("FAIL K1", out)

    @unittest.skipUnless(os.path.isfile(PEOPLE_ELF), "st-people not built")
    def test_current_people_detect_build_fails_on_uvc(self):
        # The det_emit build still streams video: the Nucleo Makefile forces
        # SCR_LIB_SCREEN_ITF=UVCL. This is the gap docs/OCCUPANCY-PATH.md stage 1 closes.
        rc, out = _run(privacy_judge.main, ["--elf", PEOPLE_ELF])
        self.assertEqual(rc, 1, out)
        self.assertIn("PASS P6 det-emitter", out)
        self.assertIn("FAIL P2 no-uvc", out)


SAMPLE_LOG = os.path.join(REPO, "host", "occupancy-bridge", "tests", "sample_capture.log")


class UartLogJudge(unittest.TestCase):
    def j(self, data: bytes):
        return uart_log_judge.judge_bytes(data)

    def test_clean_log_passes(self):
        ok, f = self.j(b"[BOOT] fw v0.3\r\nDET 1 10 0\r\nDET 2 43 1 7:512:430:120:340:88\r\n")
        self.assertTrue(ok, f)

    @unittest.skipUnless(os.path.isfile(SAMPLE_LOG), "bridge sample missing")
    def test_bridge_sample_capture_passes(self):
        with open(SAMPLE_LOG, "rb") as fh:
            ok, f = self.j(fh.read())
        self.assertTrue(ok, f)

    def test_host_stamped_lines_pass(self):
        ok, f = self.j(b"2026-10-27T08:15:00.4 DET 1 10 0\n")
        self.assertTrue(ok, f)

    def test_known_bad_hex_pixel_dump_fails(self):
        dump = b"FRAME " + b"8a7f3c" * 40 + b"\n"
        ok, f = self.j(b"DET 1 10 0\n" + dump)
        self.assertFalse(ok)
        self.assertTrue(any(x.startswith("U5") for x in f), f)

    def test_binary_bytes_fail(self):
        ok, f = self.j(b"DET 1 10 0\n\x00\xff\x10\n")
        self.assertFalse(ok)
        self.assertTrue(any(x.startswith("U1") for x in f))

    def test_malformed_det_fails(self):
        ok, f = self.j(b"DET 1 10 2 7:512:430:120:340:88\n")
        self.assertFalse(ok)
        self.assertTrue(any(x.startswith("U2") for x in f))

    def test_spaced_decimal_dump_breaks_noise_budget(self):
        row = b" ".join(b"%d" % (i % 256) for i in range(40)) + b"\n"   # 140-ish bytes, no blob run
        ok, f = self.j(b"DET 1 10 0\n" + row * 60)
        self.assertFalse(ok)
        self.assertTrue(any(x.startswith("U6") for x in f), f)

    def test_count_only_mode_rejects_box_tuples(self):
        data = b"DET 1 10 0\nDET 2 43 1 7:512:430:120:340:88\n"
        self.assertTrue(self.j(data)[0])
        ok, f = uart_log_judge.judge_bytes(data, count_only=True)
        self.assertFalse(ok)
        self.assertTrue(any(x.startswith("U8") for x in f), f)
        self.assertTrue(uart_log_judge.judge_bytes(b"DET 1 10 0\nDET 2 43 0\n", count_only=True)[0])

    def test_empty_log_fails(self):
        ok, f = self.j(b"[BOOT] hi\n")
        self.assertFalse(ok)


def _iso(dt):
    return dt.isoformat(timespec="milliseconds")


T0 = datetime(2026, 10, 27, 8, 0, 0)


def _truth(counts):
    return "minute,count\n" + "".join(
        f"{(T0 + timedelta(minutes=i)).isoformat(timespec='minutes')},{c}\n" for i, c in enumerate(counts))


def _det_log(counts, fps=2, noise=None):
    """Host-stamped DET log: `fps` frames per second for each minute's count."""
    lines, seq = [], 0
    for i, c in enumerate(counts):
        for k in range(60 * fps):
            t = T0 + timedelta(minutes=i, seconds=-30 + k / fps)
            cc = c if noise is None else noise(i, k, c)
            boxes = " ".join(f"{j + 1}:500:500:100:300:0" for j in range(cc))
            lines.append(f"{_iso(t)} DET {seq} {seq * 500} {cc}" + (f" {boxes}" if boxes else ""))
            seq += 1
    return "\n".join(lines) + "\n"


# 75 minutes: an empty start, a rush (2..6), a lull.
TRUTH = [0] * 10 + [2, 3, 4, 5, 6, 6, 5, 4, 3, 2] * 4 + [1, 1, 0, 0, 1] * 5


class CountScore(unittest.TestCase):
    def run_score(self, det_text, truth_text, *extra):
        with tempfile.TemporaryDirectory() as tmp:
            d = _write(tmp, "det.log", det_text)
            t = _write(tmp, "truth.csv", truth_text)
            return _run(count_score.main, [d, t, *extra])

    def test_perfect_device_meets_target(self):
        rc, out = self.run_score(_det_log(TRUTH), _truth(TRUTH))
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: MEET", out)

    def test_single_frame_blips_are_absorbed_by_the_median(self):
        noisy = _det_log(TRUTH, noise=lambda i, k, c: c + 3 if k % 7 == 0 else c)
        rc, out = self.run_score(noisy, _truth(TRUTH))
        self.assertEqual(rc, 0, out)

    def test_known_bad_always_zero_misses(self):
        rc, out = self.run_score(_det_log([0] * len(TRUTH)), _truth(TRUTH))
        self.assertEqual(rc, 1, out)
        self.assertIn("MISS mae", out)
        self.assertIn("MISS occupied_acc", out)

    def test_undercount_by_one_when_busy_misses_exact_and_wape(self):
        under = [max(0, c - 1) if c >= 3 else c for c in TRUTH]
        rc, out = self.run_score(_det_log(under), _truth(TRUTH))
        self.assertEqual(rc, 1, out)
        self.assertIn("MISS exact", out)

    def test_gaps_count_against_coverage(self):
        log = _det_log(TRUTH[:60])   # device silent for the last 15 labelled minutes
        rc, out = self.run_score(log, _truth(TRUTH))
        self.assertEqual(rc, 1, out)
        self.assertIn("MISS coverage", out)

    def test_small_dataset_is_insufficient_not_a_pass(self):
        rc, out = self.run_score(_det_log(TRUTH[:30]), _truth(TRUTH[:30]))
        self.assertEqual(rc, 3, out)
        self.assertIn("INSUFFICIENT", out)

    def test_raw_board_log_with_anchor_and_ts_wrap(self):
        counts = TRUTH
        lines, seq = [], 0
        start_ts = (1 << 32) - 120_000          # wraps two minutes in
        for i, c in enumerate(counts):
            for k in range(60):
                ms = int(((i * 60) - 30 + k) * 1000)
                ts = (start_ts + ms) % (1 << 32)
                boxes = " ".join(f"{j + 1}:500:500:100:300:0" for j in range(c))
                lines.append(f"DET {seq} {ts} {c}" + (f" {boxes}" if boxes else ""))
                seq += 1
        first_ts = (start_ts - 30_000) % (1 << 32)
        anchor = f"{first_ts}={(T0 - timedelta(seconds=30)).isoformat()}"
        rc, out = self.run_score("[BOOT] x\n" + "\n".join(lines) + "\n", _truth(counts), "--anchor", anchor)
        self.assertEqual(rc, 0, out)

    def test_change_plus_keepalive_stream_like_the_firmware(self):
        # det_emit.c emits on a count change, else once per DET_EMIT_KEEPALIVE_MS (1 s).
        lines, seq, last = [], 0, None
        start = T0 - timedelta(seconds=30)
        per_s = [c for c in TRUTH for _ in range(60)]
        for sec, c in enumerate(per_s):
            for sub in range(15):                  # 15 fps inference
                t = start + timedelta(seconds=sec + sub / 15)
                if c != last or sub == 0:
                    boxes = " ".join(f"{j + 1}:500:500:100:300:0" for j in range(c))
                    lines.append(f"{_iso(t)} DET {seq} {seq} {c}" + (f" {boxes}" if boxes else ""))
                    seq += 1
                    last = c
        rc, out = self.run_score("\n".join(lines) + "\n", _truth(TRUTH))
        self.assertEqual(rc, 0, out)

    def test_silent_device_is_missing_not_zero(self):
        # One line, then silence: held count must go stale, not read as an empty room.
        frames = [(T0.timestamp() - 60, 0)]
        truth = [(T0.timestamp(), 0)]
        r = count_score.score(frames, truth)
        self.assertEqual(r["minutes_scored"], 0)

    def test_raw_log_without_anchor_is_bad_input(self):
        rc, out = self.run_score("DET 1 10 0\n", _truth([0]))
        self.assertEqual(rc, 2, out)

    def test_metric_values(self):
        frames = count_score.load_frames(_det_log([1, 2, 4]).splitlines(), None)
        truth = [((T0 + timedelta(minutes=i)).timestamp(), c) for i, c in enumerate([1, 3, 4])]
        r = count_score.score(frames, truth)
        self.assertAlmostEqual(r["mae"], 1 / 3)
        self.assertAlmostEqual(r["exact"], 2 / 3)
        self.assertAlmostEqual(r["wape"], 1 / 8)
        self.assertAlmostEqual(r["bias"], -1 / 3)
        self.assertEqual(r["peak_offset_min"], 0.0)
        self.assertEqual(r["peak_shortfall"], 0)

    def test_device_peak_in_a_quiet_minute_misses(self):
        dev = list(TRUTH)
        dev[70] = 9                      # phantom crowd in the lull
        rc, out = self.run_score(_det_log(dev), _truth(TRUTH))
        self.assertEqual(rc, 1, out)
        self.assertIn("MISS peak_shortfall", out)


if __name__ == "__main__":
    unittest.main()
