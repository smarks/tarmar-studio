"""tarmar-studio#191: each element is its own track, climbed tier by tier.

Spencer's ruling (the canon's ``elemental_tracks_climb_tier_by_tier``): a
student of Elemental magic picks an element and studies that track. Learning
tier N of an element needs tier N-1 of the same element, so Fire Bolt needs
Fire Ball and Fire Ball needs Fire Missile; a mage who switches element starts
the new one at tier 1. Spells already on the record stay usable and may be
raised. Players are held to the rule; a Game Master may teach any spell.

The rung below a spell is the engine's (``tarmar_engine.spells.lower_tier``),
so no test here types a ladder. The refusal's words are configuration, so the
tests run under the fixed words (``characters.tests.fixed_refusals``).
"""

import re
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.urls import reverse
from tarmar_engine.spells import ELEMENTAL_SCHOOL, ELEMENTS, SPELLS, Spell, lower_tier

from battle.tests.magic_fixtures import seated_schools
from character_export.export_utils import generate_character_json
from characters.magic import (
    ladder_refusal,
    learn_spell,
    learnable_spells,
    rung_not_held,
    spell_points,
    spells_open_to,
)
from characters.models import Character
from characters.refusal_words import refusal_words
from characters.services import CharacterChangeError
from characters.skills import format_skill_level
from characters.tests.fixed_refusals import with_fixed_refusals
from common.test_helpers import BaseTestCase, create_character

#: The page whose Elemental intro states the ladder.
SPELL_DESCRIPTIONS = (
    Path(settings.BASE_DIR)
    / "reference/content/public-rules/magic/spell-descriptions.md"
)

#: The elements the tests climb and switch to: the first two the engine
#: names, so no element is typed.
FIRST_ELEMENT, SECOND_ELEMENT = ELEMENTS[:2]


def ladder(element: str) -> list[Spell]:
    """``element``'s spells from its first tier up, as the catalog holds them."""
    return sorted(
        (spell for spell in SPELLS.values() if spell.element == element),
        key=lambda spell: spell.level,
    )


def first_rung_above_one(element: str) -> Spell:
    """``element``'s tier-2 spell, the first that has a rung below it."""
    return next(spell for spell in ladder(element) if lower_tier(spell) is not None)


def refused_for_lacking(spell: Spell, rung_below: Spell) -> str:
    """The refusal a player meets learning ``spell`` without ``rung_below``."""
    return refusal_words().magic.lower_rung_first.format(
        spell=spell.name, rung_below=rung_below.name
    )


@with_fixed_refusals
class LadderTestCase(BaseTestCase):
    """A player's Elemental caster under the invented school table, which
    names the Elemental school, with points to spend."""

    def setUp(self):
        super().setUp()
        self.enterContext(seated_schools())
        self.owner = self.create_user("ladder_caster")
        self.character = create_character(
            self.owner,
            name="Ladder Caster",
            free_skill_points=10,
            magic_school_roll=23,
            magic_school=ELEMENTAL_SCHOOL,
        )


class PlayerClimbsTheLadderTest(LadderTestCase):
    def test_a_rung_above_one_not_held_is_refused_and_spends_nothing(self):
        spell = first_rung_above_one(FIRST_ELEMENT)
        rung_below = lower_tier(spell)
        assert rung_below is not None
        with self.assertRaises(CharacterChangeError) as refused:
            learn_spell(self.character, spell.key, is_game_master=False)
        self.assertEqual(str(refused.exception), refused_for_lacking(spell, rung_below))
        self.assertEqual(self.character.spells, {})
        self.assertEqual(self.character.free_skill_points, 10)

    def test_each_rung_opens_once_the_one_below_is_held(self):
        for spell in ladder(FIRST_ELEMENT):
            with self.subTest(spell=spell.key):
                learn_spell(self.character, spell.key, is_game_master=False)
                self.assertEqual(spell_points(self.character, spell.key), 1)
        self.assertEqual(
            set(self.character.spells),
            {spell.key for spell in ladder(FIRST_ELEMENT)},
        )

    def test_the_sheet_offers_only_the_rungs_a_player_may_climb(self):
        offered = {
            spell.key for spell in spells_open_to(self.character, is_game_master=False)
        }
        tier_ones = {
            spell.key
            for spell in SPELLS.values()
            if spell.school == ELEMENTAL_SCHOOL and lower_tier(spell) is None
        }
        self.assertEqual(offered, tier_ones)
        tier_one = ladder(FIRST_ELEMENT)[0]
        learn_spell(self.character, tier_one.key, is_game_master=False)
        offered_after = {
            spell.key for spell in spells_open_to(self.character, is_game_master=False)
        }
        self.assertEqual(
            offered_after, tier_ones | {first_rung_above_one(FIRST_ELEMENT).key}
        )

    def test_a_new_element_starts_at_tier_one(self):
        for spell in ladder(FIRST_ELEMENT):
            learn_spell(self.character, spell.key, is_game_master=False)
        second_ladder = ladder(SECOND_ELEMENT)
        above_one = first_rung_above_one(SECOND_ELEMENT)
        # The first element's top rung does not stand in for the second's
        # first: the rung below is the same element's.
        self.assertEqual(
            ladder_refusal(self.character, above_one),
            refused_for_lacking(above_one, second_ladder[0]),
        )
        learn_spell(self.character, second_ladder[0].key, is_game_master=False)
        self.assertIsNone(ladder_refusal(self.character, above_one))

    def test_a_spell_already_held_is_raised_whatever_is_below_it(self):
        top_rung = ladder(FIRST_ELEMENT)[-1]
        self.character.spells = {top_rung.key: 1}
        self.assertIsNone(rung_not_held(self.character, top_rung))
        line = learn_spell(self.character, top_rung.key, is_game_master=False)
        self.assertEqual(self.character.spells, {top_rung.key: 2})
        self.assertEqual(self.character.free_skill_points, 9)
        # The whole line: a player's raise carries no Game Master's note.
        self.assertEqual(
            line,
            f"{top_rung.name}: 1 → 2 points, mastery {format_skill_level(2)}",
        )

    def test_a_spell_of_no_element_has_no_ladder(self):
        for spell in SPELLS.values():
            if spell.element is None:
                with self.subTest(spell=spell.key):
                    self.assertIsNone(ladder_refusal(self.character, spell))

    def test_a_canon_with_no_school_table_still_holds_the_ladder(self):
        spell = first_rung_above_one(FIRST_ELEMENT)
        with seated_schools(None):
            with self.assertRaises(CharacterChangeError):
                learn_spell(self.character, spell.key, is_game_master=False)


class GameMasterTeachesPastTheLadderTest(LadderTestCase):
    def test_a_game_master_teaches_a_rung_above_one_and_it_is_recorded(self):
        top_rung = ladder(FIRST_ELEMENT)[-1]
        rung_below = lower_tier(top_rung)
        assert rung_below is not None
        line = learn_spell(self.character, top_rung.key, is_game_master=True)
        self.assertEqual(self.character.spells, {top_rung.key: 1})
        self.assertEqual(self.character.free_skill_points, 10)
        past_the_rung = refusal_words().magic.game_master_past_the_rung
        self.assertIn(f"({past_the_rung.format(rung_below=rung_below.name)})", line)

    def test_a_game_master_is_offered_every_spell(self):
        self.assertEqual(
            spells_open_to(self.character, is_game_master=True), learnable_spells()
        )

    def test_a_player_raises_what_a_game_master_taught_past_the_ladder(self):
        top_rung = ladder(FIRST_ELEMENT)[-1]
        learn_spell(self.character, top_rung.key, is_game_master=True)
        learn_spell(self.character, top_rung.key, is_game_master=False)
        self.assertEqual(self.character.spells, {top_rung.key: 2})


class LearnSpellEndpointTest(LadderTestCase):
    def post_learn(self, spell: Spell):
        url = reverse("characters:learn_spell", args=[self.character.pk])
        return self.post_json(url, {"spell": spell.key})

    def test_a_player_is_refused_on_the_sheet_and_a_game_master_is_not(self):
        spell = first_rung_above_one(FIRST_ELEMENT)
        rung_below = lower_tier(spell)
        assert rung_below is not None
        self.login_as(self.owner)
        refused = self.post_learn(spell)
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertEqual(
            refused.json()["error"], refused_for_lacking(spell, rung_below)
        )
        self.logout()
        self.login_as(self.create_dm())
        taught = self.post_learn(spell)
        self.assertEqual(taught.status_code, 200, taught.content)
        self.character.refresh_from_db()
        self.assertEqual(self.character.spells, {spell.key: 1})


class ImportHoldsTheLadderTest(LadderTestCase):
    def test_a_players_import_brings_no_spell_and_a_game_masters_keeps_them(self):
        """The import path: a player's import drops the spells known
        (``GAME_MASTER_IMPORT_FIELDS``, the #744 rule), so a hand-edited file
        cannot grant a rung past the ladder; a Game Master's keeps the file's
        spells as written, his override."""
        top_rung = ladder(FIRST_ELEMENT)[-1]
        self.character.spells = {top_rung.key: 1}
        self.character.save()
        payload = generate_character_json(self.character).encode()
        imported = {}
        for label, user in (
            ("player", self.create_user("ladder_importer")),
            ("game master", self.create_dm()),
        ):
            self.login_as(user)
            upload = SimpleUploadedFile("c.json", payload, "application/json")
            response = self.client.post(reverse("characters:import"), {"file": upload})
            self.assertEqual(response.status_code, 200, response.content)
            imported[label] = Character.objects.get(pk=response.json()["character_id"])
            self.logout()
        self.assertEqual(imported["player"].spells, {})
        self.assertEqual(imported["game master"].spells, {top_rung.key: 1})


class LadderOrderTest(SimpleTestCase):
    def test_each_ladder_reads_in_order_within_its_school(self):
        order = [spell.key for spell in learnable_spells()]
        for element in ELEMENTS:
            with self.subTest(element=element):
                keys = [spell.key for spell in ladder(element)]
                start = order.index(keys[0])
                self.assertEqual(order[start : start + len(keys)], keys)
        schools = [spell.school for spell in learnable_spells()]
        self.assertEqual(schools, sorted(schools))

    def test_the_elements_follow_the_page_not_the_alphabet(self):
        """Each element first appears in ``ELEMENTS``' order, the order
        schools-of-magic.md lists them."""
        first_seen: list[str] = []
        for spell in learnable_spells():
            if spell.element is not None and spell.element not in first_seen:
                first_seen.append(spell.element)
        self.assertEqual(tuple(first_seen), ELEMENTS)

    def test_a_spell_of_no_element_comes_first_in_its_school(self):
        for school in {spell.school for spell in SPELLS.values()}:
            in_school = [
                spell for spell in learnable_spells() if spell.school == school
            ]
            with self.subTest(school=school):
                elements = [spell.element is not None for spell in in_school]
                self.assertEqual(elements, sorted(elements))


class SpellDescriptionsStateTheLadderTest(SimpleTestCase):
    """spell-descriptions.md's Elemental intro says what the sheet enforces."""

    def setUp(self):
        text = SPELL_DESCRIPTIONS.read_text()
        intro = text.split("## Elemental", 1)[1].split("| Spell |", 1)[0]
        # One line, emphasis dropped, so a rewrap or bold changes nothing.
        self.intro = " ".join(intro.replace("**", "").split())
        self.open_questions = text.split("## Open questions for the author", 1)[1]

    def test_the_intro_names_every_element_and_the_track_from_tier_one(self):
        self.assertIn(", ".join(ELEMENTS), self.intro)
        self.assertIn("is its own track", self.intro)
        self.assertIn("learns it from tier 1, climbing it tier by tier", self.intro)
        self.assertIn("starts it at tier 1", self.intro)

    def test_the_intro_no_longer_chooses_the_element_per_spell(self):
        self.assertNotIn("chosen when you learn it", self.intro)
        self.assertNotIn("learns it again", self.intro)
        self.assertNotIn("Element choice", self.open_questions)

    def test_each_example_rung_the_intro_gives_is_the_catalogs(self):
        by_name = {spell.name: spell for spell in SPELLS.values()}
        examples = re.findall(
            r"([A-Z]\w+ [A-Z]\w+) needs ([A-Z]\w+ [A-Z]\w+)", self.intro
        )
        self.assertTrue(examples)
        for spell_name, rung_below_name in examples:
            with self.subTest(spell=spell_name):
                self.assertEqual(
                    lower_tier(by_name[spell_name]), by_name[rung_below_name]
                )
