"""Populate the beast catalog from the committed fixture.

The catalog fixture was previously loaded by hand (``loaddata beasts``), so a
fresh deployment shipped an empty bestiary (issue #183). Loading it in a data
migration means every environment — production included — is populated by
``migrate``. Rows are inserted through the historical model rather than
``loaddata`` so this migration stays valid if later migrations reshape the
model, and only missing primary keys are inserted so re-running (or a later
``loaddata``) never duplicates or clobbers GM-generated variants.
"""

import json
from pathlib import Path

from django.db import migrations

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "beasts.json"


def load_beast_catalog(apps, schema_editor):
    beast_model = apps.get_model("bestiary", "Beast")
    fixture_entries = json.loads(FIXTURE_PATH.read_text())
    existing_primary_keys = set(beast_model.objects.values_list("pk", flat=True))
    missing_beasts = [
        beast_model(pk=entry["pk"], **entry["fields"])
        for entry in fixture_entries
        if entry["pk"] not in existing_primary_keys
    ]
    beast_model.objects.bulk_create(missing_beasts)


def unload_beast_catalog(apps, schema_editor):
    beast_model = apps.get_model("bestiary", "Beast")
    fixture_entries = json.loads(FIXTURE_PATH.read_text())
    fixture_primary_keys = [entry["pk"] for entry in fixture_entries]
    beast_model.objects.filter(pk__in=fixture_primary_keys, is_generated=False).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("bestiary", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(load_beast_catalog, unload_beast_catalog),
    ]
