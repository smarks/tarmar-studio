"""Record which rules profile a battle is played under (issue #251).

Every battle stored before this migration was played under the six-phase
Tarmar profile -- it was the only one the engine had a state type for -- so
the field default backfills them correctly and no data migration is needed.
The column matters because ``state_json``'s shape follows the profile: see
``battle.persistence``.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("battle", "0004_battle_auto_start_battle_pending_input_json_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="battle",
            name="rules_profile",
            field=models.CharField(
                choices=[("tarmar", "Tarmar"), ("classic-melee", "Classic Melee")],
                default="tarmar",
                max_length=20,
            ),
        ),
    ]
