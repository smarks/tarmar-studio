"""Repair the beast primary-key sequence after the explicit-pk catalog load.

``0002_load_beast_catalog`` bulk-inserts the catalog with explicit primary
keys. On PostgreSQL that leaves the ``bestiary_beast`` id sequence at its
pre-insert value, so the next plain ``Beast.objects.create()`` — the first
GM-generated variant — collides and raises ``IntegrityError`` (issue #196;
Spencer hit it as a 500 on the generate page). SQLite self-corrects its rowid
counter, which is why dev, CI, and the test suite never saw it.

``sequence_reset_sql`` emits the engine-appropriate statements and is an
empty list on SQLite, so this is a safe no-op everywhere but the engines
that need it.
"""

from django.core.management.color import no_style
from django.db import migrations


def reset_beast_sequence(apps, schema_editor):
    beast_model = apps.get_model("bestiary", "Beast")
    statements = schema_editor.connection.ops.sequence_reset_sql(
        no_style(), [beast_model]
    )
    for statement in statements:
        schema_editor.execute(statement)


class Migration(migrations.Migration):
    dependencies = [
        ("bestiary", "0002_load_beast_catalog"),
    ]

    operations = [
        migrations.RunPython(reset_beast_sequence, migrations.RunPython.noop),
    ]
