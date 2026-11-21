"""A True mage's cast stage, turn menu and roster card (tarmar-studio #208).

A figure with a level in the True skill may True cast and craft workings
(tarmar-engine #77), so it has a cast stage whatever spells it knows. Its
recipe rows are built as any caster's are. Its True cast rows and craft rows
are read off the pending menu's options and restate none of the engine's
filters: a casting has a row because the menu offers it, and is castable or
borrowable because the menu holds it so. The stage adds what the craft
control prints, each the engine's count: a working's tier, its turns, its
mana and its drain; the craft that stands, as one continuation; and the
workings a Game Master granted this figure, and no other's.

Everything runs under the default canon's rules (its workings Sear, Knit and
Figment) and :data:`~.rulings_words.FIXED_WORDS`: each sentence is its key
followed by what filled it, in the order of the names it may use.
"""

import json
from dataclasses import replace

from django.urls import reverse
from tarmar_engine.actions import CRAFT_OPTION
from tarmar_engine.policy import Candidate
from tarmar_engine.state import CraftState

from accounts.models import User
from battle import magic_bounds, profiles, turn_menu, working_grants
from battle.engine.state import BattleState
from battle.models import Battle
from battle.persistence import TARMAR_PROFILE
from battle.services import caster_magic
from battle.viewmodel import tarmar_view
from common.test_helpers import BaseTestCase

from . import true_magic_play as play
from .magic_fixtures import DEFAULT_MAGIC_RULES, seated_rules
from .rulings_words import with_fixed_words
from .test_working_grants import FIRST_AT, DefaultCanonTestCase, record_granting

#: Sear's ninth tier and Figment's fifth, both past the tier cap of 4 that two
#: levels in the True skill give the mage.
SEAR_NINE = play.TRUE_MAGIC.working_key("sear", 9)
FIGMENT_FIVE = play.TRUE_MAGIC.working_key("figment", 5)

#: A second True mage, on the mage's side.
OTHER_MAGE = 3


def menu_and_stage(
    state: BattleState, combatant_id: int = play.MAGE, *, battle: Battle | None = None
) -> tuple[list[dict], dict | None]:
    """A figure's pending menu, as a battle stores it, and its cast stage.

    The battle's rulings are written onto the board first, as the turn does
    before it builds the menu, and the stage is the profile's, as the page's
    payload asks for it.

    Args:
        state: The board.
        combatant_id: The figure choosing.
        battle: The battle whose rulings apply; one with none when left out.
    """
    battle = battle if battle is not None else Battle()
    magic_bounds.fill(state, magic_bounds.LiftedBounds.of(battle))
    working_grants.fill(state, working_grants.WorkingGrants.of(battle))
    menu = [
        turn_menu.menu_payload(option)
        for option in turn_menu.player_menu(
            TARMAR_PROFILE, state, state.by_id(combatant_id)
        )
    ]
    return menu, profiles.TarmarProfile(battle).caster_magic(state, combatant_id, menu)


def castings(menu: list[dict], *, craft: bool) -> list[dict]:
    """The menu's craft options, or its True casts, with nothing pushed."""
    return [
        option
        for option in menu
        if not option.get("push_mana")
        and (
            option["letter"] == CRAFT_OPTION if craft else bool(option.get("true_cast"))
        )
    ]


def offered_from_the_pool(options: list[dict]) -> list[tuple[str, object]]:
    """The spell and target of each option paid from the pool, in order."""
    return [
        (option["spell_key"], option["target_id"])
        for option in options
        if not option.get("borrow")
    ]


@with_fixed_words
class WhoHasAStageTest(DefaultCanonTestCase):
    def test_a_true_mage_with_no_recipe_spell_has_a_stage(self) -> None:
        state = play.board(mage={"spells": [], "spell_skill_levels": {}})
        menu, magic = menu_and_stage(state)
        assert magic is not None
        self.assertEqual(magic["spells"], [])
        # Knowing no spell it has nothing to True cast, and may still craft.
        self.assertEqual(magic["true_casts"], [])
        self.assertTrue(magic["craft"]["workings"])
        self.assertEqual((magic["pool"], magic["max_pool"]), (10, 10))
        # With no menu to read there is still a stage, with nothing on it.
        bare = caster_magic(state, play.MAGE)
        assert bare is not None
        self.assertEqual(
            (bare["spells"], bare["true_casts"], bare["craft"]["workings"]),
            ([], [], []),
        )

    def test_a_figure_with_no_spell_and_no_level_has_none(self) -> None:
        state = play.board(mage={"spells": [], "skill_levels": {}})
        self.assertIsNone(caster_magic(state, play.MAGE))
        self.assertIsNone(caster_magic(state, play.FOE))

    def test_a_canon_with_no_true_magic_gives_the_skill_no_stage(self) -> None:
        state = play.board(mage={"spells": [], "spell_skill_levels": {}})
        with seated_rules(replace(DEFAULT_MAGIC_RULES, true_magic=None)):
            self.assertIsNone(caster_magic(state, play.MAGE))

    def test_a_caster_with_no_level_has_no_true_casts_and_no_craft(self) -> None:
        state = play.board(mage={"skill_levels": {}})
        menu, magic = menu_and_stage(state)
        assert magic is not None
        self.assertEqual([row["key"] for row in magic["spells"]], ["shield"])
        self.assertEqual((magic["true_casts"], magic["craft"]), ([], None))
        self.assertEqual(castings(menu, craft=True) + castings(menu, craft=False), [])


@with_fixed_words
class TrueCastRowsTest(DefaultCanonTestCase):
    def test_the_rows_are_the_menus_true_casts(self) -> None:
        menu, magic = menu_and_stage(play.board(mage=play.MISSILE_MAGE))
        assert magic is not None
        offered = offered_from_the_pool(castings(menu, craft=False))
        # Fire Missile, and the same rung of each other element's ladder.
        self.assertEqual(len(offered), 5)
        self.assertEqual(
            [(row["key"], row["target_id"]) for row in magic["true_casts"]], offered
        )
        for row in magic["true_casts"]:
            with self.subTest(row["key"]):
                self.assertIs(row["true_cast"], True)
                self.assertEqual(
                    (row["castable"], row["borrowable"], row["reason"]),
                    (True, False, ""),
                )

    def test_a_true_cast_row_reads_as_the_recipe_row_but_for_its_channel(self) -> None:
        _menu, magic = menu_and_stage(play.board(mage=play.MISSILE_MAGE))
        assert magic is not None
        (recipe,) = magic["spells"]
        true_cast = magic["true_casts"][0]
        self.assertEqual(true_cast["key"], "fire_missile")
        for field in ("name", "cost", "note", "target_id"):
            with self.subTest(field):
                self.assertEqual(true_cast[field], recipe[field])
        self.assertNotIn("true_cast", recipe)
        # The recipe cast's Channel is 2 x its level + 6; the True cast's is
        # 3 x the two levels of the True skill.
        self.assertEqual((recipe["channel"], true_cast["channel"]), (8, 6))

    def test_a_true_cast_only_borrowing_pays_for_is_borrowable(self) -> None:
        menu, magic = menu_and_stage(play.board(mage={**play.MISSILE_MAGE, "mana": 0}))
        assert magic is not None
        self.assertEqual(offered_from_the_pool(castings(menu, craft=False)), [])
        row = magic["true_casts"][0]
        self.assertEqual(
            (row["castable"], row["borrowable"], row["shortfall"]), (False, True, 1)
        )
        # level, mana, shortfall.
        self.assertEqual(row["reason"], "cast_stage.mana_borrowable 1 0 1")

    def test_the_rows_restate_no_filter_of_the_engines(self) -> None:
        state = play.board(mage=play.MISSILE_MAGE)
        # With no menu there is no row, whatever the mage could cast.
        bare = caster_magic(state, play.MAGE)
        assert bare is not None
        self.assertEqual(bare["true_casts"], [])
        # And a True cast only a menu could offer has its row: Heal is no
        # spell this mage may True cast, and the stage does not ask.
        only_on_this_menu = Candidate(
            "h", "CAST", 0.0, "", spell_key="heal", true_cast=True
        ).to_payload()
        told = caster_magic(state, play.MAGE, [only_on_this_menu])
        assert told is not None
        self.assertEqual(
            [(row["key"], row["castable"]) for row in told["true_casts"]],
            [("heal", True)],
        )


@with_fixed_words
class CraftRowsTest(DefaultCanonTestCase):
    def test_the_rows_are_the_menus_crafts(self) -> None:
        menu, magic = menu_and_stage(play.board(friend=True))
        assert magic is not None
        offered = offered_from_the_pool(castings(menu, craft=True))
        # Sear at the foe and Knit at the mage and its friend, each at the
        # four tiers two levels allow; Figment is nobody's without a grant.
        self.assertEqual(len(offered), 12)
        self.assertEqual(
            [(row["key"], row["target_id"]) for row in magic["craft"]["workings"]],
            offered,
        )
        self.assertEqual(
            {row["effect"] for row in magic["craft"]["workings"]}, {"sear", "knit"}
        )
        self.assertEqual(
            (magic["craft"]["skill"], magic["craft"]["tier_cap"]), ("Arcane", 4)
        )
        self.assertIsNone(magic["craft"]["standing"])

    def test_a_row_carries_what_the_craft_control_prints(self) -> None:
        _menu, magic = menu_and_stage(play.board())
        assert magic is not None
        row = next(
            row for row in magic["craft"]["workings"] if row["key"] == play.SEAR_TWO
        )
        self.assertEqual(
            row,
            {
                "key": play.SEAR_TWO,
                "target_id": play.FOE,
                "name": "Sear (tier 2)",
                # level, school.
                "cost": "cast_stage.mana_cost 2 Arcane/True Magic",
                "castable": True,
                "reason": "",
                "borrowable": False,
                "shortfall": 0,
                "channel": 6,
                "effect": "sear",
                "effect_label": "Sear",
                "tier": 2,
                # Three turns a tier, and eight Fatigue a mana of the mage's
                # 40: cost, drain, left, pool.
                "turns": 6,
                "mana": 2,
                "drain": 16,
                "drain_pool": "fatigue",
                "note": "true_magic.drains true_magic.stage_craft 6 16 24 fatigue",
            },
        )

    def test_a_working_named_at_several_figures_has_a_row_for_each(self) -> None:
        _menu, magic = menu_and_stage(play.board(friend=True))
        assert magic is not None
        self.assertEqual(
            [
                (row["target_id"], row["name"])
                for row in magic["craft"]["workings"]
                if row["key"] == play.KNIT
            ],
            [
                (play.MAGE, "cast_stage.row_at Knit (tier 1) Mage"),
                (play.FRIEND, "cast_stage.row_at Knit (tier 1) Friend"),
            ],
        )

    def test_a_working_only_borrowing_pays_for_is_borrowable(self) -> None:
        # Two mana in the pool: the first two tiers are paid from it, and the
        # menu offers the third only by borrowing the one it lacks.
        menu, magic = menu_and_stage(play.board(mage={"mana": 2}))
        assert magic is not None
        sear = {
            row["tier"]: row
            for row in magic["craft"]["workings"]
            if row["effect"] == "sear"
        }
        self.assertEqual(
            [(tier, row["castable"], row["borrowable"]) for tier, row in sear.items()],
            [
                (1, True, False),
                (2, True, False),
                (3, False, True),
                (4, False, True),
            ],
        )
        borrowing = {
            option["spell_key"]: option["borrowed"]
            for option in castings(menu, craft=True)
            if option.get("borrow") and option["target_id"] == play.FOE
        }
        self.assertEqual(
            {
                row["key"]: row["shortfall"]
                for row in sear.values()
                if row["borrowable"]
            },
            borrowing,
        )
        self.assertEqual(sear[3]["shortfall"], 1)
        self.assertEqual(sear[3]["reason"], "cast_stage.mana_borrowable 3 2 1")

    def test_a_working_the_menu_holds_paid_from_the_pool_is_not_borrowable(
        self,
    ) -> None:
        # Read off the menu alone: were one to hold a craft both paid from
        # the pool and by borrowing, its row is the craft the pool pays for.
        from_the_pool = Candidate(
            CRAFT_OPTION, "CRAFT", 0.0, "", target_id=play.FOE, spell_key=play.SEAR
        )
        told = caster_magic(
            play.board(),
            play.MAGE,
            [
                from_the_pool.to_payload(),
                {**replace(from_the_pool, borrow=True).to_payload(), "borrowed": 1},
            ],
        )
        assert told is not None
        (row,) = told["craft"]["workings"]
        self.assertEqual(
            (row["castable"], row["borrowable"], row["shortfall"], row["reason"]),
            (True, False, 0, ""),
        )

    def test_the_rows_restate_no_filter_of_the_engines(self) -> None:
        state = play.board()
        bare = caster_magic(state, play.MAGE)
        assert bare is not None
        self.assertEqual(bare["craft"]["workings"], [])
        # A tier past the cap has its row when a menu holds it: the stage
        # does not ask the cap again.
        past_the_cap = Candidate(
            CRAFT_OPTION, "CRAFT", 0.0, "", spell_key=SEAR_NINE
        ).to_payload()
        told = caster_magic(state, play.MAGE, [past_the_cap])
        assert told is not None
        self.assertEqual(
            [(row["tier"], row["castable"]) for row in told["craft"]["workings"]],
            [(9, True)],
        )


@with_fixed_words
class GrantedWorkingsTest(DefaultCanonTestCase):
    """A granted working is on the stage of the figure it was granted to."""

    def board(self) -> BattleState:
        state = play.board()
        state.combatants.append(play.true_mage(OTHER_MAGE, name="Other", q=-2, r=0))
        return state

    def granted(self, *working_keys: str, lifted: bool = False) -> Battle:
        """The battle whose Game Master granted the mage these workings, and
        with ``lifted`` lifted Channel for it."""
        liftings = magic_bounds.LiftedBounds()
        if lifted:
            liftings = liftings.lifted(
                play.MAGE, [magic_bounds.CHANNEL], by="gm", by_id=1, at=FIRST_AT
            )
        return Battle(
            workings_granted_json=record_granting(play.MAGE, *working_keys).to_json(),
            magic_bounds_lifted_json=liftings.to_json(),
        )

    def figment_tiers(self, magic: dict | None) -> list[int]:
        assert magic is not None
        return sorted(
            {
                row["tier"]
                for row in magic["craft"]["workings"]
                if row["effect"] == "figment"
            }
        )

    def test_only_its_figure_is_offered_it_and_told_of_it(self) -> None:
        battle = self.granted(play.FIGMENT)
        _menu, magic = menu_and_stage(self.board(), battle=battle)
        assert magic is not None
        self.assertEqual(
            magic["craft"]["granted"],
            [
                {
                    "key": play.FIGMENT,
                    "name": "Figment (tier 1)",
                    "waits_on_channel": False,
                    "note": "",
                }
            ],
        )
        self.assertEqual(self.figment_tiers(magic), [1])
        _menu, others = menu_and_stage(self.board(), OTHER_MAGE, battle=battle)
        assert others is not None
        self.assertEqual(others["craft"]["granted"], [])
        self.assertEqual(self.figment_tiers(others), [])
        # The other mage crafts what needs no grant, as the mage does.
        self.assertTrue(others["craft"]["workings"])

    def test_a_grant_past_the_tier_cap_says_it_waits_on_channel(self) -> None:
        _menu, magic = menu_and_stage(
            self.board(), battle=self.granted(play.FIGMENT, FIGMENT_FIVE)
        )
        assert magic is not None
        self.assertEqual(
            [
                (grant["name"], grant["waits_on_channel"], grant["note"])
                for grant in magic["craft"]["granted"]
            ],
            [
                ("Figment (tier 1)", False, ""),
                # name, working.
                (
                    "Figment (tier 5)",
                    True,
                    "true_magic.granted_waits Mage Figment (tier 5)",
                ),
            ],
        )
        # The menu offers no craft of the one that waits.
        self.assertEqual(self.figment_tiers(magic), [1])

    def test_with_channel_lifted_the_grant_waits_no_longer(self) -> None:
        _menu, magic = menu_and_stage(
            self.board(), battle=self.granted(FIGMENT_FIVE, lifted=True)
        )
        assert magic is not None
        self.assertEqual(
            [
                (grant["name"], grant["waits_on_channel"], grant["note"])
                for grant in magic["craft"]["granted"]
            ],
            [("Figment (tier 5)", False, "")],
        )
        self.assertEqual(self.figment_tiers(magic), [5])
        self.assertIsNone(magic["craft"]["tier_cap"])

    def test_a_grant_the_canon_no_longer_builds_is_left_out(self) -> None:
        # Asked of a board that still lists the key, as one written under an
        # earlier canon would: it is no working, and is not looked up.
        state = play.board(mage={"granted_workings": ["working:gone:1", play.FIGMENT]})
        magic = caster_magic(state, play.MAGE)
        assert magic is not None
        self.assertEqual(
            [grant["key"] for grant in magic["craft"]["granted"]], [play.FIGMENT]
        )


@with_fixed_words
class ContinuationTest(DefaultCanonTestCase):
    """While a craft stands the stage has one continuation and no new craft."""

    def test_the_stage_names_the_working_and_its_turn(self) -> None:
        menu, magic = menu_and_stage(play.crafting_sear())
        assert magic is not None
        self.assertEqual(
            magic["craft"]["standing"],
            {
                "key": play.SEAR,
                "name": "Sear (tier 1)",
                "turn": 2,
                "turns": 3,
                "gathered": 1,
                "mana": 3,
                "push_mana": 2,
                "borrow": False,
                # The figure the craft's latest action named.
                "target_id": play.FOE,
                # turn, turns, working.
                "line": "true_magic.craft_continue 2 3 Sear (tier 1)",
                "offered": True,
            },
        )
        self.assertEqual(magic["craft"]["workings"], [])
        # The menu's crafts are that craft's continuation and nothing else.
        self.assertEqual(
            {
                (option["spell_key"], option.get("push_mana"))
                for option in menu
                if option["letter"] == CRAFT_OPTION
            },
            {(play.SEAR, 2)},
        )

    def test_a_continuation_the_menu_withholds_is_not_offered(self) -> None:
        # In hand-to-hand with a sword in hand no cast may be made.
        state = play.crafting_sear(hth_with=[play.FOE])
        state.by_id(play.FOE).q, state.by_id(play.FOE).r = 1, 0
        state.by_id(play.FOE).hth_with = [play.MAGE]
        menu, magic = menu_and_stage(state)
        assert magic is not None
        self.assertEqual(
            [option for option in menu if option["letter"] == CRAFT_OPTION], []
        )
        self.assertIs(magic["craft"]["standing"]["offered"], False)
        self.assertEqual(magic["craft"]["standing"]["turn"], 2)


@with_fixed_words
class RosterCardTest(DefaultCanonTestCase):
    """The roster card shows a craft that stands, as it shows a Runaway."""

    def card(self, state: BattleState) -> dict | None:
        return tarmar_view(state, next_sequence=1)["combatants"][0]["crafting"]

    def test_a_figure_with_no_craft_has_no_line(self) -> None:
        self.assertIsNone(self.card(play.board()))

    def test_a_craft_names_its_working_its_turns_and_its_figure(self) -> None:
        state = play.board(
            mage={"crafting": CraftState(play.SEAR_TWO, 0, False, 4, 0, play.FOE)}
        )
        self.assertEqual(
            self.card(state),
            {
                "spell": "Sear (tier 2)",
                "spell_key": play.SEAR_TWO,
                "turns_done": 4,
                "turns": 6,
                "target": "Foe",
                # done, target, turns, working.
                "line": "true_magic.card_naming 4 Foe 6 Sear (tier 2)",
            },
        )

    def test_a_craft_that_names_nobody_says_so_of_nobody(self) -> None:
        for label, target in (("no figure named", None), ("a figure gone", 99)):
            with self.subTest(label):
                state = play.board(
                    mage={"crafting": CraftState(play.SEAR, 0, False, 1, 0, target)}
                )
                card = self.card(state)
                assert card is not None
                self.assertIsNone(card["target"])
                # done, turns, working.
                self.assertEqual(card["line"], "true_magic.card 1 3 Sear (tier 1)")


@with_fixed_words
class PendingPayloadCarriesTheMenusCastingsTest(BaseTestCase):
    """The page's payload hands the stage the pending menu, so the awaited
    True mage's stage holds what that menu offers."""

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(seated_rules(DEFAULT_MAGIC_RULES))
        self.dm = self.create_dm()
        self.player = self.create_player()
        self.battle, self.seat = play.awaiting_true_mage(self.dm, self.player)

    def pending_for(self, user: User) -> dict:
        self.login_as(user)
        response = self.client.get(reverse("battle:state", args=[self.battle.pk]))
        self.assertEqual(response.status_code, 200)
        return json.loads(response.content)["pending"]

    def test_the_stage_is_read_off_the_menu_the_page_holds(self) -> None:
        for label, user in (("the player", self.player), ("the Game Master", self.dm)):
            with self.subTest(label):
                pending = self.pending_for(user)
                menu, magic = pending["candidates"], pending["magic"]
                self.assertEqual(
                    [
                        (row["key"], row["target_id"])
                        for row in magic["craft"]["workings"]
                    ],
                    offered_from_the_pool(castings(menu, craft=True)),
                )
                self.assertEqual(
                    [(row["key"], row["target_id"]) for row in magic["true_casts"]],
                    offered_from_the_pool(castings(menu, craft=False)),
                )
                self.assertTrue(magic["craft"]["workings"])
                self.assertTrue(magic["true_casts"])

    def test_a_spectator_is_told_nothing_of_the_mages_castings(self) -> None:
        pending = self.pending_for(self.create_user(username="watcher"))
        self.assertEqual((pending["candidates"], pending["magic"]), ([], None))
