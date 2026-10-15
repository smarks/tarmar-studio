"""Deterministic TFT → Tarmar translation for bestiary source stats.

The rules of the mapping live in ``docs/bestiary-translation.md``; this module
is their single executable form. ``bestiary/fixtures/beasts.json`` is generated
from ``bestiary/data/tft_source_stats.json`` by running every record through
:func:`translate` (see the ``build_beast_fixture`` management command), and a
drift-guard test asserts the committed fixture matches this code's output —
change either side and the guard fails until they agree again.

Everything here is pure: no Django models, no randomness.
"""

import math
import re
from collections.abc import Mapping

from django.utils.text import slugify

from characters.rules import character_rules, scores_by_short_name

# --- categories -------------------------------------------------------------
# The book's table-of-contents sections, grouped into the catalog's families.
SECTION_CATEGORY: dict[str, str] = {
    # Insects, vermin, and crawling hazards (the slimes live here as vermin).
    "Ants": "insects_vermin",
    "Giant Ant Lion": "insects_vermin",
    "Giant Bombardier Beetle": "insects_vermin",
    "Giant Praying Mantis": "insects_vermin",
    "Head Fly": "insects_vermin",
    "Leeches": "insects_vermin",
    "Myrmecoleon": "insects_vermin",
    "Olgoi-khorkhoi": "insects_vermin",
    "Rats": "insects_vermin",
    "Scavenger Scarab": "insects_vermin",
    "Scolopendra": "insects_vermin",
    "Scorpions": "insects_vermin",
    "Scuttle": "insects_vermin",
    "Slimes": "insects_vermin",
    "Spiders": "insects_vermin",
    "Stone Beetle": "insects_vermin",
    "Stonemason": "insects_vermin",
    "Wasps": "insects_vermin",
    # Mammals.
    "Bats": "mammals",
    "Bears": "mammals",
    "Bovines": "mammals",
    "Camel": "mammals",
    "Canines": "mammals",
    "Deer": "mammals",
    "Equines": "mammals",
    "Felines": "mammals",
    "Giant Beaver": "mammals",
    "Giant Jackalope": "mammals",
    "Giant Wolverine": "mammals",
    "Glyptodont": "mammals",
    "Hyena": "mammals",
    "Ichneumon": "mammals",
    "Kangaroos": "mammals",
    "Pachyderms": "mammals",
    "Plocker": "mammals",
    "Scum Bunny": "mammals",
    "Simians": "mammals",
    "Slinker": "mammals",
    "Swine": "mammals",
    # Birds.
    "Bare Owl": "birds",
    "Diatryma": "birds",
    "Moa": "birds",
    "Ostrich": "birds",
    "Parrots": "birds",
    "Piranhakeet": "birds",
    "Rocs": "birds",
    "Sinstoner": "birds",
    "Swordbird": "birds",
    # Reptiles and amphibians.
    "Crocodilians": "reptiles_amphibians",
    "Dipsa": "reptiles_amphibians",
    "Frogs and Toads": "reptiles_amphibians",
    "Lizards": "reptiles_amphibians",
    "Snakes": "reptiles_amphibians",
    "Snapping Turtle": "reptiles_amphibians",
    # Sea creatures.
    "Archer Crab": "sea_creatures",
    "Crabman": "sea_creatures",
    "Gitaskog": "sea_creatures",
    "Kraken": "sea_creatures",
    "Man o’ War": "sea_creatures",
    "Minipus": "sea_creatures",
    "Ness": "sea_creatures",
    "Octopus": "sea_creatures",
    "Sea Scorpion": "sea_creatures",
    "Shadow Sharks": "sea_creatures",
    "Uncle Teeth": "sea_creatures",
    # Dragons and dragonkin.
    "Dragonet": "dragons",
    "Dragons": "dragons",
    "Hydra": "dragons",
    "Hydret": "dragons",
    "Tatzelwurm": "dragons",
    "Wyvern": "dragons",
    # Elementals.
    "Elementals": "elementals",
    # Undead and spirits.
    "Flying Head": "undead_spirits",
    "Ghoul": "undead_spirits",
    "Headless Man": "undead_spirits",
    # Plants.
    "Am Bush": "plants",
    "Bloodtree": "plants",
    "Cannon Flower": "plants",
    "Florathings": "plants",
    "Forest Star": "plants",
    "Mandrake": "plants",
    "Pit Trap Plant": "plants",
    "Tepes Briar": "plants",
    # Constructs.
    "Golems": "constructs",
    "Vitranth": "constructs",
    # Legendary beasts and folklore creatures.
    "Apep": "legendary",
    "Arachne": "legendary",
    "Basilisk": "legendary",
    "Catoblepas": "legendary",
    "Chimera": "legendary",
    "Cockatrice": "legendary",
    "Gryphon": "legendary",
    "Harpy": "legendary",
    "Hippogriff": "legendary",
    "Kingowing": "legendary",
    "Long Lankin": "legendary",
    "Mana Sponge": "legendary",
    "Manticore": "legendary",
    "Mauler": "legendary",
    "Monopod": "legendary",
    "Mothman": "legendary",
    "Phoenix": "legendary",
    "Sphinx": "legendary",
    "Troll": "legendary",
    "Wampus Cat": "legendary",
}

# WIS is not a TFT stat. Assigned per category cunning: dim for vermin and
# plants, animal-level for beasts, high for the cunning and the uncanny.
CATEGORY_WISDOM: dict[str, int] = {
    "insects_vermin": 3,
    "mammals": 5,
    "birds": 5,
    "reptiles_amphibians": 4,
    "sea_creatures": 4,
    "dragons": 12,
    "elementals": 8,
    "undead_spirits": 10,
    "plants": 2,
    "constructs": 3,
    "legendary": 8,
}

# CHA is not a TFT stat either. Same scheme: presence tracks the category.
CATEGORY_CHARISMA: dict[str, int] = {
    "insects_vermin": 2,
    "mammals": 4,
    "birds": 5,
    "reptiles_amphibians": 3,
    "sea_creatures": 3,
    "dragons": 13,
    "elementals": 6,
    "undead_spirits": 5,
    "plants": 2,
    "constructs": 3,
    "legendary": 8,
}


def fixed_fatigue_roll() -> int:
    """The Fatigue roll a beast is given: the midpoint of the canon's dice.

    The Character fatigue formula uses a rolled 2d6 (on the public pages);
    beasts get its midpoint, 7, so the mapping stays deterministic.
    """
    fatigue_roll = character_rules().fatigue_roll
    return (fatigue_roll.minimum + fatigue_roll.maximum) // 2


# Natural-attack keyword → Tarmar weapon class (keys of combat.MATRIX).
# First match in order wins; the default is Striking.
_ATTACK_CLASS_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("constrict", "Flexible / Snare"),
    ("tentacle", "Flexible / Snare"),
    ("grab", "Flexible / Snare"),
    ("grapple", "Flexible / Snare"),
    ("engulf", "Flexible / Snare"),
    ("smother", "Flexible / Snare"),
    ("snare", "Flexible / Snare"),
    ("arms", "Flexible / Snare"),
    ("gobble", "Flexible / Snare"),
    ("breath", "Missile — Bows"),
    ("fireball", "Missile — Bows"),
    ("spit", "Missile — Bows"),
    ("spray", "Missile — Bows"),
    ("gas", "Missile — Bows"),
    ("pod", "Missile — Bows"),
    ("shot", "Missile — Bows"),
    ("jet", "Missile — Bows"),
    ("drop", "Missile — Bows"),
    ("razor feathers", "Missile — Bows"),
    ("gore", "Thrusting"),
    ("horn", "Thrusting"),
    ("charge", "Thrusting"),
    ("stab", "Thrusting"),
    ("bite", "Piercing"),
    ("sting", "Piercing"),
    ("fang", "Piercing"),
    ("beak", "Piercing"),
    ("peck", "Piercing"),
    ("bill", "Piercing"),
    ("tongue", "Piercing"),
    ("spine", "Piercing"),
    ("tusk", "Piercing"),
    ("venom", "Piercing"),
)

# A beast this strong swings with mass behind it: its Striking/Thrusting
# attacks upgrade to the Heavy classes (impact carries through plate, §8).
HEAVY_STRENGTH_THRESHOLD = 30

# Armour tier by stops: the equipment catalog's tiers run Light 1-2,
# Medium 3-4, Heavy 5+.
_TIER_BY_STOPS: tuple[tuple[int, str], ...] = (
    (5, "Heavy"),
    (3, "Medium"),
    (1, "Light"),
)

# size_hexes from ST, unless the book states a figure size outright.
# Bands follow the book's own examples: horses and bears (ST into the 30s)
# are 1-2 hex figures, adult dragons (ST 60) fill 4, an elder dragon or a
# titanic queen (ST 100+) fills a megahex.
_SIZE_BANDS: tuple[tuple[int, int], ...] = ((100, 7), (60, 4), (40, 3), (21, 2))


def infer_size_hexes(strength: int, explicit: int | None = None) -> int:
    """Figure size in hexes: the book's statement if given, else the ST band."""
    if explicit:
        return explicit
    for threshold, hexes in _SIZE_BANDS:
        if strength >= threshold:
            return hexes
    return 1


def parse_movement(ma_text: str) -> tuple[int, str | None]:
    """Ground movement in hexes, plus a note for compound values.

    ``"8"`` → ``(8, None)``; ``"8/16 (land/air)"`` → ``(8, "MA 8/16 (land/air)")``
    (the ground value drives the model's ``movement``; the rest is kept as a
    special-ability note). A range like ``"22-28"`` collapses to its midpoint.
    A blank or non-numeric value is movement 0 (rooted, or water-bound).
    """
    text = (ma_text or "").strip()
    numbers = re.findall(r"\d+(?:-\d+)?", text)
    if not numbers:
        return 0, f"MA {text}" if text else None
    first = numbers[0]
    if "-" in first:
        low, high = (int(part) for part in first.split("-"))
        ground = (low + high) // 2
    else:
        ground = int(first)
    compound = len(numbers) > 1 or "(" in text or "/" in text
    return ground, f"MA {text}" if compound else None


def classify_attack(attack_name: str, strength: int) -> str:
    """Map a natural attack's name onto the closest Tarmar weapon class."""
    lowered = attack_name.lower()
    weapon_class = "Striking"
    for keyword, mapped in _ATTACK_CLASS_KEYWORDS:
        if keyword in lowered:
            weapon_class = mapped
            break
    if strength >= HEAVY_STRENGTH_THRESHOLD:
        if weapon_class == "Striking":
            return "Heavy Striking"
        if weapon_class == "Thrusting":
            return "Heavy Thrusting"
    return weapon_class


def _convert_dice(text: str) -> str:
    """TFT dice shorthand → explicit d6 notation (``2d+1`` → ``2d6+1``)."""
    return re.sub(r"\b(\d+)d(?!\d)", r"\1d6", text)


def _split_attacks(attacks_text: str) -> list[str]:
    """Split on ``;`` but never inside parentheses, where it separates
    qualifiers (``"fire breath (1d; costs 1 ST)"`` is one attack)."""
    parts = []
    depth = 0
    current = ""
    for character in attacks_text:
        if character == "(":
            depth += 1
        elif character == ")":
            depth = max(0, depth - 1)
        if character == ";" and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += character
    parts.append(current)
    return parts


def _split_name_and_damage(part: str) -> tuple[str, str]:
    """One attack entry → ``(name, damage)``.

    The name is the text before the first parenthesis; the damage is what
    that parenthesis holds, followed by whatever the entry says after it.
    Only the *first* pair of parentheses is unwrapped, so an entry whose
    damage is followed by more prose keeps both parts and leads with the
    rollable expression:

    * ``"Bite (2d6)"`` → ``("Bite", "2d6")``
    * ``"fire breath (1d6; costs 1 ST)"`` → the qualifier rides along
    * ``"Gore (1d6+2), or 1d6-1 for a tuskless pig"`` → ``("Gore",
      "1d6+2, or 1d6-1 for a tuskless pig")``
    * ``"Seed shot (1d6+1) (see below)"`` → ``("Seed shot",
      "1d6+1 (see below)")``

    The last two used to fail entirely: the old pattern required the
    closing parenthesis to be the entry's final character, so an entry with
    anything after it kept its whole text as the name and an empty damage —
    which is how a pig's gore and an arachne's bare hands ended up with no
    rollable damage at all (#645).

    Args:
        part: One attack entry, already split off and stripped.

    Returns:
        ``(name, damage)``; ``damage`` is empty for an entry with no
        parenthesis at all (e.g. ``"Tentacle grab"``).
    """
    opened = part.find("(")
    if opened == -1:
        return part, ""
    depth = 0
    closed = -1
    for index in range(opened, len(part)):
        if part[index] == "(":
            depth += 1
        elif part[index] == ")":
            depth -= 1
            if depth == 0:
                closed = index
                break
    if closed == -1:
        # The source extract drops a closing parenthesis here and there
        # (the common crocodile's bite). Read to the entry's own final ")"
        # when it has one, which is what the pattern this replaced did, and
        # keep the whole entry as a name when it has none.
        if not part.endswith(")"):
            return part, ""
        closed = len(part) - 1
    name = part[:opened].strip()
    inside = part[opened + 1 : closed].strip()
    trailing = part[closed + 1 :].strip()
    damage = (
        f"{inside}{trailing}"
        if trailing.startswith(",")
        else (f"{inside} {trailing}" if trailing else inside)
    )
    return (name or part), damage.strip()


def parse_attacks(attacks_text: str | None, strength: int) -> list[dict]:
    """Split the condensed attack notation into ``{name, damage, weapon_class}``.

    Entries are separated by top-level ``;``; each is ``Name (damage and
    qualifiers)``, split by :func:`_split_name_and_damage`. An entry with no
    parenthesis keeps its whole text as the name and an empty damage string
    (e.g. ``"Tentacle grab"``).
    """
    if not attacks_text:
        return []
    attacks = []
    for part in _split_attacks(attacks_text):
        part = part.strip().rstrip(".")
        if not part:
            continue
        name, damage = _split_name_and_damage(part)
        attacks.append(
            {
                "name": name,
                "damage": _convert_dice(damage),
                "weapon_class": classify_attack(name, strength),
            }
        )
    return attacks


def tier_for_stops(stops: int) -> str:
    """Armour tier for a stops value — the banding's single home."""
    for minimum, tier in _TIER_BY_STOPS:
        if stops >= minimum:
            return tier
    return "None"


def parse_armour(armor_text: str | None) -> tuple[str, int]:
    """``"Scales stop 3 hits"`` → ``("Medium", 3)``; no armour → ``("None", 0)``."""
    if not armor_text:
        return "None", 0
    stops_found = [int(number) for number in re.findall(r"stops?\s+(\d+)", armor_text)]
    stops = max(stops_found, default=0)
    return tier_for_stops(stops), stops


def derive_pools(scores: Mapping[str, int]) -> tuple[int, int]:
    """Fatigue and body pools via the Character formulas, deterministic.

    Fatigue: the canon's Fatigue sum (on the public pages ``CON + WIS + INT
    + max(DEX, STR)``) plus :func:`fixed_fatigue_roll` — the same derivation
    as ``Character.fatigue`` with the roll pinned to its midpoint. Body:
    ``ceil(fatigue * 2/3)``, exactly ``Character.body``, so the pools land on
    the scale the injury-threshold rules assume.

    Args:
        scores: The six core attributes by field name (``"strength"``);
            other keys are ignored.
    """
    fatigue = character_rules().fatigue_roll.total(
        scores_by_short_name(scores), fixed_fatigue_roll()
    )
    return fatigue, math.ceil(fatigue * 2 / 3)


def translate(entry: dict, description: str = "") -> dict:
    """One source-stats record → a full Beast field dict (deterministic)."""
    category = SECTION_CATEGORY[entry["tft_section"]]
    strength = max(1, entry["st"])
    attributes = {
        "strength": strength,
        "dexterity": max(1, entry["dx"]),
        "intelligence": max(1, entry["iq"]),
        "wisdom": CATEGORY_WISDOM[category],
        "constitution": strength,
        "charisma": CATEGORY_CHARISMA[category],
    }

    movement, movement_note = parse_movement(entry["ma"])
    armour_tier, stops = parse_armour(entry.get("armor"))
    fatigue_pool, body_pool = derive_pools(attributes)

    ability_parts = [
        part
        for part in (entry.get("abilities"), entry.get("armor"), movement_note)
        if part
    ]
    if entry.get("st_range"):
        ability_parts.append(f"Source ST {entry['st_range']}")

    return {
        "name": entry["name"],
        "slug": slugify(entry["name"]),
        "category": category,
        "description": description,
        **attributes,
        "fatigue_pool": fatigue_pool,
        "body_pool": body_pool,
        "armour_tier": armour_tier,
        "stops": stops,
        "attacks": parse_attacks(entry.get("attacks"), strength),
        "special_abilities": "; ".join(ability_parts),
        "size_hexes": infer_size_hexes(strength, entry.get("size_hexes_text")),
        "movement": movement,
        "source_page": entry["page"],
        "is_generated": False,
    }
