"""Add the turn-0 snapshot Restart rewinds to (issue #188).

Existing battles still sitting at turn 0 have never advanced, so their
``state_json`` *is* the starting state — those backfill exactly. Battles
already part-way through cannot have their start recovered; they keep an
empty snapshot and the UI disables Restart for them.
"""

from django.db import migrations, models

import battle.models


def backfill_untouched_battles(apps, schema_editor):
    Battle = apps.get_model("battle", "Battle")
    for battle_row in Battle.objects.filter(turn_number=0).exclude(state_json={}):
        battle_row.initial_state_json = battle_row.state_json
        battle_row.save(update_fields=["initial_state_json"])


class Migration(migrations.Migration):
    dependencies = [
        ("battle", "0002_remove_battlecombatant_beast_slug_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="battle",
            name="initial_state_json",
            field=models.JSONField(
                blank=True, default=dict, validators=[battle.models.validate_state_json]
            ),
        ),
        migrations.RunPython(backfill_untouched_battles, migrations.RunPython.noop),
    ]
