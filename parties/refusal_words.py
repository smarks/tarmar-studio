"""The words the parties app refuses a request in (issue #136).

Content, not code (the house rule of 2026-10-17, issue #77): the refusals of
the party pages and of a disbanded party chosen by hand live in
``config/parties-refusals.toml``, one table per module, which Spencer edits
with no Python. settings.py reads it once at startup into
``settings.PARTIES_REFUSAL_WORDS`` through :func:`load_refusal_words`;
:func:`common.wording.load_wording` checks it there, so a missing key or a stray
``{name}`` stops the server rather than reaching a player.

Moving a refusal's words here changes only what the player reads, never who is
refused: the condition in front of each refusal decides that.

settings.py imports this module while it is still loading, so nothing here
reads the settings at import time (``django.conf.settings`` is lazy).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from common.wording import load_wording, sentence


@dataclass(frozen=True)
class ModelsWords:
    """``parties.models``'s refusal: a disbanded party chosen by hand."""

    disbanded: str = sentence("party")


@dataclass(frozen=True)
class ViewsWords:
    """``parties.views``'s refusals: the roster's controls and the autosave."""

    owner_only_add: str = sentence()
    numeric_membership_id: str = sentence()
    already_in_party: str = sentence("name")
    remove_denied: str = sentence()
    request_not_json: str = sentence()
    field_not_editable: str = sentence()
    text_value_required: str = sentence()


@dataclass(frozen=True)
class PartiesRefusalWords:
    """The whole file: one table per module."""

    models: ModelsWords
    views: ViewsWords


def load_refusal_words(path: Path) -> PartiesRefusalWords:
    """The parties app's refusals, read from ``path``.

    Args:
        path: The TOML file, normally ``config/parties-refusals.toml`` or
            whatever ``PARTIES_REFUSAL_WORDS_FILE`` names.

    Returns:
        The words, one dataclass per table.

    Raises:
        ImproperlyConfigured: as :func:`common.wording.load_wording` does.
    """
    return load_wording(path, PartiesRefusalWords, "PARTIES_REFUSAL_WORDS_FILE")


def refusal_words() -> PartiesRefusalWords:
    """The loaded words (``settings.PARTIES_REFUSAL_WORDS``)."""
    return settings.PARTIES_REFUSAL_WORDS
