"""Which skill applies to which weapon, and at what bonus (issue #753).

The sheet's attack panel and the battle board used to answer this two ways:
the panel took whatever skill the player picked (Knowledge V added +5 to a
broadsword), and the board looked the skill up under the weapon's own name,
so the bare "weapon" skill prior experience grants counted for nothing there.
:func:`resolve_weapon_skill` is the one answer both now read.

A Weapon skill is recorded against the weapon it was taken for in the skill's
own name, ``"weapon (broadsword)"`` — the glossary's own notation, "Weapon
(Broadsword) at Level IV". The name is written by :func:`weapon_skill_name`
from the catalog id, so it survives the name normalisation every skill write
applies. Two older spellings are read as the same thing: a skill named after
the weapon itself (``"broadsword"``, which the battle's generated fighters
carry) and ``"weapon (<catalog name>)"``. A bare ``"weapon"`` has not been
tied to a weapon yet and adds nothing until it is.
"""

import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING

from reference.rules_file import derived_from_rules

from .combat import SKILL_BONUS_PER_LEVEL, weapon_skill_bonus
from .equipment import get_weapon_class, is_unusual_weapon
from .equipment_rules import equipment_catalog
from .skills import get_skill_level, normalize_skill_name

if TYPE_CHECKING:
    from .models import Character

#: The skill prior experience grants before it is tied to a weapon.
UNRECORDED_WEAPON_SKILL = "weapon"

#: The skill that is "in effect, the Weapon skill for shooting".
ARCHERY_SKILL = "archery"

#: The two weapon classes Archery covers ("the two Missile classes").
MISSILE_CLASSES: frozenset[str] = frozenset({"Missile — Bows", "Missile — Crossbows"})

_WEAPON_SKILL_PREFIX = "weapon ("


def weapon_skill_name(item_id: str) -> str:
    """The skill name that records a Weapon skill taken for ``item_id``.

    Built from the catalog id rather than the display name because several
    display names carry a parenthesis of their own ("Bastard Sword (1
    hand)") that the skill-name normaliser would mangle.

    Args:
        item_id: A catalog weapon id.
    """
    return f"{_WEAPON_SKILL_PREFIX}{item_id.replace('_', ' ')})"


@derived_from_rules
@functools.cache
def _weapon_by_spelling() -> dict[str, str]:
    """Every spelling a skill may use for a catalog weapon, to its id.

    Built from the loaded canon's catalog the first time a skill is read,
    never at import (issue #75), and held for the process.
    """
    lookup: dict[str, str] = {}
    for item_id, weapon in equipment_catalog().weapons.items():
        if not weapon.weapon_class:
            continue  # ammunition is not a weapon and has no class
        name = weapon.name
        for spelling in (
            item_id,
            item_id.replace("_", " "),
            name.casefold(),
            normalize_skill_name(name),
        ):
            lookup.setdefault(spelling, item_id)
    return lookup


def trained_weapon_id(skill_name: str) -> str | None:
    """The catalog weapon a skill records it was taken for, or None.

    None for every skill that is not a Weapon skill tied to a weapon —
    including a bare ``"weapon"``, "weapon parry" and "archery".

    Args:
        skill_name: A stored (normalised) skill name.
    """
    if skill_name.startswith(_WEAPON_SKILL_PREFIX) and skill_name.endswith(")"):
        inner = skill_name[len(_WEAPON_SKILL_PREFIX) : -1].strip()
        return _weapon_by_spelling().get(inner)
    return _weapon_by_spelling().get(skill_name)


def is_weapon_skill(skill_name: str) -> bool:
    """Whether a skill is the glossary's Weapon skill, tied or not yet tied.

    Untied covers the bare "weapon" and a "weapon (...)" naming nothing the
    catalog sells.
    """
    return (
        skill_name == UNRECORDED_WEAPON_SKILL
        or skill_name.startswith(_WEAPON_SKILL_PREFIX)
        or trained_weapon_id(skill_name) is not None
    )


def needs_weapon_tie(skill_name: str) -> bool:
    """A Weapon skill the resolver cannot read yet: the sheet offers Taken for."""
    return is_weapon_skill(skill_name) and trained_weapon_id(skill_name) is None


def adds_to_attacks(skill_name: str) -> bool:
    """Whether any attack, on the sheet or the board, reads this skill name.

    :func:`resolve_weapon_skill` reads the Weapon skills and Archery, and
    nothing else does: the rules have no bare-handed skill, so the board no
    longer reads the "brawling" its generated brawler used to train (#815,
    #867).
    """
    return skill_name == ARCHERY_SKILL or is_weapon_skill(skill_name)


@dataclass(frozen=True)
class WeaponSkill:
    """The skill that applies to one weapon in hand, and what it adds.

    Attributes:
        skill_name: The stored skill that applies, or None when untrained.
        level: That skill's own level.
        trained_weapon_id: The weapon the skill was taken for; None for
            Archery and for an untrained swing.
        bonus: The to-hit bonus with the weapon in hand.
        rule: Which part of the rule gave the bonus, for the breakdown.
    """

    skill_name: str | None
    level: int
    trained_weapon_id: str | None
    bonus: int
    rule: str

    @property
    def effective_level(self) -> int:
        """The level whose full bonus is :attr:`bonus`, for the battle engine.

        The engine adds ``SKILL_BONUS_PER_LEVEL`` per ``weapon_skill_level``
        and knows nothing of transfers, so a half transfer is seated as the
        halved level.
        """
        return self.bonus // SKILL_BONUS_PER_LEVEL


UNTRAINED = WeaponSkill(None, 0, None, 0, "untrained")


def resolve_weapon_skill(
    skills: dict[str, int] | None, attack_weapon_id: str
) -> WeaponSkill:
    """Decide which of a character's skills applies to a weapon, at what bonus.

    The rule, from
    ``reference/content/public-rules/characters/prior-experience/skills-glossary.md``:

        "Weapon ... Taken for one **specific weapon**. Adds your full skill
        level to attacks with that weapon, and **half (rounded down)** with
        any other weapon of the same class (Unusual weapons excepted)."

        "**Archery** is, in effect, the Weapon skill for shooting — use it for
        ranged attacks across both Missile classes."

    and ``reference/content/public-rules/combat/action-options/attack-rolls.md``:

        "**+1 per skill level** with the weapon the skill was taken for, and
        **half your level (rounded down)** with another weapon of the same
        class"

    No other skill adds to an attack. Every Weapon skill the character holds
    is priced against the weapon in hand (full, half or nothing, through
    :func:`characters.combat.weapon_skill_bonus`), Archery gives its full
    level with a weapon of either Missile class that is not Unusual (an
    Unusual weapon "requires its own Weapon skill"), and the best of them
    applies — a character does not add two skills to one swing.

    Args:
        skills: The character's stored ``{skill name: points}``.
        attack_weapon_id: The catalog id of the weapon in hand.

    Returns:
        The skill that applies, or :data:`UNTRAINED`.
    """
    best = UNTRAINED
    attack_class = get_weapon_class(attack_weapon_id)
    for skill_name, points in (skills or {}).items():
        level = get_skill_level(int(points or 0), skill_name)
        if level <= 0:
            continue
        candidate: WeaponSkill | None = None
        weapon_id = trained_weapon_id(skill_name)
        if weapon_id is not None:
            bonus = weapon_skill_bonus(
                level, skill_weapon_id=weapon_id, attack_weapon_id=attack_weapon_id
            )
            rule = (
                "full: the weapon it was taken for"
                if weapon_id == attack_weapon_id
                else "half: another weapon of the same class"
            )
            if bonus:
                candidate = WeaponSkill(skill_name, level, weapon_id, bonus, rule)
        elif (
            skill_name == ARCHERY_SKILL
            and attack_class in MISSILE_CLASSES
            and not is_unusual_weapon(attack_weapon_id)
        ):
            bonus = weapon_skill_bonus(level)
            candidate = WeaponSkill(
                skill_name, level, None, bonus, "full: Archery, a Missile weapon"
            )
        if candidate is not None and candidate.bonus > best.bonus:
            best = candidate
    return best


def weapon_choices() -> list[dict[str, str]]:
    """Every catalog weapon a Weapon skill can be taken for, by name."""
    return sorted(
        (
            {"item_id": item_id, "name": weapon.name}
            for item_id, weapon in equipment_catalog().weapons.items()
            if weapon.weapon_class
        ),
        key=lambda choice: choice["name"],
    )


def record_weapon_skill(
    character: "Character", skill_name: str, item_id: str, *, is_game_master: bool
) -> str:
    """Tie a Weapon skill to the weapon it was taken for.

    The rule, from the skills glossary: "When you gain the **Weapon** skill,
    tie it to one specific weapon." Prior experience grants a bare "weapon",
    so the choice is made once, on the sheet; after that the record says
    which weapon and the request never does. A player ties an untied skill;
    only a Game Master moves a tied one to another weapon. Points already
    held for that weapon are joined to it.

    Args:
        character: The character, changed in memory and not saved.
        skill_name: The stored name of the Weapon skill.
        item_id: The catalog weapon.
        is_game_master: Whether a Game Master asked.

    Returns:
        The history line.

    Raises:
        CharacterChangeError: Not a Weapon skill, not a weapon, or a player
            moving a tied skill.
    """
    from .services import CharacterChangeError

    skills = dict(character.skills or {})
    if skill_name not in skills or not is_weapon_skill(skill_name):
        raise CharacterChangeError(f"{skill_name} is not a Weapon skill on this sheet")
    if not equipment_catalog().weapon_class(item_id):
        raise CharacterChangeError(f"Unknown weapon: {item_id}")
    if not needs_weapon_tie(skill_name) and not is_game_master:
        raise CharacterChangeError(
            "This Weapon skill is already tied to its weapon; only a Game Master "
            "can change which"
        )
    target = weapon_skill_name(item_id)
    if target == skill_name:
        raise CharacterChangeError(f"{skill_name} is already tied to that weapon")
    points = int(skills.pop(skill_name) or 0)
    skills[target] = int(skills.get(target, 0) or 0) + points
    character.skills = skills
    weapon_name = equipment_catalog().name(item_id)
    return f"{skill_name} tied to {weapon_name}: now {target}"


def weapon_skill_for_granted(skills: dict[str, int], granted: str) -> str:
    """Where a prior-experience grant of a skill lands.

    The tracks grant a bare "Weapon". "Roll a skill you already have and the
    point goes toward its next level" (skills glossary): a character with a
    Weapon skill tied to its weapon already has it, so the point goes there.
    With several tied, it goes to the highest (coordinator's ruling on the
    #753 review; the first by name among equals). With none tied it lands on
    the bare "weapon" for the player to tie.

    Args:
        skills: The character's stored skills.
        granted: The normalised name the track rolled.
    """
    if granted != UNRECORDED_WEAPON_SKILL:
        return granted
    tied = sorted(name for name in skills if trained_weapon_id(name) is not None)
    if not tied:
        return granted
    return max(tied, key=lambda name: int(skills.get(name, 0) or 0))
