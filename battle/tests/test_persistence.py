"""The profile-dispatching snapshot seam (issue #251).

``Battle.state_json`` used to have exactly one possible shape, because the
app ran exactly one rules profile. Since tarmar-engine v0.6.0 the classic
profile's ``GameState`` carries the same snapshot contract ``BattleState``
does, so a battle row has to say *which* state type its snapshot holds
before anything can read it back. These tests pin that dispatch: the Tarmar
side unchanged, the classic side round-tripping with its dice stream, and
the shape-agnostic roster reader the field validator needs.
"""

import json
from pathlib import Path

from django.core.exceptions import ValidationError
from hexarena.dice import Dice
from hexarena.hex import Hex

from battle import persistence
from battle.engine.state import BattleState
from battle.models import MAX_STATE_JSON_COMBATANTS, Battle, validate_state_json
from battle.services import create_battle
from common.test_helpers import BaseTestCase


def classic_game(dice=None):
    """Two adjacent classic fighters, ready to select.

    Scripted dice by default (deterministic selection/attacks); pass a live
    ``Dice()`` for the tests that are about the random stream itself.
    """
    from tarmar_engine.classic.arena import Arena
    from tarmar_engine.classic.data import BROADSWORD, LEATHER
    from tarmar_engine.classic.figure import create_human
    from tarmar_engine.classic.state import GameState

    red = create_human(
        "Red",
        12,
        12,
        "red",
        weapons=[BROADSWORD],
        ready_weapon=BROADSWORD,
        armor=LEATHER,
    )
    blue = create_human(
        "Blue",
        12,
        12,
        "blue",
        weapons=[BROADSWORD],
        ready_weapon=BROADSWORD,
        armor=LEATHER,
    )
    arena = Arena(cols=9, rows=15)
    red.position, blue.position = Hex(2, 2), Hex(2, 3)
    red.facing = arena.layout.direction_to(red.position, blue.position)
    blue.facing = arena.layout.direction_to(blue.position, red.position)
    return GameState(arena, [red, blue], dice=dice or Dice(scripted=[5, 3] + [2] * 40))


class ProfileNamesTest(BaseTestCase):
    def test_the_two_profiles_the_app_can_persist(self):
        self.assertEqual(persistence.TARMAR_PROFILE, "tarmar")
        self.assertEqual(persistence.CLASSIC_PROFILE, "classic-melee")
        self.assertEqual(
            set(dict(persistence.PROFILE_CHOICES)), {"tarmar", "classic-melee"}
        )

    def test_an_unregistered_profile_is_refused_by_name(self):
        # Loading has to fail loudly: guessing a state type would resolve the
        # rest of the battle under the wrong rules.
        with self.assertRaisesRegex(ValueError, "unknown rules profile"):
            persistence.load_state({}, "no-such-profile")


class TarmarSideUnchangedTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.battle = create_battle(self.create_dm(), name="T", count=2, seed=7)

    def test_load_returns_the_same_state_the_engine_class_would(self):
        loaded = persistence.load_state(
            self.battle.state_json, self.battle.rules_profile
        )
        self.assertIsInstance(loaded, BattleState)
        self.assertEqual(
            loaded.to_dict(),
            BattleState.from_dict(self.battle.state_json).to_dict(),
        )

    def test_dump_is_the_engine_snapshot_verbatim(self):
        state = BattleState.from_dict(self.battle.state_json)
        self.assertEqual(persistence.dump_state(state), state.to_dict())

    def test_a_new_battle_records_the_tarmar_profile(self):
        self.assertEqual(self.battle.rules_profile, persistence.TARMAR_PROFILE)
        self.battle.refresh_from_db()
        self.assertEqual(self.battle.rules_profile, persistence.TARMAR_PROFILE)


class ClassicSideTest(BaseTestCase):
    def test_a_classic_snapshot_round_trips_through_the_seam(self):
        game = classic_game()
        game.begin_selection()
        snapshot = json.loads(json.dumps(persistence.dump_state(game)))

        restored = persistence.load_state(snapshot, persistence.CLASSIC_PROFILE)

        self.assertEqual([figure.name for figure in restored.figures], ["Red", "Blue"])
        self.assertEqual(restored.turn_number, game.turn_number)
        self.assertEqual(restored.initiative_order, game.initiative_order)

    def test_the_restored_battle_draws_the_same_future_rolls(self):
        # The whole reason the studio needed v0.6.0: a battle served out of
        # state_json must resolve identically to the one it was snapshotted
        # from, not merely start from the same board. Live dice, not the
        # scripted queue -- it is the RNG state that has to survive.
        game = classic_game(dice=Dice())
        game.dice.roll_n(3)  # spend some of the stream before snapshotting
        snapshot = json.loads(json.dumps(persistence.dump_state(game)))

        restored = persistence.load_state(snapshot, persistence.CLASSIC_PROFILE)

        self.assertEqual(restored.dice.roll_n(3), game.dice.roll_n(3))

    def test_a_scripted_queue_survives_the_trip_too(self):
        game = classic_game()
        game.dice.roll()  # consume the 5, leaving 3 at the head
        snapshot = json.loads(json.dumps(persistence.dump_state(game)))

        restored = persistence.load_state(snapshot, persistence.CLASSIC_PROFILE)

        self.assertEqual(restored.dice.roll(), 3)

    def test_the_sjg_subpackage_is_never_imported_at_module_level(self):
        # The classic subpackage is quarantined by the unification plan's
        # copyright note; the seam may reach it only lazily, inside the call
        # the consumer explicitly asked for.
        source = Path(persistence.__file__).read_text()
        module_level_imports = [
            line
            for line in source.splitlines()
            if line.startswith(("import ", "from "))
        ]
        self.assertFalse(
            [line for line in module_level_imports if "tarmar_engine.classic" in line],
            "battle/persistence.py imports the SJG subpackage at module level",
        )


class RosterEntriesTest(BaseTestCase):
    def test_reads_the_tarmar_roster_key(self):
        battle = create_battle(self.create_dm(), name="T", count=2, seed=7)
        self.assertEqual(len(persistence.roster_entries(battle.state_json)), 2)

    def test_reads_the_classic_roster_key(self):
        snapshot = persistence.dump_state(classic_game())
        self.assertEqual(len(persistence.roster_entries(snapshot)), 2)

    def test_a_snapshot_with_neither_key_has_an_empty_roster(self):
        # An unstarted lobby's state_json is ``{}``; that is not an error.
        self.assertEqual(persistence.roster_entries({}), [])


class FieldValidatorTest(BaseTestCase):
    def test_accepts_a_classic_snapshot(self):
        validate_state_json(persistence.dump_state(classic_game()))

    def test_caps_the_classic_roster_too(self):
        oversized = {"figures": [{}] * (MAX_STATE_JSON_COMBATANTS + 1)}
        with self.assertRaisesRegex(ValidationError, "Too many combatants"):
            validate_state_json(oversized)

    def test_still_caps_the_tarmar_roster(self):
        oversized = {"combatants": [{}] * (MAX_STATE_JSON_COMBATANTS + 1)}
        with self.assertRaisesRegex(ValidationError, "Too many combatants"):
            validate_state_json(oversized)

    def test_a_non_list_roster_is_refused(self):
        with self.assertRaisesRegex(ValidationError, "must be a list"):
            validate_state_json({"figures": "nope"})


class BattleProfileFieldTest(BaseTestCase):
    def test_defaults_to_tarmar(self):
        field = Battle._meta.get_field("rules_profile")
        self.assertEqual(field.default, persistence.TARMAR_PROFILE)

    def test_offers_exactly_the_persistable_profiles(self):
        field = Battle._meta.get_field("rules_profile")
        self.assertEqual(list(field.choices or []), list(persistence.PROFILE_CHOICES))


class StorableProfileTest(BaseTestCase):
    """The lenient name resolver the front-door default goes through (#332).

    A theme names its profile as a plain string -- ``common`` may not import
    ``battle`` -- so this is where a name from presentation config is
    checked before it can reach a draft row.
    """

    def test_a_persistable_name_comes_back_unchanged(self):
        for profile_name in (persistence.TARMAR_PROFILE, persistence.CLASSIC_PROFILE):
            with self.subTest(profile=profile_name):
                self.assertEqual(
                    persistence.storable_profile(profile_name), profile_name
                )

    def test_an_unknown_name_falls_back_to_tarmar_rather_than_raising(self):
        for bogus in ("calvinball", "", "TARMAR"):
            with self.subTest(profile=bogus):
                self.assertEqual(
                    persistence.storable_profile(bogus), persistence.TARMAR_PROFILE
                )
