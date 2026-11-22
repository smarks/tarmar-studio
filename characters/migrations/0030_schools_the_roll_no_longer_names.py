"""Two schools of magic leave the school roll (tarmar-studio #208).

Counter is removed as a school; Counter 1 to Counter 5 stay where they were,
among Protection's spells. Arcane/True Magic is by invitation only, so it is
never rolled or chosen. Neither stays on the school table.

A character whose school is one of them would otherwise keep a school no rule
names. The sheet would go on showing it, the player could not choose again (a
player who holds a school is offered none), and a Game Master's import of the
character's own export would be refused, because the import checks the school
against the table.

This migration clears the school on every such character and keeps the school
roll. The player then chooses again from the list the roll opened; a
character with no roll rolls first, as any character does. Each gets a
history line saying why. The canon records the reading as a ruling.

The two schools are named here and not read from the canon: this is a dated
record of what was removed on 2026-11-18, and the canon goes on changing.

Rows it touches: characters whose ``magic_school`` is ``Counter`` or
``Arcane/True Magic``. The live database held none when this was written
(the coordinator's check on 2026-11-17, before the work began). The reverse
is a no-op: the school a character held is in its history line, and putting
it back would restore a school the table does not name.
"""

from django.db import migrations
from django.utils import timezone

#: The schools the school roll named until tarmar-studio #208.
REMOVED_SCHOOLS: tuple[str, ...] = ("Counter", "Arcane/True Magic")

#: How many history entries a character keeps (``characters.models``).
MAX_HISTORY_ENTRIES = 50

#: What the history line says the school became, and why.
CLEARED_SCHOOL_NOTE = (
    "unchosen: {school} is no longer a school a character rolls or chooses "
    "(tarmar-studio #208); the school roll is kept, so choose again from the "
    "list it opened"
)


def clear_removed_schools(apps, schema_editor) -> None:
    """Clear each removed school, keep the roll, and say why in the history."""
    character_model = apps.get_model("characters", "Character")
    held = character_model.objects.filter(magic_school__in=REMOVED_SCHOOLS)
    for character in held.only("pk", "magic_school", "history").iterator():
        history = list(character.history or [])
        history.append(
            {
                "ts": timezone.now().isoformat(),
                "field": "magic_school",
                "old": character.magic_school,
                "new": CLEARED_SCHOOL_NOTE.format(school=character.magic_school),
                "by": "system",
            }
        )
        character_model.objects.filter(pk=character.pk).update(
            magic_school="", history=history[-MAX_HISTORY_ENTRIES:]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("characters", "0029_character_magic_school"),
    ]

    operations = [
        migrations.RunPython(clear_removed_schools, migrations.RunPython.noop),
    ]
