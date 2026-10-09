"""Provenance and wealth become rolled-once values (issue #749).

Adds ``provenance_recorded`` and ``wealth_recorded`` and lets
``starting_coin`` be null, which is what a Rich character's "Consult GM" coin
now is.

The data step decides, for each existing character, whether its provenance
and wealth are on the record. A character made on the creation page rolled
both, and its dice log says so ("Provenance: d100=..." and "Wealth:
d100=..."). One started in a Games Master session was saved with the model's
defaults that nobody rolled; unless a value has since been changed from the
default (by the chat's ``set_background`` or on the sheet), and its history
shows no change to it, it is marked not recorded, so its player gets the roll the rules give them. Rich characters
holding the old 0 coin get the "Consult GM" null instead.

A limit on the history check: a character keeps only its last
``MAX_HISTORY_ENTRIES`` (50) changes, so a background change older than that is
gone, and a character whose values are back at the defaults then reads as
never set and is handed its roll. The dice log, which is never trimmed, still
catches every background rolled at creation.
"""

from django.db import migrations, models

#: The model defaults a character started in a Games Master session carries.
DEFAULT_PROVENANCE = ("commoner", "Laborer")
DEFAULT_WEALTH = "Moderate"
DEFAULT_COIN = 100


def mark_unrolled_backgrounds(apps, schema_editor) -> None:
    """Mark provenance and wealth unrecorded where nothing rolled or set them."""
    character_model = apps.get_model("characters", "Character")
    for character in character_model.objects.all().only(
        "dice_log",
        "history",
        "provenance_class",
        "provenance_detail",
        "wealth_level",
        "starting_coin",
    ):
        dice_log = character.dice_log or ""
        # A change on record (the chat's set_background, a Game Master on the
        # sheet, or a player's choice while choosing was allowed) is a
        # recorded value too, even when it landed back on the default.
        changed_fields = {
            entry.get("field")
            for entry in character.history or []
            if isinstance(entry, dict)
        }
        provenance_default = (
            character.provenance_class,
            character.provenance_detail,
        ) == DEFAULT_PROVENANCE
        wealth_default = (
            character.wealth_level == DEFAULT_WEALTH
            and character.starting_coin == DEFAULT_COIN
        )
        changed: list[str] = []
        if (
            "Provenance: d100=" not in dice_log
            and provenance_default
            and not changed_fields & {"provenance_class", "provenance_detail"}
        ):
            character.provenance_recorded = False
            changed.append("provenance_recorded")
        if (
            "Wealth: d100=" not in dice_log
            and wealth_default
            and not changed_fields & {"wealth_level", "starting_coin"}
        ):
            character.wealth_recorded = False
            changed.append("wealth_recorded")
        if changed:
            character.save(update_fields=changed)
    character_model.objects.filter(wealth_level="Rich", starting_coin=0).update(
        starting_coin=None
    )


def restore_rich_coin(apps, schema_editor) -> None:
    """Put back the 0 the old code stored for an unset Rich coin."""
    character_model = apps.get_model("characters", "Character")
    character_model.objects.filter(starting_coin__isnull=True).update(starting_coin=0)


class Migration(migrations.Migration):
    dependencies = [
        ("characters", "0014_character_literacy_roll"),
    ]

    operations = [
        migrations.AddField(
            model_name="character",
            name="provenance_recorded",
            field=models.BooleanField(db_default=True, default=True),
        ),
        migrations.AddField(
            model_name="character",
            name="wealth_recorded",
            field=models.BooleanField(db_default=True, default=True),
        ),
        migrations.AlterField(
            model_name="character",
            name="starting_coin",
            field=models.PositiveIntegerField(blank=True, default=100, null=True),
        ),
        migrations.RunPython(mark_unrolled_backgrounds, restore_rich_coin),
    ]
