"""The casting success tier, the Control Roll, the debt roll, a withheld
cast, a Runaway's later turns and a Counterspell, as read.

Issue #960: the engine narrates each with the labels the canon's rules give
it (a tier's ``label``, a failure band's ``label``, and since tarmar-engine
#28 a failed debt roll's ``label``) and its own words only for held,
failed, borrows, debt and a Runaway. A Runaway's later turns (tarmar-engine
#30) carry no injected label: the drain, the collapse, the death and the
anchor roll are the engine's own words, and so is a Counterspell
(tarmar-engine #31): the declaration, the opposed roll, its result, a
lapsed counter and the contest's end. These pin that every such line
reaches both readers of a battle's log, the battle page
(``battle/_event.html``) and the GM battle report
(:func:`gm.battle_report.turn_block`). Every label and number here is
invented.
"""

from dataclasses import replace

from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.utils.html import escape
from tarmar_engine.engine import TurnRunner
from tarmar_engine.magic import BorrowingFailure, BorrowingRules, CastingSuccessTier
from tarmar_engine.policy import choose_option
from tarmar_engine.profile import TarmarProfile as EngineTarmarProfile
from tarmar_engine.state import BattleState, ContestState, RunawayState

from battle.models import Battle, BattleEvent
from gm.battle_report import turn_block

from .magic_fixtures import COUNTER_RULES, INVENTED_RULES, RUNAWAY_RULES
from .test_engine import ScriptedRoller
from .test_state import make_combatant

RULES = replace(
    INVENTED_RULES,
    casting_success_tiers=(
        CastingSuccessTier(
            key="gleam",
            label="Gleaming cast",
            natural_totals=frozenset({3}),
            effect_bonus=1,
        ),
    ),
)

#: The casting roll's faces: a natural 3, the invented tier, against INT 14.
TIER_CASTING_FACES = (1, 1, 1)
#: Control Roll faces against INT 14 less one pushed mana, a target of 13.
HELD_FACES = (2, 2, 2)
WAVER_FACES = (5, 5, 4)  # 14: fails by 1, the "Waver" band
BOLT_FACES = (6, 6, 4)  # 16: fails by 3, the "Bolt" band, a Runaway


def pushed_cast_events(control_faces: tuple[int, int, int]) -> list[BattleEvent]:
    """One pushed Fire Missile's events under :data:`RULES`, as log rows.

    Args:
        control_faces: The Control Roll's dice.
    """
    caster = make_combatant(
        1,
        name="Caster",
        intelligence=14,
        mana=6,
        max_mana=6,
        spells=["fire_missile"],
        spell_skill_levels={"fire_missile": 2},
        chosen_spell="fire_missile",
        chosen_target=2,
        chosen_push_mana=1,
    )
    foe = make_combatant(2, name="Foe", q=3, r=0)
    state = BattleState(arena_radius=6, combatants=[caster, foe])
    events: list[dict] = []
    runner = TurnRunner(
        state,
        ScriptedRoller([TIER_CASTING_FACES, control_faces]),
        events.append,
        profile=EngineTarmarProfile(magic=RULES),
    )
    runner.cast_spell(caster)
    return [as_row(event) for event in events]


#: Borrowing under :data:`RULES`: a 2d6 debt roll under WIS less two per
#: point owed. Every die of the turn below is the scripted roller's midline,
#: so the debt roll is a natural 6; which totals fail it whatever the target
#: is the test's to choose.
BORROWING = BorrowingRules(
    debt_dice="2d6",
    debt_attributes=("WIS",),
    penalty_per_debt=2,
    failure=BorrowingFailure(key="hex", label="Hexed purse", is_runaway=True),
    automatic_failure_totals=frozenset({12}),
    max_debt=4,
)


def borrowing_cast_events(failing_totals: frozenset[int]) -> list[BattleEvent]:
    """One Fire Missile cast on borrowed mana, as log rows.

    Args:
        failing_totals: The natural debt-roll totals that fail it.
    """
    caster = make_combatant(
        1,
        name="Caster",
        intelligence=14,
        wisdom=12,
        mana=0,
        max_mana=6,
        spells=["fire_missile"],
        spell_skill_levels={"fire_missile": 2},
        chosen_spell="fire_missile",
        chosen_target=2,
        chosen_borrow=True,
    )
    foe = make_combatant(2, name="Foe", q=3, r=0)
    state = BattleState(arena_radius=6, combatants=[caster, foe])
    events: list[dict] = []
    rules = replace(
        RULES,
        borrowing=replace(BORROWING, automatic_failure_totals=failing_totals),
    )
    runner = TurnRunner(
        state, ScriptedRoller(), events.append, profile=EngineTarmarProfile(magic=rules)
    )
    runner.cast_spell(caster)
    return [as_row(event) for event in events]


def as_row(event: dict) -> BattleEvent:
    """An engine event as the battle log stores it, unsaved."""
    return BattleEvent(
        turn=event["turn"],
        phase=event["phase"],
        sequence=event["sequence"],
        event_type=event["event_type"],
        actor=event["actor"],
        payload=event["payload"],
        message=event["message"],
    )


def battle_page_text(events: list[BattleEvent]) -> str:
    """The rows as the battle page's log renders them."""
    return "".join(
        render_to_string("battle/_event.html", {"event": event}) for event in events
    )


def gm_report_text(events: list[BattleEvent]) -> str:
    """The rows as the GM battle report files them."""
    return turn_block(Battle(name="Narrated"), 1, events)


class BothReadersTestCase(SimpleTestCase):
    def assert_both_read(self, events: list[BattleEvent], *phrases: str) -> None:
        # The page escapes its text for HTML; the report is plain text.
        for reader, text, written in (
            ("battle page", battle_page_text(events), escape),
            ("GM report", gm_report_text(events), str),
        ):
            for phrase in phrases:
                with self.subTest(reader=reader, phrase=phrase):
                    self.assertIn(written(phrase), text)


class PushedCastNarrationTest(BothReadersTestCase):
    def test_the_tier_and_a_held_control_roll_read(self) -> None:
        self.assert_both_read(
            pushed_cast_events(HELD_FACES),
            "is a Gleaming cast (natural 3)",
            "Caster control 3d6: [2, 2, 2] = 6 vs INT 13 (need 13 or less) — held",
            "Caster holds the pushed Fire Missile",
        )

    def test_a_deviation_band_reads_by_its_label(self) -> None:
        self.assert_both_read(
            pushed_cast_events(WAVER_FACES),
            "= 14 vs INT 13 (need 13 or less) — Waver",
            "Caster's pushed Fire Missile: Waver",
        )

    def test_a_runaway_reads_and_the_spell_does_not_take_effect(self) -> None:
        self.assert_both_read(
            pushed_cast_events(BOLT_FACES),
            "— Bolt",
            "Fire Missile runs away from Caster; the spell does not take effect",
        )


class WithheldCastNarrationTest(SimpleTestCase):
    def test_a_cast_the_channel_withheld_reads_beneath_its_decision(self) -> None:
        # Level 1 recorded in Fire Ball: the invented Channel is 2 and the
        # spell costs 2, so it stays; level 0 in Lightning Bolt withholds it.
        caster = make_combatant(
            1,
            name="Caster",
            mana=6,
            max_mana=6,
            spells=["fire_ball", "lightning_bolt"],
            spell_skill_levels={"fire_ball": 1},
        )
        state = BattleState(
            arena_radius=6,
            combatants=[caster, make_combatant(2, name="Foe", q=3, r=0)],
        )
        decision = choose_option(state, caster, magic=RULES)
        self.assertEqual(len(decision.withheld), 1)
        row = BattleEvent(
            turn=1,
            phase=3,
            sequence=1,
            event_type="decision",
            actor="Caster",
            payload=TurnRunner._decision_payload(decision),
            message="Forecast — Caster chooses",
        )
        page = battle_page_text([row])
        report = gm_report_text([row])
        reason = decision.withheld[0]
        self.assertIn("Lightning Bolt withheld", reason)
        withheld_line = f'<span class="event-withheld">{escape(reason)}</span>'
        self.assertIn(withheld_line, page)
        # Outside the collapsed working, so it reads without opening it.
        self.assertGreater(page.index(withheld_line), page.index("</details>"))
        self.assertIn(f"- Forecast — Caster chooses\n  - {reason}", report)


class BorrowingCastNarrationTest(BothReadersTestCase):
    """The debt roll and its outcome (tarmar-engine #28), on both readers."""

    def test_the_borrowing_and_a_held_debt_roll_read(self) -> None:
        self.assert_both_read(
            borrowing_cast_events(frozenset({12})),
            "borrows 1, debt 1",
            "Caster debt 2d6: [3, 3] = 6 vs WIS 10 (need 10 or less) — held",
            "Caster holds the debt roll for Fire Missile (1 owed)",
        )

    def test_a_failed_debt_roll_reads_by_its_label(self) -> None:
        events = borrowing_cast_events(frozenset({6}))
        self.assert_both_read(
            events,
            "= 6 vs WIS 10 (need 10 or less) — failed",
            "Caster fails the debt roll for Fire Missile (1 owed)",
            "Caster's failed debt for Fire Missile: Hexed purse",
        )
        # The failure is a status line of its own, in the log's order.
        report = gm_report_text(events)
        self.assertLess(
            report.index("fails the debt roll"), report.index("Hexed purse")
        )


def runaway_runner(
    faces: tuple[tuple[int, ...], ...] = (), **caster_fields
) -> tuple[TurnRunner, BattleState, list[dict]]:
    """A turn runner on :data:`RUNAWAY_RULES` (a drain of twice the original
    cost and 1 more, onto Body, death at minus the Body maximum), with a
    caster (INT 14, WIS 12, 6 mana, Body 10) and a mage beside it (INT 13,
    6 mana).

    Args:
        faces: The scripted dice, in order.
        caster_fields: Changes to the caster.
    """
    caster = make_combatant(
        1,
        **{
            "name": "Caster",
            "intelligence": 14,
            "wisdom": 12,
            "mana": 6,
            "max_mana": 6,
            "body": 10,
            "max_body": 10,
            "spells": ["fire_missile"],
            "spell_skill_levels": {"fire_missile": 2},
            **caster_fields,
        },
    )
    helper = make_combatant(
        2,
        name="Helper",
        q=1,
        r=0,
        intelligence=13,
        mana=6,
        max_mana=6,
        spells=["shield"],
    )
    state = BattleState(arena_radius=6, combatants=[caster, helper])
    events: list[dict] = []
    runner = TurnRunner(
        state,
        ScriptedRoller(list(faces)),
        events.append,
        profile=EngineTarmarProfile(magic=RUNAWAY_RULES),
    )
    return runner, state, events


class RunawayNarrationTest(BothReadersTestCase):
    """A Runaway begins, feeds, fells and is anchored (tarmar-engine #30)."""

    def test_a_runaway_from_the_control_roll_begins(self) -> None:
        runner, state, events = runaway_runner(
            (TIER_CASTING_FACES, BOLT_FACES),
            chosen_spell="fire_missile",
            chosen_target=2,
            chosen_push_mana=1,
        )
        runner.cast_spell(state.by_id(1))
        self.assert_both_read(
            [as_row(event) for event in events],
            "Fire Missile runs away and will feed on Caster each turn (2 mana cast)",
        )

    def test_the_drain_and_the_collapse_read(self) -> None:
        runner, _state, events = runaway_runner(
            mana=2, runaway=RunawayState("fire_missile", 2)
        )
        runner.phase_renew_spells()
        self.assert_both_read(
            [as_row(event) for event in events],
            "Fire Missile feeds on Caster, turn 1: it drains 2 mana (0 left) and 3 "
            "body (7 left); Caster falls unconscious",
            "Caster collapses unconscious",
        )

    def test_a_death_by_the_drain_reads(self) -> None:
        runner, _state, events = runaway_runner(
            mana=0, runaway=RunawayState("fire_missile", 10)
        )
        runner.phase_renew_spells()
        self.assert_both_read(
            [as_row(event) for event in events],
            "it drains 0 mana (0 left) and 21 body (-11 left)",
            "Fire Missile has drained Caster's body to -11, at or past the line at "
            "-10; Caster dies",
            "Caster dies",
        )

    def test_a_held_self_anchor_reads(self) -> None:
        runner, state, events = runaway_runner(
            ((1, 1),), runaway=RunawayState("fire_missile", 2, 1), chosen_anchor=1
        )
        runner.anchor_runaway(state.by_id(1))
        self.assert_both_read(
            [as_row(event) for event in events],
            "Caster anchor 2d8: [1, 1] = 2 vs WIS 12 (need 12 or less) — held",
            "Caster anchors Fire Missile after 1 turn(s)",
        )

    def test_a_failed_help_that_catches_the_helper_reads(self) -> None:
        runner, state, events = runaway_runner(
            ((10, 10),), runaway=RunawayState("fire_missile", 2, 1)
        )
        helper = state.by_id(2)
        helper.chosen_anchor = 1
        runner.anchor_runaway(helper)
        self.assert_both_read(
            [as_row(event) for event in events],
            "Helper anchor 2d10: [10, 10] = 20 vs INT 13 (need 13 or less) — failed",
            "Helper fails to anchor Caster's Fire Missile (4 mana paid, 2 left); it "
            "runs on; Helper is caught by it",
            "Fire Missile runs away and will feed on Helper each turn (2 mana cast, "
            "caught from Caster)",
        )


def counter_runner(
    faces: tuple[tuple[int, ...], ...] = (), **caster_fields
) -> tuple[TurnRunner, BattleState, list[dict]]:
    """A turn runner on :data:`COUNTER_RULES` (2d6 plus INT, +1 per 2 mana,
    ties to the defender), with a caster (INT 14, 6 mana) aiming Fire
    Missile (1 mana, +0) at a defender (INT 12, 6 mana) who has chosen to
    counter it with 2 mana (+1).

    Args:
        faces: The scripted dice, in order.
        caster_fields: Changes to the caster.
    """
    caster = make_combatant(
        1,
        **{
            "name": "Caster",
            "intelligence": 14,
            "mana": 6,
            "max_mana": 6,
            "spells": ["fire_missile"],
            "spell_skill_levels": {"fire_missile": 2},
            "chosen_letter": "h",
            "chosen_spell": "fire_missile",
            "chosen_target": 2,
            **caster_fields,
        },
    )
    defender = make_combatant(
        2,
        name="Defender",
        q=3,
        r=0,
        intelligence=12,
        mana=6,
        max_mana=6,
        spells=["shield"],
        chosen_counter=(1, 2),
    )
    state = BattleState(arena_radius=6, combatants=[caster, defender])
    events: list[dict] = []
    runner = TurnRunner(
        state,
        ScriptedRoller(list(faces)),
        events.append,
        profile=EngineTarmarProfile(magic=COUNTER_RULES),
    )
    return runner, state, events


class CounterspellNarrationTest(BothReadersTestCase):
    """A counter declared, the opposed roll, its result, a lapsed counter and
    the contest's end (tarmar-engine #31)."""

    def test_a_countered_cast_reads_both_totals_and_the_result(self) -> None:
        # Caster 2 + INT 14 = 16; Defender 12 + INT 12 + 1 = 25.
        runner, state, events = counter_runner(((1, 1), (6, 6)))
        runner.cast_spell(state.by_id(1))
        self.assert_both_read(
            [as_row(event) for event in events],
            "Defender commits 2 mana to counter Caster's spell (4 left)",
            "Caster counter 2d6: [1, 1] +14 = 16 — countered",
            "Defender counter 2d6: [6, 6] +13 = 25 — counters",
            "Caster's Fire Missile is countered: 16 against 25",
            "Caster's Fire Missile is countered by Defender: no casting roll",
        )

    def test_a_cast_that_gets_through_reads(self) -> None:
        # Caster 12 + 14 = 26 beats Defender 2 + 13 = 15; the casting roll
        # follows at the scripted roller's midline.
        runner, state, events = counter_runner(((6, 6), (1, 1)))
        runner.cast_spell(state.by_id(1))
        self.assert_both_read(
            [as_row(event) for event in events],
            "Caster counter 2d6: [6, 6] +14 = 26 — gets through",
            "Defender counter 2d6: [1, 1] +13 = 15 — overcome",
            "Caster's Fire Missile gets through: 26 against 15",
        )

    def test_a_counter_no_cast_meets_lapses(self) -> None:
        runner, state, events = counter_runner()
        runner.declare_counter(state.by_id(2))
        runner._lapse_counters()
        self.assert_both_read(
            [as_row(event) for event in events],
            "Defender's counter against Caster meets no spell; its 2 mana is spent",
        )

    def test_an_escalation_no_counter_meets_ends_the_contest(self) -> None:
        runner, state, events = counter_runner(
            contest=ContestState("fire_missile", 2, [2], 1)
        )
        state.by_id(2).chosen_counter = None
        runner.cast_spell(state.by_id(1))
        self.assert_both_read(
            [as_row(event) for event in events],
            "Caster presses it again and no counter meets it: the contest over "
            "Fire Missile ends after 1 counter(s)",
        )
