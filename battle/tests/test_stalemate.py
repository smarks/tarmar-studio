"""Stalemate detection (#289) and restart-safe crossing notes (#288).

Two QA findings about what a battle's log owes its readers. #289: some
AI-vs-AI matchups are unwinnable by construction (armour the opponent's
best blow cannot exceed, combatants that can never reach each other), and
without detection the battle sits ``paused`` forever while the log grows
without bound — the app now ends such a battle honestly, as a stalemate.
#288: a classic battle's crossing notes ("Re-armed from the classic weapon
table ...") were deleted by restart and never rewritten, contradicting
``_record_crossing``'s own promise that they stay as long as the battle
does.

The golden-fingerprint tests pin the streams byte-for-byte against hashes
captured on origin/main *before* the stalemate window existed, so they
prove normal battles neither trip the window nor shift a single event.
"""

import hashlib

from battle.engine.state import BattleState
from battle.models import Battle, BattleEvent
from battle.persistence import dump_state
from battle.services import (
    STALEMATE_TURNS,
    advance_turn,
    create_battle,
    restart_battle,
)
from common.test_helpers import BaseTestCase
from common.test_helpers import make_beast as shared_make_beast

# Strong but barely damaging, so two of them can grind to a stalemate.
STUCK_BEAST_STATS = {
    "description": "A test beast.",
    "strength": 30,
    "dexterity": 12,
    "fatigue_pool": 30,
    "body_pool": 20,
    "movement": 8,
    "attacks": [{"name": "Bite", "damage": "1d6-2", "weapon_class": "Piercing"}],
}


def make_beast(slug, name, **overrides):
    """A catalog beast for constructing provably stuck matchups."""
    return shared_make_beast(slug=slug, name=name, **{**STUCK_BEAST_STATS, **overrides})


def advance_until_over(battle, cap=200):
    """Advance until the battle reaches a terminal status, or ``cap`` turns."""
    turns = 0
    while (
        battle.status not in (Battle.STATUS_FINISHED, Battle.STATUS_STALEMATE)
        and turns < cap
    ):
        battle, _events = advance_turn(battle.pk)
        turns += 1
    return battle


def event_stream(battle):
    """The battle's whole log as comparable tuples, in log order."""
    return [
        (
            event.turn,
            event.phase,
            event.sequence,
            event.event_type,
            event.actor,
            event.message,
        )
        for event in BattleEvent.objects.filter(battle=battle).order_by(
            "turn", "sequence"
        )
    ]


def stream_hash(battle):
    """A pinnable fingerprint of the battle's event stream."""
    lines = ["|".join(str(field) for field in row) for row in event_stream(battle)]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def final_event(battle) -> BattleEvent:
    """The last event in the battle's log — which must exist to be final."""
    event = (
        BattleEvent.objects.filter(battle=battle).order_by("turn", "sequence").last()
    )
    assert event is not None, "the battle has no events at all"
    return event


class ClassicStalemateTest(BaseTestCase):
    """The Giant Deer vs Stone Beetle class of matchup (#289 shape 2).

    Both hides stop more than the other's best bite can roll, so no blow
    can ever land: the battle is unwinnable by construction.
    """

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        make_beast("qa-stone-beetle", "Stone Beetle QA", armour_tier="Heavy", stops=5)
        make_beast("qa-giant-deer", "Giant Deer QA", armour_tier="Heavy", stops=5)
        self.battle = create_battle(
            self.dm,
            name="Hopeless",
            seed=18823,
            count=0,
            beast_slugs=("qa-stone-beetle", "qa-giant-deer"),
            rules_profile="classic-melee",
        )

    def test_hopeless_classic_battle_ends_in_stalemate(self):
        battle = advance_until_over(self.battle)
        self.assertEqual(battle.status, Battle.STATUS_STALEMATE)

        final = final_event(battle)
        self.assertIn("Stalemate", final.message)
        self.assertIn("Stone Beetle QA", final.message)
        self.assertIn("Giant Deer QA", final.message)

    def test_stalemate_is_terminal(self):
        battle = advance_until_over(self.battle)
        turn = battle.turn_number
        battle, events = advance_turn(battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_STALEMATE)
        self.assertEqual(battle.turn_number, turn)
        self.assertEqual(events, [])

    def test_stalemate_replays_identically_after_restart(self):
        battle = advance_until_over(self.battle)
        first_stream = event_stream(battle)
        first_turn = battle.turn_number

        restart_battle(battle.pk)
        battle = advance_until_over(Battle.objects.get(pk=battle.pk))

        self.assertEqual(battle.status, Battle.STATUS_STALEMATE)
        self.assertEqual(battle.turn_number, first_turn)
        self.assertEqual(event_stream(battle), first_stream)


class TarmarStalemateTest(BaseTestCase):
    """A Tarmar matchup that can never end (#289 shape 1's class).

    Two rooted beasts whose hides stop more than the other's bite can roll:
    they can neither reach each other nor hurt each other, so no turn will
    ever change anyone's pools.
    """

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        for slug, name in (("qa-beetle-a", "Beetle A"), ("qa-beetle-b", "Beetle B")):
            make_beast(
                slug,
                name,
                armour_tier="Heavy",
                stops=7,
                movement=0,
                attacks=[{"name": "Bite", "damage": "1d6", "weapon_class": "Piercing"}],
            )
        self.battle = create_battle(
            self.dm,
            name="Hopeless",
            seed=26244,
            count=0,
            beast_slugs=("qa-beetle-a", "qa-beetle-b"),
        )

    def test_hopeless_tarmar_battle_ends_in_stalemate(self):
        battle = advance_until_over(self.battle)
        self.assertEqual(battle.status, Battle.STATUS_STALEMATE)
        self.assertEqual(battle.turn_number, STALEMATE_TURNS)

        final = final_event(battle)
        self.assertIn("Stalemate", final.message)
        self.assertIn("Beetle A", final.message)
        self.assertIn("Beetle B", final.message)

    def test_stalemate_counts_consecutive_turns_only(self):
        """The window asks for *consecutive* quiet turns: progress resets it."""
        battle = advance_until_over(self.battle, cap=1)
        self.assertEqual(battle.turns_without_progress, 1)


class TeamStalemateTest(BaseTestCase):
    """A team battle stalls the same way a free-for-all does (#289)."""

    def test_team_battle_ends_in_stalemate(self):
        dm = self.create_dm()
        for slug, name in (
            ("qa-team-a1", "Red Beetle"),
            ("qa-team-b1", "Blue Beetle"),
        ):
            make_beast(
                slug,
                name,
                armour_tier="Heavy",
                stops=7,
                movement=0,
                attacks=[{"name": "Bite", "damage": "1d6", "weapon_class": "Piercing"}],
            )
        battle = create_battle(
            dm,
            name="Hopeless teams",
            seed=26244,
            count=0,
            beast_slugs=("qa-team-a1", "qa-team-b1"),
            beast_teams=("Red", "Blue"),
        )
        battle = advance_until_over(battle)
        self.assertEqual(battle.status, Battle.STATUS_STALEMATE)


class GoldenSeedsStillFinishTest(BaseTestCase):
    """Normal battles never trip the window — event streams byte-identical.

    The hashes were captured on origin/main (fdadf4c) before the stalemate
    window existed, over (turn, phase, sequence, event_type, message) in
    log order — so equality means the window changed nothing at all about
    a battle that finishes on its own.
    """

    def test_tarmar_golden_seed_unchanged(self):
        dm = self.create_dm()
        battle = create_battle(dm, name="Gold", count=4, seed=42)
        battle = advance_until_over(battle, cap=300)
        self.assertEqual(battle.status, Battle.STATUS_FINISHED)
        # Re-pinned for tarmar-engine v0.9.5 and the seat that fills its new
        # fields (143 turns -> 98). The first changed decision is turn 1's
        # first: Dorra Redfen, an archer overloaded to "Walk only, 1 hex max",
        # used to MOVE ("runs 1 hex") because the seat floored her barred Run
        # at her walk. Run is seated as 0 now (#778), MOVE needs it, and she
        # shoots Ulfan Ashvale instead. The same turn Harar Redfen, the
        # brawler, charges with the dagger it now carries (#867 ruling) where
        # it used to run bare-handed. Engine 0.9.5 alone, before the seat
        # filled anything, ended this seed at turn 61 (#820/#815).
        # (Previous re-pins: v0.9.3 for #301, v0.9.2 for #291/#292/#296.)
        #
        # Re-pinned for #883 (98 turns -> 37): a generated fighter now carries
        # only what its STR bears at a Medium load, shedding its shield, then
        # stepping its weapon down, then its armour (the review's ruling).
        # Ulfan Ashvale (armoured fighter, STR 10) carries a hammer and wears
        # leather instead of a mace, chainmail and a large shield; Dorra
        # Redfen (archer, STR 8) sheds her leather. Both were Overloaded. The
        # first changed line is turn 1's initiative (lighter armour, better
        # initiative), which moves Ulfan ahead of Yaria Hawkridge. The first
        # changed decision is turn 1's second: Ulfan, able to jog 5 hexes
        # now, CHARGES Dorra Redfen with the hammer, where he used to wait for
        # Yaria to jog to him and strike her with the mace. Yaria wins at 37.
        # (The first draft of the rule, armour before weapon, dealt cloth and
        # the mace and ended at 32; the two first part at turn 8, Harar
        # Redfen striking Yaria with ATTACK where he struck Ulfan in close.)
        #
        # Re-pinned for tarmar-engine v0.9.10 (#867, #819, #866; engine #19,
        # #20, #36). No decision and no die changed: every choice, roll and
        # line of the 824 is as it was, and Yaria still wins at 37. Three
        # forecast lines changed wording only, each a DISENGAGE whose forecast
        # now names the hex the engine expects to step to. The first is turn
        # 28's: "Dorra Redfen chooses DISENGAGE (n): Step away toward (1, 4)
        # instead of attacking". Nobody in this seed enters hand-to-hand, so
        # the HTH table (engine #19) never comes up.
        #
        # Re-pinned for tarmar-engine v0.9.16 (engine #37, #38). No decision
        # and no die changed: the 824 lines are the same except 22 movement
        # lines, each the old line with ", ending N hex(es) from its start"
        # added, N being the hexes it moved, since nobody here is blocked.
        # The first is turn 1's: "Ulfan Ashvale jogs 5 hex(es) toward Dorra
        # Redfen, ending 5 hex(es) from its start". Nobody enters
        # hand-to-hand, so the break-away scoring (engine #38) never comes up,
        # and Yaria still wins at 37.
        #
        # Re-pinned for tarmar-engine v0.9.20 (#153; engine #56, #57). No die
        # changed and Yaria still wins at 37, in 824 lines. One decision
        # changed: turn 8's, where Harar Redfen, now pricing a strike in close
        # with the natural 20's extra damage, strikes Ulfan Ashvale in close
        # (HTH ATTACK, t) rather than Yaria Hawkridge (ATTACK, j); the d20
        # shows 2 and misses either way, so its roll and action lines change
        # and nothing after them does. The other 38 changed lines are
        # forecasts: a hurt figure's now read "to hit less 1 injury" with the
        # bonus and P(hit) of the penalised roll, and turn 9's Dorra Redfen
        # forecasts her shot at Harar from the side he turned to on turn 8.
        self.assertEqual(battle.turn_number, 37)
        self.assertEqual(
            stream_hash(battle),
            "e245f0dbde03a26ca3863a28886e23609d97134b09bc15faa095a25b28ecb82c",
        )

    def test_classic_golden_seed_unchanged(self):
        dm = self.create_dm()
        battle = create_battle(
            dm, name="Gold", count=4, seed=42, rules_profile="classic-melee"
        )
        battle = advance_until_over(battle, cap=300)
        self.assertEqual(battle.status, Battle.STATUS_FINISHED)
        self.assertEqual(battle.turn_number, 37)
        # Every event after the crossing note plays as it did on
        # tarmar-engine 0.9.4: the classic profile plays the seed the same way.
        # Re-pinned for #909, which changed wording only: 170 of the 294
        # lines named a figure "Yaria Hawkridge (2)" and the win "The 2 hold
        # the field", the 2 being a BattleCombatant row pk. Every turn, phase,
        # sequence, type and actor is unchanged, and each changed message is
        # the old one with " (<pk>)" dropped and the win given the figure's
        # name. The stream no longer depends on which row ids the test
        # database hands out.
        lines = ["|".join(str(field) for field in row) for row in event_stream(battle)]
        self.assertEqual(
            hashlib.sha256("\n".join(lines[1:]).encode()).hexdigest(),
            "420517451bfcdd92bd081e6eda984c11db8729cca5f5eb6e6fb16a500b4e2751",
        )
        # The ending, checked as text and not only hashed (#909): the engine's
        # victory line names the winning figure, then the app's own.
        self.assertEqual(
            lines[-3:],
            [
                "37|0|291|info||🏆 Yaria Hawkridge holds the field — victory!",
                "37|0|292|info||— Turn 38 —",
                "37|6|293|info|Yaria Hawkridge|Yaria Hawkridge stands victorious",
            ],
        )
        # The crossing note alone changed, for #867: the brawler now brings
        # a dagger the classic table has by name, so it is no longer among
        # the re-armed (it was given the table's dagger before).
        self.assertEqual(
            lines[0],
            "1|0|0|info||Re-armed from the classic weapon table: Ulfan Ashvale, "
            "Dorra Redfen.",
        )
        self.assertEqual(
            stream_hash(battle),
            "7d4f6ae6e5477d315c240fe6e9eb453fea983b44cf288d23c6a0bc501cbaf2cb",
        )


class RestartKeepsCrossingNotesTest(BaseTestCase):
    """#288: the crossing notes survive a restart, exactly as filed.

    QA's restart-determinism check found the whole stream replays
    event-for-event *except* the crossing notes, which ``restart_battle``
    deleted with the rest of the log and never rewrote —
    against ``_record_crossing``'s own promise that they stay as long as
    the battle does.
    """

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.battle = create_battle(
            self.dm,
            name="repro",
            seed=21,
            count=4,
            archetypes=("wizard", "armoured fighter"),
            rules_profile="classic-melee",
            fighter_teams=("Red", "Red", "Blue", "Blue"),
        )

    def crossing_notes(self):
        return [
            (event.turn, event.phase, event.sequence, event.message)
            for event in BattleEvent.objects.filter(
                battle_id=self.battle.pk, payload__crossing=True
            ).order_by("sequence")
        ]

    def test_battle_opens_with_crossing_notes(self):
        notes = self.crossing_notes()
        self.assertTrue(notes)
        self.assertTrue(
            BattleEvent.objects.filter(
                battle_id=self.battle.pk, message__startswith="Re-armed"
            ).exists()
        )

    def test_restart_keeps_the_crossing_notes(self):
        notes_at_creation = self.crossing_notes()
        advance_turn(self.battle.pk)
        advance_turn(self.battle.pk)

        restart_battle(self.battle.pk)

        self.assertEqual(self.crossing_notes(), notes_at_creation)
        # And nothing else survived the restart.
        self.assertEqual(
            BattleEvent.objects.filter(battle_id=self.battle.pk).count(),
            len(notes_at_creation),
        )

    def test_restart_replay_is_identical_notes_included(self):
        """QA's restart-replay check, extended to assert the notes survive."""
        battle = advance_until_over(Battle.objects.get(pk=self.battle.pk))
        first_stream = event_stream(battle)
        first_status = battle.status

        restart_battle(battle.pk)
        battle = advance_until_over(Battle.objects.get(pk=battle.pk))

        self.assertEqual(battle.status, first_status)
        self.assertEqual(event_stream(battle), first_stream)


class GrappledCorpseBattleEndsTest(BaseTestCase):
    """A battle holding corpses in grapples reaches a verdict (#670).

    The third shape of unendable battle, and the one that reached
    production. Up to tarmar-engine v0.9.3 nothing cleared
    ``grappling``/``grappled_by`` when a combatant died or collapsed, so a
    grappler holding a corpse stayed locked to the shared hex for the rest
    of the battle and squeezed the body every turn. The stalemate window
    above could not catch it either: each landed squeeze drops the corpse's
    Fatigue, which is a turn that "hurt somebody", so
    ``turns_without_progress`` reset almost every turn and never reached
    :data:`STALEMATE_TURNS`. Two such pairs on opposite teams left a battle
    that could neither be won nor called a stalemate — reproduced over 60
    turns in #670 and fixed in the engine by smarks/tarmar-engine#11.

    The setup writes the holds straight into the saved state rather than
    playing a battle into that corner, because that is the shape a battle
    already stuck in production has when it is next loaded: the deaths
    happened under the old engine, so the release-on-death path never ran
    for them and the engine's stale-hold guard is what has to rescue them.
    """

    CAP = 120  # twice STALEMATE_TURNS: long enough that a stuck battle shows

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.battle = create_battle(
            self.dm,
            name="Locked on corpses",
            count=4,
            seed=4242,
            fighter_teams=("red", "blue", "red", "blue"),
        )
        state = BattleState.from_dict(self.battle.state_json)
        red_captor, blue_corpse, red_corpse, blue_captor = state.combatants
        self._hold_a_corpse(red_captor, blue_corpse)
        self._hold_a_corpse(blue_captor, red_corpse)
        self.captor_names = (red_captor.name, blue_captor.name)
        self.battle.state_json = dump_state(state)
        self.battle.status = Battle.STATUS_PAUSED
        self.battle.save(update_fields=["state_json", "status"])

    @staticmethod
    def _hold_a_corpse(captor, corpse) -> None:
        """Put ``corpse`` dead in ``captor``'s grip, sharing its hex."""
        corpse.alive = False
        corpse.conscious = False
        corpse.position = captor.position
        captor.grappling = corpse.combatant_id
        corpse.grappled_by = captor.combatant_id

    def test_the_battle_reaches_a_verdict(self):
        battle = advance_until_over(self.battle, cap=self.CAP)
        self.assertIn(battle.status, (Battle.STATUS_FINISHED, Battle.STATUS_STALEMATE))
        self.assertLess(battle.turn_number, self.CAP)

    def test_no_hold_survives_the_battle(self):
        battle = advance_until_over(self.battle, cap=self.CAP)
        state = BattleState.from_dict(battle.state_json)
        for combatant in state.combatants:
            self.assertIsNone(combatant.grappling, combatant.name)
            self.assertIsNone(combatant.grappled_by, combatant.name)

    def test_the_captors_leave_the_hex_they_were_locked_to(self):
        """The lock is what made the battle unendable: both captors were
        pinned to their corpse's hex and could never rejoin the fight."""
        start = {
            combatant.name: combatant.position
            for combatant in BattleState.from_dict(self.battle.state_json).combatants
        }
        battle = advance_until_over(self.battle, cap=self.CAP)
        end = {
            combatant.name: combatant.position
            for combatant in BattleState.from_dict(battle.state_json).combatants
        }
        for captor_name in self.captor_names:
            self.assertNotEqual(end[captor_name], start[captor_name], captor_name)

    def test_the_corpses_are_not_squeezed_for_the_rest_of_the_battle(self):
        """No Squeeze ever *lands*. The AI still forecasts one on the first
        turn, because the hold is on its books when Phase 3 picks the
        option; Phase 5 finds the target out of the fight, releases the
        stale hold, and throws no dice."""
        battle = advance_until_over(self.battle, cap=self.CAP)
        squeezes = BattleEvent.objects.filter(
            battle=battle, event_type="action", message__contains="squeezes"
        )
        self.assertEqual(list(squeezes), [])
