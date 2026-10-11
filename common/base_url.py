"""What counts as a base URL for a link the site hands out (issue #929).

Django-free on purpose: settings validates ``SITE_BASE_URL`` with this at
import, before apps load, and ``manage.py password_reset_link`` checks its
``--base-url`` with the same rule.
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: The rule, in words, for error messages.
BASE_URL_RULE = (
    "an http or https scheme and a host, with no path, query or fragment, "
    "e.g. https://tarmar.origamisoftware.com"
)


def is_base_url(candidate: str) -> bool:
    """Whether ``candidate`` is a scheme and host a link can start with.

    Args:
        candidate: The value to check, e.g. ``https://tarmar.example``.

    Returns:
        True for an http or https URL with a host and nothing after it but
        an optional trailing slash.
    """
    parts = urlsplit(candidate)
    return (
        parts.scheme in ("http", "https")
        and bool(parts.netloc)
        and parts.path in ("", "/")
        and not parts.query
        and not parts.fragment
    )


#: The rule for an address that links may continue from (issue #914).
BASE_URL_WITH_PATH_RULE = (
    "an http or https scheme and a host, optionally followed by a path, "
    "with no query or fragment, e.g. https://github.com/smarks/tarmar-studio"
)


def is_base_url_with_path(candidate: str) -> bool:
    """Whether ``candidate`` is a scheme, host and optional path a link can extend.

    Like :func:`is_base_url`, but a path is allowed: a repository's address
    is ``https://github.com/<owner>/<name>``, and links are built beneath it.

    Args:
        candidate: The value to check, e.g. ``https://github.com/smarks/repo``.

    Returns:
        True for an http or https URL with a host and no query or fragment.
    """
    parts = urlsplit(candidate)
    return (
        parts.scheme in ("http", "https")
        and bool(parts.netloc)
        and not parts.query
        and not parts.fragment
    )
