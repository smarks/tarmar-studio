"""Tests for the GM session's characters (issue #341).

Covers the ``GMSession.characters`` link, the attach/detach endpoints, the
Characters tab of the chat page, and the hidden ``kind: "sheet"`` history
entry that carries the characters' sheets to the model -- including that it
stays stable across turns so Ollama's prompt cache holds.
"""

import json
import re
from unittest.mock import patch

from common.test_helpers import BaseTestCase, create_character

from .character_sheet import CharacterSheet
from .models import GMSession
from .refusal_words import refusal_words
from .retrieval import CANON_KIND, SHEET_KIND, is_hidden_message
from .testing import gm_replies

ATTACH_URL = "/tarmar/gm/characters/attach/"
DETACH_URL = "/tarmar/gm/characters/detach/"
STREAM_URL = "/tarmar/gm/stream/"


class GMSessionCharactersModelTest(BaseTestCase):
    """The many-to-many link and what it hides from readers."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.session = GMSession.objects.create(name="Quest", user=self.user)

    def test_attached_characters_are_ordered_by_id(self):
        second = create_character(self.user, name="Zed")
        first = create_character(self.user, name="Anna")
        self.session.characters.add(second, first)
        self.assertEqual(
            list(self.session.attached_characters()),
            sorted([first, second], key=lambda character: character.pk),
        )
        self.assertEqual(list(first.gm_sessions.all()), [self.session])

    def test_soft_deleted_characters_drop_out_but_keep_their_link(self):
        character = create_character(self.user, name="Ghost")
        self.session.characters.add(character)
        character.is_deleted = True
        character.save()
        self.assertEqual(list(self.session.attached_characters()), [])
        self.assertEqual(list(self.session.characters.all()), [character])

    def test_hard_deleting_a_character_removes_the_link(self):
        character = create_character(self.user, name="Gone")
        self.session.characters.add(character)
        character.delete()
        self.assertEqual(self.session.characters.count(), 0)

    def test_deleting_the_session_keeps_the_character(self):
        character = create_character(self.user, name="Kept")
        self.session.characters.add(character)
        self.session.delete()
        character.refresh_from_db()
        self.assertEqual(character.gm_sessions.count(), 0)

    def test_message_count_excludes_sheet_entries(self):
        self.session.history = [
            {"role": "user", "content": "sheet text", "kind": SHEET_KIND},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        self.session.save()
        self.assertEqual(self.session.message_count, 2)

    def test_hidden_kinds(self):
        self.assertTrue(is_hidden_message({"kind": CANON_KIND}))
        self.assertTrue(is_hidden_message({"kind": SHEET_KIND}))
        self.assertFalse(is_hidden_message({"role": "user", "content": "hi"}))


class CharacterSheetTest(BaseTestCase):
    """The snapshot both the panel and the model's block are built from."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.character = create_character(
            self.user,
            name="Aldwin",
            strength=16,
            # Aged 68, which aging.md gives STR -2 and DEX -2, so this sheet
            # reads STR 14 and DEX 7 -- set through age, which is what the
            # penalties are reconciled from (issue #680).
            dexterity=9,
            age=68,
            skills={"sword": 3, "arcane": 2},
            free_skill_points=2,
            free_spell_points=4,
            equipment=[
                {"name": "Sword", "quantity": 1, "equipped": True, "in_pack": False},
                {"name": "Rope", "quantity": 2, "equipped": False, "in_pack": True},
            ],
            money_earned=30,
        )

    def test_snapshot_fields_come_from_the_model(self):
        sheet = CharacterSheet.from_character(self.character)
        self.assertEqual(sheet.pk, self.character.pk)
        self.assertEqual(sheet.name, "Aldwin")
        self.assertTrue(sheet.is_alive)
        strength, dexterity = sheet.attributes[0], sheet.attributes[1]
        self.assertEqual(
            (strength.label, strength.base, strength.effective), ("STR", 16, 14)
        )
        self.assertEqual(strength.modifier, 1, "modifier follows the effective value")
        self.assertEqual((dexterity.effective, dexterity.modifier), (7, -1))
        self.assertEqual(
            [line.label for line in sheet.attributes],
            ["STR", "DEX", "INT", "WIS", "CON", "CHR"],
        )
        self.assertEqual(sheet.fatigue, self.character.fatigue)
        self.assertEqual(sheet.body, self.character.body)
        self.assertEqual(sheet.mana, 4, "the Mana Pool is the free spell points")
        self.assertEqual(sheet.physical, self.character.physical)
        self.assertEqual(sheet.mental, self.character.mental)
        self.assertEqual([line.name for line in sheet.skills], ["arcane", "sword"])
        self.assertEqual(sheet.skills[1].points, 3)
        self.assertEqual(sheet.free_skill_points, 2)
        self.assertEqual(
            [
                (line.name, line.quantity, line.equipped, line.in_pack)
                for line in sheet.equipment
            ],
            [("Sword", 1, True, False), ("Rope", 2, False, True)],
        )
        self.assertEqual(sheet.wealth_level, "Moderate")
        self.assertEqual(sheet.starting_coin, 100)
        self.assertEqual(sheet.money_earned, 30)
        self.assertEqual((sheet.total_xp, sheet.level, sheet.age), (0, 1, 68))
        self.assertEqual(sheet.experience_years, 0)


class GMCharacterEndpointsTest(BaseTestCase):
    """attach/detach: login-required, CSRF-bearing JSON, owner-scoped."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.other = self.create_user(username="other")
        self.login_as(self.user)
        self.character = create_character(self.user, name="Mine")
        self.session = GMSession.objects.create(name="Quest", user=self.user)

    def _post(self, url: str, body: dict):
        return self.client.post(url, json.dumps(body), content_type="application/json")

    def test_attach_to_existing_session(self):
        response = self._post(
            ATTACH_URL,
            {"session_id": self.session.pk, "character_id": self.character.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "session_id": self.session.pk,
                "characters": [{"id": self.character.pk, "name": "Mine"}],
                # The Battle tab's warning as it now stands, which the page
                # repaints from this answer instead of reloading (issue #15).
                "bare_handed": (
                    "Mine carries no weapon and would fight bare-handed. Buy gear "
                    "on the character's sheet before the fight."
                ),
            },
        )
        self.assertEqual(list(self.session.characters.all()), [self.character])

    def test_attach_without_a_session_creates_one(self):
        response = self._post(ATTACH_URL, {"character_id": self.character.pk})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        session = GMSession.objects.get(pk=payload["session_id"])
        self.assertEqual(session.user, self.user)
        self.assertEqual(session.name, "New Session")
        self.assertEqual(list(session.characters.all()), [self.character])

    def test_attach_twice_is_a_no_op(self):
        for _ in range(2):
            response = self._post(
                ATTACH_URL,
                {"session_id": self.session.pk, "character_id": self.character.pk},
            )
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.session.characters.count(), 1)

    def test_attach_rejects_someone_elses_character_as_not_found(self):
        foreign = create_character(self.other, name="Theirs")
        response = self._post(
            ATTACH_URL, {"session_id": self.session.pk, "character_id": foreign.pk}
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(), {"error": refusal_words().views.character_not_found}
        )

    def test_dm_may_attach_a_character_they_can_edit(self):
        dm = self.create_dm()
        # Switching users starts a fresh client session, which drops the
        # passphrase-gate flag setUp planted; logout() re-plants it.
        self.logout()
        self.login_as(dm)
        dm_session = GMSession.objects.create(name="DM", user=dm)
        response = self._post(
            ATTACH_URL, {"session_id": dm_session.pk, "character_id": self.character.pk}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(dm_session.characters.all()), [self.character])

    def test_attach_rejects_a_deleted_character(self):
        self.character.is_deleted = True
        self.character.save()
        response = self._post(
            ATTACH_URL,
            {"session_id": self.session.pk, "character_id": self.character.pk},
        )
        self.assertEqual(response.status_code, 404)

    def test_attach_rejects_a_dead_character(self):
        self.character.is_alive = False
        self.character.save()
        response = self._post(
            ATTACH_URL,
            {"session_id": self.session.pk, "character_id": self.character.pk},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": refusal_words().attach.dead})

    def test_attach_rejects_a_missing_or_malformed_character_id(self):
        missing = self._post(
            ATTACH_URL, {"session_id": self.session.pk, "character_id": 99999}
        )
        self.assertEqual(missing.status_code, 404)
        malformed = self._post(
            ATTACH_URL, {"session_id": self.session.pk, "character_id": "abc"}
        )
        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(
            malformed.json(), {"error": refusal_words().views.invalid_character_id}
        )
        listed = self._post(
            ATTACH_URL, {"session_id": self.session.pk, "character_id": [1]}
        )
        self.assertEqual(listed.status_code, 400)

    def test_attach_rejects_someone_elses_session_as_not_found(self):
        foreign_session = GMSession.objects.create(name="Theirs", user=self.other)
        response = self._post(
            ATTACH_URL,
            {"session_id": foreign_session.pk, "character_id": self.character.pk},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(), {"error": refusal_words().views.session_not_found}
        )
        self.assertEqual(foreign_session.characters.count(), 0)

    def test_attach_rejects_a_malformed_session_id(self):
        response = self._post(
            ATTACH_URL, {"session_id": "abc", "character_id": self.character.pk}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(), {"error": refusal_words().views.invalid_session_id}
        )

    def test_attach_rejects_malformed_json(self):
        response = self.client.post(ATTACH_URL, "{", content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_endpoints_require_login_and_post(self):
        self.logout()
        for url in (ATTACH_URL, DETACH_URL):
            response = self._post(
                url, {"session_id": self.session.pk, "character_id": 1}
            )
            self.assertEqual(response.status_code, 302, url)
        self.login_as(self.user)
        for url in (ATTACH_URL, DETACH_URL):
            self.assertEqual(self.client.get(url).status_code, 405, url)

    def test_detach(self):
        self.session.characters.add(self.character)
        response = self._post(
            DETACH_URL,
            {"session_id": self.session.pk, "character_id": self.character.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "session_id": self.session.pk,
                "characters": [],
                # Nobody bare-handed is attached any more (issue #15).
                "bare_handed": "",
            },
        )
        self.assertEqual(self.session.characters.count(), 0)

    def test_detach_a_character_that_died_or_was_deleted_since(self):
        self.session.characters.add(self.character)
        self.character.is_alive = False
        self.character.is_deleted = True
        self.character.save()
        response = self._post(
            DETACH_URL,
            {"session_id": self.session.pk, "character_id": self.character.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.session.characters.count(), 0)

    def test_detach_of_an_unattached_character_is_a_no_op(self):
        response = self._post(
            DETACH_URL,
            {"session_id": self.session.pk, "character_id": self.character.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_detach_needs_a_session(self):
        response = self._post(DETACH_URL, {"character_id": self.character.pk})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(), {"error": refusal_words().views.no_session_id}
        )

    def test_detach_rejects_foreign_or_malformed_session(self):
        foreign_session = GMSession.objects.create(name="Theirs", user=self.other)
        foreign_session.characters.add(self.character)
        response = self._post(
            DETACH_URL,
            {"session_id": foreign_session.pk, "character_id": self.character.pk},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(foreign_session.characters.count(), 1)
        response = self._post(
            DETACH_URL, {"session_id": "abc", "character_id": self.character.pk}
        )
        self.assertEqual(response.status_code, 400)

    def test_detach_rejects_a_malformed_character_id(self):
        for bad_id in ("abc", [1]):
            response = self._post(
                DETACH_URL, {"session_id": self.session.pk, "character_id": bad_id}
            )
            self.assertEqual(response.status_code, 400, bad_id)
            self.assertEqual(
                response.json(), {"error": refusal_words().views.invalid_character_id}
            )

    def test_detach_rejects_malformed_json(self):
        response = self.client.post(DETACH_URL, "{", content_type="application/json")
        self.assertEqual(response.status_code, 400)


class GMChatPagePanelTest(BaseTestCase):
    """The Characters tab as the chat page renders it."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)
        self.session = GMSession.objects.create(name="Quest", user=self.user)

    def _panel_config(self, response) -> dict:
        match = re.search(
            r'<script id="gm-panel-config" type="application/json">(.*?)</script>',
            response.content.decode(),
            re.DOTALL,
        )
        self.assertIsNotNone(match, "the page has no #gm-panel-config block")
        assert match is not None
        return json.loads(match.group(1))

    def test_panel_markup_and_scripts(self):
        response = self.client.get("/tarmar/gm/")
        self.assertContains(response, '<aside class="gm-panel" id="gm-panel"')
        self.assertContains(response, 'data-panel-tab="characters"')
        self.assertContains(response, 'id="gm-panel-characters"')
        self.assertContains(response, 'id="gm-panel-toggle"')
        content = response.content.decode()
        order = [
            content.index("js/chat.js"),
            content.index("js/gm-panel-layout.js"),
            content.index("js/gm-panel.js"),
            content.index("js/gm-panel-drafts.js"),
            content.index("js/gm-panel-characters.js"),
        ]
        self.assertEqual(order, sorted(order), "panel scripts load after chat.js")
        self.assertLess(content.index('id="gm-panel-config"'), order[2])

    def test_panel_declares_where_each_pane_pops_out_to(self):
        """Issue #351: the pane's "own window" control reads its markup."""
        character = create_character(self.user, name="Aldwin")
        self.session.characters.add(character)
        content = self.client.get(
            f"/tarmar/gm/?session={self.session.pk}"
        ).content.decode()
        # Each sheet is its own target: the pane opens the one on show.
        self.assertIn("data-popout-item", content)
        self.assertIn(f'data-popout-url="/tarmar/characters/{character.pk}/"', content)
        self.assertIn('data-popout-label="Aldwin"', content)
        # With no fight on the board the Battle pane says why it cannot.
        self.assertIn('data-popout-empty="No fight on the board to open yet"', content)

    def test_panel_config_for_a_new_and_an_existing_session(self):
        config = self._panel_config(self.client.get("/tarmar/gm/"))
        self.assertEqual(
            set(config),
            {
                "attachUrl",
                "detachUrl",
                "sessionId",
                "charactersPanelUrlTemplate",
                # The Campaign tab refetches the same way (issue #579).
                "campaignPanelUrlTemplate",
            },
        )
        self.assertEqual(
            config["charactersPanelUrlTemplate"],
            "/tarmar/gm/sessions/{session_id}/characters/",
        )
        self.assertEqual(config["attachUrl"], ATTACH_URL)
        self.assertEqual(config["detachUrl"], DETACH_URL)
        self.assertIsNone(config["sessionId"])
        config = self._panel_config(
            self.client.get(f"/tarmar/gm/?session={self.session.pk}")
        )
        self.assertEqual(config["sessionId"], self.session.pk)

    def test_picker_lists_only_the_users_living_unattached_characters(self):
        create_character(self.user, name="Bran")
        attached = create_character(self.user, name="Aldwin")
        dead = create_character(self.user, name="Dead", is_alive=False)
        deleted = create_character(self.user, name="Deleted", is_deleted=True)
        other = self.create_user(username="other")
        theirs = create_character(other, name="Theirs")
        self.session.characters.add(attached)

        response = self.client.get(f"/tarmar/gm/?session={self.session.pk}")
        self.assertEqual(
            [character.name for character in response.context["attachable_characters"]],
            ["Bran"],
        )
        self.assertContains(
            response,
            f'<option value="{response.context["attachable_characters"][0].pk}">Bran</option>',
        )
        for name in (dead.name, deleted.name, theirs.name):
            self.assertNotContains(response, f">{name}</option>")
        self.assertNotContains(
            response,
            ">Aldwin</option>",
            msg_prefix="attached characters leave the picker",
        )

    def test_picker_is_disabled_with_nothing_to_attach(self):
        response = self.client.get("/tarmar/gm/")
        self.assertContains(response, "No characters to attach")
        self.assertContains(response, 'id="gm-character-attach" disabled')

    def test_attached_sheets_render_from_the_model(self):
        character = create_character(
            self.user,
            name="Aldwin",
            strength=16,
            skills={"sword": 3},
            equipment=[{"name": "Sword", "quantity": 1, "equipped": True}],
        )
        self.session.characters.add(character)
        response = self.client.get(f"/tarmar/gm/?session={self.session.pk}")
        sheets = response.context["panel_characters"]
        self.assertEqual([sheet.name for sheet in sheets], ["Aldwin"])
        self.assertContains(response, f'data-character-id="{character.pk}"')
        self.assertContains(response, f'href="/tarmar/characters/{character.pk}/"')
        # Current against maximum (issue #751); an unhurt character is full.
        self.assertContains(
            response,
            f'<dd class="gm-sheet-fatigue">{character.fatigue}/{character.fatigue}</dd>',
        )
        self.assertContains(
            response,
            f'<dd class="gm-sheet-body">{character.body}/{character.body}</dd>',
        )
        self.assertContains(response, "Unhurt")
        self.assertContains(response, "<th>STR</th>")
        self.assertContains(response, '<span class="gm-sheet-value">16</span>')
        self.assertContains(response, "Sword")
        self.assertContains(response, "equipped")
        self.assertContains(response, "Level ")
        self.assertContains(response, "Alive")
        self.assertNotContains(response, "gm-sheets-empty")

    def test_an_attached_caster_shows_the_mana_owed(self):
        # Borrowing (tarmar-engine #28): the debt beside the pool, worded as
        # the record's own line words it.
        character = create_character(
            self.user, name="Aldwin", free_spell_points=6, mana_spent=6, mana_owed=2
        )
        self.session.characters.add(character)
        response = self.client.get(f"/tarmar/gm/?session={self.session.pk}")
        self.assertContains(response, '<dd class="gm-sheet-mana">0/6, owes 2</dd>')
        character.mana_owed = 0
        character.save()
        response = self.client.get(f"/tarmar/gm/?session={self.session.pk}")
        self.assertContains(response, '<dd class="gm-sheet-mana">0/6</dd>')

    def test_dead_and_aged_characters_show_as_such(self):
        character = create_character(
            self.user,
            name="Old",
            strength=16,
            age=68,  # aging.md: STR -2 at 68, so the sheet shows 14 with (16)
            is_alive=False,
            death_cause="a troll",
        )
        self.session.characters.add(character)
        response = self.client.get(f"/tarmar/gm/?session={self.session.pk}")
        self.assertContains(response, "gm-sheet-dead")
        self.assertContains(response, "Dead — a troll")
        self.assertContains(
            response, '<span class="gm-sheet-base" title="Before aging">(16)</span>'
        )

    def test_empty_session_shows_the_empty_note(self):
        response = self.client.get(f"/tarmar/gm/?session={self.session.pk}")
        self.assertContains(response, 'id="gm-sheets-empty"')
        self.assertEqual(response.context["panel_characters"], [])

    def test_sheet_entries_are_hidden_from_the_transcript(self):
        self.session.history = [
            {"role": "user", "content": "SECRET SHEET BLOCK", "kind": SHEET_KIND},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        self.session.save()
        response = self.client.get(f"/tarmar/gm/?session={self.session.pk}")
        self.assertEqual(
            [message["content"] for message in response.context["history"]],
            ["Hello", "Hi"],
        )
        self.assertNotContains(response, "SECRET SHEET BLOCK")
        self.assertEqual(response.context["sessions"][0].message_count, 2)


class GameMasterPickerTest(BaseTestCase):
    """A Game Master's picker offers his players' characters (issue #759).

    The attach endpoint has always let a Game Master attach any character he
    may edit, and ``/fight`` needs one attached; the picker listed only his
    own, so the table's party could reach his session only by a hand-built
    request.
    """

    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm(username="gamemaster")
        self.login_as(self.game_master)
        self.session = GMSession.objects.create(name="Table", user=self.game_master)
        self.mira = create_character(self.create_user(username="mira"), name="Mira")
        self.ansel = create_character(self.create_user(username="ansel"), name="Ansel")
        self.npc = create_character(self.game_master, name="Kthal")

    def test_the_picker_lists_every_players_characters_grouped_by_owner(self):
        response = self.client.get(f"/tarmar/gm/?session={self.session.pk}")

        groups = [
            (group.owner, [character.name for character in group.characters])
            for group in response.context["attachable_groups"]
        ]
        self.assertEqual(
            groups,
            [("Your characters", ["Kthal"]), ("ansel", ["Ansel"]), ("mira", ["Mira"])],
        )
        content = response.content.decode()
        self.assertIn('<optgroup label="mira">', content)
        self.assertIn(f'<option value="{self.mira.pk}">Mira</option>', content)
        self.assertNotIn("No characters to attach", content)

    def test_the_dead_the_deleted_and_the_attached_stay_out(self):
        create_character(self.mira.user, name="Fallen", is_alive=False)
        create_character(self.mira.user, name="Gone", is_deleted=True)
        self.session.characters.add(self.ansel)

        response = self.client.get(f"/tarmar/gm/?session={self.session.pk}")

        names = [
            character.name for character in response.context["attachable_characters"]
        ]
        self.assertEqual(names, ["Kthal", "Mira"])

    def test_a_listed_players_character_can_be_attached(self):
        response = self.client.post(
            ATTACH_URL,
            json.dumps({"session_id": self.session.pk, "character_id": self.mira.pk}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(self.session.attached_characters()), [self.mira])

    def test_a_player_still_sees_only_their_own(self):
        player = self.mira.user
        self.logout()
        self.login_as(player)
        own = GMSession.objects.create(name="Mine", user=player)

        response = self.client.get(f"/tarmar/gm/?session={own.pk}")

        groups = response.context["attachable_groups"]
        self.assertEqual(
            [(group.owner, [c.name for c in group.characters]) for group in groups],
            [("", ["Mira"])],
        )
        self.assertNotContains(response, "<optgroup")
        self.assertNotContains(response, ">Ansel</option>")


class GMStreamStateBlockTest(BaseTestCase):
    """Attached characters reach the model in each call's state block (#590).

    The old hidden sheet block was filed into the history; the state block is
    rebuilt from the records every call, so an edit shows at once and the
    history the prefix cache holds never changes because of it.
    """

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)
        self.character = create_character(self.user, name="Aldwin", strength=16)
        self.session = GMSession.objects.create(name="Quest", user=self.user)
        self.session.characters.add(self.character)

    def _turn(self, message: str) -> list[list[dict[str, str]]]:
        stream = gm_replies(f"reply to {message}")
        with patch(
            "gm.turn_engine.model.ollama_client.stream_chat", side_effect=stream
        ):
            response = self.client.post(
                STREAM_URL,
                json.dumps({"session_id": self.session.pk, "message": message}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)
            b"".join(response.streaming_content)
        return stream.calls

    def test_the_state_block_names_the_attached_character_by_id(self):
        plan_call, narrate_call = self._turn("I draw my sword")
        for call in (plan_call, narrate_call):
            self.assertIn(
                f"- id {self.character.pk}: Aldwin — STR 16", call[-1]["content"]
            )
        self.assertEqual([m["role"] for m in plan_call], ["system", "user"])

    def test_an_edit_shows_at_once_and_leaves_the_history_alone(self):
        first_plan, _ = self._turn("I draw my sword")
        self.character.strength = 17
        self.character.save()
        second_plan, _ = self._turn("I swing")

        self.assertEqual(second_plan[: len(first_plan) - 1], first_plan[:-1])
        self.assertIn("STR 17", second_plan[-1]["content"])
        self.session.refresh_from_db()
        self.assertFalse(any(entry.get("kind") for entry in self.session.history))


class CatalogEquipmentIsNamedInTheSheetTest(BaseTestCase):
    """The sheet the model is handed names what the character carries (#718).

    A catalog equipment entry stores only ``item_id``, ``quantity``,
    ``in_pack`` and ``notes`` -- name, weight and category live in the
    catalog and are resolved through it. ``CharacterSheet`` read ``name``
    straight off the stored entry, so every bought item read "Unknown" in
    the Games Master's Characters pane and in the hidden ``kind: "sheet"``
    history entry the model is given for that character. Only hand-entered
    custom items, which do store a name, came out right.
    """

    def test_a_bought_item_is_named_from_the_catalog(self):
        character = create_character(self.create_user(), name="Wren")
        character.equipment = [{"item_id": "dagger", "quantity": 2, "in_pack": True}]
        character.save(update_fields=["equipment"])

        sheet = CharacterSheet.from_character(character)

        self.assertEqual(
            [(line.name, line.quantity, line.in_pack) for line in sheet.equipment],
            [("Dagger", 2, True)],
        )

    def test_a_custom_item_still_reads_as_it_was_written(self):
        character = create_character(self.create_user(), name="Wren")
        character.equipment = [{"name": "Vane's key", "quantity": 1, "equipped": True}]
        character.save(update_fields=["equipment"])

        sheet = CharacterSheet.from_character(character)

        self.assertEqual(
            [(line.name, line.equipped) for line in sheet.equipment],
            [("Vane's key", True)],
        )

    def test_nothing_the_model_is_handed_says_unknown(self):
        character = create_character(self.create_user(), name="Wren")
        character.equipment = [
            {"item_id": "dagger", "quantity": 1},
            {"item_id": "sling", "quantity": 1},
        ]
        character.save(update_fields=["equipment"])

        names = [
            line.name for line in CharacterSheet.from_character(character).equipment
        ]

        self.assertNotIn("Unknown", names)
        self.assertIn("Dagger", names)
