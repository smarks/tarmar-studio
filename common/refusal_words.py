"""The words every app answers a failed request in (issues #113, #139).

Content, not code (the house rule of 2026-10-17, issue #77): the sentence a
script caller of any JSON endpoint hears when the server fails, rather than a
rule refusing it, lives in ``config/common-refusals.toml``, which Spencer edits
with no Python. It is one sentence for every app, so it is kept here rather than
in one app's file. settings.py reads it once at startup into
``settings.COMMON_REFUSAL_WORDS`` through :func:`load_refusal_words`;
:func:`common.wording.load_wording` checks it there, so a missing key or a stray
``{name}`` stops the server rather than reaching a player.

settings.py imports this module while it is still loading, so nothing here
reads the settings at import time (``django.conf.settings`` is lazy).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from common.wording import load_wording, sentence


@dataclass(frozen=True)
class DecoratorsWords:
    """``common.decorators``'s answers: a JSON endpoint the server failed."""

    request_failed: str = sentence()


@dataclass(frozen=True)
class CommonRefusalWords:
    """The whole file: one table per module."""

    decorators: DecoratorsWords


def load_refusal_words(path: Path) -> CommonRefusalWords:
    """The words every app answers a failed request in, read from ``path``.

    Args:
        path: The TOML file, normally ``config/common-refusals.toml`` or
            whatever ``COMMON_REFUSAL_WORDS_FILE`` names.

    Returns:
        The words, one dataclass per table.

    Raises:
        ImproperlyConfigured: as :func:`common.wording.load_wording` does.
    """
    return load_wording(path, CommonRefusalWords, "COMMON_REFUSAL_WORDS_FILE")


def refusal_words() -> CommonRefusalWords:
    """The loaded words (``settings.COMMON_REFUSAL_WORDS``)."""
    return settings.COMMON_REFUSAL_WORDS
