"""Registration form — the shared ``origami-auth`` one, with its refusals moved.

The form itself is bound to ``get_user_model()``, so it builds against this
app's ``accounts.User`` exactly as the previous in-app copy did (same fields,
username and password-length validation, profile fields). It is subclassed
here only to put a password refusal beside the password (issue #711).
"""

from origami_auth.forms import RegistrationForm as SharedRegistrationForm

__all__ = ["RegistrationForm"]


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
