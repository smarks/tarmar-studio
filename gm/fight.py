"""Taking a GM session's combat to the hex board (issue #345).

``/fight`` turns the fight the story has reached into a real
:class:`battle.models.Battle`: the session's attached characters on one team
(seated to the player, who decides their moves), the opposing side on the
other (the AI plays them). The battle is built by the battle app's own
setup service, :func:`battle.services.create_battle`, so the roster, the
snapshots and the arena are exactly what the setup page would have made.

Who the party faces is the Game Master's to decide (#762, #841). In a Game
Master's session he may name the foes outright (``/fight two gray wolves, a
dire wolf``), which :func:`parse_foe_text` reads without the model. In a
player's session what the player types is a request: the model, as their
Game Master, sets the foes with a ``set_foes`` answer held to the bestiary
catalog, and a person to the callings :mod:`gm.foe_people` knows (#894)
(:func:`request_set_foes`), and the opening line says so. A Game
Master who names no foes has the model asked -- through :func:`gm.services.extract_structured_json`, this project's
one schema-constrained call, which retries with the schema written into the
prompt when the loaded runner will not take it as ``format`` (issue #368)
-- for the opposing side as it stands in the story, one entry per kind of
foe. Either way each request is matched against the bestiary catalog by
slug or name (:func:`find_catalog_beast`); a creature the catalog does not
know gets a stand-in varied from a catalog beast of the requested category
and difficulty (:func:`stand_in_beast`, the bestiary's own generation
tooling), renamed to what was asked for.

Once the battle exists the session records it (``GMSession.battle``) and
two entries go into the history: a hidden ``kind: "battle"`` block telling
the model the fight is on the board and who stands where, and a visible GM
line so the transcript shows where the fight began. The board's rolls reach
the model later, turn by turn, through :mod:`gm.battle_report`.

A foe named as a person -- a bandit, a guard, a cultist -- is not looked up
in the bestiary, which holds no people: it is generated as a person of the
archetype its calling suggests (:mod:`gm.foe_people`, #787), rolled like any
generated fighter, and named on the board by what was asked for. A creature
the catalog lacks still gets a stand-in beast, and the board says so.
"""

from __future__ import annotations

import json
import logging
import random
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Length
from django.utils.text import slugify

from battle import control, profiles
from battle.access import is_dm
from battle.generation import ARCHETYPES
from battle.models import SEED_RANGE, Battle
from battle.persistence import dump_state
from battle.services import (
    MAX_COMBATANTS,
    _numbered_variant,
    create_battle,
    rename_combatant,
)
from bestiary.generation import (
    DIFFICULTY_BANDS,
    catalog_queryset,
    generate_variant,
    pick_base,
)
from bestiary.models import CATEGORY_CHOICES, Beast
from characters.constants import too_long
from characters.models import Character
from common.helpers import column_length, is_owner_or_manager

from .battle_report import battle_entry, pending_battle_entries
from .character_schema import SCHEMA_IN_PROMPT_PREAMBLE, parse_extraction_reply
from .foe_people import FOE_NAME_MAX_LENGTH, PERSON_ARCHETYPES, person_foe
from .models import GMSession
from .retrieval import ChatMessage
from .services import extract_structured_json, messages_for_model

logger = logging.getLogger(__name__)

#: The two sides of a session fight, as ``BattleCombatant.team`` tags.
PARTY_TEAM = "Party"
FOE_TEAM = "Foes"

#: How many of one kind of foe a single request may ask for. Also the
#: schema's bound, so the model cannot answer "a thousand rats".
MAX_FOES_PER_KIND = 10

#: The band a stand-in is drawn from when the request names none.
DEFAULT_STAND_IN_DIFFICULTY = "moderate"

# JSON schema the foe answer is constrained to -- sent as ``format`` on
# ``/api/chat``, or written into the ask when the runner refuses that
# (issue #368). Every field is required so a missing one is a schema
# failure at the model, not a KeyError here; the empty string is the
# explicit "none" for slug, category and difficulty.
FOE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "foes": {
            "type": "array",
            "maxItems": MAX_FOES_PER_KIND,
            "items": {
                "type": "object",
                "properties": {
                    # The model is told the limit a stand-in's name is
                    # refused past (#938), rather than finding out after.
                    "name": {"type": "string", "maxLength": FOE_NAME_MAX_LENGTH},
                    "beast_slug": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": [key for key, _label in CATEGORY_CHOICES] + [""],
                    },
                    "difficulty": {
                        "type": "string",
                        "enum": [*DIFFICULTY_BANDS, ""],
                    },
                    "count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_FOES_PER_KIND,
                    },
                },
                "required": ["name", "beast_slug", "category", "difficulty", "count"],
            },
        }
    },
    "required": ["foes"],
}

#: The action a player's session's foes are chosen through (issue #841): the
#: Game Master's answer to the player's request, held to the catalog.
SET_FOES_ACTION = "set_foes"

_FOE_COUNT_SCHEMA: dict = {
    "type": "integer",
    "minimum": 1,
    "maximum": MAX_FOES_PER_KIND,
}

# JSON schema of that answer: one ``set_foes`` action naming catalog beasts
# by slug, and people by calling (issue #894). Sent as ``format``, or
# written into the ask when the runner refuses that (issue #368).
SET_FOES_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": [SET_FOES_ACTION]},
        "foes": {
            "type": "array",
            "maxItems": MAX_FOES_PER_KIND,
            "items": {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {
                            "beast_slug": {"type": "string"},
                            "count": _FOE_COUNT_SCHEMA,
                        },
                        "required": ["beast_slug", "count"],
                    },
                    {
                        "type": "object",
                        "properties": {
                            "calling": {"type": "string"},
                            "count": _FOE_COUNT_SCHEMA,
                        },
                        "required": ["calling", "count"],
                    },
                ]
            },
        },
    },
    "required": ["action", "foes"],
}

SET_FOES_PREAMBLE = (
    "A player is taking the fight to the hex board. The foes are yours to "
    "set as their Game Master, not theirs: what the player typed is a "
    "request, and you decide the opposing side from the story as it stands "
    "right now -- every creature or person the party is about to fight, and "
    "nothing that is not hostile to them. Where the request fits the story, "
    "follow it; where it does not, the story wins. "
)

FOE_REQUEST_PREAMBLE = (
    "The player is taking the fight to the hex board. From the story as it "
    "stands right now, list the opposing side: every creature or person the "
    "party is about to fight, and nothing that is not hostile to them. "
)

# The foe text is the player's or the model's words, so each pattern below
# matches in linear time. Wherever two repeated parts could take the same
# characters (a run of spaces, say), a part only one of them can take sits
# between them: the name starts or ends on a non-space (``\S``), and the count
# is set off by an "x". There is then one way to divide the text, never a
# quadratic search over how many spaces each side takes.
_COUNT_PREFIX = re.compile(
    r"^(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten)\s+(\S.*)$",
    re.IGNORECASE,
)
_COUNT_SUFFIX = re.compile(r"^(.*\S)\s*[x×]\s*(\d+)$", re.IGNORECASE)
_NUMBER_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
# The whitespace around a separator is not part of it: every entry is stripped
# after the split. "and" still needs whitespace on both sides, so "sand" and
# "Andrew" stay whole; the lookarounds check that without consuming it.
_FOE_SEPARATOR = re.compile(r"[,;]|(?<=\s)and(?=\s)", re.IGNORECASE)


class FightError(ValueError):
    """A fight could not be started; the message is for the player."""


@dataclass(frozen=True)
class FoeRequest:
    """One kind of foe asked for: by the player's text, or by the model.

    Attributes:
        name: The foe as asked for.
        count: How many.
        beast_slug: The catalog beast the model named, or "".
        category: The bestiary category a stand-in is drawn from, or "".
        difficulty: The difficulty band a stand-in is drawn from, or "".
        calling: The calling a ``set_foes`` answer named a person by
            (issue #894), checked against :func:`gm.foe_people.person_foe`;
            "" for any other request.
    """

    name: str
    count: int
    beast_slug: str = ""
    category: str = ""
    difficulty: str = ""
    calling: str = ""


@dataclass(frozen=True)
class ResolvedFoe:
    """A request matched to (or stood in by) a ``Beast`` row, or a person.

    A person (#787) has no ``Beast``: ``beast`` is ``None`` and
    ``archetype`` names what the generated fighter is built as.
    """

    beast: Beast | None
    count: int
    requested_name: str
    # The catalog beast a stand-in was varied from; ``None`` for a catalog
    # match. The hidden block names it so the model knows what "Shadow
    # Hound" actually fights like.
    generated_from: Beast | None = None
    #: The generated archetype a person fights as; "" for a beast.
    archetype: str = ""
    #: What a person is called on the board ("Scarred bandit"); "" for a beast.
    label: str = ""

    @property
    def name(self) -> str:
        """What the foe is called: the beast's name, or the person's label."""
        if self.beast is not None:
            return self.beast.name
        return self.label


@dataclass(frozen=True)
class FightStart:
    """What ``start_fight`` made, for the endpoint's answer."""

    battle: Battle
    party: tuple[Character, ...]
    foes: tuple[ResolvedFoe, ...]
    # Foes asked for beyond what the field can hold, left out.
    dropped: int
    #: Whether the Game Master set the foes for a player's request (#841).
    chosen_by_game_master: bool = False
    #: What the player asked for, when they did.
    requested: str = ""


def _count_of(token: str) -> int:
    lowered = token.lower()
    if lowered in _NUMBER_WORDS:
        return _NUMBER_WORDS[lowered]
    return int(lowered)


def parse_foe_text(text: str) -> list[FoeRequest]:
    """Read ``"two gray wolves, a dire wolf, rat x3"`` into requests.

    Entries split on commas, semicolons and " and "; each may lead with a
    number (digits or one..ten, "a"/"an") or trail with "x N". Counts are
    clamped to :data:`MAX_FOES_PER_KIND`; an entry with no name is skipped.

    Raises:
        FightError: when nothing usable was named.
    """
    requests: list[FoeRequest] = []
    for raw_part in _FOE_SEPARATOR.split(text.strip()):
        part = raw_part.strip()
        if not part:
            continue
        count = 1
        prefix = _COUNT_PREFIX.match(part)
        suffix = _COUNT_SUFFIX.match(part)
        if prefix:
            count, part = _count_of(prefix.group(1)), prefix.group(2)
        elif suffix:
            part, count = suffix.group(1), int(suffix.group(2))
        name = part.strip(" .")
        if not name:
            continue
        requests.append(
            FoeRequest(name=name, count=max(1, min(count, MAX_FOES_PER_KIND)))
        )
    if not requests:
        raise FightError("Name at least one foe, or leave it blank to ask the GM")
    return requests


def _catalog_lines() -> str:
    """The catalog as the model sees it: one ``slug: name`` line per beast."""
    labels = dict(CATEGORY_CHOICES)
    lines: list[str] = []
    current_category = None
    for beast in catalog_queryset().order_by("category", "name"):
        if beast.category != current_category:
            current_category = beast.category
            lines.append(f"[{labels.get(current_category, current_category)}]")
        lines.append(f"{beast.slug}: {beast.name}")
    return "\n".join(lines)


def foe_request_message(
    max_foes: int, *, schema_in_prompt: bool = False
) -> ChatMessage:
    """The one-off instruction asking the model for the opposing side.

    Not filed into the session: it is a question for this call only, and
    persisting it would put the whole catalog in the prompt prefix.

    Args:
        max_foes: How many figures the field still has room for.
        schema_in_prompt: Spell :data:`FOE_SCHEMA` out in the ask, for a
            runner that will not take it as ``format`` (issue #368). The
            preamble is :data:`gm.character_schema.SCHEMA_IN_PROMPT_PREAMBLE`
            -- the wording ``/character`` and the ledger already use -- so
            every fallback in this project asks for JSON the same way.
    """
    content = (
        FOE_REQUEST_PREAMBLE
        + f"Up to {max_foes} figures in all. For each kind of foe give its "
        "name, the bestiary slug from the catalog below if an entry fits "
        "(otherwise an empty slug plus a category and a difficulty band "
        "so a stand-in can be generated), and how many of them there are.\n\n"
        f"Bestiary catalog (slug: name):\n{_catalog_lines()}"
    )
    if schema_in_prompt:
        content += (
            f"\n\n{SCHEMA_IN_PROMPT_PREAMBLE}\n{json.dumps(FOE_SCHEMA, indent=1)}"
        )
    return {"role": "user", "content": content}


def request_foes_from_model(
    history: Sequence[ChatMessage], max_foes: int, *, stop_at: float | None = None
) -> list[FoeRequest]:
    """Ask the model who the party faces, as schema-constrained JSON.

    The call, and its retry for a runner that refuses ``format``, belong to
    :func:`gm.services.extract_structured_json` -- the same seam
    ``/character`` (issue #344) and the ledger's per-turn intent read (issue
    #356) go through. Before issue #368 this asked Ollama directly and caught
    no HTTP error at all, so the production model's runner answering every
    ``format`` with 501 turned a blank foes box into a 500 for the player.

    Args:
        history: The session so far, as the model should see it.
        max_foes: How many figures the field still has room for.
        stop_at: A ``time.monotonic()`` past which the call does not hold the
            model (issue #936).

    Returns:
        One request per kind of foe, in the order the model listed them.

    Raises:
        FightError: when the model's answer is not a usable foe list.
        httpx.HTTPStatusError: on any Ollama failure other than the runner
            saying it cannot constrain output, which is not the player's
            problem to read about.
        StreamDeadlinePassed: ``stop_at`` passed.
    """
    answer = extract_structured_json(
        lambda schema_in_prompt=False: messages_for_model(
            [*history, foe_request_message(max_foes, schema_in_prompt=schema_in_prompt)]
        ),
        FOE_SCHEMA,
        stop_at=stop_at,
    )
    try:
        data = parse_extraction_reply(answer)
        entries = data["foes"]
        requests = [
            FoeRequest(
                name=str(entry["name"]).strip(),
                count=max(1, min(int(entry["count"]), MAX_FOES_PER_KIND)),
                beast_slug=str(entry.get("beast_slug") or "").strip(),
                category=str(entry.get("category") or "").strip(),
                difficulty=str(entry.get("difficulty") or "").strip(),
            )
            for entry in entries
        ]
    except (ValueError, TypeError, KeyError) as error:
        logger.warning("GM foe answer was not a foe list: %r", answer)
        raise FightError(
            "The GM could not say who you face — try naming the foes"
        ) from error
    requests = [request for request in requests if request.name]
    if not requests:
        raise FightError("The GM found nobody to fight — try naming the foes")
    return requests


def set_foes_message(
    max_foes: int, requested: str, *, schema_in_prompt: bool = False
) -> ChatMessage:
    """The one-off ask for a player's session's foes (issue #841).

    Like :func:`foe_request_message`, never filed into the session.

    Args:
        max_foes: How many figures the field still has room for.
        requested: What the player typed after ``/fight``, or ``""``.
        schema_in_prompt: Spell :data:`SET_FOES_SCHEMA` out in the ask.
    """
    asked = (
        f'The player asked for: "{requested.strip()}".'
        if requested.strip()
        else "The player named no foes."
    )
    content = (
        SET_FOES_PREAMBLE
        + asked
        + f" Up to {max_foes} figures in all, with how many of each: a "
        "creature only from the bestiary catalog below, by slug; a person "
        "(a bandit, a guard, a cultist) by their calling, one of the "
        "callings listed below, since the bestiary holds no people. Answer "
        f'with only {{"action": "{SET_FOES_ACTION}", "foes": [{{"beast_slug": '
        '"gray-wolf", "count": 2}, {"calling": "bandit", "count": 1}]}.\n\n'
        f"Callings a person may be named by: {_calling_list()}.\n\n"
        f"Bestiary catalog (slug: name):\n{_catalog_lines()}"
    )
    if schema_in_prompt:
        content += (
            f"\n\n{SCHEMA_IN_PROMPT_PREAMBLE}\n{json.dumps(SET_FOES_SCHEMA, indent=1)}"
        )
    return {"role": "user", "content": content}


def _calling_list() -> str:
    """Every calling a ``set_foes`` person entry may name, for the ask."""
    return ", ".join(sorted(PERSON_ARCHETYPES))


def read_set_foes(answer: str) -> tuple[list[FoeRequest], list[str]]:
    """The foes a ``set_foes`` answer sets, or why it sets none (issue #841).

    Held to the catalog: every slug must be a catalog beast's, so a player's
    session never fights something the bestiary does not hold, and nothing
    is guessed from a name. A person is named by calling instead
    (``{"calling": "bandit", "count": 2}``, issue #894), held to the callings
    :func:`gm.foe_people.person_foe` knows, so a player's session can face
    a person as a Game Master's can. An answer with any unknown slug or
    calling is no answer.

    Returns:
        ``(requests, errors)``; the requests are empty when there are errors.
    """
    try:
        document = parse_extraction_reply(answer)
    except ValueError:
        return [], ["the answer is not JSON"]
    if not isinstance(document, dict) or document.get("action") != SET_FOES_ACTION:
        return [], [f'the answer is one {{"action": "{SET_FOES_ACTION}", ...}}']
    entries = document.get("foes")
    if not isinstance(entries, list) or not entries:
        return [], ["foes must be a list naming at least one beast"]
    known = set(catalog_queryset().values_list("slug", flat=True))
    requests: list[FoeRequest] = []
    errors: list[str] = []
    for index, entry in enumerate(entries, start=1):
        slug = entry.get("beast_slug") if isinstance(entry, dict) else None
        count = entry.get("count") if isinstance(entry, dict) else None
        calling = entry.get("calling") if isinstance(entry, dict) else None
        if calling is not None and slug is None:
            request, error = _person_request(index, calling, count)
            if error:
                errors.append(error)
            elif request is not None:
                requests.append(request)
            continue
        if not isinstance(slug, str) or slug.strip() not in known:
            errors.append(
                f"foe {index}: {slug!r} is not a slug from the bestiary catalog"
            )
            continue
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            errors.append(f"foe {index}: count must be a whole number from 1")
            continue
        requests.append(
            FoeRequest(
                name=slug.strip(),
                count=min(count, MAX_FOES_PER_KIND),
                beast_slug=slug.strip(),
            )
        )
    return ([] if errors else requests), errors


def _person_request(
    index: int, calling: object, count: object
) -> tuple[FoeRequest | None, str]:
    """One ``set_foes`` person entry as a request, or why it is not one (#894)."""
    person = person_foe(calling.strip()) if isinstance(calling, str) else None
    if person is None:
        return None, (
            f"foe {index}: {calling!r} is not a calling a person may be named by"
        )
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        return None, f"foe {index}: count must be a whole number from 1"
    return (
        FoeRequest(
            name=str(calling).strip(),
            count=min(count, MAX_FOES_PER_KIND),
            calling=str(calling).strip(),
        ),
        "",
    )


def request_set_foes(
    history: Sequence[ChatMessage],
    max_foes: int,
    requested: str,
    *,
    stop_at: float | None = None,
) -> list[FoeRequest]:
    """Have the Game Master set a player's session's foes (issue #841).

    Ruled on #841 (coordinator's ruling under the standing rule, from #762;
    Spencer may overrule): a session fight's foes belong to the Game Master,
    so a player's ``/fight`` text is a request the model turns into foes, not
    a list the server builds. Asked once more with the errors when the answer
    names a beast the catalog does not hold.

    Args:
        history: The session so far, as the model should see it.
        max_foes: How many figures the field still has room for.
        requested: What the player typed after ``/fight``, or ``""``.
        stop_at: One ``time.monotonic()`` deadline for every call this makes,
            the second ask included (issue #936).

    Raises:
        FightError: The model set no foes the catalog holds, twice.
        httpx.HTTPStatusError: As :func:`request_foes_from_model`.
        StreamDeadlinePassed: ``stop_at`` passed.
    """
    errors: list[str] = []
    for _attempt in range(2):
        told = list(errors)

        def messages(schema_in_prompt: bool = False, told: list[str] = told):
            asked = [
                *history,
                set_foes_message(
                    max_foes, requested, schema_in_prompt=schema_in_prompt
                ),
            ]
            if told:
                asked.append(
                    {
                        "role": "user",
                        "content": "That answer could not be used: "
                        + "; ".join(told)
                        + ". Answer again, with slugs from the catalog and "
                        "callings from the list only.",
                    }
                )
            return messages_for_model(asked)

        answer = extract_structured_json(messages, SET_FOES_SCHEMA, stop_at=stop_at)
        requests, errors = read_set_foes(answer)
        if requests:
            return requests
        logger.warning("GM set_foes answer was not usable: %s; %r", errors, answer)
    raise FightError(
        "The Game Master could not find foes for that in the bestiary — try "
        "asking again"
    )


def foe_requests_for(
    session: GMSession, foes_text: str, room: int, *, stop_at: float | None = None
) -> tuple[list[FoeRequest], bool]:
    """Who the party faces, decided as the account allows (issue #841).

    A Game Master's ``/fight <foes>`` builds exactly what he named, and his
    blank one asks the model; a player's, named or not, is the Game Master's
    to set (:func:`request_set_foes`). Keyed on the account, as every Game
    Master power is (#352).

    Args:
        session: The session the fight starts in.
        foes_text: What followed ``/fight``, or ``""``.
        room: How many figures the field still has room for.
        stop_at: A ``time.monotonic()`` past which no foe call holds the
            model (issue #936).

    Returns:
        ``(requests, chosen_by_game_master)``: the second is whether the
        model set them for a player, which the player is told.
    """
    if is_dm(session.user):
        if foes_text.strip():
            return parse_foe_text(foes_text), False
        return request_foes_from_model(session.history, room, stop_at=stop_at), False
    return (
        request_set_foes(session.history, room, foes_text, stop_at=stop_at),
        True,
    )


def name_candidates(name: str) -> list[str]:
    """``name`` lower-cased, then its likely singulars ("wolves" -> "wolf")."""
    lowered = " ".join(name.lower().split())
    forms = [lowered]
    if lowered.endswith("ies"):
        forms.append(lowered[:-3] + "y")
    if lowered.endswith("ves"):
        forms.extend((lowered[:-3] + "f", lowered[:-3] + "fe"))
    if lowered.endswith("es"):
        forms.append(lowered[:-2])
    if lowered.endswith("s"):
        forms.append(lowered[:-1])
    return list(dict.fromkeys(form for form in forms if form))


def _catalog_beast_named(request: FoeRequest) -> Beast | None:
    """The catalog beast ``request`` names by slug or by its exact name.

    The first two of :func:`find_catalog_beast`'s steps: what a request names
    outright, before any looser "contains" match.
    """
    catalog = catalog_queryset()
    if request.beast_slug:
        beast = catalog.filter(slug=request.beast_slug).first()
        if beast is not None:
            return beast
    for form in name_candidates(request.name):
        beast = catalog.filter(Q(name__iexact=form) | Q(slug=slugify(form))).first()
        if beast is not None:
            return beast
    return None


def find_catalog_beast(request: FoeRequest) -> Beast | None:
    """The catalog beast ``request`` names, or ``None``.

    Slug first (the model answers in slugs), then an exact name or slug
    match on the name and its singulars, then the shortest catalog name
    containing it -- "wolf" finds Gray Wolf before Werewolf.
    """
    beast = _catalog_beast_named(request)
    if beast is not None:
        return beast
    catalog = catalog_queryset()
    forms = name_candidates(request.name)
    for form in forms:
        beast = (
            catalog.filter(name__icontains=form)
            .order_by(Length("name"), "name")
            .first()
        )
        if beast is not None:
            return beast
    return None


# Keeps the stand-ins' draws apart from the other draws made from the same
# battle seed (the roster's, the board's).
_STAND_IN_SEED_SALT = "gm-fight-stand-ins"


def stand_in_rng(seed: int) -> random.Random:
    """The generator a fight's stand-in beasts are drawn from (issue #898).

    Derived from the battle's seed, as ``battle.generation.stat_roller_for_slot``
    derives each fighter's, so a fight rebuilt from its seed picks the same
    base beasts and varies them the same way.

    Args:
        seed: The battle's seed.

    Returns:
        A ``random.Random`` seeded from ``seed`` alone.
    """
    return random.Random(f"{_STAND_IN_SEED_SALT}:{seed}")


def stand_in_beast(
    request: FoeRequest, *, rng: random.Random | None = None
) -> tuple[Beast, Beast]:
    """A generated variant standing in for a creature the catalog lacks.

    Drawn from the requested category and difficulty, falling back to the
    difficulty alone and then to the whole catalog, then varied through
    :func:`bestiary.generation.generate_variant` and renamed to what was
    asked for. Returns ``(variant, base)``.

    Args:
        request: The foe asked for.
        rng: What the base beast and its variation are drawn from; a fight
            passes :func:`stand_in_rng` of its seed (issue #898). None draws
            unseeded, as the bestiary's own generator does.

    Raises:
        FightError: when the catalog is empty, or the name asked for is longer
            than :data:`gm.foe_people.FOE_NAME_MAX_LENGTH`.
    """
    difficulty = request.difficulty or DEFAULT_STAND_IN_DIFFICULTY
    attempts = (
        {"category": request.category or None, "difficulty": difficulty},
        {"category": None, "difficulty": difficulty},
        {"category": None, "difficulty": None},
    )
    base = None
    for attempt in attempts:
        try:
            base = pick_base(**attempt, rng=rng)
            break
        except Beast.DoesNotExist:
            continue
    if base is None:
        raise FightError("The bestiary is empty — load the catalog before fighting")
    # The stand-in keeps what was asked for as its own name, so a name past
    # the bestiary's column is refused, not cut to fit (#938).
    name = request.name.strip()
    refusal = too_long("A foe's name", name, FOE_NAME_MAX_LENGTH)
    if refusal:
        raise FightError(refusal)
    variant = generate_variant(base, rng=rng)
    variant.name = name
    variant.save(update_fields=["name"])
    return variant, base


def resolve_foes(
    requests: Sequence[FoeRequest], *, rng: random.Random | None = None
) -> list[ResolvedFoe]:
    """Match every request to a ``Beast`` or a person, standing in as needed.

    A request the catalog names outright (by slug or exact name) is that
    beast. Otherwise a request naming a person (:func:`person_foe`) is
    a person of that archetype (#787), before any looser catalog match could
    turn "guard" into the Large Guard Dog. The rest are matched as before,
    with a stand-in beast for a creature the catalog lacks.

    Args:
        requests: The foes asked for.
        rng: What stand-ins are drawn from, shared across the requests in
            order (see :func:`stand_in_beast`).
    """
    resolved: list[ResolvedFoe] = []
    for request in requests:
        if request.calling:
            # Named a person outright by a set_foes answer (#894): checked
            # against the callings when it was read, never a catalog beast.
            person = person_foe(request.calling)
            if person is None:
                raise FightError(f"{request.calling!r} is not a calling")
            resolved.append(
                ResolvedFoe(
                    None,
                    request.count,
                    request.name,
                    archetype=person.archetype,
                    label=person.label,
                )
            )
            continue
        person = person_foe(request.name)
        if person is not None and _catalog_beast_named(request) is None:
            resolved.append(
                ResolvedFoe(
                    None,
                    request.count,
                    request.name,
                    archetype=person.archetype,
                    label=person.label,
                )
            )
            continue
        beast = find_catalog_beast(request)
        if beast is not None:
            resolved.append(ResolvedFoe(beast, request.count, request.name))
            continue
        variant, base = stand_in_beast(request, rng=rng)
        resolved.append(ResolvedFoe(variant, request.count, request.name, base))
    return resolved


def _fit_to_field(
    foes: Sequence[ResolvedFoe], room: int
) -> tuple[list[ResolvedFoe], int]:
    """Trim counts so the foes fit ``room`` slots; returns (kept, dropped)."""
    kept: list[ResolvedFoe] = []
    dropped = 0
    for foe in foes:
        allowed = max(0, min(foe.count, room))
        room -= allowed
        dropped += foe.count - allowed
        if allowed:
            kept.append(replace(foe, count=allowed))
    return kept, dropped


def _foe_summary(foe: ResolvedFoe) -> str:
    if foe.archetype:
        note = f" (a person: {foe.archetype})"
    elif foe.generated_from is not None:
        note = f" (a stand-in varied from {foe.generated_from.name})"
    else:
        note = ""
    count = f"{foe.count} × " if foe.count != 1 else ""
    return f"{count}{foe.name}{note}"


def opening_block(
    battle: Battle, party: Sequence[Character], foes: Sequence[ResolvedFoe]
) -> str:
    """The hidden block that tells the model the fight is on the board."""
    party_names = ", ".join(character.name for character in party)
    foe_lines = "; ".join(_foe_summary(foe) for foe in foes)
    return (
        f"Battle — the fight is now being resolved on the hex board "
        f"(battle '{battle.name}', seed {battle.seed}).\n"
        f"{PARTY_TEAM} (the player decides their moves): {party_names}.\n"
        f"{FOE_TEAM} (the AI plays them): {foe_lines}.\n"
        "Each turn's rolls and outcomes will be filed here as 'Battle log' "
        "entries as the board plays out. Narrate from those entries and do not "
        "invent attacks, damage or deaths of your own while the fight is on "
        "the board; the board's results are the truth of the fight."
    )


def opening_line(
    party: Sequence[Character],
    foes: Sequence[ResolvedFoe],
    *,
    requested: str | None = None,
) -> str:
    """The GM line the transcript shows where the fight began.

    Args:
        party: Who fights for the player.
        foes: Who they face.
        requested: For a player's fight whose foes the Game Master set
            (#841), what the player asked for (``""`` when nothing), so the
            player sees their request was turned into the foes named.
    """
    party_names = ", ".join(character.name for character in party)
    foe_names = ", ".join(_foe_summary(foe) for foe in foes)
    chosen = ""
    if requested is not None:
        asked = f'You asked for "{requested.strip()}"; ' if requested.strip() else ""
        chosen = f"{asked}the Game Master set the foes. "
    return (
        f"{chosen}The fight moves to the board: {party_names} against "
        f"{foe_names}. Play it out in the Battle tab — I will narrate from the "
        "rolls."
    )


def _label_foes_on_the_board(battle: Battle, foes: Sequence[ResolvedFoe]) -> None:
    """Name the people as asked for, and mark the stand-ins, on the board (#787).

    A generated fighter comes with a generated name ("Kestra Duskmere"); a
    person asked for as "a bandit" is called Bandit on the board, numbered
    like a repeated beast ("Bandit (2)"), through :func:`rename_combatant`,
    so a restart keeps it. A stand-in beast says it is one on its roster
    card ("stand-in beast (from Gray Wolf)"), in the battle's state and in
    the turn-0 copy a restart rewinds to.
    """
    slots = [foe.label for foe in foes if foe.archetype for _ in range(foe.count)]
    generated = battle.combatants.filter(
        beast__isnull=True, character__isnull=True, is_open_seat=False
    ).order_by("pk")
    taken = set(battle.combatants.values_list("display_name", flat=True))
    for combatant, label in zip(generated, slots, strict=True):
        taken.discard(combatant.display_name)
        wanted = _numbered_variant(label, taken)
        taken.add(wanted)
        rename_combatant(battle.pk, combatant.pk, wanted)
    # The renames went through the service, so the caller's copy is behind.
    battle.refresh_from_db()

    stand_ins = {
        foe.beast.pk: foe.generated_from.name
        for foe in foes
        if foe.beast is not None and foe.generated_from is not None
    }
    if not stand_ins:
        return
    marked = {
        combatant_id: f"stand-in beast (from {stand_ins[beast_id]})"
        for combatant_id, beast_id in battle.combatants.filter(
            beast_id__in=stand_ins
        ).values_list("pk", "beast_id")
    }
    profile = profiles.for_battle(battle)
    for field in ("state_json", "initial_state_json"):
        state = profile.load(getattr(battle, field))
        for figure in state.combatants:
            if figure.combatant_id in marked:
                figure.archetype = marked[figure.combatant_id]
        setattr(battle, field, dump_state(state))
    battle.save(update_fields=["state_json", "initial_state_json", "updated_at"])


def battle_name_for(party: Sequence[Character], foes: Sequence[ResolvedFoe]) -> str:
    """The fight named from its sides: ``"Aldwin vs 2 × Gray Wolf"`` (#831).

    It used to be ``"<session name>: fight"``, and a session is named after
    its first chat line, so fights were called "I walk into the tavern and
    look for work.: fight" in the Battle pane, the battle list and every
    battle log entry. Cut to the name column on a word, with an ellipsis.
    """
    column_width = column_length(Battle, "name")
    party_names = ", ".join(character.name for character in party)
    foe_names = ", ".join(
        # foe.name: a beast's name, or a person's label (#787).
        f"{foe.count} × {foe.name}" if foe.count != 1 else foe.name
        for foe in foes
    )
    name = f"{party_names} vs {foe_names}"
    if len(name) <= column_width:
        return name
    head, _space, _tail = name[: column_width - 1].rpartition(" ")
    return f"{(head or name[: column_width - 1]).rstrip(' ,')}…"


def fight_in_progress(session: GMSession) -> bool:
    """True while the session's battle is still being fought."""
    battle = session.battle
    return battle is not None and battle.status not in (
        Battle.STATUS_FINISHED,
        Battle.STATUS_STALEMATE,
    )


def start_fight(
    session: GMSession,
    *,
    foes_text: str = "",
    seed: int | None = None,
    stop_at: float | None = None,
) -> FightStart:
    """Put the session's fight on the hex board.

    The attached living characters make the party; ``foes_text`` names the
    foes, or -- empty -- the model is asked. The battle is created through
    :func:`battle.services.create_battle` with the party and the foes on
    their two teams, then each of the party's combatants is seated to the
    character's owner (issue #853): ``player`` set and control ``manual``,
    exactly what a lobby claim writes, so the arena asks each player for
    their own characters' moves and the AI plays the foes. In a player's
    session every character is the player's own; in a Game Master's, his
    players play theirs unless he takes an absent player's seat. (``claim_seat`` itself cannot do this:
    it seats one character per person into a lobby seat, and a party is
    several characters in a battle that never had a lobby.)

    Anything the previous fight still owed the model -- unfiled turns, its
    closing summary -- is filed first, so a new fight never buries an old
    one's ending.

    The session's row is locked before anything is written, and "is a fight
    already under way?" is asked again under that lock, so two overlapping
    requests cannot both build a battle (issue #671). The foes are resolved
    inside the same transaction, so the stand-in Beast rows a refused fight
    generated are rolled back with it (issue #675).

    ``stop_at``, a ``time.monotonic()``, bounds every model call that names
    the foes, together (issue #936).

    Raises:
        FightError: no living characters, a fight already under way, no room
            on the field, or foes that could not be named.
        StreamDeadlinePassed: the model was still naming the foes at
            ``stop_at``; nothing was started.
    """
    if fight_in_progress(session):
        raise FightError("A fight is already under way — finish it on the board first")
    # Only a complete character can fight (issue #571): one still missing
    # attributes or its Fatigue roll has no pools to put on the board.
    living = [
        character
        for character in session.attached_characters().playable()
        if character.is_alive
    ]
    # The fight writes its wounds and deaths to each character's record
    # (#750), so it seats only characters the session's owner may still
    # change: their own, or anybody's while the account is a Game Master.
    # A Game Master may attach another player's character; an account that
    # has since lost the role no longer fights with it (issue #837).
    owner = type(session.user).objects.get(pk=session.user_id)
    party = [
        character for character in living if is_owner_or_manager(owner, character.user)
    ]
    if not party and living:
        raise FightError(
            "The characters in this session belong to other players; only a "
            "Game Master fights with another player's character"
        )
    if not party:
        raise FightError("Attach a living character before starting a fight")
    room = MAX_COMBATANTS - len(party)
    if room < 1:
        raise FightError(
            f"The field holds {MAX_COMBATANTS} figures and the party fills it"
        )
    # Outside the transaction: asking the model can take half a minute, and
    # nothing should hold the session's row for that.
    requests, chosen_by_game_master = foe_requests_for(
        session, foes_text, room, stop_at=stop_at
    )
    with transaction.atomic():
        # The session is locked before anything is built, and the "already
        # fighting" question asked again under the lock. The check at the top
        # reads an unlocked in-memory instance, so two overlapping /fight
        # calls -- a double-click on the control, a retry after a slow first
        # request -- both passed it, both wrote a whole Battle with its
        # combatants and arena, and the second save overwrote session.battle.
        # The first was left in the database with the party seated to the
        # player, reachable from the battle list, with nothing pointing at
        # it: its turns would never be filed and the GM would never narrate
        # it, and the session carried two opening blocks telling the model
        # two fights were on the board (issue #671).
        locked = GMSession.objects.select_for_update().get(pk=session.pk)
        if fight_in_progress(locked):
            raise FightError(
                "A fight is already under way — finish it on the board first"
            )
        # Resolving inside the transaction too: a request the catalog cannot
        # match saves a generated Beast row, and a fight that is then trimmed
        # away, found to have no foes, or refused left those rows behind with
        # nothing referencing them -- and every later stand-in from the same
        # base numbered as though they counted (issue #675).
        # Seeded from the battle's own seed, chosen here when the caller gave
        # none, so the seed the battle records rebuilds its stand-ins too
        # (issue #898). create_battle draws an absent seed the same way.
        if seed is None:
            seed = random.randrange(SEED_RANGE)
        foes, dropped = _fit_to_field(
            resolve_foes(requests, rng=stand_in_rng(seed)), room
        )
        if not foes:
            raise FightError("No foes to fight")
        beast_slugs = tuple(
            foe.beast.slug
            for foe in foes
            if foe.beast is not None
            for _ in range(foe.count)
        )
        # The people among the foes (#787), one generated fighter each.
        people = tuple(
            foe.archetype for foe in foes if foe.archetype for _ in range(foe.count)
        )
        battle = create_battle(
            session.user,
            name=battle_name_for(party, foes),
            count=len(people),
            seed=seed,
            archetypes=people or ARCHETYPES,
            fighter_teams=(FOE_TEAM,) * len(people),
            # The Game Master's figures, not characters on the player's list.
            keep_generated=False,
            beast_slugs=beast_slugs,
            character_ids=tuple(character.pk for character in party),
            character_teams=(PARTY_TEAM,) * len(party),
            beast_teams=(FOE_TEAM,) * len(beast_slugs),
            control_mode=control.AUTOMATIC,
            # A session's fight is the story's: its wounds and deaths are the
            # characters' own (#750, #751).
            writes_to_records=True,
        )
        _label_foes_on_the_board(battle, foes)
        # Each character is seated to its owner (issue #853): in a Game
        # Master's session a party of his players' characters is played by
        # the players, and he takes a seat only for an owner who is absent,
        # through battle.services.take_seat, which the battle log records.
        for character in party:
            battle.combatants.filter(character_id=character.pk).update(
                player_id=character.user_id, control_mode=control.MANUAL
            )
        # The foes are the Game Master's side, not the player's (#762,
        # coordinator's ruling under the standing rule; Spencer may
        # overrule). The player made the battle, so without this they could
        # restart it, take the foes over and choose their moves. Keyed on
        # the account, as every Game Master power is: a session's mode is the
        # player's to switch and confers nothing (#352), so a player who
        # switched to aide mode and typed /fight used to keep the foes.
        if not is_dm(session.user):
            battle.foes_belong_to_game_master = True
            battle.save(update_fields=["foes_belong_to_game_master", "updated_at"])
        history = list(locked.history)
        history.extend(pending_battle_entries(history, locked))
        history.append(
            battle_entry(
                opening_block(battle, party, foes),
                battle=battle,
                sequence=0,
                turn=0,
            )
        )
        history.append(
            {
                "role": "assistant",
                "content": opening_line(
                    party,
                    foes,
                    requested=foes_text if chosen_by_game_master else None,
                ),
            }
        )
        locked.history = history
        locked.battle = battle
        locked.save(update_fields=["history_json", "battle", "updated_at"])
    session.battle = battle
    session.refresh_from_db()
    return FightStart(
        battle=battle,
        party=tuple(party),
        foes=tuple(foes),
        dropped=dropped,
        chosen_by_game_master=chosen_by_game_master,
        requested=foes_text.strip() if chosen_by_game_master else "",
    )
