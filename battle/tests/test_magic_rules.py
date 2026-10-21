"""The canon's ``magic-rules.json`` loader (issue #960).

Every number here is invented; the Tarmar values live only in the private
canon, and the probe test at the end compares the loaded canon's file to the
summary its ``tests/expectations.json`` carries under ``magic_rules``.
"""

import copy
import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

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
    MagicRules,
    PushRules,
    RollUnderRule,
    RunawayRules,
    SpellEffectKind,
)

from battle.magic_rules import (
    CASTING_DICE,
    MagicRulesError,
    load_magic_rules,
    magic_rules,
)
from common.test_helpers import CANON_EXPECTATIONS, skip_unless_canon_expects
from reference.paths import (
    CANON_MAGIC_RULES_FILE_NAME,
    DM_RULES_DIR,
    MAGIC_RULES_FILE,
)
from reference.rules_file import (
    BLOCK_PLACEHOLDER,
    UNKNOWN_BLOCK_EXPLANATION,
    RulesFileError,
)

from .magic_fixtures import DEFAULT_MAGIC_RULES

# A complete file with invented numbers: every field present, both kinds of
# Channel derivation, a per-spell push bonus, Borrowing, Runaway, Counterspell,
# casting failure totals and a ruling.
INVENTED_RULES: dict[str, Any] = {
    "casting_success_tiers": [
        {
            "key": "bright",
            "label": "Bright cast",
            "natural_totals": [3, 4],
            "effect_bonus": 3,
            "mana_refund": 2,
        },
        {
            "key": "steady",
            "label": "Steady cast",
            "natural_totals": [6],
            "effect_bonus": 0,
            "mana_refund": 0,
        },
    ],
    "push": {
        "control_dice": "2d6",
        "control_attribute": "WIS",
        "penalty_per_mana": 2,
        "base_cost_counts_as_invested": True,
        "failure_bands": [
            {
                "key": "wobble",
                "label": "Wobble",
                "is_runaway": False,
                "lowest_margin": 1,
                "highest_margin": 2,
                "spell_takes_effect": True,
                "push_bonus_applies": True,
            },
            {
                "key": "fizzle",
                "label": "Fizzle",
                "is_runaway": False,
                "lowest_margin": 3,
                "highest_margin": 5,
                "spell_takes_effect": False,
                "push_bonus_applies": False,
            },
            {
                "key": "torrent",
                "label": "Torrent",
                "is_runaway": True,
                "lowest_margin": 6,
                "highest_margin": None,
                "spell_takes_effect": False,
                "push_bonus_applies": False,
            },
        ],
        "automatic_runaway_totals": [2, 12],
        "max_push_mana": 5,
        "effect_bonus_per_mana_by_kind": {"damage": 2},
        "spell_effect_bonus_per_mana": {"heal": 3},
    },
    "channel": {
        "derivations": {
            "hedge": {"source_skill": None, "multiplier": 2, "offset": 1},
            "deep": {"source_skill": "lore", "multiplier": 1, "offset": 0},
        },
        "kind_by_spell": {"fire_ball": "deep"},
        "default_kind": "hedge",
    },
    "borrowing": {
        "debt_dice": "2d8",
        "debt_attributes": ["WIS", "INT"],
        "penalty_per_debt": 2,
        "failure": {"key": "backlash", "label": "Backlash", "is_runaway": False},
        "automatic_failure_totals": [16],
        "max_debt": 6,
        "refunds_pay_debt_first": False,
    },
    "runaway": {
        "drain_multiplier_of_cost": 2,
        "drain_flat": 1,
        "overflow_pool": "fatigue",
        "death_multiplier_of_max": 2,
        "self_anchor": {"dice": "2d8", "attribute": "WIS"},
        "helper_anchor": {"dice": "2d10", "attribute": "INT"},
        "helper_cost_multiplier_of_cost": 3,
        "helper_failure_catches_helper": True,
        "effect_bonus_per_turn": 2,
    },
    "counterspell": {
        "opposed_dice": "2d8",
        "opposed_attribute": "WIS",
        "mana_per_bonus": 4,
        "ties_go_to_caster": False,
        "defender_min_mana": 2,
        "defender_max_mana": 7,
    },
    "casting_failure": {
        "fumble_floor": 14,
        "mana_lost_totals": [14, 15],
        "runaway_total": 15,
    },
    "rulings": [
        {"key": "made_up", "reading": "An invented reading.", "for_spencer": True}
    ],
}

INVENTED_MAGIC_RULES = MagicRules(
    casting_success_tiers=(
        CastingSuccessTier(
            key="bright",
            label="Bright cast",
            natural_totals=frozenset({3, 4}),
            effect_bonus=3,
            mana_refund=2,
        ),
        CastingSuccessTier(
            key="steady",
            label="Steady cast",
            natural_totals=frozenset({6}),
            effect_bonus=0,
            mana_refund=0,
        ),
    ),
    push=PushRules(
        control_dice="2d6",
        control_attribute="WIS",
        penalty_per_mana=2,
        base_cost_counts_as_invested=True,
        failure_bands=(
            ControlBand(
                key="wobble",
                label="Wobble",
                is_runaway=False,
                lowest_margin=1,
                highest_margin=2,
                spell_takes_effect=True,
                push_bonus_applies=True,
            ),
            ControlBand(
                key="fizzle",
                label="Fizzle",
                is_runaway=False,
                lowest_margin=3,
                highest_margin=5,
                spell_takes_effect=False,
                push_bonus_applies=False,
            ),
            ControlBand(
                key="torrent",
                label="Torrent",
                is_runaway=True,
                lowest_margin=6,
                highest_margin=None,
                spell_takes_effect=False,
                push_bonus_applies=False,
            ),
        ),
        automatic_runaway_totals=frozenset({2, 12}),
        effect_bonus_per_mana_by_kind={SpellEffectKind.DAMAGE: 2},
        max_push_mana=5,
        spell_effect_bonus_per_mana={"heal": 3},
    ),
    channel=ChannelRules(
        derivations={
            "hedge": ChannelDerivation(source_skill=None, multiplier=2, offset=1),
            "deep": ChannelDerivation(source_skill="lore", multiplier=1, offset=0),
        },
        kind_by_spell={"fire_ball": "deep"},
        default_kind="hedge",
    ),
    borrowing=BorrowingRules(
        debt_dice="2d8",
        debt_attributes=("WIS", "INT"),
        penalty_per_debt=2,
        failure=BorrowingFailure(key="backlash", label="Backlash", is_runaway=False),
        automatic_failure_totals=frozenset({16}),
        max_debt=6,
        refunds_pay_debt_first=False,
    ),
    runaway=RunawayRules(
        drain_multiplier_of_cost=2,
        drain_flat=1,
        overflow_pool="fatigue",
        death_multiplier_of_max=2,
        self_anchor=RollUnderRule(dice="2d8", attribute="WIS"),
        helper_anchor=RollUnderRule(dice="2d10", attribute="INT"),
        helper_cost_multiplier_of_cost=3,
        helper_failure_catches_helper=True,
        effect_bonus_per_turn=2,
    ),
    counterspell=CounterspellRules(
        opposed_dice="2d8",
        opposed_attribute="WIS",
        mana_per_bonus=4,
        ties_go_to_caster=False,
        defender_min_mana=2,
        defender_max_mana=7,
    ),
    casting_failure=CastingFailureRules(
        fumble_floor=14, mana_lost_totals=frozenset({14, 15}), runaway_total=15
    ),
)


def invented_rules() -> dict[str, Any]:
    """A fresh copy of :data:`INVENTED_RULES` a test may change."""
    return copy.deepcopy(INVENTED_RULES)


class MagicRulesFileTestCase(SimpleTestCase):
    """Writes rules to a temporary ``magic-rules.json``."""

    def setUp(self) -> None:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.rules_file = directory / CANON_MAGIC_RULES_FILE_NAME

    def write_rules(self, rules: Any) -> Path:
        """Write ``rules`` as the file's JSON and return its path."""
        self.rules_file.write_text(json.dumps(rules), encoding="utf-8")
        return self.rules_file

    def assert_refused(self, rules: Any, field_path: str) -> MagicRulesError:
        """Loading ``rules`` raises an error naming the file and the field."""
        with self.assertRaises(MagicRulesError) as caught:
            load_magic_rules(self.write_rules(rules))
        message = str(caught.exception)
        self.assertIn(str(self.rules_file), message)
        self.assertIn(field_path, message)
        return caught.exception


class LoadMagicRulesTest(MagicRulesFileTestCase):
    def test_a_full_file_round_trips_to_the_engine_rules(self) -> None:
        self.assertEqual(
            load_magic_rules(self.write_rules(INVENTED_RULES)), INVENTED_MAGIC_RULES
        )

    def test_every_null_block_and_no_tiers_load(self) -> None:
        rules = {
            "casting_success_tiers": [],
            "push": None,
            "channel": None,
            "borrowing": None,
            "runaway": None,
            "counterspell": None,
            "casting_failure": None,
        }
        self.assertEqual(load_magic_rules(self.write_rules(rules)), MagicRules())

    def test_null_casting_failure_loads_as_none(self) -> None:
        rules = invented_rules()
        rules["casting_failure"] = None
        self.assertIsNone(load_magic_rules(self.write_rules(rules)).casting_failure)

    def test_casting_failure_with_no_runaway_total_or_lost_totals_loads(
        self,
    ) -> None:
        rules = invented_rules()
        rules["casting_failure"].update(mana_lost_totals=[], runaway_total=None)
        self.assertEqual(
            load_magic_rules(self.write_rules(rules)).casting_failure,
            CastingFailureRules(fumble_floor=14),
        )

    def test_borrowing_with_no_cap_loads_beside_channel(self) -> None:
        rules = invented_rules()
        rules["borrowing"]["max_debt"] = None
        loaded = load_magic_rules(self.write_rules(rules))
        assert loaded.borrowing is not None
        self.assertIsNone(loaded.borrowing.max_debt)

    def test_runaway_with_no_death_line_loads(self) -> None:
        rules = invented_rules()
        rules["runaway"]["death_multiplier_of_max"] = None
        loaded = load_magic_rules(self.write_rules(rules))
        assert loaded.runaway is not None
        self.assertIsNone(loaded.runaway.death_multiplier_of_max)

    def test_counterspell_with_no_commitment_cap_loads(self) -> None:
        rules = invented_rules()
        rules["counterspell"]["defender_max_mana"] = None
        loaded = load_magic_rules(self.write_rules(rules))
        assert loaded.counterspell is not None
        self.assertIsNone(loaded.counterspell.defender_max_mana)

    def test_rulings_are_optional(self) -> None:
        rules = invented_rules()
        del rules["rulings"]
        self.assertEqual(
            load_magic_rules(self.write_rules(rules)), INVENTED_MAGIC_RULES
        )

    def test_the_error_is_a_rules_file_error(self) -> None:
        # Not Django's ImproperlyConfigured: the shared reader stays Django-free
        # so the offline CLI's gm.engine chain can reach the canon rules files
        # (gm/tests.py's test_engine_imports_without_django).
        self.assertTrue(issubclass(MagicRulesError, RulesFileError))

    def test_a_missing_file_is_refused(self) -> None:
        with self.assertRaises(MagicRulesError) as caught:
            load_magic_rules(self.rules_file)
        self.assertIn(str(self.rules_file), str(caught.exception))

    def test_invalid_json_is_refused(self) -> None:
        self.rules_file.write_text("{not json", encoding="utf-8")
        with self.assertRaises(MagicRulesError) as caught:
            load_magic_rules(self.rules_file)
        self.assertIn(str(self.rules_file), str(caught.exception))

    def test_an_unreadable_file_is_refused(self) -> None:
        self.write_rules(INVENTED_RULES)
        with (
            patch.object(Path, "read_text", side_effect=PermissionError("denied")),
            self.assertRaises(MagicRulesError) as caught,
        ):
            load_magic_rules(self.rules_file)
        self.assertIn(str(self.rules_file), str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, PermissionError)

    def test_a_key_repeated_in_one_object_is_refused(self) -> None:
        # json.loads alone would keep the second "push" and drop the first.
        text = '{"push": null, ' + json.dumps(INVENTED_RULES)[1:]
        self.rules_file.write_text(text, encoding="utf-8")
        with self.assertRaises(MagicRulesError) as caught:
            load_magic_rules(self.rules_file)
        self.assertIn(str(self.rules_file), str(caught.exception))
        self.assertIn("'push'", str(caught.exception))

    def test_a_file_that_is_not_an_object_is_refused(self) -> None:
        self.assert_refused([], "the file")


# Each malformed shape: what to do to a valid file, and the field the error
# must name.
MALFORMED_SHAPES: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
    ("pushes", lambda rules: rules.update(pushes=None)),
    ("push", lambda rules: rules.pop("push")),
    ("casting_success_tiers", lambda rules: rules.update(casting_success_tiers={})),
    (
        "casting_success_tiers[0].natural_totals[1]",
        lambda rules: rules["casting_success_tiers"][0].update(
            natural_totals=[3, True]
        ),
    ),
    (
        "casting_success_tiers[0].natural_totals",
        lambda rules: rules["casting_success_tiers"][0].update(natural_totals=[3, 3]),
    ),
    (
        "casting_success_tiers[0].effect_bonus",
        lambda rules: rules["casting_success_tiers"][0].update(effect_bonus="2"),
    ),
    (
        "casting_success_tiers[1].label",
        lambda rules: rules["casting_success_tiers"][1].update(label=""),
    ),
    (
        "casting_success_tiers[0].bonus",
        lambda rules: rules["casting_success_tiers"][0].update(bonus=1),
    ),
    (
        "casting_success_tiers repeats the key ['bright']",
        lambda rules: rules["casting_success_tiers"][1].update(key="bright"),
    ),
    (
        "push.failure_bands repeats the key ['wobble']",
        lambda rules: rules["push"]["failure_bands"][2].update(key="wobble"),
    ),
    (
        "casting_success_tiers[0].mana_refund",
        lambda rules: rules["casting_success_tiers"][0].pop("mana_refund"),
    ),
    (
        "push.failure_bands[1].highest_margin",
        lambda rules: rules["push"]["failure_bands"][1].update(highest_margin="5"),
    ),
    (
        "push.failure_bands[0].is_runaway",
        lambda rules: rules["push"]["failure_bands"][0].update(is_runaway=0),
    ),
    ("push.failure_bands", lambda rules: rules["push"].update(failure_bands={})),
    ("push.max_push_mana", lambda rules: rules["push"].update(max_push_mana=True)),
    (
        "push.penalty_per_mana",
        lambda rules: rules["push"].update(penalty_per_mana=None),
    ),
    (
        "push.automatic_runaway_totals",
        lambda rules: rules["push"].update(automatic_runaway_totals=[12, 12]),
    ),
    (
        "push.effect_bonus_per_mana_by_kind.fire",
        lambda rules: rules["push"].update(effect_bonus_per_mana_by_kind={"fire": 1}),
    ),
    (
        "push.spell_effect_bonus_per_mana.no_such_spell",
        lambda rules: rules["push"].update(
            spell_effect_bonus_per_mana={"no_such_spell": 1}
        ),
    ),
    (
        "push.spell_effect_bonus_per_mana.shield",
        lambda rules: rules["push"].update(spell_effect_bonus_per_mana={"shield": 1}),
    ),
    ("push.control_dice", lambda rules: rules["push"].update(control_dice=3)),
    ("channel", lambda rules: rules.update(channel=[])),
    (
        "channel.derivations.hedge.multiplier",
        lambda rules: rules["channel"]["derivations"]["hedge"].update(multiplier=1.5),
    ),
    (
        "channel.derivations.deep.source_skill",
        lambda rules: rules["channel"]["derivations"]["deep"].update(source_skill=""),
    ),
    (
        "channel.kind_by_spell.fire_ball",
        lambda rules: rules["channel"].update(kind_by_spell={"fire_ball": 3}),
    ),
    (
        "channel.kind_by_spell.fireball",
        lambda rules: rules["channel"].update(kind_by_spell={"fireball": "deep"}),
    ),
    ("channel.default_kind", lambda rules: rules["channel"].update(default_kind=7)),
    ("channel.derivations", lambda rules: rules["channel"].update(derivations=[])),
    (
        "channel.kind_by_spell has an empty key",
        lambda rules: rules["channel"].update(kind_by_spell={"": "deep"}),
    ),
    ("borrowing", lambda rules: rules.pop("borrowing")),
    ("borrowing", lambda rules: rules.update(borrowing=[])),
    ("borrowing.debt_dice", lambda rules: rules["borrowing"].update(debt_dice=3)),
    ("borrowing.debt_dice", lambda rules: rules["borrowing"].pop("debt_dice")),
    (
        "borrowing.debt_attributes",
        lambda rules: rules["borrowing"].update(debt_attributes="WIS"),
    ),
    (
        "borrowing.debt_attributes[1]",
        lambda rules: rules["borrowing"].update(debt_attributes=["WIS", ""]),
    ),
    (
        "borrowing.debt_attributes repeats the entry ['WIS']",
        lambda rules: rules["borrowing"].update(debt_attributes=["WIS", "WIS"]),
    ),
    (
        "borrowing.penalty_per_debt",
        lambda rules: rules["borrowing"].update(penalty_per_debt=True),
    ),
    ("borrowing.failure", lambda rules: rules["borrowing"].update(failure=None)),
    (
        "borrowing.failure.label",
        lambda rules: rules["borrowing"]["failure"].update(label=""),
    ),
    (
        "borrowing.failure.is_runaway",
        lambda rules: rules["borrowing"]["failure"].update(is_runaway=1),
    ),
    (
        "borrowing.failure.spell_takes_effect",
        lambda rules: rules["borrowing"]["failure"].update(spell_takes_effect=True),
    ),
    (
        "borrowing.automatic_failure_totals",
        lambda rules: rules["borrowing"].update(automatic_failure_totals=[16, 16]),
    ),
    (
        "borrowing.automatic_failure_totals[0]",
        lambda rules: rules["borrowing"].update(automatic_failure_totals=[False]),
    ),
    ("borrowing.max_debt", lambda rules: rules["borrowing"].update(max_debt="6")),
    ("borrowing.max_debt", lambda rules: rules["borrowing"].pop("max_debt")),
    (
        "borrowing.refunds_pay_debt_first",
        lambda rules: rules["borrowing"].update(refunds_pay_debt_first=0),
    ),
    ("borrowing.cap", lambda rules: rules["borrowing"].update(cap=6)),
    ("runaway", lambda rules: rules.pop("runaway")),
    ("runaway", lambda rules: rules.update(runaway=[])),
    (
        "runaway.drain_multiplier_of_cost",
        lambda rules: rules["runaway"].update(drain_multiplier_of_cost=True),
    ),
    ("runaway.drain_flat", lambda rules: rules["runaway"].pop("drain_flat")),
    ("runaway.overflow_pool", lambda rules: rules["runaway"].update(overflow_pool="")),
    (
        "runaway.death_multiplier_of_max",
        lambda rules: rules["runaway"].update(death_multiplier_of_max="2"),
    ),
    ("runaway.self_anchor", lambda rules: rules["runaway"].update(self_anchor="2d8")),
    (
        "runaway.self_anchor.attribute",
        lambda rules: rules["runaway"]["self_anchor"].pop("attribute"),
    ),
    (
        "runaway.helper_anchor.dice",
        lambda rules: rules["runaway"]["helper_anchor"].update(dice=8),
    ),
    (
        "runaway.helper_anchor.penalty",
        lambda rules: rules["runaway"]["helper_anchor"].update(penalty=1),
    ),
    (
        "runaway.helper_cost_multiplier_of_cost",
        lambda rules: rules["runaway"].update(helper_cost_multiplier_of_cost=1.5),
    ),
    (
        "runaway.helper_failure_catches_helper",
        lambda rules: rules["runaway"].update(helper_failure_catches_helper=1),
    ),
    (
        "runaway.effect_bonus_per_turn",
        lambda rules: rules["runaway"].update(effect_bonus_per_turn=None),
    ),
    ("runaway.growth", lambda rules: rules["runaway"].update(growth=1)),
    ("counterspell", lambda rules: rules.pop("counterspell")),
    ("counterspell", lambda rules: rules.update(counterspell="2d8")),
    (
        "counterspell.opposed_dice",
        lambda rules: rules["counterspell"].update(opposed_dice=""),
    ),
    (
        "counterspell.opposed_attribute",
        lambda rules: rules["counterspell"].pop("opposed_attribute"),
    ),
    (
        "counterspell.mana_per_bonus",
        lambda rules: rules["counterspell"].update(mana_per_bonus=True),
    ),
    (
        "counterspell.ties_go_to_caster",
        lambda rules: rules["counterspell"].update(ties_go_to_caster="caster"),
    ),
    (
        "counterspell.defender_min_mana",
        lambda rules: rules["counterspell"].update(defender_min_mana=1.5),
    ),
    (
        "counterspell.defender_max_mana",
        lambda rules: rules["counterspell"].pop("defender_max_mana"),
    ),
    (
        "counterspell.defender_max_mana",
        lambda rules: rules["counterspell"].update(defender_max_mana="7"),
    ),
    ("counterspell.step", lambda rules: rules["counterspell"].update(step=3)),
    ("casting_failure", lambda rules: rules.pop("casting_failure")),
    ("casting_failure", lambda rules: rules.update(casting_failure=[14])),
    (
        "casting_failure.fumble_floor",
        lambda rules: rules["casting_failure"].pop("fumble_floor"),
    ),
    (
        "casting_failure.mana_lost_totals",
        lambda rules: rules["casting_failure"].pop("mana_lost_totals"),
    ),
    (
        "casting_failure.runaway_total",
        lambda rules: rules["casting_failure"].pop("runaway_total"),
    ),
    (
        "casting_failure.fumble_floor",
        lambda rules: rules["casting_failure"].update(fumble_floor="14"),
    ),
    (
        "casting_failure.fumble_floor",
        lambda rules: rules["casting_failure"].update(fumble_floor=True),
    ),
    (
        "casting_failure.mana_lost_totals",
        lambda rules: rules["casting_failure"].update(mana_lost_totals=15),
    ),
    (
        "casting_failure.mana_lost_totals[1]",
        lambda rules: rules["casting_failure"].update(mana_lost_totals=[14, False]),
    ),
    (
        "casting_failure.mana_lost_totals",
        lambda rules: rules["casting_failure"].update(mana_lost_totals=[15, 15]),
    ),
    (
        "casting_failure.runaway_total",
        lambda rules: rules["casting_failure"].update(runaway_total=15.0),
    ),
    (
        "casting_failure.runaway_total",
        lambda rules: rules["casting_failure"].update(runaway_total=True),
    ),
    (
        "casting_failure.fumble_total",
        lambda rules: rules["casting_failure"].update(fumble_total=14),
    ),
    ("rulings", lambda rules: rules.update(rulings={})),
    ("rulings[0]", lambda rules: rules.update(rulings=["An unkeyed reading."])),
    ("rulings[0].reading", lambda rules: rules["rulings"][0].pop("reading")),
]


class MalformedMagicRulesTest(MagicRulesFileTestCase):
    def test_each_malformed_shape_names_the_file_and_the_field(self) -> None:
        for field_path, malform in MALFORMED_SHAPES:
            with self.subTest(field_path=field_path):
                rules = invented_rules()
                malform(rules)
                self.assert_refused(rules, field_path)

    def test_a_block_this_studio_does_not_read_says_the_canon_is_ahead(
        self,
    ) -> None:
        # Issue #36: a canon carried counterspell before the studio read it,
        # and the refusal sent the reader to migrations. It names the block
        # and what it likely means.
        rules = invented_rules()
        rules["shieldwall"] = {"strength": 3}
        refusal = self.assert_refused(rules, "shieldwall is not a field")
        self.assertIn(
            UNKNOWN_BLOCK_EXPLANATION.replace(BLOCK_PLACEHOLDER, "shieldwall"),
            str(refusal),
        )


#: Natural totals no roll can make, a success tier the board never plays, and
#: a debt cap that leaves nothing to borrow (issue #48), each with the field
#: and the words its refusal names.
UNPLAYABLE_TOTALS: list[tuple[str, str, Callable[[dict[str, Any]], None]]] = [
    (
        "casting_success_tiers[1].natural_totals",
        f"has [2], which {CASTING_DICE} cannot roll",
        lambda rules: rules["casting_success_tiers"][1].update(natural_totals=[2]),
    ),
    (
        "casting_success_tiers[1].natural_totals",
        "has [14], at or above casting_failure.fumble_floor 14",
        lambda rules: rules["casting_success_tiers"][1].update(natural_totals=[14]),
    ),
    (
        "casting_failure.fumble_floor",
        f"has [19], which {CASTING_DICE} cannot roll",
        lambda rules: rules["casting_failure"].update(
            fumble_floor=19, mana_lost_totals=[], runaway_total=None
        ),
    ),
    (
        "casting_failure.runaway_total",
        f"has [19], which {CASTING_DICE} cannot roll",
        lambda rules: rules["casting_failure"].update(
            mana_lost_totals=[14, 19], runaway_total=19
        ),
    ),
    (
        "casting_failure.mana_lost_totals",
        f"has [19], which {CASTING_DICE} cannot roll",
        lambda rules: rules["casting_failure"].update(mana_lost_totals=[14, 15, 19]),
    ),
    (
        "borrowing.automatic_failure_totals",
        "has [1], which 2d8 cannot roll (it rolls 2 to 16)",
        lambda rules: rules["borrowing"].update(automatic_failure_totals=[1, 16]),
    ),
    (
        "push.automatic_runaway_totals",
        "has [13], which 2d6 cannot roll (it rolls 2 to 12)",
        lambda rules: rules["push"].update(automatic_runaway_totals=[2, 13]),
    ),
    (
        "borrowing.max_debt",
        "must be at least 1 or null; 0 leaves nothing to borrow",
        lambda rules: rules["borrowing"].update(max_debt=0),
    ),
]


class UnplayableTotalsTest(MagicRulesFileTestCase):
    """The loader refuses numbers the board could never play (issue #48)."""

    def test_each_unplayable_number_names_the_file_and_the_field(self) -> None:
        for field_path, words, make_unplayable in UNPLAYABLE_TOTALS:
            with self.subTest(field_path=field_path, words=words):
                rules = invented_rules()
                make_unplayable(rules)
                refusal = self.assert_refused(rules, field_path)
                self.assertIn(f"{field_path} {words}", str(refusal))


class EngineValidationTest(MagicRulesFileTestCase):
    """The engine's own validators refuse; the loader names the file."""

    def assert_engine_refused(
        self, rules: Any, field_path: str, engine_words: str
    ) -> None:
        error = self.assert_refused(rules, field_path)
        self.assertIsInstance(error.__cause__, ValueError)
        self.assertIn(engine_words, str(error))

    def test_non_contiguous_failure_bands(self) -> None:
        rules = invented_rules()
        rules["push"]["failure_bands"][1]["lowest_margin"] = 4
        self.assert_engine_refused(rules, "push", "contiguous")

    def test_an_unknown_control_attribute(self) -> None:
        rules = invented_rules()
        rules["push"]["control_attribute"] = "STR"
        self.assert_engine_refused(rules, "push", "control_attribute")

    def test_two_tiers_sharing_a_natural_total(self) -> None:
        rules = invented_rules()
        rules["casting_success_tiers"][1]["natural_totals"] = [4]
        self.assert_engine_refused(rules, "casting_success_tiers", "repeats")

    def test_a_kind_of_magic_with_no_derivation(self) -> None:
        rules = invented_rules()
        rules["channel"]["default_kind"] = "wild"
        self.assert_engine_refused(rules, "channel", "no Channel derivation")

    def test_bad_debt_dice(self) -> None:
        rules = invented_rules()
        rules["borrowing"]["debt_dice"] = "two dice"
        self.assert_refused(rules, "borrowing")

    def test_an_unknown_debt_attribute(self) -> None:
        rules = invented_rules()
        rules["borrowing"]["debt_attributes"] = ["WIS", "STR"]
        self.assert_engine_refused(rules, "borrowing", "debt_attributes")

    def test_no_debt_attributes(self) -> None:
        rules = invented_rules()
        rules["borrowing"]["debt_attributes"] = []
        self.assert_engine_refused(rules, "borrowing", "at least one attribute")

    def test_a_negative_debt_penalty(self) -> None:
        rules = invented_rules()
        rules["borrowing"]["penalty_per_debt"] = -1
        self.assert_engine_refused(rules, "borrowing", "penalty")

    def test_a_negative_debt_cap(self) -> None:
        rules = invented_rules()
        rules["borrowing"]["max_debt"] = -1
        self.assert_engine_refused(rules, "borrowing", "cap")

    def test_borrowing_with_no_cap_and_no_channel(self) -> None:
        rules = invented_rules()
        rules["channel"] = None
        rules["push"]["spell_effect_bonus_per_mana"] = {}
        rules["borrowing"]["max_debt"] = None
        self.assert_engine_refused(rules, "borrowing", "Channel")

    def test_an_unknown_overflow_pool(self) -> None:
        rules = invented_rules()
        rules["runaway"]["overflow_pool"] = "mana"
        self.assert_engine_refused(rules, "runaway", "overflow_pool")

    def test_a_negative_runaway_multiplier(self) -> None:
        rules = invented_rules()
        rules["runaway"]["helper_cost_multiplier_of_cost"] = -1
        self.assert_engine_refused(rules, "runaway", "must not be negative")

    def test_a_runaway_that_drains_nothing(self) -> None:
        rules = invented_rules()
        rules["runaway"].update(drain_multiplier_of_cost=0, drain_flat=0)
        self.assert_engine_refused(rules, "runaway", "drain something")

    def test_an_unknown_anchor_attribute(self) -> None:
        rules = invented_rules()
        rules["runaway"]["self_anchor"]["attribute"] = "STR"
        self.assert_engine_refused(rules, "runaway.self_anchor", "attribute")

    def test_bad_anchor_dice(self) -> None:
        rules = invented_rules()
        rules["runaway"]["helper_anchor"]["dice"] = "two dice"
        self.assert_refused(rules, "runaway.helper_anchor")

    def test_bad_opposed_dice(self) -> None:
        rules = invented_rules()
        rules["counterspell"]["opposed_dice"] = "two dice"
        self.assert_refused(rules, "counterspell")

    def test_an_unknown_opposed_attribute(self) -> None:
        rules = invented_rules()
        rules["counterspell"]["opposed_attribute"] = "STR"
        self.assert_engine_refused(rules, "counterspell", "opposed_attribute")

    def test_a_bonus_step_of_nothing(self) -> None:
        rules = invented_rules()
        rules["counterspell"]["mana_per_bonus"] = 0
        self.assert_engine_refused(rules, "counterspell", "greater than 0")

    def test_a_negative_least_commitment(self) -> None:
        rules = invented_rules()
        rules["counterspell"]["defender_min_mana"] = -1
        self.assert_engine_refused(rules, "counterspell", "must not be negative")

    def test_a_commitment_cap_below_the_least(self) -> None:
        rules = invented_rules()
        rules["counterspell"].update(defender_min_mana=3, defender_max_mana=2)
        self.assert_engine_refused(rules, "counterspell", "below its least")

    def test_a_fumble_floor_below_one(self) -> None:
        rules = invented_rules()
        rules["casting_failure"].update(
            fumble_floor=0, mana_lost_totals=[], runaway_total=None
        )
        self.assert_engine_refused(rules, "casting_failure", "at least 1")

    def test_a_runaway_total_below_the_fumble_floor(self) -> None:
        rules = invented_rules()
        rules["casting_failure"].update(mana_lost_totals=[], runaway_total=13)
        self.assert_engine_refused(rules, "casting_failure", "below the fumble floor")

    def test_a_mana_lost_total_below_the_fumble_floor(self) -> None:
        rules = invented_rules()
        rules["casting_failure"]["mana_lost_totals"] = [13, 14, 15]
        self.assert_engine_refused(rules, "casting_failure", "mana-lost totals [13]")

    def test_a_runaway_total_that_spends_no_mana(self) -> None:
        rules = invented_rules()
        rules["casting_failure"]["mana_lost_totals"] = [14]
        self.assert_engine_refused(rules, "casting_failure", "not a mana-lost total")


class CanonMagicRulesTest(SimpleTestCase):
    def tearDown(self) -> None:
        magic_rules.cache_clear()

    def test_the_default_canon_file_loads(self) -> None:
        rules = DEFAULT_MAGIC_RULES
        self.assertIsNotNone(rules.push)
        # The default canon's channel.md sets no separate limit on Channel,
        # so its invented Borrowing carries a debt cap to bound a casting.
        self.assertIsNone(rules.channel)
        assert rules.borrowing is not None
        self.assertIsNotNone(rules.borrowing.max_debt)
        # Its runaway.md says a Runaway feeds on the caster each round.
        self.assertIsNotNone(rules.runaway)
        # Its counterspell.md says a defender may commit mana to a counter.
        self.assertIsNotNone(rules.counterspell)
        # Its mana-pool.md says how a casting roll's high results fail.
        self.assertIsNotNone(rules.casting_failure)

    def test_the_loaded_canon_file_loads_once_per_process(self) -> None:
        self.assertEqual(MAGIC_RULES_FILE, DM_RULES_DIR / CANON_MAGIC_RULES_FILE_NAME)
        magic_rules.cache_clear()
        loaded = magic_rules()
        self.assertEqual(loaded, load_magic_rules(MAGIC_RULES_FILE))
        self.assertIs(magic_rules(), loaded)


class TarmarMagicRulesTest(SimpleTestCase):
    """The Tarmar canon's file says what its magic-mechanics pages say.

    The values are the canon's: its ``tests/expectations.json`` carries them
    under ``magic_rules``, and none appears here.
    """

    @skip_unless_canon_expects("magic_rules")
    def test_the_file_matches_the_canon_expectations(self) -> None:
        rules = load_magic_rules(MAGIC_RULES_FILE)
        assert rules.push is not None, "the Tarmar canon offers Push"
        assert rules.channel is not None, "the Tarmar canon has Channel"
        runaway_band = rules.push.runaway_band()
        assert runaway_band is not None, "the Tarmar canon has a Runaway band"
        assert rules.borrowing is not None, "the Tarmar canon offers Borrowing"
        assert rules.runaway is not None, "the Tarmar canon runs a Runaway"
        assert rules.counterspell is not None, "the Tarmar canon offers Counterspell"
        assert rules.casting_failure is not None, (
            "the Tarmar canon fails a casting roll on its high totals"
        )
        summary = {
            "tier_natural_totals": sorted(
                total
                for tier in rules.casting_success_tiers
                for total in tier.natural_totals
            ),
            "control_dice": rules.push.control_dice,
            "control_attribute": rules.push.control_attribute,
            "penalty_per_mana": rules.push.penalty_per_mana,
            "base_cost_counts_as_invested": rules.push.base_cost_counts_as_invested,
            "runaway_from_margin": runaway_band.lowest_margin,
            "failure_band_margins": [
                [band.lowest_margin, band.highest_margin]
                for band in rules.push.failure_bands
            ],
            "automatic_runaway_totals": sorted(rules.push.automatic_runaway_totals),
            "channel_source_skills": {
                kind: derivation.source_skill
                for kind, derivation in rules.channel.derivations.items()
            },
            "borrowing": {
                "debt_dice": rules.borrowing.debt_dice,
                "debt_attributes": list(rules.borrowing.debt_attributes),
                "penalty_per_debt": rules.borrowing.penalty_per_debt,
                "failure_is_runaway": rules.borrowing.failure.is_runaway,
                "automatic_failure_totals": sorted(
                    rules.borrowing.automatic_failure_totals
                ),
                "max_debt": rules.borrowing.max_debt,
                "refunds_pay_debt_first": rules.borrowing.refunds_pay_debt_first,
            },
            "runaway": {
                "drain_multiplier_of_cost": rules.runaway.drain_multiplier_of_cost,
                "drain_flat": rules.runaway.drain_flat,
                "overflow_pool": rules.runaway.overflow_pool,
                "death_multiplier_of_max": rules.runaway.death_multiplier_of_max,
                "self_anchor": [
                    rules.runaway.self_anchor.dice,
                    rules.runaway.self_anchor.attribute,
                ],
                "helper_anchor": [
                    rules.runaway.helper_anchor.dice,
                    rules.runaway.helper_anchor.attribute,
                ],
                "helper_cost_multiplier_of_cost": (
                    rules.runaway.helper_cost_multiplier_of_cost
                ),
                "helper_failure_catches_helper": (
                    rules.runaway.helper_failure_catches_helper
                ),
                "effect_bonus_per_turn": rules.runaway.effect_bonus_per_turn,
            },
            "counterspell": {
                "opposed_dice": rules.counterspell.opposed_dice,
                "opposed_attribute": rules.counterspell.opposed_attribute,
                "mana_per_bonus": rules.counterspell.mana_per_bonus,
                "ties_go_to_caster": rules.counterspell.ties_go_to_caster,
                "defender_min_mana": rules.counterspell.defender_min_mana,
                "defender_max_mana": rules.counterspell.defender_max_mana,
            },
            "casting_failure": {
                "fumble_floor": rules.casting_failure.fumble_floor,
                "mana_lost_totals": sorted(rules.casting_failure.mana_lost_totals),
                "runaway_total": rules.casting_failure.runaway_total,
            },
        }
        self.assertEqual(summary, CANON_EXPECTATIONS["magic_rules"])
