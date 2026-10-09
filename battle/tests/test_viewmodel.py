"""One shape for the page, whichever profile wrote the state (issue #251).

Slice 6 left a classic battle *creatable, storable, playable and logged, but
not watchable*: the arena and roster panels are painted in the browser from
the Tarmar state — ``combatants`` on an axial hex disc — while a classic
snapshot is ``figures`` on a rectangular offset field. These pin the answer:
the server computes one profile-neutral view model and the page paints it.
"""

import json

from battle import viewmodel
from battle.adaptation import NO_MODELLED_ATTACK_NOTE, snapshot_beast
from battle.classic_adaptation import classic_state
from battle.engine.hexes import add as hex_add
from battle.engine.state import (
    BattleState,
    CombatantState,
    GroundWeapon,
    WeaponState,
)
from common.test_helpers import BaseTestCase, make_beast


def tarmar_combatant(**overrides) -> CombatantState:
    fields = {
        "combatant_id": 7,
        "name": "Duel 1 Archer",
        "archetype": "archer",
        "strength": 12,
        "dexterity": 14,
        "max_fatigue": 20,
        "max_body": 14,
        "fatigue": 15,
        "body": 14,
        "q": 1,
        "r": -2,
        "facing": 2,
        "weapon": WeaponState(name="Longbow", weapon_class="Missile Bow"),
    }
    fields.update(overrides)
    return CombatantState(**fields)


def classic_snapshot(combatant_id: int, name: str, **overrides) -> CombatantState:
    fields = {
        "combatant_id": combatant_id,
        "name": name,
        "archetype": "fighter",
        "strength": 12,
        "dexterity": 12,
        "intelligence": 8,
        "max_fatigue": 10,
        "max_body": 10,
        "fatigue": 10,
        "body": 10,
        "weapon": WeaponState(name="Broadsword", weapon_class="Sword", damage="2d6"),
        "armour_tier": "Light",
        "stops": 2,
    }
    fields.update(overrides)
    return CombatantState(**fields)


class TarmarArenaTest(BaseTestCase):
    """The Tarmar arena, as cells rather than a radius the browser expands."""

    def test_cells_are_the_hex_disc_of_the_arena_radius(self):
        view = viewmodel.tarmar_view(BattleState(arena_radius=2), next_sequence=1)
        # 1 + 3r(r + 1) hexes in a radius-r disc.
        self.assertEqual(len(view["arena"]["cells"]), 19)
        self.assertIn([0, 0], view["arena"]["cells"])
        self.assertIn([2, 0], view["arena"]["cells"])
        self.assertNotIn([2, 1], view["arena"]["cells"])

    def test_the_arena_is_pointy_top(self):
        """The orientation the page lays the cells out on — not a default."""
        view = viewmodel.tarmar_view(BattleState(), next_sequence=1)
        self.assertEqual(view["arena"]["orientation"], viewmodel.POINTY)

    def test_the_view_names_its_profile_and_turn(self):
        view = viewmodel.tarmar_view(BattleState(turn=4), next_sequence=9)
        self.assertEqual(view["profile"], viewmodel.TARMAR_PROFILE)
        self.assertEqual(view["turn"], 4)
        self.assertEqual(view["next_sequence"], 9)


class TarmarCombatantTest(BaseTestCase):
    """What one Tarmar combatant looks like to the page."""

    def view_of(self, combatant: CombatantState) -> dict:
        state = BattleState(arena_radius=4, combatants=[combatant])
        return viewmodel.tarmar_view(state, next_sequence=1)["combatants"][0]

    def test_identity_and_position_cross(self):
        entry = self.view_of(tarmar_combatant())
        self.assertEqual(entry["combatant_id"], 7)
        self.assertEqual(entry["name"], "Duel 1 Archer")
        self.assertEqual([entry["q"], entry["r"]], [1, -2])
        self.assertTrue(entry["alive"])
        self.assertTrue(entry["conscious"])
        self.assertFalse(entry["is_beast"])

    def test_facing_is_a_step_not_an_index(self):
        """The page draws the wedge toward a hex, so it is handed the step.

        A direction *index* only means something against a particular
        direction table, and the two profiles number theirs differently.
        The vector to the faced neighbour means the same thing in both.
        """
        entry = self.view_of(tarmar_combatant(q=1, r=-2, facing=2))
        neighbour = hex_add((1, -2), 2)
        self.assertEqual(entry["facing_vector"], [neighbour[0] - 1, neighbour[1] + 2])

    def test_a_one_hex_figure_occupies_its_own_hex(self):
        entry = self.view_of(tarmar_combatant(q=1, r=-2))
        self.assertEqual(entry["footprint"], [[1, -2]])

    def test_a_multi_hex_figure_carries_its_whole_footprint(self):
        """The engine already computes this; the browser no longer re-derives it."""
        entry = self.view_of(tarmar_combatant(size_hexes=3, q=0, r=0, facing=0))
        self.assertEqual(len(entry["footprint"]), 3)
        self.assertIn([0, 0], entry["footprint"])

    def test_pools_are_fatigue_and_body(self):
        entry = self.view_of(tarmar_combatant(fatigue=15, max_fatigue=20))
        self.assertEqual(
            entry["pools"],
            [
                {"key": "fatigue", "label": "fatigue", "current": 15, "maximum": 20},
                {"key": "body", "label": "body", "current": 14, "maximum": 14},
            ],
        )

    def test_a_standing_combatant_reads_archetype_and_weapon(self):
        self.assertEqual(self.view_of(tarmar_combatant())["status"], "archer — Longbow")

    def test_an_unconscious_combatant_says_so(self):
        entry = self.view_of(tarmar_combatant(conscious=False))
        self.assertEqual(entry["status"], "Unconscious")

    def test_a_dead_combatant_says_so(self):
        entry = self.view_of(tarmar_combatant(alive=False, conscious=False))
        self.assertEqual(entry["status"], "Dead")

    def test_the_fatal_roll_chain_crosses(self):
        entry = self.view_of(tarmar_combatant(alive=False, fatal_chain=[4, 9]))
        self.assertEqual(entry["fatal_chain"], [4, 9])

    def test_a_beast_is_flagged(self):
        self.assertTrue(self.view_of(tarmar_combatant(is_beast=True))["is_beast"])

    def test_a_substituted_beasts_card_says_its_attack_is_not_modelled(self):
        """The roster card used to read "beast — tooth and claw" for a
        figure whose catalog damage had been dropped, which is a name and
        not a warning (#645). The substitution rides in the weapon's own
        name, so every surface that prints a weapon repeats it — this is
        the card."""
        beast = make_beast(save=False, strength=70, attacks=[])
        combatant = snapshot_beast(beast, 3, "Monstrous Snapping Turtle", (0, 0), 0)
        entry = self.view_of(combatant)
        self.assertIn(NO_MODELLED_ATTACK_NOTE, entry["status"])
        self.assertIn("ST 70", entry["status"])

    def test_a_beast_with_a_modelled_attack_names_the_attack(self):
        combatant = snapshot_beast(make_beast(save=False), 3, "Dire Wolf", (0, 0), 0)
        entry = self.view_of(combatant)
        self.assertNotIn(NO_MODELLED_ATTACK_NOTE, entry["status"])
        self.assertIn("Bite", entry["status"])


class ClassicArenaTest(BaseTestCase):
    """The classic field, expressed in the one geometry the page knows."""

    def setUp(self):
        super().setUp()
        self.state, _roster = classic_state(
            [
                classic_snapshot(11, "Duel 1 Fighter"),
                classic_snapshot(12, "Duel 2 Foe"),
            ],
            seed=7,
        )
        self.view = viewmodel.classic_view(self.state, turn=0, next_sequence=3)

    def test_every_arena_hex_is_a_cell(self):
        arena = self.state.arena
        self.assertEqual(len(self.view["arena"]["cells"]), arena.cols * arena.rows)

    def test_the_field_is_flat_top(self):
        """The printed Melee map's orientation, carried rather than rotated away."""
        self.assertEqual(self.view["arena"]["orientation"], viewmodel.FLAT)

    def test_cells_are_axial_so_the_page_needs_one_layout(self):
        """Offset col/row is converted here, not in the browser."""
        from hexarena.hex import Hex

        arena = self.state.arena
        cube_x, _cube_y, cube_z = arena.layout.to_cube(Hex(1, 1))
        self.assertIn([cube_x, cube_z], self.view["arena"]["cells"])

    def test_cells_are_unique(self):
        cells = self.view["arena"]["cells"]
        self.assertEqual(len(cells), len({tuple(cell) for cell in cells}))

    def test_the_view_names_its_profile_and_turn(self):
        self.assertEqual(self.view["profile"], viewmodel.CLASSIC_PROFILE)
        self.assertEqual(self.view["next_sequence"], 3)

    def test_the_turn_is_the_caller_s_and_not_the_engine_s_counter(self):
        """The classic engine's ``turn_number`` names the turn about to be
        played; this app's names the last one completed. Reading the raw
        counter here published a view a turn ahead of the payload carrying
        it, and this assertion used to say the raw counter was right (#661).
        """
        self.assertEqual(self.state.turn_number, 1)  # about to play turn 1
        self.assertEqual(
            viewmodel.classic_view(self.state, turn=0, next_sequence=1)["turn"], 0
        )
        self.assertEqual(
            viewmodel.classic_view(self.state, turn=4, next_sequence=1)["turn"], 4
        )


class ClassicCombatantTest(BaseTestCase):
    """What one classic figure looks like to the page."""

    def setUp(self):
        super().setUp()
        self.state, _roster = classic_state(
            [
                classic_snapshot(11, "Duel 1 Fighter"),
                classic_snapshot(12, "Duel 2 Foe", archetype="archer"),
            ],
            seed=7,
        )
        self.view = viewmodel.classic_view(self.state, turn=0, next_sequence=1)
        self.entries = {
            entry["combatant_id"]: entry for entry in self.view["combatants"]
        }

    def figure(self, uid: str):
        """The figure carrying this uid."""
        return next(one for one in self.state.figures if one.uid == uid)

    def placed(self, uid: str):
        """That figure and the hex it stands on.

        ``Figure.position`` is optional in the package; every figure in a
        battle this app built was placed at creation, and this says so once
        rather than in each test that needs a hex.
        """
        found = self.figure(uid)
        position = found.position
        assert position is not None, f"{found.name} was never placed"
        return found, position

    def test_a_figure_is_known_by_its_row_pk(self):
        """CLASSIC_UID_IS_ROW_PK, read back as the int the page keys cards on."""
        self.assertEqual(sorted(self.entries), [11, 12])

    def test_position_is_axial(self):
        _figure, position = self.placed("11")
        cube_x, _cube_y, cube_z = self.state.arena.layout.to_cube(position)
        entry = self.entries[11]
        self.assertEqual([entry["q"], entry["r"]], [cube_x, cube_z])

    def test_facing_is_the_step_to_the_faced_hex(self):
        figure, position = self.placed("11")
        layout = self.state.arena.layout
        here = layout.to_cube(position)
        there = layout.to_cube(layout.neighbor(position, figure.facing))
        self.assertEqual(
            self.entries[11]["facing_vector"], [there[0] - here[0], there[2] - here[2]]
        )

    def test_a_classic_pool_is_strength(self):
        """Damage lands on ST in classic, so ST is the bar — not a faked body."""
        figure = self.figure("11")
        figure.damage_taken = 4
        entry = viewmodel.classic_view(self.state, turn=0, next_sequence=1)[
            "combatants"
        ][0]
        self.assertEqual(
            entry["pools"],
            [
                {
                    "key": "st",
                    "label": "ST",
                    "current": figure.strength - 4,
                    "maximum": figure.strength,
                }
            ],
        )

    def test_a_standing_figure_reads_its_class_and_weapon(self):
        entry = self.entries[11]
        self.assertIn("—", entry["status"])
        self.assertIn("Broadsword", entry["status"])

    def test_a_collapsed_figure_is_unconscious_not_dead(self):
        figure = self.figure("11")
        figure.damage_taken = figure.strength
        entry = viewmodel.classic_view(self.state, turn=0, next_sequence=1)[
            "combatants"
        ][0]
        self.assertTrue(entry["alive"])
        self.assertFalse(entry["conscious"])
        self.assertEqual(entry["status"], "Unconscious")

    def test_a_dead_figure_says_so(self):
        figure = self.figure("11")
        figure.damage_taken = figure.strength + 1
        entry = viewmodel.classic_view(self.state, turn=0, next_sequence=1)[
            "combatants"
        ][0]
        self.assertFalse(entry["alive"])
        self.assertEqual(entry["status"], "Dead")

    def test_a_figure_on_no_hex_is_refused_by_name(self):
        """An unplaced figure cannot be drawn; say which one, not TypeError."""
        figure = self.state.figures[0]
        figure.position = None
        with self.assertRaises(ValueError) as raised:
            viewmodel.classic_view(self.state, turn=0, next_sequence=1)
        self.assertIn(figure.name, str(raised.exception))

    def test_a_figure_occupies_its_own_hex(self):
        entry = self.entries[11]
        self.assertEqual(entry["footprint"], [[entry["q"], entry["r"]]])

    def test_a_classic_figure_cites_no_fatal_rolls(self):
        """The classic runner logs lines, not roll records — empty, not absent."""
        self.assertEqual(self.entries[11]["fatal_chain"], [])

    def test_a_figure_is_not_a_beast(self):
        """The flag colours the marker; classic figures are all figures here."""
        self.assertFalse(self.entries[11]["is_beast"])

    def test_a_uid_that_is_not_a_row_pk_is_refused_by_name(self):
        figure = self.state.figures[0]
        figure.uid = "f0"
        with self.assertRaises(ValueError) as raised:
            viewmodel.classic_view(self.state, turn=0, next_sequence=1)
        self.assertIn("CLASSIC_UID_IS_ROW_PK", str(raised.exception))


class ViewIsJsonSafeTest(BaseTestCase):
    """The view model is embedded with ``json_script``; it has to serialize."""

    def test_a_tarmar_view_round_trips_through_json(self):
        import json

        view = viewmodel.tarmar_view(
            BattleState(combatants=[tarmar_combatant()]), next_sequence=1
        )
        self.assertEqual(json.loads(json.dumps(view)), view)

    def test_a_classic_view_round_trips_through_json(self):
        import json

        state, _roster = classic_state([classic_snapshot(11, "One")], seed=3)
        view = viewmodel.classic_view(state, turn=0, next_sequence=1)
        self.assertEqual(json.loads(json.dumps(view)), view)


class EmptyViewTest(BaseTestCase):
    """A lobby battle has no state to draw; the page is told so, not guessed at."""

    def test_an_empty_view_carries_its_profile_and_no_cells(self):
        view = viewmodel.empty_view(viewmodel.CLASSIC_PROFILE, turn=0)
        self.assertEqual(view["profile"], viewmodel.CLASSIC_PROFILE)
        self.assertEqual(view["arena"]["cells"], [])
        self.assertEqual(view["combatants"], [])
        self.assertEqual(view["arena"]["orientation"], viewmodel.FLAT)

    def test_every_profile_this_app_runs_has_an_orientation(self):
        """The names come from persistence, so a new profile cannot be missed."""
        from battle.profiles import PROFILES

        self.assertEqual(sorted(viewmodel.ORIENTATIONS), sorted(PROFILES))


class GroundWeaponsTest(BaseTestCase):
    """Weapons lying on the field reach the page (issue #878).

    tarmar-engine 0.9.5 leaves a dropped or thrown weapon in its hex and
    offers PICK UP WEAPON (q) beside it, but the view carried no ground
    weapons, so the arena could not draw where one lay or which it was.
    """

    def test_the_tarmar_view_names_what_lies_in_each_hex(self):
        state = BattleState(
            arena_radius=3,
            ground_weapons=[
                GroundWeapon(q=1, r=0, weapon=WeaponState(name="Broadsword")),
                GroundWeapon(q=-2, r=1, weapon=WeaponState(name="Dagger")),
                GroundWeapon(q=1, r=0, weapon=WeaponState(name="Small Bow")),
            ],
        )
        view = viewmodel.tarmar_view(state, next_sequence=1)
        self.assertEqual(
            view["ground_weapons"],
            [
                {"q": -2, "r": 1, "weapons": ["Dagger"]},
                {"q": 1, "r": 0, "weapons": ["Broadsword", "Small Bow"]},
            ],
        )

    def test_a_clear_field_and_a_lobby_carry_an_empty_list(self):
        view = viewmodel.tarmar_view(BattleState(), next_sequence=1)
        self.assertEqual(view["ground_weapons"], [])
        lobby = viewmodel.empty_view(viewmodel.TARMAR_PROFILE, turn=0)
        self.assertEqual(lobby["ground_weapons"], [])

    def test_the_classic_view_names_its_dropped_weapons_on_the_axial_grid(self):
        state, _roster = classic_state(
            [
                classic_snapshot(11, "Duel 1 Fighter"),
                classic_snapshot(12, "Duel 2 Foe"),
            ],
            seed=7,
        )
        fighter = next(one for one in state.figures if one.uid == "11")
        position = fighter.position
        assert position is not None
        state.dropped.append((position, fighter.weapons[0]))
        view = viewmodel.classic_view(state, turn=0, next_sequence=1)
        cube_x, _cube_y, cube_z = state.arena.layout.to_cube(position)
        self.assertEqual(
            view["ground_weapons"],
            [{"q": cube_x, "r": cube_z, "weapons": [fighter.weapons[0].name]}],
        )

    def test_a_view_with_ground_weapons_round_trips_through_json(self):
        state = BattleState(
            ground_weapons=[GroundWeapon(q=0, r=0, weapon=WeaponState(name="Mace"))]
        )
        view = viewmodel.tarmar_view(state, next_sequence=1)
        self.assertEqual(json.loads(json.dumps(view)), view)
