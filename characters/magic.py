"""The spells a character knows and the Mana Pool they cast from (#774, #755).

A saved character used to reach the battle with ``spells=()``: the record held
a Mana Pool (``free_spell_points``) and no spells, so a player's own caster
could never cast. The record now keeps ``Character.spells`` — ``{spell key:
skill points}`` against the combat spell catalog the engine casts from — and
:func:`battle_loadout` is what every seat reads, so the engine's existing
casting works for a saved character exactly as for a generated one.

The rules, from ``reference/content``:

* ``public-rules/magic/spell-mastery.md``: "Multiple skill points in a spell
  grant additional abilities", level 1 being "Can cast the spell". A spell is
  learned with skill points, on the ladder every skill climbs.
* ``public-rules/magic.md`` (as amended for #774): the Mana Pool is built
  from the Magic track's Spell Points, and skill points do not convert into
  it; a Game Master may grant mana.
* ``public-rules/magic/casting-spells.md``: "Spells cost mana equal to the
  spell's level" — the engine's own rule, unchanged here.
"""

from dataclasses import dataclass

from tarmar_engine.spells import SPELLS, Spell

from .models import CHARACTER_INT_FIELD_MAX, Character
from .services import CharacterChangeError
from .skills import MAX_SKILL_POINTS, format_skill_level, get_skill_level

#: The largest mana a Game Master grants in one step.
MAX_MANA_GRANT = 100

#: The largest Mana Pool the record holds: its column's, the bound the import
#: holds a file to as well (issue #940).
MAX_MANA_POOL = CHARACTER_INT_FIELD_MAX["free_spell_points"]


@dataclass(frozen=True)
class KnownSpell:
    """One spell on the sheet.

    Attributes:
        key: The catalog key.
        name: The spell's name.
        school: Its school of magic.
        spell_level: The spell's own level, which is also its mana cost.
        attribute: The attribute its casting roll is made against.
        points: Skill points invested in it.
        mastery: The mastery level those points buy.
        mastery_display: The mastery level as the sheet prints it.
    """

    key: str
    name: str
    school: str
    spell_level: int
    attribute: str
    points: int
    mastery: int
    mastery_display: str


@dataclass(frozen=True)
class SpellLoadout:
    """What a seat carries into the battle: known spells and the Pool's maximum."""

    spells: tuple[str, ...]
    mana: int


def learnable_spells() -> list[Spell]:
    """Every spell the battle can cast, by school and level."""
    return sorted(SPELLS.values(), key=lambda spell: (spell.school, spell.level))


def known_spells(character: Character) -> list[KnownSpell]:
    """The character's spells with their mastery, in catalog order."""
    rows = []
    for spell in learnable_spells():
        points = int((character.spells or {}).get(spell.key, 0) or 0)
        if points <= 0:
            continue
        rows.append(
            KnownSpell(
                key=spell.key,
                name=spell.name,
                school=spell.school,
                spell_level=spell.level,
                attribute=spell.attribute,
                points=points,
                mastery=get_skill_level(points),
                mastery_display=format_skill_level(points),
            )
        )
    return rows


def current_mana(character: Character) -> int:
    """The mana the character has now: the Pool less what fights spent.

    The pools batch (#750/#751) keeps ``Character.mana_spent`` beside the
    Fatigue and Body damage: no page gives mana a recovery rule, so spent
    mana stays spent until a Game Master sets the pools (coordinator's ruling
    on the #774 review). The seam battle seating calls; the arithmetic is
    ``Character.current_mana``'s, which the sheet's templates read, so the
    two cannot disagree.
    """
    return character.current_mana


def battle_loadout(character: Character) -> SpellLoadout:
    """The spells and Mana Pool a saved character is seated with (issue #755).

    Only spells at mastery 1 or above ("Can cast the spell") are carried.
    ``mana`` is the Pool's maximum, ``free_spell_points``: the seat's
    ``snapshot_character`` takes it as ``max_mana`` and, with the pools
    batch, seats ``max_mana - mana_spent`` as the mana in hand, so passing
    the current figure here would take the spent mana off twice.
    """
    return SpellLoadout(
        spells=tuple(spell.key for spell in known_spells(character) if spell.mastery),
        mana=character.free_spell_points,
    )


def spell_mastery(character: Character, spells: tuple[str, ...]) -> dict[str, int]:
    """The Spell Mastery level of each seated spell the record holds points in.

    ``public-rules/magic/spell-mastery.md``: level 2 casts with no gestures,
    level 3 with no incantation either, which is what a grappled caster needs
    to renew (2+) or cast (3+) a spell (tarmar-engine 0.9.5, #821). A seated
    spell the record holds no points in (a generated guest's, never saved) is
    left out, and the engine reads it at level 1, "Can cast the spell".

    Args:
        character: The character being seated.
        spells: The spell keys the seat carries.

    Returns:
        ``{spell key: mastery level}`` for the seated spells on the record.
    """
    held = character.spells or {}
    mastery: dict[str, int] = {}
    for key in spells:
        points = int(held.get(key, 0) or 0)
        if points > 0:
            mastery[key] = get_skill_level(points)
    return mastery


def learn_spell(
    character: Character, spell_key: str, points: int = 1, *, is_game_master: bool
) -> str:
    """Put skill points into a spell, learning it at mastery 1.

    The rule, ``public-rules/magic/spell-mastery.md``: "Multiple skill points
    in a spell grant additional abilities ... 1: Can cast the spell". The
    points come from the character's free skill points, the way a skill's
    do. A Game Master teaching a spell spends none of them (coordinator's
    ruling on the #774 review, marked for Spencer).

    Args:
        character: The character, changed in memory and not saved.
        spell_key: A key of the combat spell catalog.
        points: How many skill points to invest.
        is_game_master: Whether a Game Master asked.

    Returns:
        The history line.

    Raises:
        CharacterChangeError: An unknown spell, a bad amount, or a player
            without the free points.
    """
    spell = SPELLS.get(spell_key)
    if spell is None:
        raise CharacterChangeError(f"Unknown spell: {spell_key}")
    if points < 1:
        raise CharacterChangeError("Invest at least one skill point")
    spells = dict(character.spells or {})
    held = int(spells.get(spell_key, 0) or 0)
    if held + points > MAX_SKILL_POINTS:
        raise CharacterChangeError(f"A spell holds at most {MAX_SKILL_POINTS} points")
    free = character.free_skill_points
    if not is_game_master:
        if points > free:
            raise CharacterChangeError(
                f"Not enough free skill points: {spell.name} needs {points}, "
                f"{free} unallocated"
            )
        character.free_skill_points = free - points
    spells[spell_key] = held + points
    character.spells = spells
    return (
        f"{spell.name}: {held} → {held + points} points, mastery "
        f"{format_skill_level(held + points)}"
    )


def grant_mana(character: Character, amount: int, *, is_game_master: bool) -> str:
    """A Game Master raises (or lowers) the Mana Pool's maximum.

    ``free_spell_points`` only; what fights spent is the pools batch's
    ``mana_spent``, which its Game-Master-only pool setting restores.

    The rule, ``public-rules/magic.md`` as amended for #774: the Pool "grows
    only with the Spell Points the Magic track grants ... A Game Master may
    grant mana." A player is refused; skill points never convert to mana.

    The Pool is never lowered below the mana already spent from it (issue
    #940): the record would hold Mana below 0, which the pools setting
    (``services.apply_current_pools``) refuses. A Game Master restores the
    spent mana first.

    Raises:
        CharacterChangeError: A player asked, the amount is out of range, or
            it would lower the Pool below the mana spent.
    """
    if not is_game_master:
        raise CharacterChangeError("Only a Game Master can grant mana")
    if amount == 0 or abs(amount) > MAX_MANA_GRANT:
        raise CharacterChangeError(
            f"Grant between -{MAX_MANA_GRANT} and {MAX_MANA_GRANT} mana, not 0"
        )
    before = character.free_spell_points
    after = before + amount
    if not 0 <= after <= MAX_MANA_POOL:
        raise CharacterChangeError(
            f"The Mana Pool holds {before}; it cannot go to {after}"
        )
    if amount < 0 and after < character.mana_spent:
        raise CharacterChangeError(
            f"The Mana Pool holds {before} with {character.mana_spent} spent; it "
            f"cannot go to {after}, below the mana spent"
        )
    character.free_spell_points = after
    return f"Mana Pool {before} → {after} (Game Master grant)"
