"""Loader for ``config/gm-address-words.toml`` (issue #177).

The file holds the words a Game Master's own message must carry before the
Games Master acts on a character the session owner does not own: the
collective words for the whole party, the affirmatives that answer a proposal,
and each character action's subject words. settings.py reads it once at
startup into ``settings.GM_ADDRESS_WORDS``; ``TurnPolicy.addresses`` is the one
reader. A missing list or an empty one stops the server at startup.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

COLLECTIVE_KEY = "collective"
AFFIRMATIVES_KEY = "affirmatives"
IGNORED_NAMES_KEY = "ignored_names"
MIN_NAME_CHARACTERS_KEY = "min_name_characters"
SUBJECTS_TABLE = "subjects"


@dataclass(frozen=True)
class AddressWords:
    """The loaded words, every one casefolded and stripped.

    Attributes:
        collective: Ways of addressing the whole party.
        affirmatives: Whole messages that only agree to a proposal.
        ignored_names: Words too ordinary to count as a character's name.
        min_name_characters: The shortest single-word name that counts.
        subjects: Each character action's subject words, by action name.
    """

    collective: tuple[str, ...]
    affirmatives: tuple[str, ...]
    ignored_names: tuple[str, ...]
    min_name_characters: int
    subjects: Mapping[str, tuple[str, ...]]


def _word_list(path: Path, key: str, raw: object) -> tuple[str, ...]:
    """``raw`` as a tuple of casefolded phrases; refuse anything else."""
    if (
        not isinstance(raw, list)
        or not raw
        or any(not isinstance(value, str) or not value.strip() for value in raw)
    ):
        raise ImproperlyConfigured(
            f"{path}: {key} must be a list of one or more words (issue #177)."
        )
    return tuple(value.strip().casefold() for value in raw)


def load_address_words(path: Path) -> AddressWords:
    """Read the address words from ``path``.

    Args:
        path: The TOML file, normally ``config/gm-address-words.toml``.

    Returns:
        The words.

    Raises:
        ImproperlyConfigured: The file cannot be read or parsed, a list is
            missing or empty, or the subjects table is missing.
    """
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ImproperlyConfigured(
            f"{path} cannot be read as the address words ({error}); issue #177."
        ) from error
    subjects_table = document.get(SUBJECTS_TABLE)
    if not isinstance(subjects_table, dict) or not subjects_table:
        raise ImproperlyConfigured(
            f"{path}: a [{SUBJECTS_TABLE}] table with one list per character "
            "action is required (issue #177)."
        )
    minimum = document.get(MIN_NAME_CHARACTERS_KEY)
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 1:
        raise ImproperlyConfigured(
            f"{path}: {MIN_NAME_CHARACTERS_KEY} must be a whole number of 1 or "
            "more (issue #177)."
        )
    return AddressWords(
        min_name_characters=minimum,
        collective=_word_list(path, COLLECTIVE_KEY, document.get(COLLECTIVE_KEY)),
        affirmatives=_word_list(path, AFFIRMATIVES_KEY, document.get(AFFIRMATIVES_KEY)),
        ignored_names=_word_list(
            path, IGNORED_NAMES_KEY, document.get(IGNORED_NAMES_KEY)
        ),
        subjects={
            action: _word_list(path, f"{SUBJECTS_TABLE}.{action}", words)
            for action, words in subjects_table.items()
        },
    )
