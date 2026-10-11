"""Spell every stored classic team's side with the ``team:`` prefix (#944).

#930 made a teamed classic figure cross with side ``"team:<name>"``
(``battle.classic_adaptation.classic_side``), so no team name can equal a
teamless figure's side, its uid. Battles saved before then still hold a
team's bare name, and an old save whose team is named like a row id keeps
the collision #930 removed: a member of team ``"7"`` and the teamless figure
in row 7 both carry side ``"7"`` and fight as allies. This rewrites
``figures[].side`` in each classic battle's ``state_json`` and
``initial_state_json`` (a restart replays from the second, so both move
together), which lets ``classic_team`` drop its bare-name fallback.

The team is read from the figure's own ``BattleCombatant`` row (a classic
figure's uid is its row pk, ``battle.profiles.CLASSIC_UID_IS_ROW_PK``), not
from the stored side, because the stored side is the ambiguous value. A
teamed figure gets the prefixed form; a teamless one keeps its uid. A side
that already equals the row's prefixed form is left alone, so running this
twice changes nothing; the check is against the row, not the bare prefix,
because a pre-#930 team could itself be named ``"team:Red"`` and must become
``"team:team:Red"`` rather than be taken for team ``"Red"``.

When the row is gone, or the stored side is not the old spelling of the
row's team, rewriting would change who fights whom on a guess, so the side
is kept. Kept with a warning when ``classic_team`` can still read it (it is
the uid or already prefixed); otherwise the migration raises, and being
atomic rolls back whole, since a bare team name it left would be refused by
``classic_team`` at that battle's next page load.

For an affected save the ally rules change on the next turn: team ``"7"``
and the teamless row 7 stop being allies. Every other save plays exactly as
before.

Reversed, a prefixed side goes back to the team's bare name, which needs no
row. Rows touched: classic battles whose figures include a teamed one.
"""

import logging

from django.db import migrations

logger = logging.getLogger(__name__)

#: ``battle.classic_adaptation.TEAM_SIDE_PREFIX`` when this was written,
#: copied rather than imported so a later change cannot change what this did.
TEAM_SIDE_PREFIX = "team:"

#: ``battle.persistence.CLASSIC_PROFILE``, frozen here for the same reason.
CLASSIC_PROFILE = "classic-melee"

#: The two stored snapshots a classic battle carries figures in.
SNAPSHOT_FIELDS = ("state_json", "initial_state_json")

#: Battles read per database round trip, so a large table is never held in
#: memory at once.
BATCH_SIZE = 200


class UnreadableSideError(RuntimeError):
    """A side this migration cannot rewrite and ``classic_team`` cannot read."""


def readable(side: str, uid: str) -> bool:
    """Whether ``classic_team`` reads ``side`` without the bare-name fallback."""
    return side.startswith(TEAM_SIDE_PREFIX) or side == uid


def keep(side: str, uid: str, battle_id: int, reason: str) -> str:
    """Keep a side the rows cannot vouch for: warned if readable, else refused.

    Raises:
        UnreadableSideError: For a side that would 500 the battle's page.
    """
    message = (
        f"Battle {battle_id}: classic figure {uid!r} has side {side!r} and {reason}"
    )
    if not readable(side, uid):
        raise UnreadableSideError(
            f"{message}; it is a bare team name nothing can read after #944. "
            "Fix the row or the side by hand, then migrate again."
        )
    logger.warning("%s; its side is left as it is.", message)
    return side


def prefixed_side(figure: dict, teams_by_uid: dict[str, str], battle_id: int) -> str:
    """The side ``figure`` should carry under #930's spelling.

    Args:
        figure: One stored classic figure, with ``uid`` and ``side``.
        teams_by_uid: The battle's ``BattleCombatant`` teams, by row pk as a
            string (a classic figure's uid).
        battle_id: The battle's pk, named in a warning or an error.

    Returns:
        ``"team:<name>"`` for a teamed figure and the uid for a teamless one;
        the stored side unchanged when it is already that, or when the rows
        cannot vouch for a rewrite and the side is still readable.

    Raises:
        UnreadableSideError: When the rows cannot vouch for a rewrite and the
            side is a bare team name.
    """
    side = str(figure.get("side", ""))
    uid = str(figure.get("uid", ""))
    if uid not in teams_by_uid:
        return keep(side, uid, battle_id, "no BattleCombatant row")
    team = teams_by_uid[uid]
    target = f"{TEAM_SIDE_PREFIX}{team}" if team else uid
    if side == target:
        return side
    if side == (team or uid):
        return target
    return keep(side, uid, battle_id, f"its row's team is {team!r}")


def bare_side(figure: dict) -> str:
    """The side ``figure`` carried before #930: a team's bare name, or its uid."""
    side = str(figure.get("side", ""))
    if side.startswith(TEAM_SIDE_PREFIX):
        return side[len(TEAM_SIDE_PREFIX) :]
    return side


def rewrite_sides(apps, side_for, *, needs_rows: bool) -> None:
    """Rewrite each classic battle's stored figure sides with ``side_for``.

    Args:
        apps: The migration's app registry.
        side_for: Takes a figure dict, the battle's teams by uid and the
            battle's pk, and returns the side that figure should carry.
        needs_rows: Whether ``side_for`` reads the battle's combatant rows;
            the reverse does not, and skips the query.
    """
    battle_model = apps.get_model("battle", "Battle")
    combatant_model = apps.get_model("battle", "BattleCombatant")
    battles = (
        battle_model.objects.filter(rules_profile=CLASSIC_PROFILE)
        .only("pk", *SNAPSHOT_FIELDS)
        .order_by("pk")
    )
    for battle in battles.iterator(chunk_size=BATCH_SIZE):
        teams_by_uid: dict[str, str] = {}
        if needs_rows:
            teams_by_uid = {
                str(row_id): team
                for row_id, team in combatant_model.objects.filter(
                    battle_id=battle.pk
                ).values_list("pk", "team")
            }
        changed: list[str] = []
        for field in SNAPSHOT_FIELDS:
            snapshot = getattr(battle, field) or {}
            touched = False
            for figure in snapshot.get("figures", []) or []:
                if not isinstance(figure, dict):
                    continue
                side = side_for(figure, teams_by_uid, battle.pk)
                if side != figure.get("side"):
                    figure["side"] = side
                    touched = True
            if touched:
                setattr(battle, field, snapshot)
                changed.append(field)
        if changed:
            battle.save(update_fields=changed)


def forwards(apps, schema_editor) -> None:
    """Spell each teamed classic figure's side ``"team:<name>"``."""
    rewrite_sides(apps, prefixed_side, needs_rows=True)


def backwards(apps, schema_editor) -> None:
    """Spell each teamed classic figure's side as its team's bare name again."""
    rewrite_sides(
        apps, lambda figure, _teams, _battle_id: bare_side(figure), needs_rows=False
    )


class Migration(migrations.Migration):
    dependencies = [("battle", "0016_brawler_archetype_renamed")]

    operations = [migrations.RunPython(forwards, backwards)]
