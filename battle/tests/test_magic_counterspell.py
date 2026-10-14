"""Counterspell seated in the battle (tarmar-engine #31).

The engine offers a mage a counter against each enemy mage, one per
commitment the canon's ``CounterspellRules`` allow, makes the opposed roll
when that caster casts, leaves a countered spell standing as a contest on
its caster, and marks a cast that presses it again as an escalation. These
pin the studio's side: the pending menu offers the counters the engine
offered and reads them, and an escalation, in plain words; a player, a Game
Master choosing for a foe, or the AI's hand-off records only a counter the
menu offered, through the act endpoint too; the counter, the contest and the
counter choice survive the save and load between turns; and the roster card
shows a spell standing countered. Every number here is invented.
"""

from django.urls import reverse
from tarmar_engine.actions import COUNTER_OPTION
from tarmar_engine.policy import Candidate

from battle.engine.state import BattleState, ContestState, CounterState
from battle.models import Battle, BattleCombatant
from battle.persistence import TARMAR_PROFILE, dump_state, load_state
from battle.services import (
    _match_candidate,
    _take_off_board,
    advance_turn,
    create_battle,
    names_option,
    record_player_action,
    set_combatant_control_mode,
)
from battle.viewmodel import tarmar_view
from common.test_helpers import BaseTestCase

from .magic_fixtures import COUNTER_RULES, seated_rules
from .test_cast_flow import awaiting_wizard_battle
from .test_state import make_combatant

#: The spell the enemy mage knows and the wizard presses in these tests.
SPELL = "fire_missile"
#: The commitments :data:`COUNTER_RULES` allow: 1 to 3 mana.
COMMITMENTS = [1, 2, 3]


def entry_for(state: dict, combatant_id: int) -> dict:
    """One combatant's entry in a ``state_json``."""
    return next(
        entry for entry in state["combatants"] if entry["combatant_id"] == combatant_id
    )


def foe_entry(state: dict, seat: BattleCombatant) -> dict:
    """The first figure in a ``state_json`` that is not the seated wizard."""
    return next(
        entry for entry in state["combatants"] if entry["combatant_id"] != seat.pk
    )


def foe_is_a_mage(state: dict, seat: BattleCombatant) -> None:
    """Teach the wizard's foe a spell, so the wizard may counter it."""
    foe_entry(state, seat).update(
        spells=[SPELL], spell_skill_levels={SPELL: 2}, mana=6, max_mana=6
    )


def wizard_pressing_a_countered_spell(state: dict, seat: BattleCombatant) -> None:
    """Teach the wizard Fire Missile and leave its cast at its one living
    foe standing countered.

    Every other foe is laid dead first: the engine aims a cast at the
    nearest enemy, and figures that choose before the wizard move, so with
    two foes which one is nearest at the wizard's choice is not fixed.
    """
    target = foe_entry(state, seat)
    for entry in state["combatants"]:
        if entry["combatant_id"] not in (seat.pk, target["combatant_id"]):
            entry.update(alive=False, conscious=False, body=0)
    wizard = entry_for(state, seat.pk)
    wizard.update(
        spells=sorted({*wizard.get("spells", []), SPELL}),
        spell_skill_levels={**wizard.get("spell_skill_levels", {}), SPELL: 2},
        mana=6,
        max_mana=6,
        contest={
            "spell_key": SPELL,
            "target_id": target["combatant_id"],
            "defender_ids": [target["combatant_id"]],
            "turns": 1,
        },
    )


def counters(pending: dict) -> list[dict]:
    """The counter options on a pending menu, least commitment first."""
    return sorted(
        (
            candidate
            for candidate in pending["candidates"]
            if candidate["letter"] == COUNTER_OPTION
        ),
        key=lambda candidate: candidate["counter_mana"],
    )


def status_events(battle: Battle, key: str) -> list:
    """The battle's status events whose payload carries ``key``."""
    return [
        event
        for event in battle.events.filter(event_type="status")
        if event.payload.get(key)
    ]


class CounterChoiceMatchTest(BaseTestCase):
    one = Candidate(COUNTER_OPTION, "COUNTER", 0.0, "", target_id=2, counter_mana=1)
    two = Candidate(COUNTER_OPTION, "COUNTER", 0.0, "", target_id=2, counter_mana=2)

    def test_a_counter_choice_names_its_commitment_only(self) -> None:
        choice = {"letter": COUNTER_OPTION, "target_id": 2, "counter_mana": 2}
        self.assertTrue(names_option(self.two.to_payload(), choice))
        self.assertFalse(names_option(self.one.to_payload(), choice))
        # A choice naming the caster but no commitment is not a counter.
        self.assertFalse(
            names_option(self.two.to_payload(), {**choice, "counter_mana": None})
        )
        self.assertIs(_match_candidate([self.one, self.two], choice), self.two)


class CounterMenuTest(BaseTestCase):
    """A seated wizard facing an enemy mage, paused on its decision."""

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(seated_rules(COUNTER_RULES))
        self.dm = self.create_dm()
        self.player = self.create_player()
        self.battle, self.seat = awaiting_wizard_battle(
            self.dm, self.player, before_turn=foe_is_a_mage
        )
        self.pending = self.battle.pending_input_json
        self.offered = counters(self.pending)
        self.foe = self.battle.combatants.get(
            pk=foe_entry(self.battle.state_json, self.seat)["combatant_id"]
        )

    def record(self, **choice):
        offered = self.offered[1]
        return record_player_action(
            self.battle.pk,
            self.player,
            combatant_id=self.seat.pk,
            turn=self.pending["turn"],
            **{
                "letter": offered["letter"],
                "target_id": offered["target_id"],
                "counter_mana": offered["counter_mana"],
                **choice,
            },
        )

    def test_the_menu_offers_each_commitment_against_the_mage_in_plain_words(
        self,
    ) -> None:
        self.assertEqual(
            [offered["counter_mana"] for offered in self.offered], COMMITMENTS
        )
        for offered in self.offered:
            with self.subTest(mana=offered["counter_mana"]):
                self.assertEqual(offered["target_id"], self.foe.pk)
                # The AI never counters: it is never the AI's pick.
                self.assertFalse(offered["ai_choice"])
                name = offered["target_name"]
                mana = offered["counter_mana"]
                self.assertEqual(
                    offered["plain"],
                    f"Counter {name}'s spell this turn, committing {mana} mana",
                )
                self.assertEqual(
                    offered["cost"], f"{mana} mana, spent whether {name} casts or not"
                )
                # The bonus the commitment buys is the engine's working.
                self.assertIn(
                    f"{mana // 2:+d} on the opposed roll", offered["rationale"]
                )

    def test_an_offered_counter_is_recorded_declared_and_paid(self) -> None:
        battle, _events = self.record()
        chosen = [
            event.payload["chosen"]
            for event in battle.events.filter(event_type="decision")
            if event.payload["chosen"].get("letter") == COUNTER_OPTION
        ]
        self.assertEqual(chosen[0]["counter_mana"], 2)
        self.assertTrue(chosen[0]["rationale"].startswith("chosen by"))
        (declared,) = status_events(battle, "counter_declared")
        self.assertEqual(declared.payload["caster"], self.foe.pk)
        self.assertEqual(declared.payload["mana"], 2)
        # Whatever the foe did, the counter was met or lapsed by the turn's
        # end: nothing of it is left in the saved state.
        met_or_lapsed = status_events(battle, "counter_result") + status_events(
            battle, "counter_lapsed"
        )
        self.assertEqual(len(met_or_lapsed), 1)
        saved = entry_for(Battle.objects.get(pk=battle.pk).state_json, self.seat.pk)
        self.assertIsNone(saved.get("counter"))

    def test_a_counter_the_menu_did_not_offer_is_refused(self) -> None:
        for choice in (
            {"counter_mana": COMMITMENTS[-1] + 1},
            {"counter_mana": 0},
            {"counter_mana": None},
            {"target_id": self.seat.pk},
        ):
            with (
                self.subTest(choice=choice),
                self.assertRaisesMessage(ValueError, "not on the pending menu"),
            ):
                self.record(**choice)

    def test_the_endpoint_reads_counter_mana(self) -> None:
        self.login_as(self.player)
        url = reverse("battle:act", args=[self.battle.pk])
        offered = self.offered[0]
        body = {
            "combatant_id": self.seat.pk,
            "turn": self.pending["turn"],
            "letter": offered["letter"],
            "target_id": offered["target_id"],
        }
        response = self.post_json(url, {**body, "counter_mana": "all of it"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "A numeric counter_mana is required")
        response = self.post_json(url, body)
        self.assertEqual(response.status_code, 400)
        response = self.post_json(
            url, {**body, "counter_mana": offered["counter_mana"]}
        )
        self.assertEqual(response.status_code, 200)

    def test_the_ai_hand_off_does_not_counter(self) -> None:
        battle, _events = record_player_action(
            self.battle.pk,
            self.dm,
            combatant_id=self.seat.pk,
            turn=self.pending["turn"],
            letter="",
            as_ai=True,
        )
        self.assertFalse(status_events(battle, "counter_declared"))


class EscalationMenuTest(BaseTestCase):
    """A seated wizard whose Fire Missile at its foe stands countered."""

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(seated_rules(COUNTER_RULES))
        self.dm = self.create_dm()
        self.player = self.create_player()
        self.battle, self.seat = awaiting_wizard_battle(
            self.dm, self.player, before_turn=wizard_pressing_a_countered_spell
        )
        self.pending = self.battle.pending_input_json
        contest = entry_for(self.battle.state_json, self.seat.pk)["contest"]
        self.foe = self.battle.combatants.get(pk=contest["target_id"])

    def escalations(self) -> list[dict]:
        return [
            candidate
            for candidate in self.pending["candidates"]
            if candidate.get("escalate")
        ]

    def test_the_contested_cast_reads_as_pressing_it_again(self) -> None:
        (pressed, *_pushed) = self.escalations()
        self.assertEqual(pressed["spell_key"], SPELL)
        self.assertEqual(pressed["target_id"], self.foe.pk)
        self.assertFalse(pressed["ai_choice"])
        self.assertEqual(
            pressed["plain"],
            f"Press your countered Fire Missile again at {pressed['target_name']}",
        )
        self.assertIn("another opposed roll", pressed["cost"])

    def test_an_unopposed_escalation_ends_the_contest_and_casts(self) -> None:
        (pressed, *_pushed) = self.escalations()
        battle, _events = record_player_action(
            self.battle.pk,
            self.player,
            combatant_id=self.seat.pk,
            turn=self.pending["turn"],
            letter=pressed["letter"],
            target_id=pressed["target_id"],
            spell_key=pressed["spell_key"],
        )
        (ended,) = status_events(battle, "contest_ended")
        self.assertEqual(ended.payload["reason"], "unopposed")
        saved = entry_for(Battle.objects.get(pk=battle.pk).state_json, self.seat.pk)
        self.assertIsNone(saved.get("contest"))

    def test_any_other_action_ends_the_contest(self) -> None:
        otherwise = next(
            candidate
            for candidate in self.pending["candidates"]
            if not candidate.get("spell_key") and candidate["letter"] != COUNTER_OPTION
        )
        battle, _events = record_player_action(
            self.battle.pk,
            self.player,
            combatant_id=self.seat.pk,
            turn=self.pending["turn"],
            letter=otherwise["letter"],
            target_id=otherwise.get("target_id"),
        )
        (ended,) = status_events(battle, "contest_ended")
        self.assertEqual(ended.payload["reason"], "another_action")

    def test_a_standing_contest_survives_a_pause(self) -> None:
        # The pause rolls the turn back: the contest is still on the record.
        saved = entry_for(self.battle.state_json, self.seat.pk)
        self.assertEqual(saved["contest"]["spell_key"], SPELL)
        self.assertEqual(saved["contest"]["turns"], 1)


class GameMasterCountersForAFoeTest(BaseTestCase):
    """A manual foe the battle's runner decides for: the same menu rule."""

    def test_the_runner_records_only_the_counter_offered(self) -> None:
        self.enterContext(seated_rules(COUNTER_RULES))
        dm = self.create_dm()
        battle = create_battle(dm, name="Pit", count=2, seed=9)
        foe, other = battle.combatants.order_by("pk")
        state = battle.state_json
        for figure in (foe, other):
            entry_for(state, figure.pk).update(
                spells=[SPELL], spell_skill_levels={SPELL: 2}, mana=6, max_mana=6
            )
        Battle.objects.filter(pk=battle.pk).update(state_json=state)
        set_combatant_control_mode(battle.pk, foe.pk, "manual")
        battle, _events = advance_turn(battle.pk)
        pending = battle.pending_input_json
        self.assertEqual(pending["combatant_id"], foe.pk)
        self.assertEqual(
            [offered["counter_mana"] for offered in counters(pending)], COMMITMENTS
        )

        def choose(counter_mana: int):
            return record_player_action(
                battle.pk,
                dm,
                combatant_id=foe.pk,
                turn=pending["turn"],
                letter=COUNTER_OPTION,
                target_id=other.pk,
                counter_mana=counter_mana,
            )

        with self.assertRaisesMessage(ValueError, "not on the pending menu"):
            choose(COMMITMENTS[-1] + 1)
        battle, _events = choose(COMMITMENTS[0])
        (declared,) = status_events(battle, "counter_declared")
        self.assertEqual(declared.payload["caster"], other.pk)


class CounterSnapshotTest(BaseTestCase):
    def test_every_counterspell_field_survives_dump_and_load(self) -> None:
        defender = make_combatant(
            1, counter=CounterState(2, 3, declared_turn=4), chosen_counter=(2, 3)
        )
        caster = make_combatant(2, contest=ContestState(SPELL, 1, [1], 2))
        state = BattleState(arena_radius=6, combatants=[defender, caster])
        loaded = load_state(dump_state(state), TARMAR_PROFILE)
        assert isinstance(loaded, BattleState)
        self.assertEqual(loaded.by_id(1).counter, defender.counter)
        self.assertEqual(loaded.by_id(1).chosen_counter, (2, 3))
        self.assertEqual(loaded.by_id(2).contest, caster.contest)


class TakenOffTheBoardTest(BaseTestCase):
    def test_a_figure_leaving_takes_its_counter_references_with_it(self) -> None:
        defender = make_combatant(
            1, counter=CounterState(2, 3, 1), chosen_counter=(2, 3)
        )
        pressing = make_combatant(3, contest=ContestState(SPELL, 4, [2, 1], 1))
        state = BattleState(
            arena_radius=6,
            combatants=[defender, make_combatant(2), pressing, make_combatant(4)],
        )
        _take_off_board(state, 2)
        self.assertIsNone(state.by_id(1).counter)
        self.assertIsNone(state.by_id(1).chosen_counter)
        self.assertEqual(state.by_id(3).contest, ContestState(SPELL, 4, [1], 1))


class CounterCardTest(BaseTestCase):
    """The roster card's contest line, from the state."""

    def cards(self, **fields) -> list[dict]:
        defender = make_combatant(1, name="Warden")
        caster = make_combatant(2, name="Hexer", **fields.get("caster", {}))
        state = BattleState(arena_radius=6, combatants=[defender, caster])
        with seated_rules(COUNTER_RULES):
            return tarmar_view(state, next_sequence=1)["combatants"]

    def test_a_spell_standing_countered_shows_who_and_how_often(self) -> None:
        for turns, times in ((1, "once"), (2, "2 times")):
            with self.subTest(turns=turns):
                warden, hexer = self.cards(
                    caster={"contest": ContestState(SPELL, 1, [1], turns)}
                )
                self.assertIsNone(warden["contest"])
                self.assertEqual(
                    hexer["contest"]["line"],
                    f"Fire Missile countered {times} by Warden; cast it again at "
                    "Warden to press it",
                )

    def test_a_contest_naming_figures_gone_from_the_board_still_shows(self) -> None:
        _warden, hexer = self.cards(
            caster={"contest": ContestState(SPELL, 99, [98], 1)}
        )
        self.assertEqual(hexer["contest"]["defenders"], [])
        self.assertEqual(
            hexer["contest"]["line"],
            "Fire Missile countered once; cast it again to press it",
        )
