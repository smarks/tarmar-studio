"""Shared numeric and string constants for character views.

Keeps magic numbers in one place so behavior stays consistent across endpoints
and the test suite can assert against the same values. A couple of one-line
helpers that operate purely on those constants live here too, so both
``characters.views`` and ``characters.skills`` can reach them without either
importing the other.
"""

import re

# The numbers character creation reads -- the attribute ceiling, the point
# buy, the starting age and aging step, the Fatigue and literacy dice, the
# height, weight and percentile tables -- are the loaded canon's
# ``character-rules.json``, read by ``characters.rules.character_rules()``
# (issue #59). None of them is restated here.

# The "_format" marker on a character export document; the importer (and the
# GM's /character command, issue #344) accept only documents carrying it.
CHARACTER_EXPORT_FORMAT = "tarmar-character"

# The wealth level a character has until one is rolled or set: the model's
# default, a new session character's draft, and the level whose starting coin
# the extraction schema names as the default (issue #68).
DEFAULT_WEALTH_LEVEL = "Moderate"

# The attribute a "+1 STR"/"+1 DEX"/"+1 CON" experience result raises, keyed by
# the short name the result carries. Lives here rather than in ``skills.py``
# because both the simulator (which raises its running score) and
# ``Character._apply_attribute_bonus`` (which writes the gain back) read it, and
# they must agree on which attribute a result names -- issue #629 was the two
# sides disagreeing on how much a result was worth.
ATTRIBUTE_BONUS_FIELDS: dict[str, str] = {
    "str": "strength",
    "dex": "dexterity",
    "con": "constitution",
}

# The six core attributes, as (model field name, sheet label), in rolling order.
# Single source of truth for anything that walks the core six: the 3d6 roller,
# the point-buy builder, and the creation form.
CORE_ATTRIBUTE_FIELDS = (
    ("strength", "STR"),
    ("dexterity", "DEX"),
    ("intelligence", "INT"),
    ("wisdom", "WIS"),
    ("constitution", "CON"),
    ("charisma", "CHR"),
)

# The six core attributes by field name, in character-sheet order. Each has a
# max of 30 on the model. Creation, export, beast variation, the checks, the
# plan reader and the canon's skill rules all build their lists from this
# rather than restating the six names (issues #337, #70).
CHARACTER_ATTRIBUTE_FIELDS: tuple[str, ...] = tuple(
    field_name for field_name, _short_name in CORE_ATTRIBUTE_FIELDS
)


def point_buy_budget_note(spent: int) -> str | None:
    """How far a bought set of attributes sits off the point budget.

    The one wording of that sentence. The creation form writes it into the
    character's dice log (``characters.dice``) and the GM session's
    ``/character`` save reports it back to a DM (issue #352), so an
    off-budget character says the same thing wherever it was built.

    Args:
        spent: The sum of the six core attributes.

    Returns:
        The sentence, or ``None`` when ``spent`` is exactly on budget.
    """
    # Deferred: characters.rules imports this module for the attribute names.
    from .rules import character_rules

    budget = character_rules().attributes.point_buy.total
    if spent == budget:
        return None
    direction = "over" if spent > budget else "under"
    return (
        f"Point-buy total: {spent} — {abs(spent - budget)} "
        f"{direction} the {budget}-point budget."
    )


# Injury thresholds. Both pools take -2 to all rolls from 5 down to 1 -- a flat
# band, not a fraction of the pool -- so it is the same number for every
# character. See reference/content/public-rules/characters/injury-thresholds-death.md.
SEVERE_INJURY_BAND_TOP = 5
# The table's penalty to all rolls: down to half a pool (but above the severe
# band), and inside the severe band.
MINOR_INJURY_ROLL_PENALTY = -1
SEVERE_INJURY_ROLL_PENALTY = -2

# Equipment quantity and weight bounds.
MAX_EQUIPMENT_QUANTITY = 999
MAX_EQUIPMENT_WEIGHT_LBS = 1000

# The longest each free-text field of a character's record may be (issue
# #928). One number per field, read by every path that writes the field --
# the model's ``max_length`` (or its JSON validator), the sheet's endpoints,
# the Games Master chat's services and the importer -- so nothing the app
# writes is longer than its own importer takes back, the #800 class. Skill
# names were the one field the paths disagreed on: unbounded on the sheet
# and in the importer, 200 characters in the chat's ``spend_skill_points``,
# 60 in its ``skill_check`` (#843) and 50 in ``Character.origin_skill``,
# which holds one. 60 was taken over 50 because the sheet stored names with
# no bound, and ``origin_skill`` widened to it (migration 0024).
MAX_EQUIPMENT_NOTES_LENGTH = 200
MAX_EQUIPMENT_NAME_LENGTH = 100
MAX_EQUIPMENT_DAMAGE_LENGTH = 20
MAX_SKILL_NAME_LENGTH = 60
MAX_DEATH_CAUSE_LENGTH = 200
#: The reason on a coin movement's history line (#802); a Game Master's.
MAX_COIN_REASON_LENGTH = 100

# Inclusive bounds for to_hit_mod / damage_mod / critical_mod / stops_mod / dex_mod
# and analogues. Anything outside this range is rejected.
MAX_EQUIPMENT_MODIFIER = 10

# Armor stops + dex penalty caps.
MAX_ARMOR_STOPS = 10
MAX_ARMOR_DEX_PENALTY_MAGNITUDE = 10

# Damage dice format: optional dN where N is small, plus optional +/- modifier.
# Examples: "1d6", "2d8+1", "1d10-2". Rejects "999d999" etc.
# \A and \Z anchor the full string (unlike $, which matches before a trailing \n).
DAMAGE_DICE_PATTERN = re.compile(r"\A\d{1,2}d\d{1,3}(?:[+-]\d{1,3})?\Z")

# Character name allowed characters: letters (any unicode letter), digits,
# spaces, hyphens, underscores, periods, apostrophes. No emoji, no zero-width
# joiners, no control chars. \A...\Z forbids trailing newlines.
# The characters a name may use, as a regex character-class body. Exposed so
# patterns that allow a little more (battle combatant names) extend it rather
# than restate it.
CHARACTER_NAME_CHARACTERS = r"\w \-.\'"
CHARACTER_NAME_PATTERN = re.compile(rf"\A[{CHARACTER_NAME_CHARACTERS}]+\Z", re.UNICODE)
MAX_CHARACTER_NAME_LENGTH = 100

#: Each free-text field's limit by the name the sheet's template reads it
#: under (``text_limits.<field>``), so an input's ``maxlength`` is the
#: server's number rather than a copy of it (issue #928).
CHARACTER_TEXT_LIMITS: dict[str, int] = {
    "name": MAX_CHARACTER_NAME_LENGTH,
    "skill_name": MAX_SKILL_NAME_LENGTH,
    "item_name": MAX_EQUIPMENT_NAME_LENGTH,
    "item_notes": MAX_EQUIPMENT_NOTES_LENGTH,
    "item_damage": MAX_EQUIPMENT_DAMAGE_LENGTH,
    "death_cause": MAX_DEATH_CAUSE_LENGTH,
    "coin_reason": MAX_COIN_REASON_LENGTH,
}


def too_long(label: str, text: str, limit: int) -> str:
    """Why ``text`` is refused for passing ``limit``, or ``""`` when it fits.

    The one wording every path gives for a free-text field over its limit
    (issue #928), the sheet's: "Name must be 100 characters or fewer".

    Args:
        label: The field as the refusal names it, capitalised.
        text: The value, as it would be stored.
        limit: The field's limit, one of the constants above.

    Returns:
        The refusal, or ``""``.
    """
    if len(text) <= limit:
        return ""
    return f"{label} must be {limit} characters or fewer"


#: The name a character takes when nothing gives it one (the Games Master
#: chat's unnamed character, a blank account name; issue #870).
UNNAMED_CHARACTER = "Unnamed character"

# Control characters (C0 and C1) plus the Unicode line/paragraph separators.
# Used on the free-text fields CHARACTER_NAME_PATTERN does not cover -- equipment
# names/notes and skill names -- where a wide charset has to stay allowed because
# players legitimately type apostrophes, quotes and parentheses: "Bob's 6\" dagger",
# "sword (+1 to hit)".
#
# Quotes and angle brackets stay allowed on purpose; the browser side escapes them
# for both text and quoted-attribute contexts (escapeHtml in character_edit.html).
# Control characters, by contrast, have no use in a single-line name or note and
# they corrupt the machinery around them -- JSON embedded in a page, audit log
# lines, exports -- so they are removed on the way in.
CONTROL_CHARACTERS_PATTERN = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def strip_control_characters(text: str) -> str:
    """Remove control characters from a single-line free-text field.

    Control characters become a single space rather than vanishing, so
    ``"line one\\nline two"`` does not collapse into ``"line onetwo"``; runs of
    whitespace are then collapsed and the result stripped.

    Args:
        text: Raw user-supplied text.

    Returns:
        The text with control characters removed and whitespace normalized.
    """
    return " ".join(CONTROL_CHARACTERS_PATTERN.sub(" ", text).split())


#: Every character the name rule refuses (the complement of
#: :data:`CHARACTER_NAME_CHARACTERS`).
CHARACTER_NAME_REFUSED = re.compile(rf"[^{CHARACTER_NAME_CHARACTERS}]", re.UNICODE)


def name_within_rule(text: str, *, fallback: str) -> str:
    """A name the name rule accepts, made from text nobody typed as a name.

    Issue #870: three paths stored a name they built from something else --
    the Games Master chat's label for a character, a Game Master's account
    name on the creation page, a battle's free-text name -- without asking
    the rule, so the character's own export could not be imported. Each now
    makes its name here: every character the rule refuses is taken out
    (``"Aldwin, the Grey"`` becomes ``"Aldwin the Grey"``, the account
    ``"probe.gm+1@x"`` becomes ``"probe.gm1x"``), whitespace is collapsed and
    the result capped at :data:`MAX_CHARACTER_NAME_LENGTH`. A name somebody
    typed as a name is refused instead, as the sheet refuses it.

    Args:
        text: What the name is made from.
        fallback: The name when nothing of ``text`` is left; it must itself
            pass the rule.

    Returns:
        A name :data:`CHARACTER_NAME_PATTERN` matches.
    """
    kept = CHARACTER_NAME_REFUSED.sub("", strip_control_characters(text))
    name = " ".join(kept.split())[:MAX_CHARACTER_NAME_LENGTH].rstrip()
    return name if name else fallback


#: The per-item modifiers an equipment entry may carry (issue #752).
ITEM_MODIFIER_FIELDS: tuple[str, ...] = (
    "to_hit_mod",
    "damage_mod",
    "critical_mod",
    "stops_mod",
    "dex_mod",
)
