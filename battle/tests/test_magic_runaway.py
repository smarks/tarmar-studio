"""Runaway's later turns seated in the battle (tarmar-engine #30).

The engine feeds a Runaway on its caster each turn and offers the anchor as
an action, on the ``RunawayRules`` the canon injects. These pin the studio's
side: the pending menu offers the anchors the engine offered and reads them
in plain words; a player, a Game Master choosing for a foe, or the AI's
hand-off records only an anchor the menu offered, through the act endpoint
too; the Runaway survives the save and load between turns; the roster card
shows it; and the collapse and death it causes reach the character's record
on the paths that write wounds and deaths. Every number here is invented.
"""

from django.urls import reverse
from tarmar_engine.policy import ANCHOR_OPTION, Candidate

from battle.engine.state import BattleState, RunawayState
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
from characters.models import Character
from common.test_helpers import BaseTestCase, create_character

from .magic_fixtures import RUNAWAY_RULES, seated_rules
from .test_cast_flow import awaiting_wizard_battle
from .test_state import make_combatant

#: The spell running away in these tests, and the mana its casting cost: a
#: drain of 2 x 1 + 1 = 3 a turn under :data:`RUNAWAY_RULES`.
SPELL = "fire_missile"
ORIGINAL_COST = 1
DRAIN = 3


def runaway_entry(original_cost: int = ORIGINAL_COST) -> dict:
    """A ``RunawayState`` as the engine's snapshot writes it."""
    return {
        "spell_key": SPELL,
        "original_cost": original_cost,
        "turns_running": 0,
        "caught_from": None,
    }


def entry_for(state: dict, combatant_id: int) -> dict:
    """One combatant's entry in a ``state_json``."""
    return next(
        entry for entry in state["combatants"] if entry["combatant_id"] == combatant_id
    )


def running_on_the_wizard(state: dict, seat: BattleCombatant) -> None:
    """Start a Runaway on the seated wizard before the first turn."""
    entry_for(state, seat.pk)["runaway"] = runaway_entry()


def running_on_a_foe(state: dict, seat: BattleCombatant) -> None:
    """Start a Runaway on the first figure that is not the wizard."""
    foe = next(
        entry for entry in state["combatants"] if entry["combatant_id"] != seat.pk
    )
    foe["runaway"] = runaway_entry()


def anchors(pending: dict) -> list[dict]:
    """The anchor options on a pending menu."""
    return [
        candidate
        for candidate in pending["candidates"]
        if candidate["letter"] == ANCHOR_OPTION
    ]


class AnchorChoiceMatchTest(BaseTestCase):
    own = Candidate(ANCHOR_OPTION, "ANCHOR", 10.0, "", target_id=1, anchor_target=1)
    helping = Candidate(ANCHOR_OPTION, "ANCHOR", 0.0, "", target_id=2, anchor_target=2)

    def test_an_anchor_choice_names_its_runaway_only(self) -> None:
        choice = {"letter": ANCHOR_OPTION, "target_id": 2, "anchor_target": 2}
        self.assertTrue(names_option(self.helping.to_payload(), choice))
        self.assertFalse(names_option(self.own.to_payload(), choice))
        # A choice naming the target but not the Runaway is not the anchor.
        self.assertFalse(
            names_option(self.helping.to_payload(), {**choice, "anchor_target": None})
        )
        self.assertIs(_match_candidate([self.own, self.helping], choice), self.helping)


class SelfAnchorMenuTest(BaseTestCase):
    """A seated wizard feeding a Runaway, paused on its decision."""

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(seated_rules(RUNAWAY_RULES))
        self.dm = self.create_dm()
        self.player = self.create_player()
        self.battle, self.seat = awaiting_wizard_battle(
            self.dm, self.player, before_turn=running_on_the_wizard
        )
        self.pending = self.battle.pending_input_json
        (self.offered,) = anchors(self.pending)

    def record(self, **choice):
        return record_player_action(
            self.battle.pk,
            self.player,
            combatant_id=self.seat.pk,
            turn=self.pending["turn"],
            **{
                "letter": self.offered["letter"],
                "target_id": self.offered["target_id"],
                "anchor_target": self.offered["anchor_target"],
                **choice,
            },
        )

    def test_the_menu_offers_the_caster_its_own_anchor_in_plain_words(self) -> None:
        self.assertEqual(self.offered["anchor_target"], self.seat.pk)
        self.assertEqual(self.offered["target_id"], self.seat.pk)
        # The engine's AI always anchors its own: the panel tags it.
        self.assertTrue(self.offered["ai_choice"])
        self.assertEqual(
            self.offered["plain"],
            "Take back control of your runaway Fire Missile: 2d8 at or under your WIS",
        )
        self.assertEqual(self.offered["cost"], "")

    def test_an_offered_anchor_is_recorded_and_rolled(self) -> None:
        battle, _events = self.record()
        chosen = [
            event.payload["chosen"]
            for event in battle.events.filter(event_type="decision")
            if event.payload["chosen"].get("letter") == ANCHOR_OPTION
        ]
        self.assertEqual(chosen[0]["anchor_target"], self.seat.pk)
        self.assertTrue(chosen[0]["rationale"].startswith("chosen by"))
        rolls = [
            event
            for event in battle.events.filter(event_type="roll")
            if event.payload.get("purpose") == "anchor"
        ]
        self.assertEqual(len(rolls), 1)
        self.assertIn("vs WIS", rolls[0].message)
        outcomes = [
            event
            for event in battle.events.filter(event_type="status")
            if event.payload.get("anchor_target") == self.seat.pk
        ]
        self.assertEqual(len(outcomes), 1)
        self.assertIsNone(outcomes[0].payload["helper"])

    def test_an_anchor_the_menu_did_not_offer_is_refused(self) -> None:
        foe = next(
            combatant
            for combatant in self.battle.combatants.all()
            if combatant.pk != self.seat.pk
        )
        for choice in (
            {"anchor_target": foe.pk},
            {"anchor_target": None},
            {"target_id": foe.pk, "anchor_target": foe.pk},
        ):
            with (
                self.subTest(choice=choice),
                self.assertRaisesMessage(ValueError, "not on the pending menu"),
            ):
                self.record(**choice)

    def test_the_endpoint_reads_anchor_target(self) -> None:
        self.login_as(self.player)
        url = reverse("battle:act", args=[self.battle.pk])
        body = {
            "combatant_id": self.seat.pk,
            "turn": self.pending["turn"],
            "letter": self.offered["letter"],
            "target_id": self.offered["target_id"],
        }
        response = self.post_json(url, {**body, "anchor_target": "mine"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["error"], "A numeric anchor_target is required"
        )
        response = self.post_json(url, body)
        self.assertEqual(response.status_code, 400)
        response = self.post_json(
            url, {**body, "anchor_target": self.offered["anchor_target"]}
        )
        self.assertEqual(response.status_code, 200)

    def test_the_ai_hand_off_takes_the_anchor_it_marked(self) -> None:
        battle, _events = record_player_action(
            self.battle.pk,
            self.dm,
            combatant_id=self.seat.pk,
            turn=self.pending["turn"],
            letter="",
            as_ai=True,
        )
        recorded = [
            event.payload["chosen"]
            for event in battle.events.filter(event_type="decision")
            if event.payload["chosen"].get("letter") == ANCHOR_OPTION
        ]
        self.assertEqual(recorded[0]["anchor_target"], self.seat.pk)

    def test_a_turn_spent_otherwise_drains_and_the_runaway_is_saved(self) -> None:
        # A paused turn keeps nothing, so the drain is the committed turn's.
        otherwise = next(
            candidate
            for candidate in self.pending["candidates"]
            if candidate["letter"] != ANCHOR_OPTION and not candidate.get("spell_key")
        )
        battle, _events = self.record(
            letter=otherwise["letter"],
            target_id=otherwise.get("target_id"),
            anchor_target=None,
        )
        drains = [
            event
            for event in battle.events.filter(event_type="status")
            if event.payload.get("runaway_drain")
        ]
        self.assertEqual(len(drains), 1)
        self.assertEqual(drains[0].payload["drained_mana"], DRAIN)
        saved = entry_for(Battle.objects.get(pk=battle.pk).state_json, self.seat.pk)
        self.assertEqual(saved["runaway"], {**runaway_entry(), "turns_running": 1})
        state = load_state(battle.state_json, TARMAR_PROFILE)
        assert isinstance(state, BattleState)
        self.assertEqual(
            state.by_id(self.seat.pk).runaway,
            RunawayState(SPELL, ORIGINAL_COST, turns_running=1),
        )


class HelperAnchorMenuTest(BaseTestCase):
    """A seated wizard beside a foe feeding a Runaway."""

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(seated_rules(RUNAWAY_RULES))
        self.player = self.create_player()
        self.battle, self.seat = awaiting_wizard_battle(
            self.create_dm(), self.player, before_turn=running_on_a_foe
        )
        self.pending = self.battle.pending_input_json

    def test_the_wizard_is_offered_to_help_and_told_the_price(self) -> None:
        (offered,) = anchors(self.pending)
        self.assertNotEqual(offered["anchor_target"], self.seat.pk)
        self.assertEqual(offered["target_id"], offered["anchor_target"])
        # The AI never helps: its own pick is something else.
        self.assertFalse(offered["ai_choice"])
        self.assertTrue(
            offered["plain"].startswith(f"Help {offered['target_name']} take back")
        )
        self.assertTrue(offered["plain"].endswith(": 2d10 at or under your INT"))
        self.assertEqual(
            offered["cost"],
            "2 mana, paid whether it holds or not; if it fails, Fire Missile "
            "runs away from you too",
        )

    def test_an_offered_help_is_paid_and_rolled(self) -> None:
        (offered,) = anchors(self.pending)
        battle, _events = record_player_action(
            self.battle.pk,
            self.player,
            combatant_id=self.seat.pk,
            turn=self.pending["turn"],
            letter=offered["letter"],
            target_id=offered["target_id"],
            anchor_target=offered["anchor_target"],
        )
        outcome = next(
            event
            for event in battle.events.filter(event_type="status")
            if event.payload.get("helper") == self.seat.pk
        )
        self.assertEqual(outcome.payload["anchor_target"], offered["anchor_target"])
        self.assertEqual(outcome.payload["paid"], 2 * ORIGINAL_COST)


class GameMasterAnchorsForAFoeTest(BaseTestCase):
    """A manual foe the battle's runner decides for: the same menu rule."""

    def test_the_runner_records_only_the_anchor_offered(self) -> None:
        self.enterContext(seated_rules(RUNAWAY_RULES))
        dm = self.create_dm()
        battle = create_battle(dm, name="Pit", count=2, seed=9)
        foe = battle.combatants.order_by("pk").first()
        assert foe is not None
        state = battle.state_json
        entry_for(state, foe.pk).update(mana=DRAIN + 5, runaway=runaway_entry())
        Battle.objects.filter(pk=battle.pk).update(state_json=state)
        set_combatant_control_mode(battle.pk, foe.pk, "manual")
        battle, _events = advance_turn(battle.pk)
        pending = battle.pending_input_json
        self.assertEqual(pending["combatant_id"], foe.pk)
        (offered,) = anchors(pending)
        self.assertEqual(offered["anchor_target"], foe.pk)
        other = battle.combatants.exclude(pk=foe.pk).first()
        assert other is not None

        def choose(anchor_target: int):
            return record_player_action(
                battle.pk,
                dm,
                combatant_id=foe.pk,
                turn=pending["turn"],
                letter=ANCHOR_OPTION,
                target_id=anchor_target,
                anchor_target=anchor_target,
            )

        with self.assertRaisesMessage(ValueError, "not on the pending menu"):
            choose(other.pk)
        battle, _events = choose(foe.pk)
        self.assertTrue(
            [
                event
                for event in battle.events.filter(event_type="roll")
                if event.payload.get("purpose") == "anchor"
            ]
        )


class RunawaySnapshotTest(BaseTestCase):
    def test_every_runaway_field_survives_dump_and_load(self) -> None:
        caster = make_combatant(
            1,
            runaway=RunawayState(SPELL, ORIGINAL_COST, 2, caught_from=2),
            anchored_turns_running={"shield": 3},
            chosen_anchor=2,
        )
        state = BattleState(arena_radius=6, combatants=[caster, make_combatant(2)])
        loaded = load_state(dump_state(state), TARMAR_PROFILE)
        assert isinstance(loaded, BattleState)
        figure = loaded.by_id(1)
        self.assertEqual(figure.runaway, caster.runaway)
        self.assertEqual(figure.anchored_turns_running, {"shield": 3})
        self.assertEqual(figure.chosen_anchor, 2)


class TakenOffTheBoardTest(BaseTestCase):
    def test_a_figure_leaving_takes_its_runaway_references_with_it(self) -> None:
        caught = make_combatant(
            1, runaway=RunawayState(SPELL, ORIGINAL_COST, 1, caught_from=2)
        )
        helping = make_combatant(3, chosen_anchor=2)
        state = BattleState(
            arena_radius=6, combatants=[caught, make_combatant(2), helping]
        )
        _take_off_board(state, 2)
        runaway = state.by_id(1).runaway
        assert runaway is not None
        self.assertIsNone(runaway.caught_from)
        self.assertIsNone(state.by_id(3).chosen_anchor)
        # The rest of the Runaway stays with the figure it feeds on.
        self.assertEqual(runaway, RunawayState(SPELL, ORIGINAL_COST, 1))


class RunawayCardTest(BaseTestCase):
    """The roster card's Runaway line, from the state and the canon's rules."""

    def card(self, mana: int, turns_running: int, caught_from: int | None = None):
        caster = make_combatant(
            1,
            name="Caster",
            mana=mana,
            runaway=RunawayState(SPELL, ORIGINAL_COST, turns_running, caught_from),
        )
        state = BattleState(
            arena_radius=6, combatants=[caster, make_combatant(2, name="Ally")]
        )
        with seated_rules(RUNAWAY_RULES):
            view = tarmar_view(state, next_sequence=1)
        return view["combatants"][0]["runaway"], view["combatants"][1]["runaway"]

    def test_where_the_drain_comes_from_follows_the_pool(self) -> None:
        for mana, draining in (
            (DRAIN, "mana"),
            (DRAIN - 1, "mana, then body"),
            (0, "body"),
        ):
            with self.subTest(mana=mana):
                runaway, nobody = self.card(mana, 2)
                self.assertIsNone(nobody)
                self.assertEqual(runaway["draining"], draining)
                self.assertEqual(
                    runaway["line"],
                    f"Runaway Fire Missile: has fed for 2 turns; it takes {DRAIN} "
                    f"a turn from {draining}",
                )

    def test_a_runaway_caught_from_a_figure_gone_from_the_board_still_shows(
        self,
    ) -> None:
        runaway, _nobody = self.card(DRAIN, 1, caught_from=99)
        self.assertIsNone(runaway["caught_from"])
        self.assertEqual(
            runaway["line"],
            f"Runaway Fire Missile: has fed for 1 turn; it takes {DRAIN} a turn "
            "from mana",
        )

    def test_a_new_and_a_caught_runaway_say_so(self) -> None:
        runaway, _nobody = self.card(DRAIN, 0, caught_from=2)
        self.assertEqual(
            runaway["line"],
            f"Runaway Fire Missile: began this turn, caught from Ally; it takes "
            f"{DRAIN} a turn from mana",
        )


class RunawayWriteBackTest(BaseTestCase):
    """A Game Master's battle: the drain's collapse and death reach the record
    on the paths that write wounds and deaths (#750, #751)."""

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(seated_rules(RUNAWAY_RULES))
        self.character = create_character(self.create_user("player"), name="Aldwin")
        self.battle = create_battle(
            self.create_dm(),
            name="Duel",
            count=1,
            seed=3,
            character_ids=(self.character.pk,),
        )
        self.seat = self.battle.combatants.get(character=self.character)

    def run_with_runaway(self, original_cost: int) -> BattleState:
        """One turn with an emptied Aldwin feeding a Runaway; the state after."""
        state = self.battle.state_json
        entry = entry_for(state, self.seat.pk)
        entry.update(mana=0, runaway=runaway_entry(original_cost))
        Battle.objects.filter(pk=self.battle.pk).update(state_json=state)
        battle, _events = advance_turn(self.battle.pk)
        loaded = load_state(battle.state_json, TARMAR_PROFILE)
        assert isinstance(loaded, BattleState)
        return loaded

    def test_the_drain_on_body_is_a_wound_on_the_record(self) -> None:
        figure = self.run_with_runaway(ORIGINAL_COST).by_id(self.seat.pk)
        self.assertFalse(figure.conscious)
        self.character.refresh_from_db()
        _fatigue, body = self.character.pools()
        self.assertEqual(self.character.body_damage, DRAIN)
        self.assertEqual(body - self.character.body_damage, figure.body)

    def test_a_drain_past_the_death_line_kills_the_character(self) -> None:
        figure = self.run_with_runaway(100).by_id(self.seat.pk)
        self.assertFalse(figure.alive)
        self.assertIsNone(figure.runaway)
        character = Character.objects.get(pk=self.character.pk)
        self.assertFalse(character.is_alive)
        self.assertIn("Duel", character.death_cause)
