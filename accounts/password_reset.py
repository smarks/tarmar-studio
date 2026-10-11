"""The signed password-reset link, built outside a request (issue #929).

``django.contrib.auth.forms.PasswordResetForm.save`` builds the link the reset
email carries from the user's primary key and a token from
``default_token_generator``. ``manage.py password_reset_link`` hands the host
operator the same link for an account that has no email address, or for a
site whose mail is not working yet, so it has to be built exactly the same
way; this module is that one construction.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from common.base_url import is_base_url

__all__ = [
    "DEV_BASE_URL",
    "default_base_url",
    "is_base_url",
    "reset_link",
    "reset_path",
    "site_link_context",
]

#: Where a development server listens when nothing says otherwise.
DEV_BASE_URL = "http://127.0.0.1:8000"


def reset_path(user: AbstractBaseUser) -> str:
    """The confirm page's path for ``user``, token included.

    Args:
        user: The account whose password the link resets.

    Returns:
        A path such as ``/tarmar/accounts/password_reset/MQ/<token>/``.
    """
    user_pk_bytes = force_bytes(user._meta.pk.value_to_string(user))
    return reverse(
        "password_reset_confirm",
        kwargs={
            "uidb64": urlsafe_base64_encode(user_pk_bytes),
            "token": default_token_generator.make_token(user),
        },
    )


def default_base_url() -> str | None:
    """The scheme and host a link is built on when none is given.

    ``SITE_BASE_URL`` when it is set. Under DEBUG, the development server's
    own address, since ``ALLOWED_HOSTS`` names no port. Otherwise https on the
    first concrete hostname in ``ALLOWED_HOSTS`` -- the one ``deploy.sh``
    takes as the public name.

    Returns:
        A base URL with no trailing slash, or None in production when
        ``ALLOWED_HOSTS`` holds only wildcards: a link to the development
        address would be useless there, so the caller must ask for one.
    """
    configured = getattr(settings, "SITE_BASE_URL", "")
    if configured:
        return configured.rstrip("/")
    if settings.DEBUG:
        return DEV_BASE_URL
    for host in settings.ALLOWED_HOSTS:
        if host and host != "*" and not host.startswith("."):
            return f"https://{host}"
    return None


def site_link_context() -> dict[str, str] | None:
    """The reset email's link parts when ``SITE_BASE_URL`` fixes them.

    Django builds the emailed link from the request's Host header and scheme.
    With ``SITE_BASE_URL`` set, the link is built on that instead, so it never
    depends on what a request claimed. These keys override Django's own in the
    email context.

    Returns:
        ``protocol``, ``domain`` and ``site_name`` from ``SITE_BASE_URL``, or
        None when it is unset and the request decides.
    """
    configured = getattr(settings, "SITE_BASE_URL", "")
    if not configured:
        return None
    parts = urlsplit(configured)
    return {
        "protocol": parts.scheme,
        "domain": parts.netloc,
        "site_name": parts.netloc,
    }


def reset_link(user: AbstractBaseUser, base_url: str) -> str:
    """The full link for ``user`` on ``base_url``.

    Args:
        user: The account whose password the link resets.
        base_url: Scheme and host, e.g. ``https://tarmar.origamisoftware.com``.

    Returns:
        The absolute URL of the confirm page, token included.
    """
    return f"{base_url.rstrip('/')}{reset_path(user)}"
