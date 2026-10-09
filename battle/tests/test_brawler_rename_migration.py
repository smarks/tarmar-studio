"""The data migration that renames the brawler archetype in drafts (#867)."""

from importlib import import_module

from django.apps import apps as installed_apps

from battle.models import BattleDraft
from battle.services import start_draft
from common.test_helpers import BaseTestCase

MIGRATION = import_module("battle.migrations.0016_brawler_archetype_renamed")


class BrawlerRenameTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.draft = BattleDraft.objects.create(
            created_by=self.create_dm(),
            name="Old muster",
            roster_json=[
                {"kind": "fighter", "archetype": "unarmed brawler"},
                {"kind": "fighter", "archetype": "archer"},
                {"kind": "beast", "slug": "unarmed brawler"},
            ],
        )

    def test_forwards_renames_the_fighters_only_and_the_draft_starts(self):
        MIGRATION.forwards(installed_apps, None)
        self.draft.refresh_from_db()
        self.assertEqual(
            self.draft.roster_json,
            [
                {"kind": "fighter", "archetype": "brawler"},
                {"kind": "fighter", "archetype": "archer"},
                {"kind": "beast", "slug": "unarmed brawler"},
            ],
        )
        self.draft.roster_json = self.draft.roster_json[:2]
        self.draft.save()
        battle = start_draft(self.draft)
        archetypes = [entry["archetype"] for entry in battle.state_json["combatants"]]
        self.assertEqual(archetypes, ["brawler", "archer"])

    def test_backwards_restores_the_old_name(self):
        MIGRATION.forwards(installed_apps, None)
        MIGRATION.backwards(installed_apps, None)
        self.draft.refresh_from_db()
        self.assertEqual(
            self.draft.roster_json[0],
            {"kind": "fighter", "archetype": "unarmed brawler"},
        )
