"""Account forms: registration, email change, and the password-reset request.

Registration is the shared ``origami-auth`` form, with its refusals moved.

The form itself is bound to ``get_user_model()``, so it builds against this
app's ``accounts.User`` exactly as the previous in-app copy did (same fields,
username and password-length validation, profile fields). It is subclassed
here only to put a password refusal beside the password (issue #711).

The email-change form and the reset form's address filter are issue #931's:
the address on an account is a recovery credential, so changing it takes the
password, and the reset form mails only an address that has been proved.

:class:`NewPasswordForm` is the reset-confirm page's: Django's own, held to the
registration form's password limit (issue #943).
"""

from collections.abc import Iterator
from typing import Any

from django import forms
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from django.core.exceptions import ValidationError
from origami_auth.forms import RegistrationForm as SharedRegistrationForm

from accounts.models import User
from accounts.text_limits import ACCOUNT_TEXT_LIMITS
from characters.constants import too_long
from common.helpers import column_length

__all__ = [
    "EmailChangeForm",
    "NewAddressForm",
    "NewPasswordForm",
    "RegistrationForm",
    "VerifiedAddressPasswordResetForm",
]


class RegistrationForm(SharedRegistrationForm):
    """The shared form, with password complaints attached to the password.

    ``UserCreationForm`` runs Django's password validators inside
    ``clean_password2``, which is where the two entries are compared -- so
    "This password is too short. It must contain at least 8 characters."
    rendered under **Confirm Password**, a field that was typed correctly,
    while the Password field above it showed nothing at all (issue #711). A
    refusal belongs at the control that caused it.

    Only errors about the password *itself* move. A genuine mismatch between
    the two entries stays on the second field, which is the one that disagrees
    with the first.
    """

    #: What ``UserCreationForm`` calls the two-entries-differ refusal. Moving
    #: that one would put it on a field that may be perfectly well typed.
    MISMATCH_CODE = "password_mismatch"

    def _post_clean(self) -> None:
        """Run the shared post-clean, then re-file password refusals.

        ``UserCreationForm`` runs ``validate_password`` in ``_post_clean``, not
        in ``clean``, and files what it raises under ``password2`` -- so this
        has to be the hook, and it has to run after the superclass.
        """
        super()._post_clean()
        confirm_errors = self.errors.get("password2")
        if not confirm_errors:
            return
        by_error = confirm_errors.as_data()
        about_the_password = [
            error for error in by_error if error.code != self.MISMATCH_CODE
        ]
        if not about_the_password:
            return
        mismatches = [error for error in by_error if error.code == self.MISMATCH_CODE]
        del self.errors["password2"]
        for error in mismatches:
            self.add_error("password2", error)
        for error in about_the_password:
            self.add_error("password1", error)


class NewAddressForm(forms.Form):
    """A new address for an account, held to the column and normalised.

    The part of the change both paths share: the account holder's own change
    (:class:`EmailChangeForm`) and an Admin setting another account's address
    from the manager path (issue #954, ``accounts.views.profile_update_field``).
    """

    new_email = forms.EmailField(
        label="New email address", max_length=column_length(User, "email")
    )

    def __init__(self, user: User, *args: Any, **kwargs: Any) -> None:
        """Bind the form to the account whose address it changes.

        Args:
            user: The account whose address changes.
            *args: Passed to ``Form``.
            **kwargs: Passed to ``Form``.
        """
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_new_email(self) -> str:
        """Normalise the address; refuse the one already confirmed here.

        An address on file that has not been proved yet may be asked for
        again: that is how its confirmation link is sent a second time.

        Returns:
            The address with its domain lowercased.

        Raises:
            ValidationError: When it is the account's confirmed address.
        """
        address = User.objects.normalize_email(self.cleaned_data["new_email"].strip())
        if self.user.email_verified and address.lower() == self.user.email.lower():
            raise ValidationError(
                "That is already the confirmed address on this account.",
                code="unchanged",
            )
        return address


class EmailChangeForm(NewAddressForm):
    """Ask for a new address, re-authenticating with the account's password.

    A signed-in browser is not proof of who is at it: without the password,
    anyone left alone with one could point the address at their own inbox and
    take the account through password reset later (issue #931). This is
    ``PasswordChangeForm``'s rule applied to the address.
    """

    current_password = forms.CharField(
        label="Your password", strip=False, widget=forms.PasswordInput
    )

    def clean_current_password(self) -> str:
        """Refuse anything but the account's own password.

        Returns:
            The password, unchanged.

        Raises:
            ValidationError: When it is not the account's password.
        """
        password = self.cleaned_data["current_password"]
        if not self.user.check_password(password):
            raise ValidationError(
                "That is not the password for this account.",
                code="password_incorrect",
            )
        return password


class VerifiedAddressPasswordResetForm(PasswordResetForm):
    """Django's reset form, mailing only an address that has been proved.

    An address nobody has confirmed may be a typo or somebody else's, and
    mailing it a reset link would hand that inbox the account (issue #931).
    The form still answers the same way whatever was typed.
    """

    def get_users(self, email: str) -> Iterator[User]:
        """Django's matching accounts, less those whose address is unproved.

        Args:
            email: The address typed.

        Yields:
            Each active account with a usable password and this verified
            address.
        """
        for user in super().get_users(email):
            if isinstance(user, User) and user.email_verified:
                yield user


class NewPasswordForm(SetPasswordForm):
    """Django's set-a-new-password form, refusing what registration refuses.

    Registration refuses a password past the shared form's limit, and both
    pages share one password field that stops typing there; Django's form
    took any length, so a reset could set a password that registration would
    have refused and that the page itself would not let anyone type (issue
    #943). Both entries are checked, so a long confirmation is answered on
    the field that holds it.
    """

    def _refuse_too_long(self, field_name: str) -> str:
        """The entry in ``field_name``, refused when it passes the limit.

        Args:
            field_name: ``new_password1`` or ``new_password2``.

        Returns:
            The entry, unchanged.

        Raises:
            ValidationError: The entry is longer than the password limit.
        """
        password = self.cleaned_data.get(field_name) or ""
        refusal = too_long("Password", password, ACCOUNT_TEXT_LIMITS["password"])
        if refusal:
            raise ValidationError(refusal, code="password_too_long")
        return password

    def clean_new_password1(self) -> str:
        """The new password, refused past the limit."""
        return self._refuse_too_long("new_password1")

    def clean_new_password2(self) -> str:
        """The confirmation, refused past the limit."""
        return self._refuse_too_long("new_password2")
