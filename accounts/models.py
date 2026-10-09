from origami_auth.base import AbstractOrigamiUser


class User(AbstractOrigamiUser):
    """Tarmar-studio's user, re-parented onto the shared abstract base.

    Every field, validator, role, and the ``can_manage_users`` property now live
    on :class:`origami_auth.base.AbstractOrigamiUser`; they are inherited verbatim,
    so this re-parenting is migration-neutral (``makemigrations --check`` reports
    no change against the live ``accounts_user`` table). ``CONTACT_CHOICES`` is
    inherited as a class attribute, so ``User.CONTACT_CHOICES`` still resolves for
    the templatetags and forms that reference it.
    """

    # Plain Meta (not inheriting AbstractOrigamiUser.Meta) to match this app's
    # existing recorded migration state exactly — inheriting would add explicit
    # verbose_name options and produce a spurious AlterModelOptions migration.
    class Meta:
        db_table = "accounts_user"
