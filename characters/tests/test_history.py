"""Tests for lightweight per-field revision history (issue #27 Option A)."""

from characters.models import MAX_HISTORY_ENTRIES, Character
from common.test_helpers import BaseTestCase


class CharacterHistoryTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user("history-user")
        self.character = Character.objects.create(
            user=self.user,
            name="Versioned Hero",
            strength=10,
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
            appearance_roll="3",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=7,
        )

    def _update(self, field, value):
        return self.post_json(
            f"/tarmar/characters/{self.character.pk}/update-field/",
            {"field": field, "value": value},
        )

    def test_each_field_change_appends_entry(self):
        self.login_as(self.user)
        # A field the owner may set (issue #748: strength is a Game Master's).
        self._update("height_roll", "5")
        self._update("name", "Renamed Hero")
        self.character.refresh_from_db()
        history = self.character.history
        self.assertEqual(len(history), 2)

        first, second = history
        self.assertEqual(first["field"], "height_roll")
        self.assertEqual(first["old"], "3")
        self.assertEqual(first["new"], "5")
        self.assertEqual(first["by"], self.user.username)
        self.assertIn("ts", first)

        self.assertEqual(second["field"], "name")
        self.assertEqual(second["old"], "Versioned Hero")
        self.assertEqual(second["new"], "Renamed Hero")

    def test_unchanged_value_does_not_record_entry(self):
        self.login_as(self.user)
        self._update("height_roll", "3")  # same as current
        self.character.refresh_from_db()
        self.assertEqual(self.character.history, [])

    def test_history_is_capped_at_max_entries(self):
        # Seed history near the cap, then push one over.
        seeded = [
            {"ts": "t", "field": "f", "old": "x", "new": "y", "by": "seed"}
            for _ in range(MAX_HISTORY_ENTRIES)
        ]
        self.character.history = list(seeded)
        self.character.save()

        self.login_as(self.user)
        self._update("height_roll", "5")
        self.character.refresh_from_db()
        # Still capped — oldest entry was evicted.
        self.assertEqual(len(self.character.history), MAX_HISTORY_ENTRIES)
        self.assertEqual(self.character.history[-1]["field"], "height_roll")
        # First seeded entry is gone (FIFO eviction).
        self.assertEqual(self.character.history[0], seeded[1])

    def test_dm_edit_attributed_to_dm_username(self):
        dm = self.create_dm("history-dm")
        self.login_as(dm)
        self._update("strength", 13)
        self.character.refresh_from_db()
        self.assertEqual(self.character.history[-1]["by"], dm.username)

    def test_edit_page_shows_recent_changes_panel(self):
        # Make a change so the panel has something to render.
        self.login_as(self.user)
        self._update("name", "Marked Fourteen")
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recent Changes")
        # Field name + new value both appear.
        body = response.content.decode()
        self.assertIn("name", body)
        self.assertIn("Marked Fourteen", body)

    def test_edit_page_hides_panel_when_no_history(self):
        """With no history the panel is hidden.

        This used to assert the panel was left out of the page altogether,
        which was part of issue #792: a change made without a reload could
        not show the first line. It is rendered hidden now, and shown by the
        change's response.
        """
        self.login_as(self.user)
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        self.assertContains(
            response,
            '<div id="recent-changes" class="stats-section history-section" hidden>',
        )

    def test_history_size_validator_rejects_oversized_list(self):
        from django.core.exceptions import ValidationError

        from characters.models import validate_history_size

        too_many = [{"ts": "t"} for _ in range(MAX_HISTORY_ENTRIES + 1)]
        with self.assertRaises(ValidationError):
            validate_history_size(too_many)

    def test_history_size_validator_accepts_legal_list(self):
        from characters.models import validate_history_size

        # Should not raise.
        validate_history_size([{"ts": "t"}] * MAX_HISTORY_ENTRIES)

    def test_recent_history_is_newest_first_and_limited_to_ten(self):
        # Append 15 changes, verify only newest 10 show up newest-first.
        self.login_as(self.user)
        for new_value in range(11, 26):
            self._update("name", f"Hero {new_value}")
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        recent = response.context["recent_history"]
        self.assertEqual(len(recent), 10)
        # Newest entry is "25", oldest in the slice is "16".
        # HistoryEntry since #700: the stored ISO string is parsed so the
        # table can render it in the reader's own timezone.
        self.assertEqual(recent[0].new_value, "Hero 25")
        self.assertEqual(recent[-1].new_value, "Hero 16")
        self.assertIsNotNone(recent[0].when)
