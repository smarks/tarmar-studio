"""Drift guard: the committed fixture is exactly what the translation layer
produces from the committed source stats + descriptions.

If this fails, someone changed the translation rules, the source data, or the
descriptions without re-running ``manage.py build_beast_fixture`` — or edited
the fixture by hand, which is never correct.
"""

import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from bestiary.management.commands.build_beast_fixture import (
    DESCRIPTIONS,
    FIXTURE,
    SOURCE_STATS,
    render_fixture,
)
from bestiary.models import Beast


class FixtureDriftGuardTest(SimpleTestCase):
    def test_committed_fixture_matches_translation_output(self):
        self.assertEqual(
            FIXTURE.read_text(),
            render_fixture(),
            "bestiary/fixtures/beasts.json is out of date — run "
            "`manage.py build_beast_fixture` and commit the result.",
        )

    def test_every_creature_has_an_original_description(self):
        entries = json.loads(SOURCE_STATS.read_text())
        descriptions = json.loads(DESCRIPTIONS.read_text())
        for entry in entries:
            self.assertIn(entry["name"], descriptions)
            self.assertGreater(
                len(descriptions[entry["name"]]),
                40,
                f"Description for {entry['name']} is suspiciously short",
            )

    def test_source_pages_are_cited(self):
        for record in json.loads(FIXTURE.read_text()):
            self.assertIsNotNone(record["fields"]["source_page"])


class FixtureLoadsTest(TestCase):
    def test_loaddata_loads_every_beast(self):
        call_command("loaddata", "beasts", verbosity=0)
        expected = len(json.loads(FIXTURE.read_text()))
        self.assertEqual(Beast.objects.count(), expected)
        self.assertFalse(Beast.objects.filter(is_generated=True).exists())


class MissingDescriptionGuardTest(SimpleTestCase):
    def test_build_refuses_a_creature_without_a_description(self):
        from types import SimpleNamespace
        from unittest import mock

        from django.core.management.base import CommandError

        from bestiary.management.commands import build_beast_fixture

        entries = json.loads(SOURCE_STATS.read_text())
        descriptions = json.loads(DESCRIPTIONS.read_text())
        del descriptions[entries[0]["name"]]
        stub = SimpleNamespace(read_text=lambda: json.dumps(descriptions))
        with (
            mock.patch.object(build_beast_fixture, "DESCRIPTIONS", stub),
            self.assertRaises(CommandError),
        ):
            build_beast_fixture.build_fixture_records()


class BuildCommandTest(SimpleTestCase):
    def test_command_reports_count_and_is_idempotent(self):
        """The command writes a temporary copy, never the committed fixture.

        ``write_text`` truncates before it writes, so running the command on
        ``bestiary/fixtures/beasts.json`` let a parallel worker reading it
        (the drift guard, the loader test, the catalog migration) see an
        empty or partial file (issue #747). The copy starts as the committed
        fixture, so an unchanged copy is the same idempotency check.
        """
        from bestiary.management.commands import build_beast_fixture

        committed = FIXTURE.read_text()
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / "beasts.json"
            copy.write_text(committed)
            output = StringIO()
            with mock.patch.object(build_beast_fixture, "FIXTURE", copy):
                call_command("build_beast_fixture", stdout=output)
            written = copy.read_text()
        self.assertIn(f"Wrote {len(json.loads(committed))} beasts", output.getvalue())
        self.assertEqual(written, committed)
        self.assertEqual(FIXTURE.read_text(), committed)
