"""The canon's magic rules seated in the battle (issue #960, slice 2).

The engine runs the casting success tiers, Push and Channel only on the
``MagicRules`` its profile carries; these pin that a Tarmar battle's turn and
menu carry the loaded canon's, that a seated character brings the levels
Channel reads, that a snapshot written before carries them too, that a player
records only a push the menu offered, and that a canon the loader refuses
fails the system check. Every number here is invented.
"""

from dataclasses import replace
from unittest.mock import patch

from django.core.checks import Error, run_checks
from django.test import SimpleTestCase
from django.urls import reverse
from tarmar_engine.policy import Candidate, Decision
from tarmar_engine.state import BattleState, CombatantState

from battle import profiles
from battle.checks import (
    CHANNEL_SOURCE_SKILL_CHECK_ID,
    MAGIC_RULES_CHECK_ID,
    check_magic_rules,
)
from battle.magic_rules import MagicRulesError
from battle.models import Battle
from battle.persistence import TARMAR_PROFILE, load_state
from battle.services import (
    _match_candidate,
    names_option,
    record_player_action,
    snapshot_character,
)
from characters.skills import ARCANE_SKILL_NAME, points_for_level
from common.rolling import Roller
from common.test_helpers import BaseTestCase, create_character

from .magic_fixtures import INVENTED_RULES, rules_with_channel, seated_rules
from .test_cast_flow import awaiting_wizard_battle
from .test_state import make_combatant

#: The seed whose dice give the caster a successful casting roll, so the
#: pushed cast reaches its Control Roll.
PUSHED_CAST_SEED = 3


def caster_and_foe(*, spell_level_recorded: int) -> BattleState:
    """A caster with one damage spell, a few hexes from a fighter.

    Args:
        spell_level_recorded: The caster's recorded level in the spell, which
            the invented Channel doubles.
    """
    caster = make_combatant(
        1,
        name="Caster",
        q=0,
        r=0,
        facing=0,
        intelligence=18,
        max_mana=6,
        mana=6,
        spells=["fire_missile"],
        spell_skill_levels={"fire_missile": spell_level_recorded},
    )
    foe = make_combatant(2, name="Foe", q=4, r=0, facing=3)
    return BattleState(arena_radius=6, combatants=[caster, foe])


def unsaved_tarmar_battle() -> Battle:
    """The battle row the profile reads, unsaved: no turn here touches the DB."""
    return Battle(name="Seated magic", seed=1, rules_profile=TARMAR_PROFILE)


class TarmarEngineProfileTest(SimpleTestCase):
    def test_the_profile_carries_the_loaded_rules(self) -> None:
        with seated_rules():
            self.assertIs(profiles.tarmar_engine_profile().magic, INVENTED_RULES)

    def test_the_menu_is_built_with_the_loaded_channel(self) -> None:
        # Recorded level 0: Channel 0, so the engine would refuse the cast,
        # and the menu the decider builds withholds it.
        state = caster_and_foe(spell_level_recorded=0)
        with seated_rules():
            decision = profiles.decider(unsaved_tarmar_battle())(state, state.by_id(1))
        self.assertFalse(
            [candidate for candidate in decision.candidates if candidate.spell_key]
        )
        self.assertEqual(len(decision.withheld), 1)


class PushedCastTurnTest(SimpleTestCase):
    """A pushed cast through the studio's own turn path, with seeded dice."""

    def test_a_pushed_cast_is_offered_and_its_control_roll_recorded(self) -> None:
        battle = unsaved_tarmar_battle()
        state = caster_and_foe(spell_level_recorded=2)
        offered: list[Candidate] = []
        with seated_rules():
            advise = profiles.decider(battle)

            def decide(state: BattleState, actor: CombatantState) -> Decision:
                advice = advise(state, actor)
                if actor.combatant_id != 1:
                    return advice
                pushed = [
                    candidate for candidate in advice.candidates if candidate.push_mana
                ]
                offered.extend(pushed)
                return Decision(chosen=pushed[-1], candidates=advice.candidates)

            events = profiles.TarmarProfile(battle).run_turn(
                state, Roller(seed=PUSHED_CAST_SEED), decide
            )
        # Channel 4 for a level-1 spell leaves 3 mana to push; the cap is 2.
        self.assertEqual(
            [(candidate.spell_key, candidate.push_mana) for candidate in offered],
            [("fire_missile", 1), ("fire_missile", 2)],
        )
        casts = [
            event
            for event in events
            if event["event_type"] == "action"
            and event["payload"].get("spell") == "fire_missile"
        ]
        self.assertEqual(casts[0]["payload"]["push_mana"], 2)
        self.assertEqual(casts[0]["payload"]["channel"], 4)
        self.assertTrue(casts[0]["payload"]["success"])
        control_rolls = [
            event
            for event in events
            if event["event_type"] == "roll"
            and event["payload"]["purpose"] == "control"
        ]
        self.assertEqual(len(control_rolls), 1)
        # Target: INT 18 less 1 per pushed mana invested.
        self.assertEqual(control_rolls[0]["payload"]["target_number"], 16)
        outcomes = [
            event
            for event in events
            if event["event_type"] == "status" and "control_band" in event["payload"]
        ]
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["payload"]["push_mana"], 2)


class SeatedMagicLevelsTest(BaseTestCase):
    def seat(self, **character_fields) -> CombatantState:
        """Snapshot a saved caster carrying one damage spell and one ward."""
        character = create_character(self.create_user("mage"), **character_fields)
        return snapshot_character(
            character,
            1,
            display_name="Mage",
            archetype="",
            spells=("fire_missile", "shield"),
            mana=6,
        )

    def test_the_spell_levels_come_from_the_spells_record(self) -> None:
        with seated_rules():
            seated = self.seat(
                spells={
                    "fire_missile": points_for_level(3),
                    "shield": points_for_level(1),
                }
            )
        self.assertEqual(seated.spell_skill_levels, {"fire_missile": 3, "shield": 1})

    def test_every_skill_the_channel_reads_is_carried_at_its_level(self) -> None:
        rules = rules_with_channel(source_skills=(ARCANE_SKILL_NAME, "literacy"))
        with seated_rules(rules):
            seated = self.seat(
                spells={"fire_missile": points_for_level(1)},
                skills={
                    ARCANE_SKILL_NAME: points_for_level(2, ARCANE_SKILL_NAME),
                    "literacy": points_for_level(3),
                    "swimming": points_for_level(4),
                },
            )
        # Swimming is held but no kind of magic reads it.
        self.assertEqual(seated.skill_levels, {ARCANE_SKILL_NAME: 2, "literacy": 3})

    def test_a_skill_held_below_level_one_carries_no_key(self) -> None:
        # One point is level 1 on the ordinary ladder, level 0 on Arcane's
        # doubled one: read as Arcane, the skill is held at no level.
        rules = rules_with_channel(source_skills=(ARCANE_SKILL_NAME,))
        with seated_rules(rules):
            seated = self.seat(
                spells={"fire_missile": points_for_level(1)},
                skills={ARCANE_SKILL_NAME: 1},
            )
        self.assertEqual(seated.skill_levels, {})

    def test_no_channel_reads_no_skills(self) -> None:
        with seated_rules(replace(INVENTED_RULES, channel=None)):
            seated = self.seat(
                spells={"fire_missile": points_for_level(1)},
                skills={ARCANE_SKILL_NAME: points_for_level(2, ARCANE_SKILL_NAME)},
            )
        self.assertEqual(seated.skill_levels, {})


class SnapshotWrittenBeforeSeatingTest(SimpleTestCase):
    """A ``state_json`` saved before issue #960 still casts once loaded."""

    def snapshot(self, **caster_fields) -> dict:
        caster = make_combatant(
            1,
            spells=["fire_missile"],
            spell_mastery={"fire_missile": 2},
            **caster_fields,
        )
        return BattleState(arena_radius=6, combatants=[caster]).to_dict()

    def test_a_snapshot_with_no_spell_skill_levels_takes_its_mastery(self) -> None:
        empty = self.snapshot()
        missing = self.snapshot()
        del missing["combatants"][0]["spell_skill_levels"]
        for label, snapshot in (("empty", empty), ("missing", missing)):
            with self.subTest(label):
                state = load_state(snapshot, TARMAR_PROFILE)
                self.assertEqual(state.by_id(1).spell_skill_levels, {"fire_missile": 2})

    def test_a_snapshot_with_spell_skill_levels_keeps_them(self) -> None:
        snapshot = self.snapshot(spell_skill_levels={"fire_missile": 3})
        state = load_state(snapshot, TARMAR_PROFILE)
        self.assertEqual(state.by_id(1).spell_skill_levels, {"fire_missile": 3})


class PushedChoiceMatchTest(SimpleTestCase):
    plain = Candidate("h", "CAST", 1.0, "", target_id=2, spell_key="fire_missile")
    pushed = Candidate(
        "h",
        "CAST (PUSH 1)",
        0.0,
        "",
        target_id=2,
        spell_key="fire_missile",
        push_mana=1,
    )

    def test_a_pushed_choice_names_the_pushed_option_only(self) -> None:
        choice = {"letter": "h", "target_id": 2, "spell_key": "fire_missile"}
        pushed_choice = {**choice, "push_mana": 1}
        self.assertTrue(names_option(self.pushed.to_payload(), pushed_choice))
        self.assertFalse(names_option(self.plain.to_payload(), pushed_choice))
        # A choice with no push_mana, as one stored before Push, is the plain cast.
        self.assertTrue(names_option(self.plain.to_payload(), choice))
        self.assertFalse(names_option(self.pushed.to_payload(), choice))

    def test_the_replayed_menu_resolves_a_pushed_choice(self) -> None:
        choice = {
            "letter": "h",
            "target_id": 2,
            "spell_key": "fire_missile",
            "push_mana": 1,
        }
        self.assertIs(_match_candidate([self.plain, self.pushed], choice), self.pushed)
        self.assertIsNone(
            _match_candidate([self.plain, self.pushed], {**choice, "push_mana": 2})
        )


class RecordPushedChoiceTest(BaseTestCase):
    """Who may set ``push_mana``: a player, to an amount the menu offered."""

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(seated_rules())
        self.player = self.create_player()
        self.battle, self.seat = awaiting_wizard_battle(self.create_dm(), self.player)
        self.pending = self.battle.pending_input_json
        self.offered = next(
            candidate
            for candidate in self.pending["candidates"]
            if candidate.get("push_mana")
        )

    def record(self, push_mana: int):
        return record_player_action(
            self.battle.pk,
            self.player,
            combatant_id=self.seat.pk,
            turn=self.pending["turn"],
            letter=self.offered["letter"],
            target_id=self.offered["target_id"],
            spell_key=self.offered["spell_key"],
            push_mana=push_mana,
        )

    def test_an_offered_push_is_recorded_and_replayed(self) -> None:
        self.record(self.offered["push_mana"])
        chosen = [
            event.payload["chosen"]
            for event in self.battle.events.filter(event_type="decision")
            if event.payload["chosen"].get("spell_key") == self.offered["spell_key"]
        ]
        self.assertEqual(chosen[0]["push_mana"], self.offered["push_mana"])
        self.assertTrue(chosen[0]["rationale"].startswith("chosen by"))

    def test_a_pushed_copy_says_what_it_pushes_and_costs(self) -> None:
        pushed = self.offered["push_mana"]
        self.assertIn(f", pushing {pushed} more mana into it", self.offered["plain"])
        self.assertIn(f"{pushed} pushed)", self.offered["cost"])
        self.assertIn("a success makes a Control Roll", self.offered["cost"])

    def test_a_push_the_menu_did_not_offer_is_refused(self) -> None:
        most_offered = max(
            candidate.get("push_mana") or 0 for candidate in self.pending["candidates"]
        )
        with self.assertRaisesMessage(ValueError, "not on the pending menu"):
            self.record(most_offered + 1)

    def test_the_endpoint_reads_push_mana(self) -> None:
        self.login_as(self.player)
        url = reverse("battle:act", args=[self.battle.pk])
        body = {
            "combatant_id": self.seat.pk,
            "turn": self.pending["turn"],
            "letter": self.offered["letter"],
            "target_id": self.offered["target_id"],
            "spell_key": self.offered["spell_key"],
        }
        response = self.post_json(url, {**body, "push_mana": "lots"})
        self.assertEqual(response.status_code, 400)
        response = self.post_json(url, {**body, "push_mana": self.offered["push_mana"]})
        self.assertEqual(response.status_code, 200)


class MagicRulesCheckTest(SimpleTestCase):
    def test_the_loaded_canon_passes(self) -> None:
        self.assertEqual(check_magic_rules(), [])

    def test_a_refused_file_is_a_registered_error(self) -> None:
        refusal = MagicRulesError("magic-rules.json: push.control_dice is missing")
        with patch("battle.checks.magic_rules", side_effect=refusal):
            messages = [
                message
                for message in run_checks()
                if message.id == MAGIC_RULES_CHECK_ID
            ]
        self.assertEqual(len(messages), 1)
        self.assertIsInstance(messages[0], Error)
        self.assertEqual(messages[0].msg, str(refusal))

    def test_glossary_source_skills_pass(self) -> None:
        rules = rules_with_channel(source_skills=(ARCANE_SKILL_NAME, "literacy"))
        with seated_rules(rules):
            self.assertEqual(check_magic_rules(), [])

    def test_a_source_skill_the_glossary_lacks_is_refused(self) -> None:
        # Capitalised, with a chosen part, and a skill no glossary table has.
        for name in ("Arcane", "maker skill (smith)", "navigation"):
            with (
                self.subTest(name),
                seated_rules(rules_with_channel(source_skills=(name,))),
            ):
                messages = [
                    message
                    for message in run_checks()
                    if message.id == CHANNEL_SOURCE_SKILL_CHECK_ID
                ]
                self.assertEqual(len(messages), 1)
                self.assertIn(repr(name), messages[0].msg)
