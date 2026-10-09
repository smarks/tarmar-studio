"""Name older Weapon skills the way the resolver records them (issue #753).

Before #753 the battle looked a weapon skill up under the weapon's own name,
and the battle's generated fighters stored one, in the catalog name's
lowercase ("broadsword", "great sword", "bastard sword (1 hand)"). The
resolver records the weapon in the skill's name, "weapon (broadsword)".
This renames every such skill to the recorded form, and a skill called
"sword", which names no catalog weapon, to the bare "weapon" so the sheet's
Taken for control can tie it (the #753 review). Points join an existing
skill of the new name.

The spellings are frozen here, not read from the live catalog, so a later
catalog change cannot change what this migration did.

Reversing maps "weapon (<id>)" back to the catalog id. It is not an exact
undo:

* it cannot restore "sword": a bare "weapon" that was "sword" looks like one
  prior experience granted, so those stay "weapon";
* it cannot split merged points: a character that held both "broadsword" and
  "weapon (broadsword)" keeps the sum under "broadsword";
* the ids it writes back are what the pre-0017 board read only where a
  weapon's id equals its lowercase display name. The old board looked a
  skill up under the display name ("bastard sword (1 hand)"), so a skill
  restored as "bastard_sword_1h", "great_sword" or "two_handed_sword" adds
  nothing there, as it did not before this migration for "weapon (...)".
"""

from django.db import migrations

#: Every spelling an older skill used for a catalog weapon, to its id.
WEAPON_SPELLINGS: dict[str, str] = {
    "bastard_sword_1h": "bastard_sword_1h",
    "bastard sword 1h": "bastard_sword_1h",
    "bastard sword (1 hand)": "bastard_sword_1h",
    "bastard sword hand": "bastard_sword_1h",
    "bastard_sword_2h": "bastard_sword_2h",
    "bastard sword 2h": "bastard_sword_2h",
    "bastard sword (2 hands)": "bastard_sword_2h",
    "bastard sword hands": "bastard_sword_2h",
    "battle_axe": "battle_axe",
    "battle axe": "battle_axe",
    "bola": "bola",
    "boomerang": "boomerang",
    "broadsword": "broadsword",
    "cavalry_lance": "cavalry_lance",
    "cavalry lance": "cavalry_lance",
    "cestus": "cestus",
    "club": "club",
    "dagger": "dagger",
    "great_hammer": "great_hammer",
    "great hammer": "great_hammer",
    "great_sword": "great_sword",
    "great sword": "great_sword",
    "halberd": "halberd",
    "hammer": "hammer",
    "hatchet": "hatchet",
    "heavy_crossbow": "heavy_crossbow",
    "heavy crossbow": "heavy_crossbow",
    "horse_bow": "horse_bow",
    "horse bow": "horse_bow",
    "javelin": "javelin",
    "lasso": "lasso",
    "light_crossbow": "light_crossbow",
    "light crossbow": "light_crossbow",
    "longbow": "longbow",
    "mace": "mace",
    "morningstar": "morningstar",
    "net": "net",
    "nunchaku": "nunchaku",
    "pike": "pike",
    "pike_axe": "pike_axe",
    "pike axe": "pike_axe",
    "quarterstaff": "quarterstaff",
    "rapier": "rapier",
    "saber": "saber",
    "shortsword": "shortsword",
    "sling": "sling",
    "small_ax": "small_ax",
    "small ax": "small_ax",
    "small_bow": "small_bow",
    "small bow": "small_bow",
    "spear_1h": "spear_1h",
    "spear 1h": "spear_1h",
    "spear": "spear_1h",
    "spear_2h": "spear_2h",
    "spear 2h": "spear_2h",
    "spear (2 hands)": "spear_2h",
    "spear hands": "spear_2h",
    "thrown_rock": "thrown_rock",
    "thrown rock": "thrown_rock",
    "torch_weapon": "torch_weapon",
    "torch weapon": "torch_weapon",
    "torch (weapon)": "torch_weapon",
    "trident": "trident",
    "two_handed_sword": "two_handed_sword",
    "two handed sword": "two_handed_sword",
    "2-handed sword": "two_handed_sword",
    "war_ax": "war_ax",
    "war ax": "war_ax",
    "whip": "whip",
}

#: Skills that meant a weapon but name no catalog weapon.
UNTIED_WEAPON_SPELLINGS: frozenset[str] = frozenset({"sword"})


def _recorded_name(item_id: str) -> str:
    # Mirrors characters.weapon_skill.weapon_skill_name, frozen here.
    return f"weapon ({item_id.replace('_', ' ')})"


def _rename_skills(apps, rename) -> None:
    character_model = apps.get_model("characters", "Character")
    for character in character_model.objects.iterator():
        skills = dict(character.skills or {})
        renamed: dict[str, int] = {}
        for name, points in skills.items():
            target = rename(name)
            renamed[target] = int(renamed.get(target, 0) or 0) + int(points or 0)
        if renamed != skills:
            character.skills = renamed
            character.save(update_fields=["skills"])


def name_weapon_skills(apps, schema_editor) -> None:
    def rename(name: str) -> str:
        if name in WEAPON_SPELLINGS:
            return _recorded_name(WEAPON_SPELLINGS[name])
        if name in UNTIED_WEAPON_SPELLINGS:
            return "weapon"
        return name

    _rename_skills(apps, rename)


def restore_weapon_names(apps, schema_editor) -> None:
    recorded = {
        _recorded_name(item_id): item_id for item_id in WEAPON_SPELLINGS.values()
    }

    def rename(name: str) -> str:
        return recorded.get(name, name)

    _rename_skills(apps, rename)


class Migration(migrations.Migration):
    dependencies = [
        ("characters", "0016_spells_and_purse"),
    ]

    operations = [
        migrations.RunPython(name_weapon_skills, restore_weapon_names),
    ]
