"""The character rules a test runs under (issue #59).

Character creation reads its numbers from the loaded canon through
:func:`characters.rules.character_rules`. The drift tests hold the default
canon's file to the public pages whichever canon is loaded, so they read
:data:`DEFAULT_CHARACTER_RULES`; a test that needs the code to run under
particular rules seats them with :func:`seated_character_rules`.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

from characters.rules import CharacterRules, character_rules, load_character_rules
from reference.paths import CANON_CHARACTER_RULES_FILE_NAME, DEFAULT_CANON_DIR

#: The default canon's rules: the numbers the public character pages state.
DEFAULT_CHARACTER_RULES: CharacterRules = load_character_rules(
    DEFAULT_CANON_DIR / CANON_CHARACTER_RULES_FILE_NAME
)


@contextmanager
def seated_character_rules(
    rules: CharacterRules = DEFAULT_CHARACTER_RULES,
) -> Iterator[None]:
    """Seat ``rules`` as the loaded canon's for every caller, then forget them.

    Args:
        rules: The rules :func:`characters.rules.character_rules` answers with.
    """
    character_rules.cache_clear()
    try:
        with patch("characters.rules.load_character_rules", return_value=rules):
            yield
    finally:
        character_rules.cache_clear()
