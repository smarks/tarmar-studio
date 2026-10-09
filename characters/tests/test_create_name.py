"""The creation page asks for the character's name (issue #786).

Creating a character never asked for a name, so every character was named
after its account ("testuser"), a second one got the same name, and the Games
Master chat, the battle roster and the battle log all called it that. Both
creation cards now carry a Name field. A player must fill it; the name is
checked by ``Character.clean_field_value``, the rule the sheet's rename and the
import already use. A Game Master's creation path is unchanged: they may still
leave it blank, and the character takes the account's name as it always did.
"""

import re

from django.urls import reverse

from characters.models import Character
from common.test_helpers import BaseTestCase

CREATE_URL = "/tarmar/characters/create/"

# 14 + 12 + 12 + 11 + 10 + 6 == 65.
LEGAL_BUY = {
    "strength": 14,
    "dexterity": 12,
    "intelligence": 12,
    "wisdom": 11,
    "constitution": 10,
    "charisma": 6,
}


class CreationPageAsksForANameTest(BaseTestCase):
    """What the page renders."""

    def test_both_cards_ask_a_player_for_a_required_name(self) -> None:
        self.login_as(self.create_user("namer"))

        html = self.client.get(CREATE_URL).content.decode()

        inputs = re.findall(r'<input type="text"[^>]*name="name"[^>]*>', html)
        self.assertEqual(len(inputs), 2)
        for field in inputs:
            with self.subTest(field=field):
                self.assertIn("required", field)
                self.assertIn('maxlength="100"', field)
                field_id = re.search(r'id="([\w-]+)"', field)
                assert field_id is not None
                self.assertIn(f'<label for="{field_id.group(1)}">', html)

    def test_a_game_master_may_leave_it_blank(self) -> None:
        self.login_as(self.create_dm("namer-gm"))

        html = self.client.get(CREATE_URL).content.decode()

        inputs = re.findall(r'<input type="text"[^>]*name="name"[^>]*>', html)
        self.assertEqual(len(inputs), 2)
        for field in inputs:
            self.assertNotIn("required", field)
        self.assertIn("Left blank, it takes your account", html)


class PlayerNamesTheirCharacterTest(BaseTestCase):
    """A player's creation takes the name they typed, and only a good one."""

    def setUp(self) -> None:
        super().setUp()
        self.player = self.create_user("naming-player")
        self.login_as(self.player)

    def test_a_rolled_character_takes_the_typed_name(self) -> None:
        response = self.client.post(CREATE_URL, {"method": "roll", "name": " Wren "})

        character = Character.objects.get(user=self.player)
        self.assertEqual(character.name, "Wren")
        self.assertRedirects(
            response,
            reverse("characters:edit", args=[character.pk]),
            fetch_redirect_response=False,
        )

    def test_a_bought_character_takes_the_typed_name(self) -> None:
        self.client.post(
            CREATE_URL, {"method": "point-buy", "name": "Aldwin", **LEGAL_BUY}
        )

        self.assertEqual(Character.objects.get(user=self.player).name, "Aldwin")

    def test_two_characters_may_share_a_name(self) -> None:
        """Names need not be unique (issue #571)."""
        for _ in range(2):
            self.client.post(CREATE_URL, {"method": "roll", "name": "Twin"})

        self.assertEqual(Character.objects.filter(name="Twin").count(), 2)

    def test_no_name_creates_nothing_and_says_why(self) -> None:
        for body in (
            {"method": "roll"},
            {"method": "roll", "name": "   "},
            {"method": "point-buy", "name": "", **LEGAL_BUY},
        ):
            with self.subTest(body=body):
                response = self.client.post(CREATE_URL, body)

                self.assertEqual(response.status_code, 400)
                self.assertContains(
                    response, "Give your character a name.", status_code=400
                )
        self.assertFalse(Character.objects.filter(user=self.player).exists())

    def test_a_name_the_sheet_would_refuse_is_refused_here(self) -> None:
        for name, reason in (
            ("x" * 101, "Name must be 100 characters or fewer"),
            ("Wren\U0001f525", "Name may contain letters, numbers, spaces"),
        ):
            with self.subTest(name=name):
                response = self.client.post(
                    CREATE_URL, {"method": "roll", "name": name}
                )

                self.assertContains(response, reason, status_code=400)
        self.assertFalse(Character.objects.filter(user=self.player).exists())

    def test_a_refused_submission_keeps_what_was_typed(self) -> None:
        response = self.client.post(
            CREATE_URL,
            {
                "method": "point-buy",
                "name": "Keeper",
                "visibility": "public",
                **{**LEGAL_BUY, "charisma": 5},
            },
        )

        self.assertEqual(response.status_code, 400)
        html = response.content.decode()
        self.assertIn('value="Keeper"', html)
        self.assertIn('value="public" checked', html)

    def test_the_battle_setup_round_trip_still_rides_through(self) -> None:
        setup_url = "/tarmar/battles/setup/1/"

        response = self.client.post(
            CREATE_URL, {"method": "roll", "name": "Recruit", "next": setup_url}
        )

        character = Character.objects.get(user=self.player)
        self.assertEqual(
            response.url,
            f"{reverse('characters:edit', args=[character.pk])}"
            "?next=/tarmar/battles/setup/1/",
        )


class GameMasterCreationIsUnchangedTest(BaseTestCase):
    """A Game Master's blank name is the account's name, as before #786."""

    def setUp(self) -> None:
        super().setUp()
        self.game_master = self.create_dm("naming-gm")
        self.login_as(self.game_master)

    def test_a_blank_name_takes_the_account_name(self) -> None:
        for body in (
            {"method": "roll"},
            {"method": "point-buy", "name": " ", **LEGAL_BUY},
        ):
            with self.subTest(body=body):
                response = self.client.post(CREATE_URL, body)

                self.assertEqual(response.status_code, 302)
        names = set(
            Character.objects.filter(user=self.game_master).values_list(
                "name", flat=True
            )
        )
        self.assertEqual(names, {"naming-gm"})

    def test_a_typed_name_is_used(self) -> None:
        self.client.post(CREATE_URL, {"method": "roll", "name": "Goblin chief"})

        self.assertEqual(
            Character.objects.get(user=self.game_master).name, "Goblin chief"
        )

    def test_a_bad_name_is_refused_for_a_game_master_too(self) -> None:
        response = self.client.post(CREATE_URL, {"method": "roll", "name": "x" * 101})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Character.objects.filter(user=self.game_master).exists())

    def test_the_off_budget_buy_still_stands(self) -> None:
        """The Game Master's #299 allowance rides beside the name."""
        response = self.client.post(
            CREATE_URL, {"method": "point-buy", **{**LEGAL_BUY, "charisma": 3}}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Character.objects.get(user=self.game_master).charisma, 3)
