"""Tests for the 65-point point-buy character creation option (issue #123).

The rules let a player either roll 3d6 per attribute or "divide 65 points
between the six core attributes". These cover the buy path, its server-side
validation, and that the rolled path still behaves exactly as it did.
"""

from characters.constants import CORE_ATTRIBUTE_FIELDS
from characters.dice import build_point_buy_character_stats, starting_coin_for_wealth
from characters.models import Character
from characters.rules import character_rules
from common.test_helpers import BaseTestCase

CREATE_URL = "/tarmar/characters/create/"

# A legal buy: 14 + 12 + 12 + 11 + 10 + 6 == 65.
VALID_BUY = {
    "strength": 14,
    "dexterity": 12,
    "intelligence": 12,
    "wisdom": 11,
    "constitution": 10,
    "charisma": 6,
}


def buy_post(**overrides):
    """A point-buy POST body, with any attribute overridden.

    It names the character: a player must (issue #786).
    """
    body = {"method": "point-buy", "name": "Bought", **VALID_BUY}
    body.update(overrides)
    return body


class CreationPageAccessTest(BaseTestCase):
    """The creation page is behind the login wall, like the rest of the app."""

    def test_create_page_requires_login(self):
        """Anonymous users are sent to the login page, and nothing is created."""
        response = self.client.get(CREATE_URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/tarmar/accounts/login/", response.url)
        self.assertEqual(Character.objects.count(), 0)

    def test_point_buy_post_requires_login(self):
        """An anonymous point-buy POST creates nothing."""
        response = self.client.post(CREATE_URL, buy_post())
        self.assertEqual(response.status_code, 302)
        self.assertIn("/tarmar/accounts/login/", response.url)
        self.assertEqual(Character.objects.count(), 0)


class PointBuyCreationTest(BaseTestCase):
    """Creating a character by dividing 65 points."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)

    def test_valid_buy_sums_to_the_budget(self):
        """The fixture buy is actually legal — guards the other tests."""
        self.assertEqual(
            sum(VALID_BUY.values()), character_rules().attributes.point_buy.total
        )

    def test_create_page_offers_both_options(self):
        """GET shows the roll option and the point-buy form."""
        response = self.client.get(CREATE_URL)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "characters/character_create.html")
        self.assertContains(response, "Roll 3d6")
        self.assertContains(response, str(character_rules().attributes.point_buy.total))
        self.assertContains(response, 'value="point-buy"')
        for field_name, label in CORE_ATTRIBUTE_FIELDS:
            self.assertContains(response, f'name="{field_name}"')
            self.assertContains(response, f">{label}</label>")
        # Nothing is created just by looking at the page.
        self.assertEqual(Character.objects.filter(user=self.user).count(), 0)

    def test_create_page_prefills_a_legal_default_buy(self):
        """The prefilled values already total the budget."""
        response = self.client.get(CREATE_URL)
        defaults = [
            attribute["value"] for attribute in response.context["core_attributes"]
        ]
        self.assertEqual(sum(defaults), character_rules().attributes.point_buy.total)
        for value in defaults:
            self.assertGreaterEqual(
                value, character_rules().attributes.point_buy.minimum
            )
            self.assertLessEqual(value, character_rules().attributes.point_buy.maximum)

    def test_valid_buy_creates_character_with_bought_attributes(self):
        """A legal buy stores exactly the submitted attributes."""
        response = self.client.post(CREATE_URL, buy_post())
        self.assertEqual(response.status_code, 302)

        character = Character.objects.get(user=self.user)
        self.assertIn(f"/tarmar/characters/{character.pk}/", response.url)
        for field_name, expected in VALID_BUY.items():
            self.assertEqual(getattr(character, field_name), expected)
        # The name the form gave, not the account's (issue #786).
        self.assertEqual(character.name, "Bought")

    def test_valid_buy_still_rolls_everything_else(self):
        """Height, weight, appearance, fatigue, provenance, location, wealth roll."""
        self.client.post(CREATE_URL, buy_post())
        character = Character.objects.get(user=self.user)

        self.assertGreaterEqual(character.fatigue_roll, 2)
        self.assertLessEqual(character.fatigue_roll, 12)
        for rolled_field in ("appearance_roll", "height_roll", "weight_roll"):
            self.assertTrue(getattr(character, rolled_field))
        self.assertTrue(character.provenance_class)
        self.assertTrue(character.provenance_detail)
        self.assertTrue(character.location)
        self.assertTrue(character.wealth_level)
        # Coin follows the wealth table, whatever the roll gave. Rich is "consult
        # GM" and legitimately starts at 0, so this can't assert a positive purse
        # without failing on roughly one run in twenty.
        if character.wealth_level == "Merchant":
            self.assertGreater(character.starting_coin, 100)  # 100 + d100
        else:
            self.assertEqual(
                character.starting_coin,
                starting_coin_for_wealth(character.wealth_level)[0],
            )

    def test_valid_buy_records_point_buy_in_the_dice_log(self):
        """The log stays line-per-stat, with point-buy where a 3d6 chain would be."""
        self.client.post(CREATE_URL, buy_post())
        character = Character.objects.get(user=self.user)

        self.assertIn("STR: point-buy = 14", character.dice_log)
        self.assertIn("CHR: point-buy = 6", character.dice_log)
        self.assertNotIn("3d6", character.dice_log)
        # The rolled sections are still logged the usual way.
        self.assertIn("Fatigue:", character.dice_log)
        self.assertIn("Wealth:", character.dice_log)
        self.assertIn("Height:", character.dice_log)

    def test_a_second_buy_shares_the_name(self):
        """Names need not be unique (issue #571); no .1 suffix is added."""
        self.client.post(CREATE_URL, buy_post())
        self.client.post(CREATE_URL, buy_post())
        names = sorted(
            Character.objects.filter(user=self.user).values_list("name", flat=True)
        )
        self.assertEqual(names, ["Bought", "Bought"])


class PointBuyValidationTest(BaseTestCase):
    """Server-side rejection of illegal buys. The JS is convenience only."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)

    def assert_rejected(self, post_body, expected_fragment):
        """POST is refused with 400, an on-page reason, and no character.

        The fragment is matched against the error string itself, not against the
        whole page. Matching the page would let a phrase that also appears in the
        static copy pass whether or not the error block ever rendered.
        """
        response = self.client.post(CREATE_URL, post_body)
        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, "characters/character_create.html")
        self.assertIn(expected_fragment, response.context["point_buy_error"])
        # And the block that carries it is actually on the page.
        self.assertContains(response, 'id="point-buy-server-error"', status_code=400)
        self.assertEqual(Character.objects.filter(user=self.user).count(), 0)
        return response

    def test_rejects_a_total_below_the_budget(self):
        """64 points is not 65."""
        self.assert_rejected(buy_post(charisma=5), "yours total 64")

    def test_rejects_a_total_above_the_budget(self):
        """66 points is not 65."""
        self.assert_rejected(buy_post(charisma=7), "yours total 66")

    def test_rejects_an_attribute_above_the_maximum(self):
        """19 is outside what 3d6 can produce."""
        self.assert_rejected(
            buy_post(strength=19, dexterity=7),
            f"STR must be between {character_rules().attributes.point_buy.minimum} and "
            f"{character_rules().attributes.point_buy.maximum}",
        )

    def test_rejects_an_attribute_below_the_minimum(self):
        """2 is outside what 3d6 can produce."""
        self.assert_rejected(
            buy_post(charisma=2, strength=18),
            f"CHR must be between {character_rules().attributes.point_buy.minimum} and "
            f"{character_rules().attributes.point_buy.maximum}",
        )

    def test_rejects_a_zero_attribute(self):
        """Zero is rejected by the range check, not silently coerced."""
        self.assert_rejected(
            buy_post(wisdom=0, strength=18, dexterity=17),
            f"WIS must be between {character_rules().attributes.point_buy.minimum} and "
            f"{character_rules().attributes.point_buy.maximum}",
        )

    def test_rejects_a_fractional_attribute(self):
        """A decimal is not a whole number; the client blocks it too."""
        self.assert_rejected(buy_post(strength="14.5"), "STR must be a whole")

    def test_rejects_a_non_numeric_attribute(self):
        """Garbage in a number field is a validation error, not a 500."""
        self.assert_rejected(buy_post(intelligence="eleven"), "INT must be a whole")

    def test_rejects_a_missing_attribute(self):
        """An omitted attribute is not defaulted."""
        body = buy_post()
        del body["constitution"]
        self.assert_rejected(body, "CON must be a whole")

    def test_rejects_an_unknown_creation_method(self):
        """A POST with no usable method creates nothing."""
        self.assert_rejected(
            {"method": "telepathy"},
            "The form did not say whether to roll the attributes or buy them",
        )

    def test_rejects_a_post_with_no_method(self):
        """A bare POST creates nothing."""
        self.assert_rejected(
            {}, "The form did not say whether to roll the attributes or buy them"
        )

    def test_method_error_is_not_the_pages_own_intro_copy(self):
        """Guards the assertions above from matching static page copy.

        The intro paragraph and the missing-method error must stay distinct, or
        the two tests above would pass whether or not the error ever rendered.
        """
        page = self.client.get(CREATE_URL).content.decode()
        error = self.client.post(CREATE_URL, {"method": "telepathy"}).context[
            "point_buy_error"
        ]
        self.assertNotIn(error, page)

    def test_rejected_buy_is_echoed_back_into_the_form(self):
        """The player's numbers survive the round trip so they can fix them."""
        response = self.client.post(CREATE_URL, buy_post(charisma=5))
        submitted = {
            attribute["field"]: attribute["value"]
            for attribute in response.context["core_attributes"]
        }
        self.assertEqual(submitted["strength"], "14")
        self.assertEqual(submitted["charisma"], "5")


class RollPathUnchangedTest(BaseTestCase):
    """The rolled path must behave exactly as it did before point-buy existed."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)

    def test_roll_creates_character_with_3d6_attributes(self):
        """Every core attribute lands in the 3d6 range."""
        response = self.client.post(CREATE_URL, {"method": "roll", "name": "Rolled"})
        self.assertEqual(response.status_code, 302)

        character = Character.objects.get(user=self.user)
        for field_name, _label in CORE_ATTRIBUTE_FIELDS:
            value = getattr(character, field_name)
            self.assertGreaterEqual(value, 3)
            self.assertLessEqual(value, 18)

    def test_roll_still_logs_the_3d6_chain(self):
        """The rolled dice log keeps its per-die detail and says nothing of buying."""
        self.client.post(CREATE_URL, {"method": "roll", "name": "Rolled"})
        character = Character.objects.get(user=self.user)

        self.assertIn("STR: 3d6: [", character.dice_log)
        self.assertIn("CHR: 3d6: [", character.dice_log)
        self.assertNotIn("point-buy", character.dice_log)

    def test_roll_fills_every_secondary_stat(self):
        """The non-attribute rolls are unaffected by the refactor."""
        self.client.post(CREATE_URL, {"method": "roll", "name": "Rolled"})
        character = Character.objects.get(user=self.user)

        self.assertGreaterEqual(character.fatigue_roll, 2)
        self.assertLessEqual(character.fatigue_roll, 12)
        self.assertTrue(character.provenance_class)
        self.assertTrue(character.location)
        self.assertTrue(character.wealth_level)


class BuildPointBuyStatsTest(BaseTestCase):
    """The stats builder itself, without going through a request."""

    def test_core_attributes_pass_through_untouched(self):
        stats = build_point_buy_character_stats(VALID_BUY)
        for field_name, expected in VALID_BUY.items():
            self.assertEqual(stats[field_name], expected)

    def test_produces_the_same_keys_as_the_rolled_path(self):
        from characters.dice import roll_all_character_stats

        self.assertEqual(
            set(build_point_buy_character_stats(VALID_BUY)),
            set(roll_all_character_stats()),
        )


class OffBudgetBuyForPrivilegedUsersTest(BaseTestCase):
    """Admins and DMs may buy off budget; the 65 points bind players only (#299).

    A DM building an NPC, a villain, or a set piece needs a character the
    65-point budget does not describe. For them the budget is advice, so the
    server takes the buy and records how far off it landed. The per-attribute
    3-18 range is a different rule and still binds everyone: it mirrors what
    3d6 can produce, and nothing here relaxes it.
    """

    def create_off_budget(self, user, **overrides):
        """Log `user` in and submit a buy that totals 80, not 65."""
        self.login_as(user)
        body = buy_post(strength=18, dexterity=18, intelligence=17, **overrides)
        return self.client.post(CREATE_URL, body)

    def test_the_off_budget_fixture_really_is_off_budget(self):
        """Guards every test below: 80 must not equal the budget."""
        body = buy_post(strength=18, dexterity=18, intelligence=17)
        total = sum(int(body[field]) for field, _label in CORE_ATTRIBUTE_FIELDS)
        self.assertEqual(total, 80)
        self.assertNotEqual(total, character_rules().attributes.point_buy.total)

    def test_a_dm_can_create_an_over_budget_character(self):
        dm = self.create_dm()

        response = self.create_off_budget(dm)

        self.assertEqual(response.status_code, 302)
        character = Character.objects.get(user=dm)
        self.assertEqual(character.strength, 18)
        self.assertEqual(character.charisma, 6)

    def test_an_admin_can_create_an_over_budget_character(self):
        admin = self.create_admin()

        response = self.create_off_budget(admin)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Character.objects.filter(user=admin).count(), 1)

    def test_a_privileged_user_can_also_buy_under_budget(self):
        """Under-spending is as legitimate as over-spending for an NPC."""
        dm = self.create_dm()
        self.login_as(dm)

        response = self.client.post(CREATE_URL, buy_post(charisma=3))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Character.objects.get(user=dm).charisma, 3)

    def test_a_plain_player_is_still_refused(self):
        """The budget still binds anyone without the role."""
        player = self.create_player()

        response = self.create_off_budget(player)

        self.assertEqual(response.status_code, 400)
        self.assertIn("yours total 80", response.context["point_buy_error"])
        self.assertEqual(Character.objects.filter(user=player).count(), 0)

    def test_the_off_budget_character_records_the_overspend_in_its_log(self):
        """The warning outlives the request: the sheet's dice log carries it."""
        dm = self.create_dm()

        self.create_off_budget(dm)

        character = Character.objects.get(user=dm)
        self.assertIn("STR: point-buy = 18", character.dice_log)
        self.assertIn("80", character.dice_log)
        self.assertIn(
            str(character_rules().attributes.point_buy.total), character.dice_log
        )
        self.assertIn("over", character.dice_log.lower())

    def test_an_under_budget_character_logs_it_as_under(self):
        dm = self.create_dm()
        self.login_as(dm)

        self.client.post(CREATE_URL, buy_post(charisma=3))

        character = Character.objects.get(user=dm)
        self.assertIn("under", character.dice_log.lower())

    def test_an_on_budget_buy_logs_no_off_budget_note(self):
        """The note must be absent when there is nothing to warn about."""
        dm = self.create_dm()
        self.login_as(dm)

        self.client.post(CREATE_URL, buy_post())

        character = Character.objects.get(user=dm)
        self.assertNotIn("over budget", character.dice_log.lower())
        self.assertNotIn("under budget", character.dice_log.lower())

    def test_the_attribute_range_still_binds_a_dm(self):
        """19 is outside 3d6's range; the role does not unlock that."""
        dm = self.create_dm()
        self.login_as(dm)

        response = self.client.post(CREATE_URL, buy_post(strength=19))

        self.assertEqual(response.status_code, 400)
        self.assertIn("STR must be between", response.context["point_buy_error"])
        self.assertEqual(Character.objects.filter(user=dm).count(), 0)

    def test_a_dm_still_cannot_submit_nonsense(self):
        """Relaxing the total does not relax whole-number parsing."""
        dm = self.create_dm()
        self.login_as(dm)

        response = self.client.post(CREATE_URL, buy_post(intelligence="eleven"))

        self.assertEqual(response.status_code, 400)
        self.assertIn("INT must be a whole", response.context["point_buy_error"])


class OffBudgetFormAffordanceTest(BaseTestCase):
    """The creation page tells the client which rule applies to this user."""

    def test_a_dm_sees_the_page_flagged_as_allowed_off_budget(self):
        self.login_as(self.create_dm())

        response = self.client.get(CREATE_URL)

        self.assertTrue(response.context["can_exceed_point_buy"])

    def test_an_admin_sees_the_page_flagged_as_allowed_off_budget(self):
        self.login_as(self.create_admin())

        response = self.client.get(CREATE_URL)

        self.assertTrue(response.context["can_exceed_point_buy"])

    def test_a_player_does_not(self):
        self.login_as(self.create_player())

        response = self.client.get(CREATE_URL)

        self.assertFalse(response.context["can_exceed_point_buy"])

    def test_the_warning_element_is_on_the_page_for_a_dm(self):
        """A permanent element the tally script writes into — never a toast."""
        self.login_as(self.create_dm())

        response = self.client.get(CREATE_URL)

        self.assertContains(response, 'id="point-buy-off-budget"')
