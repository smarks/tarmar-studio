"""Drop CampaignState.supplies, the retired free-text column (issue #623).

The contract half of the expand/contract pair issue #575 set out. Supplies
became one row per thing carried in #579, and ``0018_supply_items`` kept the
column so the colour still serving during that deploy could still select it.
Both colours have run code that does not read it since: blue on fadcee9 and
green on 0244f8e, the release that carried 0018. So the column can go, and
``migration_baseline.json`` lists this migration as the deliberate contract
step it is.

Reversible: unapplying re-adds the column, empty, and unapplying 0018 after
that writes the rows back into it as it always did.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("gm", "0018_supply_items"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="campaignstate",
            name="supplies",
        ),
    ]
