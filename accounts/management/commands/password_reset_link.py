"""Print a password-reset link for an account (issue #929).

The same signed link the reset email carries, for the host operator to hand
over when the account has no email address or the mail is not working::

    uv run python manage.py password_reset_link spencer
    uv run python manage.py password_reset_link spencer --base-url https://tarmar.origamisoftware.com

It is the same trust level as ``manage.py changepassword``: whoever can run it
can take over the account, and it changes nothing until the link is used. The
link works once and expires after ``PASSWORD_RESET_TIMEOUT`` (Django's
default, three days). Only the link goes to stdout, so it can be piped.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser

from accounts.password_reset import default_base_url, reset_link
from common.base_url import BASE_URL_RULE, is_base_url


class Command(BaseCommand):
    help = (
        "Print the signed password-reset link for USERNAME, the one the reset "
        "email would carry. --base-url defaults to SITE_BASE_URL, else "
        "https://<first ALLOWED_HOSTS entry>, or http://127.0.0.1:8000 under "
        "DEBUG; with no concrete host in production, --base-url is required."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("username", help="The account to reset.")
        parser.add_argument(
            "--base-url",
            default=None,
            help=(
                "Scheme and host the link starts with, e.g. "
                "https://tarmar.origamisoftware.com. Default: SITE_BASE_URL, "
                "else https://<first ALLOWED_HOSTS entry> (the dev server "
                "under DEBUG)."
            ),
        )

    def handle(self, *args: object, **options: object) -> None:
        username = str(options["username"])
        user_model = get_user_model()
        try:
            user = user_model._default_manager.get_by_natural_key(username)
        except user_model.DoesNotExist as error:
            raise CommandError(f"No account with username {username!r}.") from error
        if not user.is_active:
            raise CommandError(
                f"The account {username!r} is inactive, so it could not sign in "
                "with a new password. Reactivate it first."
            )
        base_url = options["base_url"] or default_base_url()
        if base_url is None:
            raise CommandError(
                "ALLOWED_HOSTS names no concrete hostname to build the link on. "
                "Pass --base-url, e.g. --base-url https://tarmar.origamisoftware.com."
            )
        if not is_base_url(str(base_url)):
            raise CommandError(
                f"{base_url!r} is not a base URL. It needs {BASE_URL_RULE}."
            )
        self.stdout.write(reset_link(user, str(base_url)))
        self.stderr.write(
            "The link works once and expires after PASSWORD_RESET_TIMEOUT "
            "(three days by default). Anyone holding it can set this "
            "account's password."
        )
