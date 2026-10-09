"""The character sheet takes its character to the Games Master (issue #828).

The sheet offered exports and delete and no way to play: the player had to find
"Game Master" in the menu, then the Characters tab and its picker. The sheet
now carries a control that attaches this character to a new session or one of
the viewer's own, over the existing ``gm:characters_attach`` endpoint, and
renders only where that endpoint would take the character.
"""

import re

from django.template import Context, Template
from django.urls import reverse

from characters.models import Character
from common.test_helpers import BaseTestCase, create_character

from .attach import attach_refusal, attachable_characters
from .models import GMSession

PLAY_FORM = 'class="play-with-gm"'


class SheetOffersTheGamesMasterTest(BaseTestCase):
    """Where the control renders, and what it offers."""

    def setUp(self) -> None:
        super().setUp()
        self.player = self.create_user("sheet-player")
        self.character = create_character(self.player, name="Wren")

    def sheet_html(self, user, character=None) -> str:
        self.logout()
        self.login_as(user)
        target = character or self.character
        response = self.client.get(reverse("characters:edit", args=[target.pk]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_owner_is_offered_a_new_session_or_one_of_their_own(self) -> None:
        mine = GMSession.objects.create(user=self.player, name="The Barrow")
        GMSession.objects.create(user=self.create_user("stranger"), name="Theirs")

        html = self.sheet_html(self.player)

        self.assertIn(PLAY_FORM, html)
        self.assertIn(f'data-character-id="{self.character.pk}"', html)
        self.assertIn(f'data-attach-url="{reverse("gm:characters_attach")}"', html)
        self.assertIn(f'data-chat-url="{reverse("gm:chat")}"', html)
        self.assertIn('<option value="">A new session</option>', html)
        self.assertIn(f'<option value="{mine.pk}">The Barrow</option>', html)
        self.assertNotIn("Theirs", html)

    def test_a_session_already_holding_the_character_says_so(self) -> None:
        session = GMSession.objects.create(user=self.player, name="Underway")
        session.characters.add(self.character)

        html = self.sheet_html(self.player)

        self.assertIn(
            f'<option value="{session.pk}">Underway (already playing here)</option>',
            html,
        )

    def test_the_session_choice_has_a_label(self) -> None:
        html = self.sheet_html(self.player)

        select_id = re.search(r'<select id="([\w-]+)" name="session_id"', html)
        self.assertIsNotNone(select_id)
        assert select_id is not None
        self.assertIn(f'<label for="{select_id.group(1)}">', html)

    def test_an_incomplete_character_is_not_offered(self) -> None:
        unfinished = create_character(self.player, name="Half", fatigue_roll=None)

        self.assertNotIn(PLAY_FORM, self.sheet_html(self.player, unfinished))

    def test_a_dead_character_is_not_offered(self) -> None:
        fallen = create_character(self.player, name="Fallen", is_alive=False)

        self.assertNotIn(PLAY_FORM, self.sheet_html(self.player, fallen))

    def test_a_reader_of_a_public_character_is_not_offered(self) -> None:
        self.character.is_public = True
        self.character.save()

        html = self.sheet_html(self.create_user("reader"))

        self.assertIn("Wren", html)
        self.assertNotIn(PLAY_FORM, html)

    def test_a_game_master_is_offered_their_own_sessions(self) -> None:
        game_master = self.create_dm("sheet-gm")
        GMSession.objects.create(user=game_master, name="GM table")
        GMSession.objects.create(user=self.player, name="Player table")

        html = self.sheet_html(game_master)

        self.assertIn(PLAY_FORM, html)
        self.assertIn("GM table", html)
        self.assertNotIn("Player table", html)

    def test_the_endpoint_the_control_posts_to_starts_a_session(self) -> None:
        """The control's request, as the page's script sends it."""
        self.login_as(self.player)

        response = self.post_json(
            reverse("gm:characters_attach"),
            {"session_id": None, "character_id": self.character.pk},
        )

        self.assertEqual(response.status_code, 200)
        session = GMSession.objects.get(pk=response.json()["session_id"])
        self.assertEqual(session.user, self.player)
        self.assertEqual(list(session.characters.all()), [self.character])


class TheControlRendersWithoutARequestTest(BaseTestCase):
    """The tag takes the viewer, so a repaint with no request can render it."""

    def test_a_request_less_render_offers_the_viewers_sessions(self) -> None:
        player = self.create_user("fragment-player")
        character = create_character(player, name="Wren")
        GMSession.objects.create(user=player, name="The Barrow")

        html = Template(
            "{% load gm_play %}{% play_with_game_master character viewer %}"
        ).render(Context({"character": character, "viewer": player}))

        self.assertIn(f'id="play-with-gm-{character.pk}"', html)
        self.assertIn("The Barrow", html)

    def test_a_refused_character_keeps_an_empty_slot(self) -> None:
        """The stable id stays, so a repaint can fill the slot once the
        character becomes playable (sheetstale #764)."""
        player = self.create_user("slot-player")
        unfinished = create_character(player, name="Half", fatigue_roll=None)

        html = Template(
            "{% load gm_play %}{% play_with_game_master character viewer %}"
        ).render(Context({"character": unfinished, "viewer": player}))

        self.assertIn(f'id="play-with-gm-{unfinished.pk}"', html)
        self.assertNotIn("<form", html)

    def test_the_form_waits_hidden_for_its_script(self) -> None:
        """The endpoint speaks JSON only: without the script there is no
        control, rather than one that silently reloads the page."""
        player = self.create_user("hidden-player")
        character = create_character(player, name="Wren")

        html = Template(
            "{% load gm_play %}{% play_with_game_master character viewer %}"
        ).render(Context({"character": character, "viewer": player}))

        form = re.search(r"<form[^>]*>", html)
        assert form is not None
        self.assertRegex(form.group(0), r"\shidden>$")


class ThePickerAndTheEndpointAgreeTest(BaseTestCase):
    """``attachable_characters`` lists exactly what ``attach_refusal`` takes."""

    def test_over_a_mixed_set_for_a_player_and_a_game_master(self) -> None:
        player = self.create_user("mixed-player")
        other = self.create_user("mixed-other")
        game_master = self.create_dm("mixed-gm")
        for owner in (player, other, game_master):
            create_character(owner, name=f"{owner.username} ready")
            create_character(owner, name=f"{owner.username} public", is_public=True)
            create_character(owner, name=f"{owner.username} dead", is_alive=False)
            create_character(owner, name=f"{owner.username} gone", is_deleted=True)
            create_character(
                owner, name=f"{owner.username} unfinished", fatigue_roll=None
            )
            create_character(owner, name=f"{owner.username} unrolled", strength=None)

        for viewer in (player, game_master):
            with self.subTest(viewer=viewer.username):
                accepted = {
                    character.pk
                    for character in Character.objects.all()
                    if attach_refusal(viewer, character) is None
                }
                listed = set(attachable_characters(viewer).values_list("pk", flat=True))
                self.assertEqual(listed, accepted)
                self.assertTrue(accepted)

    def test_the_characters_tab_picker_lists_them(self) -> None:
        player = self.create_user("picker-player")
        ready = create_character(player, name="Ready")
        create_character(player, name="Fallen", is_alive=False)
        self.login_as(player)

        response = self.client.get(reverse("gm:chat"))

        self.assertEqual(
            [character.pk for character in response.context["attachable_characters"]],
            [ready.pk],
        )
