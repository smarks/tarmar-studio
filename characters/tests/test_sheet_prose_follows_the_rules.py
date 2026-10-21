"""The sheet's sentences state the loaded canon's rules numbers (issue #68).

Every sentence of the sheet, its help and its refusals that states a number a
canon rules file holds is built from the loaded rules. Under the default canon
each is pinned to the words it had before; seated under invented rules (every
number unlike the pages'), each follows them, so a number left typed into a
sentence fails here rather than agreeing with the default canon by chance.
"""

import json
import re
from dataclasses import replace

from django.test import SimpleTestCase

from characters import services
from characters.help_text import help_text
from characters.rules import character_rules
from characters.skill_rules import SkillCostRules
from characters.skills import aging_escalation_note
from characters.tests.fixed_refusals import with_fixed_refusals
from characters.tests.rules_fixtures import (
    DEFAULT_CHARACTER_RULES,
    DEFAULT_SKILL_RULES,
    seated_character_rules,
    seated_equipment_rules,
    seated_skill_rules,
)
from characters.tests.test_equipment_rules import INVENTED_EQUIPMENT_RULES
from characters.tests.test_rules import INVENTED_CHARACTER_RULES
from characters.tests.test_skill_rules import INVENTED_SKILL_RULES
from characters.views.characters import (
    aging_preview_horizon_age,
    survival_naturals_note,
)
from common.test_helpers import BaseTestCase, create_character
from gm.character_schema import character_fields
from gm.ledger import DraftLedger, draft_defaults

#: Each built help entry under the default canon, as it read before #68 but
#: for the Fatigue formula's one space, the sum's one rendering (the
#: coordinator's ruling on the slice B review).
DEFAULT_HELP = {
    "strength": (
        "Physical power: carry capacity, melee damage, and strength checks. At "
        "creation the six core attributes are either rolled 3d6 in order or "
        "bought by dividing 65 points among them."
    ),
    "fatigue": (
        "Total stamina pool. Spent for spells, special maneuvers, and exhaustion. "
        "Formula: CON + WIS + INT + max(DEX, STR) + fatigue roll."
    ),
    "fatigue_roll": (
        "The 2d6 roll folded into Fatigue. Rolled once, by the server: roll it "
        "here, or in a Games Master session. Only a Game Master can have it "
        "rolled again."
    ),
    "wealth_level": (
        "Lifestyle tier you can sustain between adventures — rolled separately "
        "from Provenance. Sets starting coin: Subsistence 10, Moderate 100, "
        "Merchant 100 + d100, Rich by GM."
    ),
    "free_skill_points": (
        "Unspent points from earned experience. Allocate via the + button on "
        "any skill. There is no maximum level: each level costs one point more "
        "than the last (1, 3, 6, 10, 15, 21, ... points in total), and Arcane "
        "costs double all the way up."
    ),
    "skill_check": (
        "Roll 2d6 + the governing attribute's modifier + your skill level against "
        "the difficulty the GM sets. A natural 12 always succeeds, a natural 2 "
        "always fails; untrained, you add the attribute modifier alone. A check "
        "on an attribute with no skill is 3d6 ≤ the attribute instead."
    ),
    "encumbrance": (
        "Carry-weight tier based on STR. Unencumbered (≤STR), light (≤1.5×STR), "
        "medium (≤2×STR), heavy (≤2.5×STR), overloaded (above). Each tier "
        "applies movement penalties and restricts faster gaits."
    ),
    "movement": (
        "Hexes moved per gait: base distance (Sprint 18, Run 12, Jog 7, Walk 4) "
        "plus your movement modifier (CON + STR + DEX modifiers), minus any "
        "encumbrance movement penalty. A struck-through gait is forbidden by "
        "encumbrance or worn armor; Overloaded caps Walk at 1 hex regardless "
        "of the formula."
    ),
}

#: The location entry's two built clauses under the default canon.
DEFAULT_LOCATION_CLAUSES = (
    "Changing the origin moves the grant: the one point the old origin gave "
    "is taken back, points you bought stay, and the new origin's point is "
    "added.",
    "added to the 3d6 roll, equivalently taken off INT",
)

#: Each refusal and reason built from the rules, under the default canon.
#: The default canon's dice in each field's reason, under the fixed words
#: (``characters/tests/fixed_refusals.py``): the sentence is configuration
#: (``config/characters-refusals.toml``, issue #85), the numbers the canon's.
DEFAULT_REASONS = {
    "strength": "services.field_attribute 3d6 65",
    "fatigue_roll": "services.field_fatigue_roll 2d6",
    "literacy_roll": "services.field_literacy_roll 3d6",
}


def starting_coin_description() -> str:
    """The extraction schema's description of the starting coin."""
    return next(
        field.description
        for field in character_fields()
        if field.name == "starting_coin"
    )


class DefaultCanonTextTest(SimpleTestCase):
    """The default canon's rules give the words the sheet had, whichever
    canon is loaded."""

    def setUp(self) -> None:
        self.enterContext(seated_character_rules())
        self.enterContext(seated_skill_rules())
        self.enterContext(seated_equipment_rules())

    def test_each_built_help_entry_is_the_pinned_text(self) -> None:
        for key, expected in DEFAULT_HELP.items():
            with self.subTest(key=key):
                self.assertEqual(help_text()[key], expected)
        for clause in DEFAULT_LOCATION_CLAUSES:
            with self.subTest(clause=clause):
                self.assertIn(clause, help_text()["location"])

    @with_fixed_refusals
    def test_each_reason_is_the_pinned_text(self) -> None:
        for field_name, expected in DEFAULT_REASONS.items():
            with self.subTest(field=field_name):
                self.assertEqual(services.field_refusal(field_name), expected)
        self.assertEqual(
            services.bought_not_rolled_reason(),
            "Typed scores committed this character to the 65-point buy; type "
            "the rest (the six must total 65), or ask a Game Master",
        )
        self.assertEqual(
            services.player_chosen_face_reasons()["weight_roll"],
            "A player chooses one base-weight face, 1 to 6 (8 to 13 stones).",
        )

    def test_the_aging_note_schema_and_ledger_are_the_pinned_text(self) -> None:
        self.assertEqual(
            aging_escalation_note(aging_preview_horizon_age()),
            "Continues to escalate past age 96 (roughly -1 to STR/DEX/CON every "
            "four years, plus -1 to INT/WIS every eight).",
        )
        self.assertEqual(
            starting_coin_description(),
            "Coin the character starts with (100 for Moderate if unrolled)",
        )
        self.assertEqual(
            character_rules().fatigue_roll.formula("roll"),
            "CON + WIS + INT + max(DEX, STR) + roll",
        )
        self.assertEqual(
            survival_naturals_note(),
            "Triple ones always kill; triple sixes always survive.",
        )
        self.assertEqual(
            draft_defaults()["starting_coin"], character_rules().default_starting_coin
        )

    def test_a_survival_total_off_the_dices_ends_is_said_as_its_number(self) -> None:
        off_end = replace(
            DEFAULT_SKILL_RULES,
            survival_roll=replace(
                DEFAULT_SKILL_RULES.survival_roll, automatic_death_total=4
            ),
        )
        with seated_skill_rules(off_end):
            self.assertEqual(
                survival_naturals_note(),
                "A natural 4 always kills; triple sixes always survive.",
            )

    def test_one_best_of_attribute_is_added_plainly(self) -> None:
        one_best = replace(
            DEFAULT_CHARACTER_RULES,
            fatigue_roll=replace(
                DEFAULT_CHARACTER_RULES.fatigue_roll,
                attributes=("CON",),
                best_of=("DEX",),
            ),
        )
        self.assertEqual(one_best.fatigue_roll.formula("roll"), "CON + DEX + roll")


class InventedRulesTest(SimpleTestCase):
    """Each sentence follows invented rules seated in every rules file."""

    def setUp(self) -> None:
        self.enterContext(seated_character_rules(INVENTED_CHARACTER_RULES))
        self.enterContext(seated_skill_rules(INVENTED_SKILL_RULES))
        self.enterContext(seated_equipment_rules(INVENTED_EQUIPMENT_RULES))

    def test_the_help_follows_the_rules(self) -> None:
        expected = {
            "strength": "either rolled 4d4 in order or bought by dividing 50 "
            "points among them.",
            "fatigue": "Formula: CON + fatigue roll.",
            "fatigue_roll": "The 1d8+1 roll folded into Fatigue.",
            "wealth_level": "Sets starting coin: Poor 5, Comfortable 50 + 2d6, "
            "Wealthy by GM.",
            "location": "the two points the old origin gave are taken back, "
            "points you bought stay, and the new origin's two are added. ",
            "skill_check": "Roll 3d4 + the governing attribute's modifier + your "
            "skill level against the difficulty the GM sets. A natural 11 always "
            "succeeds, a natural 3 always fails; untrained, you add the attribute "
            "modifier alone. A check on an attribute with no skill is 2d8 ≤ the "
            "attribute instead.",
            "encumbrance": "Carry-weight tier based on STR. Free (≤STR), laden "
            "(≤3.5×STR), crushed (above). Each tier",
            "movement": "base distance (Dash 9, Trot 5, Amble 0) plus your "
            "movement modifier",
        }
        for key, sentence in expected.items():
            with self.subTest(key=key):
                self.assertIn(sentence, help_text()[key])
        self.assertIn("added to the 2d10 roll", help_text()["location"])
        self.assertIn(
            "worn armor; Crushed caps Amble at 2 hex regardless of the formula.",
            help_text()["movement"],
        )
        # The invented skill rules charge Sorcery, not Arcane, a multiple.
        self.assertTrue(
            help_text()["free_skill_points"].endswith("... points in total).")
        )

    def test_an_arcane_multiple_is_named(self) -> None:
        # The default rules, the ones that govern Arcane, at other multiples.
        tripled = replace(
            DEFAULT_SKILL_RULES, skill_cost=SkillCostRules(multipliers={"arcane": 3})
        )
        with seated_skill_rules(tripled):
            self.assertIn(
                "and Arcane costs triple all the way up.",
                help_text()["free_skill_points"],
            )
        times_five = replace(
            DEFAULT_SKILL_RULES, skill_cost=SkillCostRules(multipliers={"arcane": 5})
        )
        with seated_skill_rules(times_five):
            self.assertIn(
                "and Arcane costs 5× all the way up.", help_text()["free_skill_points"]
            )

    def test_one_grant_point_reads_in_the_singular(self) -> None:
        one_point = replace(
            INVENTED_CHARACTER_RULES,
            location=replace(INVENTED_CHARACTER_RULES.location, skill_points=1),
        )
        with seated_character_rules(one_point):
            self.assertIn(DEFAULT_LOCATION_CLAUSES[0], help_text()["location"])

    @with_fixed_refusals
    def test_the_refusals_follow_the_rules(self) -> None:
        self.assertEqual(
            services.field_refusal("charisma"),
            "services.field_attribute 4d4 50",
        )
        self.assertEqual(
            services.field_refusal("fatigue_roll"),
            "services.field_fatigue_roll 1d8+1",
        )
        self.assertEqual(
            services.field_refusal("literacy_roll"),
            "services.field_literacy_roll 2d10",
        )
        self.assertEqual(
            services.bought_not_rolled_reason(),
            "Typed scores committed this character to the 50-point buy; type "
            "the rest (the six must total 50), or ask a Game Master",
        )
        self.assertIn(
            "(7 to 12 stones)", services.player_chosen_face_reasons()["weight_roll"]
        )

    def test_the_aging_note_follows_the_rules(self) -> None:
        self.assertEqual(
            aging_escalation_note(aging_preview_horizon_age()),
            "Continues to escalate past age 114 (roughly -1 to STR/DEX/CON every "
            "five years, plus -1 to INT/WIS every 15).",
        )

    def test_the_schema_names_the_canons_default_wealth_row(self) -> None:
        """The coin the schema names is the default wealth row's, by its own
        label (issues #68, #66)."""
        self.assertEqual(
            starting_coin_description(),
            "Coin the character starts with (5 for Poor if unrolled)",
        )
        with_rolled_default = replace(
            INVENTED_CHARACTER_RULES,
            defaults=replace(INVENTED_CHARACTER_RULES.defaults, wealth="Comfortable"),
        )
        with seated_character_rules(with_rolled_default):
            self.assertEqual(
                starting_coin_description(),
                "Coin the character starts with (50 + 2d6 for Comfortable if unrolled)",
            )

    def test_the_ledger_names_the_fatigue_dice(self) -> None:
        ledger = DraftLedger.from_draft({})
        self.assertIn("the 1d8+1 Fatigue roll", ledger.pools_pending)

    def test_the_survival_naturals_follow_the_rules(self) -> None:
        """2d10 has no name for its ends, so each total is said as a number."""
        self.assertEqual(
            survival_naturals_note(),
            "A natural 2 always kills; a natural 20 always survives.",
        )

    def test_a_drafts_coin_is_the_default_levels_fixed_coin(self) -> None:
        """The default wealth row's fixed coin; none when its coin is rolled or
        the Game Master's (issues #68, #66)."""
        self.assertEqual(draft_defaults()["starting_coin"], 5)
        for level, expected in (("Comfortable", None), ("Wealthy", None)):
            rules = replace(
                INVENTED_CHARACTER_RULES,
                defaults=replace(INVENTED_CHARACTER_RULES.defaults, wealth=level),
            )
            with self.subTest(level=level), seated_character_rules(rules):
                self.assertEqual(draft_defaults()["wealth_level"], level)
                self.assertEqual(draft_defaults()["starting_coin"], expected)


class SheetPagesTest(BaseTestCase):
    """The creation page and the sheet print the seated rules' numbers."""

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(seated_character_rules(INVENTED_CHARACTER_RULES))
        self.player = self.create_user("prose-player")
        self.login_as(self.player)

    def test_the_sheets_origin_select_lists_the_canons_rows(self) -> None:
        """The Origin select offers the loaded canon's rows, the character's
        default origin selected (issue #66)."""
        character = create_character(self.player)
        self.assertEqual(character.location, "Port")
        body = self.client.get(f"/tarmar/characters/{character.pk}/").content.decode()
        self.assertIn(
            '<option value="Port" selected>Port</option>'
            '<option value="Ask the GM">Ask the GM</option>',
            body,
        )
        self.assertNotIn('<option value="Village"', body)

    def test_the_creation_page_rolls_the_canons_dice(self) -> None:
        body = self.client.get("/tarmar/characters/create/").content.decode()
        self.assertIn("<h2>Roll 4d4</h2>", body)
        self.assertIn("<p>Roll 4d4 for each of the six core attributes", body)
        self.assertNotIn("3d6", body)

    def test_the_sheet_states_the_canons_numbers(self) -> None:
        character = create_character(
            self.player,
            strength=None,
            dexterity=None,
            intelligence=None,
            wisdom=None,
            constitution=None,
            charisma=None,
            fatigue_roll=None,
            literacy_roll=None,
        )
        body = self.client.get(f"/tarmar/characters/{character.pk}/").content.decode()
        for sentence in (
            "Type a missing score from 4 to 15: typed scores are a 50-point buy, "
            "so the six must total 50. Or roll the missing ones, 4d4 each.",
            ">Roll attributes (4d4 each)</button>",
            ">Roll Fatigue (1d8+1)</button>",
            '<td class="formula">1d8+1, rolled once</td>',
            '<td class="formula">CON + roll</td>',
            ">Roll literacy (2d10)</button>",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, body)
        match = re.search(
            r'<script id="character-edit-config" type="application/json">'
            r"(.*?)</script>",
            body,
            re.DOTALL,
        )
        assert match is not None, "the page has no #character-edit-config block"
        config = json.loads(match.group(1))
        self.assertEqual(
            config["agingEscalationNote"],
            aging_escalation_note(aging_preview_horizon_age()),
        )
        self.assertIn("every five years", config["agingEscalationNote"])

    def test_a_character_past_the_horizon_reads_the_note(self) -> None:
        character = create_character(self.player, age=aging_preview_horizon_age())
        body = self.client.get(f"/tarmar/characters/{character.pk}/").content.decode()
        self.assertIn(
            '<dd id="workbench-aging-preview">'
            f"{aging_escalation_note(aging_preview_horizon_age())}</dd>",
            body,
        )
