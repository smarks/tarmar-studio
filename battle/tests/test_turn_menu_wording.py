"""The turn menu's words are configuration, and moving them changed no line (#90).

Every sentence and fragment the turn menu builds (``battle.turn_menu``, both
profiles) lives in ``config/battle-rulings.toml``'s ``[turn_menu]``,
``[tarmar_menu]`` and ``[classic_menu]`` tables. The menus below were rendered
on these boards by the code before #90, which wrote the words inline, and
captured in ``turn-menu-lines.json``; the words that code wrote are frozen in
``turn-menu-words.toml``, as the shipped tables held them when they were
moved. Rendered under those frozen words, every line must come out byte for
byte as captured: the proof that moving the words changed nothing.

The shipped file is Spencer's to reword, so this test reads the frozen copy,
never the shipped tables, and goes on passing when he does. The boards seat
invented magic rules and real catalog weapons, so the lines are the same
under either canon. If a change to the menu's behaviour changes a line on
purpose, recapture with :func:`write_capture` and say why in the commit.
"""

import json
import re
import string
import tempfile
import tomllib
from dataclasses import replace
from pathlib import Path
from typing import get_type_hints

from django.conf import settings
from django.test import override_settings
from hexarena.hex import Hex
from tarmar_engine import movement
from tarmar_engine.classic import policy as classic_policy
from tarmar_engine.classic.arena import Arena
from tarmar_engine.classic.data import (
    BROADSWORD,
    CHAINMAIL,
    DAGGER,
    HALBERD,
    LEATHER,
    SMALL_BOW,
)
from tarmar_engine.classic.figure import Posture, create_human, create_wizard
from tarmar_engine.classic.monsters import create_monster
from tarmar_engine.classic.options import Option as ClassicOption
from tarmar_engine.classic.state import GameState

from battle import turn_menu
from battle.engine.state import (
    BattleState,
    CombatantState,
    RunawayState,
    WeaponState,
)
from battle.magic_bounds import LIFTABLE_BOUNDS
from battle.persistence import CLASSIC_PROFILE, TARMAR_PROFILE
from battle.policy import Candidate, choose_option
from battle.services import _catalog_weapon
from battle.tests.test_persistence import classic_game
from battle.wording import RulingsWording, load_rulings_wording
from common.test_helpers import BaseTestCase

from .magic_fixtures import (
    CASTING_FAILURE_RULES,
    COUNTER_RULES,
    INVENTED_RULES,
    RUNAWAY_RULES,
    seated_rules,
)
from .rulings_words import with_fixed_words
from .test_turn_menu import (
    boxed_in_board,
    figure,
    open_board,
    plain_line_board,
    walled_off_board,
)
from .test_turn_menu_classic import archer_board, armed, facing_toward

#: The tables of the rulings' words the turn menu reads.
MENU_TABLES = ("turn_menu", "tarmar_menu", "classic_menu")

#: A sentence's key under :data:`FIXED_WORDS`, "tarmar_menu.dodge".
FIXED_KEY = re.compile(rf"(?:{'|'.join(MENU_TABLES)})\.\w+")

#: A distance as the engine words it (``combat_math.hexes_text``), which
#: fills ``ends_from``'s {distance}: the engine's words, not the menu's.
ENGINE_DISTANCE = re.compile(r"\b\d+ hex(?:es)?\b")

#: The punctuation the menu's join sentences put between the sentences they
#: carry; no name, number or dice filling a sentence holds any of it.
JOIN_PUNCTUATION = frozenset(",;:")

#: The capture and the frozen words, beside this module.
CAPTURE = Path(__file__).with_name("turn-menu-lines.json")
FROZEN_WORDS = Path(__file__).with_name("turn-menu-words.toml")

#: Every Tarmar option key the menu words, each described on the every-letter
#: boards whether or not the engine would offer it there.
TARMAR_LETTERS = (
    "a",
    "a_yield",
    "sprint",
    "sprint_yield",
    "b",
    "b_yield",
    "c",
    "c_yield",
    "d",
    "e",
    "f",
    "g",
    "p",
    "j",
    "k",
    "l",
    "m",
    "n",
    "o",
    "q",
    "t",
    "u",
    "v",
    "hold_still",
    "squeeze",
    "maintain",
    "release",
    "zz",
)

#: A line of a rendered menu: the option, its target, and what it reads.
Line = dict[str, object]


def _lines(menu: list) -> list[Line]:
    """The words of each option on a described menu."""
    return [
        {
            "letter": entry.letter,
            "target_id": entry.target_id,
            "plain": entry.plain,
            "cost": entry.cost,
        }
        for entry in menu
    ]


def _tarmar_menu(state: BattleState, actor: CombatantState) -> list[Line]:
    advice = choose_option(state, actor)
    return _lines(turn_menu.player_menu(TARMAR_PROFILE, state, actor, advice))


def _classic_menu(game: GameState, chooser) -> list[Line]:
    advice = classic_policy.choose_option(game, chooser)
    return _lines(turn_menu.player_menu(CLASSIC_PROFILE, game, chooser, advice))


def _described(
    state: BattleState, actor: CombatantState, candidates: list[Candidate]
) -> list[Line]:
    return _lines(
        [
            turn_menu.describe(state, actor, candidate, ai_choice=False)
            for candidate in candidates
        ]
    )


def _every_letter(state: BattleState, actor: CombatantState) -> list[Line]:
    """Each Tarmar key described at figure 2 and at nobody."""
    return _described(
        state,
        actor,
        [
            Candidate(letter, letter.upper(), 0.0, "", target_id=target_id)
            for letter in TARMAR_LETTERS
            for target_id in (2, None)
        ],
    )


def _tarmar_letter_boards() -> dict[str, list[Line]]:
    boards: dict[str, list[Line]] = {}
    state, actor, foe = plain_line_board()
    boards["tarmar every letter beside a foe"] = _every_letter(state, actor)

    state, actor, foe = plain_line_board()
    actor.weapon = _catalog_weapon("dagger")
    boards["tarmar every letter with a dagger in hand"] = _every_letter(state, actor)

    state, actor, foe = plain_line_board()
    actor.weapon = WeaponState(damage="1d6-2")
    actor.spare_weapons = []
    state.ground_weapons = []
    boards["tarmar every letter bare-handed"] = _every_letter(state, actor)

    state, actor, foe = plain_line_board()
    actor.hth_with = [foe.combatant_id]
    foe.hth_with = [actor.combatant_id]
    boards["tarmar every letter in hand-to-hand"] = _every_letter(state, actor)

    state, actor, foe = plain_line_board()
    actor.grappled_by = 2
    boards["tarmar every letter held"] = _every_letter(state, actor)

    state, actor, foe = plain_line_board()
    while (cell := movement.step_away_hex(state, actor, foe)) is not None:
        blocker = figure(10 + len(state.combatants), "Shield", cell)
        blocker.team = actor.team = "Red"
        state.combatants.append(blocker)
    boards["tarmar every letter with the step away blocked"] = _every_letter(
        state, actor
    )
    actor.hth_with = [foe.combatant_id]
    foe.hth_with = [actor.combatant_id]
    boards["tarmar every letter blocked in hand-to-hand"] = _every_letter(state, actor)

    thrower = figure(1, "Harar", (0, 0), weapon=_catalog_weapon("dagger"))
    state = BattleState(arena_radius=6, combatants=[thrower, figure(2, "Foe", (3, 0))])
    boards["tarmar every letter with a dagger to throw"] = _every_letter(state, thrower)
    return boards


def _tarmar_menu_boards() -> dict[str, list[Line]]:
    fighter = figure(1, "Aldwin", (0, 0))
    archer = figure(1, "Dorra", (0, 0), weapon=_catalog_weapon("longbow"))
    brawler = figure(
        1, "Harar", (0, 0), weapon=_catalog_weapon("dagger"), movement_modifier=2
    )
    boards = {
        "tarmar two wolves": (
            BattleState(
                arena_radius=4,
                combatants=[
                    fighter,
                    figure(2, "Gray Wolf", (1, 0), stops=1, armour_tier="Light"),
                    figure(3, "Dire Wolf", (-1, 0)),
                ],
            ),
            fighter,
        ),
        "tarmar runner and a far foe": (
            BattleState(
                arena_radius=6,
                combatants=[
                    figure(1, "Aldwin", (0, 0), move_run=10, move_sprint=14),
                    figure(2, "Far", (5, 0)),
                ],
            ),
            None,
        ),
        "tarmar archer near and far": (
            BattleState(
                arena_radius=6,
                combatants=[
                    archer,
                    figure(2, "Near", (0, 3), stops=6, armour_tier="Heavy"),
                    figure(3, "Far", (5, 0)),
                ],
            ),
            archer,
        ),
        "tarmar archer cornered": (
            BattleState(arena_radius=4, combatants=[archer, figure(2, "Foe", (1, 0))]),
            archer,
        ),
        "tarmar swordsman apart": (
            BattleState(arena_radius=6, combatants=[fighter, figure(2, "Foe", (3, 0))]),
            fighter,
        ),
        "tarmar close strike": (
            BattleState(
                arena_radius=4,
                combatants=[brawler, figure(2, "Foe", (1, 0), movement_modifier=0)],
            ),
            brawler,
        ),
        "tarmar boxed in": boxed_in_board(),
        "tarmar in the open": open_board(),
        "tarmar charge walled off with a far foe": walled_off_board(with_far=True),
        "tarmar charge walled off alone": walled_off_board(with_far=False),
    }
    rendered: dict[str, list[Line]] = {}
    for name, (state, actor) in boards.items():
        chooser = actor if actor is not None else state.combatants[0]
        rendered[name] = _tarmar_menu(state, chooser)
    return rendered


def _wizard(mana: int = 8, **fields) -> CombatantState:
    return figure(
        1,
        "Morwen",
        (0, 0),
        spells=["fire_missile"],
        mana=mana,
        max_mana=8,
        intelligence=14,
        **fields,
    )


def _magic_boards() -> dict[str, list[Line]]:
    rendered: dict[str, list[Line]] = {}
    for rules_name, rules in (
        ("invented", INVENTED_RULES),
        ("casting failure", CASTING_FAILURE_RULES),
    ):
        with seated_rules(rules):
            wizard = _wizard()
            state = BattleState(
                arena_radius=6, combatants=[wizard, figure(2, "Foe", (3, 0))]
            )
            rendered[f"tarmar wizard menu, {rules_name} rules"] = _tarmar_menu(
                state, wizard
            )
            cast = Candidate(
                "h", "CAST", 0.0, "", target_id=2, spell_key="fire_missile"
            )
            poor = _wizard(mana=0)
            poor_state = BattleState(
                arena_radius=6, combatants=[poor, figure(2, "Foe", (3, 0))]
            )
            rendered[f"tarmar casts, {rules_name} rules"] = _described(
                state,
                wizard,
                [
                    cast,
                    replace(cast, target_id=None),
                    replace(cast, push_mana=2),
                    replace(cast, escalate=True),
                    replace(cast, escalate=True, push_mana=1),
                    replace(cast, letter="r"),
                ],
            ) + _described(
                poor_state,
                poor,
                [replace(cast, borrow=True), replace(cast, borrow=True, push_mana=1)],
            )
    for rules_name, rules in (
        ("no runaway rules", INVENTED_RULES),
        ("runaway rules", RUNAWAY_RULES),
    ):
        with seated_rules(rules):
            wizard = _wizard(runaway=RunawayState("fire_missile", 3))
            friend = figure(3, "Bram", (0, 1), runaway=RunawayState("fire_missile", 3))
            state = BattleState(
                arena_radius=6,
                combatants=[wizard, figure(2, "Foe", (3, 0)), friend],
            )
            anchor = Candidate(turn_menu.ANCHOR_OPTION, "ANCHOR", 0.0, "")
            rendered[f"tarmar anchors, {rules_name}"] = _described(
                state,
                wizard,
                [
                    replace(anchor, target_id=1, anchor_target=1),
                    replace(anchor, target_id=3, anchor_target=3),
                    anchor,
                ],
            )
    runaway = RUNAWAY_RULES.runaway
    assert runaway is not None
    with seated_rules(
        replace(
            RUNAWAY_RULES,
            runaway=replace(runaway, helper_failure_catches_helper=False),
        )
    ):
        wizard = _wizard()
        friend = figure(3, "Bram", (0, 1), runaway=RunawayState("fire_missile", 3))
        state = BattleState(arena_radius=6, combatants=[wizard, friend])
        rendered["tarmar helper anchor, a failure catches nobody"] = _described(
            state,
            wizard,
            [
                Candidate(
                    turn_menu.ANCHOR_OPTION,
                    "ANCHOR",
                    0.0,
                    "",
                    target_id=3,
                    anchor_target=3,
                )
            ],
        )
    with seated_rules(COUNTER_RULES):
        wizard = _wizard()
        mage = _wizard()
        mage.combatant_id, mage.name, mage.q, mage.r = 2, "Odo", 3, 0
        mage.contest = None
        state = BattleState(arena_radius=6, combatants=[wizard, mage])
        counter = Candidate("counter", "COUNTER", 0.0, "", counter_mana=2)
        rendered["tarmar counters"] = _described(
            state,
            wizard,
            [replace(counter, target_id=2), replace(counter, counter_mana=1), counter],
        )
    return rendered


#: The classic options whose line rehearses the engine's own plan for them,
#: which the engine refuses for an option not legal on the board.
_REHEARSED = frozenset(
    {
        ClassicOption.MOVE,
        ClassicOption.HALF_MOVE,
        ClassicOption.CHARGE_ATTACK,
        ClassicOption.DODGE,
        ClassicOption.READY_WEAPON,
        ClassicOption.CHANGE_WEAPONS,
        ClassicOption.PICK_UP,
        ClassicOption.CAST,
    }
)


def _every_classic_option(game: GameState, chooser) -> list[Line]:
    """Each classic option but a cast, described at each foe and at nobody:
    every one legal for ``chooser``, and every one that rehearses nothing."""
    legal = set(game.legal_options(chooser))
    foes = [figure.uid for figure in game.figures if figure.side != chooser.side]
    return _lines(
        [
            turn_menu.describe_classic(
                game,
                chooser,
                Candidate(option.value, option.value.upper(), 0.0, "", target_id=uid),
                ai_choice=False,
            )
            for option in ClassicOption
            if option != ClassicOption.CAST
            and (option in legal or option not in _REHEARSED)
            for uid in (*foes, None)
        ]
    )


def _classic_boards() -> dict[str, list[Line]]:
    rendered: dict[str, list[Line]] = {}
    game = classic_game()
    red, blue = game.figures
    rendered["classic two fighters"] = _classic_menu(game, red)
    rendered["classic every legal option engaged"] = _every_classic_option(game, red)
    game, red = archer_board()
    rendered["classic every legal option apart"] = _every_classic_option(game, red)
    game = classic_game()
    red, blue = game.figures
    red.posture = Posture.PRONE
    rendered["classic every legal option prone"] = _every_classic_option(game, red)
    game = classic_game()
    red, blue = game.figures
    red.weapons = [BROADSWORD, DAGGER]
    game.dropped.append((Hex(1, 2), DAGGER))
    rendered["classic every legal option with a spare"] = _every_classic_option(
        game, red
    )

    game = classic_game()
    red, blue = game.figures
    blue.defending = True
    rendered["classic a defending foe"] = _classic_menu(game, red)

    game = classic_game()
    red, blue = game.figures
    blue.position = Hex(2, 8)
    red.weapons = [SMALL_BOW, DAGGER]
    red.ready_weapon = SMALL_BOW
    rendered["classic an archer at range"] = _classic_menu(game, red)

    rendered["classic archer boxed in"] = _classic_menu(*archer_board(boxed=True))
    rendered["classic archer in the open"] = _classic_menu(*archer_board())
    rendered["classic archer too far"] = _classic_menu(*archer_board(red_at=Hex(4, 1)))

    def charge_board(figures, chooser) -> list[Line]:
        return _classic_menu(GameState(Arena(cols=9, rows=15), figures), chooser)

    sword = armed("Red", "red", Hex(4, 2), BROADSWORD)
    rendered["classic sword charge short"] = charge_board(
        [sword, armed("Blue", "blue", Hex(4, 9))], sword
    )
    sword = armed("Red", "red", Hex(4, 4), BROADSWORD)
    rendered["classic sword charge in reach"] = charge_board(
        [sword, armed("Blue", "blue", Hex(4, 9))], sword
    )
    halberd = armed("Red", "red", Hex(4, 2), HALBERD)
    rendered["classic halberd charge"] = charge_board(
        [halberd, armed("Blue", "blue", Hex(4, 9))], halberd
    )
    sword = armed("Red", "red", Hex(4, 2), BROADSWORD)
    giant = create_monster("Giant", "Giant", "blue")
    giant.position = Hex(4, 9)
    giant.facing = Arena(cols=9, rows=15).layout.direction_to(Hex(4, 9), Hex(4, 8))
    rendered["classic charge at a giant"] = charge_board([sword, giant], sword)
    sword = armed("Red", "red", Hex(4, 4), BROADSWORD)
    green = create_human("Green", 9, 15, "blue")
    green.position = Hex(4, 9)
    rendered["classic charge strikes another foe"] = charge_board(
        [sword, armed("Blue", "blue", Hex(4, 8)), green], sword
    )
    bare = create_human("Red", 12, 12, "red")
    bare.position = Hex(4, 4)
    armoured = armed("Blue", "blue", Hex(4, 8), BROADSWORD)
    armoured.armor = CHAINMAIL
    rendered["classic bare-handed charge grapples"] = charge_board(
        [bare, armoured], bare
    )
    bare = create_human("Red", 12, 12, "red")
    bare.position = Hex(4, 4)
    armoured = armed("Blue", "blue", Hex(4, 8), BROADSWORD)
    armoured.armor = CHAINMAIL
    green = create_human("Green", 9, 15, "blue")
    green.position = Hex(4, 9)
    rendered["classic bare-handed charge grapples another"] = charge_board(
        [bare, armoured, green], bare
    )

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
    wizard.facing = facing_toward(arena, wizard.position, foe.position)
    foe.facing = facing_toward(arena, foe.position, wizard.position)
    rendered["classic wizard"] = _classic_menu(GameState(arena, [wizard, foe]), wizard)
    return rendered


def _lifted_lines() -> list[Line]:
    """A cast and a counter past the first bound a Game Master may lift."""
    with seated_rules(COUNTER_RULES):
        wizard = _wizard()
        state = BattleState(
            arena_radius=6, combatants=[wizard, figure(2, "Foe", (3, 0))]
        )
        past = LIFTABLE_BOUNDS[:1]
        return _described(
            state,
            wizard,
            [
                Candidate(
                    "h",
                    "CAST",
                    0.0,
                    "",
                    target_id=2,
                    spell_key="fire_missile",
                    lifted_bounds=past,
                ),
                Candidate(
                    "counter",
                    "COUNTER",
                    0.0,
                    "",
                    target_id=2,
                    counter_mana=1,
                    lifted_bounds=past,
                ),
            ],
        )


def rendered_menus() -> dict[str, list[Line]]:
    """Every board's menu, as the loaded words render it."""
    return {
        **_tarmar_letter_boards(),
        **_tarmar_menu_boards(),
        **_magic_boards(),
        **_classic_boards(),
    }


def write_capture() -> None:
    """Capture :func:`rendered_menus` under the loaded words, for a deliberate
    change to what the menu says (see the module docstring)."""
    CAPTURE.write_text(
        json.dumps(rendered_menus(), indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def frozen_menu_words() -> dict[str, object]:
    """The menu's tables as :data:`FROZEN_WORDS` holds them, as dataclasses."""
    raw = tomllib.loads(FROZEN_WORDS.read_text(encoding="utf-8"))
    tables = get_type_hints(RulingsWording)
    return {name: tables[name](**raw[name]) for name in MENU_TABLES}


class MovingTheWordsChangedNoLineTest(BaseTestCase):
    """Every board's menu reads, under the frozen words, as captured (#90)."""

    def test_every_line_is_the_line_the_inline_words_wrote(self):
        with override_settings(
            BATTLE_RULINGS_WORDING=replace(
                settings.BATTLE_RULINGS_WORDING, **frozen_menu_words()
            )
        ):
            rendered = json.loads(json.dumps(rendered_menus()))
        captured = json.loads(CAPTURE.read_text(encoding="utf-8"))
        self.assertEqual(list(rendered), list(captured))
        for board, lines in captured.items():
            with self.subTest(board=board):
                self.assertEqual(rendered[board], lines)

    def test_the_frozen_words_are_the_menu_tables_and_load(self):
        """The frozen copy holds exactly the menu's tables, each checked as
        the shipped file is."""
        raw = tomllib.loads(FROZEN_WORDS.read_text(encoding="utf-8"))
        self.assertEqual(set(raw), set(MENU_TABLES))
        with tempfile.TemporaryDirectory() as scratch:
            shipped = tomllib.loads(
                Path(settings.BATTLE_RULINGS_WORDING_FILE).read_text(encoding="utf-8")
            )
            merged = Path(scratch) / "rulings.toml"
            merged.write_text(
                "\n".join(
                    f"[{name}]\n"
                    + "\n".join(
                        f"{key} = {json.dumps(text)}" for key, text in keys.items()
                    )
                    for name, keys in {**shipped, **raw}.items()
                ),
                encoding="utf-8",
            )
            load_rulings_wording(merged)


@with_fixed_words
class MenuWordsAreReadFromTheConfigurationTest(BaseTestCase):
    """The menu reads its words from the loaded configuration, never its own
    copy: under :data:`FIXED_WORDS` no shipped sentence survives (#90)."""

    def test_no_line_on_any_board_is_written_in_code(self):
        # A cast and a counter past a lifted bound join the bound's words to
        # their cost; they are not in the capture, whose words would then
        # carry the shipped magic_bounds table's too.
        menus = {**rendered_menus(), "tarmar past a lifted bound": _lifted_lines()}
        shipped = load_rulings_wording(settings.BATTLE_RULINGS_WORDING_FILE)
        # Every literal piece of every shipped sentence, around its {names}.
        fragments = {
            literal.strip()
            for name in MENU_TABLES
            for text in vars(getattr(shipped, name)).values()
            for literal, _field, _spec, _conversion in string.Formatter().parse(text)
        }
        for board, lines in menus.items():
            for line in lines:
                for field in ("plain", "cost"):
                    with self.subTest(board=board, letter=line["letter"], field=field):
                        words = str(line[field])
                        # The engine's own name for an option it does not
                        # word, and an empty cost, are not the menu's words.
                        if not words or words == str(line["letter"]).capitalize():
                            continue
                        self.assertRegex(words, rf"^{FIXED_KEY.pattern}")
                        # What fills the sentences: names, numbers, dice.
                        # The engine's own distance ("2 hexes") fills {distance}.
                        filling = ENGINE_DISTANCE.sub("", FIXED_KEY.sub("", words))
                        self.assertFalse(
                            [
                                fragment
                                for fragment in fragments
                                if len(fragment) > 3 and fragment in filling
                            ],
                            words,
                        )
                        # A join written in code ("{line}, {standing}") leaves
                        # its punctuation, which no filling carries.
                        self.assertFalse(JOIN_PUNCTUATION & set(filling), words)
