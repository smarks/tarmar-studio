"""Issue #757: coin is spent. Catalog gear is bought from the purse.

The purse is starting coin + prior-experience earnings + every movement
since; ``characters.purse.move_coin`` is the one writer.
"""

from decimal import Decimal

from django.urls import reverse
from django.utils.html import escape

from characters import services
from characters.models import Character
from characters.purse import (
    PurseError,
    charge_for_catalog_item,
    format_purse,
    given_by_game_master,
    move_coin,
)
from characters.refusal_words import refusal_words
from common.test_helpers import BaseTestCase, create_character, says


def held(coin: str) -> str:
    """What the purse prints for ``coin``, in the configured words (#136)."""
    return refusal_words().purse.held.format(coin=coin)


def held_and_unknown(coin: str) -> str:
    """The purse while the starting coin is still the Game Master's to record."""
    words = refusal_words().purse
    return words.held_and_unknown.format(held=held(coin), unknown=words.consult_gm)


class PurseRuleTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.character = create_character(
            self.create_user("holder"),
            name="Holder",
            starting_coin=10,
            money_earned=30,
        )

    def test_the_purse_is_starting_coin_plus_earnings_plus_movements(self):
        self.assertEqual(self.character.purse, Decimal(40))
        move_coin(self.character, Decimal("-12.5"), reason="a lantern")
        self.assertEqual(self.character.purse, Decimal("27.50"))

    def test_a_spend_past_the_purse_is_refused(self):
        with self.assertRaises(PurseError) as refused:
            move_coin(self.character, Decimal(-41), reason="fine plate")
        self.assertTrue(
            says(str(refused.exception), refusal_words().purse.not_enough, "40")
        )
        self.assertEqual(self.character.purse, Decimal(40))


class PurchaseEndpointTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user("buyer")
        # 100 coin: a dagger (10) or a broadsword (80) fits, chainmail (200) does not.
        self.character = create_character(
            self.owner, name="Buyer", starting_coin=100, equipment=[]
        )
        self.login_as(self.owner)

    def post(self, name, payload):
        return self.post_json(
            reverse(f"characters:{name}", args=[self.character.pk]), payload
        )

    def test_a_catalog_item_costs_its_price_and_the_history_says_so(self):
        response = self.post("equipment_add", {"item_id": "broadsword"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["purse"], held("20"))
        self.character.refresh_from_db()
        self.assertEqual(self.character.purse, Decimal(20))
        self.assertNotIn("paid", self.character.equipment[0])
        lines = [entry["new"] for entry in self.character.history]
        words = refusal_words().purse
        paid = words.paid.format(
            coin="80",
            reason=words.quantity_of.format(quantity=1, name="Broadsword"),
            purse=held("20"),
        )
        self.assertIn(paid, lines)

    def test_a_player_short_of_coin_is_refused_and_nothing_changes(self):
        response = self.post("equipment_add", {"item_id": "chainmail"})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(
            says(response.json()["error"], refusal_words().purse.not_enough)
        )
        self.character.refresh_from_db()
        self.assertEqual(self.character.equipment, [])
        self.assertEqual(self.character.purse, Decimal(100))

    def test_a_player_asking_for_it_free_still_pays(self):
        self.post("equipment_add", {"item_id": "dagger", "free": True})
        self.character.refresh_from_db()
        self.assertEqual(self.character.purse, Decimal(90))

    def test_a_game_master_may_give_an_item_free(self):
        self.logout()
        self.login_as(self.create_dm())
        self.post("equipment_add", {"item_id": "chainmail", "free": True})
        self.character.refresh_from_db()
        self.assertEqual(self.character.purse, Decimal(100))

    def test_more_of_an_item_is_bought_and_fewer_returns_nothing(self):
        """Ruling on the #757 review: no refunds; a Game Master may give coin."""
        self.post("equipment_add", {"item_id": "dagger"})
        self.post("equipment_update", {"index": 0, "quantity": 3})
        self.character.refresh_from_db()
        self.assertEqual(self.character.purse, Decimal(70))
        refused = self.post("equipment_update", {"index": 0, "quantity": 20})
        self.assertEqual(refused.status_code, 400)
        self.post("equipment_update", {"index": 0, "quantity": 1})
        self.character.refresh_from_db()
        self.assertEqual(self.character.purse, Decimal(70))
        self.assertEqual(self.character.equipment[0]["quantity"], 1)

    def test_removing_and_bulk_removing_return_nothing(self):
        self.post("equipment_add", {"item_id": "broadsword"})
        self.post("equipment_add", {"item_id": "dagger"})
        self.post("equipment_remove", {"index": 0})
        self.post("equipment_bulk_remove", {"indices": [0]})
        self.character.refresh_from_db()
        self.assertEqual(self.character.equipment, [])
        self.assertEqual(self.character.purse, Decimal(10))

    def test_a_gift_raised_by_a_player_is_bought_and_lowered_returns_nothing(self):
        """The reviewer's gift-then-raise-then-lower probe."""
        self.logout()
        self.login_as(self.create_dm())
        self.post("equipment_add", {"item_id": "dagger", "free": True})
        self.logout()
        self.login_as(self.owner)
        self.post("equipment_update", {"index": 0, "quantity": 2})
        self.post("equipment_update", {"index": 0, "quantity": 1})
        self.character.refresh_from_db()
        self.assertEqual(self.character.purse, Decimal(90))

    def test_a_players_custom_item_is_bought_at_its_stated_cost(self):
        response = self.post(
            "equipment_add", {"is_custom": True, "name": "Heirloom", "cost": 30}
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.post("equipment_update", {"index": 0, "quantity": 2})
        self.character.refresh_from_db()
        self.assertEqual(self.character.purse, Decimal(40))
        short = self.post(
            "equipment_add", {"is_custom": True, "name": "Crown", "cost": 500}
        )
        self.assertTrue(says(short.json()["error"], refusal_words().purse.not_enough))

    def test_a_non_finite_cost_or_weight_is_refused(self):
        """The second review: cost "nan" or "inf" answered a 500."""
        for field, value in (("cost", "nan"), ("cost", "inf"), ("weight", "nan")):
            with self.subTest(field=field, value=value):
                response = self.post(
                    "equipment_add", {"is_custom": True, "name": "Odd", field: value}
                )
                self.assertEqual(response.status_code, 400)

    def test_a_player_cannot_invent_a_combat_item(self):
        """#845: 10-Stop "Dragonhide" armour, free, reached the board."""
        for category, extra in (
            ("armor", {"stops": 10}),
            ("shield", {"stops": 3}),
            ("weapons", {"damage": "3d6"}),
        ):
            with self.subTest(category):
                response = self.post(
                    "equipment_add",
                    {"is_custom": True, "name": "Dragonhide", "category": category}
                    | extra,
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("Game Master", response.json()["error"])
        self.character.refresh_from_db()
        self.assertEqual(self.character.equipment, [])

    def test_a_game_masters_custom_item_is_free(self):
        self.logout()
        self.login_as(self.create_dm())
        response = self.post(
            "equipment_add",
            {"is_custom": True, "name": "Dragonhide", "category": "armor", "cost": 900},
        )
        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        self.assertEqual(self.character.purse, Decimal(100))
        # The gift is recorded in the purse history (review of #927).
        self.assertEqual(
            (self.character.history[-1]["field"], self.character.history[-1]["new"]),
            ("purse", given_by_game_master(1, "Dragonhide")),
        )

    def test_more_of_an_item_carried_at_no_cost_is_a_game_masters_to_give(self):
        """Review of #927: the sheet's raise of a cost-0 or old-shape item."""
        for carried in (
            {"item_id": "custom", "name": "Keepsake", "cost": 0, "quantity": 1},
            {"name": "Keepsake", "quantity": 1},
        ):
            with self.subTest(carried=carried):
                self.character.equipment = [carried]
                self.character.save()

                raised = self.post("equipment_update", {"index": 0, "quantity": 4})

                self.assertEqual(raised.status_code, 400)
                self.assertEqual(
                    raised.json()["error"], refusal_words().purse.custom_item_more
                )
                self.character.refresh_from_db()
                self.assertEqual(self.character.equipment, [carried])
                self.assertEqual(self.character.purse, Decimal(100))
        self.logout()
        self.login_as(self.create_dm())
        given = self.post("equipment_update", {"index": 0, "quantity": 4})
        self.assertEqual(given.status_code, 200, given.content)

    def test_more_of_a_game_masters_combat_item_is_his_to_give(self):
        """#927: the quantity raise is held to the add's rule, as the chat is.

        A player raising the quantity of a custom sword the Game Master gave
        at no cost made more of it for nothing, numbers and all.
        """
        self.logout()
        self.login_as(self.create_dm())
        given = self.post(
            "equipment_add",
            {"is_custom": True, "name": "Starblade", "category": "weapons"},
        )
        self.assertEqual(given.status_code, 200, given.content)
        self.logout()
        self.login_as(self.owner)

        raised = self.post("equipment_update", {"index": 0, "quantity": 3})

        self.assertEqual(raised.status_code, 400)
        self.assertIn("Game Master", raised.json()["error"])
        with self.assertRaisesMessage(
            services.CharacterChangeError, refusal_words().purse.custom_combat_item
        ):
            services.add_equipment(
                self.character.pk,
                "Starblade",
                2,
                actor=self.owner,
                purchase=True,
                price=Decimal(1),
            )
        self.character.refresh_from_db()
        self.assertEqual(self.character.equipment[0]["quantity"], 1)
        self.assertEqual(self.character.purse, Decimal(100))
        self.logout()
        self.login_as(self.create_dm("dm2"))
        self.assertEqual(
            self.post("equipment_update", {"index": 0, "quantity": 3}).status_code,
            200,
        )

    def test_the_games_masters_chat_grant_is_free(self):
        """``services.add_equipment`` from a Game Master's session is a gift."""
        services.add_equipment(self.character.pk, "Broadsword", 1, actor=self.owner)
        self.character.refresh_from_db()
        self.assertEqual(self.character.purse, Decimal(100))

    def test_a_players_chat_session_buys_the_item(self):
        """A player's session: add_equipment is a purchase (the #757 review)."""
        services.add_equipment(
            self.character.pk, "Broadsword", 1, actor=self.owner, purchase=True
        )
        self.character.refresh_from_db()
        self.assertEqual(self.character.purse, Decimal(20))
        with self.assertRaises(services.CharacterChangeError):
            services.add_equipment(
                self.character.pk, "Chainmail", 1, actor=self.owner, purchase=True
            )
        # Something the catalog does not sell is bought at its stated price,
        # as the sheet buys it, and refused with none stated (#927).
        with self.assertRaisesMessage(
            services.CharacterChangeError, refusal_words().purse.custom_item_price
        ):
            services.add_equipment(
                self.character.pk, "Vane's key", 1, actor=self.owner, purchase=True
            )
        services.add_equipment(
            self.character.pk,
            "Vane's key",
            1,
            actor=self.owner,
            purchase=True,
            price=Decimal(5),
        )
        self.character.refresh_from_db()
        self.assertEqual(self.character.purse, Decimal(15))
        self.assertEqual(len(self.character.equipment), 2)

    def test_the_sheet_shows_the_running_purse(self):
        page = self.client.get(reverse("characters:edit", args=[self.character.pk]))
        self.assertContains(page, f'<strong id="purse">{escape(held("100"))}</strong>')
        self.assertContains(page, 'id="equipment-purse"')
        self.assertNotContains(page, "Move coin (Game Master)")


class GameMasterCoinTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user("purse_owner")
        self.character = create_character(self.owner, name="Granted", starting_coin=10)
        self.url = reverse("characters:move_coin", args=[self.character.pk])

    def test_a_player_cannot_give_themselves_coin(self):
        self.login_as(self.owner)
        response = self.post_json(self.url, {"amount": "500"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Character.objects.get(pk=self.character.pk).purse, Decimal(10))

    def test_a_game_master_gives_and_takes_coin_with_a_reason(self):
        self.login_as(self.create_dm())
        given = self.post_json(self.url, {"amount": "25.5", "reason": "the reward"})
        self.assertEqual(given.json()["purse"], held("35.50"))
        taken = self.post_json(self.url, {"amount": "-35.50"})
        self.assertEqual(taken.json()["purse"], held("0"))
        overdrawn = self.post_json(self.url, {"amount": "-1"})
        self.assertEqual(overdrawn.status_code, 400)
        self.character.refresh_from_db()
        self.assertIn("the reward", self.character.history[-2]["new"])
        for bad in ("lots", "0", "NaN"):
            self.assertEqual(self.post_json(self.url, {"amount": bad}).status_code, 400)


class ConsultGameMasterPurseTest(BaseTestCase):
    """wealth.md's Rich row is "Consult GM": #749 stores that as a null
    starting coin. It counts as no coin (the #757 review ruling),
    and the purse says "Consult GM"."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("rich")
        self.character = create_character(
            self.owner, name="Rich", money_earned=0, starting_coin=None
        )

    def test_the_purse_reads_consult_gm_and_holds_nothing(self):
        self.assertEqual(self.character.purse, Decimal(0))
        self.assertEqual(format_purse(self.character), refusal_words().purse.consult_gm)

    def test_nothing_is_bought_until_coin_is_given_or_recorded(self):
        with self.assertRaises(PurseError) as refused:
            charge_for_catalog_item(self.character, "dagger", 1)
        self.assertIn("Consult GM", str(refused.exception))
        self.assertEqual(self.character.coin_adjustment, 0)
        charge_for_catalog_item(self.character, "dagger", 1, free=True)

    def test_coin_a_game_master_gives_can_be_spent(self):
        line = move_coin(self.character, Decimal(50), reason="a Game Master's grant")
        words = refusal_words().purse
        self.assertEqual(
            line,
            words.received.format(
                coin="50", reason="a Game Master's grant", purse=held_and_unknown("50")
            ),
        )
        charge_for_catalog_item(self.character, "dagger", 1)
        self.assertEqual(format_purse(self.character), held_and_unknown("40"))
        self.character.starting_coin = 200
        self.assertEqual(format_purse(self.character), held("240"))

    def test_the_sheet_says_consult_gm(self):
        self.login_as(self.owner)
        page = self.client.get(reverse("characters:edit", args=[self.character.pk]))
        self.assertContains(
            page,
            f'<strong id="purse">{escape(refusal_words().purse.consult_gm)}</strong>',
        )

    def test_the_markdown_export_says_consult_gm(self):
        from character_export.export_utils import generate_character_markdown

        markdown = generate_character_markdown(self.character)
        self.assertIn(f"- **Purse:** {refusal_words().purse.consult_gm}\n", markdown)
