"""ThinkingFilter's handling of <think>...</think> tags split across tokens
(issue #151).

The filter used to check each token in isolation for "<think>" / "</think>".
A tag split by the streaming layer -- "<thi" then "nk>" -- never matched
either half, so the thinking text between them leaked to the client. And a
token containing "</think>" plus trailing visible text ("</think> tail") was
treated as pure tag, dropping " tail". These tests pin the fix: minimal
tag-prefix buffering across feed() calls, with the tag-plus-trailing-text
case and the streaming feel (no unbounded buffering) both preserved.
"""

import time

from django.test import TestCase

from common.services.sse import ThinkingFilter, strip_thinking


class ThinkingFilterTest(TestCase):
    def _feed_all(self, filt: ThinkingFilter, tokens: list[str]) -> str:
        """Feed each token in turn and concatenate whatever comes back."""
        return "".join(visible for t in tokens if (visible := filt.feed(t)) is not None)

    def test_passthrough_when_no_tags(self):
        """Ordinary tokens are returned unchanged and immediately."""
        filt = ThinkingFilter()
        for token in ["hello ", "world"]:
            self.assertEqual(filt.feed(token), token)

    def test_whole_tag_in_a_single_token(self):
        """Baseline: tags that already arrive intact are suppressed."""
        filt = ThinkingFilter()
        self.assertEqual(
            self._feed_all(
                filt, ["Visible1 ", "<think>", "secret", "</think>", "Visible2"]
            ),
            "Visible1 Visible2",
        )

    def test_open_tag_split_across_tokens(self):
        """ "<thi" + "nk>" must not leak the thinking text that follows."""
        filt = ThinkingFilter()
        visible = self._feed_all(
            filt, ["before ", "<thi", "nk>", "secret reasoning", "</think>", " after"]
        )
        self.assertEqual(visible, "before  after")
        self.assertNotIn("secret", visible)

    def test_close_tag_split_across_tokens_with_trailing_text(self):
        """ "...</thi" + "nk> tail" must drop the reasoning but keep " tail"."""
        filt = ThinkingFilter()
        visible = self._feed_all(filt, ["<think>", "secret", "</thi", "nk> tail"])
        self.assertEqual(visible, " tail")

    def test_close_tag_plus_trailing_text_in_one_token(self):
        """A single token carrying "</think>" and visible text after it."""
        filt = ThinkingFilter()
        visible = self._feed_all(filt, ["<think>secret", "</think> tail"])
        self.assertEqual(visible, " tail")

    def test_open_and_close_tag_plus_trailing_text_in_one_token(self):
        """Tag, body, and close tag all inside a single token."""
        filt = ThinkingFilter()
        visible = self._feed_all(filt, ["lead ", "<think>secret</think> tail"])
        self.assertEqual(visible, "lead  tail")

    def test_multiple_think_blocks(self):
        """Two separate thinking blocks in one stream are both suppressed."""
        filt = ThinkingFilter()
        visible = self._feed_all(
            filt,
            [
                "a",
                "<think>",
                "one",
                "</think>",
                "b",
                "<think>",
                "two",
                "</think>",
                "c",
            ],
        )
        self.assertEqual(visible, "abc")

    def test_multiple_think_blocks_in_a_single_token(self):
        visible = self._feed_all(
            ThinkingFilter(), ["a<think>one</think>b<think>two</think>c"]
        )
        self.assertEqual(visible, "abc")

    def test_unterminated_think_block_at_stream_end(self):
        """A stream that ends mid-thinking-block must not leak or crash."""
        filt = ThinkingFilter()
        visible = self._feed_all(filt, ["visible ", "<think>", "never closes"])
        self.assertEqual(visible, "visible ")

    def test_lone_angle_bracket_resolves_on_the_next_token(self):
        """A "<" that turns out not to start a tag must still reach the user.

        This is the cost of buffering: a lone "<" is ambiguous until the next
        token disambiguates it, so it is held back for exactly one feed()
        call, not lost.
        """
        filt = ThinkingFilter()
        first = filt.feed("hello <")
        self.assertEqual(first, "hello ")  # the "<" itself is held back
        second = filt.feed("3")
        self.assertEqual((first or "") + (second or ""), "hello <3")

    def test_buffering_stays_bounded(self):
        """A run of characters that almost-but-never-match a tag must not
        grow the internal pending buffer without limit."""
        filt = ThinkingFilter()
        filt.feed("<" * 50)
        # At most len("</think>") - 1 characters can be legitimately withheld.
        self.assertLessEqual(len(filt._pending), len("</think>") - 1)


class StripThinkingTest(TestCase):
    """strip_thinking() is the persistence-side counterpart of ThinkingFilter:
    it runs once over the full accumulated text rather than token by token,
    but must agree with the filter on what counts as thinking content
    (issue #169).
    """

    def test_terminated_blocks_are_removed(self):
        """Baseline: complete blocks, including multiple ones, are dropped."""
        self.assertEqual(strip_thinking("a<think>x</think>b"), "ab")
        self.assertEqual(
            strip_thinking("one<think>a</think>two<think>b</think>three"),
            "onetwothree",
        )

    def test_text_without_tags_is_unchanged(self):
        self.assertEqual(strip_thinking("no think here"), "no think here")

    def test_unterminated_block_at_end_is_dropped(self):
        """A closing tag that never arrives must not leak into history."""
        self.assertEqual(strip_thinking("visible <think>never closes"), "visible")

    def test_unterminated_block_after_a_terminated_one(self):
        """A completed block earlier in the text is still removed even
        though a later block never closes."""
        self.assertEqual(strip_thinking("a<think>one</think>b<think>two"), "ab")

    def test_literal_angle_think_mid_word_is_not_a_tag(self):
        """ "<thinking>" is not the "<think>" tag (no '>' right after "think"),
        so it must pass through untouched -- matching ThinkingFilter, which
        only recognizes the exact literal tag."""
        text = "I am <thinking> about this out loud"
        self.assertEqual(strip_thinking(text), text)

    def test_second_open_tag_inside_an_unterminated_block_is_also_dropped(self):
        """Once inside a block with no closing tag anywhere after it, a
        further literal "<think>" is just more thinking content -- not the
        start of an independent block -- exactly like ThinkingFilter, which
        never exits an open block except on a real "</think>"."""
        self.assertEqual(strip_thinking("a<think>one<think>two"), "a")

    def test_blocks_are_removed_whatever_surrounds_them(self):
        cases = {
            "<think></think>": "",
            "<think>a</think><think>b</think>": "",
            "a<think>x\ny</think>\nb": "a\nb",
            "a<think>x<think>y</think>z": "az",
            "a</think>b": "a</think>b",
            "<think>a</think>b</think>c": "b</think>c",
            "a<think>b</think>c<think>d</think>e<think>f": "ace",
            "a<think>b</think>c<think>d<think>e": "ac",
            # Removing a block can close up a tag; that tag is cut like any
            # other left open, as it was when a regular expression did this.
            "a<th<think>x</think>ink>b": "a",
        }
        for reply, expected in cases.items():
            with self.subTest(reply=reply):
                self.assertEqual(strip_thinking(reply), expected)

    def test_a_long_reply_is_read_whole(self):
        pair = "<think>a</think>b"
        self.assertEqual(strip_thinking(pair * 20_000), "b" * 20_000)

    def test_strip_thinking_stays_fast_on_crafted_input(self):
        # The tests above check what is removed; this one checks how long it
        # takes. A player can steer the model into a reply of many opening
        # tags and no closing one. The old pattern read to the end of the text
        # from each opening tag: 10 to 16 s on each of the first three inputs
        # below (measured on one machine, 2026-10-18), so code that backtracks
        # again fails here within the minute while this takes milliseconds.
        crafted = {
            "opening tags and no closing tag": "<think>" * 26_000,
            "opening tags with text between": "<think>a " * 26_000,
            "a pair then opening tags": "<think>a</think>" + "<think>" * 26_000,
            "guard: closing tags and no opening tag": "</think>" * 26_000,
            "guard: many pairs": "<think>a</think>b" * 26_000,
        }
        for label, reply in crafted.items():
            with self.subTest(input=label):
                started = time.perf_counter()
                strip_thinking(reply)
                self.assertLess(time.perf_counter() - started, 1.0)
