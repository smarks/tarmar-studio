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
* ``public-rules/magic/schools-of-magic.md`` (issue #18): a caster's school is
  rolled on the canon's ``schools`` table (``services.apply_school_roll``) and
  chosen from the row it lands on (:func:`choose_school`); a player learns a
  new spell only from that school (:func:`school_refusal`), and a Game Master
  may teach any.
"""

from dataclasses import dataclass

from tarmar_engine.spells import SPELLS, Spell

from battle.magic_rules import (
    SchoolRow,
    loaded_casting_success_ceiling,
    magic_rules,
    magic_schools,
)
from common.rolling import Roller

from .constants import CORE_ATTRIBUTE_FIELDS
from .dice import roll_dice_logged
from .models import CHARACTER_INT_FIELD_MAX, Character, magic_school_refusal
from .rules import DiceRollRules
from .services import (
    CAST_ON_THE_BOARD,
    CharacterChangeError,
    seated_in_a_live_fight,
)
from .skill_checks import (
    attribute_not_recorded,
    check_stated_total,
    effective_score,
    injury_penalty,
    unconscious_refusal,
)
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


def spell_points(character: Character, spell_key: str) -> int:
    """The skill points the record holds in one spell; 0 for none.

    The record's ``spells`` may be empty, or hold a spell at 0 (a seat test's
    record does), so every reader of one spell's points asks here.
    """
    return int((character.spells or {}).get(spell_key, 0) or 0)


def school_refusal(character: Character, spell: Spell) -> str | None:
    """Why a player may not put points into ``spell``, by school (issue #18).

    The canon's reading of schools-of-magic.md and spell-descriptions.md (its
    ``school_gates_a_new_spell`` ruling): a player learns a spell they do not
    yet know only from their own school, rolled and chosen first. A spell
    already on the record, which a Game Master taught or which was learned
    before schools were rolled, may be raised whatever its school. A canon
    with no school table gates nothing. A Game Master is never asked this.

    Args:
        character: The character.
        spell: A spell of the catalog.

    Returns:
        The refusal, or ``None`` when the school allows it.
    """
    if magic_schools() is None:
        return None
    if spell_points(character, spell.key) > 0:
        return None
    school = character.magic_school
    if not school:
        return (
            "Roll and choose a school of magic first; a player learns only the "
            "spells of their school"
        )
    if spell.school != school:
        return (
            f"{spell.name} is of the {spell.school} school and this character's is "
            f"{school}; only a Game Master can teach a spell of another school"
        )
    return None


def spells_open_to(character: Character, *, is_game_master: bool) -> list[Spell]:
    """The spells this asker may put points into, in catalog order (#18).

    The sheet's learn control lists these; :func:`learn_spell` refuses the
    rest with :func:`school_refusal`'s reason.
    """
    return [
        spell
        for spell in learnable_spells()
        if is_game_master or school_refusal(character, spell) is None
    ]


def rolled_school_row(character: Character) -> SchoolRow | None:
    """The row of the canon's school table the character's roll landed on.

    ``None`` with no roll, or under a canon with no school table (#18).
    """
    table = magic_schools()
    roll = character.magic_school_roll
    if table is None or roll is None:
        return None
    return table.row_for(roll)


def schools_to_choose(character: Character, *, is_game_master: bool) -> tuple[str, ...]:
    """The schools this asker may make the character's school now (#18).

    A Game Master: any the canon's table names but the one held. A player:
    the schools the rolled row names, until one is chosen; none before the
    roll, and none once chosen.
    """
    table = magic_schools()
    if table is None:
        return ()
    if is_game_master:
        return tuple(
            school for school in table.schools if school != character.magic_school
        )
    opened = rolled_school_row(character)
    if character.magic_school or opened is None:
        return ()
    return opened.schools


def choose_school(character: Character, school: str, *, is_game_master: bool) -> str:
    """Make ``school`` the character's school of magic (issue #18).

    The canon's ``school_roll_opens_a_list`` ruling: the school roll lands on
    a row of the canon's table, and the player chooses one of the schools it
    names, once. A Game Master may set any school the table names, rolled or
    not, which the history records as his choice where the roll did not open
    it.

    Args:
        character: The character, changed in memory and not saved.
        school: The school chosen.
        is_game_master: Whether a Game Master asked.

    Returns:
        The history line.

    Raises:
        CharacterChangeError: The canon has no school table, the school is not
            one it names or is already held; or a player before the roll,
            after choosing, or naming a school the roll did not open.
    """
    if refusal := magic_school_refusal(school):
        raise CharacterChangeError(refusal)
    held = character.magic_school
    if school == held:
        raise CharacterChangeError(f"The school of magic is already {school}")
    opened = rolled_school_row(character)
    if not is_game_master:
        if opened is None:
            raise CharacterChangeError(
                "Roll the school of magic first; the roll opens the list a "
                "school is chosen from"
            )
        if held:
            raise CharacterChangeError(
                f"The {held} school is chosen; only a Game Master can change it"
            )
        if school not in opened.schools:
            raise CharacterChangeError(
                f"The school roll ({character.magic_school_roll}, {opened.label}) opens "
                f"{', '.join(opened.schools)}; only a Game Master can set another "
                "school"
            )
    character.magic_school = school
    line = f"school of magic {held or 'unchosen'} → {school}"
    if opened is None and is_game_master:
        return f"{line} (Game Master's choice, with no school roll)"
    if opened is not None and school not in opened.schools:
        return (
            f"{line} (Game Master's choice, outside the {opened.label} schools the "
            "roll opened)"
        )
    return line


def known_spells(character: Character) -> list[KnownSpell]:
    """The character's spells with their mastery, in catalog order."""
    rows = []
    for spell in learnable_spells():
        points = spell_points(character, spell.key)
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
    to renew (2+) or cast (3+) a spell (tarmar-engine 0.9.5, #821). The seat
    also carries these levels as the spells' skill levels, which a canon's
    Channel may derive from (#960). A seated spell the record holds no points
    in is left out, and the engine reads the two maps differently there: its
    mastery at level 1, "Can cast the spell", and its skill level at 0, so a
    Channel derived from the spell's level allows it no mana.

    Args:
        character: The character being seated.
        spells: The spell keys the seat carries.

    Returns:
        ``{spell key: mastery level}`` for the seated spells on the record.
    """
    mastery: dict[str, int] = {}
    for key in spells:
        points = spell_points(character, key)
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
    ruling on the #774 review, marked for Spencer). A player learns a new
    spell only from their school of magic (:func:`school_refusal`, issue
    #18); a Game Master teaching one from another school is recorded as such.

    Args:
        character: The character, changed in memory and not saved.
        spell_key: A key of the combat spell catalog.
        points: How many skill points to invest.
        is_game_master: Whether a Game Master asked.

    Returns:
        The history line.

    Raises:
        CharacterChangeError: An unknown spell, a bad amount, or a player
            without the free points or learning outside their school.
    """
    spell = SPELLS.get(spell_key)
    if spell is None:
        raise CharacterChangeError(f"Unknown spell: {spell_key}")
    if points < 1:
        raise CharacterChangeError("Invest at least one skill point")
    spells = dict(character.spells or {})
    held = spell_points(character, spell_key)
    if held + points > MAX_SKILL_POINTS:
        raise CharacterChangeError(f"A spell holds at most {MAX_SKILL_POINTS} points")
    free = character.free_skill_points
    outside_school = ""
    if is_game_master:
        if held == 0 and school_refusal(character, spell) is not None:
            school = character.magic_school or "unchosen"
            outside_school = f" (Game Master, outside the caster's school: {school})"
    else:
        if refusal := school_refusal(character, spell):
            raise CharacterChangeError(refusal)
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
        f"{format_skill_level(held + points)}{outside_school}"
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


#: The casting roll's dice. ``public-rules/magic/casting-spells.md``: "Roll:
#: 3d6 ≤ INT or WIS (depending on the spell)". The drift test holds this
#: constant to the page; the engine writes its own "3d6" for the board's
#: casting roll, which no test here pins.
CASTING_DICE = "3d6"
#: The casting roll as a roll whose totals a stated total is held to.
CASTING_ROLL = DiceRollRules(CASTING_DICE)

#: The dice of the DEX roll a spell that must hit adds. casting-spells.md
#: names the roll ("Magic requiring hitting a target needs an additional DEX
#: roll") and gives it no dice; the engine reads it as the rules' standard
#: attribute check, 3d6 ≤ DEX (``tarmar_engine.spells``), and so does this.
AIM_DICE = "3d6"

#: The refusals a cast off the board gives (issue #18), each written once.
SPELL_NOT_CASTABLE = "{spell} is not a spell this character can cast; learn it first"
STATED_CASTING_DICE_GAME_MASTER_ONLY = "Only a Game Master can state the dice"
POOL_SHORT = "{spell} costs {cost} mana and the Pool holds {pool}"

#: The attribute field each spell's short attribute name reads.
_ATTRIBUTE_FIELD_BY_SHORT_NAME = {
    short_name: field_name for field_name, short_name in CORE_ATTRIBUTE_FIELDS
}


@dataclass(frozen=True)
class StoryCast:
    """One spell cast outside a battle (issue #18), every number the line prints.

    Attributes:
        spell: The spell's catalog key.
        spell_name: Its name.
        attribute: The attribute the roll is made under, as the spell names it.
        attribute_score: The character's effective (aged) score in it.
        injury_penalty: The injury table's -1 or -2, or 0.
        ceiling: The highest total that succeeds: the score less the injury,
            capped under the canon's fumble floor
            (``battle.magic_rules.loaded_casting_success_ceiling``).
        dice: The dice as the line prints them, or the Game Master's stated
            total.
        total: The casting roll's total.
        succeeded: Whether the spell is cast.
        tier: The success tier's label (``casting_success_tiers``), or ``""``.
        mana_cost: The spell's cost, its level.
        mana_spent: Mana taken from the Pool, less any refund.
        mana_refund: Mana a success tier gave back.
        mana_left: Mana in the Pool afterwards.
        runaway: Whether the total starts a Runaway, which the Game Master runs.
        hit: For a spell that must hit, the DEX roll's line; ``""`` otherwise
            or when the spell failed.
        game_master_call: What a Game Master cast past, which a player could
            not; ``""`` for an ordinary cast.
    """

    spell: str
    spell_name: str
    attribute: str
    attribute_score: int
    injury_penalty: int
    ceiling: int
    dice: str
    total: int
    succeeded: bool
    tier: str
    mana_cost: int
    mana_spent: int
    mana_refund: int
    mana_left: int
    runaway: bool
    hit: str
    game_master_call: str

    @property
    def line(self) -> str:
        """The history line: the dice, the ceiling, the outcome and the mana."""
        against = f"{self.attribute} {self.attribute_score}" + (
            f" {self.injury_penalty:+d} injured" if self.injury_penalty else ""
        )
        comparison = "≤" if self.succeeded else ">"
        outcome = "cast" if self.succeeded else "fails"
        text = (
            f"casts {self.spell_name}: {self.dice} {comparison} {self.ceiling} "
            f"({against}): {outcome}"
        )
        if self.tier:
            text += f", {self.tier}"
        text += f"; {self.mana_spent} mana spent"
        if self.mana_refund:
            text += f" after {self.mana_refund} back"
        text += f", {self.mana_left} left"
        if self.runaway:
            text += "; a Runaway starts, which the Game Master runs"
        if self.hit:
            text += f"; {self.hit}"
        if self.game_master_call:
            text += f" (a Game Master's call: {self.game_master_call})"
        return text


def cast_in_story(
    character: Character,
    spell_key: str,
    *,
    is_game_master: bool,
    stated_total: int | None = None,
    roller: Roller | None = None,
) -> StoryCast:
    """Cast a spell outside a battle, from the sheet (issue #18).

    The rules, from ``reference/content/public-rules/magic/``:
    casting-spells.md, "Spells cost mana equal to the spell's level" and
    "Roll: 3d6 ≤ INT or WIS (depending on the spell)", with "Magic requiring
    hitting a target needs an additional DEX roll"; the very high totals are
    the canon's ``casting_failure`` block, the very low its
    ``casting_success_tiers``, each read as the board reads them. The roll is
    judged by the board's own ceiling
    (``battle.magic_rules.loaded_casting_success_ceiling``), so a total
    succeeds here exactly when it would on the board. The injury table's
    penalty lowers the attribute, as it does every roll off the board.

    The mana is spent from the record's Pool (``Character.mana_spent``, the
    pools batch's field) on a success, and on a failure only on the canon's
    ``mana_lost_totals``; a success tier's refund comes back, never more than
    was spent. A Runaway the total starts is recorded on the line, and the
    Game Master runs it: nothing off the board drains a Pool turn by turn.
    Push, Channel and Borrowing are the board's and are not offered here.

    A player is held to the rules: a spell on the record at mastery 1 or more
    (spell-mastery.md, "Can cast the spell"), the whole cost in the Pool, a
    conscious caster, and dice the server rolls. A Game Master may cast a
    spell the record does not hold, past the Pool (spending only what is
    there), for an unconscious caster, or with a total rolled at his table,
    each named on the line. Nobody casts here for a dead character, an
    attribute nobody has recorded (DEX too, for a spell that must hit), or a
    character fighting on a live board, whose own turns cast. Every refusal
    is made before any die is rolled or any mana moves.

    Args:
        character: The character, changed in memory and not saved.
        spell_key: A key of the combat spell catalog.
        is_game_master: Whether a Game Master asked.
        stated_total: A Game Master's casting total from the table.
        roller: Roll through this roller instead of the dice module default.

    Returns:
        The cast.

    Raises:
        CharacterChangeError: Any refusal above.
    """
    spell = SPELLS.get(spell_key)
    if spell is None:
        raise CharacterChangeError(f"Unknown spell: {spell_key}")
    overridden: list[str] = []
    unconscious = unconscious_refusal(character)
    if unconscious is not None:
        if not is_game_master or not character.is_alive:
            raise CharacterChangeError(unconscious)
        overridden.append("unconscious")
    if seated_in_a_live_fight(character):
        raise CharacterChangeError(f"{character.name} {CAST_ON_THE_BOARD}")
    if not get_skill_level(spell_points(character, spell.key)):
        if not is_game_master:
            raise CharacterChangeError(SPELL_NOT_CASTABLE.format(spell=spell.name))
        overridden.append(f"{spell.name} is not on the record")
    if stated_total is not None:
        if not is_game_master:
            raise CharacterChangeError(STATED_CASTING_DICE_GAME_MASTER_ONLY)
        check_stated_total(CASTING_ROLL, stated_total)
        overridden.append("the dice stated")
    attribute_field = _ATTRIBUTE_FIELD_BY_SHORT_NAME[spell.attribute]
    score = effective_score(character, attribute_field)
    if score is None:
        raise CharacterChangeError(attribute_not_recorded(attribute_field))
    # Every refusal comes before any die is rolled or any mana moves, so a
    # refused cast leaves the record as it was.
    aim_dexterity = character.combat_dexterity_without_modifiers
    if spell.targeted and aim_dexterity is None:
        raise CharacterChangeError(attribute_not_recorded("dexterity"))
    pool = character.current_mana
    cost = spell.level
    if pool < cost:
        if not is_game_master:
            raise CharacterChangeError(
                POOL_SHORT.format(spell=spell.name, cost=cost, pool=pool)
            )
        overridden.append(f"{cost - pool} mana past the Pool, not spent")
    if stated_total is not None:
        total = stated_total
        dice = f"{CASTING_DICE} stated by a Game Master = {stated_total}"
    else:
        total, dice = roll_dice_logged(CASTING_DICE, roller)
    injured = injury_penalty(character)
    ceiling = loaded_casting_success_ceiling(score + injured)
    succeeded = total <= ceiling
    rules = magic_rules()
    casting_failure = rules.casting_failure
    pays = succeeded or (
        casting_failure is not None and total in casting_failure.mana_lost_totals
    )
    spent = min(cost, pool) if pays else 0
    tier = rules.success_tier(total) if succeeded else None
    refund = min(tier.mana_refund, spent) if tier is not None else 0
    character.mana_spent += spent - refund
    runaway = (
        not succeeded
        and casting_failure is not None
        and total == casting_failure.runaway_total
    )
    hit = ""
    if succeeded and aim_dexterity is not None and spell.targeted:
        hit = _hit_roll(
            aim_dexterity + injured, roller, server_rolled=stated_total is not None
        )
    return StoryCast(
        spell=spell.key,
        spell_name=spell.name,
        attribute=spell.attribute,
        attribute_score=score,
        injury_penalty=injured,
        ceiling=ceiling,
        dice=dice,
        total=total,
        succeeded=succeeded,
        tier=tier.label if tier is not None else "",
        mana_cost=cost,
        mana_spent=spent - refund,
        mana_refund=refund,
        mana_left=character.current_mana,
        runaway=runaway,
        hit=hit,
        game_master_call=", ".join(overridden),
    )


def _hit_roll(target: int, roller: Roller | None, *, server_rolled: bool) -> str:
    """The DEX roll a spell that must hit adds (casting-spells.md).

    "Magic requiring hitting a target needs an additional DEX roll": the aim
    dice under the board's DEX for the caster, the effective score less worn
    armour's own DEX penalty (``Character.combat_dexterity_without_modifiers``,
    which the battle seats), less the injury table's penalty. The board's
    aim adds its own situation (a dodging target, an off-balance caster),
    which off the board is the table's. What the spell hits is the table's
    too.

    Args:
        target: The highest total that hits.
        roller: Roll through this roller instead of the dice module default.
        server_rolled: Whether to say the server rolled the aim: so a Game
            Master who stated the casting total from his table reads that
            the aim was not his dice.

    Returns:
        The roll's part of the history line.
    """
    total, dice = roll_dice_logged(AIM_DICE, roller)
    landed = total <= target
    rolled_by = " rolled by the server" if server_rolled else ""
    return (
        f"to hit{rolled_by}, {dice} {'≤' if landed else '>'} {target} (DEX): "
        f"{'hits' if landed else 'misses'}"
    )
