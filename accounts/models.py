from django.db import models
from origami_auth.base import AbstractOrigamiUser

from common.helpers import column_length


class User(AbstractOrigamiUser):
    """Tarmar-studio's user, re-parented onto the shared abstract base.

    Every field, validator, role, and the ``can_manage_users`` property now live
    on :class:`origami_auth.base.AbstractOrigamiUser`; they are inherited verbatim,
    so this re-parenting is migration-neutral (``makemigrations --check`` reports
    no change against the live ``accounts_user`` table). ``CONTACT_CHOICES`` is
    inherited as a class attribute, so ``User.CONTACT_CHOICES`` still resolves for
    the templatetags and forms that reference it.

    The three email-confirmation fields are this site's own (issue #931). Since
    password reset (#929) the address on an account is a recovery credential,
    so it has to be proved before the reset form will mail it, and a new one
    waits in ``pending_email`` until the link sent to it is opened
    (``accounts.email_change``). They live here, on the concrete model, rather
    than in ``origami-auth``: this site owns ``accounts.User`` and its
    migrations, and the library's profile endpoint is already wrapped here.

    Attributes:
        email_verified: True once the holder of ``email`` has opened a
            confirmation link sent to it. Password reset mails only a verified
            address. False by default, so an address set by any path that does
            not prove it (registration, the shell, Django's admin) is not a
            recovery route until it is proved.
        pending_email: An address asked for but not yet confirmed; empty when
            no change is waiting.
        pending_email_sent_at: When the confirmation link for
            ``pending_email`` was sent. Part of the link's signature, so a
            newer request makes every older link useless.
    """

    # db_default as well as default: during a blue/green deploy the colour
    # still serving the previous release inserts users without these columns
    # (issue #575).
    email_verified = models.BooleanField(default=False, db_default=False)
    # The address it is waiting to become, so the email column's own length
    # (issue #943), not a second copy of it.
    pending_email = models.EmailField(
        max_length=column_length(AbstractOrigamiUser, "email"),
        blank=True,
        default="",
        db_default="",
    )
    pending_email_sent_at = models.DateTimeField(null=True, blank=True)

    # Plain Meta (not inheriting AbstractOrigamiUser.Meta) to match this app's
    # existing recorded migration state exactly — inheriting would add explicit
    # verbose_name options and produce a spurious AlterModelOptions migration.
    class Meta:
        db_table = "accounts_user"
