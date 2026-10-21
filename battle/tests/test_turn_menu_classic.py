"""A classic Melee figure's turn menu, read as choices to weigh (issue #890).

#788 gave a seated Tarmar figure's options a plain line, a cost and the AI's
scoring behind a switch; a classic figure's menu was passed through with the
classic policy's tactic label ("already in reach: stand and strike") as its
only line. Each classic option now carries the same fields, built from the
classic figures' own numbers read through the classic engine at the pause.

The lines are read under :data:`~.rulings_words.FIXED_WORDS` (#90): each
sentence is its own key followed by what filled it, so the tests prove which
sentence was chosen and what filled it, never the shipped wording.
"""

import json

from django.urls import reverse
from hexarena.hex import Hex
from tarmar_engine.classic import policy as classic_policy
from tarmar_engine.classic.arena import Arena
from tarmar_engine.classic.data import (
    BROADSWORD,
    CHAINMAIL,
    DAGGER,
    HALBERD,
    LEATHER,
    LIGHT_CROSSBOW,
    MAIN_GAUCHE,
    SABER,
    SMALL_BOW,
    SPEAR,
)
from tarmar_engine.classic.figure import Figure, create_human, create_wizard
from tarmar_engine.classic.monsters import create_monster
from tarmar_engine.classic.options import Option as ClassicOption
from tarmar_engine.classic.state import GameState

from battle import control, turn_menu
from battle.models import Battle
from battle.persistence import CLASSIC_PROFILE
from battle.policy import Candidate
from battle.services import advance_turn, create_battle
from battle.tests.fixed_refusals import with_fixed_refusals
from battle.tests.test_persistence import classic_game
from common.test_helpers import BaseTestCase, create_character

from .rulings_words import with_fixed_words

#: How far a classic move goes, under the fixed words.
CLASSIC_REACH = "turn_menu.reach {hexes} classic_menu.move_word"

#: A charge that strikes ``foe`` with ``weapon`` after moving its 5 hexes,
#: with its odds, under the fixed words.
CHARGE_WITH_ODDS = (
    r"^turn_menu\.with_odds classic_menu\.charge {foe} 5 turn_menu\.the_weapon "
    r"{weapon} classic_menu\."
)


def facing_toward(arena: Arena, start: Hex, end: Hex) -> int:
    """The direction from ``start`` of the first hex on the line to ``end``."""
    facing = arena.layout.direction_to(start, arena.layout.line(start, end)[1])
    assert facing is not None
    return facing


def archer_board(
    *, red_at: Hex = Hex(4, 4), boxed: bool = False
) -> tuple[GameState, Figure]:
    """Red, and Blue with a bow at (4, 9); Red ringed by its own side
    when ``boxed``."""
    arena = Arena(cols=9, rows=15)
    red = create_human("Red", 12, 12, "red")
    blue = create_human(
        "Blue", 12, 12, "blue", weapons=[SMALL_BOW], ready_weapon=SMALL_BOW
    )
    red.position, blue.position = red_at, Hex(4, 9)
    guards = []
    for index, cell in enumerate(arena.layout.neighbors(red.position)):
        guard = create_human(f"Guard {index}", 12, 12, "red")
        guard.position = cell
        guards.append(guard)
    return GameState(arena, [red, blue, *(guards if boxed else [])]), red


def armed(name: str, side: str, at: Hex, weapon=None) -> Figure:
    """A ST 13, DX 11 human at ``at``, with ``weapon`` ready if given."""
    kit = {"weapons": [weapon], "ready_weapon": weapon} if weapon else {}
    fighter = create_human(name, 13, 11, side, **kit)
    fighter.position = at
    return fighter


@with_fixed_words
class ClassicMenuIsDescribedTest(BaseTestCase):
    def menu_for(self, game, figure) -> list[dict]:
        menu = turn_menu.player_menu(CLASSIC_PROFILE, game, figure)
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
        red, blue = game.figures
        attack = self.entry(self.menu_for(game, red), "attack")
        # adjDX 10 (DX 12, leather -2) on 3d6 is 50%; the Broadsword's 2d6
        # less the leather's 2; and what a hit does on average, the engine's
        # own figure (tarmar-engine #23).
        forecast = game.attack_forecast(red, blue, ClassicOption.ATTACK)
        self.assertEqual(
            attack["plain"],
            "turn_menu.with_odds turn_menu.attack Blue turn_menu.the_weapon "
            "Broadsword classic_menu.odds_damage_armoured turn_menu.about "
            f"{round(forecast.expected_damage)} 2d6 classic_menu.odds 50 3d6 10 2",
        )
        self.assertEqual(attack["cost"], "")

    def test_a_ready_main_gauche_s_parry_counts_among_the_stops(self):
        """#131: a target with a main-gauche ready in its off hand turns aside
        one hit more, as the engine's damage roll does."""
        red = armed("Red", "red", Hex(4, 4), BROADSWORD)
        blue = create_human(
            "Blue", 13, 11, "blue", weapons=[SABER, MAIN_GAUCHE], ready_weapon=SABER
        )
        blue.position = Hex(4, 5)
        game = GameState(Arena(cols=9, rows=15), [red, blue])
        forecast = game.attack_forecast(red, blue, ClassicOption.ATTACK)
        self.assertEqual(forecast.armour_stops, 1)
        attack = self.entry(self.menu_for(game, red), "attack")
        self.assertRegex(
            attack["plain"],
            r" classic_menu\.odds_damage_armoured (turn_menu\.about \d+|"
            r"turn_menu\.under_one) 2d6 classic_menu\.odds \d+ 3d6 \d+ 1$",
        )

    def test_a_defending_target_is_read_as_four_dice(self):
        game = classic_game()
        red, blue = game.figures
        blue.defending = True
        attack = self.entry(self.menu_for(game, red), "attack")
        self.assertRegex(attack["plain"], r" classic_menu\.odds \d+ 4d6 10 ")
        defend = self.entry(self.menu_for(game, red), "shift_defend")
        self.assertEqual(defend["plain"], "classic_menu.shift_defend 4d6 3d6")

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
        # The Small bow's 1d6-1 less the leather's 2.
        self.assertRegex(
            shot["plain"],
            r"^turn_menu\.with_odds turn_menu\.shoot Blue turn_menu\.the_weapon "
            r"Small bow classic_menu\.odds_damage_armoured "
            r"(turn_menu\.about \d+|turn_menu\.under_one) 1d6-1 classic_menu\.odds "
            rf"\d+ 3d6 {needed} 2$",
        )
        self.assertEqual(
            self.entry(entries, "ready_weapon")["plain"], "turn_menu.ready Dagger"
        )
        self.assertEqual(
            self.entry(entries, "move")["plain"], "classic_menu.move_toward Blue 8"
        )

    def test_a_boxed_in_figure_s_moves_read_as_standing(self):
        """A move the classic engine's plan walks nowhere says so (#57)."""
        game, red = archer_board(boxed=True)
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

        def no_nearer(hexes: int) -> str:
            return f"turn_menu.no_nearer Blue {CLASSIC_REACH.format(hexes=hexes)}"

        self.assertEqual(
            self.entry(entries, "move")["plain"], f"turn_menu.stand {no_nearer(10)}"
        )
        self.assertEqual(
            self.entry(entries, "half_move")["plain"], f"turn_menu.stand {no_nearer(5)}"
        )
        self.assertEqual(
            self.entry(entries, "dodge")["plain"],
            f"classic_menu.dodge 4d6 turn_menu.standing {no_nearer(5)} 3d6",
        )

    def test_in_the_open_the_dodge_moves_toward_the_archer(self):
        entries = self.menu_for(*archer_board())
        self.assertEqual(
            self.entry(entries, "dodge")["plain"],
            "classic_menu.dodge 4d6 classic_menu.dodge_moving_toward Blue 5 3d6",
        )
        self.assertEqual(
            self.entry(entries, "move")["plain"], "classic_menu.move_toward Blue 10"
        )

    def charge_line(self, figures: list[Figure], chooser: Figure) -> str:
        """The CHARGE ATTACK line ``chooser`` reads on a 9 by 15 board."""
        game = GameState(Arena(cols=9, rows=15), figures)
        return self.entry(self.menu_for(game, chooser), "charge_attack")["plain"]

    def test_a_charge_that_strikes_nobody_says_so(self):
        """A charge whose move ends with no foe in reach strikes nothing (#56)."""
        no_attack = "turn_menu.no_attack_charge Blue classic_menu.no_foe_in_reach"
        move = CLASSIC_REACH.format(hexes=5)
        boards = {
            "boxed in": (
                archer_board(boxed=True),
                f"{no_attack} turn_menu.no_nearer Blue {move} turn_menu.bare_hands",
            ),
            "too far": (
                archer_board(red_at=Hex(4, 1)),
                f"{no_attack} turn_menu.ends_from Blue 3 hexes {move} "
                "turn_menu.bare_hands",
            ),
            # Beside Blue, but bare hands only grapple, and the engine admits
            # no grapple at Blue here (Melee p.17).
            "bare hands": (
                archer_board(),
                f"{no_attack} turn_menu.ends_from Blue 1 hex {move} "
                "turn_menu.bare_hands",
            ),
        }
        for label, ((game, red), wanted) in boards.items():
            with self.subTest(board=label):
                self.assertEqual(
                    self.entry(self.menu_for(game, red), "charge_attack")["plain"],
                    wanted,
                )
        sword = armed("Red", "red", Hex(4, 2), BROADSWORD)
        self.assertEqual(
            self.charge_line([sword, armed("Blue", "blue", Hex(4, 9))], sword),
            f"{no_attack} turn_menu.ends_from Blue 2 hexes {move} "
            "turn_menu.the_weapon Broadsword",
        )

    def test_a_charge_in_reach_moves_and_strikes(self):
        sword = armed("Red", "red", Hex(4, 4), BROADSWORD)
        self.assertRegex(
            self.charge_line([sword, armed("Blue", "blue", Hex(4, 9))], sword),
            CHARGE_WITH_ODDS.format(foe="Blue", weapon="Broadsword"),
        )

    def test_a_charge_that_ends_on_the_flank_takes_the_flank_bonus(self):
        """The charge line reads the blow from where the charge ends
        (tarmar-engine #23), not from where the charger stands: Red starts in
        Blue's front arc, where a blow would take no facing bonus, and its
        charge ends on Blue's flank, which gives the +2 the blow will be
        declared with."""
        red = armed("Red", "red", Hex(6, 8), BROADSWORD)
        blue = armed("Blue", "blue", Hex(4, 8))
        blue.facing = 0
        game = GameState(Arena(cols=9, rows=15), [red, blue])
        from_the_start = game.attack_forecast(red, blue, ClassicOption.ATTACK)
        self.assertEqual((from_the_start.zone, from_the_start.needed), ("front", 11))
        line = self.entry(self.menu_for(game, red), "charge_attack")["plain"]
        # The whole line, anchored at both ends: the odds at 13 and nothing
        # after them, no "before any flank or rear bonus" (the clause the
        # menu wrote before the engine forecast a charge from its end).
        self.assertRegex(
            line,
            r"^turn_menu\.with_odds classic_menu\.charge Blue 5 turn_menu\.the_weapon "
            r"Broadsword classic_menu\.odds_damage(_armoured)? "
            r"(turn_menu\.about \d+|turn_menu\.under_one) 2d6 classic_menu\.odds "
            r"\d+ 3d6 13( \d+)?$",
        )

    def test_a_pole_charge_in_a_straight_line_adds_its_die(self):
        """A pole used in a charge of three or more straight hexes does an
        extra die (p.12), the engine's ``extra_dice``."""
        spear = armed("Red", "red", Hex(4, 3), SPEAR)
        self.assertRegex(
            self.charge_line([spear, armed("Blue", "blue", Hex(4, 7))], spear),
            r"^turn_menu\.with_odds classic_menu\.charge Blue 5 turn_menu\.the_weapon "
            r"Spear classic_menu\.odds_damage turn_menu\.about \d+ "
            r"classic_menu\.charge_dice 1d6\+1 1d6 classic_menu\.odds \d+ 3d6 \d+$",
        )

    def test_armour_after_a_pole_s_charge_die_comes_off_the_whole(self):
        spear = armed("Red", "red", Hex(4, 3), SPEAR)
        blue = armed("Blue", "blue", Hex(4, 7))
        blue.armor = LEATHER
        self.assertRegex(
            self.charge_line([spear, blue], spear),
            r" classic_menu\.odds_damage_armoured turn_menu\.about \d+ "
            r"classic_menu\.charge_dice_armoured 1d6\+1 1d6 classic_menu\.odds \d+ "
            r"3d6 \d+ 2$",
        )

    def test_a_pole_weapon_s_charge_jabs_two_hexes_off(self):
        """A charge ending two hexes off still strikes with a pole (p.12, #56)."""
        halberd = armed("Red", "red", Hex(4, 2), HALBERD)
        self.assertRegex(
            self.charge_line([halberd, armed("Blue", "blue", Hex(4, 9))], halberd),
            CHARGE_WITH_ODDS.format(foe="Blue", weapon="Halberd"),
        )

    def test_a_charge_reaches_any_hex_of_a_multi_hex_foe(self):
        """The giant's forward hexes are in reach though its anchor is not."""
        sword = armed("Red", "red", Hex(4, 2), BROADSWORD)
        giant = create_monster("Giant", "Giant", "blue")
        giant.position = Hex(4, 9)
        giant.facing = Arena(cols=9, rows=15).layout.direction_to(Hex(4, 9), Hex(4, 8))
        self.assertRegex(
            self.charge_line([sword, giant], sword),
            CHARGE_WITH_ODDS.format(foe="Giant", weapon="Broadsword"),
        )

    def test_a_charge_names_the_foe_the_engine_strikes(self):
        """The engine strikes the foe in reach, not the one it closed on."""
        sword = armed("Red", "red", Hex(4, 4), BROADSWORD)
        blue = armed("Blue", "blue", Hex(4, 8))
        # Weaker, so the AI aims at it, but behind Blue and out of reach.
        green = create_human("Green", 9, 15, "blue")
        green.position = Hex(4, 9)
        self.assertRegex(
            self.charge_line([sword, blue, green], sword),
            r"^turn_menu\.with_odds classic_menu\.charge_another Green 5 Blue "
            r"turn_menu\.the_weapon Broadsword classic_menu\.",
        )

    def test_a_bare_handed_charge_into_contact_grapples_with_no_odds(self):
        """A bare-handed charge grapples a slower foe; no to-hit roll (#56).

        The engine's charge ends beside Blue and, with no weapon, begins a
        grapple (``GameState.hth_attack``): Blue's own roll settles it, so
        the line names no 3d6 chance.
        """
        red = create_human("Red", 12, 12, "red")
        red.position = Hex(4, 4)
        blue = armed("Blue", "blue", Hex(4, 8), BROADSWORD)
        blue.armor = CHAINMAIL
        self.assertLess(blue.movement_allowance, red.movement_allowance)
        line = self.charge_line([red, blue], red)
        self.assertEqual(
            line, "classic_menu.charge_grapple Blue classic_menu.grapple_words 5"
        )
        self.assertNotIn("3d6", line)

    def bare_handed_beside(self, foe_armour=None) -> tuple[GameState, Figure]:
        """Bare-handed Red beside Blue's Broadsword, Blue slowed by
        ``foe_armour`` if given, so a grapple may be begun on it (Melee p.17:
        a foe with a lower MA)."""
        red = create_human("Red", 12, 12, "red")
        red.position = Hex(4, 4)
        blue = armed("Blue", "blue", Hex(4, 5), BROADSWORD)
        if foe_armour is not None:
            blue.armor = foe_armour
        return GameState(Arena(cols=9, rows=15), [red, blue]), red

    def test_a_bare_handed_attack_says_it_grapples_with_no_odds(self):
        """#146: the ATTACK and SHIFT ATTACK rows read "attack Blue with bare
        hands", though the engine's blow is a grapple (``attack_blow``)."""
        game, red = self.bare_handed_beside(CHAINMAIL)
        entries = self.menu_for(game, red)
        grapple = "classic_menu.grapple_words"
        self.assertEqual(
            self.entry(entries, "attack")["plain"],
            f"classic_menu.hth_attack Blue {grapple}",
        )
        self.assertEqual(
            self.entry(entries, "shift_attack")["plain"],
            f"classic_menu.shift_grapple {grapple} Blue",
        )

    def test_a_bare_handed_attack_no_grapple_may_begin_strikes_nobody(self):
        """Beside a foe as quick as it, from the front, bare hands may begin no
        grapple, so the engine's ATTACK strikes nobody, and the row says so."""
        game, red = self.bare_handed_beside()
        entries = self.menu_for(game, red)
        for letter, sentence in (
            ("attack", "turn_menu.attack Blue"),
            ("shift_attack", "classic_menu.shift_attack Blue"),
        ):
            with self.subTest(letter=letter):
                self.assertEqual(
                    self.entry(entries, letter)["plain"],
                    f"classic_menu.no_attack {sentence} turn_menu.bare_hands "
                    "classic_menu.no_grapple",
                )

    def test_bare_hands_grapple_after_a_hold_or_a_move_and_the_row_says_so(self):
        """Bare hands grapple under any option but a dodge, a defence, a
        disengage or a cast (``attack_blow``, melee #275): a hold beside a
        slower foe, and a move that ends beside one, grapple it too."""
        game, red = self.bare_handed_beside(CHAINMAIL)
        hold = self.entry(self.menu_for(game, red), "do_nothing")["plain"]
        red.position = Hex(4, 2)
        entries = self.menu_for(game, red)
        grapple = "classic_menu.then_grapple classic_menu.grapple_words"
        self.assertEqual(hold, f"{grapple} classic_menu.do_nothing Blue")
        self.assertEqual(
            self.entry(entries, "move")["plain"],
            f"{grapple} classic_menu.move_toward Blue 10 Blue",
        )
        # A move it then attacks from is not one "without attacking".
        self.assertEqual(
            self.entry(entries, "half_move")["plain"],
            f"{grapple} classic_menu.move_toward Blue 5 Blue",
        )
        # A dodge does not attack, bare-handed or not.
        self.assertNotIn("grapple", self.entry(entries, "dodge")["plain"])

    def test_a_blow_names_the_foe_the_engine_strikes(self):
        """A weapon strikes the weakest foe its front hexes reach
        (``attack_blow``), which may not be the one its row is aimed at."""
        red = armed("Red", "red", Hex(4, 4), BROADSWORD)
        blue = armed("Blue", "blue", Hex(4, 5), BROADSWORD)
        green = create_human("Green", 9, 15, "blue")
        green.position = Hex(5, 4)
        game = GameState(Arena(cols=9, rows=15), [red, blue, green])
        aimed_at_blue = turn_menu.describe_classic(
            game,
            red,
            Candidate("attack", "ATTACK", 0.0, "", target_id=blue.uid),
            ai_choice=False,
        )
        self.assertRegex(
            aimed_at_blue.plain,
            r"^turn_menu\.with_odds turn_menu\.attack Green turn_menu\.the_weapon "
            r"Broadsword classic_menu\.",
        )
        self.assertEqual(aimed_at_blue.target_name, "Blue")

    def test_a_shot_into_a_pile_with_a_friend_strikes_nobody_and_says_so(self):
        """The engine looses no missile into a hand-to-hand pile holding a
        friend (``attack_blow``), so a crossbow's MISSILE ATTACK at the foe in
        it strikes nobody, though the foe is in range (review of #146, the
        reviewer's seed 341)."""
        friend = armed("F0", "red", Hex(3, 2))
        foe = armed("F1", "blue", Hex(3, 2))
        friend.hth_opponents, foe.hth_opponents = [foe.uid], [friend.uid]
        crossbow = armed("F2", "red", Hex(3, 7), LIGHT_CROSSBOW)
        game = GameState(Arena(cols=9, rows=15), [friend, foe, crossbow])
        entry = self.entry(self.menu_for(game, crossbow), "missile_attack")
        self.assertEqual(entry["target_name"], "F1")
        self.assertEqual(
            entry["plain"],
            "classic_menu.no_attack turn_menu.shoot F1 turn_menu.the_weapon "
            "Light crossbow classic_menu.no_foe_struck turn_menu.the_weapon "
            "Light crossbow",
        )

    def test_an_option_the_engine_offers_and_then_refuses_says_so(self):
        """The engine's menu offers CHANGE WEAPONS to a bow while engaged, and
        its own verbs then refuse to ready a missile weapon there (review of
        #146; tarmar-engine #68, #69). The menu is built, the row says in the
        studio's words that it cannot be carried out, and it is marked so the
        panel draws it unselectable and the choice path refuses it."""
        archer = armed("Red", "red", Hex(4, 4), SMALL_BOW)
        game = GameState(
            Arena(cols=9, rows=15),
            [archer, armed("Blue", "blue", Hex(4, 5), BROADSWORD)],
        )
        before = game.to_dict()
        entries = self.menu_for(game, archer)
        entry = self.entry(entries, "change_weapons")
        self.assertEqual(
            entry["plain"],
            "classic_menu.cannot_carry_out turn_menu.change_to Small bow",
        )
        self.assertIs(entry[turn_menu.REFUSED], True)
        # Not to be taken, so it costs nothing (the weapon is not dropped).
        self.assertEqual(entry["cost"], "")
        # Only the refused option carries the mark.
        self.assertEqual(
            [one["letter"] for one in entries if turn_menu.REFUSED in one],
            ["change_weapons"],
        )
        self.assertEqual(game.to_dict(), before)

    def test_bare_hands_grapple_after_a_hold_named_at_nobody(self):
        """A hold with no foe named is rehearsed too (review of #146)."""
        game, red = self.bare_handed_beside(CHAINMAIL)
        hold = turn_menu.describe_classic(
            game,
            red,
            Candidate("do_nothing", "DO NOTHING", 0.0, "", target_id=None),
            ai_choice=False,
        )
        self.assertEqual(
            hold.plain,
            "classic_menu.then_grapple classic_menu.grapple_words "
            "classic_menu.do_nothing Blue",
        )

    def test_the_chance_counts_the_automatic_hits_and_misses(self):
        # Melee p.10: a 3, 4 or 5 always hits and a 16, 17 or 18 always
        # misses, so no to-hit number makes a classic blow certain either way:
        # 10 of 216 rolls hit whatever the adjDX, and 10 of 216 miss.
        for dexterity, chance in ((1, 5), (24, 95)):
            with self.subTest(dexterity=dexterity):
                game = classic_game()
                red, _blue = game.figures
                red.dexterity = dexterity
                attack = self.entry(self.menu_for(game, red), "attack")
                self.assertRegex(
                    attack["plain"], rf" classic_menu\.odds {chance} 3d6 -?\d+"
                )

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
        self.assertEqual(cast["plain"], "turn_menu.cast_at Blue Magic Fist")
        self.assertRegex(cast["cost"], r"^classic_menu\.cast_cost \d+$")


@with_fixed_refusals
class AnOptionTheRulesRefuseIsRefusedWhenChosenTest(BaseTestCase):
    """A choice of an option the menu marks refused is refused, not stored to
    fail the turn (review of #146): the same for a player, a Game Master and
    the AI hand-off. The choice path reads the stored menu, the same in either
    profile, so a Tarmar battle's pause stands in, one option marked."""

    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm()
        battle = create_battle(
            self.game_master,
            name="Refused option",
            count=1,
            seed=3,
            character_ids=(
                create_character(self.create_user("player"), name="Aldwin").pk,
            ),
            control_mode=control.MANUAL,
        )
        battle, _events = advance_turn(battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        pending = battle.pending_input_json
        self.refused = pending["candidates"][0]
        self.refused[turn_menu.REFUSED] = True
        Battle.objects.filter(pk=battle.pk).update(pending_input_json=pending)
        self.battle = Battle.objects.get(pk=battle.pk)
        self.login_as(self.game_master)

    def act(self, choice: dict):
        pending = self.battle.pending_input_json
        return self.client.post(
            reverse("battle:act", args=[self.battle.pk]),
            json.dumps(
                {"combatant_id": pending["combatant_id"], "turn": pending["turn"]}
                | choice
            ),
            content_type="application/json",
        )

    def assert_refused(self, response) -> None:
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(
            response.json()["error"],
            "services.option_cannot_be_carried_out " + self.refused["name"],
        )
        after = Battle.objects.get(pk=self.battle.pk)
        self.assertEqual(after.turn_inputs_json, {})
        self.assertEqual(after.status, Battle.STATUS_AWAITING)
        self.assertEqual(after.turn_number, self.battle.turn_number)

    def test_choosing_it_is_refused(self):
        self.assert_refused(
            self.act(
                {
                    "letter": self.refused["letter"],
                    "target_id": self.refused.get("target_id"),
                    "spell_key": self.refused.get("spell_key") or "",
                }
            )
        )

    def test_handing_it_to_the_ai_is_refused(self):
        pending = self.battle.pending_input_json
        for candidate in pending["candidates"]:
            candidate.pop("ai_choice", None)
        self.refused["ai_choice"] = True
        pending["candidates"][0] = self.refused
        Battle.objects.filter(pk=self.battle.pk).update(pending_input_json=pending)
        self.battle = Battle.objects.get(pk=self.battle.pk)
        self.assert_refused(self.act({"ai": True}))

    def test_another_option_is_still_taken(self):
        other = self.battle.pending_input_json["candidates"][1]
        response = self.act(
            {
                "letter": other["letter"],
                "target_id": other.get("target_id"),
                "spell_key": other.get("spell_key") or "",
            }
        )
        self.assertEqual(response.status_code, 200, response.content)
