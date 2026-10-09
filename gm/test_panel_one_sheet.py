"""Every character in the Characters pane is drawn one way (issues #848, #872).

A character built in the session was shown from the draft ledger and an
attached one from ``CharacterSheet``, through two templates: the built one
had no Physical, Mental or load, no purse and no spells, and its Literacy
sat in another place. Both are ``Character`` rows since #571, so both are
shown from one sheet through one partial now, and the built one keeps only
what a character being built needs besides (what it lacks, the skill-point
account).
"""

import re

from common.test_helpers import BaseTestCase, create_character

from .character_sheet import CharacterSheet
from .models import GMSession
from .roster import start_character

CHAT_URL = "/tarmar/gm/"

COMPLETE = {
    "strength": 12,
    "dexterity": 11,
    "intelligence": 13,
    "wisdom": 10,
    "constitution": 12,
    "charisma": 9,
    "fatigue_roll": 7,
}


def article(html: str, character_id: int) -> str:
    """The pane's card for one character."""
    found = re.search(
        rf'<article class="gm-sheet[^"]*"[^>]*data-character-id="{character_id}"'
        r"[\s\S]*?</article>",
        html,
    )
    assert found is not None, f"no card for character {character_id}"
    return found.group(0)


def facts(card: str) -> list[str]:
    """The labels of a card's pools and facts, in order."""
    return re.findall(r"<dt>([^<]+)</dt>", card)


class OneSheetForEveryCharacterTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="one-sheet")
        self.login_as(self.user)
        self.session = GMSession.objects.create(name="Both", user=self.user)
        self.built = start_character(self.session, "the scout", name="Wren")
        for field, value in COMPLETE.items():
            setattr(self.built, field, value)
        self.built.spells = {"fatigue": 2}
        self.built.equipment = [{"name": "Rope", "quantity": 1}]
        self.built.save()
        self.attached = create_character(
            self.user, name="Aldwin", spells={"fatigue": 2}
        )
        self.session.characters.add(self.attached)

    def page(self) -> str:
        response = self.client.get(f"{CHAT_URL}?session={self.session.pk}")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_built_character_shows_what_the_attached_one_does(self):
        html = self.page()
        built = article(html, self.built.pk)
        attached = article(html, self.attached.pk)

        self.assertEqual(facts(built), facts(attached))
        for label in ("Physical", "Mental", "Purse", "Spells", "Literacy"):
            self.assertIn(label, facts(built))
        sheet = CharacterSheet.from_character(self.built)
        self.assertIn(f'<dd class="gm-sheet-physical">{sheet.physical}</dd>', built)
        self.assertIn(f'<dd class="gm-sheet-mental">{sheet.mental}</dd>', built)
        self.assertIn(f'<dd class="gm-sheet-purse">{sheet.purse}</dd>', built)
        self.assertIn(sheet.spells[0], built)
        self.assertIn("gm-sheet-encumbrance", built)
        self.assertIn(str(sheet.encumbrance_level).title(), built)

    def test_literacy_follows_background_on_both(self):
        """As on the character sheet (review of #848)."""
        html = self.page()
        for character in (self.built, self.attached):
            order = facts(article(html, character.pk))
            with self.subTest(character=character.name):
                self.assertEqual(order.index("Literacy"), order.index("Background") + 1)

    def test_the_built_one_keeps_what_only_a_character_being_built_needs(self):
        built = article(self.page(), self.built.pk)
        self.assertIn('id="gm-draft-ledger"', built)
        self.assertIn("gm-draft-account", built)
        self.assertIn('id="gm-draft-free-points"', built)
        self.assertIn('id="gm-draft-literacy"', built)
        self.assertNotIn("gm-sheet-detach", built)

    def test_the_attached_one_keeps_its_detach_control(self):
        attached = article(self.page(), self.attached.pk)
        self.assertIn("gm-sheet-detach", attached)
        self.assertNotIn("gm-draft-account", attached)

    def test_a_character_still_being_built_shows_dashes_not_numbers(self):
        self.built.strength = None
        self.built.fatigue_roll = None
        self.built.save()

        built = article(self.page(), self.built.pk)

        self.assertIn("Being built", built)
        self.assertIn(
            '<dd class="gm-sheet-physical"><span class="gm-draft-unset"', built
        )
        self.assertIn(
            '<dd class="gm-sheet-fatigue"><span class="gm-draft-unset"', built
        )
        self.assertNotIn("gm-sheet-encumbrance", built)
        sheet = CharacterSheet.from_character(self.built)
        self.assertIsNone(sheet.fatigue)
        self.assertIsNone(sheet.physical)
        self.assertIsNone(sheet.encumbrance_level)
        self.assertIsNone(sheet.condition)
        # A rolled score shows without a modifier until all six are in.
        dexterity = sheet.attributes[1]
        self.assertEqual((dexterity.base, dexterity.modifier), (11, None))
        self.assertFalse(sheet.attributes[0].is_set)

    def test_the_purse_follows_coin_given_after_creation(self):
        """What #872 asked: the built one's purse, not only its starting coin."""
        self.built.coin_adjustment = 25
        self.built.save()
        sheet = CharacterSheet.from_character(self.built)
        self.assertIn(
            f'<dd class="gm-sheet-purse">{sheet.purse}</dd>',
            article(self.page(), self.built.pk),
        )
        self.assertNotEqual(
            sheet.purse, CharacterSheet.from_character(self.attached).purse
        )
