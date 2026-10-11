"""Changing an account's email address, proved by a link sent to it (#931).

Since password reset (#929) the address on an account is a recovery
credential: whoever reads mail there can set the password. So an address only
becomes the account's address, and only becomes one the reset form will mail,
once somebody has opened a link sent to it. Until then it waits in
``User.pending_email``.

The link is Django's own construction, the one the reset link uses: the
account's primary key in base64 and a token from a
:class:`~django.contrib.auth.tokens.PasswordResetTokenGenerator` with its own
salt and its own hash value. That gives it the reset link's properties without
a scheme of this site's making: it expires after ``PASSWORD_RESET_TIMEOUT``
(three days), it is signed with ``SECRET_KEY``, it stops working once it has
been used (confirming clears the pending address it was made for), and it
stops working when a newer request replaces that address or the password
changes, so resetting a stolen password also kills a change the thief started.
"""

from __future__ import annotations

import enum
import logging
import smtplib
from typing import Any

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from origami_common.audit import audit_logger

from accounts.mail_allowance import (
    address_allowance_spent,
    address_digest,
    charge_address,
)
from accounts.models import User
from accounts.password_reset import site_link_context

logger = logging.getLogger(__name__)

#: The URL name of the page the confirmation link opens.
CONFIRM_URL_NAME = "email_confirm"


class EmailChangeTokenGenerator(PasswordResetTokenGenerator):
    """Django's reset-token generator, hashing the pending change instead.

    The reset generator hashes ``last_login``, which would kill a pending
    link the moment its owner signed in on another device; this one hashes
    the pending address and when it was asked for in its place, and keeps
    the password hash, so the link is spent by use, by a newer request, and
    by a password change.
    """

    key_salt = "accounts.email_change.EmailChangeTokenGenerator"

    def _make_hash_value(self, user: AbstractBaseUser, timestamp: int) -> str:
        """The account state the token is bound to.

        Args:
            user: The account the link confirms an address for.
            timestamp: When the token was made, in Django's token seconds.

        Returns:
            The string the token's HMAC is taken over.

        Raises:
            TypeError: When handed anything but this site's user model.
        """
        if not isinstance(user, User):
            raise TypeError(f"expected accounts.User, got {type(user).__name__}")
        sent_at = (
            ""
            if user.pending_email_sent_at is None
            else str(int(user.pending_email_sent_at.timestamp()))
        )
        return (
            f"{user.pk}{user.password}{user.email}{user.email_verified}"
            f"{user.pending_email}{sent_at}{timestamp}"
        )


email_change_token_generator = EmailChangeTokenGenerator()


class ConfirmationOutcome(enum.Enum):
    """What :func:`request_confirmation` did."""

    SENT = "sent"
    ADDRESS_LIMITED = "address_limited"
    SEND_FAILED = "send_failed"


def site_link_parts(request: HttpRequest) -> dict[str, str]:
    """The scheme and host a link in a message is built on.

    ``SITE_BASE_URL`` when it is set, as for the reset email; otherwise the
    request's own scheme and (``ALLOWED_HOSTS``-checked) host.

    Args:
        request: The request that is sending the message.

    Returns:
        ``protocol``, ``domain`` and ``site_name``.
    """
    fixed = site_link_context()
    if fixed is not None:
        return fixed
    domain = request.get_host()
    return {
        "protocol": "https" if request.is_secure() else "http",
        "domain": domain,
        "site_name": domain,
    }


def confirm_path(user: User) -> str:
    """The confirmation page's path for ``user``'s pending address.

    Args:
        user: An account with ``pending_email`` and its sent time set.

    Returns:
        A path such as ``/tarmar/accounts/password_reset/address/MQ/<token>/``.
    """
    return reverse(
        CONFIRM_URL_NAME,
        kwargs={
            "uidb64": urlsafe_base64_encode(force_bytes(user.pk)),
            "token": email_change_token_generator.make_token(user),
        },
    )


def _send(template_prefix: str, address: str, context: dict[str, Any]) -> None:
    """Render and send one plain-text message.

    Args:
        template_prefix: ``accounts/<prefix>_subject.txt`` and
            ``accounts/<prefix>_email.txt`` are the subject and body.
        address: The one recipient.
        context: The templates' context.
    """
    subject = render_to_string(f"accounts/{template_prefix}_subject.txt", context)
    body = render_to_string(f"accounts/{template_prefix}_email.txt", context)
    send_mail("".join(subject.splitlines()), body, None, [address])


def request_confirmation(
    request: HttpRequest,
    user: User,
    address: str,
    *,
    notify_previous: bool,
    set_by: User | None = None,
) -> ConfirmationOutcome:
    """Make ``address`` the account's pending address and mail it the link.

    The address on file does not change here, unless ``set_by`` is given. The
    per-address allowance (``accounts.mail_allowance``) is checked and charged
    first, the link is sent next, and the account is saved only once the link
    has gone, so a failed send leaves the account as it was. Then, when
    ``notify_previous`` is set, :func:`notify_previous_address` tells the
    address the account had.

    When ``set_by`` is given, an Admin is setting another account's address
    (issue #954): ``address`` becomes the address on file at once, unconfirmed
    (``email_verified`` False, so password reset does not mail it), and is
    also the pending address its link confirms. The audit line names both
    accounts. The caller has decided ``set_by`` may do this.

    Args:
        request: The request asking, for the link's host.
        user: The account.
        address: The address to confirm.
        notify_previous: Whether to send the notice to the address on file.
        set_by: The Admin setting the address, or None when the account
            holder is asking for the change.

    Returns:
        Whether the link was sent, refused by the allowance, or failed.
    """
    digest = address_digest(address)
    if address_allowance_spent(address):
        audit_logger.info(
            "EMAIL_CONFIRMATION_SUPPRESSED: target limit reached for email hash "
            "%s, user=%s (pk=%s)%s",
            digest[:12],
            user.get_username(),
            user.pk,
            _set_by_clause(set_by),
        )
        return ConfirmationOutcome.ADDRESS_LIMITED
    charge_address(address)

    previous_address, previous_verified = user.email, user.email_verified
    previous_pending = (user.pending_email, user.pending_email_sent_at)
    changed_fields = ["pending_email", "pending_email_sent_at"]
    if set_by is not None:
        # Set before the token is made: it hashes the address on file and its
        # flag, so these are the values the link is bound to.
        user.email, user.email_verified = address, False
        changed_fields += ["email", "email_verified"]
    user.pending_email = address
    # Whole seconds, so the value the token hashes is the one the database
    # gives back on every backend.
    user.pending_email_sent_at = timezone.now().replace(microsecond=0)
    context = {
        **site_link_parts(request),
        "username": user.get_username(),
        "new_email": address,
        "confirm_path": confirm_path(user),
        "set_by_admin": set_by is not None,
    }
    try:
        _send("email_change_confirm", address, context)
    except (smtplib.SMTPException, OSError):
        logger.exception(
            "Failed to send the email confirmation link for user pk=%s%s",
            user.pk,
            _set_by_clause(set_by),
        )
        user.pending_email, user.pending_email_sent_at = previous_pending
        user.email, user.email_verified = previous_address, previous_verified
        return ConfirmationOutcome.SEND_FAILED
    user.save(update_fields=changed_fields)
    if set_by is None:
        audit_logger.info(
            "EMAIL_CHANGE_REQUESTED: user=%s (pk=%s) asked to make %s the "
            "account's address; a confirmation link was sent there",
            user.get_username(),
            user.pk,
            address,
        )
    else:
        audit_logger.info(
            "EMAIL_SET_BY_ADMIN: admin=%s (pk=%s) set the address of user=%s "
            "(pk=%s) from %s to %s, unconfirmed; a confirmation link was sent "
            "there",
            set_by.get_username(),
            set_by.pk,
            user.get_username(),
            user.pk,
            previous_address or "(none)",
            address,
        )

    if notify_previous:
        notify_previous_address(
            request,
            user,
            previous_address,
            previous_verified=previous_verified,
            address=address,
            set_by=set_by,
        )
    return ConfirmationOutcome.SENT


def _set_by_clause(set_by: User | None) -> str:
    """The words a journal line adds when an Admin is setting the address.

    Args:
        set_by: The Admin, or None when the account holder is asking.

    Returns:
        ``" set by admin=<name> (pk=<pk>)"``, or nothing.
    """
    if set_by is None:
        return ""
    return f" set by admin={set_by.get_username()} (pk={set_by.pk})"


def notify_previous_address(
    request: HttpRequest,
    user: User,
    previous_address: str,
    *,
    previous_verified: bool,
    address: str,
    set_by: User | None = None,
) -> None:
    """Tell the address the account had that it is being replaced.

    A notice, not a link. Only a confirmed old address, different from the
    new one, and only within its inbox's hourly allowance, which it is
    charged against: otherwise registering with a stranger's address and
    repeating the change form would mail that stranger a notice per request
    (#931 review). Skipped silently past any of these; the link to the new
    address, which is what the change waits on, has already gone.

    The Admin path (issue #954) calls this after its transaction, so the
    account's row is not held locked through a slow mail server.

    Args:
        request: The request that made the change, for the site's name.
        user: The account.
        previous_address: The address on file before the change.
        previous_verified: Whether that address was confirmed.
        address: The new address.
        set_by: The Admin who set the address, or None when the account
            holder asked for the change.
    """
    if (
        not previous_address
        or not previous_verified
        or previous_address.strip().lower() == address.strip().lower()
        or address_allowance_spent(previous_address)
    ):
        return
    charge_address(previous_address)
    context = {
        **site_link_parts(request),
        "username": user.get_username(),
        "set_by_username": "" if set_by is None else set_by.get_username(),
    }
    try:
        _send("email_change_notice", previous_address, context)
    except (smtplib.SMTPException, OSError):
        # The change still waits on the link, so a dead old address (the
        # usual reason to change it) must not block it; the journal says the
        # notice did not go.
        logger.exception(
            "Failed to send the email-change notice for user pk=%s%s",
            user.pk,
            _set_by_clause(set_by),
        )


def user_from_uid(uidb64: str) -> User | None:
    """The account a confirmation link names, the way Django's reset reads it.

    Args:
        uidb64: The base64 primary key from the link.

    Returns:
        The account, or None when the value names none.
    """
    try:
        primary_key = force_str(urlsafe_base64_decode(uidb64))
        return User.objects.get(pk=primary_key)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist, ValidationError):
        return None


def link_is_valid(user: User | None, token: str) -> bool:
    """Is ``token`` a live link for ``user``'s pending address?

    Args:
        user: The account the link names, or None.
        token: The token from the link.

    Returns:
        True when an address is pending and the token was made for exactly
        this pending request, inside the expiry.
    """
    return (
        user is not None
        and bool(user.pending_email)
        and email_change_token_generator.check_token(user, token)
    )


def confirm_pending_address(user: User) -> str:
    """Make the pending address the account's address, proved.

    Args:
        user: An account whose link has just been checked.

    Returns:
        The address now on the account.
    """
    previous_address = user.email
    user.email = user.pending_email
    user.email_verified = True
    user.pending_email = ""
    user.pending_email_sent_at = None
    user.save(
        update_fields=[
            "email",
            "email_verified",
            "pending_email",
            "pending_email_sent_at",
        ]
    )
    audit_logger.info(
        "EMAIL_CHANGE_CONFIRMED: user=%s (pk=%s) address changed from %s to %s",
        user.get_username(),
        user.pk,
        previous_address or "(none)",
        user.email,
    )
    return user.email
