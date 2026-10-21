"""The turn panel's own words come from the server (#94).

The page drew its prompt, each option's cost and the AI's pick, the push,
borrow and counter controls, the cast stage, the hex picker and its buttons
in words battle.js wrote itself. They are ``config/battle-rulings.toml``'s
``[turn_panel]`` now, rendered once into the arena partial as
``#battle-turn-panel-words`` (they cannot change while the server runs), so
the turn panel prints no wording of its own. battle.js's Jest tests draw the
panel under ``static/js/__tests__/fixtures/turn-panel-words.json``, which is
held here to the server's fixed test words, and one of them checks that every
key is drawn.
"""

import json
import re
from dataclasses import asdict
from pathlib import Path

from django.conf import settings
from django.urls import reverse

from battle.views import TURN_PANEL_WORDS_CONTEXT
from battle.wording import words
from common.test_helpers import BaseTestCase

from .rulings_words import FIXED_WORDS, with_fixed_words
from .test_cast_flow import awaiting_wizard_battle

#: The fixed words battle.js's Jest tests draw the panel in.
JEST_PANEL_WORDS = (
    Path(settings.BASE_DIR) / "static/js/__tests__/fixtures/turn-panel-words.json"
)

#: The block the arena partial renders the words into, which battle.js reads.
PANEL_WORDS_BLOCK = re.compile(
    r'<script id="battle-turn-panel-words" type="application/json">(.*?)</script>',
    re.DOTALL,
)


class JestPanelWordsAreTheFixedWordsTest(BaseTestCase):
    def test_the_jest_fixture_is_the_server_s_fixed_panel_words(self):
        """A key added, dropped or renamed on the server fails here, not as a
        brace on a player's screen."""
        self.assertEqual(
            json.loads(JEST_PANEL_WORDS.read_text(encoding="utf-8")),
            asdict(FIXED_WORDS.turn_panel),
        )


@with_fixed_words
class TheArenaCarriesThePanelWordsOnceTest(BaseTestCase):
    """The battle page carries the configured words once, and the polls the
    panel repaints from do not carry them again."""

    def test_the_page_has_the_words_and_the_poll_has_none(self):
        dm, player = self.create_dm(), self.create_player()
        battle, _seat = awaiting_wizard_battle(dm, player)
        self.login_as(player)
        page = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertEqual(
            page.context[TURN_PANEL_WORDS_CONTEXT], asdict(words().turn_panel)
        )
        block = PANEL_WORDS_BLOCK.search(page.content.decode())
        self.assertIsNotNone(block, "the page has no #battle-turn-panel-words block")
        assert block is not None
        rendered = json.loads(block.group(1))
        self.assertEqual(rendered, asdict(FIXED_WORDS.turn_panel))
        self.assertEqual(rendered["costs"], "turn_panel.costs {cost}")
        poll = self.client.get(reverse("battle:state", args=[battle.pk]))
        self.assertEqual(poll.status_code, 200)
        pending = json.loads(poll.content)["pending"]
        self.assertTrue(pending["candidates"])
        # Under the fixed words any panel word would say "turn_panel.".
        self.assertNotIn("turn_panel.", json.dumps(pending))
