"""Roll the Dungeons & Ruins tables into a populated site (issue #194).

Every die comes through :class:`common.rolling.Roller`, so a seed reproduces
the whole ruin, and every roll is kept as a full :class:`RollRecord` for
display. The tables themselves come from :mod:`ruins.tables`, parsed from the
reference markdown at call time — this module only names the tables it needs.
"""

from dataclasses import dataclass

from common.rolling import Roller, RollRecord
from ruins.tables import DiceTable, load_tables

ORIGIN_TABLE = "Built by..."
PURPOSE_TABLE = "Original Purpose"
CURRENT_STATE_TABLE = "Current State"
OCCUPANTS_TABLE = "Currently inhabited by..."
ROOM_CONTENTS_TABLE = "This area contains..."
HAZARD_TABLE = "Environmental Hazard"
TREASURE_TYPE_TABLE = "Treasure Type"
TREASURE_COMPLICATION_TABLE = "Treasure Complication"

# Every table the generator rolls. The drift guard in tests/test_tables.py
# asserts the markdown still publishes exactly this set, so a renamed or
# deleted table fails loudly instead of silently changing the generator.
EXPECTED_TABLE_TITLES = (
    ORIGIN_TABLE,
    PURPOSE_TABLE,
    CURRENT_STATE_TABLE,
    OCCUPANTS_TABLE,
    ROOM_CONTENTS_TABLE,
    HAZARD_TABLE,
    TREASURE_TYPE_TABLE,
    TREASURE_COMPLICATION_TABLE,
)

MIN_ROOMS = 1
MAX_ROOMS = 20
DEFAULT_ROOMS = 5


@dataclass(frozen=True)
class TableRoll:
    """One table lookup: the roll and the row it landed on.

    Attributes:
        table_title: The table's header label from the markdown.
        die: The die notation rolled, e.g. ``"d8"``.
        face: The face rolled.
        result: The matched row's text.
        record: The full :class:`RollRecord` behind the roll.
    """

    table_title: str
    die: str
    face: int
    result: str
    record: RollRecord


def roll_table(table: DiceTable, roller: Roller) -> TableRoll:
    """Roll one table's die and look up the row the face lands on.

    Args:
        table: The table to roll on.
        roller: The roller every die comes through, so a seeded roller
            reproduces the lookup.

    Returns:
        The roll and the row it landed on.
    """
    record = roller.roll(f"1d{table.die_sides}", purpose=table.title)
    face = record.faces[0]
    return TableRoll(
        table_title=table.title,
        die=table.die,
        face=face,
        result=table.row_for_face(face),
        record=record,
    )


@dataclass(frozen=True)
class GeneratedRoom:
    """One rolled room: its contents and its environmental hazard."""

    number: int
    contents: TableRoll
    hazard: TableRoll


@dataclass(frozen=True)
class GeneratedRuin:
    """A fully rolled site, with every roll preserved for display."""

    seed: int | None
    origin: TableRoll
    purpose: TableRoll
    current_state: TableRoll
    occupants: TableRoll
    rooms: tuple[GeneratedRoom, ...]
    treasure_type: TableRoll
    treasure_complication: TableRoll

    @property
    def site_rolls(self) -> tuple[TableRoll, ...]:
        """The site-level rolls, in the order the tables appear on the page."""
        return (self.origin, self.purpose, self.current_state, self.occupants)

    @property
    def all_rolls(self) -> tuple[TableRoll, ...]:
        """Every roll made, in the order it was made."""
        rolls = list(self.site_rolls)
        for room in self.rooms:
            rolls.append(room.contents)
            rolls.append(room.hazard)
        rolls.append(self.treasure_type)
        rolls.append(self.treasure_complication)
        return tuple(rolls)


def generate_ruin(
    room_count: int = DEFAULT_ROOMS, seed: int | None = None
) -> GeneratedRuin:
    """Roll every table and return the populated ruin.

    Args:
        room_count: How many rooms to roll (``MIN_ROOMS``–``MAX_ROOMS``).
        seed: When given, the same seed reproduces the same ruin.

    Raises:
        ValueError: If ``room_count`` is out of bounds.
        KeyError: If the markdown no longer publishes an expected table
            (the drift guard test catches this before it can happen live).
    """
    if not MIN_ROOMS <= room_count <= MAX_ROOMS:
        raise ValueError(
            f"Room count must be between {MIN_ROOMS} and {MAX_ROOMS}, got {room_count}"
        )
    tables = load_tables()
    roller = Roller(seed=seed)

    def roll_on(table_title: str) -> TableRoll:
        return roll_table(tables[table_title], roller)

    origin = roll_on(ORIGIN_TABLE)
    purpose = roll_on(PURPOSE_TABLE)
    current_state = roll_on(CURRENT_STATE_TABLE)
    occupants = roll_on(OCCUPANTS_TABLE)
    rooms = tuple(
        GeneratedRoom(
            number=room_number,
            contents=roll_on(ROOM_CONTENTS_TABLE),
            hazard=roll_on(HAZARD_TABLE),
        )
        for room_number in range(1, room_count + 1)
    )
    return GeneratedRuin(
        seed=seed,
        origin=origin,
        purpose=purpose,
        current_state=current_state,
        occupants=occupants,
        rooms=rooms,
        treasure_type=roll_on(TREASURE_TYPE_TABLE),
        treasure_complication=roll_on(TREASURE_COMPLICATION_TABLE),
    )
