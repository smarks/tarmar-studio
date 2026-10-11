"""The app-side profile seam (issue #251 slice 4).

Slice 3 made *storage* profile-aware and left everything above it Tarmar-only:
the turn loop, the survivor check, rename-by-combatant-id and the cast stage
all read through ``load_tarmar_state`` and refused a classic battle by name.
``battle.profiles`` is that refusal turned into an answer, so these pin both
halves — the Tarmar path unchanged (the other 440 battle tests are the real
proof of that), and a classic battle that now actually advances a turn.
"""

import json

from django.db import IntegrityError

from battle import profiles
from battle.access import Actor
from battle.classic_adaptation import classic_side, classic_state
from battle.models import Battle, BattleCombatant, BattleEvent
from battle.persistence import CLASSIC_PROFILE, TARMAR_PROFILE, dump_state
from battle.services import advance_turn, create_battle, rename_combatant
from battle.tests.test_classic_adaptation import snapshot as classic_snapshot
from battle.tests.test_persistence import classic_game
from battle.views import battle_payload
from common.test_helpers import BaseTestCase


def classic_wizard_battle(dm, *, seed: int = 11) -> Battle:
    """A stored classic battle whose second figure crossed as a wizard.

    ``classic_battle``'s fixture is two fighters, which is all the profile
    seam needed before the cast stage existed; a wizard has to be crossed
    from a studio caster (:func:`battle.classic_adaptation.classic_figures`)
    the way a real battle makes one.
    """
    battle = Battle.objects.create(
        name="Classic wizards",
        seed=seed,
        created_by=dm,
        rules_profile=CLASSIC_PROFILE,
    )
    state, _roster = classic_state(
        [
            classic_snapshot(1, "Ada"),
            classic_snapshot(2, "Wynn", intelligence=12, spells=("mage bolt",)),
        ],
        seed=seed,
    )
    for figure in state.figures:
        combatant = BattleCombatant.objects.create(
            battle=battle, display_name=figure.name
        )
        figure.uid = str(combatant.pk)
        # Teamless, so its side is its uid, spelled as a real crossing would.
        figure.side = classic_side("", combatant.pk)
    battle.state_json = dump_state(state)
    battle.initial_state_json = dump_state(state)
    battle.save()
    return battle


def classic_battle(dm, *, seed: int = 11) -> Battle:
    """A stored classic battle whose figures carry their row pks as uids.

    The app knows a combatant by its ``BattleCombatant`` pk and the engine by
    a string ``uid``; ``profiles.CLASSIC_UID_IS_ROW_PK`` is this app's
    contract that they are the same value, so a battle built here honours it.
    The fixture's sides ("red", "blue") become the rows' teams, spelled as
    ``classic_side`` spells a team's side (#930), since ``classic_team``
    reads no other spelling (#944).
    """
    battle = Battle.objects.create(
        name="Classic", seed=seed, created_by=dm, rules_profile=CLASSIC_PROFILE
    )
    game = classic_game()
    for figure in game.figures:
        combatant = BattleCombatant.objects.create(
            battle=battle, display_name=figure.name, team=figure.side
        )
        figure.uid = str(combatant.pk)
        figure.side = classic_side(combatant.team, combatant.pk)
    battle.state_json = dump_state(game)
    battle.initial_state_json = dump_state(game)
    battle.save()
    return battle


class AdapterLookupTest(BaseTestCase):
    def test_a_tarmar_battle_gets_the_tarmar_adapter(self):
        battle = create_battle(self.create_dm(), name="T", count=2, seed=7)
        adapter = profiles.for_battle(battle)
        self.assertIsInstance(adapter, profiles.TarmarProfile)
        self.assertEqual(adapter.name, TARMAR_PROFILE)

    def test_a_classic_battle_gets_the_classic_adapter(self):
        adapter = profiles.for_battle(classic_battle(self.create_dm()))
        self.assertIsInstance(adapter, profiles.ClassicProfile)
        self.assertEqual(adapter.name, CLASSIC_PROFILE)

    def test_an_unrunnable_profile_is_refused_by_name(self):
        battle = create_battle(self.create_dm(), name="T", count=2, seed=7)
        battle.rules_profile = "no-such-profile"
        with self.assertRaisesRegex(ValueError, "unknown rules profile"):
            profiles.for_battle(battle)

    def test_each_profile_brings_its_own_dice_source(self):
        from hexarena.dice import Dice

        from common.rolling import Roller

        dm = self.create_dm()
        tarmar = create_battle(dm, name="T", count=2, seed=7)
        self.assertIsInstance(profiles.for_battle(tarmar).roller(), Roller)
        classic = classic_battle(dm)
        self.assertIsInstance(profiles.for_battle(classic).roller(), Dice)

    def test_each_profile_brings_its_own_policy(self):
        from tarmar_engine.classic import policy as classic_policy

        from battle import policy as tarmar_policy

        dm = self.create_dm()
        tarmar = create_battle(dm, name="T", count=2, seed=7)
        self.assertIs(profiles.decider(tarmar), tarmar_policy.choose_option)
        classic = classic_battle(dm)
        self.assertIs(profiles.decider(classic), classic_policy.choose_option)


class ClassicTurnTest(BaseTestCase):
    """The point of the slice: a classic battle advances on this app's loop."""

    def test_a_classic_battle_advances_a_turn(self):
        battle = classic_battle(self.create_dm())
        battle, events = advance_turn(battle.pk)
        self.assertTrue(events)
        self.assertEqual(battle.turn_number, 1)
        self.assertIn(battle.status, {Battle.STATUS_PAUSED, Battle.STATUS_FINISHED})

    def test_the_battle_and_its_log_agree_about_which_turn_that_was(self):
        """The classic state counts the turn *ahead*; this app counts the last
        one played, as it does for Tarmar. Reporting the engine's own counter
        put the page's turn label one above the log group beneath it (#251)."""
        battle = classic_battle(self.create_dm())
        battle, events = advance_turn(battle.pk)
        self.assertEqual({event.turn for event in events}, {battle.turn_number})

    def test_the_view_and_the_payload_name_the_same_turn(self):
        """One response carried ``turn`` and ``view["turn"]`` a turn apart
        for a classic battle, because the view published the engine's own
        counter while everything else published the last turn completed
        (#661). Nothing read the view's copy, so the gap was a trap for the
        next consumer rather than a live fault — this is what closes it."""
        battle = classic_battle(self.create_dm())
        actor = Actor(user=battle.created_by, guest_key="")
        for _turn in range(3):
            battle, events = advance_turn(battle.pk)
            payload = battle_payload(battle, actor, events)
            self.assertEqual(payload["view"]["turn"], payload["turn"])
            self.assertEqual(payload["view"]["turn"], battle.turn_number)
            if battle.status in {Battle.STATUS_FINISHED, Battle.STATUS_STALEMATE}:
                break

    def test_a_tarmar_battle_agrees_the_same_way(self):
        """The Tarmar profile never had the gap; this says so, so a future
        change cannot close one profile's and open the other's."""
        battle = create_battle(self.create_dm(), name="Tarmar turn", count=2, seed=11)
        actor = Actor(user=battle.created_by, guest_key="")
        battle, events = advance_turn(battle.pk)
        payload = battle_payload(battle, actor, events)
        self.assertEqual(payload["view"]["turn"], payload["turn"])

    def test_the_victory_line_is_filed_under_the_turn_that_won(self):
        battle = classic_battle(self.create_dm())
        for _attempt in range(40):
            battle, _events = advance_turn(battle.pk)
            if battle.status == Battle.STATUS_FINISHED:
                break
        self.assertEqual(battle.status, Battle.STATUS_FINISHED)
        victory = battle.events.filter(message__contains="stands victorious").first()
        self.assertIsNotNone(victory)
        assert victory is not None
        self.assertEqual(victory.turn, battle.turn_number)

    def test_the_turn_logs_what_each_figure_considered(self):
        battle = classic_battle(self.create_dm())
        battle, events = advance_turn(battle.pk)
        decisions = [event for event in events if event.event_type == "decision"]
        self.assertEqual(len(decisions), 2)  # both fighters chose
        for decision in decisions:
            self.assertTrue(decision.payload["candidates"])
            self.assertIn(
                decision.payload["chosen"]["letter"],
                [candidate["letter"] for candidate in decision.payload["candidates"]],
            )

    def test_the_runners_log_lines_land_as_events(self):
        battle = classic_battle(self.create_dm())
        _battle, events = advance_turn(battle.pk)
        lines = [event for event in events if event.event_type == "info"]
        self.assertTrue(lines)
        self.assertTrue(all(event.message for event in lines))

    def test_event_sequences_are_unique_and_continue_across_turns(self):
        battle = classic_battle(self.create_dm())
        advance_turn(battle.pk)
        advance_turn(battle.pk)
        sequences = list(
            BattleEvent.objects.filter(battle=battle)
            .order_by("sequence")
            .values_list("sequence", flat=True)
        )
        self.assertEqual(sequences, sorted(set(sequences)))
        self.assertEqual(sequences, list(range(len(sequences))))

    def test_a_second_turn_cannot_reuse_a_written_sequence(self):
        # The uniqueness the numbering exists to keep: proved against the DB
        # constraint, not just by inspection.
        battle = classic_battle(self.create_dm())
        _battle, events = advance_turn(battle.pk)
        with self.assertRaises(IntegrityError):
            BattleEvent.objects.create(
                battle=battle,
                turn=1,
                phase=0,
                sequence=events[0].sequence,
                event_type="info",
                message="duplicate",
            )

    def test_the_state_is_re_snapshotted_as_classic_state(self):
        battle = classic_battle(self.create_dm())
        battle, _events = advance_turn(battle.pk)
        self.assertIn("figures", battle.state_json)
        # It survives a JSON round trip, which is what the column stores.
        json.dumps(battle.state_json)

    def test_the_same_seed_replays_the_same_turn(self):
        dm = self.create_dm()
        first = classic_battle(dm, seed=23)
        second = classic_battle(dm, seed=23)
        _first, first_events = advance_turn(first.pk)
        _second, second_events = advance_turn(second.pk)
        self.assertEqual(
            [event.message for event in first_events],
            [event.message for event in second_events],
        )


class ClassicRosterTest(BaseTestCase):
    def test_a_classic_combatant_renames_by_its_row_pk(self):
        battle = classic_battle(self.create_dm())
        combatant = battle.combatants.first()
        battle, combatant = rename_combatant(battle.pk, combatant.pk, "Renamed")
        adapter = profiles.for_battle(battle)
        state = adapter.load(battle.state_json)
        self.assertIn("Renamed", [figure.name for figure in state.figures])

    def test_renaming_a_combatant_the_state_does_not_hold_says_so(self):
        battle = classic_battle(self.create_dm())
        adapter = profiles.for_battle(battle)
        state = adapter.load(battle.state_json)
        with self.assertRaisesRegex(ValueError, "no combatant"):
            adapter.rename(state, 999_999, "Nobody")

    def test_survivors_and_names_read_off_the_classic_state(self):
        battle = classic_battle(self.create_dm())
        adapter = profiles.for_battle(battle)
        state = adapter.load(battle.state_json)
        survivors = adapter.survivors(state)
        self.assertEqual(
            sorted(adapter.actor_name(one) for one in survivors), ["Blue", "Red"]
        )
        self.assertEqual(
            sorted(adapter.actor_id(one) for one in survivors),
            sorted(battle.combatants.values_list("pk", flat=True)),
        )


class ClassicCastStageTest(BaseTestCase):
    def test_a_classic_fighter_still_draws_no_cast_stage(self):
        # The honest absence a non-caster has always had, kept: a fighter
        # gets no stage, in either profile.
        battle = classic_battle(self.create_dm())
        adapter = profiles.for_battle(battle)
        state = adapter.load(battle.state_json)
        combatant = battle.combatants.first()
        self.assertIsNone(adapter.caster_magic(state, combatant.pk))

    def test_a_classic_wizard_draws_its_own_stage(self):
        """The profile reaches the classic builder, in classic's words (#263).

        What the payload holds is pinned in ``test_cast_flow``; what is
        pinned here is that the profile seam leads to it at all — through a
        stored battle, loaded state and row pk, the way the page reaches it
        — and that nothing on the way dresses ST as mana.
        """
        battle = classic_wizard_battle(self.create_dm())
        adapter = profiles.for_battle(battle)
        state = adapter.load(battle.state_json)
        wizard = next(one for one in state.figures if one.spells_known)
        magic = adapter.caster_magic(state, int(wizard.uid))
        assert magic is not None
        self.assertEqual(magic["pool_label"], "ST")
        self.assertNotIn("mana", magic)
        self.assertTrue(magic["spells"])


class QuarantineTest(BaseTestCase):
    def test_the_sjg_subpackage_is_never_imported_at_module_level(self):
        from pathlib import Path

        source = Path(profiles.__file__).read_text()
        offenders = [
            line
            for line in source.splitlines()
            if line.startswith(("import ", "from ")) and "tarmar_engine.classic" in line
        ]
        self.assertFalse(
            offenders, "battle/profiles.py imports the SJG subpackage at module level"
        )


class ClassicVictoryTest(BaseTestCase):
    """The one event this app mints itself has to number like any other."""

    def _lone_survivor_battle(self, dm) -> Battle:
        battle = classic_battle(dm)
        adapter = profiles.for_battle(battle)
        state = adapter.load(battle.state_json)
        felled = state.figures[1]
        felled.damage_taken = felled.strength  # down and out of the fight
        battle.state_json = dump_state(state)
        battle.save(update_fields=["state_json"])
        return battle

    def test_the_last_one_standing_ends_the_battle(self):
        battle = self._lone_survivor_battle(self.create_dm())
        battle, events = advance_turn(battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_FINISHED)
        victory = [event for event in events if "stands victorious" in event.message]
        self.assertEqual(len(victory), 1)

    def test_the_victory_line_carries_the_next_free_sequence(self):
        battle = self._lone_survivor_battle(self.create_dm())
        _battle, events = advance_turn(battle.pk)
        sequences = sorted(event.sequence for event in events)
        self.assertEqual(sequences, list(range(len(sequences))))
        victory = next(
            event for event in events if "stands victorious" in event.message
        )
        self.assertEqual(victory.sequence, max(sequences))


class SeamContractTest(BaseTestCase):
    """A profile that forgets an answer fails loudly, not silently."""

    def test_the_base_adapter_answers_nothing_on_its_own(self):
        battle = create_battle(self.create_dm(), name="T", count=2, seed=7)
        base = profiles.BattleProfile(battle)
        for call in (
            lambda: base.roller(),
            lambda: base.run_turn(None, None, None),
            lambda: base.mint_event(None),
            lambda: base.survivors(None),
            lambda: base.actor_id(None),
            lambda: base.actor_name(None),
            lambda: base.turn_number(None),
            lambda: base.rename(None, 1, "x"),
            lambda: base.caster_magic(None, 1),
        ):
            with self.assertRaises(NotImplementedError):
                call()

    def test_a_classic_figure_whose_uid_is_not_a_row_pk_is_refused_by_name(self):
        battle = classic_battle(self.create_dm())
        adapter = profiles.for_battle(battle)
        state = adapter.load(battle.state_json)
        stray = state.figures[0]
        stray.uid = "not-a-pk"
        with self.assertRaisesRegex(ValueError, "not a BattleCombatant row pk"):
            adapter.actor_id(stray)
