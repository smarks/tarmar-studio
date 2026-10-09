"""Formerly cleared the game data made before characters became records (#571).

Production ran the reset on 2026-09-16 (deploy f6a37c2, after a ``pg_dump``):
it deleted the pre-#571 characters, Games Master sessions, turn records,
reviews, and the battle and battle draft, and kept the user accounts.

It is a no-op now. The reset refused to run on any database holding more rows
than production did that day, which protected production but left every
development database with older data unable to migrate past this point, and
so unable to apply any later Games Master migration. Production has this
migration recorded as applied and will not run it again, so emptying it
changes nothing there and unblocks everyone else. A development database keeps
its old rows; the new code simply ignores their JSON drafts.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("gm", "0012_session_characters"),
        ("characters", "0011_incomplete_characters"),
        ("battle", "0012_battle_deleted_at_battle_is_deleted"),
        ("parties", "0002_party_soft_delete"),
    ]

    operations = [
        migrations.RunPython(migrations.RunPython.noop, migrations.RunPython.noop),
    ]
