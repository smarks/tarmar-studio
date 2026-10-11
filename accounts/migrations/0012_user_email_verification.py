"""Email confirmation fields (issue #931), with today's addresses kept usable.

Every address on file before this migration was accepted without proof, and
password reset (#929) has mailed all of them since it shipped. Marking them
unverified would silently take the reset away from every existing account, so
each non-empty address is marked verified as it stands. An address the
operator doubts is marked unverified with ``manage.py set_email_verified
<username> false`` (DEPLOY.md, Mail).
"""

from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def mark_existing_addresses_verified(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Treat each address already on file as proved.

    Args:
        apps: The historical app registry.
        schema_editor: Unused; part of RunPython's signature.
    """
    user_model = apps.get_model("accounts", "User")
    user_model.objects.exclude(email="").update(email_verified=True)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0011_add_phone_discord_validators"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="email_verified",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AddField(
            model_name="user",
            name="pending_email",
            field=models.EmailField(
                blank=True, db_default="", default="", max_length=254
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="pending_email_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(
            mark_existing_addresses_verified, migrations.RunPython.noop
        ),
    ]
