"""The character, skills and experience rules a test runs under (#59, #70).

Character creation reads its numbers from the loaded canon through
:func:`characters.rules.character_rules`, and skills and prior experience
through :func:`characters.skill_rules.skill_rules`. The drift tests hold the
default canon's files to the public pages whichever canon is loaded, so they
read :data:`DEFAULT_CHARACTER_RULES` and :data:`DEFAULT_SKILL_RULES`; a test
that needs the code to run under particular rules seats them with
:func:`seated_character_rules` or :func:`seated_skill_rules`.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

from characters.rules import CharacterRules, character_rules, load_character_rules
from characters.skill_rules import SkillRules, load_skill_rules, skill_rules
from gm.engine import clear_prompt_caches
from reference.paths import (
    CANON_CHARACTER_RULES_FILE_NAME,
    CANON_SKILL_RULES_FILE_NAME,
    DEFAULT_CANON_DIR,
)

#: The default canon's rules: the numbers the public character pages state.
DEFAULT_CHARACTER_RULES: CharacterRules = load_character_rules(
    DEFAULT_CANON_DIR / CANON_CHARACTER_RULES_FILE_NAME
)

#: The default canon's skills and experience rules: the numbers the public
#: attributes, prior experience and skills pages state.
DEFAULT_SKILL_RULES: SkillRules = load_skill_rules(
    DEFAULT_CANON_DIR / CANON_SKILL_RULES_FILE_NAME
)


def _forget_loaded_rules() -> None:
    """Forget both rules files and every prompt built from them.

    The Games Master's prompts are built from the loaded rules once and held
    for the process (``gm.engine.clear_prompt_caches``), so a seated test that
    reads a prompt reads one built from the seated rules, and no later test
    reads one built from them.
    """
    character_rules.cache_clear()
    skill_rules.cache_clear()
    clear_prompt_caches()


@contextmanager
def seated_character_rules(
    rules: CharacterRules = DEFAULT_CHARACTER_RULES,
) -> Iterator[None]:
    """Seat ``rules`` as the loaded canon's for every caller, then forget them.

    Args:
        rules: The rules :func:`characters.rules.character_rules` answers with.
    """
    _forget_loaded_rules()
    try:
        with patch("characters.rules.load_character_rules", return_value=rules):
            yield
    finally:
        _forget_loaded_rules()


@contextmanager
def seated_skill_rules(rules: SkillRules = DEFAULT_SKILL_RULES) -> Iterator[None]:
    """Seat ``rules`` as the loaded canon's for every caller, then forget them.

    Args:
        rules: The rules :func:`characters.skill_rules.skill_rules` answers
            with.
    """
    _forget_loaded_rules()
    try:
        with patch("characters.skill_rules.load_skill_rules", return_value=rules):
            yield
    finally:
        _forget_loaded_rules()
