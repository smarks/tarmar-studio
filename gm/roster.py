"""A session's characters, as records (issue #571).

A character built in a Games Master session is a ``characters.Character`` row
from its first change. The session's roster is its attached characters
(``GMSession.characters``), each with what the conversation calls it
(:class:`gm.models.SessionCharacter`). Identity is the row's id: two
characters may share a name, and a rename keeps the row.

A plan names its character by record id (issue #590), from the roster the
state block lists, so no name is ever resolved or guessed at.
"""

from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from characters.constants import name_within_rule, unnamed_character
from characters.dice import (
    FATIGUE_LOG_LABEL,
    dice_log_line,
    roll_fatigue_logged,
    roll_physical_features,
)
from characters.models import Character
from characters.services import (
    apply_background_roll,
    apply_provenance_roll,
    apply_wealth_roll,
    follow_origin,
)

from .ledger import (
    GAME_MASTER_SKILL_POINTS_KEY,
    LABEL_KEY,
    DraftLedger,
    draft_identity,
    draft_values,
)
from .models import GMSession, SessionCharacter


@dataclass(frozen=True)
class RosterEntry:
    """One of the session's characters, with what the conversation calls it."""

    character: Character
    label: str = ""
    game_master_skill_points: int = 0

    @property
    def identity(self) -> str:
        """How the character is named where several are listed."""
        return draft_identity(self.values)

    @property
    def values(self) -> dict[str, Any]:
        """The character's fields, with its label, as the notices read them."""
        return {**draft_values(self.character), LABEL_KEY: self.label}

    def ledger(self) -> DraftLedger:
        """The ledger snapshot the panel and the model both read."""
        return DraftLedger.from_draft(
            {
                **draft_values(self.character),
                GAME_MASTER_SKILL_POINTS_KEY: self.game_master_skill_points,
            }
        )


def roster(session: GMSession | None) -> list[RosterEntry]:
    """The session's attached characters, oldest attachment first.

    Soft-deleted characters drop out, as they do from the sheet block.
    """
    if session is None or session.pk is None:
        return []
    labels = {
        link.character_id: link
        for link in SessionCharacter.objects.filter(session=session)
    }
    entries = []
    for character in session.attached_characters():
        link = labels.get(character.pk)
        entries.append(
            RosterEntry(
                character=character,
                label=link.label if link else "",
                game_master_skill_points=(link.game_master_skill_points if link else 0),
            )
        )
    return entries


def built_here(session: GMSession | None) -> list[RosterEntry]:
    """The roster entries this session's conversation built or labelled.

    These are the characters the ledger block lists; characters attached
    from the picker travel in the sheet block instead.
    """
    if session is None or session.pk is None:
        return []
    labelled = set(
        SessionCharacter.objects.filter(session=session).values_list(
            "character_id", flat=True
        )
    )
    return [entry for entry in roster(session) if entry.character.pk in labelled]


def start_character(
    session: GMSession, who: str, *, name: str = "", origin: str = ""
) -> Character:
    """Create a character record for this session and attach it.

    The server rolls the dice nobody chooses now, because code owns the dice:
    appearance, height and weight, the 2d6 Fatigue roll, then origin,
    provenance and wealth through the services the sheet's roll controls use,
    with the Origin's skill and the coin (issue #754). So the record holds what
    the creation page's would (``characters.dice.roll_secondary_character_stats``),
    in the same dice-log lines and order. The Fatigue roll joined them with
    issue #859 (Spencer's standing rule, as the coordinator ruled: the chat
    rolls it as the creation page does, so a chat-built character is a record
    like a sheet-built one); a player's later ``roll_fatigue`` is a second
    roll, which only a Game Master gets. The attributes stay unknown until
    they are rolled or bought, so the character is incomplete until then.

    Args:
        session: The session; its owner owns the character.
        who: What the conversation calls the character; kept as its label.
        name: The character's name, when the change that starts it gives one.
        origin: The Origin the plan that starts the character states
            (``set_background``), as a choice of ``Character.location``; empty
            to roll it. The rules let the Origin be rolled or chosen, a stated
            one is recorded, not rolled (#860), and a roll is final for a
            player (#970), so it is recorded here as a choice, with the skill
            it grants, instead of being rolled and then refused. Recorded at
            creation rather than by the ``set_background`` itself, so a part
            of that change the server refuses (a player's provenance or
            wealth) does not take the Origin back with it. Provenance and
            wealth are rolled as ever.

    Returns:
        The saved, attached character.

    Raises:
        ValidationError: ``name`` breaks the name rule (issue #870).
    """
    # One name rule on every path (issue #870). A name the change gives is
    # held to it, as the sheet's rename is: a name it refuses refuses the
    # step (ValidationError, which the executor reports), so no record holds
    # a name its own export could not bring back in. The label is what the
    # conversation calls the character, not a name anybody gave, so a name
    # made from it keeps only what the rule accepts. Coordinator's ruling
    # under the standing rule; Spencer may overrule.
    if name:
        name = str(Character.clean_field_value("name", name))
    label = who.strip()[: SessionCharacter.MAX_LABEL_CHARS]
    physical, log_entries = roll_physical_features()
    fatigue_roll, fatigue_log = roll_fatigue_logged()
    log_entries.append(dice_log_line(FATIGUE_LOG_LABEL, fatigue_log))
    character = Character(
        user=session.user,
        name=name or name_within_rule(label, fallback=unnamed_character()),
        strength=None,
        dexterity=None,
        intelligence=None,
        wisdom=None,
        constitution=None,
        charisma=None,
        fatigue_roll=fatigue_roll,
        # Nothing is on the record until the rolls below put it there: the
        # model's defaults are not a background anybody rolled.
        provenance_recorded=False,
        location_recorded=False,
        wealth_recorded=False,
        skills={},
        dice_log="\n".join(log_entries),
        **physical,
    )
    if origin:
        # Chosen, as the sheet records a chosen Origin (services.change_field):
        # on the record, with its skill, and no dice-log line, so it stays the
        # player's to choose again until literacy fixes it.
        unrolled = character.location
        character.location = origin
        character.location_recorded = True
        follow_origin(character, unrolled, was_recorded=False)
        # The creation page's order, less the Origin: provenance, then wealth.
        apply_provenance_roll(character)
        apply_wealth_roll(character)
    else:
        apply_background_roll(character)
    with transaction.atomic():
        character.save()
        session.characters.add(character)
        SessionCharacter.objects.create(
            session=session, character=character, label=label
        )
    return character


def grant_skill_points(session: GMSession, character: Character, points: int) -> None:
    """Record skill points a Game Master spent past those earned (issue #404).

    Added in the database rather than read into Python and written back: two
    grants that interleave -- two turns of one session resolved at once, or a
    retried request -- both read the old total, and the second write threw
    the first away. The recorded grant then under-counted, and
    ``skill_point_account`` reported a ``spent`` figure too high by exactly
    the lost grant (issue #689).
    """
    link, _created = SessionCharacter.objects.get_or_create(
        session=session, character=character
    )
    SessionCharacter.objects.filter(pk=link.pk).update(
        game_master_skill_points=F("game_master_skill_points") + points
    )


def remove(session: GMSession, character: Character) -> str:
    """Take a character out of the session (issue #403).

    A character that is still incomplete was only ever part of this session,
    so it is soft-deleted as well; a complete one is only detached.

    Returns:
        The note naming who was removed.
    """
    entry_name = character.name
    with transaction.atomic():
        session.characters.remove(character)
        SessionCharacter.objects.filter(session=session, character=character).delete()
        if not character.is_complete:
            Character.objects.filter(pk=character.pk).update(
                is_deleted=True, deleted_at=timezone.now()
            )
    return f"removed {entry_name} from this session"


def ledger_headings(entries: list[RosterEntry]) -> list[tuple[str, DraftLedger]]:
    """``(heading, ledger)`` for each entry, as the pane and the model read them.

    A character alone is headed by its name; several are headed by name and
    label, so no number reads as belonging to the wrong one.
    """
    several = len(entries) > 1
    headed = []
    for entry in entries:
        ledger = entry.ledger()
        headed.append((entry.identity if several else ledger.name, ledger))
    return headed
