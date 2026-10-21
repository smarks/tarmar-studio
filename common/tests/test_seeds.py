"""Tests for the shared seed parsing and rolling."""

from unittest import mock

from django.test import SimpleTestCase

from common.refusal_words import refusal_words
from common.seeds import MAX_RANDOM_SEED, InvalidSeed, parse_seed, roll_seed


class ParseSeedTest(SimpleTestCase):
    def test_blank_means_no_seed(self):
        for raw in ("", "   ", None):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_seed(raw))

    def test_whole_numbers_are_read_ignoring_whitespace(self):
        self.assertEqual(parse_seed(" 42 "), 42)
        self.assertEqual(parse_seed(7), 7)

    def test_anything_else_is_refused_with_a_showable_message(self):
        with self.assertRaises(InvalidSeed) as refusal:
            parse_seed("abc")
        self.assertEqual(
            str(refusal.exception),
            refusal_words().seeds.not_whole_number.format(text="abc"),
        )
        self.assertIsInstance(refusal.exception, ValueError)


class RollSeedTest(SimpleTestCase):
    def test_rolls_within_one_to_the_maximum(self):
        with mock.patch("common.seeds.secrets.randbelow", return_value=0):
            self.assertEqual(roll_seed(), 1)
        with mock.patch(
            "common.seeds.secrets.randbelow", return_value=MAX_RANDOM_SEED - 1
        ):
            self.assertEqual(roll_seed(), MAX_RANDOM_SEED)
