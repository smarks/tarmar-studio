"""Model and factory tests for the battle app."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError

from battle.models import (
    MAX_STATE_JSON_COMBATANTS,
    Battle,
    BattleCombatant,
    BattleEvent,
    validate_state_json,
)
from common.test_helpers import BaseTestCase, create_character, make_beast


class CharacterFactoryTest(BaseTestCase):
    def test_creates_character_with_defaults(self):
        user = self.create_user()
        character = create_character(user)
        self.assertEqual(character.user, user)
        self.assertEqual(character.name, "Test Character")
        self.assertEqual(character.strength, 14)
        self.assertTrue(character.is_alive)

    def test_overrides_apply(self):
        user = self.create_user()
        character = create_character(
            user, name="Grimble", dexterity=17, skills={"weapon broadsword": 6}
        )
        self.assertEqual(character.name, "Grimble")
        self.assertEqual(character.dexterity, 17)
        self.assertEqual(character.skills, {"weapon broadsword": 6})


class BattleModelTest(BaseTestCase):
    def make_battle(self, **overrides):
        user = overrides.pop("created_by", None) or self.create_dm()
        fields = {"name": "Test Battle", "seed": 42, "created_by": user}
        fields.update(overrides)
        return Battle.objects.create(**fields)

    def test_defaults(self):
        battle = self.make_battle()
        self.assertEqual(battle.status, Battle.STATUS_SETUP)
        self.assertEqual(battle.turn_number, 0)
        self.assertEqual(battle.state_json, {})
        self.assertEqual(str(battle), "Test Battle (seed 42)")

    def test_state_json_validator_rejects_non_dict(self):
        with self.assertRaises(ValidationError):
            validate_state_json([])

    def test_state_json_validator_rejects_non_list_combatants(self):
        with self.assertRaises(ValidationError):
            validate_state_json({"combatants": "nope"})

    def test_state_json_validator_rejects_too_many_combatants(self):
        state = {"combatants": [{}] * (MAX_STATE_JSON_COMBATANTS + 1)}
        with self.assertRaises(ValidationError):
            validate_state_json(state)

    def test_state_json_validator_accepts_valid_state(self):
        validate_state_json({"combatants": [{"name": "A"}]})

    def test_combatant_str_and_character_link(self):
        battle = self.make_battle()
        character = create_character(battle.created_by)
        combatant = BattleCombatant.objects.create(
            battle=battle, character=character, display_name="Test Battle Archer 1"
        )
        self.assertEqual(combatant.character, character)
        self.assertIsNone(combatant.beast)
        self.assertEqual(str(combatant), "Test Battle Archer 1 in Test Battle")

    def test_combatant_links_beast(self):
        battle = self.make_battle()
        beast = make_beast()
        combatant = BattleCombatant.objects.create(
            battle=battle, beast=beast, display_name=beast.name
        )
        self.assertEqual(combatant.beast, beast)
        self.assertIsNone(combatant.character)

    def test_combatant_with_both_sources_is_rejected_by_the_db(self):
        battle = self.make_battle()
        character = create_character(battle.created_by)
        beast = make_beast()
        with self.assertRaises(IntegrityError):
            BattleCombatant.objects.create(
                battle=battle,
                character=character,
                beast=beast,
                display_name="Chimera of the join table",
            )

    def test_clean_requires_exactly_one_source(self):
        battle = self.make_battle()
        character = create_character(battle.created_by)
        beast = make_beast()
        with self.assertRaises(ValidationError):
            BattleCombatant(battle=battle, display_name="Nobody").clean()
        with self.assertRaises(ValidationError):
            BattleCombatant(
                battle=battle,
                character=character,
                beast=beast,
                display_name="Both",
            ).clean()
        BattleCombatant(battle=battle, character=character, display_name="One").clean()
        BattleCombatant(battle=battle, beast=beast, display_name="Other").clean()

    def test_hard_deleting_a_fought_source_leaves_a_tombstone_row(self):
        # Cleanup flows (user cascades, GM variant pruning) must stay able to
        # hard-delete a fought source; the combatant row survives with its
        # display_name as the audit trail.
        battle = self.make_battle()
        beast = make_beast()
        combatant = BattleCombatant.objects.create(
            battle=battle, beast=beast, display_name=beast.name
        )
        beast.delete()
        combatant.refresh_from_db()
        self.assertIsNone(combatant.beast)
        self.assertEqual(combatant.display_name, "Test Wolf")

    def test_deleting_a_user_cascades_through_fought_characters(self):
        # The regression the e2e teardown caught: User -> Character cascade
        # must not be blocked by combatant provenance rows.
        battle = self.make_battle()
        character = create_character(battle.created_by)
        BattleCombatant.objects.create(
            battle=battle, character=character, display_name=character.name
        )
        battle.created_by.delete()  # cascades Battle and Character away
        self.assertFalse(BattleCombatant.objects.exists())

    def test_event_str_and_ordering(self):
        battle = self.make_battle()
        second = BattleEvent.objects.create(
            battle=battle,
            turn=1,
            phase=1,
            sequence=2,
            event_type="roll",
            actor="A",
            message="second",
        )
        first = BattleEvent.objects.create(
            battle=battle,
            turn=1,
            phase=1,
            sequence=1,
            event_type="phase",
            message="first",
        )
        self.assertEqual(list(battle.events.all()), [first, second])
        self.assertIn("roll: second", str(second))

    def test_event_sequence_unique_per_battle(self):
        battle = self.make_battle()
        BattleEvent.objects.create(
            battle=battle, turn=1, phase=1, sequence=1, event_type="info", message="a"
        )
        with self.assertRaises(IntegrityError):
            BattleEvent.objects.create(
                battle=battle,
                turn=1,
                phase=2,
                sequence=1,
                event_type="info",
                message="b",
            )
