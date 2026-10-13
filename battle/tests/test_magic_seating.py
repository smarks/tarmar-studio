"""The canon's magic rules seated in the battle (issue #960, slice 2).

The engine runs the casting success tiers, Push and Channel only on the
``MagicRules`` its profile carries; these pin that a Tarmar battle's turn and
menu carry the loaded canon's, that a seated character brings the levels
Channel reads, that a snapshot written before carries them too, that a player
records only a push the menu offered, and that a canon the loader refuses
fails the system check. Borrowing (tarmar-engine #28) the same way: a cast
the caster cannot afford is offered as a borrowing copy, its debt roll is
recorded, and a player records only a borrowing the menu offered. Every
number here is invented.
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
from battle.turn_menu import borrowed_mana
from characters.skills import ARCANE_SKILL_NAME, points_for_level
from common.rolling import Roller
from common.test_helpers import BaseTestCase, create_character

from .magic_fixtures import (
    BORROWING_RULES,
    INVENTED_RULES,
    rules_with_channel,
    seated_rules,
)
from .test_cast_flow import awaiting_wizard_battle
from .test_state import make_combatant

#: The seed whose dice give the caster a successful casting roll, so the
#: pushed cast reaches its Control Roll.
PUSHED_CAST_SEED = 3
#: Seeds whose dice give a caster with no mana a successful borrowing cast of
#: Fire Missile, then a debt roll that holds, and one that fails.
DEBT_HELD_SEED = 20
DEBT_FAILED_SEED = 28
#: A seed whose dice let a caster with 1 mana pay for the most-pushed
#: borrowing copy of Fire Missile, so the engine borrows.
PUSHED_BORROW_SEED = 1


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


class BorrowingCastTurnTest(SimpleTestCase):
    """A borrowing cast through the studio's own turn path, with seeded dice."""

    #: What the studio read the chosen copy as borrowing, set by each turn.
    studio_borrowed: int = 0

    def run_borrowing_turn(
        self, seed: int, *, mana: int = 0, pushed: bool = False
    ) -> tuple[BattleState, list[dict]]:
        """The caster borrows for Fire Missile; the foe acts.

        Args:
            seed: The turn's dice.
            mana: The caster's pool when the turn begins.
            pushed: Choose the most-pushed borrowing copy, not the plain one.

        Returns:
            The state after the turn and its events. The studio's own reading
            of what the chosen copy borrows (:func:`borrowed_mana`) is kept on
            ``self.studio_borrowed``, for comparison with the engine's.
        """
        battle = unsaved_tarmar_battle()
        state = caster_and_foe(spell_level_recorded=2)
        state.by_id(1).mana = mana
        with seated_rules(BORROWING_RULES):
            advise = profiles.decider(battle)

            def decide(state: BattleState, actor: CombatantState) -> Decision:
                advice = advise(state, actor)
                if actor.combatant_id != 1:
                    return advice
                borrowing = [
                    candidate
                    for candidate in advice.candidates
                    if candidate.borrow and bool(candidate.push_mana) == pushed
                ]
                self.assertEqual(
                    {candidate.spell_key for candidate in borrowing}, {"fire_missile"}
                )
                # Only a player borrows: the AI's own pick is something else.
                self.assertFalse(advice.chosen.borrow)
                chosen = borrowing[-1]
                self.studio_borrowed = borrowed_mana(actor, chosen)
                return Decision(chosen=chosen, candidates=advice.candidates)

            events = profiles.TarmarProfile(battle).run_turn(
                state, Roller(seed=seed), decide
            )
        return state, events

    def cast_payload(self, events: list[dict]) -> dict:
        return next(
            event["payload"]
            for event in events
            if event["event_type"] == "action"
            and event["payload"].get("spell") == "fire_missile"
        )

    def test_the_studio_s_shortfall_is_what_the_engine_borrows(self) -> None:
        # The engine's Candidate carries no shortfall, so the menu and the
        # cast stage read it through one studio helper; the engine's cast
        # payload is the check, for a plain copy and for a pushed one.
        for seed, mana, pushed in (
            (DEBT_HELD_SEED, 0, False),
            (PUSHED_BORROW_SEED, 1, True),
        ):
            with self.subTest(pushed=pushed):
                _state, events = self.run_borrowing_turn(seed, mana=mana, pushed=pushed)
                payload = self.cast_payload(events)
                self.assertEqual(bool(payload.get("push_mana")), pushed)
                self.assertGreater(payload["borrowed"], 0)
                self.assertEqual(payload["borrowed"], self.studio_borrowed)

    def debt_events(self, events: list[dict]) -> tuple[list[dict], list[dict]]:
        rolls = [
            event
            for event in events
            if event["event_type"] == "roll" and event["payload"]["purpose"] == "debt"
        ]
        outcomes = [
            event
            for event in events
            if event["event_type"] == "status" and "held" in event["payload"]
        ]
        return rolls, outcomes

    def test_a_borrowing_cast_records_its_debt_and_a_held_roll(self) -> None:
        state, events = self.run_borrowing_turn(DEBT_HELD_SEED)
        caster = state.by_id(1)
        self.assertEqual((caster.mana, caster.debt), (0, 1))
        cast = self.cast_payload(events)
        self.assertEqual((cast["borrowed"], cast["debt"]), (1, 1))
        rolls, outcomes = self.debt_events(events)
        self.assertEqual(len(rolls), 1)
        # Target: WIS 10 less 2 per point owed.
        self.assertEqual(outcomes[0]["payload"]["target"], 8)
        self.assertTrue(outcomes[0]["payload"]["held"])
        self.assertEqual(
            profiles.TarmarProfile(unsaved_tarmar_battle())
            .figure_vitals(state)[1]
            .debt,
            1,
        )

    def test_a_failed_debt_roll_names_the_injected_failure(self) -> None:
        _state, events = self.run_borrowing_turn(DEBT_FAILED_SEED)
        _rolls, outcomes = self.debt_events(events)
        self.assertFalse(outcomes[0]["payload"]["held"])
        failure = next(
            event
            for event in events
            if event["event_type"] == "status" and "debt_failure" in event["payload"]
        )
        self.assertEqual(failure["payload"]["label"], "Slip of the purse")
        self.assertFalse(failure["payload"]["runaway"])


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


class BorrowedChoiceMatchTest(SimpleTestCase):
    plain = Candidate("h", "CAST", 1.0, "", target_id=2, spell_key="fire_missile")
    borrowing = Candidate(
        "h",
        "CAST (BORROW 1)",
        0.0,
        "",
        target_id=2,
        spell_key="fire_missile",
        borrow=True,
    )
    choice = {"letter": "h", "target_id": 2, "spell_key": "fire_missile"}

    def test_a_borrowing_choice_names_the_borrowing_option_only(self) -> None:
        borrowing_choice = {**self.choice, "borrow": True}
        self.assertTrue(names_option(self.borrowing.to_payload(), borrowing_choice))
        self.assertFalse(names_option(self.plain.to_payload(), borrowing_choice))
        # A choice with no borrow, as one stored before Borrowing, is the plain cast.
        self.assertTrue(names_option(self.plain.to_payload(), self.choice))
        self.assertFalse(names_option(self.borrowing.to_payload(), self.choice))

    def test_the_replayed_menu_resolves_a_borrowing_choice(self) -> None:
        self.assertIs(
            _match_candidate(
                [self.plain, self.borrowing], {**self.choice, "borrow": True}
            ),
            self.borrowing,
        )
        self.assertIsNone(
            _match_candidate([self.plain], {**self.choice, "borrow": True})
        )


class RecordBorrowedChoiceTest(BaseTestCase):
    """Who may set ``borrow``: a player, for a borrowing copy the menu offered."""

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(seated_rules(BORROWING_RULES))
        self.player = self.create_player()
        self.battle, self.seat = awaiting_wizard_battle(
            self.create_dm(), self.player, mana=0
        )
        self.pending = self.battle.pending_input_json
        self.offered = next(
            candidate
            for candidate in self.pending["candidates"]
            if candidate.get("borrow") and not candidate.get("push_mana")
        )

    def record(self, option: dict, *, borrow: bool):
        return record_player_action(
            self.battle.pk,
            self.player,
            combatant_id=self.seat.pk,
            turn=self.pending["turn"],
            letter=option["letter"],
            target_id=option.get("target_id"),
            spell_key=option.get("spell_key") or "",
            push_mana=option.get("push_mana") or 0,
            borrow=borrow,
        )

    def test_an_offered_borrowing_is_recorded_and_replayed(self) -> None:
        self.record(self.offered, borrow=True)
        chosen = [
            event.payload["chosen"]
            for event in self.battle.events.filter(event_type="decision")
            if event.payload["chosen"].get("spell_key") == self.offered["spell_key"]
        ]
        self.assertTrue(chosen[0]["borrow"])
        self.assertTrue(chosen[0]["rationale"].startswith("chosen by"))

    def test_a_borrowing_copy_says_what_it_borrows(self) -> None:
        cost = int(self.offered["borrowed"])
        self.assertGreater(cost, 0)
        self.assertIn(f", borrowing the {cost} mana you lack", self.offered["plain"])
        self.assertIn(f"borrows the {cost} you lack as debt", self.offered["cost"])
        self.assertIn("a debt roll follows the spell", self.offered["cost"])

    def test_the_same_cast_without_borrowing_is_not_on_the_menu(self) -> None:
        # The caster cannot afford the plain cast: only the borrowing copy is
        # offered, and a choice that leaves borrow off is refused.
        with self.assertRaisesMessage(ValueError, "not on the pending menu"):
            self.record(self.offered, borrow=False)

    def test_a_borrowing_the_menu_did_not_offer_is_refused(self) -> None:
        not_a_cast = next(
            candidate
            for candidate in self.pending["candidates"]
            if not candidate.get("spell_key")
        )
        with self.assertRaisesMessage(ValueError, "not on the pending menu"):
            self.record(not_a_cast, borrow=True)

    def test_the_endpoint_reads_borrow(self) -> None:
        self.login_as(self.player)
        url = reverse("battle:act", args=[self.battle.pk])
        body = {
            "combatant_id": self.seat.pk,
            "turn": self.pending["turn"],
            "letter": self.offered["letter"],
            "target_id": self.offered["target_id"],
            "spell_key": self.offered["spell_key"],
        }
        response = self.post_json(url, {**body, "borrow": "yes"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "borrow is true or false")
        response = self.post_json(url, body)
        self.assertEqual(response.status_code, 400)
        response = self.post_json(url, {**body, "borrow": True})
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
