"""Luck, spent once per session per level (issue #861).

The skills glossary: "once per session per level, after seeing a roll's
result you may add this skill's level to it." The count is the session's
(``SessionCharacter.luck_spent``); a spend is a record in the character's
history. A player is held to the level; a Game Master may grant more.
"""

from django.urls import reverse

from characters.services import CharacterChangeError
from common.test_helpers import BaseTestCase, create_character

from .luck import LuckAccount, luck_accounts, luck_level, spend_luck
from .models import GMSession, SessionCharacter
from .refusal_words import refusal_words

# Three points buy Luck level 2 (1 + 2).
LUCK_LEVEL_TWO = {"luck": 3}


class SpendLuckTest(BaseTestCase):
    """The count, the refusal past the level, and the record."""

    def setUp(self) -> None:
        super().setUp()
        self.player = self.create_user(username="alice")
        self.character = create_character(
            self.player, name="Wren", skills=LUCK_LEVEL_TWO
        )
        self.session = GMSession.objects.create(user=self.player, name="The Barrow")
        self.session.characters.add(self.character)

    def test_the_level_is_the_skills(self) -> None:
        self.assertEqual(luck_level(self.character), 2)
        self.assertEqual(
            luck_accounts(self.session), {self.character.pk: LuckAccount(2, 0)}
        )

    def test_a_player_spends_up_to_the_level_and_no_further(self) -> None:
        first = spend_luck(
            self.session, self.character.pk, is_game_master=False, roll="the lockpick"
        )
        self.assertEqual(
            first, "spent Luck in The Barrow: +2 to the lockpick; 1 of 2 this session"
        )
        spend_luck(self.session, self.character.pk, is_game_master=False)
        with self.assertRaisesMessage(
            CharacterChangeError,
            refusal_words().luck.luck_spent.format(name=self.character.name, level=2),
        ):
            spend_luck(self.session, self.character.pk, is_game_master=False)
        self.assertEqual(luck_accounts(self.session)[self.character.pk].remaining, 0)
        self.character.refresh_from_db()
        lines = [
            entry["new"] for entry in self.character.history if entry["field"] == "luck"
        ]
        self.assertEqual(len(lines), 2)
        self.assertEqual(
            lines[1],
            ("spent Luck in The Barrow: +2 to the roll just made; 2 of 2 this session"),
        )
        self.assertEqual({entry["by"] for entry in self.character.history}, {"alice"})

    def test_the_count_is_per_session(self) -> None:
        spend_luck(self.session, self.character.pk, is_game_master=False)
        spend_luck(self.session, self.character.pk, is_game_master=False)
        other = GMSession.objects.create(user=self.player, name="Next Night")
        other.characters.add(self.character)
        spend_luck(other, self.character.pk, is_game_master=False)
        self.assertEqual(luck_accounts(other)[self.character.pk].spent, 1)

    def test_a_game_master_grants_past_the_level_and_the_line_says_so(self) -> None:
        for _use in range(2):
            spend_luck(self.session, self.character.pk, is_game_master=False)
        line = spend_luck(self.session, self.character.pk, is_game_master=True)
        self.assertTrue(
            line.endswith(
                "3 of 2 this session (past the level: the Game Master's grant)"
            )
        )

    def test_no_luck_or_no_seat_is_refused(self) -> None:
        plain = create_character(self.player, name="Plain")
        self.session.characters.add(plain)
        with self.assertRaisesMessage(
            CharacterChangeError, refusal_words().luck.no_luck.format(name="Plain")
        ):
            spend_luck(self.session, plain.pk, is_game_master=False)
        stranger = create_character(self.player, name="Elsewhere", skills={"luck": 1})
        with self.assertRaisesMessage(
            CharacterChangeError, refusal_words().luck.not_in_session
        ):
            spend_luck(self.session, stranger.pk, is_game_master=False)
        self.assertNotIn(plain.pk, luck_accounts(self.session))
        self.assertEqual(luck_accounts(None), {})


class SpendLuckEndpointTest(BaseTestCase):
    """The Characters tab's control."""

    def setUp(self) -> None:
        super().setUp()
        self.player = self.create_user(username="alice")
        self.character = create_character(
            self.player, name="Wren", skills=LUCK_LEVEL_TWO
        )
        self.session = GMSession.objects.create(user=self.player, name="The Barrow")
        self.session.characters.add(self.character)
        self.url = reverse("gm:spend_luck", args=[self.session.pk])
        self.login_as(self.player)

    def test_the_tab_shows_the_count_and_the_control(self) -> None:
        response = self.client.get(
            reverse("gm:characters_panel", args=[self.session.pk])
        )
        html = response.content.decode()
        self.assertIn("Luck +2: 0 of 2 used this session", html)
        self.assertIn(f'data-luck-url="{self.url}"', html)

    def test_a_spend_is_recorded_and_a_refusal_is_in_words(self) -> None:
        for _use in range(2):
            response = self.post_json(
                self.url, {"character_id": self.character.pk, "roll": "the climb"}
            )
            self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            SessionCharacter.objects.get(
                session=self.session, character=self.character
            ).luck_spent,
            2,
        )
        refused = self.post_json(self.url, {"character_id": self.character.pk})
        self.assertEqual(refused.status_code, 400)
        self.assertIn(
            refusal_words().luck.luck_spent.format(name=self.character.name, level=2),
            refused.json()["error"],
        )
        self.assertEqual(
            self.post_json(self.url, {"character_id": "x"}).status_code, 400
        )

    def test_only_the_sessions_owner_spends_in_it(self) -> None:
        self.logout()
        self.login_as(self.create_user(username="bob"))
        response = self.post_json(self.url, {"character_id": self.character.pk})
        self.assertEqual(response.status_code, 404)
