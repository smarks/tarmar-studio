"""Which foes a session fight names are people, and what each fights as (#787).

``/fight a bandit`` used to build "bandit — beast — Kick" with 51 Fatigue: the
bestiary holds no people, so a foe the catalog did not know got a stand-in
varied from a catalog beast, and a bandit, a guard or a cultist -- the
commonest first-fight foes -- fought with a beast's attack and no weapon.

A foe named as a person is now generated as one: a rolled character of the
archetype its calling suggests, carrying catalog weapons, through the battle
app's own generation (:func:`battle.generation.generate_character`). This
module only answers "is this a person, and which archetype?"; the fight
builds them. Coordinator's ruling under the standing rule (players are held
to the rules; the Game Master decides the foes), Spencer may overrule: a
foe whose name ends in one of the callings below is a person; everything
else is looked up in the bestiary as before.

The table is by the name's last word (its head noun), so a "guard dog" is
still a dog, and only when every word before it is a plain description:
"a scarred bandit" is a bandit, but an "ant soldier" is the catalog's ant
and a "skeleton warrior" a stand-in beast, as before this change (review of
#787). A name the bestiary holds by its exact name or slug (the catalog's
"Headless Man") stays the catalog's.
"""

from __future__ import annotations

from dataclasses import dataclass

#: A calling, singular and lower-case, to the generated archetype it fights
#: as (one of :data:`battle.generation.ARCHETYPES`). Soldiers and watchmen wear armour; outlaws and zealots fight light;
#: bowmen shoot; casters cast. A plain person with no calling is a brawler.
PERSON_ARCHETYPES: dict[str, str] = {
    **dict.fromkeys(
        (
            "guard",
            "guardsman",
            "soldier",
            "knight",
            "man-at-arms",
            "sentry",
            "sentinel",
            "watchman",
            "warrior",
            "mercenary",
            "sellsword",
            "captain",
            "sergeant",
            "footman",
            "militiaman",
            "gladiator",
            "champion",
            "warden",
            "chief",
            "chieftain",
            "leader",
            "boss",
            "lieutenant",
            "lord",
            "lady",
            "noble",
        ),
        "armoured fighter",
    ),
    **dict.fromkeys(
        (
            "bandit",
            "brigand",
            "thug",
            "cutthroat",
            "rogue",
            "thief",
            "assassin",
            "pirate",
            "raider",
            "highwayman",
            "footpad",
            "ruffian",
            "outlaw",
            "smuggler",
            "duelist",
            "swordsman",
            "cultist",
            "zealot",
            "fanatic",
            "spy",
        ),
        "unarmoured duelist",
    ),
    **dict.fromkeys(
        (
            "archer",
            "bowman",
            "crossbowman",
            "hunter",
            "ranger",
            "scout",
            "poacher",
            "marksman",
        ),
        "archer",
    ),
    **dict.fromkeys(
        (
            "wizard",
            "mage",
            "sorcerer",
            "sorceress",
            "witch",
            "warlock",
            "necromancer",
            "enchanter",
            "enchantress",
            "magician",
            "conjurer",
            "priest",
            "priestess",
            "acolyte",
            "cleric",
            "shaman",
            "druid",
        ),
        "wizard",
    ),
    **dict.fromkeys(("battlemage", "spellsword", "templar"), "fighter-mage"),
    **dict.fromkeys(
        (
            "brawler",
            "pugilist",
            "wrestler",
            "peasant",
            "villager",
            "farmer",
            "drunk",
            "man",
            "woman",
            "person",
            "human",
            "stranger",
        ),
        "brawler",
    ),
}


def _singulars(word: str) -> list[str]:
    """``word`` and the singulars an English plural of it may have."""
    forms = [word]
    if word.endswith("men"):
        forms.append(word[:-3] + "man")  # guardsmen, bowmen, men-at-arms
    if word.endswith("ies"):
        forms.append(word[:-3] + "y")  # spies
    if word.endswith("ves"):
        forms.append(word[:-3] + "f")
    if word.endswith("es"):
        forms.append(word[:-2])  # sorceresses, witches
    if word.endswith("s"):
        forms.append(word[:-1])
    if word == "people":
        forms.append("person")
    return forms


@dataclass(frozen=True)
class PersonFoe:
    """A foe named as a person: what the board calls them and fights them as.

    Attributes:
        label: The name as asked for, its calling made singular and the
            first letter capital ("two scarred bandits" -> "Scarred bandit"),
            which the board numbers per figure as it does a repeated beast.
        archetype: The generated archetype the person is built as.
    """

    label: str
    archetype: str


#: Words that may stand before a calling and leave it a person: plain
#: descriptions of how someone looks, where they serve, or what they are
#: like ("a scarred bandit", "the city guard", "two drunken thugs"). Any
#: other word before the calling ("ant soldier", "skeleton warrior", "lizard
#: men") names a creature, which the bestiary answers as before (review of
#: #787, coordinator's ruling). A calling may also describe another
#: ("bandit captain", "temple guard" is not: "temple" is below).
PLAIN_ADJECTIVES: frozenset[str] = frozenset(
    {
        # Articles and quantities the foe text may keep.
        "the", "a", "an", "some", "few", "several", "many", "other", "more",
        "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
        # How they look.
        "scarred", "old", "young", "elderly", "masked", "hooded", "cloaked",
        "armed", "armoured", "armored", "tall", "short", "burly", "big",
        "small", "fat", "thin", "lean", "bald", "bearded", "one-eyed",
        "grizzled", "ragged", "wounded", "dark", "pale", "black", "white",
        "red", "grey", "gray", "green", "blue", "golden", "silver",
        # What they are like.
        "drunken", "drunk", "angry", "desperate", "starving", "hungry",
        "cruel", "brutal", "savage", "wild", "mad", "crazed", "fanatical",
        "corrupt", "renegade", "veteran", "seasoned", "hardened", "hired",
        "elite", "rival", "enemy", "hostile", "local", "foreign", "lone",
        # Where they serve.
        "city", "town", "village", "castle", "temple", "palace", "royal",
        "imperial", "gate", "watch", "prison", "harbour", "harbor", "hedge",
    }
)  # fmt: skip


#: Words a label leaves out: "the guard" is called Guard on the board.
_LEADING: frozenset[str] = frozenset(
    {"the", "a", "an", "some", "few", "several", "many", "two", "three", "four",
     "five", "six", "seven", "eight", "nine", "ten"}
)  # fmt: skip


def _plain_prefix(words: list[str]) -> bool:
    """Whether every word before the calling is a plain description."""
    return all(
        word.lower().strip(".,;:!?") in PLAIN_ADJECTIVES
        or any(form in PERSON_ARCHETYPES for form in _singulars(word.lower()))
        for word in words
    )


def person_foe(name: str) -> PersonFoe | None:
    """The person a foe named ``name`` is, or None for a creature.

    Read from the whole name first (``man-at-arms``, ``men-at-arms``), then
    from its last word, each tried as written and as a singular. A last-word
    calling makes a person only when every word before it is a plain
    description (:data:`PLAIN_ADJECTIVES`, or another calling): "a scarred
    bandit" is a person, an "ant soldier" or a "skeleton warrior" is not.
    """
    words = name.replace("’", "'").split()
    if not words:
        return None
    whole = "-".join(words).lower()
    for form in (*_singulars(whole), *_whole_plurals(whole)):
        if form in PERSON_ARCHETYPES:
            return PersonFoe(_capitalised(form), PERSON_ARCHETYPES[form])
    if not _plain_prefix(words[:-1]):
        return None
    head = words[-1].strip(".,;:!?")
    described = [word for word in words[:-1] if word.lower() not in _LEADING]
    for form in _singulars(head.lower()):
        if form in PERSON_ARCHETYPES:
            label = " ".join([*described, form])[:100]
            return PersonFoe(_capitalised(label), PERSON_ARCHETYPES[form])
    return None


def _capitalised(text: str) -> str:
    return text[:1].upper() + text[1:]


def _whole_plurals(whole: str) -> list[str]:
    """ "men-at-arms" -> "man-at-arms": a plural inside a hyphenated calling."""
    head, _, rest = whole.partition("-")
    if not rest:
        return []
    return [f"{form}-{rest}" for form in _singulars(head)]
