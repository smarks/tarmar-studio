"""Count the Luck each character spends in a session (issue #861).

Schema only: ``SessionCharacter.luck_spent``, with a database default of 0 so
every existing row (120 on the local database this was tried against) reads
0, and so the colour still serving the previous release can insert rows
without it during a deploy. Reversing drops the column.

Numbered 0024 because batch D2's 0022 and 0023 came first; it follows
``0023_records_set_by_hand``.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gm", "0023_records_set_by_hand"),
    ]

    operations = [
        migrations.AddField(
            model_name="sessioncharacter",
            name="luck_spent",
            field=models.PositiveIntegerField(db_default=0, default=0),
        ),
    ]
