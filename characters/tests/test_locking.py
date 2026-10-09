"""Row-locking tests for the character read-modify-write endpoints (issue #134).

Every endpoint that reads a character, mutates a JSON column, and writes the
whole row back has to hold a row lock across that sequence, or two concurrent
requests lose one another's edits. SQLite (the test database) is a no-op for
``SELECT ... FOR UPDATE``, so these tests assert the *structure* of the write
instead of racing real connections: the character is fetched through
``select_for_update()``, and that fetch happens inside the view's own
transaction rather than in autocommit.
"""

from contextlib import contextmanager
from unittest.mock import patch

from django.db import connection
from django.db.models.query import QuerySet

from characters.models import Character
from common.test_helpers import BaseTestCase


@contextmanager
def record_locked_fetches():
    """Record ``(model, atomic_depth)`` for each select_for_update() in the block.

    ``connection.savepoint_ids`` grows by one per nested ``atomic()``, so the
    depth recorded at call time tells us whether the locked fetch ran inside a
    transaction the view opened itself. That depth comparison assumes the caller
    is a django ``TestCase`` (as ``BaseTestCase`` is), which already holds the
    test inside an atomic block — under ``TransactionTestCase`` the view's block
    would be the outermost one and the recorded depth would stay 0.
    """
    locked_fetches = []
    original_select_for_update = QuerySet.select_for_update

    def spy(self, *args, **kwargs):
        locked_fetches.append((self.model, len(connection.savepoint_ids)))
        return original_select_for_update(self, *args, **kwargs)

    with patch.object(QuerySet, "select_for_update", spy):
        yield locked_fetches


class LockedCharacterWriteTest(BaseTestCase):
    """Each read-modify-write endpoint locks the character row it rewrites."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("lock-owner")
        self.dungeon_master = self.create_dm("lock-dm")
        self.character = Character.objects.create(
            user=self.owner,
            name="Locked Hero",
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

    def assert_locked_character_fetch(self, locked_fetches, outer_depth):
        """The request fetched Character under a lock, inside its own atomic block."""
        character_fetches = [
            depth for model, depth in locked_fetches if model is Character
        ]
        self.assertTrue(
            character_fetches,
            "endpoint did not fetch the character with select_for_update()",
        )
        for depth in character_fetches:
            self.assertGreater(
                depth,
                outer_depth,
                "locked fetch ran outside the view's transaction.atomic() block",
            )

    def post_locked(self, url, payload=None):
        """POST and return ``(response, locked_fetches, outer_depth)``."""
        outer_depth = len(connection.savepoint_ids)
        with record_locked_fetches() as locked_fetches:
            if payload is None:
                response = self.client.post(url)
            else:
                response = self.post_json(url, payload)
        return response, locked_fetches, outer_depth

    # ---- equipment endpoints ----

    def test_equipment_add_locks_row(self):
        self.login_as(self.owner)
        response, locked_fetches, outer_depth = self.post_locked(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"item_id": "broadsword"},
        )
        self.assertEqual(response.status_code, 200)
        self.assert_locked_character_fetch(locked_fetches, outer_depth)

    def test_equipment_update_locks_row(self):
        self.character.equipment = [
            {"item_id": "torch", "quantity": 1, "in_pack": False, "notes": ""}
        ]
        self.character.save()
        self.login_as(self.owner)
        response, locked_fetches, outer_depth = self.post_locked(
            f"/tarmar/characters/{self.character.pk}/equipment/update/",
            {"index": 0, "quantity": 3},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["equipment"][0]["quantity"], 3)
        self.assert_locked_character_fetch(locked_fetches, outer_depth)

    def test_equipment_remove_locks_row(self):
        self.character.equipment = [
            {"item_id": "torch", "quantity": 1, "in_pack": False, "notes": ""}
        ]
        self.character.save()
        self.login_as(self.owner)
        response, locked_fetches, outer_depth = self.post_locked(
            f"/tarmar/characters/{self.character.pk}/equipment/remove/",
            {"index": 0},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["equipment"], [])
        self.assert_locked_character_fetch(locked_fetches, outer_depth)

    def test_equipment_bulk_remove_locks_row(self):
        self.character.equipment = [
            {"item_id": "torch", "quantity": 1, "in_pack": False, "notes": ""},
            {"item_id": "rope", "quantity": 1, "in_pack": False, "notes": ""},
        ]
        self.character.save()
        self.login_as(self.owner)
        response, locked_fetches, outer_depth = self.post_locked(
            f"/tarmar/characters/{self.character.pk}/equipment/bulk-remove/",
            {"indices": [0, 1]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["removed"], 2)
        self.assert_locked_character_fetch(locked_fetches, outer_depth)

    def test_equipment_toggle_pack_locks_row(self):
        self.login_as(self.owner)
        response, locked_fetches, outer_depth = self.post_locked(
            f"/tarmar/characters/{self.character.pk}/equipment/toggle-pack/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["pack_dropped"])
        self.assert_locked_character_fetch(locked_fetches, outer_depth)

    # ---- skills endpoint ----

    def test_rename_skill_locks_row(self):
        # A rename a player may make (issue #768): naming a Maker Skill's
        # craft. This used to rename Swordplay to Fencing, which moves points.
        self.character.skills = {"maker skill": 3}
        self.character.save()
        self.login_as(self.owner)
        response, locked_fetches, outer_depth = self.post_locked(
            f"/tarmar/characters/{self.character.pk}/rename-skill/",
            {"old_name": "Maker Skill", "new_name": "Maker Skill (Smith)"},
        )
        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        self.assertIn("maker skill (smith)", self.character.skills)
        self.assertNotIn("maker skill", self.character.skills)
        self.assert_locked_character_fetch(locked_fetches, outer_depth)

    def test_rename_skill_still_denies_other_users(self):
        """The lock did not change who may rename a skill."""
        self.character.skills = {"swordplay": 3}
        self.character.save()
        intruder = self.create_user("lock-intruder")
        self.login_as(intruder)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/rename-skill/",
            {"old_name": "Swordplay", "new_name": "Fencing"},
        )
        self.assertEqual(response.status_code, 403)

    # ---- mark-dead / revive ----

    def test_mark_dead_locks_row(self):
        self.login_as(self.dungeon_master)
        response, locked_fetches, outer_depth = self.post_locked(
            f"/tarmar/characters/{self.character.pk}/mark-dead/",
            {"cause": "Slain by goblin chief"},
        )
        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        self.assertFalse(self.character.is_alive)
        self.assert_locked_character_fetch(locked_fetches, outer_depth)

    def test_revive_locks_row(self):
        self.character.is_alive = False
        self.character.death_cause = "Slain by goblin chief"
        self.character.save()
        self.login_as(self.dungeon_master)
        response, locked_fetches, outer_depth = self.post_locked(
            f"/tarmar/characters/{self.character.pk}/revive/"
        )
        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        self.assertTrue(self.character.is_alive)
        self.assertEqual(self.character.death_cause, "")
        self.assert_locked_character_fetch(locked_fetches, outer_depth)

    # ---- delete / restore (issue #162) ----

    def test_delete_character_locks_row(self):
        """delete_character does a full-row save, so it locks like the rest.

        Without the lock, a concurrent equipment/skill write made under its
        own row lock could be clobbered by this save's stale snapshot of the
        other columns.
        """
        self.login_as(self.owner)
        response, locked_fetches, outer_depth = self.post_locked(
            f"/tarmar/characters/{self.character.pk}/delete/"
        )
        self.assertEqual(response.status_code, 302)
        self.character.refresh_from_db()
        self.assertTrue(self.character.is_deleted)
        self.assert_locked_character_fetch(locked_fetches, outer_depth)

    def test_delete_character_still_denies_other_users(self):
        """The lock is taken before the permission check, not instead of it."""
        self.login_as(self.create_user("lock-stranger"))
        response, _locked_fetches, _outer_depth = self.post_locked(
            f"/tarmar/characters/{self.character.pk}/delete/"
        )
        self.assertEqual(response.status_code, 403)
        self.character.refresh_from_db()
        self.assertFalse(self.character.is_deleted)

    def test_restore_character_locks_row(self):
        self.character.is_deleted = True
        self.character.deleted_at = None
        self.character.save()
        self.login_as(self.dungeon_master)
        response, locked_fetches, outer_depth = self.post_locked(
            f"/tarmar/characters/{self.character.pk}/restore/"
        )
        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        self.assertFalse(self.character.is_deleted)
        self.assert_locked_character_fetch(locked_fetches, outer_depth)

    def test_restore_character_still_denies_non_owner(self):
        """Restore is the owner's or a manager's (#339 item 15), so, like the
        other endpoints, its permission check reads the locked row and a
        stranger is refused without the character changing."""
        self.character.is_deleted = True
        self.character.deleted_at = None
        self.character.save()
        self.login_as(self.create_user("restore_stranger"))
        response, _locked_fetches, _outer_depth = self.post_locked(
            f"/tarmar/characters/{self.character.pk}/restore/"
        )
        self.assertEqual(response.status_code, 403)
        self.character.refresh_from_db()
        self.assertTrue(self.character.is_deleted)

    # ---- combat endpoint ----

    def test_resolve_attack_locks_row(self):
        """The attack endpoint appends to combat_log, so it locks like the rest.

        Two attacks resolved at once would otherwise each read the log, append
        one entry, and write the whole column back — losing one of them.
        """
        self.character.equipment = [{"item_id": "saber", "quantity": 1}]
        self.character.save()
        self.login_as(self.owner)
        response, locked_fetches, outer_depth = self.post_locked(
            f"/tarmar/characters/{self.character.pk}/attack/",
            {"weapon_index": 0, "armour_tier": "None"},
        )
        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        self.assertEqual(len(self.character.combat_log), 1)
        self.assert_locked_character_fetch(locked_fetches, outer_depth)

    def test_resolve_attack_still_denies_other_users(self):
        """The lock is taken before the permission check, not instead of it."""
        self.character.equipment = [{"item_id": "saber", "quantity": 1}]
        self.character.save()
        self.login_as(self.create_user("lock-stranger"))
        response, _locked_fetches, _outer_depth = self.post_locked(
            f"/tarmar/characters/{self.character.pk}/attack/",
            {"weapon_index": 0, "armour_tier": "None"},
        )
        self.assertEqual(response.status_code, 403)
        self.character.refresh_from_db()
        self.assertEqual(self.character.combat_log, [])

    def test_missing_character_still_404s(self):
        """Locking kept get_object_or_404 semantics (not a 500 or a JSON 404)."""
        self.login_as(self.owner)
        response = self.post_json(
            "/tarmar/characters/999999/equipment/add/", {"item_id": "broadsword"}
        )
        self.assertEqual(response.status_code, 404)


class CommittedStateEquipmentWriteTest(BaseTestCase):
    """The equipment write is built on state committed by other requests.

    This is *not* a lost-update reproduction: a real interleaving (a second
    writer committing between this request's read and its save) cannot be made
    to fail on SQLite, which ignores ``SELECT ... FOR UPDATE`` — the lock tests
    above are the ones that fail without the fix. What this pins is the weaker
    but still useful property, in the shape of the GM-stream test (0cbe205):
    another request's write commits before this one reads the row, and this
    request's update extends that committed state instead of an older snapshot.
    """

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("race-owner")
        self.character = Character.objects.create(
            user=self.owner,
            name="Contested Hero",
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
        self.login_as(self.owner)

    def test_add_builds_on_committed_state(self):
        from characters.views import equipment as views

        original_parse_json_body = views._parse_json_body

        def commit_other_add(request):
            """Another request's add commits just before this one reads the row."""
            concurrent = Character.objects.get(pk=self.character.pk)
            concurrent.equipment = [
                {"item_id": "torch", "quantity": 1, "in_pack": False, "notes": ""}
            ]
            concurrent.save()
            return original_parse_json_body(request)

        with patch.object(views, "_parse_json_body", commit_other_add):
            response = self.post_json(
                f"/tarmar/characters/{self.character.pk}/equipment/add/",
                {"item_id": "broadsword"},
            )

        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        item_ids = [item["item_id"] for item in self.character.equipment]
        self.assertEqual(item_ids, ["torch", "broadsword"])
