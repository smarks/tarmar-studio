"""A classic Melee figure's turn menu, read as choices to weigh (issue #890).

#788 gave a seated Tarmar figure's options a plain line, a cost and the AI's
scoring behind a switch; a classic figure's menu was passed through with the
classic policy's tactic label ("already in reach: stand and strike") as its
only line. Each classic option now carries the same fields, built from the
classic figures' own numbers read through the classic engine at the pause.
"""

from hexarena.hex import Hex
from tarmar_engine.classic import policy as classic_policy
from tarmar_engine.classic.arena import Arena
from tarmar_engine.classic.data import DAGGER, LEATHER, SMALL_BOW
from tarmar_engine.classic.figure import create_human, create_wizard
from tarmar_engine.classic.state import GameState

from battle import turn_menu
from battle.persistence import CLASSIC_PROFILE
from battle.tests.test_persistence import classic_game
from common.test_helpers import BaseTestCase


def facing_toward(arena: Arena, start: Hex, end: Hex) -> int:
    """The direction from ``start`` of the first hex on the line to ``end``."""
    facing = arena.layout.direction_to(start, arena.layout.line(start, end)[1])
    assert facing is not None
    return facing


class ClassicMenuIsDescribedTest(BaseTestCase):
    def menu_for(self, game, figure) -> list[dict]:
        advice = classic_policy.choose_option(game, figure)
        menu = turn_menu.player_menu(CLASSIC_PROFILE, game, figure, advice)
        return [candidate.to_payload() for candidate in menu]

    def entry(self, entries: list[dict], letter: str) -> dict:
        return next(entry for entry in entries if entry["letter"] == letter)

    def test_every_option_carries_a_plain_line_and_the_engine_menu_is_kept(self):
        game = classic_game()
        red, _blue = game.figures
        advice = classic_policy.choose_option(game, red)
        entries = self.menu_for(game, red)
        # The engine's own options, targets, scores and working, unchanged.
        self.assertEqual(
            [
                (
                    entry["letter"],
                    entry["target_id"],
                    entry["score"],
                    entry["rationale"],
                )
                for entry in entries
            ],
            [(c.letter, c.target_id, c.score, c.rationale) for c in advice.candidates],
        )
        for entry in entries:
            with self.subTest(letter=entry["letter"]):
                self.assertTrue(entry["plain"])
                self.assertIn("cost", entry)
                self.assertEqual(entry["target_name"], "Blue")
        self.assertEqual(sum(entry["ai_choice"] for entry in entries), 1)
        self.assertTrue(self.entry(entries, advice.chosen.letter)["ai_choice"])

    def test_an_attack_reads_its_odds_and_damage_off_the_classic_figures(self):
        game = classic_game()
        red, _blue = game.figures
        attack = self.entry(self.menu_for(game, red), "attack")
        # adjDX 10 (DX 12, leather -2) on 3d6 is 50%; the Broadsword's 2d6
        # less the leather's 2. No average: that is the engine's to say
        # (smarks/tarmar-engine#23), so the line does not claim one.
        self.assertEqual(
            attack["plain"],
            "Attack Blue with the Broadsword: 10 or less on 3d6 to hit "
            "(about 50%), 2d6 less 2 armour",
        )
        self.assertEqual(attack["cost"], "")

    def test_a_defending_target_is_read_as_four_dice(self):
        game = classic_game()
        red, blue = game.figures
        blue.defending = True
        attack = self.entry(self.menu_for(game, red), "attack")
        self.assertIn("10 or less on 4d6 to hit", attack["plain"])
        defend = self.entry(self.menu_for(game, red), "shift_defend")
        self.assertEqual(
            defend["plain"],
            "Shift a hex and defend: blows in melee roll 4d6 to hit you, not 3d6",
        )

    def test_an_archer_at_range_takes_the_range_penalty_and_names_its_spare(self):
        game = classic_game()
        red, blue = game.figures
        blue.position = Hex(2, 8)
        red.weapons = [SMALL_BOW, DAGGER]
        red.ready_weapon = SMALL_BOW
        entries = self.menu_for(game, red)
        shot = self.entry(entries, "missile_attack")
        needed = game.rules.to_hit_number(
            red,
            zone=None,
            ignore_facing=True,
            range_penalty=game.rules.missile_range_penalty(1),
        )
        self.assertTrue(
            shot["plain"].startswith(
                f"Shoot Blue with the Small bow: {needed} or less on 3d6 to hit"
            ),
            shot["plain"],
        )
        self.assertIn("1d6-1 less 2 armour", shot["plain"])
        self.assertEqual(
            self.entry(entries, "ready_weapon")["plain"], "Ready the Dagger"
        )
        self.assertEqual(
            self.entry(entries, "move")["plain"], "Move up to 8 hexes toward Blue"
        )

    def test_a_boxed_in_figure_s_moves_read_as_standing(self):
        """A move the classic engine's plan walks nowhere says so (#57)."""
        arena = Arena(cols=9, rows=15)
        red = create_human("Red", 12, 12, "red")
        blue = create_human(
            "Blue", 12, 12, "blue", weapons=[SMALL_BOW], ready_weapon=SMALL_BOW
        )
        red.position, blue.position = Hex(4, 4), Hex(4, 9)
        guards = []
        for index, cell in enumerate(arena.layout.neighbors(red.position)):
            guard = create_human(f"Guard {index}", 12, 12, "red")
            guard.position = cell
            guards.append(guard)
        game = GameState(arena, [red, blue, *guards])
        entries = self.menu_for(game, red)
        # The engine's own plan for each walks nowhere.
        for letter in ("move", "half_move", "dodge"):
            with self.subTest(letter=letter):
                candidate = next(
                    candidate
                    for candidate in classic_policy.choose_option(game, red).candidates
                    if candidate.letter == letter
                )
                self.assertEqual(classic_policy.plan_for(game, red, candidate).path, [])
        self.assertEqual(
            self.entry(entries, "move")["plain"],
            "Stand where you are, since no move of up to 10 hexes gets nearer to Blue",
        )
        self.assertEqual(
            self.entry(entries, "half_move")["plain"],
            "Stand where you are, since no move of up to 5 hexes gets nearer to Blue",
        )
        self.assertEqual(
            self.entry(entries, "dodge")["plain"],
            "Dodge standing, since no move of up to 5 hexes gets nearer to Blue: "
            "missiles and thrown weapons roll 4d6 to hit you, not 3d6",
        )

    def test_in_the_open_the_dodge_moves_toward_the_archer(self):
        arena = Arena(cols=9, rows=15)
        red = create_human("Red", 12, 12, "red")
        blue = create_human(
            "Blue", 12, 12, "blue", weapons=[SMALL_BOW], ready_weapon=SMALL_BOW
        )
        red.position, blue.position = Hex(4, 4), Hex(4, 9)
        entries = self.menu_for(GameState(arena, [red, blue]), red)
        self.assertEqual(
            self.entry(entries, "dodge")["plain"],
            "Dodge while moving up to 5 hexes toward Blue: missiles and thrown "
            "weapons roll 4d6 to hit you, not 3d6",
        )
        self.assertEqual(
            self.entry(entries, "move")["plain"], "Move up to 10 hexes toward Blue"
        )

    def test_the_chance_counts_the_automatic_hits_and_misses(self):
        game = classic_game()
        # Melee p.10: a 3, 4 or 5 always hits and a 16, 17 or 18 always
        # misses, so no to-hit number makes a classic blow certain either way.
        self.assertGreater(turn_menu._classic_hit_chance(game, 0, 3), 0)
        self.assertLess(turn_menu._classic_hit_chance(game, 18, 3), 100)
        self.assertEqual(turn_menu._classic_hit_chance(game, 10, 3), 50)

    def test_a_wizard_s_cast_says_what_it_invests(self):
        wizard = create_wizard(
            "Ilse",
            strength=10,
            dexterity=12,
            intelligence=10,
            side="red",
            spells_known=["magic_fist"],
        )
        foe = create_human("Blue", 12, 12, "blue", armor=LEATHER)
        arena = Arena(cols=9, rows=15)
        wizard.position, foe.position = Hex(2, 2), Hex(2, 6)
        # Each faces the first hex on the line to the other: direction_to
        # names only an adjacent hex, and gave these two None, a facing no
        # classic figure has (``Figure.facing`` is 0-5).
        wizard.facing = facing_toward(arena, wizard.position, foe.position)
        foe.facing = facing_toward(arena, foe.position, wizard.position)
        game = GameState(arena, [wizard, foe])
        casts = [entry for entry in self.menu_for(game, wizard) if entry["spell_key"]]
        self.assertTrue(casts, "the wizard is offered no cast")
        cast = casts[0]
        self.assertEqual(cast["plain"], "Cast Magic Fist at Blue")
        self.assertRegex(cast["cost"], r"^\d+ ST$")
