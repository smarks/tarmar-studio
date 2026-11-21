"""The turn menu's lines for a True cast and a craft (tarmar-studio #208).

The engine offers a True mage the True cast of a spell under the cast's own
letter, and the crafts it may begin or carry on under the craft letter
(tarmar-engine #77). :func:`battle.turn_menu.describe` gives each the line a
player reads and what it costs, in the words of
``config/battle-rulings.toml``'s ``[true_magic]`` table: a True cast is the
cast's own line marked as one, and says what it drains; a craft names its
working and the figure it names, the mana it gathers over its turns, and
what the working drains when it goes off; the continuation of a craft that
stands names the working and its "turn k of n", and the share this turn
gathers.

Everything runs under the default canon's rules (its workings Sear, Knit and
Figment) and :data:`~.rulings_words.FIXED_WORDS`: each sentence is its key
followed by what filled it, in the order of the names it may use.
"""

from dataclasses import replace

from tarmar_engine.actions import CRAFT_OPTION
from tarmar_engine.magic import CastingEnding
from tarmar_engine.policy import Candidate

from battle import turn_menu, workings
from battle.engine.state import BattleState
from battle.persistence import TARMAR_PROFILE

from . import true_magic_play as play
from .magic_fixtures import DEFAULT_MAGIC_RULES, seated_rules
from .rulings_words import with_fixed_words
from .test_working_grants import DefaultCanonTestCase


def cast(*, push_mana: int = 0, true_cast: bool = False) -> Candidate:
    """A cast of Fire Missile at the foe, as the engine's menu carries one."""
    return Candidate(
        "h",
        "CAST SPELL",
        0.0,
        "",
        target_id=play.FOE,
        spell_key="fire_missile",
        push_mana=push_mana,
        true_cast=true_cast,
    )


def craft(
    working: str,
    target: int | None = play.FOE,
    *,
    push_mana: int = 0,
    borrow: bool = False,
) -> Candidate:
    """A craft action, as the engine's menu carries one."""
    return Candidate(
        CRAFT_OPTION,
        "CRAFT WORKING",
        0.0,
        "",
        target_id=target,
        spell_key=working,
        push_mana=push_mana,
        borrow=borrow,
    )


def as_described(
    state: BattleState, candidate: Candidate
) -> turn_menu.DescribedCandidate:
    """One option of the mage's, as the turn menu describes it."""
    return turn_menu.describe(state, state.by_id(play.MAGE), candidate, ai_choice=False)


@with_fixed_words
class TrueCastLineTest(DefaultCanonTestCase):
    def test_a_true_cast_is_the_casts_line_marked_as_one(self) -> None:
        state = play.board(mage=play.MISSILE_MAGE)
        recipe = as_described(state, cast())
        true_cast = as_described(state, cast(true_cast=True))
        # aimed, spell.
        self.assertEqual(recipe.plain, "turn_menu.cast_at Foe Fire Missile")
        self.assertEqual(
            true_cast.plain,
            "true_magic.as_true_cast turn_menu.cast_at Foe Fire Missile",
        )
        # Nothing pushed, nothing drained: it costs what the cast costs.
        self.assertEqual(true_cast.cost, recipe.cost)
        self.assertTrue(recipe.cost.startswith("tarmar_menu.cast_cost 1 "))

    def test_a_pushed_true_cast_says_what_it_drains(self) -> None:
        state = play.board(mage=play.MISSILE_MAGE)
        recipe = as_described(state, cast(push_mana=2))
        true_cast = as_described(state, cast(push_mana=2, true_cast=True))
        self.assertEqual(
            true_cast.plain,
            "true_magic.as_true_cast tarmar_menu.pushing "
            "turn_menu.cast_at Foe Fire Missile 2",
        )
        # Eight Fatigue a mana pushed, of the mage's 40: cost, drain, left,
        # pool.
        self.assertEqual(
            true_cast.cost, f"true_magic.drains {recipe.cost} 16 24 fatigue"
        )
        self.assertNotIn("true_magic.", recipe.cost)


@with_fixed_words
class CraftLineTest(DefaultCanonTestCase):
    def test_a_craft_names_its_working_its_target_and_what_it_takes(self) -> None:
        described = as_described(play.board(), craft(play.SEAR_TWO))
        # craft_aimed is aimed then line; craft_begin the working.
        self.assertEqual(
            described.plain,
            "true_magic.craft_aimed Foe true_magic.craft_begin Sear (tier 2)",
        )
        # craft_cost is mana then turns; drains is cost, drain, left, pool.
        self.assertEqual(
            described.cost,
            "true_magic.drains true_magic.craft_cost 2 6 16 24 fatigue",
        )
        self.assertEqual(described.target_name, "Foe")

    def test_the_figure_is_named_by_what_the_working_does(self) -> None:
        state = play.board(friend=True)
        for label, candidate, plain in (
            (
                "a healing working restores its figure",
                craft(play.KNIT, play.FRIEND),
                "true_magic.craft_healing Friend true_magic.craft_begin Knit (tier 1)",
            ),
            (
                "a working the Game Master describes names its figure",
                craft(play.FIGMENT, play.FOE),
                "true_magic.craft_named Foe true_magic.craft_begin Figment (tier 1)",
            ),
            (
                "a working that names nobody says only what it is",
                craft(play.FIGMENT, None),
                "true_magic.craft_begin Figment (tier 1)",
            ),
        ):
            with self.subTest(label):
                self.assertEqual(as_described(state, candidate).plain, plain)

    def test_a_pushed_craft_says_what_it_pushes_and_drains(self) -> None:
        described = as_described(play.board(), craft(play.SEAR, push_mana=2))
        # pushing is line then pushed.
        self.assertEqual(
            described.plain,
            "tarmar_menu.pushing true_magic.craft_aimed Foe "
            "true_magic.craft_begin Sear (tier 1) 2",
        )
        # craft_push_cost is level, pushed, total, turns; three mana drain 24.
        self.assertEqual(
            described.cost,
            "true_magic.drains true_magic.craft_push_cost 1 2 3 3 24 16 fatigue",
        )

    def test_a_borrowing_craft_says_what_it_would_borrow(self) -> None:
        # One mana in the pool, three to gather.
        described = as_described(
            play.board(mage={"mana": 1}), craft(play.SEAR, push_mana=2, borrow=True)
        )
        self.assertEqual(described.borrowed, 2)
        # borrowing is line then owed.
        self.assertEqual(
            described.plain,
            "tarmar_menu.borrowing tarmar_menu.pushing true_magic.craft_aimed Foe "
            "true_magic.craft_begin Sear (tier 1) 2 2",
        )
        # craft_borrowing_cost is cost then owed.
        self.assertEqual(
            described.cost,
            "true_magic.drains true_magic.craft_borrowing_cost "
            "true_magic.craft_push_cost 1 2 3 3 2 24 16 fatigue",
        )


@with_fixed_words
class ContinuationLineTest(DefaultCanonTestCase):
    """A craft that stands: Sear with two mana pushed, one turn of its three
    taken and one of its three mana gathered."""

    def continuing(
        self, *, borrow: bool = False, push: int = 2, **mage: object
    ) -> turn_menu.DescribedCandidate:
        state = play.crafting_sear(push=push, borrow=borrow, turns_done=1, **mage)
        return as_described(state, craft(play.SEAR, push_mana=push, borrow=borrow))

    def test_it_names_the_working_and_its_turn(self) -> None:
        described = self.continuing()
        # craft_continue is turn, turns, working.
        self.assertEqual(
            described.plain,
            "true_magic.craft_aimed Foe true_magic.craft_continue 2 3 Sear (tier 1)",
        )
        # craft_share is gathered, mana, share.
        self.assertEqual(
            described.cost,
            "true_magic.drains true_magic.craft_share 1 3 1 24 16 fatigue",
        )

    def test_a_turn_with_no_share_due_says_so(self) -> None:
        # Unpushed, Sear's one mana is gathered on its last turn.
        described = self.continuing(push=0)
        # craft_no_share is gathered then mana.
        self.assertEqual(
            described.cost,
            "true_magic.drains true_magic.craft_no_share 0 1 8 32 fatigue",
        )

    def test_a_share_the_pool_cannot_pay_is_borrowed_where_the_craft_borrows(
        self,
    ) -> None:
        described = self.continuing(borrow=True, mana=0)
        # craft_share_borrowed is cost then owed.
        self.assertEqual(
            described.cost,
            "true_magic.drains true_magic.craft_share_borrowed "
            "true_magic.craft_share 1 3 1 1 24 16 fatigue",
        )
        # Two of its three mana are still to gather, and the pool is empty.
        self.assertEqual(described.borrowed, 2)

    def test_a_share_the_pool_cannot_pay_loses_a_craft_that_does_not_borrow(
        self,
    ) -> None:
        described = self.continuing(mana=0)
        # craft_share_unpaid is cost then mana_left.
        self.assertEqual(
            described.cost,
            "true_magic.drains true_magic.craft_share_unpaid "
            "true_magic.craft_share 1 3 1 0 24 16 fatigue",
        )
        self.assertEqual(described.borrowed, 0)


def craft_actions(events: list[dict]) -> list[dict]:
    """The payload of each craft action among a turn's events."""
    return [
        event["payload"]
        for event in events
        if event["event_type"] == "action" and "craft" in event["payload"]
    ]


def crafts_lost(events: list[dict]) -> list[dict]:
    """The payload of each event that loses a craft."""
    return [event["payload"] for event in events if event["payload"].get("craft_lost")]


def share_forecast(line: turn_menu.DescribedCandidate) -> str:
    """What a continuation's cost says of this turn's share, without the
    drain it goes on to name: ``drains`` is cost, drain, left, pool."""
    opening = "true_magic.drains "
    assert line.cost.startswith(opening), line.cost
    return line.cost.removeprefix(opening).rsplit(" ", 3)[0]


@with_fixed_words
class ForecastAgreesWithTheEngineTest(DefaultCanonTestCase):
    """What the menu says a craft will take is what the engine then takes.

    The engine has no public call for how a share is paid: the pool pays what
    it holds, the rest is borrowed where the craft borrows, and where it does
    not the working is lost unpaid (``TurnRunner.craft_working``). The menu
    says which before the turn is played, reading the shortfall through
    :func:`battle.turn_menu.mana_short`, so it is held to the engine here as
    ``test_magic_seating`` holds a borrowing cast's: the option is read off
    the mage's own menu, the mage takes it, the turn is played, and the line
    is compared with the events the engine wrote.

    Sear's first tier takes three turns. With two mana pushed its three mana
    are gathered one a turn; with three pushed its four are gathered one, one
    and two, so its last share can be part paid.
    """

    def carried_on(
        self, state: BattleState, *, push: int, borrow: bool, turns: int = 1
    ) -> tuple[turn_menu.DescribedCandidate, list[dict]]:
        """Play ``turns`` turns in which the mage crafts Sear at the foe.

        Returns:
            The craft option as the mage's menu described it before the
            first of those turns, and every event of the turns played.
        """
        pick = play.craft(play.SEAR, play.FOE, push=push, borrow=borrow)
        line = next(
            option
            for option in turn_menu.player_menu(
                TARMAR_PROFILE, state, state.by_id(play.MAGE)
            )
            if pick(option)
        )
        assert isinstance(line, turn_menu.DescribedCandidate)
        events = play.play(state, [{play.MAGE: pick, play.FOE: play.idle}] * turns)
        return line, events

    @staticmethod
    def share_sentence(action: dict) -> str:
        """The ``craft_share`` sentence a craft action's own numbers fill:
        the mana gathered before it, the craft's whole mana, and its share."""
        return (
            f"true_magic.craft_share {action['gathered'] - action['share']} "
            f"{action['mana_total']} {action['share']}"
        )

    def assert_borrows_what_its_turns_borrow(
        self,
        line: turn_menu.DescribedCandidate,
        events: list[dict],
        *,
        turns: list[int],
        borrowed: int,
    ) -> list[dict]:
        """The craft actions played were ``turns``, and between them the
        engine borrowed what the line said the craft would: ``borrowed``.

        Returns:
            The craft actions' payloads, in order.
        """
        actions = craft_actions(events)
        self.assertEqual([action["turn"] for action in actions], turns)
        self.assertEqual(line.borrowed, sum(action["borrowed"] for action in actions))
        self.assertEqual(line.borrowed, borrowed)
        return actions

    def test_the_share_a_line_says_is_borrowed_is_what_the_engine_borrows(
        self,
    ) -> None:
        for label, push, turns_done, mana, borrowed in (
            ("an empty pool", 2, 1, 0, 1),
            ("a part-full pool", 3, 2, 1, 1),
        ):
            with self.subTest(label):
                state = play.crafting_sear(
                    push=push, borrow=True, turns_done=turns_done, mana=mana
                )
                line, events = self.carried_on(state, push=push, borrow=True)
                (action,) = craft_actions(events)
                self.assertEqual(action["borrowed"], borrowed)
                # craft_share_borrowed is cost then owed.
                self.assertEqual(
                    share_forecast(line),
                    "true_magic.craft_share_borrowed "
                    f"{self.share_sentence(action)} {action['borrowed']}",
                )
                self.assertEqual(crafts_lost(events), [])

    def test_the_share_a_line_says_loses_the_working_loses_it_unpaid(self) -> None:
        for label, push, turns_done, mana, mana_total, share in (
            ("an empty pool", 2, 1, 0, 3, 1),
            ("a part-full pool", 3, 2, 1, 4, 2),
        ):
            with self.subTest(label):
                state = play.crafting_sear(push=push, turns_done=turns_done, mana=mana)
                line, events = self.carried_on(state, push=push, borrow=False)
                self.assertEqual(craft_actions(events), [])
                (lost,) = crafts_lost(events)
                self.assertEqual(lost["ending"], CastingEnding.UNPAID.value)
                # Nothing was taken: the pool is what the line said it was.
                mage = state.by_id(play.MAGE)
                self.assertEqual(mage.mana, mana)
                # craft_share_unpaid is cost then mana_left.
                self.assertEqual(
                    share_forecast(line),
                    "true_magic.craft_share_unpaid true_magic.craft_share "
                    f"{lost['mana_spent']} {mana_total} {share} {mage.mana}",
                )

    def test_a_share_the_pool_covers_is_neither_borrowed_nor_lost(self) -> None:
        for label, borrow, mana in (
            ("a pool that just covers it", False, 2),
            ("a borrowing craft's pool that just covers it", True, 2),
            ("a full pool", False, 9),
        ):
            with self.subTest(label):
                state = play.crafting_sear(
                    push=3, borrow=borrow, turns_done=2, mana=mana
                )
                line, events = self.carried_on(state, push=3, borrow=borrow)
                (action,) = craft_actions(events)
                self.assertEqual((action["share"], action["borrowed"]), (2, 0))
                self.assertEqual(share_forecast(line), self.share_sentence(action))
                self.assertEqual(crafts_lost(events), [])

    def test_a_turn_with_no_share_due_asks_nothing_of_an_empty_pool(self) -> None:
        # Unpushed, Sear's one mana falls due on its last turn.
        state = play.crafting_sear(push=0, mana=0)
        line, events = self.carried_on(state, push=0, borrow=False)
        (action,) = craft_actions(events)
        self.assertEqual((action["share"], action["borrowed"]), (0, 0))
        # craft_no_share is gathered then mana.
        self.assertEqual(
            share_forecast(line),
            f"true_magic.craft_no_share {action['gathered']} {action['mana_total']}",
        )
        self.assertEqual(crafts_lost(events), [])

    def test_what_a_craft_not_yet_begun_would_borrow_is_what_its_turns_borrow(
        self,
    ) -> None:
        for label, push, borrow, mana, borrowed in (
            ("an empty pool", 2, True, 0, 3),
            ("a part-full pool", 3, True, 2, 2),
            ("a craft that does not borrow", 2, False, 10, 0),
        ):
            with self.subTest(label):
                state = play.board(mage={"mana": mana})
                line, events = self.carried_on(state, push=push, borrow=borrow, turns=3)
                self.assert_borrows_what_its_turns_borrow(
                    line, events, turns=[1, 2, 3], borrowed=borrowed
                )

    def test_what_a_continuation_would_borrow_is_what_its_turns_left_borrow(
        self,
    ) -> None:
        # Not the share its line says this turn borrows: the mana the craft
        # has still to gather, less the pool.
        for label, push, mana, owed_this_turn, borrowed in (
            ("an empty pool", 2, 0, 1, 2),
            # This turn's one mana is paid; the two of the last are borrowed.
            ("a part-full pool", 3, 1, 0, 2),
        ):
            with self.subTest(label):
                state = play.crafting_sear(push=push, borrow=True, mana=mana)
                line, events = self.carried_on(state, push=push, borrow=True, turns=2)
                actions = self.assert_borrows_what_its_turns_borrow(
                    line, events, turns=[2, 3], borrowed=borrowed
                )
                self.assertEqual(actions[0]["borrowed"], owed_this_turn)


@with_fixed_words
class NoTrueMagicTest(DefaultCanonTestCase):
    """Under a canon with no True Magic the engine offers neither option, and
    one handed to the menu all the same is refused by name, not guessed at."""

    def test_a_craft_and_a_true_cast_are_refused_by_name(self) -> None:
        state = play.board(mage=play.MISSILE_MAGE)
        with seated_rules(replace(DEFAULT_MAGIC_RULES, true_magic=None)):
            with self.assertRaisesMessage(ValueError, "has no True Magic"):
                workings.true_magic()
            for candidate in (cast(true_cast=True), craft(play.SEAR)):
                with (
                    self.subTest(candidate.letter),
                    self.assertRaisesMessage(ValueError, "has no True Magic"),
                ):
                    as_described(state, candidate)


@with_fixed_words
class EveryOfferedOptionIsWordedTest(DefaultCanonTestCase):
    """No True cast or craft the engine's own menu offers falls back to the
    engine's name for it."""

    #: A line opens with a ``[true_magic]`` sentence, inside the menu's own
    #: sentences for a push and a borrowing where the option carries them.
    WORDED = r"^(tarmar_menu\.(pushing|borrowing) )*true_magic\.\w+ "

    def test_every_true_cast_and_craft_on_the_menu_has_its_line(self) -> None:
        boards = {
            "a full pool": play.board(mage=play.MISSILE_MAGE, friend=True),
            "an empty pool": play.board(mage={**play.MISSILE_MAGE, "mana": 0}),
            "a craft standing": play.crafting_sear(borrow=True, mana=0),
        }
        seen = 0
        for label, state in boards.items():
            for option in turn_menu.player_menu(
                TARMAR_PROFILE, state, state.by_id(play.MAGE)
            ):
                if not option.true_cast and option.letter != CRAFT_OPTION:
                    continue
                payload = turn_menu.menu_payload(option)
                with self.subTest(board=label, option=option.name):
                    self.assertRegex(payload["plain"], self.WORDED)
                    self.assertTrue(payload["cost"])
                    seen += 1
        self.assertGreater(seen, 40)
