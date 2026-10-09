"""Wilderness encounter tables and the seedable generator (issue #213).

The tables transcribe the hand-written Pillars campaign tables (the vault's
``Fastness/Pillars/Encounters.md``), fleshed out against the bestiary: every
row that has a match in the canonical 239-beast catalog carries that beast's
slug, so the generator can link straight to its stats. Rows that are Pillars
homebrew with no catalog entry (gremlins, the mountain giant, mountain goats)
keep their place in the tables with ``beast_slug=None`` — the table is the
campaign's, not the catalog's. Where a homebrew creature plays like a catalog
beast, the mapping is noted on the entry (spiderbats fly and drink blood, so
they use Vampire Bat stats; the desert sand worm is the Olgoi-Khorkhoi).

Structure: one d20 main table per terrain, and three d10 subtables (humans,
animals, special), each also per terrain. The deepest chain the tables allow
is main → humans/animals → special; the special tables are all concrete,
which ``encounters/tests/test_generation.py`` guards.

Pure Python below the Django line on purpose, like ``common.rolling``: the
tables and ``generate_encounter`` know nothing about models or requests. The
view resolves ``beast_slug`` to a ``Beast`` row itself.
"""

from dataclasses import dataclass

from common.rolling import Roller, RollRecord

MAIN_TABLE_SIZE = 20
SUBTABLE_SIZE = 10

TERRAINS: tuple[str, ...] = ("plains", "forest", "mountains", "desert")


@dataclass(frozen=True)
class EncounterEntry:
    """One row of an encounter table.

    Attributes:
        name: The campaign's name for the encounter, as the GM announces it.
        beast_slug: Slug of the catalog ``Beast`` supplying stats, when the
            bestiary has one.
        count: Number-appearing dice (``NdS``/``NdS±M``); ``None`` means one.
        subtable: Name of the subtable this row defers to instead of being a
            concrete encounter. A subtable row carries nothing else.
        note: GM-facing aside — stat substitutions, homebrew status.
    """

    name: str
    beast_slug: str | None = None
    count: str | None = None
    subtable: str | None = None
    note: str | None = None


# Shared rows, named once so a terrain table reads as a list of row names.
_HUMANS = EncounterEntry(name="Humans", subtable="humans")
_ANIMALS = EncounterEntry(name="Animals", subtable="animals")
_SPECIAL = EncounterEntry(name="Special", subtable="special")
_WOLF_PACK = EncounterEntry(name="Wolf pack", beast_slug="gray-wolf", count="2d6")
_TROLL = EncounterEntry(name="Troll", beast_slug="troll")
_MANTICORE = EncounterEntry(name="Manticore", beast_slug="manticore")
_DRAGON = EncounterEntry(
    name="Dragon",
    beast_slug="young-dragon",
    note="A 'base' dragon; the special table's dragons are full adults.",
)
_UNDEAD = EncounterEntry(name="Undead", beast_slug="ghoul", count="2d6")
_GREAT_UNDEAD = EncounterEntry(
    name="Greater undead",
    beast_slug="headless-man",
    note="Or another greater horror from the undead shelf, GM's choice.",
)
_SPIDERBAT = EncounterEntry(
    name="Spiderbats",
    beast_slug="vampire-bat",
    count="1d6",
    note="Pillars homebrew; flies and feeds like a Vampire Bat, so use its "
    "stats with a webbing attack.",
)
_GREMLINS = EncounterEntry(
    name="Gremlins",
    count="2d6",
    note="Pillars homebrew — no bestiary entry yet.",
)
_GIANT_SPIDER = EncounterEntry(name="Giant spider", beast_slug="giant-spider-adult")

MAIN_TABLES: dict[str, tuple[EncounterEntry, ...]] = {
    "plains": (
        _WOLF_PACK,  # 1
        _WOLF_PACK,  # 2
        _WOLF_PACK,  # 3
        _WOLF_PACK,  # 4
        _WOLF_PACK,  # 5
        _HUMANS,  # 6
        _HUMANS,  # 7
        _HUMANS,  # 8
        _SPIDERBAT,  # 9
        _SPIDERBAT,  # 10
        _ANIMALS,  # 11
        _ANIMALS,  # 12
        _ANIMALS,  # 13
        _TROLL,  # 14
        _MANTICORE,  # 15
        _MANTICORE,  # 16
        _DRAGON,  # 17
        _UNDEAD,  # 18
        _GREAT_UNDEAD,  # 19
        _SPECIAL,  # 20
    ),
    "forest": (
        _WOLF_PACK,  # 1
        _WOLF_PACK,  # 2
        _WOLF_PACK,  # 3
        EncounterEntry(name="Dire wolves", beast_slug="dire-wolf", count="1d6"),  # 4
        EncounterEntry(name="Dire wolves", beast_slug="dire-wolf", count="1d6"),  # 5
        _HUMANS,  # 6
        EncounterEntry(  # 7
            name="Spiders", beast_slug="venomous-spider", count="2d6"
        ),
        EncounterEntry(  # 8
            name="Spiders", beast_slug="venomous-spider", count="2d6"
        ),
        _GIANT_SPIDER,  # 9
        _GREMLINS,  # 10
        _ANIMALS,  # 11
        _ANIMALS,  # 12
        _ANIMALS,  # 13
        _TROLL,  # 14
        _MANTICORE,  # 15
        _DRAGON,  # 16
        _UNDEAD,  # 17
        _GREAT_UNDEAD,  # 18
        _GREAT_UNDEAD,  # 19
        _SPECIAL,  # 20
    ),
    "mountains": (
        EncounterEntry(name="Mountain lion", beast_slug="cougar"),  # 1
        EncounterEntry(name="Mountain lion", beast_slug="cougar"),  # 2
        EncounterEntry(  # 3
            name="Yeti",
            beast_slug="great-ape",
            count="1d3",
            note="The yeti-like thing of the high passes; Great Ape stats, "
            "white-furred and cold-proof.",
        ),
        EncounterEntry(  # 4
            name="Yeti",
            beast_slug="great-ape",
            count="1d3",
            note="The yeti-like thing of the high passes; Great Ape stats, "
            "white-furred and cold-proof.",
        ),
        _HUMANS,  # 5
        EncounterEntry(  # 6
            name="Dwarven expedition",
            count="2d6",
            note="Prospectors or a trade delegation; no bestiary stats — "
            "treat as armed humans.",
        ),
        EncounterEntry(  # 7
            name="Mountain giant",
            note="Pillars homebrew — no bestiary entry yet.",
        ),
        _SPIDERBAT,  # 8
        _GREMLINS,  # 9
        _GREMLINS,  # 10
        _ANIMALS,  # 11
        _ANIMALS,  # 12
        _ANIMALS,  # 13
        _TROLL,  # 14
        _MANTICORE,  # 15
        _DRAGON,  # 16
        _UNDEAD,  # 17
        _GREAT_UNDEAD,  # 18
        _GREAT_UNDEAD,  # 19
        _SPECIAL,  # 20
    ),
    "desert": (
        EncounterEntry(  # 1
            name="Scorpions", beast_slug="common-scorpion", count="1d6"
        ),
        EncounterEntry(  # 2
            name="Scorpions", beast_slug="common-scorpion", count="1d6"
        ),
        EncounterEntry(name="Giant scorpion", beast_slug="giant-scorpion"),  # 3
        _GIANT_SPIDER,  # 4
        _HUMANS,  # 5
        _HUMANS,  # 6
        EncounterEntry(  # 7
            name="Sand worm",
            beast_slug="olgoi-khorkhoi",
            note="The burrowing horror of the deep dunes.",
        ),
        EncounterEntry(  # 8
            name="Sand worm",
            beast_slug="olgoi-khorkhoi",
            note="The burrowing horror of the deep dunes.",
        ),
        EncounterEntry(  # 9
            name="Giant beetles", beast_slug="giant-bombardier-beetle", count="1d3"
        ),
        EncounterEntry(  # 10
            name="Giant beetles", beast_slug="giant-bombardier-beetle", count="1d3"
        ),
        EncounterEntry(name="Hydra", beast_slug="hydra"),  # 11
        EncounterEntry(  # 12
            name="Sand cat",
            beast_slug="cougar",
            note="Pillars homebrew; Cougar stats, sand-colored and ambushing "
            "from burrows.",
        ),
        _ANIMALS,  # 13
        EncounterEntry(  # 14
            name="Giant lizard", beast_slug="giant-monitor"
        ),
        EncounterEntry(  # 15
            name="Giant lizard", beast_slug="giant-monitor"
        ),
        _DRAGON,  # 16
        _UNDEAD,  # 17
        _GREAT_UNDEAD,  # 18
        _GREAT_UNDEAD,  # 19
        _SPECIAL,  # 20
    ),
}

_TRADERS = EncounterEntry(
    name="Trader caravan",
    count="2d6",
    note="Wagons, guards, and goods; disposition depends on the road so far.",
)
_BANDITS = EncounterEntry(name="Bandit band", count="2d6")
_TRIBAL = EncounterEntry(name="Tribal hunting party", count="3d6")
_ADVENTURERS = EncounterEntry(name="Adventuring party", count="1d6+1")
_DWARFS = EncounterEntry(name="Dwarven expedition", count="2d6")

_TURKEYS = EncounterEntry(
    name="Wild turkeys",
    count="1d6",
    note="Dinner, not danger — no bestiary entry.",
)
_SMALL_MAMMALS = EncounterEntry(
    name="Small mammals",
    beast_slug="fox",
    count="1d3",
    note="Foxes, hares, and other small game; Fox stats when it matters.",
)

SUBTABLES: dict[str, dict[str, tuple[EncounterEntry, ...]]] = {
    "humans": {
        "plains": (
            _TRADERS,  # 1
            _TRADERS,  # 2
            _TRADERS,  # 3
            _BANDITS,  # 4
            _BANDITS,  # 5
            _BANDITS,  # 6
            _TRIBAL,  # 7
            _TRIBAL,  # 8
            _ADVENTURERS,  # 9
            _SPECIAL,  # 10
        ),
        "forest": (
            _BANDITS,  # 1
            _BANDITS,  # 2
            _BANDITS,  # 3
            _BANDITS,  # 4
            _TRADERS,  # 5
            _TRIBAL,  # 6
            _TRIBAL,  # 7
            _TRIBAL,  # 8
            _ADVENTURERS,  # 9
            _SPECIAL,  # 10
        ),
        "mountains": (
            _DWARFS,  # 1
            _DWARFS,  # 2
            _BANDITS,  # 3
            _BANDITS,  # 4
            _BANDITS,  # 5
            _TRIBAL,  # 6
            _TRIBAL,  # 7
            _TRIBAL,  # 8
            _ADVENTURERS,  # 9
            _SPECIAL,  # 10
        ),
        "desert": (
            _TRADERS,  # 1
            _TRADERS,  # 2
            _TRADERS,  # 3
            _BANDITS,  # 4
            _BANDITS,  # 5
            _BANDITS,  # 6
            _TRIBAL,  # 7
            _TRIBAL,  # 8
            _ADVENTURERS,  # 9
            _SPECIAL,  # 10
        ),
    },
    "animals": {
        "plains": (
            EncounterEntry(name="Boars", beast_slug="wild-boar", count="1d6"),  # 1
            EncounterEntry(name="Boars", beast_slug="wild-boar", count="1d6"),  # 2
            EncounterEntry(name="Boars", beast_slug="wild-boar", count="1d6"),  # 3
            EncounterEntry(  # 4
                name="Deer", beast_slug="common-large-deer", count="2d6"
            ),
            EncounterEntry(  # 5
                name="Deer", beast_slug="common-large-deer", count="2d6"
            ),
            EncounterEntry(  # 6
                name="Deer", beast_slug="common-large-deer", count="2d6"
            ),
            _TURKEYS,  # 7
            _SMALL_MAMMALS,  # 8
            _SMALL_MAMMALS,  # 9
            _SPECIAL,  # 10
        ),
        "forest": (
            EncounterEntry(name="Boars", beast_slug="wild-boar", count="1d6"),  # 1
            EncounterEntry(name="Boars", beast_slug="wild-boar", count="1d6"),  # 2
            EncounterEntry(name="Boars", beast_slug="wild-boar", count="1d6"),  # 3
            EncounterEntry(  # 4
                name="Deer", beast_slug="common-small-deer", count="2d6"
            ),
            EncounterEntry(  # 5
                name="Deer", beast_slug="common-small-deer", count="2d6"
            ),
            EncounterEntry(  # 6
                name="Deer", beast_slug="common-small-deer", count="2d6"
            ),
            _TURKEYS,  # 7
            _SMALL_MAMMALS,  # 8
            _SMALL_MAMMALS,  # 9
            _SPECIAL,  # 10
        ),
        "mountains": (
            EncounterEntry(  # 1
                name="Mountain goats",
                count="1d6",
                note="No bestiary entry — sure-footed and unbothered.",
            ),
            EncounterEntry(  # 2
                name="Mountain goats",
                count="1d6",
                note="No bestiary entry — sure-footed and unbothered.",
            ),
            EncounterEntry(  # 3
                name="Mountain goats",
                count="1d6",
                note="No bestiary entry — sure-footed and unbothered.",
            ),
            EncounterEntry(  # 4
                name="Mountain goats",
                count="1d6",
                note="No bestiary entry — sure-footed and unbothered.",
            ),
            EncounterEntry(name="Mountain lion", beast_slug="cougar"),  # 5
            EncounterEntry(name="Mountain lion", beast_slug="cougar"),  # 6
            _TURKEYS,  # 7
            _SMALL_MAMMALS,  # 8
            _SMALL_MAMMALS,  # 9
            _SPECIAL,  # 10
        ),
        "desert": (
            EncounterEntry(  # 1
                name="Desert rats",
                beast_slug="giant-rat",
                count="2d6",
                note="Big, bold, and thirsty; Giant Rat stats.",
            ),
            EncounterEntry(  # 2
                name="Desert rats",
                beast_slug="giant-rat",
                count="2d6",
                note="Big, bold, and thirsty; Giant Rat stats.",
            ),
            EncounterEntry(  # 3
                name="Desert rats",
                beast_slug="giant-rat",
                count="2d6",
                note="Big, bold, and thirsty; Giant Rat stats.",
            ),
            EncounterEntry(  # 4
                name="Armadillo",
                beast_slug="glyptodont",
                note="A big one; Glyptodont stats.",
            ),
            EncounterEntry(  # 5
                name="Armadillo",
                beast_slug="glyptodont",
                note="A big one; Glyptodont stats.",
            ),
            EncounterEntry(name="Camels", beast_slug="camel", count="1d6"),  # 6
            EncounterEntry(name="Camels", beast_slug="camel", count="1d6"),  # 7
            EncounterEntry(name="Camels", beast_slug="camel", count="1d6"),  # 8
            EncounterEntry(  # 9
                name="Desert cat",
                beast_slug="cougar",
                note="Pillars homebrew; Cougar stats.",
            ),
            _SPECIAL,  # 10
        ),
    },
    "special": {
        "plains": (
            EncounterEntry(name="Chimera", beast_slug="chimera"),  # 1
            EncounterEntry(name="Chimera", beast_slug="chimera"),  # 2
            EncounterEntry(name="Chimera", beast_slug="chimera"),  # 3
            EncounterEntry(name="Wandering mage"),  # 4
            EncounterEntry(name="Undead mage"),  # 5
            EncounterEntry(name="Dragon", beast_slug="adult-dragon"),  # 6
            EncounterEntry(name="Eldar group", count="1d6"),  # 7
            EncounterEntry(name="Other travelers", count="2d6"),  # 8
            EncounterEntry(name="Dragon", beast_slug="adult-dragon"),  # 9
            EncounterEntry(name="Legendary character"),  # 10
        ),
        "forest": (
            EncounterEntry(name="Basilisk", beast_slug="basilisk"),  # 1
            EncounterEntry(name="Basilisk", beast_slug="basilisk"),  # 2
            EncounterEntry(name="Basilisk", beast_slug="basilisk"),  # 3
            EncounterEntry(name="Wandering mage"),  # 4
            EncounterEntry(name="Undead mage"),  # 5
            EncounterEntry(name="Undead mage"),  # 6
            EncounterEntry(name="Eldar group", count="1d6"),  # 7
            EncounterEntry(name="Other travelers", count="2d6"),  # 8
            EncounterEntry(name="Dragon", beast_slug="adult-dragon"),  # 9
            EncounterEntry(name="Legendary character"),  # 10
        ),
        "mountains": (
            EncounterEntry(name="Gryphon", beast_slug="gryphon"),  # 1
            EncounterEntry(name="Gryphon", beast_slug="gryphon"),  # 2
            EncounterEntry(name="Gryphon", beast_slug="gryphon"),  # 3
            EncounterEntry(name="Wandering mage"),  # 4
            EncounterEntry(name="Undead mage"),  # 5
            EncounterEntry(name="Undead mage"),  # 6
            EncounterEntry(name="Eldar group", count="1d6"),  # 7
            EncounterEntry(name="Other travelers", count="2d6"),  # 8
            EncounterEntry(name="Dragon", beast_slug="adult-dragon"),  # 9
            EncounterEntry(name="Legendary character"),  # 10
        ),
        "desert": (
            EncounterEntry(name="Sphinx", beast_slug="sphinx"),  # 1
            EncounterEntry(name="Sphinx", beast_slug="sphinx"),  # 2
            EncounterEntry(name="Sphinx", beast_slug="sphinx"),  # 3
            EncounterEntry(name="Wandering mage"),  # 4
            EncounterEntry(name="Undead mage"),  # 5
            EncounterEntry(name="Undead mage"),  # 6
            EncounterEntry(name="Eldar group", count="1d6"),  # 7
            EncounterEntry(name="Other travelers", count="2d6"),  # 8
            EncounterEntry(name="Dragon", beast_slug="adult-dragon"),  # 9
            EncounterEntry(name="Legendary character"),  # 10
        ),
    },
}


def _require_terrain(terrain: str) -> None:
    if terrain not in MAIN_TABLES:
        raise ValueError(f"Unknown terrain: {terrain}")


def main_entry(terrain: str, roll: int) -> EncounterEntry:
    """The main-table row a d20 ``roll`` lands on in ``terrain``."""
    _require_terrain(terrain)
    return MAIN_TABLES[terrain][roll - 1]


def subtable_entry(name: str, terrain: str, roll: int) -> EncounterEntry:
    """The row a d10 ``roll`` lands on in subtable ``name`` for ``terrain``."""
    _require_terrain(terrain)
    return SUBTABLES[name][terrain][roll - 1]


@dataclass(frozen=True)
class TableStep:
    """One table consulted on the way to the result: which, and what came up."""

    table: str
    roll: int
    entry_name: str


@dataclass(frozen=True)
class GeneratedEncounter:
    """A fully resolved encounter, with the trail of rolls that produced it.

    ``rolls`` holds every ``RollRecord`` in order (table rolls, then the
    number-appearing roll when the entry has count dice), so the page can show
    the GM the whole chain.
    """

    terrain: str
    steps: tuple[TableStep, ...]
    entry: EncounterEntry
    count: int | None
    rolls: tuple[RollRecord, ...]

    @property
    def count_label(self) -> str:
        return str(self.count) if self.count is not None else "1"


def generate_encounter(
    terrain: str,
    *,
    seed: int | None = None,
    roller: Roller | None = None,
) -> GeneratedEncounter:
    """Roll a complete encounter for ``terrain``.

    Args:
        terrain: One of :data:`TERRAINS`.
        seed: Seeds a private :class:`Roller` so the same seed reproduces the
            same encounter — how the generator page makes its results
            URL-addressable. Ignored when ``roller`` is given.
        roller: Caller-supplied roller, for callers that already record rolls
            (e.g. a battle engine seeding an encounter).

    Returns:
        The resolved :class:`GeneratedEncounter`.

    Raises:
        ValueError: If ``terrain`` is not a known terrain.
    """
    _require_terrain(terrain)
    active_roller = roller if roller is not None else Roller(seed=seed)
    records: list[RollRecord] = []

    def roll(specification: str, *, purpose: str) -> RollRecord:
        record = active_roller.roll(specification, purpose=purpose)
        records.append(record)
        return record

    first = roll("1d20", purpose="encounter table")
    entry = main_entry(terrain, first.total)
    steps = [TableStep(table="encounter", roll=first.total, entry_name=entry.name)]

    while entry.subtable is not None:
        name = entry.subtable
        record = roll("1d10", purpose=f"{name} subtable")
        entry = subtable_entry(name, terrain, record.total)
        steps.append(TableStep(table=name, roll=record.total, entry_name=entry.name))

    count = None
    if entry.count is not None:
        count = roll(entry.count, purpose="number appearing").total

    return GeneratedEncounter(
        terrain=terrain,
        steps=tuple(steps),
        entry=entry,
        count=count,
        rolls=tuple(records),
    )
