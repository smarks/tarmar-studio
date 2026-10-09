"""Rename the "unarmed brawler" archetype to "brawler" in unstarted drafts (#867).

The generated brawler now carries a dagger, so "unarmed" no longer describes
it and the archetype is named "brawler". A draft's muster stores archetype
names (``BattleDraft.roster_json``), and starting a draft looks each one up in
``battle.generation``'s tables, so a draft saved under the old name would
fail to start. Only drafts are touched: a battle already built keeps the
label its figures were made under, which is what they were (bare-handed).

Rows touched: every ``BattleDraft`` whose muster has a fighter entry named
"unarmed brawler"; 0 in the local database when this was written. Reversed,
"brawler" goes back to "unarmed brawler".
"""

from django.db import migrations

OLD_NAME = "unarmed brawler"
NEW_NAME = "brawler"


def rename(apps, from_name: str, to_name: str) -> None:
    """Rewrite fighter entries named ``from_name`` in every draft's muster."""
    battle_draft = apps.get_model("battle", "BattleDraft")
    for draft in battle_draft.objects.all():
        roster = draft.roster_json or []
        changed = False
        for entry in roster:
            if (
                isinstance(entry, dict)
                and entry.get("kind") == "fighter"
                and entry.get("archetype") == from_name
            ):
                entry["archetype"] = to_name
                changed = True
        if changed:
            draft.roster_json = roster
            draft.save(update_fields=["roster_json"])


def forwards(apps, schema_editor) -> None:
    rename(apps, OLD_NAME, NEW_NAME)


def backwards(apps, schema_editor) -> None:
    rename(apps, NEW_NAME, OLD_NAME)


class Migration(migrations.Migration):
    dependencies = [("battle", "0015_session_fights_at_deploy")]

    operations = [migrations.RunPython(forwards, backwards)]
