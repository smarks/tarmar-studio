"""Armour marked both worn and packed comes out of the pack (issue #869).

Until #869 the sheet let a player tick both "equipped" and "in pack" on one
armour row, and every reader of worn armour read ``equipped`` alone, so such
armour kept its stops, DEX penalty, movement restriction and battle tier
even in a dropped pack. The readers now count armour only when it is
equipped and not in the pack (``characters.equipment.is_worn_armour``).

This migration runs before those readers serve a request and settles every
such entry as worn: ``in_pack`` is cleared, ``equipped`` kept. So no
character in play loses the stops, DEX penalty or movement restriction it
had the moment before; the one thing that can move is the weight of a
dropped pack, which no longer holds the armour the character is wearing.

Rows it touches: characters whose ``equipment`` holds an armour entry (a
catalog armour id below, or a custom item whose own category is ``armor``)
with both ``equipped`` and ``in_pack`` true. Measured on a copy of the main
checkout's development database on 2026-09-30: 0 of 208 characters. The
reverse is a no-op: which entries were both is not kept, and putting armour
back into the pack while worn would restore the bug.
"""

from django.db import migrations

#: The catalog's armour ids (``characters/fixtures/equipment.json``, group
#: ``armor``, as of #869).
CATALOG_ARMOUR_IDS: frozenset[str] = frozenset(
    {
        "cloth_armor",
        "leather_armor",
        "chainmail",
        "half_plate",
        "plate_armor",
        "fine_plate",
    }
)


def _is_armour(item: dict) -> bool:
    """A catalog armour id, or an entry whose own category is armour.

    The runtime reads a catalog item's category from the catalog; the only
    entry this reads differently is a catalog non-armour item that stores a
    category of its own, which no writer produces.
    """
    return item.get("item_id") in CATALOG_ARMOUR_IDS or item.get("category") == "armor"


def take_worn_armour_out_of_the_pack(apps, schema_editor) -> None:
    """Clear ``in_pack`` on every armour entry that is also ``equipped``."""
    character_model = apps.get_model("characters", "Character")
    for character in character_model.objects.only("pk", "equipment").iterator():
        equipment = character.equipment
        if not isinstance(equipment, list):
            continue
        changed = False
        for item in equipment:
            if (
                isinstance(item, dict)
                and _is_armour(item)
                and item.get("equipped")
                and item.get("in_pack")
            ):
                item["in_pack"] = False
                changed = True
        if changed:
            character_model.objects.filter(pk=character.pk).update(equipment=equipment)


class Migration(migrations.Migration):
    dependencies = [
        ("characters", "0022_origin_ruling_people_dragonborn_and_entry_into_play"),
    ]

    operations = [
        migrations.RunPython(
            take_worn_armour_out_of_the_pack, migrations.RunPython.noop
        ),
    ]
