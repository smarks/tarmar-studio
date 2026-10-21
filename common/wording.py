"""Words the pages show, read from TOML files at startup (issues #77, #37, #97).

The house rule of 2026-10-17: a sentence a player reads, and that someone may
want to change, is configuration with its default in the repo, not a literal
in code. Each app that has such sentences keeps them in its own file under
``config/`` -- ``battle-rulings.toml``, ``battle-refusals.toml``,
``gm-refusals.toml`` -- which Spencer edits with no Python, and settings.py
reads each once at startup through :func:`load_wording`. A missing or unknown
key, a value that is not text, and a ``{name}`` a sentence may not use all stop
the server then, not in front of a player.

A file is described by a frozen dataclass whose fields are its tables, each
table a frozen dataclass whose fields are its keys, so code and templates read
a sentence as an attribute and no key is spelled twice. A key's field is made
with :func:`sentence`, which names the ``{placeholders}`` that sentence may use;
a sentence may leave any of them out, except the names a field requires (each carries another sentence).

settings.py imports this module while it is still loading, so nothing here
reads the settings.
"""

from __future__ import annotations

import string
import tomllib
from dataclasses import field, fields
from pathlib import Path
from typing import Any, get_type_hints

from django.core.exceptions import ImproperlyConfigured

from common.message_templates import MessageTemplateError, require_bare_fields

#: The field metadata key naming the placeholders a sentence may use.
PLACEHOLDERS = "placeholders"

#: The field metadata key naming the placeholders a sentence must use (#90):
#: those whose loss would drop a whole line, a cost or a ruling the sentence
#: carries ({line}, {cost}, {past}, {odds} and their kin). A name whose loss
#: drops only a detail ({move} in no_nearer and ends_from, {average},
#: {weapon}, {grapple}, {aimed}, {name}) stays optional, as every other
#: placeholder is.
REQUIRED = "required"


def sentence(*placeholders: str, required: tuple[str, ...] = ()) -> Any:
    """A wording field whose sentence may use ``placeholders``.

    Args:
        *placeholders: The ``{names}`` the sentence may fill.
        required: The names among them the sentence must use, because each
            carries another sentence, a cost or a ruling (#90).

    Returns:
        A dataclass field carrying both sets in its metadata.
    """
    return field(
        metadata={
            PLACEHOLDERS: frozenset(placeholders),
            REQUIRED: frozenset(required),
        }
    )


def _table(where: str, raw: Any, words_type: type) -> Any:
    """One table of a file as its dataclass, or ``ImproperlyConfigured``."""
    if not isinstance(raw, dict):
        raise ImproperlyConfigured(f"{where} is missing or is not a table")
    known = {word_field.name: word_field for word_field in fields(words_type)}
    unknown = sorted(set(raw) - set(known))
    missing = sorted(set(known) - set(raw))
    if unknown or missing:
        raise ImproperlyConfigured(
            f"{where}: unknown keys {unknown}, missing keys {missing}"
        )
    for key, word_field in known.items():
        value = raw[key]
        if not isinstance(value, str) or not value.strip():
            raise ImproperlyConfigured(f"{where} {key} is not a sentence")
        allowed = word_field.metadata[PLACEHOLDERS]
        # A trial fill with every name the sentence may use catches what a
        # player would otherwise meet: a name it may not use, a stray or
        # unbalanced brace, a positional {} or a nested {name:{spec}}.
        try:
            value.format(**dict.fromkeys(allowed, ""))
        except (KeyError, IndexError, ValueError, AttributeError) as error:
            raise ImproperlyConfigured(
                f"{where} {key} cannot be filled ({error!r}); it may use only "
                f"{sorted(allowed)}"
            ) from error
        # An empty trial value fills shapes the real ones break on, and the
        # page fills some of these itself (#94): each field is a bare name.
        try:
            require_bare_fields(f"{where} {key}", value)
        except MessageTemplateError as error:
            raise ImproperlyConfigured(str(error)) from error
        used = {
            field_name
            for _text, field_name, _spec, _conversion in string.Formatter().parse(value)
        }
        dropped = sorted(word_field.metadata[REQUIRED] - used)
        if dropped:
            raise ImproperlyConfigured(
                f"{where} {key} must use {dropped}: each carries another sentence"
            )
        # A sentence with no placeholders is sent as it is, never formatted, so
        # a doubled brace would reach the reader doubled.
        if not allowed and ("{{" in value or "}}" in value):
            raise ImproperlyConfigured(
                f"{where} {key} takes no placeholders, so it cannot write a "
                f"doubled brace; write a single brace"
            )
    return words_type(**raw)


def load_wording[Wording](
    path: Path, wording_type: type[Wording], setting: str
) -> Wording:
    """A wording file's sentences, read from ``path`` and checked.

    Args:
        path: The TOML file.
        wording_type: The dataclass describing the file, one field per table.
        setting: The setting that names the file, quoted in every refusal so
            whoever reads the startup error knows which file to open.

    Returns:
        The words, one dataclass per table.

    Raises:
        ImproperlyConfigured: The file cannot be read or is not TOML; a table
            is missing or unknown, or has an unknown or a missing key; a value
            is not text; or a sentence uses a ``{name}`` it may not.
    """
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ImproperlyConfigured(
            f"{setting} {path} cannot be read ({error}); the pages need their "
            "words from it (issues #37, #97)."
        ) from error
    tables = get_type_hints(wording_type)
    unknown = sorted(set(raw) - set(tables))
    if unknown:
        raise ImproperlyConfigured(f"{setting} {path}: unknown tables {unknown}")
    return wording_type(
        **{
            name: _table(f"{setting} {path} [{name}]", raw.get(name), words_type)
            for name, words_type in tables.items()
        }
    )


def fixed_wording[Wording](wording_type: type[Wording]) -> Wording:
    """Every sentence of ``wording_type`` as ``"<table>.<key> {name} ..."``.

    For tests: a shipped wording file is Spencer's to edit, so no test pins its
    sentences. A test runs under these words instead, in which each sentence
    is distinct and carries every ``{name}`` it may use, so the test still
    proves which sentence was chosen and what filled it (the gate hints'
    pattern, #77).

    Args:
        wording_type: The dataclass describing a wording file.

    Returns:
        An instance of it holding the fixed words.
    """
    tables = {}
    for table, words_type in get_type_hints(wording_type).items():
        tables[table] = words_type(
            **{
                word_field.name: " ".join(
                    [
                        f"{table}.{word_field.name}",
                        *(
                            f"{{{placeholder}}}"
                            for placeholder in sorted(word_field.metadata[PLACEHOLDERS])
                        ),
                    ]
                )
                for word_field in fields(words_type)
            }
        )
    return wording_type(**tables)
