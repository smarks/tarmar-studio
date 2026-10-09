"""The skill check: which attribute a skill rides on, and the roll (issue #756).

Skill checks are the commonest roll outside combat, and the app had no
mechanic, no control and no skill-to-attribute map for them. Spencer's
ruling on #756 (2026-09-28): a skill check is the skills glossary's
mechanic, 2d6 + the governing attribute's modifier + the skill level against
a difficulty the Game Master sets; 3d6 ≤ attribute stays the mechanic for a
raw attribute check with no skill. Players roll through the sheet's control,
which rolls here with the dice module; a Game Master may override.

Scores are the effective (aged) ones, per the ruling on #773: checks use
aged scores everywhere except the creation-time checks.
"""

from dataclasses import dataclass

from .dice import Roller, roll_2d6_faces
from .models import Character
from .services import CharacterChangeError
from .skills import (
    SPECIALISED_SKILLS,
    calculate_attribute_modifier,
    get_skill_level,
    glossary_skill_level,
    split_specialised_skill,
)
from .weapon_skill import is_weapon_skill

#: The glossary's Governs column, by stored (normalised) skill name. ``None``
#: is the glossary's own "—": Luck is "Governed by no attribute" and is not
#: rolled as a check. The drift test reads the glossary's tables against this.
GOVERNING_ATTRIBUTES: dict[str, str | None] = {
    # Combat skills
    "weapon": "dexterity",
    "weapon parry": "dexterity",
    "archery": "dexterity",
    "poison": "intelligence",
    # Social & roguish skills
    "deceit": "charisma",
    "bluff": "charisma",
    "persuasion": "charisma",
    "diplomacy": "charisma",
    "detect truth": "wisdom",
    "sleight of hand": "dexterity",
    "stealth": "dexterity",
    "street smarts": "wisdom",
    "bribery": "charisma",
    "gamble": "intelligence",
    "morale": "charisma",
    "luck": None,
    # Trade, knowledge & craft skills
    "coins": "intelligence",
    "barter": "charisma",
    "appraise": "intelligence",
    "logistics": "intelligence",
    "literacy": "intelligence",
    "knowledge": "intelligence",
    "contracts/law": "intelligence",
    "maker skill": "intelligence",
    "arts": "charisma",
    "enchanting": "intelligence",
    # Wilderness, survival & labor skills
    "tracker": "wisdom",
    "hunting": "wisdom",
    "survival": "wisdom",
    "weather/path": "wisdom",
    "herb lore": "intelligence",
    "medic": "intelligence",
    "perception": "intelligence",
    "alert": "wisdom",
    "portage": "strength",
    "miner": "strength",
    "sailor": "dexterity",
}

#: Skills the glossary's tables do not list, and the attribute the rules
#: imply for each. Named in the PR for Spencer.
#:
#: * Arcane (True Magic) — the skills glossary prices it and channel.md
#:   uses it, but no table gives it a Governs entry. attributes.md: INT
#:   "governs a character's 'smarts'--the ability to perceive or infer
#:   information, retain it as knowledge, and apply it"; True Magic is that
#:   study, so INT.
#:
#: "Brawling" is not here: the rules have no such skill (#815, #867), and a
#: record still carrying one is checked on the attribute a Game Master names.
IMPLIED_GOVERNING_ATTRIBUTES: dict[str, str] = {
    "arcane": "intelligence",
}

#: Attribute names the check accepts, as the sheet and the model spell them.
ATTRIBUTES: tuple[str, ...] = (
    "strength",
    "dexterity",
    "intelligence",
    "wisdom",
    "constitution",
    "charisma",
)

#: The naturals: "A natural 12 always succeeds; a natural 2 always fails".
NATURAL_SUCCESS = 12
NATURAL_FAILURE = 2

#: What a focused Knowledge field adds to a check in it (#808).
FOCUSED_FIELD_BONUS = 1

#: The Literacy skill's stored name.
LITERACY_SKILL = "literacy"

#: Why an illiterate character rolls no Literacy check (issue #863). The
#: origin's literacy check decides whether a character can read at all
#: (location.md: "This is the check to be literate at all, and is separate
#: from the Literacy skill").
ILLITERATE_REASON = (
    "This character failed the literacy check, the check to be literate at "
    "all, and cannot read; only a Game Master can call for a Literacy check"
)

#: Why a character whose literacy nobody has settled rolls none either (the
#: #863 review): nothing yet says they can read.
LITERACY_UNSETTLED_REASON = (
    "This character's literacy check has not been rolled (or, at Consult GM, "
    "ruled), so nothing says they can read; only a Game Master can call for a "
    "Literacy check until it is"
)


def literacy_refusal(character: Character) -> str | None:
    """Why a player may not roll this character's Literacy check, or None.

    A player's check needs a character who can read: one who failed the
    literacy check cannot, and one whose check is not settled is not known
    to (#863 and its review). A Game Master may still call for one.
    """
    literate = character.is_literate
    if literate is False:
        return ILLITERATE_REASON
    if literate is None:
        return LITERACY_UNSETTLED_REASON
    return None


def unconscious_refusal(character: Character) -> str | None:
    """Why this character rolls nothing now, or None (the #840 review).

    injury-thresholds-death.md: a pool at 0 or below is unconscious, and the
    one roll left is the survival save. The dead roll nothing at all. The
    -1/-2 bands above 0 are :func:`injury_penalty`'s.
    """
    if not character.is_alive:
        return f"{character.name} is dead and makes no rolls"
    condition = character.condition
    if condition is not None and not condition.conscious:
        return (
            f"{character.name} is unconscious (a pool at 0 or below) and makes "
            "no rolls until healed above 0; only the survival save is rolled"
        )
    return None


#: The widest difficulty the control takes. The glossary sets no scale; 2d6
#: plus a peak modifier and a high skill reaches past 30 rarely.
MAX_DIFFICULTY = 40


def governing_attribute(skill_name: str) -> str | None:
    """The attribute a skill rides on, or None when no rule names one.

    A Weapon skill tied to a weapon ("weapon (broadsword)", or the older
    "broadsword") is the Weapon skill and rides on DEX; a Maker Skill or
    Knowledge with its chosen part ("maker skill (smith)") rides on the
    skill's own attribute.
    """
    if skill_name in GOVERNING_ATTRIBUTES:
        return GOVERNING_ATTRIBUTES[skill_name]
    # A Maker Skill or Knowledge with its chosen part is that skill (#808).
    chosen_under = split_specialised_skill(skill_name)[0]
    if chosen_under != skill_name:
        return GOVERNING_ATTRIBUTES[chosen_under]
    if skill_name in IMPLIED_GOVERNING_ATTRIBUTES:
        return IMPLIED_GOVERNING_ATTRIBUTES[skill_name]
    if is_weapon_skill(skill_name):
        return GOVERNING_ATTRIBUTES["weapon"]
    return None


def is_glossary_skill(skill_name: str) -> bool:
    """Whether the skills glossary names the skill this stored name falls under.

    A Weapon skill, tied or not, is the glossary's Weapon; a Maker Skill or
    Knowledge with its chosen part is that skill
    (:func:`characters.skills.glossary_skill`). Arcane counts, though no
    glossary table gives it an attribute (:data:`IMPLIED_GOVERNING_ATTRIBUTES`).
    "Brawling" and a freely typed "navigation" do not (issue #862).

    Args:
        skill_name: A stored (normalised) skill name.
    """
    from .skills import glossary_skill

    under = glossary_skill(skill_name)
    return under in GOVERNING_ATTRIBUTES or under in IMPLIED_GOVERNING_ATTRIBUTES


def effective_score(character: Character, attribute: str) -> int | None:
    """The aged score the check reads (ruling on #773); None if unrecorded.

    Charisma takes no aging penalty, so its stored score is its effective one.
    """
    if attribute == "charisma":
        return character.charisma
    return getattr(character, f"effective_{attribute}")


@dataclass(frozen=True)
class SkillCheck:
    """One skill check, every number the sheet prints.

    Attributes:
        skill: The skill checked.
        level: The character's level in it; 0 untrained.
        attribute: The attribute used.
        attribute_score: Its effective (aged) score.
        attribute_modifier: That score's modifier.
        substituted: Whether a Game Master named a different attribute.
        faces: The two dice, or ``()`` when a Game Master stated the total.
        stated: Whether a Game Master stated the dice.
        dice_total: The 2d6 total.
        total: Dice + modifier + level + focus bonus + injury penalty.
        difficulty: The difficulty, or None when none was given.
        outcome: "success", "failure", or "" with no difficulty.
        natural: "natural 12" / "natural 2" when a natural decided it.
        focus_bonus: +1 for a check in a focused Knowledge field (#808).
        injury_penalty: The injury table's -1 or -2 to all rolls, or 0
            (issue #840).
        game_master_call: What a Game Master called the check past, which a
            player could not (``"unconscious"``, ``"not known to read"``);
            ``""`` for an ordinary check.
    """

    skill: str
    level: int
    attribute: str
    attribute_score: int
    attribute_modifier: int
    substituted: bool
    faces: tuple[int, ...]
    stated: bool
    dice_total: int
    total: int
    difficulty: int | None
    outcome: str
    natural: str
    focus_bonus: int = 0
    injury_penalty: int = 0
    game_master_call: str = ""

    @property
    def line(self) -> str:
        """The history line."""
        dice = (
            f"2d6 {list(self.faces)} = {self.dice_total}"
            if not self.stated
            else f"2d6 stated by a Game Master = {self.dice_total}"
        )
        attribute = self.attribute[:3].upper()
        text = (
            f"{self.skill} check: {dice} {self.attribute_modifier:+d} {attribute} "
            f"{self.level:+d} level"
            + (f" {self.focus_bonus:+d} focus" if self.focus_bonus else "")
        )
        if self.injury_penalty:
            text += f" {self.injury_penalty:+d} injured"
        text += f" = {self.total}"
        if self.substituted:
            text += f" ({attribute} named by a Game Master)"
        if self.difficulty is not None:
            text += f" vs {self.difficulty}"
        if self.outcome:
            text += f": {self.outcome}"
        if self.natural:
            text += f" ({self.natural})"
        if self.game_master_call:
            text += f" (a Game Master's call: {self.game_master_call})"
        return text


def injury_penalty(character: Character) -> int:
    """The injury table's penalty on a roll the character makes (issue #840).

    injury-thresholds-death.md: "Down to 1/2 (but above 5) — −1 to all rolls;
    Between 1 and 5 — −2 to all rolls", the worse pool governing. The record
    knows where the pools stand (``Character.condition``, #751), and the board
    applies it (``TarmarReactions.injury_penalty``); a roll made off the board
    applies the same number.

    Returns:
        -1, -2, or 0 (0 too for a character whose pools are not yet known).
    """
    condition = character.condition
    return condition.roll_penalty if condition is not None else 0


def skill_check(
    character: Character,
    skill_name: str,
    *,
    difficulty: int | None = None,
    attribute: str | None = None,
    stated_total: int | None = None,
    is_game_master: bool = False,
    roller: Roller | None = None,
) -> SkillCheck:
    """Roll one skill check for a character.

    The rule, from
    ``reference/content/public-rules/characters/prior-experience/skills-glossary.md``:

        "**Skill check:** roll **2d6 + the governing attribute's modifier +
        your skill level**, against a difficulty the GM sets. A natural 12
        always succeeds; a natural 2 always fails. Untrained, you roll with
        the attribute modifier alone."

        "The **Governs** column below names the attribute whose modifier the
        skill rides on; the GM may substitute a better-fit attribute for an
        unusual use."

    A player rolls with the governing attribute; only a Game Master sets the
    difficulty ("a difficulty the GM sets"), names another attribute or one
    for a skill no rule governs, or states dice rolled at the table (the
    override Spencer's ruling allows). A player may roll as often as they
    like, so each of their rolls is recorded with no difficulty and no
    outcome, only the dice (a natural named as such): what makes a roll
    count is the Game Master asking for it, and the Game Master sets the
    difficulty and reads the result (coordinator's ruling on the #756
    review, marked for Spencer).

    Args:
        character: The character; not changed.
        skill_name: A stored (normalised) skill name: one the character has,
            or a glossary skill rolled untrained.
        difficulty: The Game Master's difficulty, or None.
        attribute: A Game Master's substitute attribute.
        stated_total: A Game Master's 2d6 total from the table.
        is_game_master: Whether a Game Master asked.
        roller: Roll through this roller instead of the dice module default.

    Returns:
        The check.

    Raises:
        CharacterChangeError: An unknown skill, Luck, a player overriding,
            a player's Literacy check for a character who failed the literacy
            check or has not settled it, or a player's check for an
            unconscious character (a Game Master may call either, and the
            line says so), a dead character, or an attribute not yet
            recorded.
    """
    # What only a Game Master may call past (the standing rule), named on
    # the check's line when he does: a Literacy check for a character not
    # known to read (#863), and a check for an unconscious one (second review
    # of #840). The dead roll nothing, for anyone.
    overridden: list[str] = []
    unread = literacy_refusal(character) if skill_name == LITERACY_SKILL else None
    if unread is not None:
        if not is_game_master:
            raise CharacterChangeError(unread)
        overridden.append("not known to read")
    unconscious = unconscious_refusal(character)
    if unconscious is not None:
        if not is_game_master or not character.is_alive:
            raise CharacterChangeError(unconscious)
        overridden.append("unconscious")
    skills = character.skills or {}
    governing = governing_attribute(skill_name)
    if skill_name not in skills and skill_name not in GOVERNING_ATTRIBUTES:
        raise CharacterChangeError(f"Unknown skill: {skill_name}")
    if skill_name in GOVERNING_ATTRIBUTES and governing is None:
        raise CharacterChangeError(
            "Luck is not rolled as a check: once per session per level, after "
            "seeing a roll, add its level to that roll"
        )
    if not is_game_master and (
        attribute is not None or stated_total is not None or difficulty is not None
    ):
        raise CharacterChangeError(
            "Only a Game Master can set the difficulty, name the attribute or "
            "state the dice"
        )
    if attribute is not None and attribute not in ATTRIBUTES:
        raise CharacterChangeError(f"Unknown attribute: {attribute}")
    used = attribute or governing
    if used is None:
        raise CharacterChangeError(
            f"No rule names the attribute {skill_name} rides on; a Game Master "
            "names one for the check"
        )
    if difficulty is not None and not 0 <= difficulty <= MAX_DIFFICULTY:
        raise CharacterChangeError(f"Difficulty must be between 0 and {MAX_DIFFICULTY}")
    score = effective_score(character, used)
    if score is None:
        raise CharacterChangeError(f"{used.title()} is not recorded yet")

    if stated_total is not None:
        if not 2 <= stated_total <= 12:
            raise CharacterChangeError("A 2d6 total is between 2 and 12")
        faces: tuple[int, ...] = ()
        dice_total = stated_total
    else:
        first_die, second_die, dice_total = roll_2d6_faces(roller)
        faces = (first_die, second_die)

    if skill_name in SPECIALISED_SKILLS:
        # A check on the bare Maker Skill or Knowledge reads the chosen part
        # the character holds it as (#808), not 0.
        level = glossary_skill_level(skills, skill_name)
    else:
        level = get_skill_level(int(skills.get(skill_name, 0) or 0), skill_name)
    modifier = calculate_attribute_modifier(score)
    # The glossary: "the GM may let you focus a field for a larger bonus in
    # it". The bonus is +1 to checks in the field: coordinator's ruling under
    # the standing rule; Spencer may overrule (#808).
    chosen_under, field_detail = split_specialised_skill(skill_name)
    focus_bonus = (
        FOCUSED_FIELD_BONUS if chosen_under == "knowledge" and field_detail else 0
    )
    injured = injury_penalty(character)
    total = dice_total + modifier + level + focus_bonus + injured
    natural = {NATURAL_SUCCESS: "natural 12", NATURAL_FAILURE: "natural 2"}.get(
        dice_total, ""
    )
    outcome = ""
    if difficulty is not None:
        if dice_total == NATURAL_SUCCESS:
            outcome = "success"
        elif dice_total == NATURAL_FAILURE:
            outcome = "failure"
        else:
            outcome = "success" if total >= difficulty else "failure"
    return SkillCheck(
        skill=skill_name,
        level=level,
        attribute=used,
        attribute_score=score,
        attribute_modifier=modifier,
        substituted=attribute is not None and attribute != governing,
        faces=faces,
        stated=stated_total is not None,
        dice_total=dice_total,
        total=total,
        difficulty=difficulty,
        outcome=outcome,
        natural=natural,
        focus_bonus=focus_bonus,
        injury_penalty=injured,
        game_master_call=", ".join(overridden),
    )
