"""A text the chat writes always survives its character's own export (#928).

The chat read item, skill and character names at its 200-character plan
ceiling while the importer took 100 for an item and a character's name, so a
character could fail to re-import its own export (the #800 class). Each
field now has one limit (``characters.constants.CHARACTER_TEXT_LIMITS``),
held by the service the sheet and the chat share: a text at the limit
round-trips unchanged, and one character more is refused with a line the
ledger shows, never cut short.
"""

import json

from character_export.export_utils import generate_character_json
from characters.constants import (
    CHARACTER_TEXT_LIMITS,
    MAX_CHARACTER_NAME_LENGTH,
    MAX_COIN_REASON_LENGTH,
    MAX_DEATH_CAUSE_LENGTH,
    MAX_EQUIPMENT_NAME_LENGTH,
    MAX_SKILL_NAME_LENGTH,
)
from characters.import_service import (
    held_to_player,
    save_imported_character,
    validate_import_payload,
)
from characters.models import Character
from common.test_helpers import create_character

from .engine import MODE_PLAY
from .models import GMEvent
from .test_turn_engine_executor import ExecutorTestCase, plan
from .testing import add_character, recorded
from .turn_engine import events
from .turn_engine.plan import MAX_RECORD_TEXT_CHARS


def exported_and_imported(character: Character, *, is_game_master: bool) -> Character:
    """``character`` through its own export and back in, as a new record.

    Raises:
        CharacterImportError: The export does not import.
    """
    imported = validate_import_payload(json.loads(generate_character_json(character)))
    kept = held_to_player(imported, is_game_master=is_game_master)
    return save_imported_character(character.user, kept)


class _ChatTextCase(ExecutorTestCase):
    def run_action(self, **action):
        """Run one planned action; returns its result and its ledger event."""
        outcome, _yielded, _model, turn = self.play(plan(action))
        return outcome.results[0], GMEvent.objects.get(turn=turn, kind=events.ACTION)

    def assert_refused_in_the_ledger(self, result, event, reason: str) -> None:
        self.assertFalse(result.ran)
        self.assertIn(reason, result.error)
        # A line the ledger shows, not a plan the model is asked to rewrite.
        self.assertTrue(event.visible)


class PlanCeilingTest(_ChatTextCase):
    def test_the_plan_ceiling_lets_every_field_reach_its_own_service(self):
        """A text one past its field's limit reaches the service that says so."""
        for field, limit in CHARACTER_TEXT_LIMITS.items():
            with self.subTest(field=field):
                self.assertGreater(MAX_RECORD_TEXT_CHARS, limit)


class GameMasterChatTextTest(_ChatTextCase):
    """Skill names and character names: a Game Master's to give (#902)."""

    def setUp(self):
        super().setUp()
        self.gorn = add_character(self.session, recorded("Gorn", score=10))

    def test_a_skill_at_the_limit_round_trips(self):
        skill = "q" * MAX_SKILL_NAME_LENGTH
        result, _event = self.run_action(
            action="spend_skill_points",
            character_id=self.gorn.pk,
            skill=skill,
            points=1,
        )
        self.assertTrue(result.ran, result.error)
        self.gorn.refresh_from_db()

        back = exported_and_imported(self.gorn, is_game_master=True)

        self.assertEqual(back.skills, self.gorn.skills)
        self.assertIn(skill, back.skills)

    def test_a_skill_one_past_the_limit_is_refused_in_the_ledger(self):
        result, event = self.run_action(
            action="spend_skill_points",
            character_id=self.gorn.pk,
            skill="q" * (MAX_SKILL_NAME_LENGTH + 1),
            points=1,
        )

        self.assert_refused_in_the_ledger(
            result, event, f"Skill name must be {MAX_SKILL_NAME_LENGTH} characters"
        )
        self.gorn.refresh_from_db()
        self.assertEqual(self.gorn.skills, {})

    def test_a_skill_is_refused_by_its_received_length_in_the_ledger(self):
        # Issue #52: this would store as "sword to hit", which fits.
        result, event = self.run_action(
            action="spend_skill_points",
            character_id=self.gorn.pk,
            skill="Sword (+1 to hit" + " " * MAX_SKILL_NAME_LENGTH + ")",
            points=1,
        )

        self.assert_refused_in_the_ledger(
            result, event, f"Skill name must be {MAX_SKILL_NAME_LENGTH} characters"
        )

    def test_a_name_at_the_limit_round_trips(self):
        name = "N" * MAX_CHARACTER_NAME_LENGTH
        result, _event = self.run_action(
            action="rename_character", character_id=self.gorn.pk, name=name
        )
        self.assertTrue(result.ran, result.error)
        self.gorn.refresh_from_db()

        back = exported_and_imported(self.gorn, is_game_master=True)

        self.assertEqual(back.name, name)

    def test_a_name_one_past_the_limit_is_refused_in_the_ledger(self):
        result, event = self.run_action(
            action="rename_character",
            character_id=self.gorn.pk,
            name="N" * (MAX_CHARACTER_NAME_LENGTH + 1),
        )

        self.assert_refused_in_the_ledger(
            result, event, f"Name must be {MAX_CHARACTER_NAME_LENGTH} characters"
        )
        self.gorn.refresh_from_db()
        self.assertEqual(self.gorn.name, "Gorn")

    def test_a_cause_of_death_one_past_the_limit_is_refused_in_the_ledger(self):
        result, event = self.run_action(
            action="mark_dead",
            character_id=self.gorn.pk,
            cause="c" * (MAX_DEATH_CAUSE_LENGTH + 1),
        )

        self.assert_refused_in_the_ledger(
            result, event, f"Cause must be {MAX_DEATH_CAUSE_LENGTH} characters"
        )
        self.gorn.refresh_from_db()
        self.assertTrue(self.gorn.is_alive)

    def test_a_coin_reason_one_past_the_limit_is_refused_in_the_ledger(self):
        result, event = self.run_action(
            action="set_coin",
            character_id=self.gorn.pk,
            change=5,
            reason="r" * (MAX_COIN_REASON_LENGTH + 1),
        )

        self.assert_refused_in_the_ledger(
            result, event, f"Reason must be {MAX_COIN_REASON_LENGTH} characters"
        )
        self.gorn.refresh_from_db()
        self.assertEqual(self.gorn.coin_adjustment, 0)

    def test_a_new_character_named_past_the_limit_is_refused_not_cut(self):
        """The plan used to cut a new character's name to 100 and start it."""
        too_long = "N" * (MAX_CHARACTER_NAME_LENGTH + 1)
        result, event = self.run_action(
            action="roll_attributes", new_character={"name": too_long}
        )

        self.assert_refused_in_the_ledger(
            result, event, f"Name must be {MAX_CHARACTER_NAME_LENGTH} characters"
        )
        self.assertFalse(Character.objects.filter(name__startswith="N" * 50).exists())


class PlayerChatItemTest(_ChatTextCase):
    """An item a player's chat buys (#927), at and past the name limit."""

    game_master = False
    mode = MODE_PLAY

    def setUp(self):
        super().setUp()
        self.wren = create_character(self.user, name="Wren", starting_coin=100)
        self.session.characters.add(self.wren)

    def test_an_item_at_the_limit_round_trips(self):
        item = "x" * MAX_EQUIPMENT_NAME_LENGTH
        result, _event = self.run_action(
            action="add_equipment", character_id=self.wren.pk, item=item, price=1
        )
        self.assertTrue(result.ran, result.error)
        self.wren.refresh_from_db()

        # A player's own import takes the file as written without refusing it
        # (its gear is bought again on the sheet), and a Game Master's keeps
        # the item as it was exported.
        exported_and_imported(self.wren, is_game_master=False)
        back = exported_and_imported(self.wren, is_game_master=True)

        self.assertEqual(back.equipment, self.wren.equipment)
        self.assertEqual(back.equipment[0]["name"], item)

    def test_an_item_one_past_the_limit_is_refused_in_the_ledger(self):
        result, event = self.run_action(
            action="add_equipment",
            character_id=self.wren.pk,
            item="x" * (MAX_EQUIPMENT_NAME_LENGTH + 1),
            price=1,
        )

        self.assert_refused_in_the_ledger(
            result, event, f"Name must be {MAX_EQUIPMENT_NAME_LENGTH} characters"
        )
        self.wren.refresh_from_db()
        self.assertEqual(self.wren.equipment, [])
