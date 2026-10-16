"""Dice fidelity guards: creation and equipment docs vs the code (issue #290).

Doc→engine column of ``docs/dice-fidelity-audit.md``. Each guard parses the
rules markdown (the ``test_rules_consistency`` / ``test_rules_drift`` pattern)
and asserts the code rolls or looks up exactly the documented values, so an
edit to either side without the other fails loudly here.

The audit's seven mismatches are all fixed and closed; nothing on the
doc→engine column stands unresolved (#657). The two that touched this file's
subject matter:

* #293 — the attack service's ``reaches_body`` flag, fixed in ``b2089b5``.
  ``characters/attack.py`` now reads ``outcome["severe"] and final_damage >
  0``, and ``test_attack_service.py::test_a_plain_hit_rolls_damage_once``
  asserts a plain hit does *not* reach Body. This docstring used to say the
  mismatch was filed rather than fixed and that the named test pinned the
  wrong behaviour, which sent a reader to the opposite of the truth.
* #295 — the height demon-die chain, fixed in ``f603664``. The base table is
  guarded here; the chain reading (the first roll is the base, each further
  1 or 6 adjusts it ±4") matches the doc and is pinned by
  ``test_dice.py::test_demon_die_to_height_inches``.
"""

import re
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from characters.dice import starting_coin_for_wealth
from characters.equipment import ARMOR, SHIELDS, WEAPONS, get_shield_bonus
from characters.models import Character
from characters.skills import calculate_attribute_modifier
from common.rolling import Roller
from reference.markdown_tables import table_rows_under_heading as _table_rows
from reference.paths import PUBLIC_RULES_DIR

from .rules_fixtures import (
    DEFAULT_CHARACTER_RULES,
    DEFAULT_SKILL_RULES,
    seated_character_rules,
    seated_skill_rules,
)

RULES = PUBLIC_RULES_DIR
ATTRIBUTES_MD = RULES / "characters" / "attributes.md"
WEALTH_MD = RULES / "characters" / "wealth.md"
LOCATION_MD = RULES / "characters" / "location.md"
DERIVED_POOLS_MD = RULES / "characters" / "derived-pools.md"
WEAPONS_MD = RULES / "equipment" / "weapons.md"
ARMOR_MD = RULES / "equipment" / "armor-and-shields.md"
ATTACK_ROLLS_MD = RULES / "combat" / "action-options" / "attack-rolls.md"

#: The audit page these guards are the executable half of (#290).
AUDIT_MD = Path(settings.BASE_DIR) / "docs" / "dice-fidelity-audit.md"


def _number(cell: str) -> int:
    """A doc number cell as an int — the docs use U+2212 for minus."""
    return int(cell.replace("−", "-").replace("+", ""))


class AttributeModifierDocTest(SimpleTestCase):
    """attributes.md's modifier table vs the default canon's (issue #70)."""

    def test_modifier_table_matches_code(self):
        markdown = ATTRIBUTES_MD.read_text()
        rows = _table_rows(markdown, "## Determining Core Attributes")[1:]
        documented: dict[int, int] = {}
        for attribute_cell, modifier_cell in rows:
            modifier = _number(modifier_cell)
            band = re.fullmatch(r"(\d+)(?:-(\d+))?", attribute_cell)
            assert band is not None, attribute_cell
            low = int(band.group(1))
            high = int(band.group(2) or band.group(1))
            for value in range(low, high + 1):
                documented[value] = modifier
        # The table covers the whole 3d6 span, and so does the canon's.
        self.assertEqual(sorted(documented), list(range(3, 19)))
        table = DEFAULT_SKILL_RULES.attribute_modifier
        self.assertEqual((table.lowest_score, table.highest_score), (3, 18))
        with seated_skill_rules():
            for value, modifier in documented.items():
                self.assertEqual(
                    calculate_attribute_modifier(value),
                    modifier,
                    f"attribute {value} should be {modifier:+d} per attributes.md",
                )


class HeightWeightDocTest(SimpleTestCase):
    """attributes.md's height and weight d6 tables vs the default canon's.

    The height *base* values are guarded here. The demon-die chain semantics
    (what a leading 1/6 does to the base) are pinned by
    ``test_dice.py::test_demon_die_to_height_inches`` (issue #295, fixed:
    the first roll sets the base, extra 1s/6s adjust it ±4").
    """

    @staticmethod
    def _imperial_to_inches(cell: str) -> int:
        match = re.fullmatch(r"(\d+)'(\d+)\"", cell)
        assert match is not None, cell
        feet, inches = match.groups()
        return int(feet) * 12 + int(inches)

    def test_height_table_matches_code(self):
        markdown = ATTRIBUTES_MD.read_text()
        rows = _table_rows(markdown, "**Height:** Choose or roll 1d6:")[1:]
        documented = {int(row[0]): self._imperial_to_inches(row[2]) for row in rows}
        height = DEFAULT_CHARACTER_RULES.height
        self.assertEqual(
            documented,
            dict(enumerate(height.base_inches_by_face, start=1)),
        )

    def test_height_reroll_rule_is_still_documented(self):
        # The ±4" explode wording the demon die implements.
        markdown = ATTRIBUTES_MD.read_text()
        height = DEFAULT_CHARACTER_RULES.height
        self.assertIn(
            f'Roll again on 1: subtract {-height.inches_per_extra_one}" each time',
            markdown,
        )
        self.assertIn(
            f'Roll again on 6: add {height.inches_per_extra_six}" each time', markdown
        )

    def test_weight_table_matches_code(self):
        markdown = ATTRIBUTES_MD.read_text()
        rows = _table_rows(markdown, "**Weight:** Base weight")[1:]
        documented_stones = {int(row[0]): int(row[1]) for row in rows}
        weight = DEFAULT_CHARACTER_RULES.weight
        self.assertEqual(
            documented_stones, dict(enumerate(weight.stones_by_face, start=1))
        )
        # The lbs column is the stones column at the canon's pounds per stone
        # — the conversion ``calculate_final_weight_lbs`` uses.
        self.assertIn(f"A stone is {weight.pounds_per_stone} pounds", markdown)
        for row in rows:
            self.assertEqual(int(row[2]), int(row[1]) * weight.pounds_per_stone)

    def test_final_weight_formula_is_still_documented(self):
        markdown = ATTRIBUTES_MD.read_text()
        divisor = DEFAULT_CHARACTER_RULES.weight.strength_divisor
        self.assertIn(
            f"Final weight = Base weight + (STR ÷ {divisor}) stones", markdown
        )

    def test_weight_is_figured_from_the_attribute_the_page_names(self):
        """The page says STR, so Character.weight_lbs must read STR (#691).

        No rules-consistency test compared the choice of attribute against
        attributes.md, so the code could pass effective_strength -- and did --
        with the corpus and the suite both green.
        """
        markdown = ATTRIBUTES_MD.read_text()
        self.assertIn("Final weight = Base weight + (STR ÷ 2) stones", markdown)
        self.assertNotIn("Effective STR ÷ 2", markdown)

        character = Character(
            strength=14,
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
            weight_roll="3",
            fatigue_roll=7,
            age=68,  # aging.md: STR -2, so effective STR is 12
        )
        with seated_character_rules():
            character.reconcile_aging_penalties()
            self.assertEqual(character.effective_strength, 12)
            # 10 stones of base weight + 14 // 2 = 17 stones, at 14 lbs a stone.
            self.assertEqual(character.weight_lbs, 17 * 14)


class WealthDocTest(SimpleTestCase):
    """wealth.md's percentile table vs the default canon's wealth table."""

    def test_wealth_bands_match_code(self):
        markdown = WEALTH_MD.read_text()
        rows = _table_rows(markdown, "# Wealth")[1:]
        for percentile_cell, level, coin_cell in rows:
            low, high = (int(part) for part in percentile_cell.split("-"))
            wealth = DEFAULT_CHARACTER_RULES.wealth
            for roll in range(max(low, 1), high + 1):
                self.assertEqual(
                    wealth.row_for(roll).label,
                    level,
                    f"d100={roll} should be {level} per wealth.md",
                )
            if coin_cell.isdigit():
                row = wealth.row_labelled(level)
                assert row is not None
                self.assertEqual(row.coin, int(coin_cell))

    def test_merchant_coin_is_100_plus_percentile(self):
        markdown = WEALTH_MD.read_text()
        self.assertIn("100 + percentile dice", markdown)
        # Two identically seeded rollers produce the same d100, so the
        # expected bonus is known without stubbing the roll service.
        bonus = Roller(seed=290).roll("1d100", purpose="d100").total
        with seated_character_rules():
            coin, detail = starting_coin_for_wealth("Merchant", Roller(seed=290))
        self.assertEqual(coin, 100 + bonus)
        self.assertEqual(detail, f"100 + d100({bonus}) = {100 + bonus}")


class LocationDocTest(SimpleTestCase):
    """location.md's percentile table vs the default canon's location table.

    The location *effects* (granted skill, literacy modifier, display form)
    are already guarded by ``test_rules_consistency.LocationEffectsRuleTest``;
    this covers the d100 bands themselves.
    """

    def test_location_bands_match_code(self):
        markdown = LOCATION_MD.read_text()
        rows = _table_rows(markdown, "# Location")[1:]
        for percentile_cell, location in rows:
            parts = [int(part) for part in percentile_cell.split("-")]
            low, high = parts[0], parts[-1]
            for roll in range(max(low, 1), high + 1):
                self.assertEqual(
                    DEFAULT_CHARACTER_RULES.location.row_for(roll).label,
                    location,
                    f"d100={roll} should be {location} per location.md",
                )


class DerivedPoolFormulaTest(SimpleTestCase):
    """derived-pools.md's Fatigue/Body formulas vs the model properties."""

    @staticmethod
    def _character(**overrides) -> Character:
        fields = {
            "strength": 12,
            "dexterity": 14,
            "intelligence": 11,
            "wisdom": 13,
            "constitution": 15,
            "charisma": 10,
            "fatigue_roll": 7,
            "age": 16,  # no aging penalties: effective == base
        }
        fields.update(overrides)
        return Character(**fields)

    def test_fatigue_formula_is_documented_and_matched(self):
        markdown = DERIVED_POOLS_MD.read_text()
        fatigue_roll = DEFAULT_CHARACTER_RULES.fatigue_roll
        self.assertIn(
            f"Fatigue = {' + '.join(fatigue_roll.attributes)} + "
            f"({' or '.join(fatigue_roll.best_of)}, whichever is higher) + "
            f"{fatigue_roll.dice}",
            markdown,
        )
        with seated_character_rules():
            character = self._character()
            self.assertEqual(character.fatigue, 15 + 13 + 11 + max(14, 12) + 7)
            # "Whichever is higher": swap so STR wins.
            strong = self._character(strength=17, dexterity=9)
            self.assertEqual(strong.fatigue, 15 + 13 + 11 + 17 + 7)

    def test_body_formula_is_documented_and_matched(self):
        """Body against literals, so it can fail independently of Fatigue.

        This used to assert ``character.body == -(-fatigue * 2 // 3)`` with
        ``fatigue`` read off the same character -- the exact expression
        ``Character.body`` computes, taken from the property it depends on.
        It could not tell a correct Body from one faithfully tracking a wrong
        Fatigue, nor catch a rewrite that kept the shape and reversed the
        rounding (issue #692).
        """
        markdown = DERIVED_POOLS_MD.read_text()
        self.assertIn("Body = 2/3 x Fatigue (round up)", markdown)
        self.enterContext(seated_character_rules())
        character = self._character()
        # CON 15 + WIS 13 + INT 11 + DEX 14 (higher than STR 12) + 7 = 60.
        self.assertEqual(character.fatigue, 60)
        # Two thirds of 60 is exactly 40.
        self.assertEqual(character.body, 40)
        # And a Fatigue that does not divide evenly rounds up, not down:
        # 61 * 2 / 3 is 40.67, so Body is 41.
        odd = self._character(fatigue_roll=8)
        self.assertEqual(odd.fatigue, 61)
        self.assertEqual(odd.body, 41)


class WeaponTableDocTest(SimpleTestCase):
    """weapons.md's Damage and STR columns vs the equipment catalog.

    Every row of every weapon table — the unrollable entries (club "Varies",
    cestus "By STR", torch "1d6 fire", lasso "Varies", bola "—") included,
    since the attack service's refusal of them cites exactly these values.
    """

    # weapons.md name -> catalog name, where they differ.
    NAME_OVERRIDES = {"Torch": "Torch (weapon)"}

    WEAPON_TABLE_HEADINGS = (
        "## Knives and Swords",
        "## Axes/Hammers/Maces",
        "## Missile Weapons",
        "## Pole Weapons",
        "## Unusual Weapons",
    )

    @staticmethod
    def _doc_name(cell: str) -> str:
        # Strip the thrown/two-handed daggers and the comma between them
        # ("Spear (2 hands)††,†" -> "Spear (2 hands)").
        return cell.replace("††", "").replace("†", "").strip(" ,")

    def _documented_rows(self) -> list[tuple[str, str, str]]:
        markdown = WEAPONS_MD.read_text()
        rows: list[tuple[str, str, str]] = []
        for heading in self.WEAPON_TABLE_HEADINGS:
            for row in _table_rows(markdown, heading)[1:]:
                rows.append((self._doc_name(row[0]), row[1], row[2]))
        return rows

    def test_every_documented_weapon_matches_the_catalog(self):
        catalog_by_name = {item["name"]: item for item in WEAPONS.values()}
        documented = self._documented_rows()
        self.assertEqual(len(documented), len(WEAPONS))
        for doc_name, damage_cell, str_cell in documented:
            name = self.NAME_OVERRIDES.get(doc_name, doc_name)
            self.assertIn(name, catalog_by_name, f"{name} missing from catalog")
            item = catalog_by_name[name]
            documented_damage = damage_cell.replace("−", "-")
            catalog_damage = item.get("damage")
            if documented_damage == "—":
                self.assertIsNone(
                    catalog_damage, f"{name}: doc has no damage, catalog does"
                )
            else:
                self.assertEqual(
                    (catalog_damage or "").casefold(),
                    documented_damage.casefold(),
                    f"{name}: damage drifted between weapons.md and the catalog",
                )
            if str_cell == "—":
                self.assertIsNone(
                    item.get("str_req"), f"{name}: doc has no STR requirement"
                )
            else:
                self.assertEqual(
                    item.get("str_req"),
                    int(str_cell),
                    f"{name}: str_req drifted between weapons.md and the catalog",
                )


class ArmourShieldTableDocTest(SimpleTestCase):
    """armor-and-shields.md's Stops and DEX columns vs the catalog."""

    def test_every_documented_item_matches_the_catalog(self):
        markdown = ARMOR_MD.read_text()
        rows = _table_rows(markdown, "# Armor and Shields")[1:]
        catalog_by_name = {item["name"]: item for item in {**ARMOR, **SHIELDS}.values()}
        self.assertEqual(len(rows), len(catalog_by_name))
        for row in rows:
            name, stops_cell, dex_cell = row[0], row[1], row[2]
            self.assertIn(name, catalog_by_name, f"{name} missing from catalog")
            item = catalog_by_name[name]
            self.assertEqual(
                item["stops"],
                int(stops_cell),
                f"{name}: stops drifted between armor-and-shields.md and catalog",
            )
            self.assertEqual(
                item["dex_penalty"],
                _number(dex_cell),
                f"{name}: DEX penalty drifted between doc and catalog",
            )


class ShieldBonusDocTest(SimpleTestCase):
    """attack-rolls.md's shield TN bonuses vs the catalog's shield_bonus."""

    DOCUMENTED_BONUSES = {
        "small_shield": 1,
        "spike_shield": 1,
        "large_shield": 2,
        "tower_shield": 3,
        "main_gauche": 1,
    }

    def test_shield_tn_line_is_still_documented(self):
        markdown = ATTACK_ROLLS_MD.read_text()
        self.assertIn(
            "small/spike shield +1, large shield +2,\n  tower shield +3, "
            "main-gauche +1 (parry)",
            markdown,
        )

    def test_catalog_shield_bonuses_match_the_documented_line(self):
        self.assertEqual(set(self.DOCUMENTED_BONUSES), set(SHIELDS))
        for shield_id, bonus in self.DOCUMENTED_BONUSES.items():
            self.assertEqual(get_shield_bonus(shield_id), bonus, shield_id)


class AuditPageReportsClosedIssuesAsClosedTest(SimpleTestCase):
    """``docs/dice-fidelity-audit.md`` cannot go on filing fixed mismatches.

    The audit is the document the repo points at to answer "does the code
    roll what the rules say". All seven mismatches it filed were fixed
    within days, and for eighteen months nothing noticed that the page —
    and this module's own docstring — still reported them as outstanding, so
    a reader was told the opposite of the truth for those rows (#657).

    This does not forbid the page recording a *new* mismatch; that is what
    the column is for. It forbids one of these seven, which are closed and
    cannot reopen without somebody filing a fresh issue.
    """

    #: The issues the audit filed, every one of them closed (#291–#297).
    settled_issues = frozenset({"291", "292", "293", "294", "295", "296", "297"})

    def test_no_settled_issue_is_still_reported_as_a_mismatch(self):
        still_filed = sorted(
            set(re.findall(r"MISMATCH\(#(\d+)\)", AUDIT_MD.read_text()))
            & self.settled_issues
        )
        self.assertEqual(
            still_filed,
            [],
            "the audit still reports these closed issues as outstanding "
            "mismatches; re-read the rows against the code and mark them "
            "VERIFIED with the test that guards each",
        )

    def test_this_modules_docstring_does_not_repeat_the_stale_claim(self):
        """The docstring carried the same wrong claim the audit did, and
        pointed a reader at ``test_a_plain_hit_rolls_damage_once`` as a test
        pinning the *unfixed* behaviour. It asserts the opposite."""
        module_docstring = sys.modules[__name__].__doc__ or ""
        self.assertNotIn("Known mismatches are filed, not fixed", module_docstring)
        self.assertIn("all fixed and closed", module_docstring)
