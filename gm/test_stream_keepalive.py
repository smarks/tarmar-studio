"""A GM stream keeps talking while the model is silent (issue #415).

Session 30's turn 4 was dropped by the page 36 seconds in, well inside the
server's budget. Production traffic arrives through a proxy, and the stream
sent nothing while the model read a long prompt before its first token, so the
connection looked idle. The stream now sends SSE keep-alive comments in those
gaps; the view's side is in gm/test_plan_turn_view.py.
"""

import threading
import time

from common.test_helpers import BaseTestCase

from .turn_engine.model import KeepAliveTokens as _KeepAliveTokens

STREAM_URL = "/tarmar/gm/stream/"


def slow_tokens(delay: float, tokens: list[str], closed: threading.Event | None = None):
    try:
        time.sleep(delay)
        yield from tokens
    finally:
        if closed is not None:
            closed.set()


class KeepAliveTokensTest(BaseTestCase):
    def test_gaps_are_filled_before_the_first_token(self):
        items = list(_KeepAliveTokens(slow_tokens(1.0, ["Hello"]), interval=0.05))

        self.assertIn(None, items)
        self.assertEqual([item for item in items if item is not None], ["Hello"])
        self.assertLess(items.index(None), items.index("Hello"))

    def test_a_model_error_reaches_the_consumer(self):
        def failing():
            yield "partial"
            raise RuntimeError("model went away")

        seen = []
        with self.assertRaisesMessage(RuntimeError, "model went away"):
            for item in _KeepAliveTokens(failing(), interval=1.0):
                seen.append(item)
        self.assertEqual(seen, ["partial"])

    def test_closing_stops_the_reader_and_closes_the_model_stream(self):
        closed = threading.Event()
        tokens = _KeepAliveTokens(
            slow_tokens(0.0, ["a", "b", "c"] * 50, closed), interval=1.0
        )

        next(iter(tokens))
        tokens.close()

        self.assertTrue(closed.wait(timeout=10), "the model stream was never closed")
