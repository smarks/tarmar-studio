"""Regenerate ``bestiary/fixtures/beasts.json`` from the committed source data.

The fixture is *generated, never hand-edited*: every record in
``bestiary/data/tft_source_stats.json`` runs through
``bestiary.translation.translate`` and is merged with its original description
from ``bestiary/data/descriptions.json``. The drift-guard test
(``bestiary/tests/test_fixture.py``) asserts the committed fixture matches
this command's output byte-for-byte, so a change to the translation rules or
the source data must be followed by re-running this command.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from bestiary.translation import translate

APP_DIR = Path(__file__).resolve().parent.parent.parent
SOURCE_STATS = APP_DIR / "data" / "tft_source_stats.json"
DESCRIPTIONS = APP_DIR / "data" / "descriptions.json"
FIXTURE = APP_DIR / "fixtures" / "beasts.json"


def build_fixture_records() -> list[dict]:
    """The fixture content the committed data implies, as Django fixture rows."""
    entries = json.loads(SOURCE_STATS.read_text())
    descriptions = json.loads(DESCRIPTIONS.read_text())
    missing = [entry["name"] for entry in entries if entry["name"] not in descriptions]
    if missing:
        raise CommandError(f"Missing descriptions for: {', '.join(missing)}")
    records = []
    for primary_key, entry in enumerate(entries, start=1):
        fields = translate(entry, description=descriptions[entry["name"]])
        records.append({"model": "bestiary.beast", "pk": primary_key, "fields": fields})
    return records


def render_fixture() -> str:
    return json.dumps(build_fixture_records(), indent=1, ensure_ascii=False) + "\n"


class Command(BaseCommand):
    help = (
        "Regenerate bestiary/fixtures/beasts.json from the committed TFT source stats"
    )

    def handle(self, *args, **options):
        content = render_fixture()
        FIXTURE.write_text(content)
        count = len(json.loads(content))
        self.stdout.write(self.style.SUCCESS(f"Wrote {count} beasts to {FIXTURE}"))
