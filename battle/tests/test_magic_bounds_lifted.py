"""A lifted magic bound reaches the menu, the cast, the counter and the log (#37).

Slice A recorded the ruling on the battle (``test_magic_bounds``). Here the
Tarmar profile writes it onto each figure's ``lifted_magic_bounds`` every turn
(tarmar-engine v0.9.17), the engine's menu then offers that figure the wider
amounts and runs them, the studio's own readings (the cast stage, the turn
menu's cost line) follow it through the engine's methods, and a cast or
counter that went past a lifted bound says so in the battle log and the GM
battle report. Every rule here is invented (``magic_fixtures``), so the tests
read the same under either canon; the e2e spec lifts what the loaded canon
imposes.
"""

from dataclasses import replace

from django.test import SimpleTestCase
from django.utils.html import escape
from django.utils.text import capfirst
from tarmar_engine.engine import TurnRunner
from tarmar_engine.magic import LIFTED_BOUNDS_KEY
from tarmar_engine.policy import Candidate, choose_option
from tarmar_engine.profile import TarmarProfile as EngineTarmarProfile

from battle import magic_bounds, profiles, turn_menu
from battle.access import Actor
from battle.engine.state import BattleState
from battle.models import Battle
from battle.persistence import dump_state, load_state
from battle.services import (
    advance_turn,
    caster_magic,
    create_battle,
    set_magic_bounds_lifted,
)
from common.rolling import Roller
from common.test_helpers import BaseTestCase

from .magic_fixtures import (
    BORROWING_RULES,
    COUNTER_RULES,
    INVENTED_RULES,
    rules_with_channel,
    seated_rules,
)
from .rulings_words import with_fixed_words
from .test_cast_flow import (
    WIZARD_SPELL_LEVELS,
    assert_stage_agrees_with_menu,
    wizard_state,
)
from .test_engine import ScriptedRoller
from .test_magic_narration import as_row, battle_page_text, gm_report_text
from .test_state import make_combatant

CHANNEL = magic_bounds.CHANNEL
PUSH_CAP = magic_bounds.PUSH_CAP
COUNTER_BOUNDS = magic_bounds.COUNTER_BOUNDS

#: Lightning Bolt (3 mana) at recorded level 1: the invented Channel of twice
#: the level is 2, so the cast is past it.
BOLT_PAST_CHANNEL = {**WIZARD_SPELL_LEVELS, "lightning_bolt": 1}


def record_lifting(combatant_id: int, *bounds: str) -> magic_bounds.LiftedBounds:
    """A ruling lifting ``bounds`` for one figure."""
    return magic_bounds.LiftedBounds().lifted(
        combatant_id, bounds, by="gm", by_id=1, at="2026-10-18T10:00:00+00:00"
    )


def lifted(state: BattleState, combatant_id: int, *bounds: str) -> BattleState:
    """``state`` with ``bounds`` lifted for one figure, as the profile fills it."""
    magic_bounds.fill(state, record_lifting(combatant_id, *bounds))
    return state


def menu(state: BattleState, combatant_id: int = 1) -> list[Candidate]:
    """The engine's menu for one figure, on the seated rules."""
    actor = state.by_id(combatant_id)
    return choose_option(
        state, actor, magic=profiles.tarmar_engine_profile().magic
    ).candidates


def counter_state() -> BattleState:
    """A defender mage (5 mana) facing an enemy caster."""
    return BattleState(
        arena_radius=6,
        combatants=[
            make_combatant(1, name="Defender", mana=5, max_mana=5, spells=["shield"]),
            make_combatant(
                2,
                name="Caster",
                q=3,
                r=0,
                mana=6,
                max_mana=6,
                spells=["fire_missile"],
            ),
        ],
    )


class FillTest(SimpleTestCase):
    """The ruling written onto the engine's snapshot, and read back."""

    def test_each_figure_carries_its_own_bounds_and_a_refill_clears_them(self):
        state = wizard_state()
        magic_bounds.fill(state, record_lifting(1, COUNTER_BOUNDS, CHANNEL))
        self.assertEqual(state.by_id(1).lifted_magic_bounds, [CHANNEL, COUNTER_BOUNDS])
        self.assertEqual(state.by_id(2).lifted_magic_bounds, [])
        magic_bounds.fill(state, magic_bounds.LiftedBounds())
        self.assertEqual(state.by_id(1).lifted_magic_bounds, [])

    def test_the_field_survives_dump_and_load(self):
        state = lifted(wizard_state(), 1, CHANNEL)
        reloaded = load_state(dump_state(state), "tarmar")
        self.assertEqual(reloaded.by_id(1).lifted_magic_bounds, [CHANNEL])

    def test_a_snapshot_from_before_the_field_loads_with_nothing_lifted(self):
        """A battle saved before tarmar-engine v0.9.17 carries no such key."""
        snapshot = dump_state(wizard_state())
        for combatant in snapshot["combatants"]:
            combatant.pop("lifted_magic_bounds", None)
            self.assertNotIn("lifted_magic_bounds", combatant)
        reloaded = load_state(snapshot, "tarmar")
        for figure in reloaded.combatants:
            self.assertEqual(figure.lifted_magic_bounds, [])

    def test_the_profile_fills_it_every_turn_and_for_the_cast_stage(self):
        battle = Battle(magic_bounds_lifted_json=record_lifting(1, CHANNEL).to_json())
        profile = profiles.TarmarProfile(battle)
        with seated_rules(INVENTED_RULES):
            state = wizard_state(spell_skill_levels=BOLT_PAST_CHANNEL)
            profile.run_turn(state, Roller(seed=3), profiles.decider(battle))
            self.assertEqual(state.by_id(1).lifted_magic_bounds, [CHANNEL])
            # The cast stage reads the ruling as it stands, not as the state
            # last had it.
            stale = wizard_state(spell_skill_levels=BOLT_PAST_CHANNEL)
            magic = profile.caster_magic(stale, 1)
        assert magic is not None
        bolt = next(row for row in magic["spells"] if row["key"] == "lightning_bolt")
        self.assertTrue(bolt["castable"])


@with_fixed_words
class LiftedChannelTest(SimpleTestCase):
    """Channel lifted: the cast past it is offered, shown castable and noted."""

    def setUp(self):
        super().setUp()
        self.enterContext(seated_rules(INVENTED_RULES))

    def bolts(self, state: BattleState) -> list[Candidate]:
        return [
            candidate
            for candidate in menu(state)
            if candidate.spell_key == "lightning_bolt" and not candidate.push_mana
        ]

    def test_held_the_cast_past_channel_is_withheld_everywhere(self):
        state = wizard_state(spell_skill_levels=BOLT_PAST_CHANNEL)
        self.assertEqual(self.bolts(state), [])
        magic = caster_magic(state, 1)
        assert magic is not None
        bolt = next(row for row in magic["spells"] if row["key"] == "lightning_bolt")
        self.assertFalse(bolt["castable"])

    def test_lifted_the_menu_offers_the_cast_past_it_and_says_so(self):
        state = lifted(wizard_state(spell_skill_levels=BOLT_PAST_CHANNEL), 1, CHANNEL)
        (bolt,) = self.bolts(state)
        magic = caster_magic(state, 1)
        assert magic is not None
        row = next(row for row in magic["spells"] if row["key"] == "lightning_bolt")
        self.assertTrue(row["castable"])
        # No Channel bounds the cast now, so the push control names none.
        self.assertIsNone(row["channel"])
        described = turn_menu.describe(state, state.by_id(1), bolt, ai_choice=False)
        self.assertTrue(
            described.cost.endswith(
                "; magic_bounds.past_by_ruling magic_bounds.channel"
            ),
            described.cost,
        )
        # Another figure's menu is unchanged: Fire Missile notes nothing.
        missile = next(
            candidate
            for candidate in menu(state)
            if candidate.spell_key == "fire_missile"
        )
        self.assertNotIn(
            "past_by_ruling",
            turn_menu.describe(state, state.by_id(1), missile, ai_choice=False).cost,
        )

    def test_the_cast_runs_names_its_channel_and_the_log_says_so(self):
        state = lifted(wizard_state(spell_skill_levels=BOLT_PAST_CHANNEL), 1, CHANNEL)
        (bolt,) = self.bolts(state)
        caster = state.by_id(1)
        caster.chosen_letter = bolt.letter
        caster.chosen_spell = bolt.spell_key
        caster.chosen_target = bolt.target_id
        events: list[dict] = []
        TurnRunner(
            state,
            ScriptedRoller([(1, 1, 1)]),
            events.append,
            profile=EngineTarmarProfile(magic=INVENTED_RULES),
        ).cast_spell(caster)
        (cast,) = [
            event for event in events if LIFTED_BOUNDS_KEY in (event["payload"] or {})
        ]
        self.assertEqual(cast["payload"][LIFTED_BOUNDS_KEY], [CHANNEL])
        self.assertEqual(cast["payload"]["channel"], 2)
        # The option carried the engine's answer before it ran.
        self.assertEqual(bolt.lifted_bounds, (CHANNEL,))
        line = capfirst(magic_bounds.past_by_ruling([CHANNEL]))
        rows = [as_row(event) for event in events]
        self.assertIn(escape(line), battle_page_text(rows))
        self.assertIn(f"  - {line}", gm_report_text(rows))
        # Exactly the one line: nothing else carries it.
        self.assertEqual(battle_page_text(rows).count("event-past-by-ruling"), 1)


class LiftedChannelBorrowingAgreesWithTheMenuTest(SimpleTestCase):
    """The cast stage's borrowable rows equal the engine menu's borrowing
    copies with Channel lifted or held, with and without a debt cap: the
    stage bounds a borrowing cast by the engine's
    :meth:`~tarmar_engine.magic.MagicRules.channel_bound` with ``borrowing``,
    which with Channel lifted and no cap lets the cast take the purse plus the
    Channel."""

    def test_every_purse_cap_and_ruling(self):
        assert BORROWING_RULES.borrowing is not None
        for max_debt in (None, 3):
            rules = replace(
                BORROWING_RULES,
                borrowing=replace(BORROWING_RULES.borrowing, max_debt=max_debt),
            )
            for mana in (0, 1, 2):
                for channel_lifted in (False, True):
                    with (
                        self.subTest(
                            max_debt=max_debt, mana=mana, lifted=channel_lifted
                        ),
                        seated_rules(rules),
                    ):
                        state = wizard_state(
                            mana=mana, spell_skill_levels=BOLT_PAST_CHANNEL
                        )
                        if channel_lifted:
                            lifted(state, 1, CHANNEL)
                        assert_stage_agrees_with_menu(self, state)


@with_fixed_words
class LiftedPushCapTest(SimpleTestCase):
    """Push's own cap lifted: pushes past it are offered and noted."""

    def setUp(self):
        super().setUp()
        # A Channel of ten times the level, so only the push cap of 2 binds.
        self.enterContext(seated_rules(rules_with_channel(multiplier=10)))

    def pushes(self, state: BattleState) -> list[int]:
        return sorted(
            candidate.push_mana
            for candidate in menu(state)
            if candidate.spell_key == "fire_missile" and candidate.push_mana
        )

    def test_held_the_cap_holds_lifted_the_purse_bounds_it(self):
        state = wizard_state(mana=8)
        self.assertEqual(self.pushes(state), [1, 2])
        lifted_state = lifted(wizard_state(mana=8), 1, PUSH_CAP)
        # Fire Missile costs 1: the rest of a purse of 8 may be pushed.
        self.assertEqual(self.pushes(lifted_state), list(range(1, 8)))
        past = next(
            candidate
            for candidate in menu(lifted_state)
            if candidate.spell_key == "fire_missile" and candidate.push_mana == 3
        )
        self.assertEqual(past.lifted_bounds, (PUSH_CAP,))


@with_fixed_words
class LiftedCounterBoundsTest(SimpleTestCase):
    """The counter's commitment bounds lifted: from nothing to the purse."""

    def setUp(self):
        super().setUp()
        self.enterContext(seated_rules(COUNTER_RULES))

    def commitments(self, state: BattleState) -> list[int]:
        return sorted(
            candidate.counter_mana
            for candidate in menu(state)
            if candidate.counter_mana is not None
        )

    def test_held_one_to_three_lifted_nothing_to_the_purse(self):
        self.assertEqual(self.commitments(counter_state()), [1, 2, 3])
        self.assertEqual(
            self.commitments(lifted(counter_state(), 1, COUNTER_BOUNDS)),
            [0, 1, 2, 3, 4, 5],
        )

    def test_a_counter_past_the_bounds_runs_and_the_log_says_so(self):
        state = lifted(counter_state(), 1, COUNTER_BOUNDS)
        defender = state.by_id(1)
        defender.chosen_counter = (2, 5)
        events: list[dict] = []
        TurnRunner(
            state,
            ScriptedRoller([]),
            events.append,
            profile=EngineTarmarProfile(magic=COUNTER_RULES),
        ).declare_counter(defender)
        (declared,) = [
            event for event in events if LIFTED_BOUNDS_KEY in (event["payload"] or {})
        ]
        self.assertEqual(declared["payload"][LIFTED_BOUNDS_KEY], [COUNTER_BOUNDS])
        line = capfirst(magic_bounds.past_by_ruling([COUNTER_BOUNDS]))
        rows = [as_row(event) for event in events]
        self.assertIn(escape(line), battle_page_text(rows))
        self.assertIn(f"  - {line}", gm_report_text(rows))


#: Each option a lifted bound puts on the menu, one case per bound and one
#: for Channel and the push cap together: the rules, the bounds lifted, the
#: figure's purse and spell levels, and how to pick the option off the menu.
LIFTED_MENU_CASES = (
    (
        "Channel",
        INVENTED_RULES,
        (CHANNEL,),
        {"spell_skill_levels": BOLT_PAST_CHANNEL},
        lambda option: option.spell_key == "lightning_bolt" and not option.push_mana,
    ),
    (
        "the push cap",
        rules_with_channel(multiplier=10),
        (PUSH_CAP,),
        {"mana": 8},
        lambda option: option.spell_key == "fire_missile" and option.push_mana == 3,
    ),
    (
        "Channel and the push cap",
        INVENTED_RULES,
        (CHANNEL, PUSH_CAP),
        {"mana": 8},
        # Fire Missile at level 1, Channel 2: 1 + 3 pushed is past both.
        lambda option: option.spell_key == "fire_missile" and option.push_mana == 3,
    ),
)


@with_fixed_words
class MenuNoteEqualsTheEnginePayloadTest(SimpleTestCase):
    """For every option a lifted bound puts on the menu, the bounds the turn
    menu's cost line names are the bounds the engine's event lists when the
    option runs: both read the engine's one answer
    (``Candidate.lifted_bounds`` and :data:`LIFTED_BOUNDS_KEY`)."""

    def assert_note_names(self, state, option, payload_bounds: list[str]) -> None:
        described = turn_menu.describe(state, state.by_id(1), option, ai_choice=False)
        self.assertTrue(payload_bounds)
        self.assertEqual(list(option.lifted_bounds), payload_bounds)
        # The described option, which the page is sent, keeps the answer.
        self.assertEqual(described.lifted_bounds, option.lifted_bounds)
        self.assertTrue(
            described.cost.endswith(f"; {magic_bounds.past_by_ruling(payload_bounds)}"),
            described.cost,
        )

    def test_each_cast_past_a_lifted_bound(self):
        for label, rules, bounds, wizard, picks in LIFTED_MENU_CASES:
            with self.subTest(label), seated_rules(rules):
                state = lifted(wizard_state(**wizard), 1, *bounds)
                option = next(option for option in menu(state) if picks(option))
                caster = state.by_id(1)
                caster.chosen_letter = option.letter
                caster.chosen_spell = option.spell_key
                caster.chosen_target = option.target_id
                caster.chosen_push_mana = option.push_mana
                events: list[dict] = []
                TurnRunner(
                    state,
                    ScriptedRoller([(1, 1, 1)]),
                    events.append,
                    profile=EngineTarmarProfile(magic=rules),
                ).cast_spell(caster)
                (cast,) = [
                    event
                    for event in events
                    if LIFTED_BOUNDS_KEY in (event["payload"] or {})
                ]
                self.assert_note_names(
                    state, option, cast["payload"][LIFTED_BOUNDS_KEY]
                )

    def test_a_counter_past_its_lifted_bounds(self):
        with seated_rules(COUNTER_RULES):
            state = lifted(counter_state(), 1, COUNTER_BOUNDS)
            option = next(option for option in menu(state) if option.counter_mana == 5)
            defender = state.by_id(1)
            defender.chosen_counter = (option.target_id, option.counter_mana)
            events: list[dict] = []
            TurnRunner(
                state,
                ScriptedRoller([]),
                events.append,
                profile=EngineTarmarProfile(magic=COUNTER_RULES),
            ).declare_counter(defender)
            (declared,) = [
                event
                for event in events
                if LIFTED_BOUNDS_KEY in (event["payload"] or {})
            ]
            self.assert_note_names(
                state, option, declared["payload"][LIFTED_BOUNDS_KEY]
            )


class RulingReachesTheTurnTest(BaseTestCase):
    """On a real battle: the record reaches the engine's snapshot each turn,
    and a figure held again is held from the next turn."""

    def test_lifting_and_holding_again_follow_the_record(self):
        dm = self.create_dm()
        battle = create_battle(dm, name="Pit", count=2, seed=5)
        figure = battle.combatants.order_by("pk").first()
        bound = magic_bounds.offered_bounds()[0]
        game_master = Actor(user=dm, guest_key="")

        def entry(battle: Battle) -> dict:
            (found,) = [
                combatant
                for combatant in battle.state_json["combatants"]
                if combatant["combatant_id"] == figure.pk
            ]
            return found

        set_magic_bounds_lifted(
            battle.pk, game_master, combatant_id=figure.pk, bounds=[bound], lifted=True
        )
        battle, _events = advance_turn(battle.pk)
        self.assertEqual(entry(battle)["lifted_magic_bounds"], [bound])
        set_magic_bounds_lifted(
            battle.pk, game_master, combatant_id=figure.pk, bounds=[bound], lifted=False
        )
        battle, _events = advance_turn(battle.pk)
        self.assertNotIn("lifted_magic_bounds", entry(battle))
