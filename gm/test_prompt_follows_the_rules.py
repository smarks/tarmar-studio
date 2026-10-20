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
#: the two subsections built from the rules (#48), so its text depends on which
#: canon is loaded. Its two built bodies are pinned as text instead, below.
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
#: (#48), by heading, whichever canon is loaded.
DEFAULT_MAGIC_BODIES: dict[str, str] = dict(
    zip(
        engine.GENERATED_MAGIC_HEADINGS,
        (
            "3: Lucky cast (+1 to the spell's rolled damage or healing). A natural "
            "13 or more fails whatever the attribute: 13: bad fumble (spell fails, "
            "mana lost); 14: catastrophic (spell fails, mana lost) — triggers "
            "Runaway; 15-18: fumble (spell fails, mana kept).",
            "Spend all remaining mana, borrow the rest as Debt, up to 4 owed in all. "
            "Debt Roll after the spell: 2d6 ≤ (WIS − 2 × Debt). A natural 12 always "
            "fails. A failed Debt Roll is a Backlash; the spell has already "
            "resolved. Mana handed back pays the Debt before it refills the pool.",
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
#: another name opening on a vowel, and refunds that leave the Debt owed.
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
)


def magic_body(heading: str) -> str:
    """The body the magic section carries under ``heading``, as built now.

    Args:
        heading: One of :data:`gm.engine.GENERATED_MAGIC_HEADINGS`.
    """
    section = engine.section_magic()
    start = section.index(f"{heading}\n") + len(heading) + 1
    return section[start : section.index("\n", start)]


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
        special, _ = engine.GENERATED_MAGIC_HEADINGS
        with seated_rules(UNLIKE_EITHER_CANONS_MAGIC_RULES):
            body = magic_body(special)
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
        _, borrowing = engine.GENERATED_MAGIC_HEADINGS
        with seated_rules(UNLIKE_EITHER_CANONS_MAGIC_RULES):
            body = magic_body(borrowing)
        self.assertEqual(
            body,
            "Spend all remaining mana, borrow the rest as Debt, up to 5 owed in all. "
            "Debt Roll after the spell: 2d8 ≤ (WIS + INT − 3 × Debt). A natural 2 "
            "or 16 always fails. A failed Debt Roll is an Arcane surge, a Runaway; the "
            "spell has already resolved. Mana handed back refills the pool; the "
            "Debt stays owed.",
        )

    def test_a_runaway_named_runaway_is_named_once(self) -> None:
        _, borrowing = engine.GENERATED_MAGIC_HEADINGS
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
            body = magic_body(borrowing)
        self.assertEqual(
            body,
            "Spend all remaining mana, borrow the rest as Debt. Debt Roll after the "
            "spell: 2d8 ≤ (WIS + INT − Debt). A failed Debt Roll is a runaway; the "
            "spell has already resolved. Mana handed back refills the pool; the "
            "Debt stays owed.",
        )

    def test_a_debt_roll_with_no_penalty_names_the_attributes_alone(self) -> None:
        _, borrowing = engine.GENERATED_MAGIC_HEADINGS
        rules = UNLIKE_EITHER_CANONS_MAGIC_RULES
        assert rules.borrowing is not None
        no_penalty = replace(
            rules, borrowing=replace(rules.borrowing, penalty_per_debt=0)
        )
        with seated_rules(no_penalty):
            body = magic_body(borrowing)
        self.assertIn(" Debt Roll after the spell: 2d8 ≤ (WIS + INT). ", body)

    def test_no_tiers_no_failing_totals_and_no_borrowing(self) -> None:
        with seated_rules(INVENTED_RULES):
            bodies = [
                magic_body(heading) for heading in engine.GENERATED_MAGIC_HEADINGS
            ]
        self.assertEqual(
            bodies,
            [
                "The attribute alone judges a casting roll: no natural total fails on "
                "its own, a failed cast keeps its mana, and no cast starts a Runaway.",
                "A caster without the mana for a spell cannot cast it; there is no "
                "Borrowing.",
            ],
        )
