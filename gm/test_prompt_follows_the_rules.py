"""The Games Master's prompt states the loaded canon's rules numbers (issue #68).

Every sentence of the prompt that states a number a canon rules file holds is
built from the loaded rules. Under the default canon the sections hash to
pinned values, so a change to their text is a deliberate one; seated under
invented rules (every number unlike the pages'), each sentence follows them,
so a number left typed into the prompt fails here rather than agreeing with
the default canon by coincidence.
"""

import hashlib
from collections.abc import Callable
from dataclasses import replace

from django.test import SimpleTestCase
from tarmar_engine.magic import (
    BorrowingFailure,
    BorrowingRules,
    CastingFailureRules,
    CastingSuccessTier,
    ChannelDerivation,
    ChannelRules,
    ControlBand,
    CounterspellRules,
    PushRules,
    RollUnderRule,
    RunawayRules,
    SpellEffectKind,
)

from battle.tests.magic_fixtures import (
    DEFAULT_MAGIC_RULES,
    INVENTED_RULES,
    seated_rules,
)
from characters.equipment_rules import EncumbranceLevel, EncumbranceRules
from characters.rules import FatigueRollRules
from characters.skill_rules import SkillRules, SkillTrackRules
from characters.skills import MAX_EXPERIENCE_YEARS_PER_RUN
from characters.tests.rules_fixtures import (
    DEFAULT_CHARACTER_RULES,
    seated_character_rules,
    seated_equipment_catalog,
    seated_equipment_rules,
    seated_skill_rules,
)
from characters.tests.test_equipment_rules import (
    INVENTED_EQUIPMENT_CATALOG,
    INVENTED_EQUIPMENT_RULES,
)
from characters.tests.test_rules import INVENTED_CHARACTER_RULES
from characters.tests.test_skill_rules import (
    INVENTED_SKILL_RULES,
    SAILOR_SKILLS,
    SMITH_SKILLS,
)

from . import engine
from .engine import MODE_PLAY
from .turn_engine.messages import plan_message, planning_rules
from .turn_engine.policy import TurnPolicy

#: Each section built from the rules, by its builder, and the SHA-256 of its
#: text under the default canon's rules. ``section_core`` and
#: ``section_dm_authority`` are byte for byte what they were before #68; the
#: other four changed where the data could not reproduce the old prose (each
#: track's XP a year and the label "Hunter/Gatherer", the weapon, armour and
#: shield lists, Heavy encumbrance's "or slow", the plan examples' track).
#: ``section_magic`` is not here: it is the loaded canon's ``magic.md`` around
#: the subsections built from the rules (#48, #127), so its text depends on
#: which canon is loaded. Its built bodies are pinned as text instead, below.
DEFAULT_SECTION_HASHES: dict[Callable[[], str], str] = {
    engine.section_core: (
        "da9031c648b4d17c88cd8a9cc964febf405f610c7162879660ea90f09033d464"
    ),
    engine.section_character_creation: (
        "db4206fb59b14f980abee3e499b4a0a2dc01b10d9d74691cccf3191e72d84932"
    ),
    engine.section_combat: (
        "b8d941a4ff496f645cba171e2d411b066a0be2e83b5cc024e1faa685376e5367"
    ),
    engine.section_equipment: (
        "1a60945721413e392fc7958d32bf4ca0644bd722ce59b348660b8f94e806fa5f"
    ),
    engine.section_plan_schema: (
        "5b1d359559bb186d5d330e32e79c16ec65bf8fcdb920bf1eef66ec431620dcf4"
    ),
    engine.section_dm_authority: (
        "a5db0207d8e5a666d5c34d9b82aaff6993d29539f3a467fefc8f6e6f54d2ce0b"
    ),
}


#: The magic section's built bodies under the default canon's magic rules
#: (#48, #127), by heading, whichever canon is loaded.
DEFAULT_MAGIC_BODIES: dict[str, str] = dict(
    zip(
        engine.GENERATED_MAGIC_HEADINGS,
        (
            "3: Lucky cast (+1 to the spell's rolled damage or healing). A natural "
            "13 or more fails whatever the attribute: 13: bad fumble (spell fails, "
            "mana lost); 14: catastrophic (spell fails, mana lost) — triggers "
            "Runaway; 15-18: fumble (spell fails, mana kept).",
            "Invest extra mana for greater effect. No more than 3 mana may be "
            "pushed. Control Roll: 3d6 ≤ INT − (spell "
            "cost + mana pushed). Fail by 1-4: Slip (spell takes effect without the "
            "push's bonus). Fail by 5+: Surge, a Runaway (spell fails). Natural 18: "
            "always Surge, a Runaway.",
            "There is no Channel: only the caster's mana, and the push and debt caps "
            "where the rules set them, bound a casting.",
            "Spend all remaining mana, borrow the rest as Debt, up to 4 owed in all. "
            "Debt Roll after the spell: 2d6 ≤ (WIS − 2 × Debt). A natural 12 always "
            "fails. A failed Debt Roll is a Backlash; the spell has already "
            "resolved. Mana handed back pays the Debt before it refills the pool.",
            "The spell feeds on the caster: each turn it drains mana equal to 1 more "
            "than the original casting cost and its effect grows unpredictably (GM "
            "decides; once anchored, the anchorer's next cast of that spell adds +2 "
            "to its rolled effect for each turn it ran). At 0 mana the "
            "caster falls unconscious and it drains Body instead; death when Body "
            "reaches 0. Anchor: the conscious caster rolls 3d6 ≤ WIS each turn to "
            "regain control. A third-party mage may intervene by investing mana "
            "equal to 2 × the original casting cost and rolling 2d10 ≤ INT — failure "
            "catches both in the Runaway. Survivors often bear marks: white streaks "
            "in the hair, strange scars, a haunted look.",
            "The defender commits 1 to 5 mana and declares a counter (on their "
            "action); opposed 2d10 + INT rolls, each side +1 per 2 mana invested "
            "(the caster's whole casting, the defender's commitment); ties go to the "
            "defender. Defender wins: spell countered. Caster wins: spell goes off. "
            "Only the caster can escalate on later turns by paying again; the "
            "defender gets one shot per casting and may oppose again (paying again) "
            "or let it through. A failed Control Roll during escalation can trigger "
            "Runaway.",
        ),
        strict=True,
    )
)

#: Magic rules whose every number is unlike both canons' (#48): tiers out of
#: order, one of two naturals, one that only hands mana back and one that
#: takes from the effect and hands mana back; a casting
#: floor of 10 with a lone plain total, a run of them between the specials
#: and a run up to the dice's highest; and a Debt Roll on two attributes at 3
#: a point, with two automatic failures, a failure that is a Runaway under
#: another name opening on a vowel, and refunds that leave the Debt owed (#48).
#: A Control Roll of 2d8 under WIS at 2 a mana with the spell's cost counted,
#: a band of one margin, one where the push's bonus holds, one that fails
#: short of a Runaway, and two automatic Runaway totals; a Channel that
#: doubles the spell's level and adds 1 beside one read off a general skill
#: that costs 3× to advance, less 1; a Runaway that drains 3 × the cost + 2 into
#: Fatigue, kills at twice its maximum, grows 4 a turn and lets a helper fail
#: alone at 3 × the cost; and a Counterspell of 2d8 + WIS at 4 mana a bonus,
#: ties to the defender, and exactly 2 mana committed (#127).
UNLIKE_EITHER_CANONS_MAGIC_RULES = replace(
    INVENTED_RULES,
    casting_success_tiers=(
        CastingSuccessTier(
            key="spark", label="Spark", natural_totals=frozenset({8}), mana_refund=2
        ),
        CastingSuccessTier(key="steady", label="Steady", natural_totals=frozenset({9})),
        CastingSuccessTier(
            key="glint", label="Glint", natural_totals=frozenset({6, 7}), effect_bonus=3
        ),
        CastingSuccessTier(
            key="flare",
            label="Flare",
            natural_totals=frozenset({5}),
            effect_bonus=-2,
            mana_refund=3,
        ),
    ),
    casting_failure=CastingFailureRules(
        fumble_floor=10, mana_lost_totals=frozenset({11, 15}), runaway_total=15
    ),
    borrowing=BorrowingRules(
        debt_dice="2d8",
        debt_attributes=("WIS", "INT"),
        penalty_per_debt=3,
        failure=BorrowingFailure(key="surge", label="Arcane surge", is_runaway=True),
        automatic_failure_totals=frozenset({16, 2}),
        max_debt=5,
        refunds_pay_debt_first=False,
    ),
    push=PushRules(
        control_dice="2d8",
        control_attribute="WIS",
        penalty_per_mana=2,
        base_cost_counts_as_invested=True,
        failure_bands=(
            ControlBand(
                key="flicker",
                label="Flicker",
                is_runaway=False,
                lowest_margin=1,
                highest_margin=1,
                spell_takes_effect=True,
                push_bonus_applies=True,
            ),
            ControlBand(
                key="stagger",
                label="Stagger",
                is_runaway=False,
                lowest_margin=2,
                highest_margin=4,
                spell_takes_effect=True,
                push_bonus_applies=False,
            ),
            ControlBand(
                key="fizzle",
                label="Fizzle",
                is_runaway=False,
                lowest_margin=5,
                highest_margin=8,
                spell_takes_effect=False,
                push_bonus_applies=False,
            ),
            ControlBand(
                key="wild",
                label="Wild surge",
                is_runaway=True,
                lowest_margin=9,
                highest_margin=None,
                spell_takes_effect=False,
                push_bonus_applies=False,
            ),
        ),
        automatic_runaway_totals=frozenset({16, 2}),
        effect_bonus_per_mana_by_kind={SpellEffectKind.DAMAGE: 1},
    ),
    channel=ChannelRules(
        derivations={
            "studied": ChannelDerivation(source_skill=None, multiplier=2, offset=1),
            "sorcerous": ChannelDerivation(
                source_skill="sorcery", multiplier=1, offset=-1
            ),
        },
        default_kind="studied",
    ),
    runaway=RunawayRules(
        drain_multiplier_of_cost=3,
        drain_flat=2,
        overflow_pool="fatigue",
        death_multiplier_of_max=2,
        self_anchor=RollUnderRule(dice="2d8", attribute="INT"),
        helper_anchor=RollUnderRule(dice="2d10", attribute="WIS"),
        helper_cost_multiplier_of_cost=3,
        helper_failure_catches_helper=False,
        effect_bonus_per_turn=4,
    ),
    counterspell=CounterspellRules(
        opposed_dice="2d8",
        opposed_attribute="WIS",
        mana_per_bonus=4,
        ties_go_to_caster=False,
        defender_min_mana=2,
        defender_max_mana=2,
    ),
)

#: :data:`UNLIKE_EITHER_CANONS_MAGIC_RULES`' Push, Runaway and Counterspell
#: at the other end of each choice (#127): no penalty, a Runaway band named
#: Runaway and no automatic total; a Runaway that drains the cost alone into
#: Body, kills at its maximum below zero, does not grow and catches a failed
#: helper paying the cost; a Counterspell whose ties go to the caster and
#: whose least commitment is the only bound.
OTHER_END_MAGIC_RULES = replace(
    UNLIKE_EITHER_CANONS_MAGIC_RULES,
    push=PushRules(
        control_dice="3d6",
        control_attribute="INT",
        penalty_per_mana=0,
        base_cost_counts_as_invested=False,
        automatic_runaway_totals=frozenset(),
        effect_bonus_per_mana_by_kind={SpellEffectKind.DAMAGE: 1},
        failure_bands=(
            ControlBand(
                key="runaway",
                label="Runaway",
                is_runaway=True,
                lowest_margin=1,
                highest_margin=None,
                spell_takes_effect=False,
                push_bonus_applies=False,
            ),
        ),
    ),
    runaway=RunawayRules(
        drain_multiplier_of_cost=1,
        drain_flat=0,
        overflow_pool="body",
        death_multiplier_of_max=1,
        self_anchor=RollUnderRule(dice="2d8", attribute="INT"),
        helper_anchor=RollUnderRule(dice="2d10", attribute="WIS"),
        helper_cost_multiplier_of_cost=1,
        helper_failure_catches_helper=True,
        effect_bonus_per_turn=0,
    ),
    counterspell=CounterspellRules(
        opposed_dice="2d8",
        opposed_attribute="WIS",
        mana_per_bonus=4,
        ties_go_to_caster=True,
        defender_min_mana=2,
    ),
)


#: The magic section's generated headings, in the section's order.
(
    SPECIAL_CASTING_RESULTS,
    PUSH,
    CHANNEL,
    BORROWING,
    RUNAWAY,
    COUNTERSPELL,
) = engine.GENERATED_MAGIC_HEADINGS


def magic_body(heading: str) -> str:
    """The body the magic section carries under ``heading``, as built now.

    Args:
        heading: One of :data:`gm.engine.GENERATED_MAGIC_HEADINGS`.
    """
    section = engine.section_magic()
    start = section.index(f"{heading}\n") + len(heading) + 1
    return section[start:].split("\n", 1)[0]


def with_track_skills(sailor: list[str], smith: list[str]) -> SkillRules:
    """The invented skill rules with the sailor's and smith's tables replaced.

    Args:
        sailor: The sailor track's twenty entries.
        smith: The smith track's twenty entries.
    """
    tracks = INVENTED_SKILL_RULES.tracks.tracks
    return replace(
        INVENTED_SKILL_RULES,
        tracks=SkillTrackRules(
            tracks={
                "sailor": replace(tracks["sailor"], skills=tuple(sailor)),
                "smith": replace(tracks["smith"], skills=tuple(smith)),
            }
        ),
    )


#: The invented catalog with armour and a shield whose every number, and
#: whose tier, no default canon item has; Silk has no DEX penalty at all.
_QUILT = replace(
    INVENTED_EQUIPMENT_CATALOG.armour["quilt"],
    stops=7,
    dex_penalty=-6,
    armour_tier="None",
)
UNLIKE_THE_DEFAULT_CATALOG = replace(
    INVENTED_EQUIPMENT_CATALOG,
    armour={
        "quilt": _QUILT,
        "silk": replace(
            _QUILT,
            identifier="silk",
            name="Silk",
            stops=8,
            dex_penalty=0,
            movement_restriction=None,
        ),
    },
    shields={
        "buckler": replace(
            INVENTED_EQUIPMENT_CATALOG.shields["buckler"],
            stops=4,
            dex_penalty=-3,
            shield_bonus=5,
        )
    },
)


class DefaultCanonTextTest(SimpleTestCase):
    """The default canon's rules give the pinned text, whichever canon is
    loaded."""

    def setUp(self) -> None:
        self.enterContext(seated_character_rules())
        self.enterContext(seated_skill_rules())
        self.enterContext(seated_equipment_catalog())
        self.enterContext(seated_equipment_rules())
        self.enterContext(seated_rules(DEFAULT_MAGIC_RULES))

    def test_each_section_is_the_pinned_text(self) -> None:
        for builder, expected in DEFAULT_SECTION_HASHES.items():
            with self.subTest(section=builder.__name__):
                built = hashlib.sha256(builder().encode()).hexdigest()
                self.assertEqual(built, expected)

    def test_the_magic_sections_built_bodies_are_the_pinned_text(self) -> None:
        for heading, body in DEFAULT_MAGIC_BODIES.items():
            with self.subTest(heading=heading):
                self.assertEqual(magic_body(heading), body)

    def test_the_plan_calls_rules_line_is_the_pinned_text(self) -> None:
        self.assertEqual(
            planning_rules(),
            "Rules: attributes are 3d6 each; Fatigue needs all six attributes and "
            "the 2d6 Fatigue roll, which is rolled when a character is made; years "
            "of experience are played 1 to 20 at a time on a track, after the "
            "attributes are recorded, and each year can kill.",
        )

    def test_the_movement_sentences_read_as_the_pages(self) -> None:
        self.assertIn(
            "Unencumbered: up to STR (no penalty). Light: to STR×1.5 (−1 "
            "movement). Medium: to STR×2 (−2, no Run or Sprint). Heavy: to "
            "STR×2.5 (−4, Walk only). Overloaded: over STR×2.5 (Walk only, 1 hex "
            "max).",
            engine.section_combat(),
        )
        self.assertIn(
            "Leather Armor stops 2 (−2 DEX, cannot Sprint). Medium: Chainmail "
            "stops 3 (−3 DEX, cannot Run or Sprint).",
            engine.section_equipment(),
        )

    def test_a_single_best_of_attribute_is_added_plainly(self) -> None:
        one_best = replace(
            DEFAULT_CHARACTER_RULES,
            fatigue_roll=FatigueRollRules(
                dice="2d6", attributes=("CON",), best_of=("DEX",)
            ),
        )
        with seated_character_rules(one_best):
            self.assertIn("- Fatigue = CON + DEX + 2d6.", engine.section_core())


class InventedRulesTest(SimpleTestCase):
    """Each sentence follows invented rules seated in every rules file."""

    def setUp(self) -> None:
        self.enterContext(seated_character_rules(INVENTED_CHARACTER_RULES))
        self.enterContext(seated_skill_rules(INVENTED_SKILL_RULES))
        self.enterContext(seated_equipment_catalog(INVENTED_EQUIPMENT_CATALOG))
        self.enterContext(seated_equipment_rules(INVENTED_EQUIPMENT_RULES))

    def test_the_core_section_states_the_checks_and_attributes(self) -> None:
        core = engine.section_core()
        for sentence in (
            "An attribute check with no skill rolls 2d8 ≤ the attribute (or "
            "adjusted attribute). A skill check rolls 3d4 + the governing "
            "attribute's modifier + the skill level against a difficulty the GM "
            "sets; a natural 11 always succeeds and a natural 3 always fails",
            "STR, DEX, INT, WIS, CON, CHR — each 4d4, or divide 50 points between "
            "them with every attribute ending between 4 and 15.\n",
            "Height (1d6: 4'2\" to 5'10\"), Weight (7-12 stones base + STR/3 "
            "stones; a stone is 10 lbs).",
            "- Fatigue = CON + 1d8+1. Normal hits",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, core)

    def test_the_creation_section_states_the_dice_age_and_tracks(self) -> None:
        creation = engine.section_character_creation()
        for sentence in (
            "1. Core Attributes (4d4 each or 50 points). ",
            "2. The 1d8+1 Fatigue roll is made as the character is made",
            "Provenance (Nobility 1-20%, Merchant 21-50%, Commoner 51-100%)",
            "rolled once on 2d10 for the player",
            "bought a year at a time from age 14: each year",
            "### Experience Tracks\nSailor (300 XP/year), Smith (400 XP/year) — "
            "each with different survival thresholds, money/year, and its own "
            "20-entry skill list.",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, creation)
        self.assertNotIn("Merchant (750", creation)
        # Neither invented table opens with a Weapon skill or Weapon Parry, so
        # the yields sentence stands alone.
        self.assertIn(
            "+1 toward its next level. Magic yields Spell Points (mana)", creation
        )

    def test_the_weapon_lead_is_stated_only_when_most_tracks_open_so(
        self,
    ) -> None:
        both_lead = with_track_skills(
            ["Weapon", *SAILOR_SKILLS[1:]], ["Weapon Parry", *SMITH_SKILLS[1:]]
        )
        with seated_skill_rules(both_lead):
            self.assertIn(
                "Weapon skills and Weapon Parry lead most tracks; Magic yields ",
                engine.section_character_creation(),
            )
        one_leads = with_track_skills(["Weapon", *SAILOR_SKILLS[1:]], SMITH_SKILLS)
        with seated_skill_rules(one_leads):
            self.assertNotIn("lead most tracks", engine.section_character_creation())

    def test_the_combat_section_states_the_dice_shields_and_loads(self) -> None:
        combat = engine.section_combat()
        for sentence in (
            "skill checks roll 3d4 + attribute modifier + skill level",
            "difficulty; attribute checks roll 2d8 ≤ attribute; saves and spells "
            "3d6 ≤ attribute.)\n",
            "+ shield (+2 buckler) + defender dodge",
            "Free: up to STR (no penalty). Laden: to STR×3.5 (−2, no Dash). "
            "Crushed: over STR×3.5 (−1, Amble only, 2 hex max).\n",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, combat)

    def test_the_equipment_section_lists_the_catalog(self) -> None:
        with seated_equipment_catalog(UNLIKE_THE_DEFAULT_CATALOG):
            equipment = engine.section_equipment()
        for sentence in (
            "### Weapons (damage; minimum STR; class; notes)\n"
            "Glaive 2d4+1 (9, Flexible / Snare or Piercing; Reach.). In a missile "
            "weapon's notes, a number with no attribute named is adjDEX.\n",
            "### Armor and Shields (stops; DEX penalty)\n"
            "None: Quilt stops 7 (−6 DEX, cannot Dash); Silk stops 8 (0 DEX). "
            "Buckler stops 4 (−3 DEX, +5 TN).\n",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, equipment)
        # Ammunition has no class, so it is no weapon to list.
        self.assertNotIn("Pebbles", equipment)

    def test_a_walk_cap_alone_and_a_level_for_any_load(self) -> None:
        one_level = replace(
            INVENTED_EQUIPMENT_RULES,
            encumbrance=EncumbranceRules(
                levels=(
                    EncumbranceLevel(
                        key="burdened",
                        max_multiplier=None,
                        penalty=0,
                        forbidden_gaits=(),
                        walk_limit=3,
                    ),
                )
            ),
        )
        with seated_equipment_rules(one_level):
            self.assertIn(
                "Burdened: any load (Amble 3 hex max).\n", engine.section_combat()
            )

    def test_the_plan_format_names_the_dice_and_a_loaded_track(self) -> None:
        schema = engine.section_plan_schema()
        for sentence in (
            "rolls 4d4 for each of the six attributes",
            "rolls the 1d8+1 Fatigue roll again",
            '"constitution"} rolls 2d8 against an attribute with no skill',
            "rolls the 2d10 literacy check",
            '"track": "sailor", "years": 5} plays',
            '"subject": "survival", "track": "sailor"} reads',
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, schema)
        self.assertNotIn("campaigner", schema)

    def test_the_plan_calls_rules_line_names_the_dice(self) -> None:
        line = planning_rules()
        self.assertTrue(line.startswith("Rules: attributes are 4d4 each; "))
        self.assertIn("all six attributes and the 1d8+1 Fatigue roll,", line)
        self.assertIn(f"played 1 to {MAX_EXPERIENCE_YEARS_PER_RUN} at a time", line)
        self.assertIn(
            f"\n{line}\n",
            plan_message(
                "STATE", "LATEST", TurnPolicy.for_turn(MODE_PLAY, is_game_master=False)
            )["content"],
        )

    def test_the_game_masters_authority_names_the_point_buy(self) -> None:
        authority = engine.section_dm_authority()
        for sentence in (
            "including above 15 and above anything 4d4 or the 50-point buy could "
            "produce",
            '"that is 13 over the 50-point budget"',
            "whether 4d4 or the point buy could have reached it",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, authority)
        self.assertNotIn("65", authority)

    def test_the_magic_section_states_the_casting_table(self) -> None:
        with seated_rules(UNLIKE_EITHER_CANONS_MAGIC_RULES):
            body = magic_body(SPECIAL_CASTING_RESULTS)
        self.assertEqual(
            body,
            "5: Flare (−2 to the spell's rolled damage or healing, 3 mana handed "
            "back); 6 or 7: Glint (+3 to the spell's rolled damage or healing); 8: "
            "Spark "
            "(2 mana handed back); 9: Steady (no bonus). A natural 10 or more fails "
            "whatever the attribute: 10: fumble (spell fails, mana kept); 11: bad "
            "fumble (spell fails, mana lost); 12-14: fumble (spell fails, mana "
            "kept); 15: catastrophic (spell fails, mana lost) — triggers Runaway; "
            "16-18: fumble (spell fails, mana kept).",
        )

    def test_the_magic_section_states_the_borrowing(self) -> None:
        with seated_rules(UNLIKE_EITHER_CANONS_MAGIC_RULES):
            body = magic_body(BORROWING)
        self.assertEqual(
            body,
            "Spend all remaining mana, borrow the rest as Debt, up to 5 owed in all. "
            "Debt Roll after the spell: 2d8 ≤ (WIS + INT − 3 × Debt). A natural 2 "
            "or 16 always fails. A failed Debt Roll is an Arcane surge, a Runaway; the "
            "spell has already resolved. Mana handed back refills the pool; the "
            "Debt stays owed.",
        )

    def test_a_runaway_named_runaway_is_named_once(self) -> None:
        rules = UNLIKE_EITHER_CANONS_MAGIC_RULES
        assert rules.borrowing is not None
        named_runaway = replace(
            rules,
            borrowing=replace(
                rules.borrowing,
                failure=BorrowingFailure(
                    key="runaway", label="runaway", is_runaway=True
                ),
                automatic_failure_totals=frozenset(),
                max_debt=None,
                penalty_per_debt=1,
            ),
        )
        with seated_rules(named_runaway):
            body = magic_body(BORROWING)
        self.assertEqual(
            body,
            "Spend all remaining mana, borrow the rest as Debt. Debt Roll after the "
            "spell: 2d8 ≤ (WIS + INT − Debt). A failed Debt Roll is a runaway; the "
            "spell has already resolved. Mana handed back refills the pool; the "
            "Debt stays owed.",
        )

    def test_a_debt_roll_with_no_penalty_names_the_attributes_alone(self) -> None:
        rules = UNLIKE_EITHER_CANONS_MAGIC_RULES
        assert rules.borrowing is not None
        no_penalty = replace(
            rules, borrowing=replace(rules.borrowing, penalty_per_debt=0)
        )
        with seated_rules(no_penalty):
            body = magic_body(BORROWING)
        self.assertIn(" Debt Roll after the spell: 2d8 ≤ (WIS + INT). ", body)

    def test_no_tiers_no_failing_totals_no_borrowing_runaway_or_counter(self) -> None:
        with seated_rules(INVENTED_RULES):
            bodies = [
                magic_body(heading) for heading in engine.GENERATED_MAGIC_HEADINGS
            ]
        self.assertEqual(
            bodies,
            [
                "The attribute alone judges a casting roll: no natural total fails on "
                "its own, a failed cast keeps its mana, and no cast starts a Runaway.",
                "Invest extra mana for greater effect. No more than 2 mana may be "
                "pushed. Control Roll: 3d6 ≤ INT − (mana "
                "pushed). Fail by 1-2: Waver (spell takes effect without the push's "
                "bonus). Fail by 3+: Bolt, a Runaway (spell fails).",
                "Hard cap on mana per casting, the spell's cost and any pushed mana "
                "together. Studied Magic: equals 2 × skill level in that specific "
                "spell.",
                "A caster without the mana for a spell cannot cast it; there is no "
                "Borrowing.",
                "A Runaway is narrated when it starts; the rules give it no later "
                "turns, no drain and no anchor roll.",
                "No mage may commit mana to counter another's casting; there is no "
                "Counterspell.",
            ],
        )

    def test_the_magic_section_states_the_push(self) -> None:
        with seated_rules(UNLIKE_EITHER_CANONS_MAGIC_RULES):
            body = magic_body(PUSH)
        self.assertEqual(
            body,
            "Invest extra mana for greater effect. Control Roll: 2d8 ≤ WIS − 2 × "
            "(spell cost + mana pushed). Fail by 1: Flicker (spell takes effect with "
            "the push's bonus). Fail by 2-4: Stagger (spell takes effect without the "
            "push's bonus). Fail by 5-8: Fizzle (spell fails). Fail by 9+: Wild "
            "surge, a Runaway (spell fails). Natural 2 or 16: always Wild surge, a "
            "Runaway.",
        )

    def test_a_push_with_no_cap_states_none(self) -> None:
        with seated_rules(UNLIKE_EITHER_CANONS_MAGIC_RULES):
            body = magic_body(PUSH)
        self.assertNotIn("No more than", body)
        self.assertIn("Invest extra mana for greater effect. Control Roll: ", body)

    def test_a_push_with_no_runaway_band_starts_no_runaway_in_escalation(
        self,
    ) -> None:
        tame = replace(
            UNLIKE_EITHER_CANONS_MAGIC_RULES,
            push=PushRules(
                control_dice="3d6",
                control_attribute="INT",
                penalty_per_mana=1,
                base_cost_counts_as_invested=False,
                failure_bands=(
                    ControlBand(
                        key="wobble",
                        label="Wobble",
                        is_runaway=False,
                        lowest_margin=1,
                        highest_margin=None,
                        spell_takes_effect=True,
                        push_bonus_applies=False,
                    ),
                ),
                automatic_runaway_totals=frozenset(),
                effect_bonus_per_mana_by_kind={SpellEffectKind.DAMAGE: 1},
            ),
        )
        with seated_rules(tame):
            push = magic_body(PUSH)
            counterspell = magic_body(COUNTERSPELL)
        self.assertTrue(
            push.endswith(
                "Fail by 1+: Wobble (spell takes effect without the push's bonus)."
            )
        )
        self.assertNotIn("Control Roll", counterspell)
        self.assertTrue(counterspell.endswith("or let it through."))

    def test_a_kind_of_magic_named_in_two_words_from_an_ordinary_skill(
        self,
    ) -> None:
        """A key's underscore is a space, and a source skill that costs the
        ordinary price to advance carries no cost note."""
        rules = replace(
            UNLIKE_EITHER_CANONS_MAGIC_RULES,
            borrowing=None,
            channel=ChannelRules(
                derivations={
                    "high_elven": ChannelDerivation(
                        source_skill="lore", multiplier=1, offset=0
                    )
                },
                default_kind="high_elven",
            ),
        )
        with seated_rules(rules):
            body = magic_body(CHANNEL)
        self.assertEqual(
            body,
            "Hard cap on mana per casting, the spell's cost and any pushed mana "
            "together. High Elven Magic: equals Lore skill level.",
        )

    def test_the_magic_section_states_the_channel(self) -> None:
        with seated_rules(UNLIKE_EITHER_CANONS_MAGIC_RULES):
            body = magic_body(CHANNEL)
        self.assertEqual(
            body,
            "Hard cap on mana per casting, the spell's cost and any pushed mana "
            "together. Studied Magic: equals 2 × skill level in that specific spell "
            "+ 1. Sorcerous Magic: equals Sorcery skill level − 1, never below 0 "
            "(costs 3× the skill points to advance). Channel cannot be exceeded even "
            "by Borrowing.",
        )

    def test_a_channel_that_reads_no_level_states_its_number(self) -> None:
        rules = replace(
            UNLIKE_EITHER_CANONS_MAGIC_RULES,
            borrowing=None,
            channel=ChannelRules(
                derivations={
                    "fixed": ChannelDerivation(
                        source_skill=None, multiplier=0, offset=6
                    )
                },
                default_kind="fixed",
            ),
        )
        with seated_rules(rules):
            body = magic_body(CHANNEL)
        self.assertEqual(
            body,
            "Hard cap on mana per casting, the spell's cost and any pushed mana "
            "together. Fixed Magic: is 6.",
        )

    def test_the_magic_section_states_the_runaway(self) -> None:
        with seated_rules(UNLIKE_EITHER_CANONS_MAGIC_RULES):
            body = magic_body(RUNAWAY)
        self.assertEqual(
            body,
            "The spell feeds on the caster: each turn it drains mana equal to 3 × "
            "the original casting cost + 2 and its effect grows unpredictably (GM "
            "decides; once anchored, the anchorer's next cast of that spell adds +4 "
            "to its rolled effect for each turn it ran). At 0 mana the "
            "caster falls unconscious and it drains Fatigue instead; death when "
            "Fatigue reaches negative 2 × full starting value. Anchor: the conscious "
            "caster rolls 2d8 ≤ INT each turn to regain control. A third-party mage "
            "may intervene by investing mana equal to 3 × the original casting cost "
            "and rolling 2d10 ≤ WIS — failure costs the helper only that mana. "
            "Survivors often bear marks: white streaks in the hair, strange scars, a "
            "haunted look.",
        )

    def test_the_magic_section_states_the_counterspell(self) -> None:
        with seated_rules(UNLIKE_EITHER_CANONS_MAGIC_RULES):
            body = magic_body(COUNTERSPELL)
        self.assertEqual(
            body,
            "The defender commits exactly 2 mana and declares a counter (on their "
            "action); opposed 2d8 + WIS rolls, each side +1 per 4 mana invested (the "
            "caster's whole casting, the defender's commitment); ties go to the "
            "defender. Defender wins: spell countered. Caster wins: spell goes off. "
            "Only the caster can escalate on later turns by paying again; the "
            "defender gets one shot per casting and may oppose again (paying again) "
            "or let it through. A failed Control Roll during escalation can trigger "
            "Runaway.",
        )

    def test_the_other_end_of_each_push_runaway_and_counter_choice(self) -> None:
        with seated_rules(OTHER_END_MAGIC_RULES):
            bodies = {heading: magic_body(heading) for heading in (PUSH, RUNAWAY)}
            counterspell = magic_body(COUNTERSPELL)
        self.assertEqual(
            bodies,
            {
                PUSH: "Invest extra mana for greater effect. Control Roll: 3d6 ≤ INT. "
                "Fail by 1+: Runaway (spell fails).",
                RUNAWAY: "The spell feeds on the caster: each turn it drains mana "
                "equal to the original casting cost and its effect grows "
                "unpredictably (GM decides). At 0 mana the caster falls unconscious "
                "and it drains Body instead; death when Body reaches negative full "
                "starting value. Anchor: the conscious caster rolls 2d8 ≤ INT each "
                "turn to regain control. A third-party mage may intervene by "
                "investing mana equal to the original casting cost and rolling 2d10 "
                "≤ WIS — failure catches both in the Runaway. Survivors often bear "
                "marks: white streaks in the hair, strange scars, a haunted look.",
            },
        )
        self.assertTrue(
            counterspell.startswith(
                "The defender commits at least 2 mana and declares a counter (on "
                "their action); opposed 2d8 + WIS rolls, each side +1 per 4 mana "
                "invested (the caster's whole casting, the defender's commitment); "
                "ties go to the caster. "
            )
        )

    def test_a_drain_with_no_death_line_or_cost_and_an_unbounded_counter(
        self,
    ) -> None:
        rules = UNLIKE_EITHER_CANONS_MAGIC_RULES
        assert rules.runaway is not None and rules.counterspell is not None
        flat = replace(
            rules,
            runaway=replace(
                rules.runaway,
                drain_multiplier_of_cost=0,
                drain_flat=3,
                death_multiplier_of_max=None,
                helper_cost_multiplier_of_cost=0,
            ),
            counterspell=replace(
                rules.counterspell, defender_min_mana=0, defender_max_mana=None
            ),
        )
        with seated_rules(flat):
            runaway = magic_body(RUNAWAY)
            counterspell = magic_body(COUNTERSPELL)
        for sentence in (
            "each turn it drains 3 mana and its effect",
            "drains Fatigue instead; it sets no death line of its own. ",
            "may intervene by investing no mana and rolling 2d10",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, runaway)
        self.assertTrue(
            counterspell.startswith("The defender commits mana and declares a counter")
        )

    def test_no_push_no_control_roll_and_no_escalation_runaway(self) -> None:
        with seated_rules(replace(UNLIKE_EITHER_CANONS_MAGIC_RULES, push=None)):
            push = magic_body(PUSH)
            counterspell = magic_body(COUNTERSPELL)
        self.assertEqual(
            push, "A spell is cast at its cost; there is no Push and no Control Roll."
        )
        self.assertNotIn("Control Roll", counterspell)
        self.assertTrue(counterspell.endswith("or let it through."))
