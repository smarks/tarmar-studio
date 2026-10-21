"""A Games Master session's characters are records from the first change (#571).

Replaces the JSON-draft identity tests: the twins give exactly two Character
rows named as stated, a rename keeps the row, a character the user already
owns is never touched or suffixed, and every change names its character by
record id (issue #590). No test reaches Ollama.
"""

import json
from importlib import import_module
from unittest.mock import patch

from django.apps import apps as installed_apps
from django.test import override_settings

from characters.models import Character
from common.test_helpers import BaseTestCase, create_character

from . import roster
from .fight import FightError, start_fight
from .models import GMSession, SessionCharacter
from .refusal_words import refusal_words
from .testing import add_character, gm_replies, recorded, run_actions
from .turn_engine.state import roster_lines

TWINS = json.dumps(
    {
        "actions": [
            {
                "action": "record_attributes",
                "new_character": {"name": "Twain"},
                "all": 18,
            },
            {
                "action": "record_attributes",
                "new_character": {"name": "Sister"},
                "all": 18,
            },
        ]
    }
)


@override_settings(GM_RETRIEVAL_ENABLED=False)
class TwinsAreTwoRecordsTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_admin()
        self.login_as(self.user)
        self.session = GMSession.objects.create(user=self.user, name="s", mode="aide")

    def play(self, message: str, plan: str, narration: str) -> list[dict]:
        """Play one turn; returns its procedure frames."""
        with patch(
            "gm.turn_engine.model.ollama_client.stream_chat",
            side_effect=gm_replies(narration, plans=[plan]),
        ):
            response = self.client.post(
                "/tarmar/gm/stream/",
                json.dumps({"session_id": self.session.pk, "message": message}),
                content_type="application/json",
            )
            body = b"".join(response.streaming_content).decode()
        done = json.loads(body.strip().splitlines()[-1][len("data: ") :])
        return done.get("procedures", [])

    def test_twins_give_exactly_two_character_rows_named_as_stated(self):
        procedures = self.play("Twins, Twain and Sister, all 18s", TWINS, "Done.")

        self.assertTrue(all(frame["ran"] for frame in procedures), procedures)
        rows = Character.objects.filter(user=self.user)
        self.assertEqual(
            sorted(rows.values_list("name", flat=True)), ["Sister", "Twain"]
        )
        self.assertEqual(
            sorted(self.session.characters.values_list("name", flat=True)),
            ["Sister", "Twain"],
        )
        self.assertFalse(
            any(name.endswith(".1") for name in rows.values_list("name", flat=True))
        )
        for row in rows:
            self.assertEqual(row.strength, 18)
            # The dice nobody chooses were rolled on the server at creation,
            # the Fatigue roll among them as the creation page rolls it
            # (#859). "Not complete" here pinned the older design, which
            # left the Fatigue roll for a later plan.
            self.assertTrue(row.appearance_roll and row.height_roll and row.weight_roll)
            self.assertIsNotNone(row.fatigue_roll)
            self.assertTrue(row.is_complete)

    def test_a_second_turn_changes_the_same_rows(self):
        self.play("Twins", TWINS, "Done.")
        ids = set(Character.objects.values_list("pk", flat=True))
        twain = Character.objects.get(name="Twain")

        self.play(
            "Roll Twain's Fatigue",
            json.dumps(
                {"actions": [{"action": "roll_fatigue", "character_id": twain.pk}]}
            ),
            "Rolled.",
        )

        self.assertEqual(set(Character.objects.values_list("pk", flat=True)), ids)
        twain.refresh_from_db()
        self.assertIsNotNone(twain.fatigue_roll)
        self.assertTrue(twain.is_complete)

    def test_a_pre_existing_twain_is_untouched_and_gets_no_suffix(self):
        older = create_character(self.user, name="Twain", strength=9)

        self.play("Twins", TWINS, "Done.")

        older.refresh_from_db()
        self.assertEqual(older.strength, 9)
        self.assertNotIn(older, self.session.characters.all())
        self.assertEqual(Character.objects.filter(name="Twain").count(), 2)
        self.assertFalse(Character.objects.filter(name__endswith=".1").exists())

    def test_the_panel_shows_each_row_with_what_it_still_needs(self):
        # Stated strength alone, so each still needs its other attributes.
        # The Fatigue roll this test used to count is rolled as a character
        # is made (#859); "needs Fatigue roll" pinned the older design.
        self.play(
            "Twins, Twain and Sister, strength 18",
            json.dumps(
                {
                    "actions": [
                        {
                            "action": "record_attributes",
                            "new_character": {"name": name},
                            "scores": {"strength": 18},
                        }
                        for name in ("Twain", "Sister")
                    ]
                }
            ),
            "Done.",
        )

        page = self.client.get(f"/tarmar/gm/?session={self.session.pk}")

        self.assertContains(page, "needs attributes", count=2)
        self.assertNotContains(page, "needs Fatigue roll")
        for row in Character.objects.all():
            self.assertContains(page, f'data-character-id="{row.pk}"')


class RenameKeepsTheRowTest(BaseTestCase):
    def test_a_rename_keeps_the_row_id_and_the_label(self):
        session = GMSession.objects.create(user=self.create_admin(), name="s")
        run_actions(
            session,
            {
                "action": "record_attributes",
                "new_character": {"label": "the brother"},
                "all": 12,
            },
        )
        (entry,) = roster.roster(session)

        (result,) = run_actions(
            session,
            {
                "action": "rename_character",
                "character_id": entry.character.pk,
                "name": "Twain",
            },
        )

        (renamed,) = roster.roster(session)
        self.assertEqual(renamed.character.pk, entry.character.pk)
        self.assertEqual(renamed.character.name, "Twain")
        self.assertEqual(renamed.label, "the brother")
        self.assertEqual(result.note, "Twain (the brother): named Twain")


class RosterTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.session = GMSession.objects.create(user=self.create_user(), name="s")

    def test_a_game_masters_overspend_is_kept_on_the_label(self):
        session = GMSession.objects.create(user=self.create_admin(), name="gm")
        character = add_character(session, recorded("Twain"))

        run_actions(
            session,
            {
                "action": "spend_skill_points",
                "character_id": character.pk,
                "skill": "sword",
                "points": 2,
            },
        )

        link = SessionCharacter.objects.get(session=session)
        self.assertEqual(link.game_master_skill_points, 2)
        (entry,) = roster.roster(session)
        self.assertEqual(entry.ledger().skill_points.from_game_master, 2)

    def test_the_state_block_lists_picked_characters_beside_the_ones_built_here(self):
        built = add_character(self.session, recorded("Twain"))
        picked = create_character(self.session.user, name="Picked")
        self.session.characters.add(picked)

        lines = {line.character_id: line for line in roster_lines(self.session)}

        self.assertEqual(set(lines), {built.pk, picked.pk})
        self.assertEqual(lines[picked.pk].name, "Picked")

    def test_a_character_started_here_has_its_background_rolled(self):
        """The session rolls origin, provenance and wealth when it starts the
        character (#754), so its player has no second roll of them (#749)."""
        character = roster.start_character(self.session, "Twain")

        self.assertTrue(character.provenance_recorded)
        self.assertTrue(character.location_recorded)
        self.assertTrue(character.wealth_recorded)
        self.assertIn("Provenance: d100=", character.dice_log)
        self.assertIn("Location: d100=", character.dice_log)
        self.assertIn("Wealth: d100=", character.dice_log)

    def test_only_attached_characters_are_listed(self):
        create_character(self.session.user, name="Twain")
        self.assertEqual(roster_lines(self.session), [])


class IncompleteCharactersDoNotFightTest(BaseTestCase):
    def test_a_fight_seats_only_complete_characters(self):
        session = GMSession.objects.create(user=self.create_user(), name="s")
        add_character(session, recorded("Twain"))

        with self.assertRaisesMessage(
            FightError, refusal_words().fight.attach_living_character
        ):
            start_fight(session, foes_text="two wolves")


class ResetMigrationTest(BaseTestCase):
    """``gm.0013_reset_before_character_records`` is a no-op now.

    Production ran the reset on 2026-09-16. Its row-count guard then left every
    development database with more data unable to migrate past it, so it was
    emptied; running it must delete nothing.
    """

    def test_applying_it_deletes_nothing(self):
        migration = import_module("gm.migrations.0013_reset_before_character_records")
        user = self.create_user()
        session = GMSession.objects.create(user=user, name="s")
        add_character(session, recorded("Twain"))
        for number in range(7):
            create_character(user, name=f"Hero {number}")

        for operation in migration.Migration.operations:
            operation.code(installed_apps, None)

        self.assertTrue(GMSession.objects.filter(pk=session.pk).exists())
        self.assertEqual(Character.objects.count(), 8)


class CharactersPanelFragmentTest(BaseTestCase):
    """The Characters tab's live fragment (issue #571)."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)
        self.session = GMSession.objects.create(user=self.user, name="s")

    def url(self, session_id: int) -> str:
        return f"/tarmar/gm/sessions/{session_id}/characters/"

    def test_it_renders_the_characters_the_session_is_building(self):
        add_character(self.session, recorded("Twain"))

        response = self.client.get(self.url(self.session.pk))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="gm-draft-ledger"')
        self.assertContains(response, "Twain")
        self.assertContains(response, "needs Fatigue roll")
        self.assertContains(response, 'id="gm-character-picker"')
        self.assertNotContains(response, "<section")

    def test_it_matches_what_the_page_renders(self):
        add_character(self.session, recorded("Twain"))

        fragment = self.client.get(self.url(self.session.pk)).content.decode()
        page = self.client.get(f"/tarmar/gm/?session={self.session.pk}")

        self.assertIn(fragment.strip(), page.content.decode())

    def test_another_users_session_is_not_found(self):
        other = GMSession.objects.create(user=self.create_user("other"), name="o")

        self.assertEqual(self.client.get(self.url(other.pk)).status_code, 404)

    def test_it_needs_a_login(self):
        self.logout()

        response = self.client.get(self.url(self.session.pk))

        self.assertEqual(response.status_code, 302)
