"""Character sheets for the GM session (issue #341).

A session may have :class:`characters.models.Character` rows attached. The
chat page's Characters panel shows each as a complete sheet beside the chat,
built from one :class:`CharacterSheet` snapshot. The model does not read this
snapshot: it reads each call's state block (issue #590,
:func:`gm.turn_engine.state.key_figures`), built from the same record. That
line carries the figures, skills, gear, purse, spells, aged scores and
literacy (issue #771) but not every line of the sheet -- modifiers,
encumbrance and the physical and mental stats are the panel's alone -- so a
figure added here is not the model's until it is added there too.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from characters.magic import known_spells
from characters.models import Character
from characters.purse import format_purse
from characters.skills import calculate_attribute_modifier, format_skill_level

ATTRIBUTE_LABELS = (
    ("STR", "strength", "effective_strength"),
    ("DEX", "dexterity", "effective_dexterity"),
    ("INT", "intelligence", "effective_intelligence"),
    ("WIS", "wisdom", "effective_wisdom"),
    ("CON", "constitution", "effective_constitution"),
    ("CHR", "charisma", "charisma"),
)


@dataclass(frozen=True)
class AttributeLine:
    """One core attribute as the sheet shows it.

    ``effective`` and ``modifier`` are ``None`` until all six attributes are
    recorded, and ``base`` is ``None`` for one not rolled yet: a character a
    session is still building is shown by this same sheet (issue #848).
    """

    label: str
    base: int | None
    effective: int | None
    modifier: int | None

    @property
    def is_set(self) -> bool:
        """Whether this attribute has been rolled or bought."""
        return self.base is not None


@dataclass(frozen=True)
class SkillLine:
    """One skill: its name, level (roman, per ``format_skill_level``) and points."""

    name: str
    level_display: str
    points: int


@dataclass(frozen=True)
class EquipmentLine:
    """One carried item, as stored on ``Character.equipment``."""

    name: str
    quantity: int
    equipped: bool
    in_pack: bool


@dataclass(frozen=True)
class CharacterSheet:
    """A read-only snapshot of one character for the panel.

    Every character in the Characters pane is shown from one of these, an
    attached one and one the session built alike (issue #848), so a line
    added here reaches both. A character still being built has ``None``
    wherever the rules cannot give a number yet -- the pools, Physical,
    Mental and the load -- and the pane says so rather than inventing one.
    """

    pk: int
    name: str
    is_alive: bool
    death_cause: str
    attributes: tuple[AttributeLine, ...]
    fatigue: int | None
    body: int | None
    current_fatigue: int | None
    current_body: int | None
    condition: str | None
    mana: int
    current_mana: int
    physical: int | None
    mental: int | None
    skills: tuple[SkillLine, ...]
    free_skill_points: int
    equipment: tuple[EquipmentLine, ...]
    encumbrance_level: str | None
    wealth_level: str
    literacy: str
    # The background, and whether each part of it was rolled or set (issues
    # #754, #848): the chat-built character's pane has always marked what
    # nobody rolled, and one sheet now shows it for every character.
    provenance_class: str
    provenance_detail: str
    location: str
    provenance_recorded: bool
    location_recorded: bool
    wealth_recorded: bool
    # None for a Rich character until a Game Master sets it (issue #749).
    starting_coin: int | None
    money_earned: int
    #: The coin held now, as the sheet prints it (issue #757).
    purse: str
    #: Spells known, "Name (mastery)" (issue #774).
    spells: tuple[str, ...]
    total_xp: int
    level: int
    age: int
    experience_years: int

    @classmethod
    def from_character(cls, character: Character) -> "CharacterSheet":
        """Snapshot ``character`` through the model's derived properties.

        Args:
            character: The saved character.

        Returns:
            The sheet, with skills sorted by name so the rendering (and its
            hash) does not depend on JSON key order.
        """
        # An aged score subtracts a penalty from the base, so it is read only
        # once every base is known -- the rule the draft ledger has kept since
        # issue #374 -- and a character still being built shows its rolled
        # scores without a modifier (issue #848).
        known = all(
            getattr(character, base_field) is not None
            for _label, base_field, _effective in ATTRIBUTE_LABELS
        )
        attributes = tuple(
            AttributeLine(
                label=label,
                base=getattr(character, base_field),
                effective=getattr(character, effective_field) if known else None,
                modifier=(
                    calculate_attribute_modifier(getattr(character, effective_field))
                    if known
                    else None
                ),
            )
            for label, base_field, effective_field in ATTRIBUTE_LABELS
        )
        skills = tuple(
            SkillLine(
                name=skill_name,
                level_display=format_skill_level(points, skill_name),
                points=points,
            )
            for skill_name, points in sorted((character.skills or {}).items())
        )
        # Through equipment_rows, which resolves each stored entry against
        # the catalog (issue #633). A catalog entry stores only item_id,
        # quantity, in_pack and notes, so reading "name" off it gave
        # "Unknown" for every bought item -- in the Characters pane, and in
        # the hidden sheet entry the model was handed for that character
        # (issue #718).
        equipment = tuple(
            EquipmentLine(
                name=row.name,
                quantity=row.quantity,
                equipped=row.equipped,
                in_pack=row.in_pack,
            )
            for row in character.equipment_rows
        )
        # A character a session is still building has no pools, Physical,
        # Mental or load yet (issues #571, #848); the load's level is priced
        # against STR, so it is unknown while STR is (issue #630). Each is
        # None until the rules can give it.
        condition = character.condition
        encumbrance_level = character.encumbrance_level
        return cls(
            pk=character.pk,
            name=character.name,
            is_alive=character.is_alive,
            death_cause=character.death_cause,
            attributes=attributes,
            fatigue=character.fatigue,
            body=character.body,
            # What the fights left them (issue #751): the record's current
            # pools and the injury table's row for them.
            current_fatigue=condition.fatigue if condition is not None else None,
            current_body=condition.body if condition is not None else None,
            condition=condition.state if condition is not None else None,
            # The Mana Pool is the Spell Points earned through the Magic
            # track (gm/engine.py, "Derived Pools"); the model stores them as
            # free_spell_points.
            mana=character.free_spell_points,
            current_mana=character.current_mana,
            physical=character.physical,
            mental=character.mental,
            skills=skills,
            free_skill_points=character.free_skill_points,
            equipment=equipment,
            encumbrance_level=encumbrance_level,
            wealth_level=character.wealth_level,
            # Whether the character can read, which the Games Master should
            # know (issue #741): the origin's check and the stored roll.
            literacy=character.literacy_summary,
            provenance_class=character.provenance_class,
            provenance_detail=character.provenance_detail,
            location=character.location,
            provenance_recorded=character.provenance_recorded,
            location_recorded=character.location_recorded,
            wealth_recorded=character.wealth_recorded,
            starting_coin=character.starting_coin,
            money_earned=character.money_earned,
            purse=format_purse(character),
            spells=tuple(
                f"{spell.name} ({spell.mastery_display})"
                for spell in known_spells(character)
            ),
            total_xp=character.total_xp,
            level=character.level,
            age=character.age,
            experience_years=character.experience_years,
        )


def pools_line(character: Character) -> str:
    """Fatigue and Body as a turn's character line writes them (issue #751).

    The pools the record holds now against their maxima, with the injury
    table's row for them -- ``Fatigue 27/44, Body 30/30 (-1 to all rolls)``
    -- or what they still wait for. After a lost fight the line used to say
    ``Fatigue 44, Body 30`` beside a battle summary saying unconscious, and
    the model was asked to carry the difference itself.
    """
    if character.fatigue_roll is None:
        return "Fatigue roll not made yet"
    condition = character.condition
    if condition is None:
        return f"Fatigue roll {character.fatigue_roll}"
    # Spent mana stays spent between fights, like a wound.
    mana = f", {character.mana_text}" if character.mana_text else ""
    return f"{condition.summary}{mana}"


def sheets_for(characters: Iterable[Character]) -> list[CharacterSheet]:
    """Snapshot ``characters`` in the order given."""
    return [CharacterSheet.from_character(character) for character in characters]
