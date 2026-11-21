"""``common.prose_list.listed_in_words``: the one list joiner (#208)."""

from django.test import SimpleTestCase

from common.prose_list import listed_in_words


class ListedInWordsTest(SimpleTestCase):
    def test_none_one_two_and_three_items(self) -> None:
        for items, expected in (
            ([], ""),
            (["Walk"], "Walk"),
            (["Walk", "Run"], "Walk and Run"),
            (["Walk", "Run", "Sprint"], "Walk, Run and Sprint"),
        ):
            with self.subTest(items=items):
                self.assertEqual(listed_in_words(items, and_word="and"), expected)

    def test_the_word_is_the_callers(self) -> None:
        self.assertEqual(
            listed_in_words(["Walk", "Run", "Sprint"], and_word="or"),
            "Walk, Run or Sprint",
        )

    def test_a_serial_comma_only_before_the_last_of_three_or_more(self) -> None:
        for items, expected in (
            (["Walk"], "Walk"),
            (["Walk", "Run"], "Walk or Run"),
            (["Walk", "Run", "Sprint"], "Walk, Run, or Sprint"),
        ):
            with self.subTest(items=items):
                self.assertEqual(
                    listed_in_words(items, and_word="or", serial_comma=True), expected
                )
