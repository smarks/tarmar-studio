"""Skill names as they are stored: ``normalize_skill_name`` (issue #70).

Moved out of ``characters.skills`` so ``characters.skill_rules``, which
refuses a canon skill name that is not already in stored form, can use it
without importing ``characters.skills``, which reads those rules: the two
would otherwise import each other. ``characters.skills`` re-exports it, so
its callers are unchanged.

Free of Django, like ``characters.constants``, which it reads.
"""

import re

from .constants import strip_control_characters

# The three patterns normalize_skill_name applies. A skill name comes from a
# player's request, an imported file (up to 256 KB, checked against the
# 60-character limit only after it is normalized) or the model, so each runs
# in linear time (issue #11). Two things keep it so. No two repeated parts
# take the same characters: the bonus's text starts on a non-space the
# whitespace before it cannot take, and a run of whitespace is tried only
# from its first character (the lookbehinds), not again from each character
# inside it. And the parenthesis patterns only see the name up to its last
# ")": past it, every "(" scanned to the end of the name and failed, so a
# name of many unclosed "(+1 " was scanned once per "(".
_BONUS_IN_PARENTHESES = re.compile(r"\(\+?\d+\s+(\S[^)\n]*|[^\S\n])\)")
_PARENTHESES = re.compile(r"(?<!\s)\s*\([^)]*\)")
_ROMAN_NUMERAL_SUFFIX = re.compile(r"(?<!\s)\s+[ivx]+\s*$", re.IGNORECASE)


def normalize_skill_name(raw_name: str) -> str:
    """
    Normalize a skill name for storage.

    Rules:
    - Convert to lowercase
    - Drop control characters (they have no place in a skill name and would
      travel into the page as a data-skill attribute value)
    - Strip parenthetical modifiers like (+1 to hit)
    - Remove trailing Roman numerals (I, II, III, IV, V)
    - Collapse whitespace

    Quotes and apostrophes are deliberately left alone -- "wolf's bane" is a
    legitimate skill name -- and the templates escape them for both text and
    attribute contexts.

    Examples:
    - "Sword" -> "sword"
    - "Sword (+1 to hit)" -> "sword to hit"
    - "Shield Parry II" -> "shield parry"
    - "  Navigation  " -> "navigation"
    """
    name = strip_control_characters(raw_name).lower()

    # Remove parenthetical modifiers but keep content like "to hit"
    # Match patterns like "(+1 to hit)" and extract "to hit" part. Every match
    # ends on a ")", so nothing past the last one can match.
    closed = name[: name.rfind(")") + 1]
    paren_match = _BONUS_IN_PARENTHESES.search(closed)
    if paren_match:
        suffix = paren_match.group(1)
        name = _PARENTHESES.sub("", closed) + name[len(closed) :]
        name = f"{name.strip()} {suffix}"

    # Remove trailing Roman numerals (case-insensitive since we lowercased)
    name = _ROMAN_NUMERAL_SUFFIX.sub("", name)

    # Collapse whitespace
    name = " ".join(name.split())

    return name
