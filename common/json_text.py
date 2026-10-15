"""The one guarded JSON parser, free of Django (issue #767).

``common.helpers`` re-exports :func:`parse_json_text` for the request
handlers; it lives here so the canon rules files (``reference.rules_file``),
which ``gm.engine``'s Django-free import chain may reach, can read JSON the
same guarded way without importing Django.
"""

import json
from collections.abc import Callable
from typing import Any


def parse_json_text(
    text: bytes | str,
    *,
    object_pairs_hook: Callable[[list[tuple[str, Any]]], Any] | None = None,
) -> object:
    """``json.loads`` for text a user sent, with every refusal a ``ValueError``.

    ``json.loads`` raises ``JSONDecodeError`` for malformed text, but also
    ``UnicodeDecodeError`` for bytes that are not UTF-8, ``ValueError`` for
    an integer longer than Python's digit limit, and ``RecursionError`` for
    deep nesting (``"[" * 100000``). The first three are ``ValueError``s;
    the last is not, so it is turned into one here.

    Args:
        text: The request body or uploaded file's bytes, or decoded text.
        object_pairs_hook: Passed through to ``json.loads``: builds each
            object from its key-value pairs, and may raise ``ValueError`` to
            refuse one (``reference.rules_file`` refuses a repeated key).

    Returns:
        The parsed JSON value.

    Raises:
        ValueError: The text is not JSON this server can read.
    """
    try:
        return json.loads(text, object_pairs_hook=object_pairs_hook)
    except RecursionError:
        raise ValueError("The JSON is nested too deeply") from None
