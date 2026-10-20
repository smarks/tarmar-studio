"""Skill names as they are stored: ``normalize_skill_name`` (issue #70).

Moved out of ``characters.skills`` so ``characters.skill_rules``, which
refuses a canon skill name that is not already in stored form, can use it
without importing ``characters.skills``, which reads those rules: the two
would otherwise import each other. ``characters.skills`` re-exports it, so
its callers are unchanged.

Free of Django, like ``characters.constants``, which it reads.
"""

import re

from .constants import MAX_SKILL_NAME_LENGTH, strip_control_characters, too_long

# The two patterns normalize_skill_name applies, and the parenthesis scanner
# beside them. A skill name comes from a player's request, an imported file
# (up to 256 KB) or the model, so each runs in linear time (issue #11); the
# paths that take a name refuse one past the limit before they normalize it
# (:func:`skill_name_refusal`), but this function does not rely on that. The
# regular expressions only ever see short pieces with no repeated part that
# can take the same characters as its neighbour: the bonus's text starts on a
# non-space the whitespace before it cannot take. Parentheses are not matched
# by a pattern at all (issue #52): a pattern from "(" to the next ")" comes
# apart on a nested name, and one that balances them backtracks, so
# :func:`top_level_parentheses` pairs them in one pass with a stack.
_BONUS_CONTENT = re.compile(r"\+?\d+\s+(\S[^\n]*|[^\S\n])")
_PARENTHESIS_RUN = re.compile(r"\(+|\)+")
_ROMAN_NUMERAL_SUFFIX = re.compile(r"(?<!\s)\s+[ivx]+\s*$", re.IGNORECASE)


def skill_name_refusal(name: str) -> str:
    """Why a skill name is refused for its length, or ``""`` when it fits.

    A storage bound, not a rule of play, so it is the same for a Game Master.
    Every path that takes a name asks it of the name as received, before
    :func:`normalize_skill_name` reads it, so a crafted name is refused at the
    limit rather than scanned in full; and of the stored name after, which the
    record keeps (issue #928).

    Measured stripped, as the skill-check request and the chat's plan read a
    name, so no path refuses a name another accepts for its edge whitespace.

    Args:
        name: A skill name, raw or stored.

    Returns:
        The refusal, or ``""``.
    """
    return too_long("Skill name", name.strip(), MAX_SKILL_NAME_LENGTH)


def top_level_parentheses(name: str) -> list[tuple[int, int]]:
    """The balanced parenthesis groups of a name that no other group encloses.

    A ")" with no "(" before it and a "(" with no ")" after it belong to no
    group and are left as ordinary text; a group may hold others, and the
    whole group counts as one. One pass over the name, so linear however
    deeply it nests, with no recursion for a crafted name to exhaust. It works
    a run of the same parenthesis at a time, not a character at a time (a
    256 KB name of "()" is the worst case), and a run of ")" closes that many
    open groups in one step: each encloses the one before it, so only the
    outermost remains.

    Args:
        name: Any text; a skill name in either raw or stored form.

    Returns:
        Each group as ``(index of "(", index of its ")")``, in order.
    """
    # Nothing before the first "(" or after the last ")" can be in a group.
    first_opening = name.find("(")
    last_closing = name.rfind(")")
    if first_opening < 0 or last_closing < first_opening:
        return []
    open_positions: list[int] = []
    groups: list[tuple[int, int]] = []
    for run in _PARENTHESIS_RUN.finditer(name, first_opening, last_closing + 1):
        run_start, run_end = run.span()
        if name[run_start] == "(":
            open_positions.extend(range(run_start, run_end))
            continue
        closed = min(run_end - run_start, len(open_positions))
        if not closed:
            continue
        opening = open_positions[-closed]
        del open_positions[-closed:]
        # Groups found since this "(" opened lie inside it: drop them.
        while groups and groups[-1][0] > opening:
            groups.pop()
        groups.append((opening, run_start + closed - 1))
    return groups


def normalize_skill_name(raw_name: str) -> str:
    """
    Normalize a skill name for storage.

    Rules:
    - Convert to lowercase
    - Drop control characters (they have no place in a skill name and would
      travel into the page as a data-skill attribute value)
    - Strip parenthetical modifiers like (+1 to hit), keeping the text of
      the first bonus (and any balanced parentheses inside that text)
    - Remove trailing Roman numerals (I, II, III, IV, V)
    - Collapse whitespace

    Quotes and apostrophes are deliberately left alone -- "wolf's bane" is a
    legitimate skill name -- and the templates escape them for both text and
    attribute contexts.

    Examples:
    - "Sword" -> "sword"
    - "Sword (+1 to hit)" -> "sword to hit"
    - "Lore (+1 (old) tales)" -> "lore (old) tales"
    - "Shield Parry II" -> "shield parry"
    - "  Navigation  " -> "navigation"
    """
    name = strip_control_characters(raw_name).lower()

    # Remove parenthetical modifiers but keep content like "to hit": the
    # first group that reads "(+1 to hit)" gives its text, "to hit", and every
    # group goes. Only a group that nothing encloses can be the bonus, and its
    # text keeps any parentheses balanced inside it: "Lore (+1 (old) tales)"
    # is "lore (old) tales".
    groups = top_level_parentheses(name)
    for opening, closing in groups:
        bonus = _BONUS_CONTENT.fullmatch(name, opening + 1, closing)
        if bonus:
            kept: list[str] = []
            cursor = 0
            for group_opening, group_closing in groups:
                # Whitespace before a group goes with it.
                kept.append(name[cursor:group_opening].rstrip())
                cursor = group_closing + 1
            kept.append(name[cursor:])
            remainder = "".join(kept).strip()
            name = f"{remainder} {bonus.group(1)}"
            break

    # Remove trailing Roman numerals (case-insensitive since we lowercased)
    name = _ROMAN_NUMERAL_SUFFIX.sub("", name)

    # Collapse whitespace
    name = " ".join(name.split())

    return name
