"""Mark an account's email address proved or unproved (issue #931).

Password reset mails only an address that has been proved by opening a
confirmation link. Migration 0012 marked every address already on file as
proved, since each had been a working reset route until then. When the host
operator doubts one, or has proved one by other means::

    uv run python manage.py set_email_verified spencer false
    uv run python manage.py set_email_verified spencer true

``false`` stops the reset form mailing that address; the account holder can
prove it again from their profile (Change email address, the same address).
The change is written to the audit log. A pending address is left as it is.
"""

from django.core.management.base import BaseCommand, CommandError, CommandParser
from origami_common.audit import audit_logger

from accounts.models import User

#: What the command accepts for each state.
STATE_WORDS = {"true": True, "false": False}


class Command(BaseCommand):
    help = (
        "Set whether USERNAME's email address counts as confirmed: true or "
        "false. Password reset mails only a confirmed address."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("username", help="The account.")
        parser.add_argument(
            "state",
            choices=sorted(STATE_WORDS),
            help="true: the address is a reset route; false: it is not.",
        )

    def handle(self, *args: object, **options: object) -> None:
        username = str(options["username"])
        verified = STATE_WORDS[str(options["state"])]
        try:
            user = User.objects.get_by_natural_key(username)
        except User.DoesNotExist as error:
            raise CommandError(f"No account with username {username!r}.") from error
        if verified and not user.email:
            raise CommandError(
                f"The account {username!r} has no email address to confirm."
            )
        previous = user.email_verified
        user.email_verified = verified
        user.save(update_fields=["email_verified"])
        audit_logger.info(
            "EMAIL_VERIFIED_SET: operator set email_verified from %s to %s on "
            "user=%s (pk=%s)",
            previous,
            verified,
            user.get_username(),
            user.pk,
        )
        state = "confirmed" if verified else "not confirmed"
        self.stdout.write(f"{username}: {user.email or '(no address)'} is now {state}.")
