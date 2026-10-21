"""The character sheet's editable cells, written once.

character_edit.html had ``{% if can_edit %}<div class="editable-field"
data-field="...">`` twelve times over, and the three that offer a choice
listed their options by hand -- a second copy of the location table and its
two siblings, which a change to the model would leave behind without saying
so (issue #338 item 5).

The tags here render the shapes that repeat: a bounded attribute number, a
whole number bounded like the field-update endpoint (starting coin, total XP),
a select over a model field's own choices, and a short free-text field whose
length limit is the model's (the three physical rolls and the provenance
detail, which each typed maxlength="50" out by hand). The name heading is one
of a kind and stays written out in the template.

Which cells a viewer may change is decided by ``characters.services`` (issue
#748): a cell is an input only when the viewer may edit the character *and*
the field is in ``services.update_fields_for`` for their role, the same list
the field-update endpoint enforces. An editor who may not set a field (a
player, for anything rolled or awarded) gets the value and the reason beside
it, persistent, rather than an input the server would refuse. Fields that
sit together and share one reason (the six attributes, issue #84; the
provenance pair, issue #138) have it stated once, under them
(:func:`shared_locked_note`, :data:`SHARED_LOCKED_NOTES`), and each locked
cell points to that note.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from django import template
from django.db.models import Model

from characters import services
from characters.constants import CHARACTER_ATTRIBUTE_FIELDS, PROVENANCE_FIELDS
from characters.models import (
    Character,
    character_field_options,
    character_int_field_maximum,
    character_int_field_minimum,
)
from characters.rules import character_rules
from common.helpers import column_length

register = template.Library()


#: The element that states, once, why a player may not change a recorded
#: score (issue #84): the six attribute cells point to it rather than each
#: printing the sentence, and the page reveals it when a bought score locks.
ATTRIBUTE_LOCKED_NOTE_ID = "attribute-locked-reason"
#: The same for the provenance roll's class and detail (issue #138).
PROVENANCE_LOCKED_NOTE_ID = "provenance-locked-reason"


@dataclass(frozen=True)
class SharedLockedNote:
    """One reason stated once for fields the sheet shows together.

    Attributes:
        note_id: The note element's id, which each locked cell's
            ``aria-describedby`` names.
        fields: The fields it speaks for; one rule covers them all
            (``services.FIELD_RULES``), so they give one reason.
    """

    note_id: str
    fields: tuple[str, ...]


#: The sheet's shared locked notes, by the name the template asks for
#: (``{% shared_locked_note character "provenance" can_edit %}``). A field in
#: one of them never prints its reason beside its value; the note does.
SHARED_LOCKED_NOTES: Mapping[str, SharedLockedNote] = MappingProxyType(
    {
        "attributes": SharedLockedNote(
            ATTRIBUTE_LOCKED_NOTE_ID, CHARACTER_ATTRIBUTE_FIELDS
        ),
        "provenance": SharedLockedNote(PROVENANCE_LOCKED_NOTE_ID, PROVENANCE_FIELDS),
    }
)

#: The note each grouped field points to.
_SHARED_NOTE_ID_BY_FIELD: Mapping[str, str] = MappingProxyType(
    {
        field: note.note_id
        for note in SHARED_LOCKED_NOTES.values()
        for field in note.fields
    }
)


def cell_state(
    context: template.Context, character: Character, field: str, can_edit: bool
) -> dict:
    """Whether this cell is an input, and the reason when it is not.

    Args:
        context: The template context; its ``is_game_master`` says the role
            (absent means a player, the stricter reading).
        character: The character shown; a player may buy a score it has not
            recorded (``services.can_set_field``).
        field: The model field the cell shows.
        can_edit: Whether the viewer may edit this character at all.

    Returns:
        ``can_edit`` (render the input), ``locked_reason`` (the note an
        editor sees beside a value their role may not set; empty otherwise)
        and ``locked_note_id`` (the shared note that states the reason
        instead, when the field has one and is locked; empty otherwise).
    """
    is_game_master = bool(context.get("is_game_master", False))
    settable = services.can_set_field(character, field, is_game_master=is_game_master)
    locked_reason = services.field_refusal(field) if can_edit and not settable else ""
    return {
        "can_edit": bool(can_edit) and settable,
        "locked_reason": locked_reason,
        "locked_note_id": _SHARED_NOTE_ID_BY_FIELD.get(field, "")
        if locked_reason
        else "",
    }


def accessible_name(character: Model, field: str, label: str = "") -> str:
    """What a screen reader should call this cell's control (issue #696).

    Every one of these cells takes its heading from a sibling table cell or a
    ``<div class="stat-label">``, which reads correctly and is invisible to
    assistive technology: axe counted 16 unlabelled inputs and 5 unlabelled
    selects on the sheet, all critical, and every one of them autosaves on
    change -- so a wrong guess wrote straight to the character.

    The name comes from the model field's own ``verbose_name`` unless the
    caller gives one, so a field added to ``Character`` arrives labelled
    rather than waiting for somebody to remember this template. An override
    exists because a few headings on the page do not match the field name --
    ``location`` is headed "Origin", ``provenance_class`` is headed "Social
    Class" -- and WCAG 2.5.3 asks the accessible name to carry the visible
    words, not a different set of them.

    Args:
        character: The character being shown.
        field: The model field the cell edits.
        label: The heading printed beside the cell, when it differs from the
            field's verbose name.

    Returns:
        A capitalised label, e.g. "Strength" or "Social Class".
    """
    if label:
        return label
    verbose = str(character._meta.get_field(field).verbose_name)
    return verbose[:1].upper() + verbose[1:]


# The least an attribute may be. The template repeated its bounds six times;
# they are one rule about one kind of number, so they live here, the ceiling
# being the canon's (``attributes.maximum``).
ATTRIBUTE_MINIMUM = 1


@register.inclusion_tag("characters/_editable_attribute.html", takes_context=True)
def editable_attribute(context, character, field, can_edit, label=""):
    """One attribute cell: an autosaving number, or the number alone.

    :param character: The character being shown.
    :param field: The attribute's field name, e.g. ``"strength"``.
    :param can_edit: Whether this viewer may edit the character; the field's
        own rule (:func:`cell_state`) decides the rest.
    :param label: The cell's heading, when it differs from the field's
        verbose name. Becomes the input's accessible name (issue #696).
    """
    # A Game Master may state any score a character can hold; a player types
    # only a missing score, held to the buy's range (3 to 18, #748 review).
    is_game_master = bool(context.get("is_game_master", False))
    attribute_rules = character_rules().attributes
    return {
        "field": field,
        "value": getattr(character, field),
        **cell_state(context, character, field, can_edit),
        "minimum": (
            ATTRIBUTE_MINIMUM if is_game_master else attribute_rules.point_buy.minimum
        ),
        "maximum": (
            attribute_rules.maximum
            if is_game_master
            else attribute_rules.point_buy.maximum
        ),
        "label": accessible_name(character, field, label),
    }


@register.inclusion_tag("characters/_shared_locked_note.html", takes_context=True)
def shared_locked_note(
    context: template.Context, character: Character, group: str, can_edit: bool
) -> dict:
    """Why a player may not change a group of fields, said once (#84, #138).

    The fields of a group have the one rule and so the one reason
    (``services.FIELD_RULES``), which each of their cells printed until
    #84 (the attributes) and #138 (the provenance pair). The note is
    rendered for any editor, hidden while none of its fields is locked, so
    the page can show it when a player's bought score locks without a
    reload; it names the fields it speaks for (``data-locked-note-for``),
    which is how the page finds it.

    Args:
        context: The template context; its ``is_game_master`` says the role,
            as for :func:`cell_state`.
        character: The character shown.
        group: A key of :data:`SHARED_LOCKED_NOTES`; an unknown one raises,
            so a mistyped tag fails the page rather than dropping the note.
        can_edit: Whether the viewer may edit this character at all; a viewer
            who may not gets no note, as they get no reason beside any other
            value.

    Returns:
        ``can_edit`` (render the note at all), ``note_id``, ``fields`` (the
        fields it speaks for) and ``reason`` (empty while none of them is
        locked for this viewer, which renders the note hidden).
    """
    note = SHARED_LOCKED_NOTES[group]
    locked = [
        field
        for field in note.fields
        if cell_state(context, character, field, can_edit)["locked_reason"]
    ]
    return {
        "can_edit": bool(can_edit),
        "note_id": note.note_id,
        "fields": note.fields,
        "reason": services.field_refusal(locked[0]) if locked else "",
    }


@register.inclusion_tag("characters/_editable_number.html", takes_context=True)
def editable_number(context, character, field, can_edit, label="", empty="—"):
    """One whole-number cell (starting coin, total XP): an input, or the number.

    The input's bounds are the field-update endpoint's own
    (``character_int_field_minimum`` / ``_maximum``). The read-only number carries
    ``data-readonly-field`` so the page can repaint it after a roll or a run.

    :param character: The character being shown.
    :param field: The field name, e.g. ``"total_xp"``.
    :param can_edit: Whether this viewer may edit the character.
    :param label: The cell's heading; becomes the input's accessible name.
    :param empty: What an unset value reads as (a Rich character's coin is
        "Consult GM", issue #749).
    """
    return {
        "field": field,
        "value": getattr(character, field),
        "empty": empty,
        **cell_state(context, character, field, can_edit),
        "minimum": character_int_field_minimum(field),
        "maximum": character_int_field_maximum(field),
        "label": accessible_name(character, field, label),
    }


@register.inclusion_tag("characters/_editable_choice.html", takes_context=True)
def editable_choice(context, character, field, can_edit, label="", disabled=False):
    """One choice cell: an autosaving select, or the chosen label alone.

    The options are ``characters.models.character_field_options``'s: the
    loaded canon's rows for a table field (issue #66), the model's own for
    the rest, so a row the canon adds appears here the same day rather than
    whenever somebody remembers this template.

    :param character: The character being shown.
    :param field: The field name, e.g. ``"wealth_level"``.
    :param can_edit: Whether this viewer may change it.
    :param label: The cell's heading, when it differs from the field's
        verbose name. Becomes the select's accessible name (issue #696).
    :param disabled: Render the select for an editor but locked, for a value
        this editor may not change right now and may again later (the Origin
        once literacy is rolled, issue #741). The page can unlock it in place.
    """
    options = character_field_options(field) or {}
    value = getattr(character, field)
    return {
        "field": field,
        "choices": list(options.items()),
        "value": value,
        "display": options.get(value, value),
        **cell_state(context, character, field, can_edit),
        "disabled": disabled,
        "label": accessible_name(character, field, label),
    }


@register.inclusion_tag("characters/_editable_text.html", takes_context=True)
def editable_text(context, character, field, can_edit, label=""):
    """One short free-text cell: an autosaving text input, or the text alone.

    The input's maxlength is the model field's own ``max_length``, so the page
    refuses the same length the database does, from one definition.

    :param character: The character being shown.
    :param field: The field name, e.g. ``"height_roll"``.
    :param can_edit: Whether this viewer may change it.
    :param label: The cell's heading, when it differs from the field's
        verbose name. Becomes the input's accessible name (issue #696).
    """
    return {
        "field": field,
        "value": getattr(character, field),
        **cell_state(context, character, field, can_edit),
        "max_length": column_length(type(character), field),
        "label": accessible_name(character, field, label),
    }
