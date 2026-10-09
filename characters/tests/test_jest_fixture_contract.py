"""The Jest fixtures describe the real character editor (#338 item 26).

character-edit.test.js and attack-panel.test.js build the page they test from
hand-copied markup (pageHtml, panelHtml), because Jest has no Django. A copy
nothing checks goes stale the day the template renames an element: the suite
keeps passing against markup the page no longer has. This test reads every
id those fixtures use and requires the rendered editor to carry it, so a
rename fails here instead of silently.
"""

import re
from pathlib import Path

from django.conf import settings
from django.urls import reverse

from common.test_helpers import BaseTestCase, create_character

JEST_DIRECTORY = Path(settings.BASE_DIR) / "static" / "js" / "__tests__"

# (Jest file, name of the function that returns the copied markup)
FIXTURES = (
    ("character-edit.test.js", "pageHtml"),
    ("attack-panel.test.js", "panelHtml"),
)


def fixture_ids(file_name: str, function_name: str) -> set[str]:
    """The element ids in one fixture function's markup."""
    source = (JEST_DIRECTORY / file_name).read_text()
    match = re.search(
        rf"^function {function_name}\(.*?^\}}", source, re.MULTILINE | re.DOTALL
    )
    assert match is not None, f"{file_name} has no {function_name}()"
    return set(re.findall(r'\bid="([A-Za-z][\w-]*)"', match.group(0)))


class JestFixtureContractTest(BaseTestCase):
    def rendered_editor_pages(self) -> str:
        """The editor in each state the fixtures draw from, concatenated.

        An owner whose living character carries a catalog weapon and has free
        skill and spell points (the attack form, the add-skill control, the
        spell-point readout); an owner of a character with none of those (the
        no-weapons line); and a DM looking at a living and a dead character
        (Mark Dead, Revive).
        """
        owner = self.create_user("contract-owner")
        armed = create_character(
            owner, name="Armed", free_skill_points=3, free_spell_points=2
        )
        armed.equipment = [
            {"item_id": "broadsword", "category": "weapons", "equipped": True}
        ]
        armed.save()
        unarmed = create_character(owner, name="Unarmed")
        pages = []
        self.login_as(owner)
        for character in (armed, unarmed):
            pages.append(
                self.client.get(reverse("characters:edit", args=[character.pk]))
            )

        # A DM can mark a character dead or revive it (test_mark_dead.py). Log
        # the owner out first: logging a second user in on the same client
        # flushes the session, passphrase-gate flag and all.
        manager = self.create_dm("contract-dm")
        fallen = create_character(
            owner, name="Fallen", is_alive=False, death_cause="Wolves"
        )
        self.logout()
        self.login_as(manager)
        for character in (armed, fallen):
            pages.append(
                self.client.get(reverse("characters:edit", args=[character.pk]))
            )

        for response in pages:
            self.assertEqual(response.status_code, 200)
        return "\n".join(response.content.decode() for response in pages)

    def test_every_id_the_fixtures_use_is_on_the_real_page(self):
        html = self.rendered_editor_pages()
        rendered_ids = set(re.findall(r'\bid="([A-Za-z][\w-]*)"', html))
        for file_name, function_name in FIXTURES:
            with self.subTest(fixture=f"{file_name}:{function_name}"):
                ids = fixture_ids(file_name, function_name)
                self.assertGreater(len(ids), 5)
                self.assertEqual(sorted(ids - rendered_ids), [])
