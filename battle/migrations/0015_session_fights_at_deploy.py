"""Bring the session fights already on the board across the deploy (#750, #762).

Two things changed underneath fights that exist when this deploys:

* A Games Master session's fight made by a player now gives its foes to the
  Game Master (``Battle.foes_belong_to_game_master``). Without this a fight
  under way at deploy would keep handing the player the foes.
* Wounds and deaths are written by the board as each turn commits, for
  fights that write to records (``Battle.writes_to_records``), and the old
  write -- deaths only, made when the next chat message filed the battle's
  summary -- is gone. A fight still under way, or one that finished with no
  summary filed yet, would otherwise never have what it did written at all.

So for every battle a session points at: the foes flag follows the owner's
account, as ``gm.fight.start_fight`` now sets it; and a fight whose summary
is not filed yet is marked as writing to records, and what it has done so
far -- each saved character's losses since the start, and a death -- is
written now, so later turns and a Game Master's restart (which hands the
fight's losses back) work from a record that matches the board. A fight
whose summary was filed had its deaths written by the old path and is left
as it was: its wounds were never on a record, and writing them now would
impose old wounds the story has moved past.
"""

import json

from django.db import migrations
from django.utils import timezone

#: The battle states ``gm.battle_report`` treated as over.
TERMINAL_STATUSES = ("finished", "stalemate")

#: What the history names as the author of a change no user made.
UNATTRIBUTED = "system"

#: The history cap, as ``characters.models.MAX_HISTORY_ENTRIES`` (frozen here,
#: as a migration must be).
MAX_HISTORY_ENTRIES = 50

#: The most damage a pool column holds.
MAX_POOL_DAMAGE = 32767


def summary_filed(history_json: str, battle_id: int) -> bool:
    """Whether a session's history (stored as JSON text) holds the summary."""
    history = json.loads(history_json or "[]")
    return any(
        isinstance(entry, dict)
        and entry.get("kind") == "battle"
        and entry.get("battle_id") == battle_id
        and entry.get("battle_ended")
        for entry in history
    )


def figures_by_id(snapshot: dict) -> dict:
    """A Tarmar snapshot's combatants, by combatant id."""
    return {
        entry["combatant_id"]: entry
        for entry in (snapshot or {}).get("combatants", [])
        if isinstance(entry, dict) and "combatant_id" in entry
    }


def note(character, field: str, old: str, new: str, by: str) -> None:
    """Append one history line, as ``Character.record_field_change`` does."""
    entries = list(character.history or [])
    entries.append(
        {
            "ts": timezone.now().isoformat(),
            "field": field,
            "old": old,
            "new": new,
            "by": by,
        }
    )
    character.history = entries[-MAX_HISTORY_ENTRIES:]


def pools_text(figure: dict) -> str:
    """``Fatigue 27/44, Body 30/30`` (and ``; Mana 3/5`` for a caster).

    The words ``characters.services.record_fight`` writes for a fight's one
    history line, frozen here as a migration must be.
    """
    text = (
        f"Fatigue {figure.get('fatigue')}/{figure.get('max_fatigue')}, "
        f"Body {figure.get('body')}/{figure.get('max_body')}"
    )
    if figure.get("max_mana"):
        text += f"; Mana {figure.get('mana')}/{figure.get('max_mana')}"
    return text


def lost(start: dict, end: dict, key: str) -> int:
    """How much one pool fell between the two snapshots."""
    return int(start.get(key, 0) or 0) - int(end.get(key, 0) or 0)


def add(damage: int, amount: int) -> int:
    """Damage plus an amount, floored at unhurt and capped at the column."""
    return min(MAX_POOL_DAMAGE, max(0, damage + amount))


def write_progress(apps, battle) -> None:
    """Write what an unfiled session fight has done to its saved characters.

    As the board would have: each pool's loss (Mana spent included) goes on
    the record, and the fight's one history line carries the pools the fight
    found the character at as its old value, naming the Games Master for a
    fight whose foes are theirs and the maker for a Game Master's own.
    """
    BattleCombatant = apps.get_model("battle", "BattleCombatant")
    Character = apps.get_model("characters", "Character")
    starting = figures_by_id(battle.initial_state_json)
    reached = figures_by_id(battle.state_json)
    # The Game Master's side dealt it, not the player who started the fight
    # (as characters.services.GAMES_MASTER_AUTHOR, frozen here).
    maker = (
        "Games Master"
        if battle.foes_belong_to_game_master or not battle.created_by_id
        else battle.created_by.username
    )
    rows = BattleCombatant.objects.filter(
        battle=battle, is_saved_character=True, character__isnull=False
    )
    for row in rows:
        start, end = starting.get(row.pk), reached.get(row.pk)
        if start is None or end is None:
            continue
        character = Character.objects.select_for_update().get(pk=row.character_id)
        if character.is_deleted or not character.is_alive:
            continue
        fatigue_lost = lost(start, end, "fatigue")
        body_lost = lost(start, end, "body")
        mana_spent = lost(start, end, "mana")
        died = not end.get("alive", True)
        if not (fatigue_lost or body_lost or mana_spent or died):
            continue
        character.fatigue_damage = add(character.fatigue_damage, fatigue_lost)
        character.body_damage = add(character.body_damage, body_lost)
        character.mana_spent = add(character.mana_spent, mana_spent)
        where = (
            f"killed in {battle.name}, turn {battle.turn_number}"
            if died
            else f"in {battle.name} at turn {battle.turn_number}, when the board "
            "began recording fights"
        )
        note(
            character,
            "current pools",
            pools_text(start),
            f"{pools_text(end)}, {where}",
            maker,
        )
        if died:
            character.is_alive = False
            character.death_cause = f"Killed in battle — {battle.name}"[:200]
            note(character, "is_alive", "True", "False", maker)
        character.save()


def forwards(apps, schema_editor) -> None:
    GMSession = apps.get_model("gm", "GMSession")
    sessions = GMSession.objects.exclude(battle=None).select_related(
        "user", "battle", "battle__created_by"
    )
    for session in sessions:
        battle = session.battle
        owner = session.user
        battle.foes_belong_to_game_master = not (owner.is_dm or owner.is_role_admin)
        unfiled = not summary_filed(session.history_json, battle.pk)
        if unfiled and battle.rules_profile == "tarmar":
            battle.writes_to_records = True
            write_progress(apps, battle)
        battle.save(update_fields=["foes_belong_to_game_master", "writes_to_records"])


class Migration(migrations.Migration):
    dependencies = [
        ("battle", "0014_battle_game_master_fights"),
        ("characters", "0020_character_current_pools"),
        ("gm", "0019_drop_retired_supplies_column"),
        # forwards reads the session owner's role flags (issue #974: named
        # here rather than left to arrive through gm 0007).
        ("accounts", "0002_user_is_dm_user_is_player_user_is_role_admin"),
    ]

    operations = [
        # Nothing to undo: the two columns are dropped by 0014's reverse, and
        # a death or a wound the board dealt stays on the record either way.
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
