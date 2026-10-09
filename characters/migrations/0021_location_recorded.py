"""The Origin becomes a record, and so does the skill it grants (issue #754).

Adds ``location_recorded`` (whether anybody rolled or chose the Origin) and
``origin_skill`` (the skill holding the one point the Origin grants), and
decides both for every existing character:

* A character holding a skill its Origin's grant may be held in (location.md:
  "City: Street smarts ... Village: Street smarts OR survival ... Rural:
  Survival") records that skill as the grant. Every character made on the
  creation page or imported by a player was granted it; one whose points
  were bought on top keeps them, and an Origin change moves only the grant.
* A character made on the creation page that no longer holds the skill (its
  dice log says "Location: d100=") was renamed away from it or reworked by a
  Game Master: nothing is guessed, and no grant is recorded.
* A character started in a Games Master session before #754 (it has a
  ``SessionCharacter`` row and no "Location: d100=" line) was saved with the
  model's Village that nobody rolled, and never granted a skill:

  - still at that Village, with no Origin change in its history and no
    literacy rolled or ruled against it, its Origin is marked not recorded,
    so its player rolls it (``roll_background``) or chooses it, and the grant
    comes then;
  - its literacy rolled or ruled against that Village: the Village is the
    Origin the check was made against, which the player may no longer change
    (``can_change_origin``), so it stays recorded and is granted the Village
    skill now, with a history line saying so;
  - its Origin changed since (the chat's ``set_background``, or the sheet):
    that Origin was chosen, so it stays recorded and is granted its skill
    now, with a history line saying so.

What this changes, measured on a copy of the main checkout's development
database on 2026-09-29 (122 characters): 108 session-built characters marked
not recorded, one session-built character granted the skill it was never
given, and 12 grants recorded. A Village a player restated in the chat before
#754 left no history (the chat skipped an unchanged value), so it is among the
108 and reads as never chosen. Such a player loses nothing: a player may
choose the Origin anyway ("Roll percentile dice or choose"), and choosing it
grants its skill. Characters made on the creation page, imported, or attached
from the picker are never marked not recorded.

The same limit as ``0015``: a character keeps only its last 50 history
entries, so an Origin change older than that is gone, and that character is
treated as never changed.
"""

from django.db import migrations, models
from django.utils import timezone

#: The skills each Origin's grant may be held in, the granted one first
#: (``characters.dice.LOCATION_SKILL_CHOICES`` as of #754, normalized).
GRANT_SKILLS: dict[str, tuple[str, ...]] = {
    "City": ("street smarts",),
    "Village": ("street smarts", "survival"),
    "Rural": ("survival",),
}

#: The points a grant is (``characters.dice.LOCATION_SKILL_POINTS``).
GRANT_POINTS = 1

#: The Origin a character started in a Games Master session carried.
DEFAULT_ORIGIN = "Village"

#: How many history entries a character keeps (``characters.models``).
MAX_HISTORY_ENTRIES = 50


def _held_grant(skills: dict, choices: tuple[str, ...]) -> str:
    """The skill key holding one of ``choices``, or ``""``."""
    by_name = {str(name).casefold(): name for name in skills}
    for choice in choices:
        if choice in by_name:
            return str(by_name[choice])
    return ""


def _grant(character, choices: tuple[str, ...]) -> None:
    """Grant the Origin's skill to a character that was never granted it."""
    skills = dict(character.skills or {})
    before = dict(skills)
    granted = choices[0]
    skills[granted] = skills.get(granted, 0) + GRANT_POINTS
    character.skills = skills
    character.origin_skill = granted
    history = list(character.history or [])
    history.append(
        {
            "ts": timezone.now().isoformat(),
            "field": "skills",
            "old": str(before),
            "new": f"{granted} {GRANT_POINTS} granted by the {character.location} "
            "origin, which was never granted it (issue #754)",
            "by": "system",
        }
    )
    character.history = history[-MAX_HISTORY_ENTRIES:]


def record_origins(apps, schema_editor) -> None:
    """Decide ``location_recorded`` and ``origin_skill`` for each character."""
    character_model = apps.get_model("characters", "Character")
    session_character_model = apps.get_model("gm", "SessionCharacter")
    session_built = set(
        session_character_model.objects.values_list("character_id", flat=True)
    )
    for character in character_model.objects.all().only(
        "location",
        "skills",
        "dice_log",
        "history",
        "literacy_rolled",
        "literacy_ruling",
    ):
        choices = GRANT_SKILLS.get(character.location, ())
        if not choices:
            continue
        held = _held_grant(character.skills or {}, choices)
        if held:
            character.origin_skill = held
            character.save(update_fields=["origin_skill"])
            continue
        rolled_at_creation = "Location: d100=" in (character.dice_log or "")
        if rolled_at_creation or character.pk not in session_built:
            continue
        changed_fields = {
            entry.get("field")
            for entry in character.history or []
            if isinstance(entry, dict)
        }
        literacy_decided = (
            character.literacy_rolled or character.literacy_ruling is not None
        )
        if (
            character.location == DEFAULT_ORIGIN
            and "location" not in changed_fields
            and not literacy_decided
        ):
            character.location_recorded = False
            character.save(update_fields=["location_recorded"])
            continue
        _grant(character, choices)
        character.save(update_fields=["skills", "origin_skill", "history"])


class Migration(migrations.Migration):
    dependencies = [
        ("characters", "0020_character_current_pools"),
        # Which characters a session built (SessionCharacter).
        ("gm", "0021_command_entries_stored_as_typed"),
    ]

    operations = [
        migrations.AddField(
            model_name="character",
            name="location_recorded",
            field=models.BooleanField(db_default=True, default=True),
        ),
        migrations.AddField(
            model_name="character",
            name="origin_skill",
            field=models.CharField(
                blank=True, db_default="", default="", max_length=50
            ),
        ),
        migrations.RunPython(record_origins, migrations.RunPython.noop),
    ]
