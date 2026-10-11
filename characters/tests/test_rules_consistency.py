"""Drift guards between the Python rule tables and the markdown rulebook.

The numbers that drive prior-experience simulation live in
``characters/skills.py`` (``TRACK_DATA``, ``AGING_TABLE``, ``EXPERIENCE_LEVELS``,
``SKILL_POINTS_PER_LEVEL``, ``SKILL_TRACKS``) and are *also* printed for players
in ``reference/content/public-rules/characters/prior-experience/``. They have no
shared source, so editing one without the other would silently diverge. These
tests parse the markdown tables and assert they still match the code.

The weapon-skill rule (issue #119) is guarded here too: the skills glossary and
the attack-rolls page must both state the ruling that ``characters.combat`` and
``characters.skills`` implement — **+1 per skill level with no cap**, **half the
level (rounded down)** with another weapon of the same class, and **triangular
level costs** (level N costs N(N+1)/2 points).

Arcane's exception to that cost (issue #154) is guarded the same way: the
glossary, the attack-rolls page, and the Channel page all state that Arcane
costs double, and ``characters.skills`` has to charge N(N+1) for it.

The Physical/Mental composite stats (issue #144) are guarded here too:
``derived-pools.md`` must print the same formulas as ``Character.physical``
and ``Character.mental`` — both built from *effective* (aging-adjusted)
attributes, except CHA, which the model never ages.

Counterspell escalation (issue #926) is guarded as a pair: the public
Counterspell page and the canon's ``magic-mechanics/counterspell.md`` must
agree that the caster escalates after the defender wins (the spell was
countered), never after the caster wins, and that only the caster may.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

from characters import combat
from characters.constants import (
    POINT_BUY_MAX_ATTRIBUTE,
    POINT_BUY_MIN_ATTRIBUTE,
    POINT_BUY_TOTAL,
    SEVERE_INJURY_BAND_TOP,
)
from characters.dice import (
    COMMONER_TABLE,
    LOCATION_LITERACY_MODIFIER,
    LOCATION_STARTING_SKILL,
    MERCHANT_TABLE,
    NOBILITY_TABLE,
    SOCIAL_CLASS_TABLE,
)
from characters.equipment import EQUIPMENT_CATALOG
from characters.models import Character
from characters.movement import GAIT_BASE_DISTANCES
from characters.skills import (
    AGING_TABLE,
    ARCANE_SKILL_NAME,
    EXPERIENCE_LEVELS,
    SKILL_POINTS_PER_LEVEL,
    SKILL_TRACKS,
    SURVIVAL_DICE_COUNT,
    TRACK_DATA,
    age_for_aging_stage,
    aging_stage_for_age,
    get_aging_penalties,
    get_skill_level,
    normalize_skill_name,
    points_for_level,
    skill_cost_multiplier,
)
from common.test_helpers import CANON_EXPECTATIONS, skip_unless_canon_expects
from reference.markdown_tables import is_separator_row, split_table_row
from reference.paths import CONTENT_DIR, DM_RULES_DIR

REFERENCE_CONTENT = CONTENT_DIR
PUBLIC_RULES = REFERENCE_CONTENT / "public-rules"
CHANNEL_RULES = DM_RULES_DIR / "magic-mechanics" / "channel.md"
MANA_POOL_RULES = DM_RULES_DIR / "magic-mechanics" / "mana-pool.md"
COUNTERSPELL_CANON_RULES = DM_RULES_DIR / "magic-mechanics" / "counterspell.md"
MAGIC_MECHANICS_INDEX = DM_RULES_DIR / "magic-mechanics.md"
MAGIC_RULES = PUBLIC_RULES / "magic.md"
COUNTERSPELL_PUBLIC_RULES = PUBLIC_RULES / "magic" / "counterspell.md"
CHARACTERS_RULES = PUBLIC_RULES / "characters"
PRIOR_EXPERIENCE = CHARACTERS_RULES / "prior-experience"
SKILLS_GLOSSARY = PRIOR_EXPERIENCE / "skills-glossary.md"
ATTACK_ROLLS = PUBLIC_RULES / "combat" / "action-options" / "attack-rolls.md"
ATTRIBUTES_RULES = CHARACTERS_RULES / "attributes.md"
INJURY_RULES = CHARACTERS_RULES / "injury-thresholds-death.md"
DERIVED_POOLS_RULES = CHARACTERS_RULES / "derived-pools.md"
LOCATION_RULES = CHARACTERS_RULES / "location.md"
PROVENANCE_RULES = CHARACTERS_RULES / "provenance.md"
EQUIPMENT_RULES = PUBLIC_RULES / "equipment"
WEAPONS_RULES = EQUIPMENT_RULES / "weapons.md"
GEAR_RULES = EQUIPMENT_RULES / "gear.md"
MOVEMENT_RULES = PUBLIC_RULES / "combat" / "action-options" / "movement.md"

# The glossary prints the level ladder in Roman numerals.
ROMAN_TO_LEVEL = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6}

# Player-facing track headings -> the keys used in skills.py.
TRACK_DISPLAY_TO_KEY = {
    "Merchant": "merchant",
    "Campaigner": "campaigner",
    "Laborer": "laborer",
    "Magic": "magic",
    "Underworld": "underworld",
    "Civil Service": "civil_service",
    "Craft": "craft",
    "Hunter/Gatherer": "hunter_gatherer",
}

# Aging row labels -> attribute keys.
AGING_LABEL_TO_ATTRIBUTE = {
    "STR": "strength",
    "DEX": "dexterity",
    "INT": "intelligence",
    "WIS": "wisdom",
    "CON": "constitution",
}


def _normalize_number(text: str) -> str:
    """Strip the rulebook's Unicode minus, en dash, and thousands commas."""
    return text.replace("−", "-").replace("–", "-").replace(",", "").strip()


def _rows_from_text(text: str) -> list[list[str]]:
    """Return each markdown table row in ``text`` as a list of stripped cells.

    The ``|---|---|`` separator rows are dropped.
    """
    rows: list[list[str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = split_table_row(line)
        if is_separator_row(cells):
            continue
        rows.append(cells)
    return rows


def _table_rows(markdown_path: Path) -> list[list[str]]:
    """Return each markdown table row as a list of stripped cells.

    The ``|---|---|`` separator rows are dropped.
    """
    return _rows_from_text(markdown_path.read_text(encoding="utf-8"))


def _parse_roll_range(cell: str) -> tuple[int, int]:
    """Parse a rules-table roll cell ("6-12" or a bare "1") into (lo, hi)."""
    normalized = _normalize_number(cell)
    if "-" in normalized:
        lo_str, hi_str = normalized.split("-", 1)
        return int(lo_str), int(hi_str)
    value = int(normalized)
    return value, value


def _ranges_from_thresholds(table: list[tuple[int, str]]) -> list[tuple[int, int, str]]:
    """Turn a dice.py ``(threshold, label)`` table into non-overlapping ranges.

    Mirrors ``characters.dice.lookup_table``'s ``roll <= threshold`` semantics:
    the first entry covers 1..threshold, and each following entry covers
    (previous threshold + 1)..threshold.
    """
    ranges = []
    lo = 1
    for threshold, label in table:
        ranges.append((lo, threshold, label))
        lo = threshold + 1
    return ranges


class RulesConsistencyTest(SimpleTestCase):
    """Each markdown rule table must match the Python source of truth."""

    def test_survivability_table_matches_track_data(self):
        """survivability.md survival/XP/money must match TRACK_DATA."""
        rows = _table_rows(PRIOR_EXPERIENCE / "survivability.md")
        header, *data_rows = rows
        self.assertEqual(header[0], "Track")

        seen = set()
        for track_display, survival, xp_per_year, money in data_rows:
            key = TRACK_DISPLAY_TO_KEY[track_display]
            seen.add(key)
            expected = TRACK_DATA[key]
            survival_target = int(_normalize_number(survival).rstrip("+"))
            money_multiplier = int(_normalize_number(money).split()[0])
            self.assertEqual(survival_target, expected["survivability"], key)
            self.assertEqual(
                int(_normalize_number(xp_per_year)), expected["xp_per_year"], key
            )
            self.assertEqual(money_multiplier, expected["money_multiplier"], key)

        self.assertEqual(seen, set(TRACK_DATA), "tracks differ between docs and code")

    def test_experience_levels_table_matches(self):
        """prior-experience.md levels must match EXPERIENCE_LEVELS + per-level rates."""
        rows = _table_rows(CHARACTERS_RULES / "prior-experience.md")

        xp_by_level: dict[int, int] = {}
        points_by_level: dict[int, int] = {}
        for cells in rows:
            if not cells[0].isdigit():
                continue
            level = int(cells[0])
            xp_by_level[level] = int(_normalize_number(cells[1]))
            points = _normalize_number(cells[2])
            if points not in ("—", "-", ""):  # em dash = "no points at level 1"
                points_by_level[level] = int(points)

        self.assertEqual(xp_by_level, EXPERIENCE_LEVELS)
        self.assertEqual(points_by_level, SKILL_POINTS_PER_LEVEL)

    def test_aging_table_matches(self):
        """aging.md (transposed, keyed by age) must match AGING_TABLE."""
        rows = _table_rows(PRIOR_EXPERIENCE / "aging.md")
        header = rows[0]
        self.assertEqual(header[0], "Age")
        ages = [int(value) for value in header[1:]]

        parsed = {age: {} for age in ages}
        for cells in rows[1:]:
            label = cells[0]
            self.assertIn(label, AGING_LABEL_TO_ATTRIBUTE, label)
            attribute = AGING_LABEL_TO_ATTRIBUTE[label]
            for age, value in zip(ages, cells[1:]):
                parsed[age][attribute] = int(_normalize_number(value))

        self.assertEqual(
            parsed, {age: AGING_TABLE[aging_stage_for_age(age)] for age in ages}
        )

    def test_aging_age_columns_step_four_years_from_the_start_age(self):
        """aging.md's age columns must be start_age + 4*stage (issues #124, #221).

        Tied to the real ``Character.age`` field default rather than a
        hardcoded 16, so a change to the starting age would fail this test
        instead of silently drifting from the printed table.
        """
        start_age = Character._meta.get_field("age").default

        rows = _table_rows(PRIOR_EXPERIENCE / "aging.md")
        ages = [int(value) for value in rows[0][1:]]

        steps = {ages[i + 1] - ages[i] for i in range(len(ages) - 1)}
        self.assertEqual(
            steps, {4}, "aging.md age columns are not a uniform 4-year step"
        )

        for age in ages:
            stage = aging_stage_for_age(age)
            self.assertEqual(age, start_age + 4 * stage, f"age {age}")
            self.assertEqual(age_for_aging_stage(stage), age)

    def test_survivability_states_the_survival_dice(self):
        """survivability.md must print the dice the simulator actually rolls."""
        text = (PRIOR_EXPERIENCE / "survivability.md").read_text(encoding="utf-8")
        self.assertIn(f"Roll {SURVIVAL_DICE_COUNT}d6 +", text)
        self.assertIn(f"Survival ({SURVIVAL_DICE_COUNT}d6)", text)

    def test_aging_escalation_prose_matches_formula_past_the_table(self):
        """aging.md's worked past-68 examples must match get_aging_penalties.

        Guards the escalation rule (issues #124, #221): AGING_TABLE stops at
        age 68, but penalties must keep escalating by formula, not freeze. The
        doc's prose examples ("age 76 totals STR/DEX/CON at -4 and INT/WIS at
        -2...") are parsed and checked against the code.
        """
        last_tabulated_age = age_for_aging_stage(max(AGING_TABLE))
        text = (PRIOR_EXPERIENCE / "aging.md").read_text(encoding="utf-8")
        pattern = re.compile(
            r"age (\d+) totals\s+STR/DEX/CON at ([−-]\d+)\s+and INT/WIS at ([−-]\d+)"
        )
        matches = pattern.findall(text)
        self.assertGreaterEqual(
            len(matches), 2, "no worked past-table aging examples found in aging.md"
        )

        for age_str, physical_str, mental_str in matches:
            age = int(age_str)
            self.assertGreater(
                age, last_tabulated_age, "example must be past the table"
            )
            physical = int(_normalize_number(physical_str))
            mental = int(_normalize_number(mental_str))
            with self.subTest(age=age):
                penalties = get_aging_penalties(aging_stage_for_age(age))
                assert penalties is not None
                self.assertEqual(penalties["strength"], physical)
                self.assertEqual(penalties["dexterity"], physical)
                self.assertEqual(penalties["constitution"], physical)
                self.assertEqual(penalties["intelligence"], mental)
                self.assertEqual(penalties["wisdom"], mental)

    def test_aging_penalties_never_freeze_past_term_13(self):
        """Regression guard: aging must keep escalating past term 13, never
        freeze the way it used to before AGING_TABLE was extended by
        formula (issue #124)."""
        previous = get_aging_penalties(13)
        assert previous is not None
        for term in range(14, 40):
            current = get_aging_penalties(term)
            assert current is not None, f"term {term} has no aging penalties"
            self.assertLessEqual(current["strength"], previous["strength"], term)
            self.assertLessEqual(
                current["intelligence"], previous["intelligence"], term
            )
            previous = current

        # It must actually keep moving, not just hold flat forever.
        term_13 = get_aging_penalties(13)
        term_39 = get_aging_penalties(39)
        assert term_13 is not None
        assert term_39 is not None
        self.assertLess(term_39["strength"], term_13["strength"])
        self.assertLess(term_39["intelligence"], term_13["intelligence"])

    def test_skill_tracks_table_matches(self):
        """skill-tracks.md must match SKILL_TRACKS for every d20 position (1-20)."""
        rows = _table_rows(PRIOR_EXPERIENCE / "skill-tracks.md")
        header = rows[0]
        self.assertEqual(header[0], "Year")
        track_keys = [TRACK_DISPLAY_TO_KEY[name] for name in header[1:]]

        years_checked = 0
        for cells in rows[1:]:
            if not cells[0].isdigit():
                continue
            year = int(cells[0])
            years_checked += 1
            for key, skill in zip(track_keys, cells[1:]):
                self.assertEqual(
                    skill, SKILL_TRACKS[key][year - 1], f"{key} year {year}"
                )

        # The player table covers the 20 rollable positions (a d20 never hits 21).
        self.assertEqual(years_checked, 20)


def _glossary_level_row(label: str) -> dict[int, str]:
    """Read one row of the glossary's level tables, keyed by skill level.

    Both tables are headed ``| Level | I | II | ... |``; ``label`` picks the row
    beneath it ("Bonus" or "Cumulative cost").
    """
    levels: list[int] = []
    for cells in _table_rows(SKILLS_GLOSSARY):
        if cells[0] == "Level":
            levels = [ROMAN_TO_LEVEL[numeral] for numeral in cells[1:]]
            continue
        if cells[0] == label:
            return dict(zip(levels, cells[1:]))
    raise AssertionError(f"no '{label}' row found in {SKILLS_GLOSSARY.name}")


class PointBuyRuleTest(SimpleTestCase):
    """The published point-buy budget must match the code (issue #123)."""

    def test_published_budget_matches_the_code(self):
        """The rules' point total is the one character creation enforces."""
        text = ATTRIBUTES_RULES.read_text(encoding="utf-8")
        budgets = re.findall(r"divide \*\*(\d+) points\*\*", text)
        self.assertEqual(budgets, [str(POINT_BUY_TOTAL)])

    def test_published_bounds_match_the_code(self):
        """The rules state the per-attribute range the form enforces."""
        text = ATTRIBUTES_RULES.read_text(encoding="utf-8")
        self.assertIn(
            f"between {POINT_BUY_MIN_ATTRIBUTE} and {POINT_BUY_MAX_ATTRIBUTE}", text
        )


class WeaponSkillRuleTest(SimpleTestCase):
    """The published weapon-skill ruling must match the code (issue #119)."""

    def test_glossary_bonus_ladder_matches_skill_bonus(self):
        """The glossary's per-level bonus table is +1 per level, as code says."""
        printed = _glossary_level_row("Bonus")
        self.assertGreaterEqual(len(printed), 5)
        for level, bonus in printed.items():
            with self.subTest(level=level):
                self.assertEqual(
                    int(_normalize_number(bonus).lstrip("+")),
                    combat.skill_bonus(level),
                )

    def test_glossary_cost_table_matches_points_for_level(self):
        """The glossary's cumulative costs are the triangular numbers in code."""
        printed = _glossary_level_row("Cumulative cost")
        self.assertGreaterEqual(len(printed), 5)
        for level, cost in printed.items():
            with self.subTest(level=level):
                self.assertEqual(int(_normalize_number(cost)), points_for_level(level))

    def test_glossary_states_the_levels_are_uncapped(self):
        """A reinstated cap in the prose would contradict the code."""
        text = SKILLS_GLOSSARY.read_text(encoding="utf-8")
        self.assertIn("no cap", text.lower())
        self.assertNotIn("V (max)", text)

    def test_glossary_weapon_classes_match_the_combat_matrix(self):
        """The half-transfer table uses the same classes as combat.MATRIX."""
        printed = {
            cells[0]
            for cells in _table_rows(SKILLS_GLOSSARY)
            if len(cells) == 2 and cells[0] in combat.MATRIX
        }
        self.assertEqual(printed, set(combat.MATRIX))

    def test_attack_rolls_weapon_skill_row_matches_code(self):
        """attack-rolls.md must print the code's per-level bonus and the half."""
        rows = [
            cells
            for cells in _table_rows(ATTACK_ROLLS)
            if cells and cells[0] == "Weapon skill"
        ]
        self.assertEqual(len(rows), 1, "attack-rolls.md lost its Weapon skill row")
        value = rows[0][1]
        self.assertIn(
            f"+{combat.SKILL_BONUS_PER_LEVEL} per skill level",
            value,
        )
        self.assertIn("half your level", value.lower())

    def test_attack_rolls_drops_the_old_tier_scale(self):
        """The retired Trained/Expert/Master +2/+4/+6 scale must stay gone."""
        text = ATTACK_ROLLS.read_text(encoding="utf-8")
        self.assertNotIn("Trained +2", text)
        self.assertNotIn("Master +6", text)
        self.assertIsNone(re.search(r"\+2 per level", text))


class CombatMatrixRuleTest(SimpleTestCase):
    """attack-rolls.md's Base Target Number Matrix must match code (issue #104).

    This copy is the highest-blast-radius one: ``RULES_DIR`` (default
    ``reference/content/public-rules``) is loaded verbatim into the live GM's
    context, so a drift here would silently mis-instruct the model instead of
    just misleading a human reader. The proposal spec's matrix already has an
    equivalent guard (``characters/tests/test_combat.py``'s
    ``SpecDriftGuardTests``); this is the same check for the page players and
    the GM actually see.
    """

    def test_attack_rolls_matrix_matches_combat_matrix(self):
        parsed: dict[str, dict[str, int]] = {}
        for cells in _table_rows(ATTACK_ROLLS):
            if len(cells) != 1 + len(combat.ARMOUR_TIERS):
                continue
            label, values = cells[0], cells[1:]
            if label not in combat.MATRIX:
                continue
            try:
                numbers = [int(_normalize_number(value)) for value in values]
            except ValueError:
                continue
            parsed[label] = dict(zip(combat.ARMOUR_TIERS, numbers))

        self.assertEqual(
            set(parsed),
            set(combat.MATRIX),
            "attack-rolls.md's Base Target Number Matrix drifted from combat.MATRIX",
        )
        self.assertEqual(parsed, combat.MATRIX)


class ArcaneSkillCostRuleTest(SimpleTestCase):
    """Arcane's doubled cost must be stated in the rules and priced in the
    code the same way (issue #154)."""

    def test_glossary_states_the_arcane_exception(self):
        """The glossary prints the exception the code implements: N(N+1)."""
        text = SKILLS_GLOSSARY.read_text(encoding="utf-8")
        exception = re.search(r"\*\*Exception:\*\*(.+?)\n\n", text, re.DOTALL)
        if exception is None:
            self.fail("the glossary lost its Arcane exception paragraph")
        prose = " ".join(exception.group(1).split())
        self.assertIn("Arcane", prose)
        self.assertIn("double", prose)
        self.assertIn("N(N+1)", prose.replace(" ", ""))

    def test_attack_rolls_names_the_arcane_exception(self):
        """attack-rolls.md prints the uniform cost; it must flag the one skill
        that does not follow it, or players read a rule the code contradicts."""
        text = ATTACK_ROLLS.read_text(encoding="utf-8")
        self.assertRegex(text, r"\*\*Arcane\*\* costs double")

    def test_code_prices_arcane_at_double_the_ordinary_ladder(self):
        """Reaching level N in Arcane costs N(N+1), twice the usual N(N+1)/2."""
        for level in range(1, 11):
            with self.subTest(level=level):
                self.assertEqual(points_for_level(level, "Arcane"), level * (level + 1))
                self.assertEqual(
                    points_for_level(level, "Arcane"), 2 * points_for_level(level)
                )

    def test_published_arcane_ladder_matches_get_skill_level(self):
        """The doubled prices the glossary implies are the ones the level
        lookup charges — the boundary point buys the level, one less does not."""
        for level in range(1, 11):
            cost = level * (level + 1)
            with self.subTest(level=level):
                self.assertEqual(get_skill_level(cost, "Arcane"), level)
                self.assertEqual(get_skill_level(cost - 1, "Arcane"), level - 1)

    # What the canon's own Channel page must say for the doubled cost to have
    # its reason; the canon supplies the phrases (issue #921), since the rule
    # is on no public page.
    @skip_unless_canon_expects("channel_rule_phrases")
    def test_channel_page_still_gives_the_doubled_cost_its_reason(self):
        text = CHANNEL_RULES.read_text(encoding="utf-8")
        for phrase in CANON_EXPECTATIONS["channel_rule_phrases"]:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_the_doubled_skill_is_spelled_the_way_storage_spells_it(self):
        """The code matches Arcane by normalized name; the rules' spelling of
        it must survive that normalization or the exception never fires."""
        self.assertEqual(normalize_skill_name("Arcane"), ARCANE_SKILL_NAME)
        self.assertEqual(skill_cost_multiplier("Arcane"), 2)
        self.assertEqual(skill_cost_multiplier("Weapon"), 1)


class InjuryThresholdRuleTest(SimpleTestCase):
    """Character.injury_thresholds must match injury-thresholds-death.md."""

    def test_severe_band_top_matches_the_rules_table(self):
        """The flat -2 band's top comes from the "Between 1 and N" row."""
        printed_tops = [
            int(found.group(1))
            for cells in _table_rows(INJURY_RULES)
            if cells and (found := re.fullmatch(r"Between 1 and (\d+)", cells[0]))
        ]
        self.assertEqual(printed_tops, [SEVERE_INJURY_BAND_TOP])

    def test_table_still_lists_every_condition_the_code_computes(self):
        labels = {cells[0] for cells in _table_rows(INJURY_RULES)}
        for label in (
            "Down to 1/2 (but above 5)",
            "Between 1 and 5",
            "0",
            "Negative 1/2 starting value",
            "Negative full starting value",
        ):
            self.assertIn(label, labels)

    def test_worked_example_matches_the_computed_thresholds(self):
        """The md's Body 30 example: unconscious 0, saves -15, penalized -30."""
        # _normalize_number drops thousands commas, so the sentence commas go
        # with them -- match with the comma optional.
        text = _normalize_number(INJURY_RULES.read_text(encoding="utf-8"))
        pool = re.search(r"Body (\d+) goes unconscious at (\d+)\.", text)
        survival = re.search(r"At (-\d+),? they begin making survival saves", text)
        penalized = re.search(r"At (-\d+),? saves continue but are now penalized", text)
        if pool is None or survival is None or penalized is None:
            self.fail("injury-thresholds-death.md lost its worked example")

        thresholds = Character(constitution=15)._pool_injury_thresholds(
            int(pool.group(1))
        )
        self.assertEqual(thresholds["unconscious_at"], int(pool.group(2)))
        self.assertEqual(thresholds["survival_save_at"], int(survival.group(1)))
        self.assertEqual(thresholds["penalized_save_at"], int(penalized.group(1)))

    def test_derived_pools_wording_matches_this_table_at_zero(self):
        """Issue #167: derived-pools.md said Body goes "semi-unconscious" at
        0 while this table -- the canonical source per #130 -- says
        "Unconscious" for both Fatigue and Body. Both pool sections must use
        this table's wording, not a synonym that can quietly drift."""
        text = DERIVED_POOLS_RULES.read_text(encoding="utf-8")
        self.assertNotIn("semi-unconscious", text.lower())
        self.assertEqual(
            text.lower().count("unconscious"),
            2,
            "expected exactly one 'unconscious' each in the Fatigue and Body sections",
        )

    def test_body_crit_line_matches_the_confirmed_natural_20_rule(self):
        """Issue #294: derived-pools.md's Body section said "Critical hits
        (roll of 3 or 4)" reduce Body -- the old 3d6 roll-under crit. The
        live system is d20; only a *severe* critical (a confirmed natural
        20, see attack-rolls.md, guarded by CritFumbleRuleTest below) reaches
        Body. Pin the corrected wording so the stale roll-under phrasing
        cannot silently return."""
        text = DERIVED_POOLS_RULES.read_text(encoding="utf-8").lower()
        self.assertNotIn("roll of 3 or 4", text)
        self.assertIn("natural 20", text)
        self.assertIn("severe critical", text)


class PhysicalMentalRuleTest(SimpleTestCase):
    """Character.physical/.mental must match derived-pools.md (#144)."""

    def _derived_pools_text(self) -> str:
        return DERIVED_POOLS_RULES.read_text(encoding="utf-8")

    def test_the_rules_page_prints_the_physical_formula(self):
        self.assertIn(
            "Physical = ceil((Effective STR + Effective DEX + Effective CON) / 2)",
            self._derived_pools_text(),
        )

    def test_the_rules_page_prints_the_mental_formula(self):
        self.assertIn(
            "Mental = ceil((Effective WIS + Effective INT + CHA) / 2)",
            self._derived_pools_text(),
        )

    def test_the_rules_page_states_effective_means_aging_adjusted(self):
        """Players must be told "Effective" isn't the raw rolled attribute.

        Asserts the sentence, not the word: "aging" in text.lower() passed on
        any page mentioning the word once anywhere (issue #682).
        """
        text = self._derived_pools_text()
        self.assertEqual(
            text.count('"Effective" means after aging penalties are applied'),
            2,
            "both the Physical and the Mental section must say it",
        )

    def test_the_rules_page_states_charisma_is_not_aged(self):
        text = self._derived_pools_text()
        self.assertIn("Charisma has no aging penalty", text)

    def test_physical_matches_the_model_for_an_aged_character(self):
        """Aging penalties must actually move Physical, per the printed formula.

        Every expectation here is a literal. It used to build ``expected``
        from the same ``effective_*`` properties ``Character.physical``
        reads, so any fault in those moved both sides of the assertion and
        the test stayed green -- a stored-penalty desync (issue #680), a sign
        error, a double subtraction (issue #682).

        The character is aged through ``age``, which is what aging is keyed
        to; aging.md's age-68 column is STR -2, DEX -2, CON -2.
        """
        character = Character(strength=15, dexterity=14, constitution=13, age=68)
        character.reconcile_aging_penalties()

        self.assertEqual(
            (
                character.effective_strength,
                character.effective_dexterity,
                character.effective_constitution,
            ),
            (13, 12, 11),
        )
        # 13 + 12 + 11 = 36, and ceil(36 / 2) is 18.
        self.assertEqual(character.physical, 18)
        # Unaged, the same character is 15 + 14 + 13 = 42, ceil(42 / 2) = 21.
        unaged = Character(strength=15, dexterity=14, constitution=13)
        self.assertEqual(unaged.physical, 21)

    def test_mental_matches_the_model_and_leaves_charisma_unaged(self):
        """Literals again, for the same reason as Physical above (issue #682).

        aging.md's age-68 column gives INT -1 and WIS -1, and Charisma is
        never aged.
        """
        character = Character(wisdom=15, intelligence=14, charisma=13, age=68)
        character.reconcile_aging_penalties()

        self.assertEqual(
            (
                character.effective_wisdom,
                character.effective_intelligence,
            ),
            (14, 13),
        )
        # 14 + 13 + 13 (Charisma, unaged) = 40, and ceil(40 / 2) is 20.
        self.assertEqual(character.mental, 20)
        # CHA has no aging_*_penalty field, so it can never be discounted here.
        self.assertFalse(hasattr(character, "aging_cha_penalty"))


class LocationEffectsRuleTest(SimpleTestCase):
    """The published location effects must match the tables in dice.py (#131)."""

    def _effects_section(self) -> str:
        """Everything printed under the Location Effects heading."""
        text = LOCATION_RULES.read_text(encoding="utf-8")
        section = text.split("## Location Effects", 1)
        self.assertEqual(
            len(section), 2, "location.md lost its Location Effects section"
        )
        return section[1]

    def _published_effects(self) -> dict[str, str]:
        """Map each origin to the effects line printed under Location Effects.

        The origin pattern allows spaces so a multi-word origin (a future
        "Consult GM" entry, say) is picked up rather than silently skipped.
        """
        return {
            match.group(1): match.group(2).strip()
            for match in re.finditer(
                r"\*\*([\w ]+):\*\*\s*(.+)", self._effects_section()
            )
        }

    def test_every_published_origin_has_coded_effects(self):
        printed = self._published_effects()
        self.assertEqual(set(printed), set(LOCATION_STARTING_SKILL))
        self.assertEqual(set(printed), set(LOCATION_LITERACY_MODIFIER))

    def test_granted_skill_matches_the_published_effect(self):
        # Village prints "Street smarts OR survival (choose)", so this passes for
        # either half by design — the code grants street smarts and the player
        # renames it to survival. The assertion guards the coded name against a
        # rewording of the line, not the choice.
        for origin, line in self._published_effects().items():
            with self.subTest(origin=origin):
                self.assertIn(LOCATION_STARTING_SKILL[origin].lower(), line.lower())

    def test_literacy_modifier_matches_the_published_effect(self):
        for origin, line in self._published_effects().items():
            with self.subTest(origin=origin):
                match = re.search(r"Literacy on INT check(?:\s*\+(\d+))?", line)
                if match is None:
                    self.fail(f"{origin} lost its literacy-check effect")
                printed = int(match.group(1) or 0)
                self.assertEqual(printed, LOCATION_LITERACY_MODIFIER[origin])

    def test_published_effects_state_the_modifier_is_a_penalty(self):
        """The rules must say which way the modifier cuts, not just its size."""
        section = self._effects_section()
        self.assertIn("penalty", section.lower())
        for origin, modifier in LOCATION_LITERACY_MODIFIER.items():
            with self.subTest(origin=origin):
                printed = "3d6 ≤ INT" if not modifier else f"3d6 ≤ INT − {modifier}"
                self.assertIn(printed, section)

    def test_literacy_check_display_matches_the_published_form(self):
        """The sheet prints each origin's check exactly as the rules write it."""
        section = self._effects_section()
        character = Character(strength=10)
        for origin in LOCATION_LITERACY_MODIFIER:
            with self.subTest(origin=origin):
                character.location = origin
                self.assertIn(character.location_literacy_check, section)


class ProvenanceRuleTest(SimpleTestCase):
    """provenance.md's roll ranges and labels must match dice.py.

    The printed tables used to overlap at their boundaries (e.g. "6-12 Duke"
    next to "12-30 March/Border Lords") while the code resolved ties via
    ``lookup_table``'s ``roll <= threshold`` semantics. Issue #142's ruling:
    the code's reading wins, so the tables are rewritten to match it exactly
    and this guard keeps them from drifting apart again.

    Issue #142 also left the rank *labels* unreconciled -- dice.py said
    "March/Border Lord" and "Knight" where provenance.md (and the fuller
    nobility-titles.md rank headings) said "March/Border Lords" and "Knights
    and Warrior Nobility". Issue #167 picked the md's fuller spelling as
    canon and updated dice.py's ``NOBILITY_TABLE`` to match verbatim; this
    guard now checks label text, not just ranges, so they can't drift apart
    again.
    """

    def test_class_split_headings_match_social_class_thresholds(self):
        """The '## N-M ClassName' headings must match SOCIAL_CLASS_TABLE."""
        text = PROVENANCE_RULES.read_text(encoding="utf-8")
        headings = re.findall(r"^## (\d+)-(\d+) (\w+)", text, re.MULTILINE)
        self.assertEqual(len(headings), len(SOCIAL_CLASS_TABLE))

        expected = _ranges_from_thresholds(SOCIAL_CLASS_TABLE)
        for (lo_str, hi_str, name), (exp_lo, exp_hi, exp_class) in zip(
            headings, expected
        ):
            with self.subTest(heading=name):
                self.assertEqual(int(lo_str), exp_lo)
                self.assertEqual(int(hi_str), exp_hi)
                self.assertEqual(name.lower(), exp_class.lower())

    def _section_rows(self, heading_prefix: str) -> list[list[str]]:
        """Rows of the table under the '## <heading_prefix>...' section."""
        text = PROVENANCE_RULES.read_text(encoding="utf-8")
        sections = re.split(r"^## .*$", text, flags=re.MULTILINE)
        headings = re.findall(r"^## (.*)$", text, re.MULTILINE)
        for heading, body in zip(headings, sections[1:]):
            if heading.startswith(heading_prefix):
                return _rows_from_text(body)
        raise AssertionError(f"no '## {heading_prefix}...' section found")

    def _assert_table_matches(
        self, rows: list[list[str]], table: list[tuple[int, str]]
    ):
        data_rows = rows[1:]  # drop the "| Roll | ... |" header row
        expected = _ranges_from_thresholds(table)
        self.assertEqual(len(data_rows), len(expected), data_rows)
        for cells, (exp_lo, exp_hi, exp_label) in zip(data_rows, expected):
            lo, hi = _parse_roll_range(cells[0])
            with self.subTest(cell=cells[0]):
                self.assertEqual((lo, hi), (exp_lo, exp_hi))
                self.assertEqual(cells[1], exp_label)

    def test_nobility_table_ranges_and_labels_match_dice_thresholds(self):
        self._assert_table_matches(self._section_rows("1-10 Nobility"), NOBILITY_TABLE)

    def test_merchant_table_ranges_and_labels_match_dice_thresholds(self):
        self._assert_table_matches(self._section_rows("11-30 Merchant"), MERCHANT_TABLE)

    def test_commoner_table_ranges_and_labels_match_dice_thresholds(self):
        self._assert_table_matches(
            self._section_rows("31-100 Commoner"), COMMONER_TABLE
        )

    def test_ranges_within_each_table_are_non_overlapping(self):
        """Belt-and-suspenders: no two rows in any table may share a roll."""
        for heading_prefix, table in (
            ("1-10 Nobility", NOBILITY_TABLE),
            ("11-30 Merchant", MERCHANT_TABLE),
            ("31-100 Commoner", COMMONER_TABLE),
        ):
            rows = self._section_rows(heading_prefix)[1:]  # drop header row
            ranges = [_parse_roll_range(cells[0]) for cells in rows]
            for (lo_a, hi_a), (lo_b, hi_b) in zip(ranges, ranges[1:]):
                with self.subTest(table=heading_prefix):
                    self.assertEqual(hi_a + 1, lo_b)

    def test_the_roll_requirement_is_documented_as_the_sheet_enforces_it(self):
        """Issue #142 documented 'must roll for Nobility' as a table rule the
        software did not enforce. Since #749 (Spencer's ruling, 2026-09-28)
        the sheet enforces a roll for every class, so the page says that
        instead, and says who may set it."""
        text = " ".join(PROVENANCE_RULES.read_text(encoding="utf-8").lower().split())
        self.assertIn("a player rolls provenance once and cannot choose it", text)
        self.assertIn("only the gm sets it", text)


class EquipmentCostRuleTest(SimpleTestCase):
    """The torch's cost must agree across gear.md, weapons.md, and the catalog.

    Issue #142: weapons.md's Unusual-weapons torch entry used to say 1 (gp),
    contradicting gear.md's 1 cp and the catalog's ``torch`` item. Both torch
    entries in the catalog are now priced at 1 cp (0.01 gp-equivalent).
    """

    def test_gear_and_weapon_torch_cost_1_cp_in_markdown(self):
        gear_rows = _table_rows(GEAR_RULES)
        weapon_rows = _table_rows(WEAPONS_RULES)

        gear_torch = next(row for row in gear_rows if row[0] == "Torch")
        weapon_torch = next(row for row in weapon_rows if row[0].startswith("Torch"))
        self.assertEqual(gear_torch[1], "1 cp")
        self.assertEqual(weapon_torch[3], "1 cp")

    def test_catalog_torch_items_cost_one_copper_piece(self):
        # 1 cp = 0.01 gp-equivalent, per characters.equipment's cost convention.
        one_copper_piece_in_gp = 0.01
        self.assertEqual(EQUIPMENT_CATALOG["torch"]["cost"], one_copper_piece_in_gp)
        self.assertEqual(
            EQUIPMENT_CATALOG["torch_weapon"]["cost"], one_copper_piece_in_gp
        )


class MovementGaitRuleTest(SimpleTestCase):
    """movement.md's gait table and worked example must match
    characters.movement (issue #143)."""

    def test_gait_base_distances_match_the_rules_table(self):
        """Each gait's '<N> + modifier' cell must match GAIT_BASE_DISTANCES."""
        rows = _table_rows(MOVEMENT_RULES)
        speed_cells = {
            cells[0]: cells[1]
            for cells in rows
            if cells and cells[0] in ("Sprint", "Run", "Jog", "Walk")
        }
        self.assertEqual(
            {speed.lower() for speed in speed_cells}, set(GAIT_BASE_DISTANCES)
        )
        for speed, cell in speed_cells.items():
            match = re.fullmatch(r"(\d+)\s*\+\s*modifier", cell)
            if match is None:
                self.fail(
                    f"movement.md's {speed} distance cell changed shape: {cell!r}"
                )
            self.assertEqual(
                int(match.group(1)), GAIT_BASE_DISTANCES[speed.lower()], speed
            )

    def test_movement_modifier_formula_is_documented(self):
        """The exact attribute-modifier formula must still be printed."""
        text = MOVEMENT_RULES.read_text(encoding="utf-8")
        self.assertIn(
            "**Movement Modifier:** CON modifier + STR modifier + DEX modifier",
            text,
        )

    def test_overloaded_walk_limit_matches_the_rules_prose(self):
        """'Walk only, 1 hex max' must match equipment.py's walk_limit."""
        from characters.equipment import ENCUMBRANCE_LEVELS

        rows = _table_rows(MOVEMENT_RULES)
        overloaded_row = next(
            cells for cells in rows if cells and cells[0] == "Overloaded"
        )
        restrictions_cell = overloaded_row[-1]
        match = re.search(r"(\d+) hex max", restrictions_cell)
        if match is None:
            self.fail("movement.md's Overloaded row lost its hex-max wording")
        self.assertEqual(
            int(match.group(1)), ENCUMBRANCE_LEVELS["overloaded"]["walk_limit"]
        )

    def test_worked_example_matches_the_code(self):
        """The doc's worked example must reproduce exactly via the model."""
        text = MOVEMENT_RULES.read_text(encoding="utf-8")
        match = re.search(
            r"CON 14 \(\+1\), STR 10 \(0\), DEX 12 \(0\) has a movement modifier "
            r"of \+1\. They walk (\d+), jog (\d+), run (\d+), sprint (\d+)\.",
            text,
        )
        if match is None:
            self.fail("movement.md lost its worked movement-modifier example")
        walk, jog, run, sprint = (int(group) for group in match.groups())

        character = Character(constitution=14, strength=10, dexterity=12)
        self.assertEqual(character.movement_modifier, 1)
        self.assertEqual(character.walk_distance, walk)
        self.assertEqual(character.jog_distance, jog)
        self.assertEqual(character.run_distance, run)
        self.assertEqual(character.sprint_distance, sprint)


_UNIT_TO_GP = {"gp": 1.0, "sp": 0.1, "cp": 0.01}


def _parse_gp_cost(cell: str) -> float:
    """Parse a rules-table cost cell ("2 sp", "1,000 gp", "2 cp/mile") to gp.

    A trailing "/unit" rate qualifier (e.g. "/mile") is dropped -- callers
    compare the per-unit price, not the rate suffix.
    """
    text = _normalize_number(cell).split("/", 1)[0].strip()
    amount_str, unit = text.split()
    return float(amount_str) * _UNIT_TO_GP[unit]


def _section_rows(markdown_path: Path, heading_prefix: str) -> list[list[str]]:
    """Rows of the table under the first '## <heading_prefix>...' section."""
    text = markdown_path.read_text(encoding="utf-8")
    sections = re.split(r"^## .*$", text, flags=re.MULTILINE)
    headings = re.findall(r"^## (.*)$", text, re.MULTILINE)
    for heading, body in zip(headings, sections[1:]):
        if heading.startswith(heading_prefix):
            return _rows_from_text(body)
    raise AssertionError(
        f"no '## {heading_prefix}...' section found in {markdown_path.name}"
    )


class MissingCatalogEntriesTest(SimpleTestCase):
    """Issue #147: gear.md tables with no matching catalog entries.

    Guards month rations, the Standard Pack bundle, Mounts & Vehicles, and
    Lodging & Services against silently drifting back out of the catalog.
    """

    def test_month_rations_prices_match_catalog(self):
        text = GEAR_RULES.read_text(encoding="utf-8")
        cram = re.search(r"One month cram: (.+?) gp", text)
        regular = re.search(r"One month regular: (.+?) gp", text)
        assert cram is not None, "gear.md lost the month cram ration entry"
        assert regular is not None, "gear.md lost the month regular ration entry"
        self.assertEqual(
            float(cram.group(1)), EQUIPMENT_CATALOG["rations_month_cram"]["cost"]
        )
        self.assertEqual(
            float(regular.group(1)), EQUIPMENT_CATALOG["rations_month_regular"]["cost"]
        )

    def test_standard_pack_bundle_lists_the_same_contents_as_gear_md(self):
        text = GEAR_RULES.read_text(encoding="utf-8")
        section = text.split("## Standard Pack", 1)[1].split("##", 1)[0]
        bullets = [
            line.strip("- ").strip()
            for line in section.splitlines()
            if line.strip().startswith("-")
        ]
        self.assertTrue(bullets, "gear.md lost the Standard Pack contents list")
        bundle = EQUIPMENT_CATALOG["standard_pack"]
        notes = bundle["notes"]
        basics_rows = _section_rows(GEAR_RULES, "Basics")[1:]  # drop header row

        total_cost = 0.0
        total_weight = 0.0
        for item_name in bullets:
            # "50' rope" in the bullet list vs. "50 ft" in the catalog notes --
            # match on the distinctive noun instead of the exact punctuation.
            keyword = item_name.split("'")[0].split()[0].lower()
            with self.subTest(item_name=item_name):
                self.assertIn(keyword, notes.lower())

            candidates = [row for row in basics_rows if keyword in row[0].lower()]
            if len(candidates) > 1:
                # Only "50' rope" is ambiguous (hemp vs. silk both have "50"
                # in their Basics name); the bundle notes pin the variant.
                variant = "hemp" if "hemp" in notes.lower() else "silk"
                candidates = [row for row in candidates if variant in row[0].lower()]
            with self.subTest(item_name=item_name, step="resolve component row"):
                self.assertEqual(len(candidates), 1, candidates)
            _name, cost_cell, weight_cell = candidates[0]
            total_cost += _parse_gp_cost(cost_cell)
            total_weight += float(_normalize_number(weight_cell))

        # Issue #167: #147 only checked the component *names* match; it
        # never re-summed their gear.md prices against the bundle's stored
        # cost/weight, so a Basics price change could drift silently.
        self.assertAlmostEqual(total_cost, bundle["cost"])
        self.assertAlmostEqual(total_weight, bundle["weight"])

    def test_mounts_and_vehicles_all_in_catalog_with_correct_cost(self):
        rows = _section_rows(GEAR_RULES, "Mounts & Vehicles")
        header, *data_rows = rows
        self.assertEqual(header, ["Item", "Cost"])

        by_name = {item["name"]: item for item in EQUIPMENT_CATALOG.values()}
        for name, cost_cell in data_rows:
            with self.subTest(name=name):
                self.assertIn(name, by_name, f"{name!r} missing from the catalog")
                item = by_name[name]
                self.assertEqual(item["subcategory"], "mounts_vehicles")
                self.assertAlmostEqual(_parse_gp_cost(cost_cell), item["cost"])

    def test_lodging_and_services_all_in_catalog_with_correct_cost(self):
        rows = _section_rows(GEAR_RULES, "Lodging & Services")
        header, *data_rows = rows
        self.assertEqual(header, ["Service", "Cost"])

        by_name = {item["name"]: item for item in EQUIPMENT_CATALOG.values()}
        for name, cost_cell in data_rows:
            with self.subTest(name=name):
                self.assertIn(name, by_name, f"{name!r} missing from the catalog")
                item = by_name[name]
                self.assertEqual(item["subcategory"], "lodging_services")
                self.assertAlmostEqual(_parse_gp_cost(cost_cell), item["cost"])
                if "/mile" in cost_cell:
                    self.assertIn("per mile", item["notes"].lower())


class ToolPriceRangeRuleTest(SimpleTestCase):
    """Issue #147: tool prices collapsed to a point value must note the
    printed range so the GM sees the spread gear.md allows."""

    def test_artisans_tools_notes_the_printed_range(self):
        rows = _table_rows(GEAR_RULES)
        row = next(cells for cells in rows if cells[0] == "Artisan's tools")
        self.assertEqual(row[1], "10-50 gp")
        self.assertIn("10-50 gp", EQUIPMENT_CATALOG["artisans_tools"]["notes"])

    def test_musical_instrument_notes_the_printed_range(self):
        rows = _table_rows(GEAR_RULES)
        row = next(cells for cells in rows if cells[0] == "Musical instrument")
        self.assertEqual(row[1], "5-50 gp")
        self.assertIn("5-50 gp", EQUIPMENT_CATALOG["musical_instrument"]["notes"])


class CrossbowRateOfFireRuleTest(SimpleTestCase):
    """Issue #147: weapons.md names no attribute for crossbow rate-of-fire
    thresholds (unlike bows, which explicitly say adjDEX); the catalog notes
    must not invent one."""

    def test_light_crossbow_notes_match_weapons_md_verbatim(self):
        rows = _table_rows(WEAPONS_RULES)
        row = next(cells for cells in rows if cells[0].startswith("Light Crossbow"))
        self.assertEqual(row[-1], "Every other turn; every if 14+")
        self.assertEqual(EQUIPMENT_CATALOG["light_crossbow"]["notes"], row[-1])

    def test_heavy_crossbow_notes_match_weapons_md_verbatim(self):
        rows = _table_rows(WEAPONS_RULES)
        row = next(cells for cells in rows if cells[0].startswith("Heavy Crossbow"))
        self.assertEqual(row[-1], "Every 3rd turn; every other 16+")
        self.assertEqual(EQUIPMENT_CATALOG["heavy_crossbow"]["notes"], row[-1])

    def test_crossbow_notes_do_not_name_an_attribute(self):
        for item_id in ("light_crossbow", "heavy_crossbow"):
            with self.subTest(item_id=item_id):
                notes = EQUIPMENT_CATALOG[item_id]["notes"]
                self.assertNotIn("STR", notes)
                self.assertNotIn("DEX", notes)


def _natural_rolls_section(markdown_path: Path) -> str:
    """The body of attack-rolls.md's "## Natural Rolls" section, whitespace-flat.

    Markdown wraps these bullets across lines, so the section is collapsed to a
    single spaced string before anything is matched against it.
    """
    text = markdown_path.read_text(encoding="utf-8")
    match = re.search(
        r"^## Natural Rolls\n(.*?)(?=^## )", text, re.MULTILINE | re.DOTALL
    )
    if match is None:
        raise AssertionError(f"{markdown_path.name} lost its Natural Rolls section")
    return " ".join(match.group(1).split())


def _parse_fumble_bands(section: str) -> list[tuple[int, int, str, str]]:
    """Parse the fumble bullet's 1d6 bands into ``(lo, hi, label, effect)``.

    Reads the authored form ``**1–3** off-balance (−2 to your next action) ·
    **4–5** drop weapon · **6** weapon takes stress (breaks on a second
    fumble).`` — bands separated by ``·``, each an emboldened range, a label,
    and an optional parenthesised effect.
    """
    match = re.search(r"Roll 1d6:(.+?)(?:\.\s|\.$)", section)
    if match is None:
        raise AssertionError("attack-rolls.md lost its 'Roll 1d6' fumble subtable")
    bands: list[tuple[int, int, str, str]] = []
    for chunk in match.group(1).split("·"):
        band = re.match(
            r"\s*\*\*(\d+)(?:-(\d+))?\*\*\s*([^(]+?)\s*(?:\(([^)]*)\))?\s*$",
            _normalize_number(chunk),
        )
        if band is None:
            raise AssertionError(
                f"unparseable fumble band in attack-rolls.md: {chunk!r}"
            )
        low = int(band.group(1))
        high = int(band.group(2)) if band.group(2) else low
        bands.append((low, high, band.group(3), band.group(4) or ""))
    return bands


# Rulebook words for "roll the damage dice N times" -> N.
DAMAGE_ROLL_WORDS = {"twice": 2, "double": 2, "triple": 3, "thrice": 3}


class CritFumbleRuleTest(SimpleTestCase):
    """attack-rolls.md's Natural Rolls section must match the code (issue #105).

    The crit multipliers and the 1d6 fumble subtable were printed here (and in
    the GM prompt) long before anything implemented them; ``combat`` now does,
    so the two need locking together the same way ``WeaponSkillRuleTest`` locks
    the skill ruling.
    """

    def test_fumble_bands_match_the_code_table(self):
        bands = _parse_fumble_bands(_natural_rolls_section(ATTACK_ROLLS))
        self.assertEqual(
            len(bands),
            len(combat.FUMBLE_TABLE),
            "attack-rolls.md prints a different number of fumble bands than "
            "combat.FUMBLE_TABLE holds",
        )
        expected_low = 1
        for (low, high, label, effect), entry in zip(bands, combat.FUMBLE_TABLE):
            threshold, _key, code_label, code_effect = entry
            with self.subTest(band=f"{low}-{high}"):
                self.assertEqual(low, expected_low, "fumble bands must be contiguous")
                self.assertEqual(high, threshold)
                self.assertEqual(label.lower(), code_label.lower())
                self.assertEqual(
                    _normalize_number(effect).lower(),
                    _normalize_number(code_effect).lower(),
                )
            expected_low = high + 1
        self.assertEqual(expected_low - 1, combat.FUMBLE_DIE_SIDES)

    def test_every_die_face_resolves_to_the_printed_band(self):
        """Rolling each face in code lands in the band the rules print for it."""
        bands = _parse_fumble_bands(_natural_rolls_section(ATTACK_ROLLS))
        for low, high, label, _effect in bands:
            for die in range(low, high + 1):
                with self.subTest(die=die):
                    self.assertEqual(
                        combat.fumble_result(die)["label"].lower(), label.lower()
                    )

    def test_critical_rolls_damage_the_printed_number_of_times(self):
        section = _natural_rolls_section(ATTACK_ROLLS)
        printed = re.search(r"damage dice \*\*(\w+)\*\*", section)
        if printed is None:
            raise AssertionError("attack-rolls.md stopped printing the crit multiplier")
        self.assertEqual(
            DAMAGE_ROLL_WORDS[printed.group(1).lower()], combat.CRIT_DAMAGE_ROLLS
        )

    def test_severe_crit_rolls_damage_the_printed_number_of_times(self):
        section = _natural_rolls_section(ATTACK_ROLLS)
        printed = re.search(r"\*\*(\w+) damage \+ bleeding\*\*", section)
        if printed is None:
            raise AssertionError(
                "attack-rolls.md stopped printing the severe-crit rule"
            )
        self.assertEqual(
            DAMAGE_ROLL_WORDS[printed.group(1).lower()],
            combat.SEVERE_CRIT_DAMAGE_ROLLS,
        )

    def test_severe_crit_needs_a_confirm_roll_and_bleeds(self):
        """The confirm roll and the bleeding flag the code sets are both printed."""
        section = _natural_rolls_section(ATTACK_ROLLS)
        self.assertIn("confirm", section.lower())
        self.assertIn("second d20", section)
        self.assertIn("bleeding", section.lower())
        confirmed = combat.resolve_attack(20, 10, 0, confirm_roll=20)
        self.assertTrue(confirmed["severe"])
        self.assertTrue(confirmed["bleeding"])

    def test_confirm_ruling_is_printed_and_implemented(self):
        """Each clause of the confirm ruling is on the page and in the code.

        The page has to carry the ruling in words a GM can act on, so each
        assertion below pairs the printed phrase with the behaviour it
        promises — drop the sentence from the markdown and this fails.
        """
        section = _natural_rolls_section(ATTACK_ROLLS).lower()

        # Same TN, same bonus: a confirm that clears the TN on the nose lands.
        self.assertIn("same tn with the same bonus", section)
        self.assertTrue(combat.resolve_attack(20, 17, 4, confirm_roll=13)["severe"])
        self.assertFalse(combat.resolve_attack(20, 17, 4, confirm_roll=12)["severe"])

        # A natural 1 fails to confirm, and is not a second fumble.
        self.assertIn("natural 1 on the confirm simply fails to confirm", section)
        self.assertIn("not a second fumble", section)
        natural_one = combat.resolve_attack(20, 2, 30, confirm_roll=1)
        self.assertFalse(natural_one["confirm"]["hit"])
        self.assertFalse(natural_one["severe"])
        self.assertFalse(natural_one["fumble"])
        self.assertIsNone(natural_one["fumble_detail"])

        # A natural 20 confirms whatever the TN.
        self.assertIn("natural 20 confirms automatically", section)
        self.assertTrue(combat.resolve_attack(20, 99, 0, confirm_roll=20)["severe"])


class ManaPoolRuleTest(SimpleTestCase):
    """Issue #297: mana-pool.md and magic.md said the Mana Pool is "3d6 at
    character creation (secret from player)" -- a roll and a hidden field
    nothing implements. The battle engine reads
    ``Character.free_spell_points`` (a visible field, default 0) as the
    pool, accumulated from the Magic skill track's Spell Point grants per
    the skills-glossary ruling. Both docs must describe that pool, not the
    unimplemented secret roll, and this guard pins the corrected wording so
    it cannot silently drift back."""

    def test_mana_pool_md_drops_the_secret_creation_roll(self):
        text = MANA_POOL_RULES.read_text(encoding="utf-8").lower()
        self.assertNotIn("secret from player", text)
        self.assertNotIn("3d6 at character creation", text)
        self.assertIn("spell point", text)
        self.assertIn("starts at 0", text)

    def test_magic_md_drops_the_secret_creation_roll(self):
        text = MAGIC_RULES.read_text(encoding="utf-8").lower()
        self.assertNotIn("**secret**", text)
        self.assertNotIn("determined at character creation", text)
        self.assertIn("spell point", text)

    def test_magic_mechanics_index_drops_the_secret_summary(self):
        text = MAGIC_MECHANICS_INDEX.read_text(encoding="utf-8").lower()
        self.assertNotIn("secret mana determination", text)

    def test_free_spell_points_field_matches_the_documented_pool(self):
        """The pool is visible and starts at 0 -- not a hidden rolled field."""
        field = Character._meta.get_field("free_spell_points")
        self.assertEqual(field.default, 0)


# How the public Counterspell page words the duel's two outcomes and the
# escalation limit. The canon page words them its own way, and supplies its
# markers under "counterspell_escalation" in tests/expectations.json.
PUBLIC_COUNTERSPELL_MARKERS = {
    "caster_wins": "If the caster wins",
    "defender_wins": "If the defender wins",
    "only_caster": "Only the caster can escalate",
}


def _outcome_clause(text: str, marker: str) -> str:
    """The rule a page gives for one outcome: ``marker`` to the sentence's end.

    The clause runs to the first full stop or line end, so a page's line or
    sentence for one outcome never borrows the next outcome's words.

    Args:
        text: The page's markdown.
        marker: The words that open the outcome, such as "If the caster wins".

    Returns:
        The clause, marker included.
    """
    match = re.search(re.escape(marker) + r"[^.\n]*", text)
    if match is None:
        raise AssertionError(f"no {marker!r} clause on the page")
    return match.group(0)


class CounterspellEscalationRuleTest(SimpleTestCase):
    """Issue #926: the public page said the caster escalates after winning,
    the canon page after losing. The canon is right: a won casting has gone
    off, so there is nothing left to escalate, and the canon's escalation ends
    when the caster succeeds or the defender lets the spell through. Both
    pages must now say escalation follows a countered spell and that only the
    caster may escalate."""

    def assert_escalation_follows_a_counter(
        self, page: Path, markers: dict[str, str]
    ) -> None:
        text = page.read_text(encoding="utf-8")
        defender_wins = _outcome_clause(text, markers["defender_wins"])
        caster_wins = _outcome_clause(text, markers["caster_wins"])
        self.assertIn("escalat", defender_wins.lower())
        self.assertNotIn("escalat", caster_wins.lower())
        self.assertIn(markers["only_caster"], text)

    def test_public_page_escalates_after_the_spell_is_countered(self) -> None:
        self.assert_escalation_follows_a_counter(
            COUNTERSPELL_PUBLIC_RULES, PUBLIC_COUNTERSPELL_MARKERS
        )

    @skip_unless_canon_expects("counterspell_escalation")
    def test_canon_page_agrees_with_the_public_page(self) -> None:
        self.assert_escalation_follows_a_counter(
            COUNTERSPELL_CANON_RULES, CANON_EXPECTATIONS["counterspell_escalation"]
        )
