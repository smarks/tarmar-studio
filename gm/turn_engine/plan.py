"""Reading the model's JSON plan, and holding it to the schema and roster (#590).

The planning call answers with ``{"actions": [...]}``, parsed with
``json.loads`` and nothing cleverer. Two kinds of problem are told apart:

* A plan that is not the schema -- prose, broken JSON, an unknown action, a
  field of the wrong type -- is an *error*. The model is asked again once with
  the errors; a second failure means the turn runs no actions and says so.
  Nothing is guessed from a malformed plan.
* A well-formed action this turn may not run -- a character id the session
  does not have, a player stating attribute scores, a player reviving someone
  -- is a *refusal*. It is kept in the plan, in order, and reported in the
  results in plain words, so the narration can say what was not done.

Deciding what the rules allow (a score of 40, a dead character's years) is
not this module's job: :mod:`characters.services` refuses those when the
action runs.
"""

import json
import math
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, TypeIs

from characters.constants import (
    CHARACTER_ATTRIBUTE_FIELDS,
    CHARACTER_TEXT_LIMITS,
    MAX_ARMOR_DEX_PENALTY_MAGNITUDE,
    MAX_ARMOR_STOPS,
    MAX_EQUIPMENT_DAMAGE_LENGTH,
    MAX_EQUIPMENT_QUANTITY,
    MAX_SKILL_NAME_LENGTH,
)
from characters.purse import CUSTOM_COMBAT_CATEGORIES, MAX_COIN_MOVEMENT
from characters.rules import character_rules
from characters.skill_checks import MAX_DIFFICULTY
from characters.skill_rules import skill_rules
from characters.skills import (
    MAX_EXPERIENCE_YEARS_PER_RUN,
    selectable_tracks,
    track_key_for,
)

from ..models import (
    CampaignState,
    SceneEntity,
    SessionCharacter,
    StoryThread,
    SupplyItem,
    TableRuling,
)
from ..refusal_words import refusal_words
from ..rules_lookup import MAX_SUBJECT_CHARS, MAX_TRACK_CHARS
from .policy import ACTION_NAMES, TurnPolicy, already_refused_error, refusal_text

#: The most actions one plan may hold. A turn asking for more is a plan gone
#: wrong, not a table's real request.
MAX_ACTIONS = 16

#: The most separate rolls one ``roll`` action may ask for (``3d6 x6``).
MAX_ROLL_TIMES = 6

#: The longest free text a plan field may carry.
MAX_TEXT_CHARS = 200

#: The longest text a plan may carry for a field the character record
#: stores -- a character's name, an item's, a skill's, a cause of death, a
#: coin movement's reason: a bound on a plan gone wrong, not the field's
#: limit. Each is held to its own limit
#: (``characters.constants.CHARACTER_TEXT_LIMITS``) by the service that
#: writes it, the one the sheet calls, so a text one character too long is a
#: refusal the ledger shows rather than a plan the model is quietly asked to
#: rewrite shorter (issue #928). Built from those limits, so it stays above
#: every one of them (``gm.test_text_limits``).
MAX_RECORD_TEXT_CHARS = 2 * max(CHARACTER_TEXT_LIMITS.values())

#: The most XP one ``award_xp`` may give: past a campaign's worth, it is a
#: typo rather than an award (#802).
MAX_XP_AWARDED = 1_000_000


#: Dice a ``roll`` action may ask for: ``NdM``, ``NdM+K``, ``NdM-K``, and a
#: count of separate rolls on the end, ``3d6 x6``.
#:
#: The dice field is the model's, so the pattern runs in linear time (issue
#: #11). The whitespace after the times is inside their optional group: it
#: was a ``\s*`` after the group, next to the ``\s*`` before it, and with no
#: times the two divided a run of spaces every way before failing: quadratic,
#: 0.78 s on 20,000 spaces.
DICE_PATTERN = re.compile(
    r"^\s*(?P<dice>\d{1,2}d\d{1,3}(?:\s*[+-]\s*\d{1,3})?)\s*"
    r"(?:[x×]\s*(?P<times>\d{1,2})\s*)?$",
    re.IGNORECASE,
)

#: The background fields a plan names, and the character fields they set.
BACKGROUND_FIELDS: dict[str, str] = {
    "provenance": "provenance_class",
    "location": "location",
    "wealth": "wealth_level",
}

# A Markdown fence around the plan: "```json ... ```", or "```" with no tag.
# The reply is the model's words, which a player can steer, so the pattern
# matches in linear time (issue #11). It has one repeated part, the body. The
# old pattern put a "\s*" on each side of a lazy body, three repeats that
# could all take the same spaces, so "```" and a run of spaces with no closing
# fence was split every possible way before failing: cubic, 2 s at 2,000
# spaces. The body now runs to the closing "```" at the very end (the reply is
# stripped first): it takes the rest of the reply and gives back one
# character at a time until a fence closes it, one comparison a step. The
# caller strips the whitespace the "\s*"s took.
_FENCE = re.compile(r"```(?:json)?(?P<body>.*)```", re.DOTALL | re.IGNORECASE)


def unknown_thread_error(said: int, open_ids: Collection[int]) -> str:
    """Why a ``resolve_thread`` names no thread this session can close (#627).

    The model planned ``resolve_thread`` with an invented id after putting
    the party's promise inside an NPC's ``condition`` field rather than
    opening a thread for it, so there was nothing to close. Refusing the
    action left the narration to say the promise was kept with no record of
    it; this makes the plan unreadable instead, which the engine answers by
    asking once more -- with the ids that exist, and with what to do when
    none do.

    Args:
        said: The id the plan named.
        open_ids: Every thread this session still has open.

    Returns:
        The error, as the re-ask lists it.
    """
    if not open_ids:
        return (
            f"there is no thread {said}: this session has no open threads, so "
            "nothing can be closed. A promise, a debt or anything else left "
            "hanging is opened with open_thread first, in the same plan, and "
            "closed in a later turn"
        )
    listed = ", ".join(str(one) for one in sorted(open_ids))
    return (
        f"there is no open thread {said} in this session; the open threads "
        f"are {listed}. Close one of those, or open a new one with open_thread"
    )


#: The times of day a plan may set, from the record's own choices so the two
#: cannot drift apart.
TIMES_OF_DAY = frozenset(value for value, _label in CampaignState.TIMES_OF_DAY)

#: The most days one action may advance the clock. A long rest or a season's
#: travel is several actions, and a typo is not a year.
MAX_DAYS_ADVANCED = 60


@dataclass(frozen=True)
class NewCharacter:
    """A character a plan starts: its name, and what the conversation calls it."""

    name: str = ""
    label: str = ""

    @property
    def keys(self) -> set[str]:
        """The references a later action in the same plan may use for it."""
        return {key.casefold() for key in (self.name, self.label) if key}

    @property
    def called(self) -> str:
        """How the character is named before it has an id."""
        return self.name or self.label


@dataclass(frozen=True)
class PlannedAction:
    """One action from a plan, checked against the schema.

    Only the fields its ``action`` uses are set.

    Attributes:
        action: One of :data:`gm.turn_engine.policy.ACTION_NAMES`.
        character_id: The roster id it acts on, when it names one.
        new_character: The character it starts, for the attribute actions.
        new_character_ref: The name of a character started earlier in the
            same plan, when ``character_id`` named one that way.
        scores: Stated scores, ``(attribute, value)`` in schema order.
        all_score: One stated score for all six attributes.
        refusal: Why this turn may not run it; ``""`` when it may.
    """

    action: str
    character_id: int | None = None
    new_character: NewCharacter | None = None
    new_character_ref: str = ""
    scores: tuple[tuple[str, int], ...] = ()
    all_score: int | None = None
    track: str = ""
    years: int = 0
    dice: str = ""
    times: int = 1
    purpose: str = ""
    name: str = ""
    background: tuple[tuple[str, str], ...] = ()
    skill: str = ""
    points: int = 0
    item: str = ""
    quantity: int = 0
    #: For ``add_equipment`` (#927): the price of one item the catalog does
    #: not sell, as decimal text; ``""`` when none was stated, which is not
    #: the same as a stated 0.
    price: str = ""
    #: For ``add_equipment`` (#927): a custom item's combat numbers, a Game
    #: Master's to give, as ``(field, value)`` pairs from ``category``,
    #: ``damage``, ``stops`` and ``dex_penalty``.
    combat: tuple[tuple[str, str | int], ...] = ()
    question: str = ""
    topic: str = ""
    rule: str = ""
    ruling_id: int | None = None
    place: str = ""
    days: int = 0
    time_of_day: str = ""
    amount: str = ""
    spent: bool = False
    where: str = ""
    wants: str = ""
    condition: str = ""
    notes: str = ""
    text: str = ""
    thread_id: int | None = None
    resolution: str = ""
    subject: str = ""
    literate: bool | None = None
    #: A Fatigue roll a Game Master states, rather than rolling (#802).
    fatigue_value: int | None = None
    #: How a character died, for ``mark_dead`` (#802).
    cause: str = ""
    #: XP earned in play, for ``award_xp`` (#802).
    xp: int = 0
    #: For ``set_coin`` (#802): coin to add (negative to take), or the purse
    #: to leave, as decimal text; exactly one is set.
    coin_change: str = ""
    coin_total: str = ""
    #: What coin moved for, for its history line (#802).
    reason: str = ""
    #: A skill check's Game Master's overrides (#843): the difficulty, the
    #: attribute used instead of the governing one, and a 2d6 total rolled
    #: at his table. An attribute check (#911) names its attribute here, and
    #: a Game Master's 3d6 total rolled at his table.
    difficulty: int | None = None
    attribute: str = ""
    stated_total: int | None = None
    #: A Game Master's situational modifier on an attribute check's target
    #: (#953), never a survival save's penalty (``survival_save`` owns that).
    #: 0 for none.
    modifier: int = 0
    refusal: str = ""
    #: The policy's reason key for ``refusal`` (a ``REASON_*`` of
    #: :mod:`gm.turn_engine.policy`), or ``""`` when the policy is not why.
    refusal_reason: str = ""

    @property
    def refused(self) -> bool:
        """Whether this action is refused before it runs."""
        return bool(self.refusal)

    def as_payload(self) -> dict[str, Any]:
        """The action as the event log keeps it: only the fields it set."""
        payload: dict[str, Any] = {"action": self.action}
        for name, value in (
            ("character_id", self.character_id),
            ("new_character_ref", self.new_character_ref),
            ("all", self.all_score),
            ("track", self.track),
            ("years", self.years),
            ("dice", self.dice),
            ("purpose", self.purpose),
            ("name", self.name),
            ("skill", self.skill),
            ("points", self.points),
            ("item", self.item),
            ("quantity", self.quantity),
            ("question", self.question),
            ("topic", self.topic),
            ("rule", self.rule),
            ("ruling_id", self.ruling_id),
            ("place", self.place),
            ("days", self.days),
            ("time_of_day", self.time_of_day),
            ("amount", self.amount),
            ("where", self.where),
            ("wants", self.wants),
            ("condition", self.condition),
            ("notes", self.notes),
            ("text", self.text),
            ("thread_id", self.thread_id),
            ("resolution", self.resolution),
            ("subject", self.subject),
            ("value", self.fatigue_value),
            ("cause", self.cause),
            ("xp", self.xp),
            ("reason", self.reason),
            ("attribute", self.attribute),
            ("total", self.stated_total),
            ("modifier", self.modifier),
            ("refusal", self.refusal),
        ):
            if value not in (None, "", 0):
                payload[name] = value
        if self.times != 1:
            payload["times"] = self.times
        if self.spent:
            # Written out only when true: False and 0 are the same to the
            # loop above, and a payload of defaults says nothing.
            payload["spent"] = True
        if self.literate is not None:
            # False is a ruling, not a default, so it is written out too.
            payload["literate"] = self.literate
        if self.difficulty is not None:
            # A difficulty of 0 is a difficulty, not a default.
            payload["difficulty"] = self.difficulty
        # Coin is a JSON number in a plan, so it is written back as one; a
        # purse of 0 is a figure, not a default.
        if self.coin_change:
            payload["change"] = float(self.coin_change)
        if self.coin_total:
            payload["purse"] = float(self.coin_total)
        if self.price:
            payload["price"] = float(self.price)
        # A combat number of 0 is a number, not a default (#927).
        payload.update(self.combat)
        if self.new_character is not None:
            payload["new_character"] = {
                "name": self.new_character.name,
                "label": self.new_character.label,
            }
        if self.scores:
            payload["scores"] = dict(self.scores)
        if self.background:
            payload["background"] = dict(self.background)
        return payload


@dataclass(frozen=True)
class PlanReading:
    """What a planning reply said, once read.

    Attributes:
        actions: The actions, in order, refused ones included; empty when the
            reply had errors.
        errors: Why the reply is not a plan; empty when it is one.
    """

    actions: tuple[PlannedAction, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether the reply was a plan."""
        return not self.errors


def _json_object(raw: str) -> tuple[Any, str]:
    """The JSON the reply holds, or ``(None, error)``."""
    text = raw.strip()
    fenced = _FENCE.fullmatch(text)
    if fenced is not None:
        text = fenced.group("body").strip()
    if not text:
        return None, "the reply was empty; a plan is a JSON object"
    try:
        return json.loads(text), ""
    except json.JSONDecodeError as error:
        return None, (
            f"the reply is not JSON ({error.msg} at character {error.pos}); "
            'reply with only the JSON object {"actions": [...]}'
        )


def _unsaid(value: object) -> bool:
    """Whether an optional field's value says nothing: null or a blank string.

    See :meth:`_ActionReader.given`, which reads every optional field this
    way (issues #648, #833).
    """
    return value is None or (isinstance(value, str) and not value.strip())


def _is_whole(value: object) -> TypeIs[int]:
    """Whether ``value`` is a plain whole number, which ``True`` is not."""
    return isinstance(value, int) and not isinstance(value, bool)


class _ActionReader:
    """Reads one action object, collecting every error it has."""

    def __init__(
        self,
        entry: Mapping[str, Any],
        where: str,
        started: dict[str, NewCharacter],
    ) -> None:
        self.entry = entry
        self.where = where
        self.started = started
        self.errors: list[str] = []
        self.fields: dict[str, Any] = {}

    def fail(self, message: str) -> None:
        self.errors.append(f"{self.where}: {message}")

    def given(self, key: str) -> bool:
        """Whether the model actually said anything for the optional ``key``.

        A key it left out, wrote as ``null``, or wrote as an empty string has
        nothing in it to record, and every optional field here means "leave
        this as it is" when it is unsaid. All three therefore read the same
        way. Reading them as values instead cost the whole plan: one
        ``"wants": null`` -- routine, and the thing ``record_object`` is told
        to write, since a thing does not want anything -- made the reply no
        plan at all, so the well-formed actions beside it ran nothing either
        (issue #648).

        A number, a list or any other type is *not* silently dropped: the
        field is read, and the reader says what was wrong with it.

        Args:
            key: The field's name in the action object.
        """
        return not _unsaid(self.entry.get(key))

    def text(
        self, key: str, *, required: bool = True, limit: int = MAX_TEXT_CHARS
    ) -> str:
        value = self.entry.get(key)
        if value is None and not required:
            return ""
        if not isinstance(value, str) or (required and not value.strip()):
            self.fail(f"{key} must be a non-empty string")
            return ""
        if len(value.strip()) > limit:
            self.fail(f"{key} must be at most {limit} characters")
            return ""
        return value.strip()

    def whole(
        self, key: str, low: int, high: int, *, default: int | None = None
    ) -> int:
        # A field with a default is an optional one, so a key the model left
        # out, wrote as null or wrote as an empty string takes the default
        # (issues #648, #833). A required one has no default and is read as
        # written, so its absence is still an error.
        value: Any = self.entry.get(key)
        if default is not None and not self.given(key):
            value = default
        if not _is_whole(value) or not low <= value <= high:
            self.fail(f"{key} must be a whole number from {low} to {high}")
            return low
        return value

    def _says_new_character(self) -> bool:
        """Whether ``new_character`` holds anything, beside a ``character_id``.

        ``"new_character": {}`` -- or one whose name and label are null or
        blank -- written next to a ``character_id`` says nothing about a new
        character, the way an empty optional field says nothing (issue #833).
        Written alone it is still read, so the model is told it needs a name
        or a label.
        """
        if not self.given("new_character"):
            return False
        new = self.entry["new_character"]
        if isinstance(new, Mapping) and self.given("character_id"):
            return any(not _unsaid(value) for value in new.values())
        return True

    def coin(self, key: str, *, allow_negative: bool) -> str:
        """A coin amount in gp, as decimal text; ``""`` after an error.

        A number, to the Minor MU (two places); never a bool or a string,
        because a plan's figures are JSON numbers. A change of nothing is not
        a change.
        """
        value = self.entry.get(key)
        # JSON as Python reads it takes NaN and Infinity, and 1e400 arrives as
        # infinity: none is a number of gold pieces, and Decimal raised on
        # them out of read_plan, losing the turn (review of #833).
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or (isinstance(value, float) and not math.isfinite(value))
        ):
            self.fail(f"{key} must be a number of gold pieces")
            return ""
        low = -MAX_COIN_MOVEMENT if allow_negative else Decimal(0)
        amount = Decimal(str(value))
        # Held to the range before rounding: quantizing 1e300 to the Minor
        # MU is past Decimal's precision and raises.
        if low <= amount <= MAX_COIN_MOVEMENT:
            amount = amount.quantize(Decimal("0.01"))
        if not low <= amount <= MAX_COIN_MOVEMENT or (allow_negative and not amount):
            self.fail(
                f"{key} must be between {low} and {MAX_COIN_MOVEMENT} gold pieces"
                + (", and not 0" if allow_negative else "")
            )
            return ""
        return str(amount)

    def target(self, *, may_start: bool = False, optional: bool = False) -> None:
        """Read ``character_id`` (or ``new_character``) into the fields.

        Both references are optional fields as :meth:`given` reads them: an
        empty string or null is unsaid, so a ``roll`` written with
        ``"character_id": ""`` is a roll by nobody in particular rather than
        a plan with a bad reference in it (issue #833).
        """
        if may_start and self._says_new_character():
            if self.given("character_id"):
                self.fail("give character_id or new_character, not both")
                return
            new = self.entry["new_character"]
            if not isinstance(new, Mapping):
                self.fail('new_character must be an object {"name": ..., "label": ...}')
                return
            # A key written as null says nothing, so it reads as unsaid
            # rather than as a name of the wrong type (issue #648).
            name, label = new.get("name") or "", new.get("label") or ""
            if not isinstance(name, str) or not isinstance(label, str):
                self.fail("new_character's name and label must be strings")
                return
            if not (name.strip() or label.strip()):
                self.fail("new_character needs a name or a label")
                return
            if len(name.strip()) > MAX_RECORD_TEXT_CHARS:
                self.fail(
                    f"new_character's name must be at most "
                    f"{MAX_RECORD_TEXT_CHARS} characters"
                )
                return
            # The name is held to the name rule where the character is
            # started (``gm.roster``), a refusal the ledger shows (#870,
            # #928); cutting it here made a 150-character name pass.
            started = NewCharacter(
                name=name.strip(),
                label=label.strip()[: SessionCharacter.MAX_LABEL_CHARS],
            )
            if started.called.casefold() in self.started:
                # One character, started twice in one plan, would be two
                # records with the later actions' work split between them
                # (issue #856).
                self.fail(
                    f"{started.called} is already started earlier in this plan; "
                    'name them again with "new_character_ref": '
                    f'"{started.called}", or give a second character a name of '
                    "its own"
                )
                return
            for key in started.keys:
                self.started[key] = started
            self.fields["new_character"] = started
            return
        # A character this plan started is named either in "character_id" or
        # in "new_character_ref". The second is what ``as_payload`` writes, so
        # a plan that was read once could not be read back in its own shape,
        # and a model told to use that key had every such action refused
        # (issue #608).
        value = None
        for key in ("character_id", "new_character_ref"):
            if self.given(key):
                value = self.entry[key]
                break
        if value is None:
            if not optional:
                self.fail(
                    "character_id is required, or new_character_ref naming a "
                    "character an earlier action in this plan started"
                )
            return
        if _is_whole(value):
            self.fields["character_id"] = value
            return
        if isinstance(value, str) and value.strip().casefold() in self.started:
            self.fields["new_character_ref"] = self.started[
                value.strip().casefold()
            ].called
            return
        self.fail(
            "character_id must be an id from the character list, or, for a "
            "character this plan starts, the name an earlier action gave in "
            'its "new_character" field'
        )


def _read_action(
    entry: object,
    index: int,
    started: dict[str, NewCharacter],
    thread_ids: Collection[int] = (),
) -> tuple[PlannedAction | None, list[str]]:
    """One action, or the errors that stop it being one."""
    where = f"action {index}"
    if not isinstance(entry, Mapping):
        return None, [f"{where}: each action must be a JSON object"]
    name = entry.get("action")
    if name not in ACTION_NAMES:
        return None, [
            f"{where}: unknown action {name!r}; the actions are "
            + ", ".join(ACTION_NAMES)
        ]
    reader = _ActionReader(entry, f"{where} ({name})", started)
    fields = reader.fields
    if name == "record_attributes":
        reader.target(may_start=True)
        # A member written as null (or blank) states no score for that
        # attribute, as an unsaid optional field says nothing: a Game
        # Master's partial scores keep the ones he gave (issue #833). What is
        # left may be nothing at all, which is then no scores.
        scores = entry.get("scores")
        if isinstance(scores, Mapping):
            scores = {
                attribute: value
                for attribute, value in scores.items()
                if not _unsaid(value)
            }
        has_scores = reader.given("scores") and scores != {}
        has_all = reader.given("all")
        if has_scores == has_all:
            reader.fail('give either "scores" or "all"')
        elif has_all:
            fields["all_score"] = reader.whole("all", 1, 99)
        else:
            if not isinstance(scores, Mapping) or not scores:
                reader.fail("scores must be an object of attribute names to numbers")
            else:
                given: dict[str, int] = {}
                for attribute, value in scores.items():
                    key = str(attribute).strip().casefold()
                    if key not in CHARACTER_ATTRIBUTE_FIELDS:
                        reader.fail(
                            f"{attribute!r} is not an attribute; write them in full: "
                            + ", ".join(CHARACTER_ATTRIBUTE_FIELDS)
                        )
                    elif not _is_whole(value):
                        reader.fail(f"the score for {key} must be a whole number")
                    else:
                        given[key] = value
                fields["scores"] = tuple(
                    (attribute, given[attribute])
                    for attribute in CHARACTER_ATTRIBUTE_FIELDS
                    if attribute in given
                )
    elif name == "roll_attributes":
        reader.target(may_start=True)
    elif name == "roll_fatigue":
        reader.target()
        if reader.given("value"):
            fatigue_roll = character_rules().fatigue_roll
            fields["fatigue_value"] = reader.whole(
                "value", fatigue_roll.minimum, fatigue_roll.maximum
            )
    elif name in (
        "roll_literacy",
        "roll_background",
        "revive_character",
        "remove_character",
        "survival_save",
    ):
        reader.target()
    elif name == "mark_dead":
        reader.target()
        # Held to MAX_DEATH_CAUSE_LENGTH by services.apply_death (#928).
        fields["cause"] = reader.text("cause", limit=MAX_RECORD_TEXT_CHARS)
    elif name == "award_xp":
        reader.target()
        fields["xp"] = reader.whole("xp", 1, MAX_XP_AWARDED)
    elif name == "set_coin":
        reader.target()
        said = [key for key in ("change", "purse") if reader.given(key)]
        if len(said) != 1:
            reader.fail('give "change" (coin to add, negative to take) or "purse"')
        else:
            amount = reader.coin(said[0], allow_negative=said[0] == "change")
            if amount:
                fields["coin_change" if said[0] == "change" else "coin_total"] = amount
        if reader.given("reason"):
            # Held to MAX_COIN_REASON_LENGTH by purse.move_coin_for_reason.
            fields["reason"] = reader.text("reason", limit=MAX_RECORD_TEXT_CHARS)
    elif name == "skill_check":
        reader.target()
        # A check names a skill held, so no longer than one may be (#843).
        fields["skill"] = reader.text("skill", limit=MAX_SKILL_NAME_LENGTH)
        if reader.given("difficulty"):
            fields["difficulty"] = reader.whole("difficulty", 0, MAX_DIFFICULTY)
        if reader.given("attribute"):
            said_attribute = reader.text("attribute").casefold()
            if said_attribute not in CHARACTER_ATTRIBUTE_FIELDS:
                reader.fail(
                    "attribute must be written in full: "
                    + ", ".join(CHARACTER_ATTRIBUTE_FIELDS)
                )
            else:
                fields["attribute"] = said_attribute
        if reader.given("total"):
            # What the canon's skill check dice can show (2d6 on the pages).
            skill_check_dice = skill_rules().skill_check
            fields["stated_total"] = reader.whole(
                "total", skill_check_dice.minimum, skill_check_dice.maximum
            )
    elif name == "attribute_check":
        # 3d6 ≤ the attribute (#911): the rule names the direction, so the
        # plan names only whose check and on what.
        reader.target()
        said_attribute = reader.text("attribute").casefold()
        if said_attribute and said_attribute not in CHARACTER_ATTRIBUTE_FIELDS:
            reader.fail(
                "attribute must be written in full: "
                + ", ".join(CHARACTER_ATTRIBUTE_FIELDS)
            )
        elif said_attribute:
            fields["attribute"] = said_attribute
        attribute_check_rules = skill_rules().attribute_check
        if reader.given("total"):
            # What the canon's attribute check dice can show (3d6 on the pages).
            fields["stated_total"] = reader.whole(
                "total", attribute_check_rules.minimum, attribute_check_rules.maximum
            )
        if reader.given("modifier"):
            # On the target, after any injury (#953): who may set it is
            # read_plan's ruling and the check's own; how far, the canon's.
            limit = attribute_check_rules.game_master_modifier_limit
            fields["modifier"] = reader.whole("modifier", -limit, limit)
    elif name == "play_years":
        reader.target()
        track = entry.get("track")
        normalised = track_key_for(track) if isinstance(track, str) else ""
        allowed_tracks = selectable_tracks()
        if normalised not in allowed_tracks:
            reader.fail("track must be one of " + ", ".join(allowed_tracks))
        fields["track"] = normalised
        fields["years"] = reader.whole("years", 1, MAX_EXPERIENCE_YEARS_PER_RUN)
    elif name == "spend_skill_points":
        reader.target()
        fields["skill"] = reader.text("skill", limit=MAX_RECORD_TEXT_CHARS)
        fields["points"] = reader.whole("points", 1, 99)
    elif name == "add_equipment":
        reader.target()
        fields["item"] = reader.text("item", limit=MAX_RECORD_TEXT_CHARS)
        fields["quantity"] = reader.whole(
            "quantity", 1, MAX_EQUIPMENT_QUANTITY, default=1
        )
        # Who pays it, or may name combat numbers at all, is the service's
        # ruling (#927); the plan carries what was stated.
        if reader.given("price"):
            fields["price"] = reader.coin("price", allow_negative=False)
        fields["combat"] = _combat_numbers(reader)
    elif name == "roll":
        reader.target(optional=True)
        dice = entry.get("dice")
        matched = DICE_PATTERN.match(dice) if isinstance(dice, str) else None
        if matched is None:
            reader.fail(
                'dice must be dice notation such as "3d6", "1d20+4" or "3d6 x6"'
            )
        else:
            times = int(matched.group("times") or 1)
            if not 1 <= times <= MAX_ROLL_TIMES:
                reader.fail(f"a roll may be repeated 1 to {MAX_ROLL_TIMES} times")
            fields["dice"] = re.sub(r"\s+", "", matched.group("dice")).lower()
            fields["times"] = times
        fields["purpose"] = reader.text("purpose", required=False)
    elif name == "rule_literacy":
        reader.target()
        literate = entry.get("literate")
        if not isinstance(literate, bool):
            reader.fail("literate must be true or false")
        else:
            fields["literate"] = literate
    elif name == "rename_character":
        reader.target()
        fields["name"] = reader.text("name", limit=MAX_RECORD_TEXT_CHARS)
    elif name == "set_background":
        reader.target()
        given_background = []
        for key, field_name in BACKGROUND_FIELDS.items():
            if reader.given(key):
                value = reader.text(key)
                given_background.append((field_name, value))
        if not given_background:
            reader.fail("give at least one of provenance, location or wealth")
        fields["background"] = tuple(given_background)
    elif name == "record_house_rule":
        # Whitespace is collapsed: the rule is read back to the model as a
        # single state-block line.
        for key, limit in (
            ("topic", TableRuling.MAX_TOPIC_CHARS),
            ("rule", TableRuling.MAX_RULE_CHARS),
        ):
            fields[key] = " ".join(reader.text(key, limit=limit).split())
    elif name == "retire_house_rule":
        value = entry.get("ruling_id")
        if not _is_whole(value):
            reader.fail("ruling_id must be a ruling id from the table's rulings")
        else:
            fields["ruling_id"] = value
    elif name == "move_party":
        fields["place"] = reader.text("place", limit=CampaignState.MAX_PLACE_CHARS)
    elif name == "advance_time":
        if reader.given("days"):
            fields["days"] = reader.whole("days", 0, MAX_DAYS_ADVANCED)
        if reader.given("time_of_day"):
            said = reader.text("time_of_day").strip().casefold()
            if said not in TIMES_OF_DAY:
                reader.fail(
                    "time_of_day must be one of " + ", ".join(sorted(TIMES_OF_DAY))
                )
            else:
                fields["time_of_day"] = said
        if not reader.given("days") and not reader.given("time_of_day"):
            reader.fail("advance_time needs days, time_of_day, or both")
    elif name == "adjust_supplies":
        fields["item"] = reader.text("item", limit=SupplyItem.MAX_NAME_CHARS)
        if reader.given("amount"):
            fields["amount"] = reader.text("amount", limit=SupplyItem.MAX_AMOUNT_CHARS)
        if reader.given("note"):
            fields["notes"] = reader.text("note", limit=SupplyItem.MAX_NOTE_CHARS)
        if reader.given("spent"):
            if entry["spent"] is not True and entry["spent"] is not False:
                reader.fail("spent must be true or false")
            else:
                fields["spent"] = entry["spent"]
    elif name in ("record_npc", "record_object"):
        fields["name"] = reader.text("name", limit=SceneEntity.MAX_NAME_CHARS)
        for key in ("where", "wants", "condition", "notes"):
            if reader.given(key):
                fields[key] = reader.text(key, limit=SceneEntity.MAX_DETAIL_CHARS)
        if name == "record_object" and fields.get("wants"):
            reader.fail("a thing does not want anything; use condition")
    elif name == "open_thread":
        fields["text"] = reader.text("text", limit=StoryThread.MAX_TEXT_CHARS)
    elif name == "resolve_thread":
        value = entry.get("thread_id")
        if not _is_whole(value):
            reader.fail("thread_id must be a thread id from the open threads")
        elif value not in thread_ids:
            reader.fail(unknown_thread_error(value, thread_ids))
        else:
            fields["thread_id"] = value
        if reader.given("resolution"):
            fields["resolution"] = reader.text(
                "resolution", limit=StoryThread.MAX_TEXT_CHARS
            )
    elif name == "rules_lookup":
        fields["subject"] = reader.text("subject", limit=MAX_SUBJECT_CHARS)
        if reader.given("track"):
            fields["track"] = reader.text("track", limit=MAX_TRACK_CHARS)
    else:  # ask_gm
        fields["question"] = reader.text("question")
    if reader.errors:
        return None, reader.errors
    return PlannedAction(action=name, **fields), []


def _combat_numbers(reader: _ActionReader) -> tuple[tuple[str, str | int], ...]:
    """A custom item's stated combat numbers, as ``add_equipment`` carries them.

    The sheet's builder holds them to its bounds again when the action runs
    (``characters.equipment_service.custom_equipment_item``); a player's are
    refused there (#845, #927).
    """
    numbers: list[tuple[str, str | int]] = []
    if reader.given("category"):
        category = reader.text("category").strip().casefold()
        if category not in CUSTOM_COMBAT_CATEGORIES:
            reader.fail(
                "category must be one of " + ", ".join(CUSTOM_COMBAT_CATEGORIES)
            )
        numbers.append(("category", category))
    if reader.given("damage"):
        numbers.append(
            ("damage", reader.text("damage", limit=MAX_EQUIPMENT_DAMAGE_LENGTH))
        )
    if reader.given("stops"):
        numbers.append(("stops", reader.whole("stops", 0, MAX_ARMOR_STOPS)))
    if reader.given("dex_penalty"):
        numbers.append(
            (
                "dex_penalty",
                reader.whole("dex_penalty", -MAX_ARMOR_DEX_PENALTY_MAGNITUDE, 0),
            )
        )
    if numbers and not reader.given("category"):
        reader.fail("combat numbers need a category: weapons, armor or shield")
    return tuple(numbers)


def policy_refusal_reason(action: PlannedAction, policy: TurnPolicy) -> str:
    """Which refusal the turn's policy gives ``action`` (a ``REASON_*`` key of
    :mod:`gm.turn_engine.policy`), or ``""``."""
    return policy.refusal_reason(
        action.action,
        states_scores=action.action == "record_attributes"
        or (action.action == "roll_fatigue" and action.fatigue_value is not None),
    )


def policy_refusal(action: PlannedAction, policy: TurnPolicy) -> str:
    """Why the turn's policy refuses ``action``, in its words, or ``""``."""
    return refusal_text(policy_refusal_reason(action, policy))


def _refusal(
    action: PlannedAction, roster_ids: Collection[int], policy: TurnPolicy
) -> tuple[str, str]:
    """Why this turn may not run ``action``, and the policy's reason key when
    the policy is why; ``("", "")`` when it may."""
    if action.character_id is not None and action.character_id not in roster_ids:
        return (
            refusal_words().executor.no_such_character.format(id=action.character_id),
            "",
        )
    reason = policy_refusal_reason(action, policy)
    return refusal_text(reason), reason


#: Why a player's skill check may not carry the Game Master's overrides.
PLAYER_SKILL_CHECK_ERROR = (
    "action {index} (skill_check): a player's skill check carries no "
    "difficulty, attribute or total -- those are the Game Master's to set; "
    "plan it with the character and the skill alone"
)


#: Why a player's attribute check may not state its dice (#911) or carry a
#: modifier (#953).
PLAYER_ATTRIBUTE_CHECK_ERROR = (
    "action {index} (attribute_check): a player's attribute check carries no "
    "total or modifier -- the dice are rolled here, and only the Game Master "
    "states his table's or sets a modifier; plan it with the character and "
    "the attribute alone"
)


def already_held_error(called: str, character_id: int) -> str:
    """Why a plan may not start a character the session already holds (#856).

    The model re-planned ``new_character`` for a Wren the session had made on
    the previous turn: the server rolled a second Wren and played the years
    on her, leaving the first at nought. The same for players and Game
    Masters -- a Game Master who wants two characters of one name can name
    the second differently -- and the existing one is offered by its id.

    Args:
        called: The name the plan gave the new character.
        character_id: The id of the character already called that.
    """
    return (
        f"{called} is already a character in this session, id {character_id}; "
        f'name them with "character_id": {character_id} rather than starting '
        f"a second {called}. A different character needs a name of its own"
    )


def read_plan(
    raw: str,
    *,
    roster_ids: Collection[int],
    policy: TurnPolicy,
    settled: Mapping[str, str] | None = None,
    thread_ids: Collection[int] = (),
    held_names: Mapping[str, int] | None = None,
) -> PlanReading:
    """Read a planning reply into checked actions.

    Args:
        raw: The model's reply, as it wrote it.
        roster_ids: The ids of the session's characters.
        policy: The turn's policy, which decides what is refused.
        settled: Actions already refused in this session for a reason that
            cannot change, from
            :func:`gm.turn_engine.state.settled_refusals`. Planning one again
            makes the reply no plan, so the model is asked once more with the
            reason and what to do instead -- a wasted turn becomes a second
            try inside the same turn (issue #614).
        thread_ids: The ids of the session's open threads. A
            ``resolve_thread`` naming anything else is an error rather than
            a refusal, so the model is asked again with the ids it may
            actually close (issue #627).
        held_names: The session's characters by name, casefolded, to their
            ids. A ``new_character`` with one of these names is an error, so
            the model is asked again and told the id (issue #856).

    Returns:
        The actions with their refusals, or the errors that make the reply no
        plan at all.
    """
    document, error = _json_object(raw)
    if error:
        return PlanReading(errors=(error,))
    if not isinstance(document, Mapping) or not isinstance(
        document.get("actions"), list
    ):
        return PlanReading(
            errors=(
                'a plan is a JSON object with an "actions" list: {"actions": [...]}',
            )
        )
    entries = document["actions"]
    if len(entries) > MAX_ACTIONS:
        return PlanReading(errors=(f"a plan holds at most {MAX_ACTIONS} actions",))
    started: dict[str, NewCharacter] = {}
    actions: list[PlannedAction] = []
    errors: list[str] = []
    for index, entry in enumerate(entries, start=1):
        action, action_errors = _read_action(entry, index, started, thread_ids)
        errors.extend(action_errors)
        if (
            action is not None
            and action.action == "skill_check"
            and not policy.is_game_master
            and (
                action.difficulty is not None or action.attribute or action.stated_total
            )
        ):
            # The skill check's service refuses these for a player too; as a
            # plan error the model is asked again inside the turn and the
            # check the player asked for still rolls (issue #843).
            errors.append(PLAYER_SKILL_CHECK_ERROR.format(index=index))
            continue
        if (
            action is not None
            and action.action == "attribute_check"
            and not policy.is_game_master
            and (action.stated_total is not None or action.modifier)
        ):
            # As for a skill check: the check's service refuses it too, but as
            # a plan error the model is asked again and the check still rolls.
            errors.append(PLAYER_ATTRIBUTE_CHECK_ERROR.format(index=index))
            continue
        if action is not None and action.new_character is not None:
            held = (held_names or {}).get(action.new_character.called.casefold())
            if held is not None:
                errors.append(
                    f"action {index} ({action.action}): "
                    + already_held_error(action.new_character.called, held)
                )
                continue
        if action is not None:
            # Held against the plan only while this turn's policy would still
            # refuse it: the role a settled refusal turned on is read off the
            # account every turn, and may have changed since (issue #835).
            settled_reason = (settled or {}).get(action.action)
            if settled_reason and policy_refusal(action, policy):
                errors.append(already_refused_error(action.action, settled_reason))
                continue
            refusal, reason = _refusal(action, roster_ids, policy)
            actions.append(
                replace(action, refusal=refusal, refusal_reason=reason)
                if refusal
                else action
            )
    if errors:
        return PlanReading(errors=tuple(errors))
    return PlanReading(actions=tuple(actions))
