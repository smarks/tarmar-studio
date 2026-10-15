"""The character sheet's editable cells, written once.

character_edit.html had ``{% if can_edit %}<div class="editable-field"
data-field="...">`` twelve times over, and the three that offer a choice
listed their options by hand -- a second copy of ``Character.LOCATION_CHOICES``
and its two siblings, which a change to the model would leave behind without
saying so (issue #338 item 5).

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
it, persistent, rather than an input the server would refuse.
"""

from django import template
from django.db.models import Model

from characters import services
from characters.models import (
    Character,
    character_int_field_maximum,
    character_int_field_minimum,
)
from characters.rules import character_rules
from common.helpers import column_length

register = template.Library()


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
        ``can_edit`` (render the input) and ``locked_reason`` (the note an
        editor sees beside a value their role may not set; empty otherwise).
    """
    is_game_master = bool(context.get("is_game_master", False))
    settable = services.can_set_field(character, field, is_game_master=is_game_master)
    return {
        "can_edit": bool(can_edit) and settable,
        "locked_reason": services.field_refusal(field)
        if can_edit and not settable
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

    The options come from the model field's own ``choices``, so a choice added
    to ``Character`` appears here the same day rather than whenever somebody
    remembers this template.

    :param character: The character being shown.
    :param field: The field name, e.g. ``"wealth_level"``.
    :param can_edit: Whether this viewer may change it.
    :param label: The cell's heading, when it differs from the field's
        verbose name. Becomes the select's accessible name (issue #696).
    :param disabled: Render the select for an editor but locked, for a value
        this editor may not change right now and may again later (the Origin
        once literacy is rolled, issue #741). The page can unlock it in place.
    """
    choices = list(character._meta.get_field(field).choices or [])
    value = getattr(character, field)
    return {
        "field": field,
        "choices": choices,
        "value": value,
        "display": dict(choices).get(value, value),
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
