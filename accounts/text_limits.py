"""Each text an account page takes, and its limit (issue #943).

The account side of #928 and #938. The register, sign-in, profile and
password pages typed their ``maxlength`` and ``minlength`` out as literal
numbers, restating the shared Origami user model's columns, the shared
registration form's limits and the password validator's minimum with nothing
tying them together. The pages read ``account_text_limits.<name>`` from
:data:`ACCOUNT_TEXT_LIMITS` instead, so an input stops where the server would
refuse, and nowhere else.
"""

from django import forms
from django.contrib.auth.password_validation import (
    MinimumLengthValidator,
    get_default_password_validators,
)
from django.core.exceptions import ImproperlyConfigured
from origami_auth.forms import RegistrationForm as SharedRegistrationForm

from accounts.models import User
from common.helpers import column_length


def registration_limits() -> tuple[int, int]:
    """The shared registration form's username and password limits.

    Neither is a column: ``accounts.User.username`` holds 150 characters, but
    origami-auth's ``RegistrationForm`` narrows a new name to fewer in its
    ``__init__`` and refuses a longer one in ``clean_username``; a password is
    stored as a hash, and the form puts its typed limit on the password
    inputs' ``maxlength`` and refuses past it in ``_validate_password_length``.
    The library names neither as a constant, so they are read off a fresh form,
    the one place it states them as numbers. ``test_text_limits`` holds the
    form's refusals to the same numbers.

    Returns:
        The longest username a new account may take, and the longest password
        the form accepts.

    Raises:
        TypeError: The form no longer states one of them as a number.
    """
    fields = SharedRegistrationForm().fields
    username_field = fields["username"]
    username_limit = (
        username_field.max_length
        if isinstance(username_field, forms.CharField)
        else None
    )
    password_limit = fields["password1"].widget.attrs.get("maxlength")
    if not isinstance(username_limit, int) or not isinstance(password_limit, int):
        raise TypeError(
            "RegistrationForm no longer states its username or password limit"
        )
    return username_limit, password_limit


def password_minimum() -> int:
    """The longest ``min_length`` of the configured ``MinimumLengthValidator``.

    Read from the validators ``AUTH_PASSWORD_VALIDATORS`` builds, the ones
    every form that sets a password runs, so the page's ``minlength`` and its
    hint are the validators' own option. Every one of them runs, so when more
    than one is configured the longest minimum is the one a password meets.

    Returns:
        The fewest characters a new password may have.

    Raises:
        ImproperlyConfigured: No ``MinimumLengthValidator`` is configured.
    """
    minimums = [
        validator.min_length
        for validator in get_default_password_validators()
        if isinstance(validator, MinimumLengthValidator)
    ]
    if minimums:
        return max(minimums)
    raise ImproperlyConfigured(
        "AUTH_PASSWORD_VALIDATORS sets no MinimumLengthValidator"
    )


NEW_USERNAME_LIMIT, PASSWORD_LIMIT = registration_limits()

#: Each account text's limit by the name the pages read it under. Every path
#: that stores one refuses a value past it: the registration form, the profile
#: endpoint (the column's own validators), the email-change form
#: (:class:`accounts.forms.EmailChangeForm`), an Admin setting another
#: account's address (:class:`accounts.forms.NewAddressForm`, issue #954) and
#: the reset-confirm form (:class:`accounts.forms.NewPasswordForm`); the reset
#: request refuses an address past the column. Nothing cuts a value short.
ACCOUNT_TEXT_LIMITS: dict[str, int] = {
    # A new account's name: the registration form's, narrower than the column.
    "username": NEW_USERNAME_LIMIT,
    # A name being signed in with: the column. Django's sign-in form states
    # it as its field's max_length but runs no validator on it; the column is
    # what refuses a longer name, when an account is saved, so no account can
    # hold one. An account the admin or createsuperuser made may carry a name
    # longer than registration allows, and the sign-in page must take it.
    "sign_in_username": column_length(User, "username"),
    # A new password, typed: the registration form's, held on reset as well.
    "password": PASSWORD_LIMIT,
    "password_min": password_minimum(),
    "real_name": column_length(User, "real_name"),
    "email": column_length(User, "email"),
    "phone": column_length(User, "phone"),
    "discord": column_length(User, "discord"),
}
