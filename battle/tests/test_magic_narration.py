"""The casting success tier, the Control Roll and a withheld cast, as read.

Issue #960: the engine narrates each with the labels the canon's rules give
it (a tier's ``label``, a failure band's ``label``) and its own words only
for held and a Runaway. These pin that every such line reaches both readers
of a battle's log, the battle page (``battle/_event.html``) and the GM
battle report (:func:`gm.battle_report.turn_block`). Every label here is
invented.
"""

from dataclasses import replace

from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.utils.html import escape
from tarmar_engine.engine import TurnRunner
from tarmar_engine.magic import CastingSuccessTier
from tarmar_engine.policy import choose_option
from tarmar_engine.profile import TarmarProfile as EngineTarmarProfile
from tarmar_engine.state import BattleState

from battle.models import Battle, BattleEvent
from gm.battle_report import turn_block

from .magic_fixtures import INVENTED_RULES
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


class PushedCastNarrationTest(SimpleTestCase):
    def assert_both_read(self, events: list[BattleEvent], *phrases: str) -> None:
        # The page escapes its text for HTML; the report is plain text.
        for reader, text, written in (
            ("battle page", battle_page_text(events), escape),
            ("GM report", gm_report_text(events), str),
        ):
            for phrase in phrases:
                with self.subTest(reader=reader, phrase=phrase):
                    self.assertIn(written(phrase), text)

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
