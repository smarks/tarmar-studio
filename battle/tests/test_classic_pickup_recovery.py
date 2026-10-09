"""Battles bricked by the unguarded classic PICK UP path come back (#286/#287).

Up to tarmar-engine v0.9.0 the classic PICK UP WEAPON action skipped both
guards that READY WEAPON applies: it let a figure take a weapon over its ST,
and left a shield readied under a two-handed weapon. Because the app reloads
``Battle.state_json`` every turn and ``Figure.__post_init__`` re-validates
wielding on each load, the first of those bricked the battle outright — detail
page and advance-turn both 500, with the restart button stranded behind the
dead page.

The engine fix (v0.9.1) closes the pickup path and teaches the state loader to
salvage the gear an older engine already wrote. These tests pin the app-facing
halves of that: a snapshot saved by a pre-fix engine loads and renders again,
and a long classic battle plays through the fumble-and-pickup window without
the round trip breaking.
"""

from battle.models import Battle
from battle.persistence import CLASSIC_PROFILE, dump_state, load_state
from battle.services import advance_turn, create_battle
from common.test_helpers import BaseTestCase


class BrickedSnapshotRecoveryTest(BaseTestCase):
    """A battle a pre-v0.9.1 engine bricked is playable again after the bump."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.battle = create_battle(
            self.dm,
            name="Salvage",
            count=3,
            seed=4242,
            rules_profile=CLASSIC_PROFILE,
        )

    def brick(self, **gear):
        """Rewrite figure 0's gear the way the unguarded pickup used to."""
        snapshot = dict(self.battle.state_json)
        figures = [dict(figure) for figure in snapshot["figures"]]
        figures[0] = {**figures[0], **gear}
        snapshot["figures"] = figures
        self.battle.state_json = snapshot
        self.battle.save(update_fields=["state_json"])
        return figures[0]["name"]

    def test_an_over_st_snapshot_loads_instead_of_raising(self):
        # The exact #286 shape: ST 7 figure wielding an ST 11 Mace.
        self.brick(strength=7, weapons=["Mace"], ready_weapon="Mace")

        state = load_state(self.battle.state_json, CLASSIC_PROFILE)

        figure = state.figures[0]
        self.assertIsNone(figure.ready_weapon)
        self.assertNotIn("Mace", [weapon.name for weapon in figure.weapons])
        self.assertIn("Mace", [weapon.name for _hex, weapon in state.dropped])

    def test_the_salvaged_weapon_waits_on_the_ground_to_be_retaken(self):
        """Salvage drops it at the figure's hex, not into nothing."""
        self.brick(strength=7, weapons=["Mace"], ready_weapon="Mace")

        state = load_state(self.battle.state_json, CLASSIC_PROFILE)

        figure = state.figures[0]
        self.assertIn(
            "Mace",
            [
                weapon.name
                for hex_pos, weapon in state.dropped
                if hex_pos == figure.position
            ],
        )

    def test_the_battle_page_serves_again_instead_of_500ing(self):
        """The user-facing brick: detail was unreachable, so was restart."""
        self.brick(strength=7, weapons=["Mace"], ready_weapon="Mace")
        self.client.force_login(self.dm)

        response = self.client.get(f"/tarmar/battles/{self.battle.pk}/")

        self.assertEqual(response.status_code, 200)

    def test_a_bricked_battle_can_be_advanced_again(self):
        self.brick(strength=7, weapons=["Mace"], ready_weapon="Mace")

        battle, events = advance_turn(self.battle.pk)

        self.assertTrue(events)

    def test_a_shield_left_up_under_two_hands_is_lowered_out_loud(self):
        """#287: the flip used to be silent, so the log and the board lied.

        ``__post_init__`` always lowered the shield on load, which is why
        ``dump(load(x)) != x``; what it never did was say so, leaving the DM
        with a figure that had been defending illegally and no record of it.
        """
        name = self.brick(
            strength=15,
            dexterity=9,
            weapons=["Battleaxe"],
            ready_weapon="Battleaxe",
            shield_ready=True,
        )

        state = load_state(self.battle.state_json, CLASSIC_PROFILE)

        self.assertFalse(state.figures[0].shield_ready)
        self.assertTrue(
            any(name in line and "shield" in line.lower() for line in state.log),
            f"no shield note for {name} in {state.log}",
        )
        # And from the salvaged state on, the round trip is faithful.
        resnapshot = dump_state(state)
        self.assertEqual(
            dump_state(load_state(resnapshot, CLASSIC_PROFILE)), resnapshot
        )

    def test_an_untouched_battle_is_not_disturbed_by_the_salvage_layer(self):
        """Salvage must be inert for every state the fixed engine writes."""
        snapshot = self.battle.state_json

        self.assertEqual(dump_state(load_state(snapshot, CLASSIC_PROFILE)), snapshot)


class SeededClassicSoakTest(BaseTestCase):
    """The QA pass's exact seeds run clean to completion (#286 / #287)."""

    def _drive(self, battle, *, max_turns, check_round_trip=False):
        """Advance to completion, reloading each saved state as the UI would."""
        for _turn in range(max_turns):
            battle, _events = advance_turn(battle.pk)
            # The detail page and the next advance both reload state_json; a
            # state that cannot load (or loads changed) is a bricked or
            # drifting battle even when advance_turn itself kept going.
            restored = load_state(battle.state_json, CLASSIC_PROFILE)
            if check_round_trip:
                self.assertEqual(dump_state(restored), battle.state_json)
            if battle.status == Battle.STATUS_FINISHED:
                return battle
        self.fail(f"battle did not finish within {max_turns} turns")

    def test_the_brick_seed_completes_and_every_turn_reloads(self):
        # Seed 26251, count 8: a ST 7 figure fumbled at turn 90, picked up an
        # ST 11 Mace, and turn 91's load raised ValueError — bricking the
        # battle with no in-UI recovery (#286).
        battle = create_battle(
            self.create_dm(),
            name="QA brick 26251",
            count=8,
            seed=26251,
            rules_profile=CLASSIC_PROFILE,
        )
        battle = self._drive(battle, max_turns=200)
        self.assertGreater(battle.turn_number, 91)

    def test_the_shield_seed_round_trips_faithfully_every_turn(self):
        # Seed 10220, count 8, two teams: at turn 36 a Large-shield bearer
        # picked a Longbow off the ground and kept shield_ready=True —
        # illegal protection, and dump(load(state)) flipped it, so the
        # persisted and in-memory battles disagreed (#287).
        battle = create_battle(
            self.create_dm(),
            name="QA shield 10220",
            count=8,
            seed=10220,
            rules_profile=CLASSIC_PROFILE,
            fighter_teams=("Red", "Blue") * 4,
        )
        self._drive(battle, max_turns=200, check_round_trip=True)
