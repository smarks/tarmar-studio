"""Give every GM session a mode, and leave existing ones behaving as they did.

Issue #372. The column's default is ``play``, which is right for a session a
player opens and wrong for every session a DM or admin already has: those have
been answered by the aide since #353, and this field is not an occasion to
change what a running conversation does. So the schema step is followed by a
backfill that puts every existing session belonging to an account with
``can_manage_users`` into ``aide``.

The backfill reads ``is_role_admin`` and ``is_dm`` off the historical user
model rather than calling ``battle.access.is_dm``: a migration must keep
working when the property it would have called has moved on.
"""

from django.db import migrations, models

from gm.engine import MODE_AIDE, MODE_PLAY


def open_existing_dm_sessions_in_aide(apps, schema_editor):
    """Put every session an admin or DM already owns into aide mode."""
    GMSession = apps.get_model("gm", "GMSession")
    GMSession.objects.filter(user__is_role_admin=True).update(mode=MODE_AIDE)
    GMSession.objects.filter(user__is_dm=True).update(mode=MODE_AIDE)


def open_every_session_in_play(apps, schema_editor):
    """Reverse: put the column back to the default it is dropped with."""
    GMSession = apps.get_model("gm", "GMSession")
    GMSession.objects.update(mode=MODE_PLAY)


class Migration(migrations.Migration):
    dependencies = [
        ("gm", "0006_gmsession_character_draft"),
        # The backfill filters on the user model's role flags, which
        # accounts 0002 adds (issue #974: depending on 0001 alone let a
        # targeted migrate run this before the flags existed).
        ("accounts", "0002_user_is_dm_user_is_player_user_is_role_admin"),
    ]

    operations = [
        migrations.AddField(
            model_name="gmsession",
            name="mode",
            field=models.CharField(
                choices=[("play", "Game Master"), ("aide", "GM aide")],
                default="play",
                max_length=8,
            ),
        ),
        migrations.RunPython(
            open_existing_dm_sessions_in_aide,
            open_every_session_in_play,
        ),
    ]
