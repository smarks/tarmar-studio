"""Who may record a True cast or a craft: whoever acts for the figure, and
only one the menu offered (tarmar-studio #208).

The engine offers a True mage the True cast of a spell under the recipe
cast's own letter, target and spell (tarmar-engine #77), so ``true_cast`` is
part of what sets one option apart (``battle.services.option_variant``). A
craft needs no field of its own: it is the engine's craft letter with the
working's key as its spell, its push, its borrowing and its target. Either is
recorded only when the pending menu holds it (``names_option``), for a player
and for a Game Master alike, whether he chooses for a player's seat or sits in
one himself.

Every battle here runs under the default canon's rules and the fixed words.
"""

from typing import Any

from django.test import SimpleTestCase
from django.urls import reverse
from tarmar_engine.actions import CRAFT_OPTION
from tarmar_engine.policy import Candidate

from accounts.models import User
from battle.models import Battle, BattleEvent
from battle.refusal_words import refusal_words
from battle.services import (
    _match_candidate,
    advance_turn,
    names_option,
    option_variant,
    record_player_action,
)
from common.test_helpers import BaseTestCase

from . import true_magic_play as play
from .magic_fixtures import DEFAULT_MAGIC_RULES, seated_rules
from .rulings_words import with_fixed_words
from .test_cast_flow import awaiting_wizard_battle

#: A spell the mage of :func:`~.true_magic_play.awaiting_true_mage` does not
#: know and may True cast: the rung of another element's ladder beside the
#: Fire Missile it knows.
SIBLING = "water_missile"
#: A catalog spell that mage neither knows nor may True cast.
NOT_TRUE_CASTABLE = "heal"


class TrueCastChoiceMatchTest(SimpleTestCase):
    recipe = Candidate("h", "CAST", 1.0, "", target_id=2, spell_key="fire_missile")
    true = Candidate(
        "h",
        "CAST (TRUE CAST)",
        0.0,
        "",
        target_id=2,
        spell_key="fire_missile",
        true_cast=True,
    )
    choice = {"letter": "h", "target_id": 2, "spell_key": "fire_missile"}

    def test_true_cast_is_part_of_what_sets_an_option_apart(self) -> None:
        self.assertEqual(
            option_variant(self.true.to_payload()),
            {
                "push_mana": 0,
                "borrow": False,
                "anchor_target": None,
                "counter_mana": None,
                "true_cast": True,
            },
        )
        self.assertIs(option_variant(self.recipe.to_payload())["true_cast"], False)

    def test_a_true_cast_choice_names_the_true_cast_only(self) -> None:
        true_choice = {**self.choice, "true_cast": True}
        self.assertTrue(names_option(self.true.to_payload(), true_choice))
        self.assertFalse(names_option(self.recipe.to_payload(), true_choice))
        # A choice with no true_cast, as one stored before True Magic, is the
        # recipe cast.
        self.assertTrue(names_option(self.recipe.to_payload(), self.choice))
        self.assertFalse(names_option(self.true.to_payload(), self.choice))

    def test_the_replayed_menu_resolves_a_true_cast(self) -> None:
        menu = [self.recipe, self.true]
        self.assertIs(
            _match_candidate(menu, {**self.choice, "true_cast": True}), self.true
        )
        self.assertIs(_match_candidate(menu, self.choice), self.recipe)
        self.assertIsNone(
            _match_candidate([self.recipe], {**self.choice, "true_cast": True})
        )


class CraftChoiceMatchTest(SimpleTestCase):
    """A craft is its letter, its working, its target, its push and its
    borrowing: a choice that differs in any one names another option."""

    offered = Candidate(
        CRAFT_OPTION, "CRAFT", 1.0, "", target_id=2, spell_key=play.SEAR, push_mana=1
    )
    choice = {
        "letter": CRAFT_OPTION,
        "target_id": 2,
        "spell_key": play.SEAR,
        "push_mana": 1,
    }

    def test_the_choice_that_names_the_craft(self) -> None:
        self.assertTrue(names_option(self.offered.to_payload(), self.choice))

    def test_a_choice_that_differs_names_no_such_craft(self) -> None:
        for field, other in (
            ("spell_key", play.KNIT),
            ("target_id", 3),
            ("push_mana", 2),
            ("borrow", True),
            ("letter", "h"),
            ("true_cast", True),
        ):
            with self.subTest(field=field):
                self.assertFalse(
                    names_option(
                        self.offered.to_payload(), {**self.choice, field: other}
                    )
                )


@with_fixed_words
class TrueMagicChoiceTestCase(BaseTestCase):
    """A battle paused on a seated player's True mage, the default canon's
    rules seated."""

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(seated_rules(DEFAULT_MAGIC_RULES))
        self.dm = self.create_dm()
        self.player = self.create_player()
        self.battle, self.seat = play.awaiting_true_mage(self.dm, self.player)
        self.pending = self.battle.pending_input_json

    def offered(self, **fields: object) -> dict:
        """The first option of the pending menu whose fields are these, an
        absent key reading as the plain option's."""
        return next(
            option
            for option in self.pending["candidates"]
            if all(
                option.get(key, plain_of(key)) == value for key, value in fields.items()
            )
        )

    def record(
        self, user: User | None = None, **choice: Any
    ) -> tuple[Battle, list[BattleEvent]]:
        """Record a choice for the mage; ``user`` is the player unless given."""
        return record_player_action(
            self.battle.pk,
            user if user is not None else self.player,
            combatant_id=self.seat.pk,
            turn=self.pending["turn"],
            **choice,
        )

    def choice_of(self, option: dict) -> dict:
        """The arguments that record ``option`` exactly."""
        return {
            "letter": option["letter"],
            "target_id": option.get("target_id"),
            "spell_key": option.get("spell_key") or "",
            "push_mana": option.get("push_mana") or 0,
            "borrow": bool(option.get("borrow")),
            "true_cast": bool(option.get("true_cast")),
        }

    def chosen(self) -> dict:
        """What the mage's decision in the log records as chosen."""
        decision = BattleEvent.objects.filter(
            battle=self.battle, event_type="decision", actor=self.seat.display_name
        ).latest("sequence")
        return decision.payload["chosen"]

    def assert_not_on_the_menu(self, user: User | None = None, **choice: Any) -> None:
        with self.assertRaisesMessage(
            ValueError, refusal_words().services.option_not_on_menu
        ):
            self.record(user, **choice)
        # Nothing was stored to replay.
        self.assertEqual(Battle.objects.get(pk=self.battle.pk).turn_inputs_json, {})


def plain_of(key: str) -> object:
    """What a menu option's payload means by leaving ``key`` out."""
    return {"push_mana": 0, "borrow": False, "true_cast": False}.get(key)


class RecordTrueCastTest(TrueMagicChoiceTestCase):
    """Who may set ``true_cast``: whoever acts for the figure, for a True cast
    the menu offered."""

    def test_an_offered_true_cast_is_recorded_and_replayed(self) -> None:
        offered = self.offered(letter="h", spell_key=SIBLING, true_cast=True)
        self.record(**self.choice_of(offered))
        chosen = self.chosen()
        self.assertEqual((chosen["spell_key"], chosen["true_cast"]), (SIBLING, True))
        # The engine ran it as a True cast, and the log says so.
        cast = BattleEvent.objects.get(
            battle=self.battle, event_type="action", payload__spell=SIBLING
        )
        self.assertIs(cast.payload["true_cast"], True)
        self.assertTrue(cast.message.startswith("true_magic_log.true_cast "))

    def test_a_spell_only_a_true_cast_reaches_is_refused_without_it(self) -> None:
        offered = self.offered(letter="h", spell_key=SIBLING, true_cast=True)
        self.assert_not_on_the_menu(**{**self.choice_of(offered), "true_cast": False})

    def test_a_known_spell_without_true_cast_is_the_recipe_cast(self) -> None:
        offered = self.offered(letter="h", spell_key="fire_missile", true_cast=False)
        self.record(**self.choice_of(offered))
        self.assertNotIn("true_cast", self.chosen())
        cast = BattleEvent.objects.get(
            battle=self.battle, event_type="action", payload__spell="fire_missile"
        )
        self.assertNotIn("true_cast", cast.payload)
        self.assertFalse(cast.message.startswith("true_magic_log."))

    def test_a_true_cast_the_menu_does_not_offer_is_refused(self) -> None:
        offered = self.offered(letter="h", spell_key=SIBLING, true_cast=True)
        self.assert_not_on_the_menu(
            **{**self.choice_of(offered), "spell_key": NOT_TRUE_CASTABLE}
        )

    def test_a_figure_with_no_level_in_the_true_skill_may_not_true_cast(self) -> None:
        # A generated wizard holds no level in the True skill: its menu
        # offers the recipe cast of its Fire Missile and no True cast of it.
        battle, seat = awaiting_wizard_battle(self.dm, self.create_player("other"))
        recipe = next(
            option
            for option in battle.pending_input_json["candidates"]
            if option.get("spell_key") == "fire_missile"
            and not option.get("push_mana")
            and not option.get("borrow")
        )
        self.assertFalse(
            any(
                option.get("true_cast")
                for option in battle.pending_input_json["candidates"]
            )
        )
        with self.assertRaisesMessage(
            ValueError, refusal_words().services.option_not_on_menu
        ):
            record_player_action(
                battle.pk,
                seat.player,
                combatant_id=seat.pk,
                turn=battle.pending_input_json["turn"],
                letter=recipe["letter"],
                target_id=recipe["target_id"],
                spell_key="fire_missile",
                true_cast=True,
            )

    def test_the_endpoint_reads_true_cast(self) -> None:
        offered = self.offered(letter="h", spell_key=SIBLING, true_cast=True)
        self.login_as(self.player)
        url = reverse("battle:act", args=[self.battle.pk])
        body = {
            "combatant_id": self.seat.pk,
            "turn": self.pending["turn"],
            "letter": offered["letter"],
            "target_id": offered["target_id"],
            "spell_key": SIBLING,
        }
        response = self.post_json(url, {**body, "true_cast": "yes"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["error"], refusal_words().views.true_cast_true_or_false
        )
        # Left out, the spell is not one the recipe cast reaches.
        response = self.post_json(url, body)
        self.assertEqual(
            response.json()["error"], refusal_words().services.option_not_on_menu
        )
        response = self.post_json(url, {**body, "true_cast": True})
        self.assertEqual(response.status_code, 200)
        self.assertIs(self.chosen()["true_cast"], True)


class RecordCraftTest(TrueMagicChoiceTestCase):
    """Who may begin or carry on a craft: whoever acts for the figure, at a
    working, a target, a push and a borrowing the menu offered."""

    def test_an_offered_craft_is_recorded_and_begins(self) -> None:
        offered = self.offered(letter=CRAFT_OPTION, spell_key=play.SEAR)
        battle, _events = self.record(**self.choice_of(offered))
        self.assertEqual(self.chosen()["spell_key"], play.SEAR)
        begun = BattleEvent.objects.get(
            battle=self.battle, event_type="action", payload__craft="begun"
        )
        self.assertTrue(begun.message.startswith("true_magic_log.craft_begun "))
        mage = next(
            figure
            for figure in battle.state_json["combatants"]
            if figure["combatant_id"] == self.seat.pk
        )
        self.assertEqual(mage["crafting"]["spell_key"], play.SEAR)

    def test_a_craft_the_menu_does_not_offer_is_refused(self) -> None:
        offered = self.choice_of(self.offered(letter=CRAFT_OPTION, spell_key=play.SEAR))
        tier_cap = DEFAULT_MAGIC_RULES.tier_cap(play.true_mage())
        assert tier_cap is not None, "nobody has lifted the mage's cap"
        past_the_cap = tier_cap + 1
        for label, change in (
            (
                "a tier past the cap",
                {"spell_key": play.TRUE_MAGIC.working_key("sear", past_the_cap)},
            ),
            ("a working nobody granted", {"spell_key": play.FIGMENT}),
            ("a key that is no working", {"spell_key": "working:nothing:1"}),
            ("a push past the push cap", {"push_mana": 99}),
            ("a borrowing the pool does not need", {"borrow": True}),
            ("a target it may not name", {"target_id": self.seat.pk}),
            ("a catalog spell under the craft letter", {"spell_key": "fire_missile"}),
            ("a working under the cast letter", {"letter": "h"}),
        ):
            with self.subTest(label):
                self.assert_not_on_the_menu(**{**offered, **change})

    def test_a_game_master_choosing_for_the_seat_is_held_to_the_menu(self) -> None:
        offered = self.choice_of(self.offered(letter=CRAFT_OPTION, spell_key=play.SEAR))
        self.assert_not_on_the_menu(self.dm, **{**offered, "spell_key": play.FIGMENT})
        self.assert_not_on_the_menu(
            self.dm,
            **{
                **offered,
                "letter": "h",
                "spell_key": NOT_TRUE_CASTABLE,
                "true_cast": True,
            },
        )
        # And may record what it does offer.
        self.record(self.dm, **offered)
        self.assertEqual(self.chosen()["spell_key"], play.SEAR)

    def test_the_endpoint_records_a_craft_with_no_field_of_its_own(self) -> None:
        offered = self.offered(letter=CRAFT_OPTION, spell_key=play.SEAR, push_mana=1)
        self.login_as(self.player)
        response = self.post_json(
            reverse("battle:act", args=[self.battle.pk]),
            {
                "combatant_id": self.seat.pk,
                "turn": self.pending["turn"],
                "letter": CRAFT_OPTION,
                "target_id": offered["target_id"],
                "spell_key": play.SEAR,
                "push_mana": 1,
            },
        )
        self.assertEqual(response.status_code, 200)
        chosen = self.chosen()
        self.assertEqual((chosen["spell_key"], chosen["push_mana"]), (play.SEAR, 1))


class ContinuationChoiceTest(TrueMagicChoiceTestCase):
    """While a craft stands, the only craft on the menu is its continuation."""

    def setUp(self) -> None:
        super().setUp()
        begun = self.offered(letter=CRAFT_OPTION, spell_key=play.SEAR, push_mana=1)
        self.record(**self.choice_of(begun))
        # The turn that began the craft has run; the next pauses on the mage.
        self.battle, _events = advance_turn(self.battle.pk)
        self.assertEqual(self.battle.status, Battle.STATUS_AWAITING)
        self.pending = self.battle.pending_input_json

    def test_the_menu_offers_the_craft_that_stands_and_no_other(self) -> None:
        crafts = [
            option
            for option in self.pending["candidates"]
            if option["letter"] == CRAFT_OPTION
        ]
        self.assertTrue(crafts)
        self.assertEqual(
            {(option["spell_key"], option.get("push_mana")) for option in crafts},
            {(play.SEAR, 1)},
        )

    def test_another_working_is_refused_and_the_continuation_recorded(self) -> None:
        continuation = self.choice_of(
            self.offered(letter=CRAFT_OPTION, spell_key=play.SEAR, push_mana=1)
        )
        self.assert_not_on_the_menu(**{**continuation, "spell_key": play.KNIT})
        # Its push was fixed when it began.
        self.assert_not_on_the_menu(**{**continuation, "push_mana": 0})
        self.record(**continuation)
        carried_on = BattleEvent.objects.get(
            battle=self.battle, event_type="action", payload__craft="continued"
        )
        self.assertTrue(
            carried_on.message.startswith("true_magic_log.craft_continued ")
        )


@with_fixed_words
class GameMasterInASeatTest(BaseTestCase):
    """A Game Master sitting in a seat with his own True mage is a player
    there: he records only what the menu offered."""

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(seated_rules(DEFAULT_MAGIC_RULES))
        self.dm = self.create_dm()
        self.battle, self.seat = play.awaiting_true_mage(
            self.dm, self.create_player(), seated_by=self.dm
        )
        self.pending = self.battle.pending_input_json

    def test_his_own_seat_holds_him_to_the_menu(self) -> None:
        self.assertEqual(self.seat.player, self.dm)
        offered = next(
            option
            for option in self.pending["candidates"]
            if option["letter"] == CRAFT_OPTION and option["spell_key"] == play.SEAR
        )
        choice = {
            "combatant_id": self.seat.pk,
            "turn": self.pending["turn"],
            "letter": CRAFT_OPTION,
            "target_id": offered["target_id"],
        }
        for label, off_menu in (
            ("a working he has not granted himself", {"spell_key": play.FIGMENT}),
            (
                "a True cast of a spell out of reach",
                {"letter": "h", "spell_key": NOT_TRUE_CASTABLE, "true_cast": True},
            ),
        ):
            with (
                self.subTest(label),
                self.assertRaisesMessage(
                    ValueError, refusal_words().services.option_not_on_menu
                ),
            ):
                record_player_action(self.battle.pk, self.dm, **{**choice, **off_menu})
        battle, _events = record_player_action(
            self.battle.pk, self.dm, spell_key=play.SEAR, **choice
        )
        mage = next(
            figure
            for figure in battle.state_json["combatants"]
            if figure["combatant_id"] == self.seat.pk
        )
        self.assertEqual(mage["crafting"]["spell_key"], play.SEAR)
