"""Tests for the pure wire-protocol parser."""

import unittest

from occbridge.protocol import Box, Frame, parse_line


class TestParseLine(unittest.TestCase):
    def test_valid_multi_box(self):
        line = "DET 1422 38911 2 7:512:430:120:340:88 9:300:455:110:300:81"
        frame = parse_line(line)
        self.assertEqual(
            frame,
            Frame(
                seq=1422,
                ts_ms=38911,
                count=2,
                boxes=(
                    Box(7, 512, 430, 120, 340, 88),
                    Box(9, 300, 455, 110, 300, 81),
                ),
            ),
        )

    def test_zero_person_frame(self):
        frame = parse_line("DET 1423 38944 0")
        self.assertIsNotNone(frame)
        self.assertEqual(frame.count, 0)
        self.assertEqual(frame.boxes, ())

    def test_single_box_id_zero_tracking_disabled(self):
        frame = parse_line("DET 5 100 1 0:500:500:100:100:75")
        self.assertEqual(frame.boxes[0].id, 0)

    def test_trailing_whitespace_tolerated(self):
        frame = parse_line("DET 1 2 1 7:1:1:1:1:1   \r\n")
        self.assertIsNotNone(frame)
        self.assertEqual(frame.count, 1)

    def test_wrapped_seq_and_ts_accepted(self):
        # 32-bit max-ish values must parse fine (counters wrap).
        frame = parse_line("DET 4294967295 4294967290 0")
        self.assertEqual(frame.seq, 4294967295)
        self.assertEqual(frame.ts_ms, 4294967290)

    def test_non_det_noise_returns_none(self):
        self.assertIsNone(parse_line("[BOOT] STM32N6 occupancy fw"))
        self.assertIsNone(parse_line("INFO camera ok"))
        self.assertIsNone(parse_line("DETX 1 2 0"))  # tag must be exactly DET

    def test_empty_and_none(self):
        self.assertIsNone(parse_line(""))
        self.assertIsNone(parse_line("   "))
        self.assertIsNone(parse_line(None))

    def test_count_mismatch_too_few(self):
        self.assertIsNone(parse_line("DET 1 2 2 7:1:1:1:1:1"))

    def test_count_mismatch_too_many(self):
        self.assertIsNone(parse_line("DET 1 2 0 7:1:1:1:1:1"))

    def test_malformed_non_integer_fields(self):
        self.assertIsNone(parse_line("DET x 2 0"))
        self.assertIsNone(parse_line("DET 1 y 0"))
        self.assertIsNone(parse_line("DET 1 2 z"))

    def test_malformed_box_wrong_arity(self):
        self.assertIsNone(parse_line("DET 1 2 1 7:1:1:1:1"))  # 5 fields
        self.assertIsNone(parse_line("DET 1 2 1 7:1:1:1:1:1:1"))  # 7 fields

    def test_box_field_out_of_range(self):
        # permille coord > 1000
        self.assertIsNone(parse_line("DET 1 2 1 7:1001:1:1:1:1"))
        # confidence percent > 100
        self.assertIsNone(parse_line("DET 1 2 1 7:1:1:1:1:101"))

    def test_negative_numbers_rejected(self):
        self.assertIsNone(parse_line("DET -1 2 0"))
        self.assertIsNone(parse_line("DET 1 2 1 7:-1:1:1:1:1"))

    def test_too_short(self):
        self.assertIsNone(parse_line("DET 1 2"))
        self.assertIsNone(parse_line("DET"))

    def test_non_ascii_digits_rejected(self):
        # str.isdigit() is True for these, but the protocol is ASCII-only.
        self.assertIsNone(parse_line("DET １ 2 0"))   # full-width '1'
        self.assertIsNone(parse_line("DET 1 2 ²"))   # superscript '2'
        self.assertIsNone(parse_line("DET 1 2 1 7:５:1:1:1:1"))

    def test_huge_count_does_not_allocate_or_raise(self):
        # A bogus huge count with no tuples must simply drop (no MemoryError).
        self.assertIsNone(parse_line("DET 1 2 999999999999999999999"))


if __name__ == "__main__":
    unittest.main()
