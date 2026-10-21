"""What a turn may do, decided once when it starts (issue #590).

A :class:`TurnPolicy` is built from the session's mode and the account's Game
Master authority before anything else happens, and every later stage reads
it: the state block says who is speaking, the plan validator refuses what the
speaker may not do, and the narrate message carries the manner. Nothing the
model writes, and nothing typed in the chat, changes it (issue #352).
"""

import functools
import re
from dataclasses import dataclass

from characters.constants import CHARACTER_ATTRIBUTE_FIELDS
from characters.rules import character_rules, game_master_origins_in_words
from characters.skill_rules import skill_rules
from reference.rules_file import derived_from_rules

from ..engine import MODE_AIDE, MODE_PLAY
from ..refusal_words import refusal_words

#: Every action a plan may hold, in the order the schema lists them.
ACTION_NAMES: tuple[str, ...] = (
    "record_attributes",
    "roll_attributes",
    "roll_fatigue",
    "roll_literacy",
    "rule_literacy",
    "roll_background",
    "play_years",
    "spend_skill_points",
    "add_equipment",
    "roll",
    "rename_character",
    "set_background",
    "revive_character",
    "remove_character",
    "record_house_rule",
    "retire_house_rule",
    "move_party",
    "advance_time",
    "adjust_supplies",
    "record_npc",
    "record_object",
    "open_thread",
    "resolve_thread",
    "rules_lookup",
    "ask_gm",
    # Added after the rest, so every earlier position is unchanged (#802,
    # #843): a Game Master's records, and the skill check.
    "mark_dead",
    "award_xp",
    "set_coin",
    "skill_check",
    # After the rest again (#886): a dying character's survival save.
    "survival_save",
    # And again (#911): 3d6 ≤ an attribute, read the rule's way.
    "attribute_check",
)

#: Actions that change what the campaign holds: where the party is, who and
#: what is in play, what is still open (issue #579).
CAMPAIGN_ACTIONS = frozenset(
    {
        "move_party",
        "advance_time",
        "adjust_supplies",
        "record_npc",
        "record_object",
        "open_thread",
        "resolve_thread",
    }
)

#: Actions that set the rules this table plays by (issue #600).
HOUSE_RULE_ACTIONS = frozenset({"record_house_rule", "retire_house_rule"})

#: What only a Game Master records on a character by saying so: a death, XP
#: earned in play, and coin given or taken (issue #802). All three are his on
#: the sheet (``characters.services.FIELD_RULES``).
GAME_MASTER_RECORD_ACTIONS = frozenset({"mark_dead", "award_xp", "set_coin"})

#: Actions only a Game Master's account may have run (issues #403, #600,
#: #743, #802).
GAME_MASTER_ONLY_ACTIONS = frozenset(
    {
        "revive_character",
        "remove_character",
        "rule_literacy",
        *HOUSE_RULE_ACTIONS,
        *GAME_MASTER_RECORD_ACTIONS,
    }
)


#: The policy's refusals, each by its key in ``config/gm-refusals.toml``'s
#: [policy] table (issue #97). A refusal is known by its key, never by its
#: words: two reasons may be given the same sentence, and a sentence may be
#: edited, without either changing what the refusal is.
REASON_GAME_MASTER_ONLY = "game_master_only"
REASON_HOUSE_RULE = "house_rule"
REASON_LITERACY_RULING = "literacy_ruling"
REASON_PLAYER_STATED_SCORES = "player_stated_scores"
REASON_PLAYER_STATED_FATIGUE = "player_stated_fatigue"
REASON_GAME_MASTER_RECORDS = "game_master_records"
REASON_AIDE_UNASKED_ROLL = "aide_unasked_roll"

#: The key an action's procedure frame records its policy refusal under, so a
#: later turn reads the reason rather than its words.
REFUSAL_REASON = "refusal_reason"

#: The refusals that come from who is at the table, not from the message. An
#: account's role does not change inside a session, so these hold on every
#: later turn: asking again can only waste the turn (issue #614). The aide's
#: unasked-roll refusal is deliberately not here -- it depends on the message,
#: and the next one may ask for a roll.
SETTLED_REASONS: frozenset[str] = frozenset(
    {
        REASON_PLAYER_STATED_SCORES,
        REASON_PLAYER_STATED_FATIGUE,
        REASON_GAME_MASTER_ONLY,
        REASON_GAME_MASTER_RECORDS,
        REASON_HOUSE_RULE,
        REASON_LITERACY_RULING,
    }
)


def refusal_text(reason: str) -> str:
    """The configured words of the policy refusal ``reason``, or ``""``.

    The literacy ruling's names the canon's origins left to the Game Master
    when it has any (``literacy_ruling_origin``, issue #128).

    Args:
        reason: One of the ``REASON_*`` keys, or ``""`` for no refusal.
    """
    if not reason:
        return ""
    words = refusal_words().policy
    text = getattr(words, reason)
    if reason == REASON_LITERACY_RULING:
        origins = game_master_origins_in_words(character_rules())
        if origins:
            text += words.literacy_ruling_origin.format(origin=origins)
    return text


#: What to do instead, where the rules offer something. A player may not
#: state their scores, but they may have them rolled, and saying so turns a
#: dead end into the next step.
INSTEAD_OF: dict[str, str] = {
    "record_attributes": "plan roll_attributes: a player's scores are rolled",
    "rule_literacy": "plan roll_literacy where the Origin has a check",
    "roll_fatigue": 'plan roll_fatigue with no "value": a player\'s is rolled',
}


def _remedy(action: str) -> str | None:
    """What :data:`INSTEAD_OF` offers for ``action``, with the canon's
    origins left to the Game Master named after ``rule_literacy``'s (#128).

    Args:
        action: The refused action's name.
    """
    remedy = INSTEAD_OF.get(action)
    if action == "rule_literacy" and remedy:
        origins = game_master_origins_in_words(character_rules())
        if origins:
            remedy += f", or say that at {origins} the Game Master decides"
    return remedy


def already_refused_error(action: str, reason: str) -> str:
    """Why a plan repeating a settled refusal is no plan at all."""
    said = (
        f"{action} was already refused in this session, because {reason}. "
        "That will not change by asking again"
    )
    remedy = _remedy(action)
    return f"{said}; {remedy}" if remedy else f"{said}; do something else instead"


#: Words that show a message is about dice: roll, rolls, rolling (and the
#: same with re-), dice, a die, or dice notation such as 3d6 or d20. "Rolled"
#: reports a roll already made and does not count. "Die" counts only as a
#: thing -- "a die", "the die", "two die" -- because the verb ("would he
#: die?") is a question about the table, which the dice do not decide
#: (issues #598, #794).
_DICE_WORDS = re.compile(
    r"\b(?:re)?roll(?:s|ing)?\b"
    r"|\bdice\b"
    r"|\b(?:a|an|the|one|that|this|his|her|their|my|your|\d+)\s+die\b"
    r"|\b\d*d\d+\b",
    re.IGNORECASE,
)


@derived_from_rules
@functools.cache
def _check_words() -> re.Pattern[str]:
    """A skill check asked for as one.

    "a skill check", "his stealth check", "check Gorn's stealth against 12",
    with the skills by name for the two dice actions that are asked for that
    way rather than as a roll (issue #906). Not the bare word: "check the
    roster" asks for no dice (second review of #906). Compiled when first
    asked, not at import, because the skills it names are the loaded
    canon's ``skill-rules.json`` (issue #70), which no module may read while
    it imports.

    The message is the player's or the Game Master's, so this runs in linear
    time (issue #11). Reading on from "check" to "against" stops at the next
    "check": it used to read to the end of the sentence from every "check" in
    it, quadratic on a sentence of them (0.27 s at 20,000 characters).
    Whether a sentence holds a "check" with "against" after it is unchanged,
    since the last "check" before the "against" still reaches it.
    """
    governed = skill_rules().skill_check.governing_attributes
    skill_names = "|".join(
        re.escape(name) for name in sorted(governed, key=len, reverse=True)
    )
    return re.compile(
        rf"\bskill\s+checks?\b"
        rf"|\b(?:{skill_names})\s+checks?\b"
        rf"|\bchecks?\b(?:(?!\bchecks?\b)[^.!?\n])*\bagainst\b",
        re.IGNORECASE,
    )


#: A survival save asked for as one: "his survival save", "a save vs death",
#: "a saving throw". Not "saving up for a horse".
_SAVE_WORDS = re.compile(
    r"\bsurvival\s+sav(?:e|es|ing)\b"
    r"|\bsav(?:e|es|ing)\s+(?:vs\.?|versus)\s"
    r"|\bsaving\s+throws?\b",
    re.IGNORECASE,
)

#: An attribute check asked for as one (#911): "an attribute check", "a
#: strength check", "his CON check". The three-letter forms are the sheet's.
_ATTRIBUTE_NAMES = "|".join(
    name
    for attribute in CHARACTER_ATTRIBUTE_FIELDS
    for name in (attribute, attribute[:3])
)
_ATTRIBUTE_CHECK_WORDS = re.compile(
    rf"\b(?:attribute|{_ATTRIBUTE_NAMES}|chr)\s+checks?\b", re.IGNORECASE
)

#: Every action that rolls dice in play, each refused in aide mode unless the
#: Game Master's message asks for it (issues #598, #906). The creation rolls
#: -- attributes, Fatigue, background, literacy, the years -- make a
#: character he asked to have made and are not gated here.
AIDE_DICE_ACTIONS = frozenset(
    {"roll", "skill_check", "survival_save", "attribute_check"}
)


def dice_actions_asked_for(latest: str | None) -> frozenset[str]:
    """Which of :data:`AIDE_DICE_ACTIONS` ``latest`` asks for (issue #906).

    Any dice word asks for any of them; a skill check, a survival save or
    an attribute check named as one (:func:`_check_words`,
    :data:`_SAVE_WORDS`, :data:`_ATTRIBUTE_CHECK_WORDS`) asks for that
    action. ``None`` (no chat message, as when a
    tool runs actions) asks for all of them.
    """
    if latest is None or _DICE_WORDS.search(latest):
        return AIDE_DICE_ACTIONS
    asked = set()
    if _check_words().search(latest):
        asked.add("skill_check")
    if _SAVE_WORDS.search(latest):
        asked.add("survival_save")
    if _ATTRIBUTE_CHECK_WORDS.search(latest):
        asked.add("attribute_check")
    return frozenset(asked)


#: The aide's planning rules, added to the plan message (issue #598). The
#: shared system prompt stays byte-identical across plan and narrate calls.
AIDE_PLANNING_GUIDANCE = (
    "You are planning for the aide to a Game Master whose table you cannot "
    "see.\n"
    "- Never plan a roll, or record anything, to decide what happened at his "
    "table: what the players did, what an NPC saw or knows, how a scene went. "
    "Only he knows that, and the plan for such a question is "
    '{"actions": []}, with no roll and no ask_gm.\n'
    "- Roll only when his message asks for a roll.\n"
    "- A rules question needs no roll and no ask_gm. When the same message "
    "states a house rule, the plan records it with record_house_rule and "
    "nothing else."
)

#: A reminder of how an aide answers, said again in each narrate message
#: because the aide section sits tens of thousands of tokens back. The last
#: two rules restate "Only What You Were Told" and "Their Table, Their
#: Rulings" where the narration reads them (issue #598).
AIDE_MANNER = (
    "You are the aide to a Game Master running a table you cannot see: say "
    "what is now true in plain sentences and stop, with no scene-setting, no "
    "numbered menu and no offer or question at the end.\n"
    "- Answer from the rules and from what he has told you. When the answer "
    "turns on something only his table knows (what the players did, what an "
    "NPC saw, how a scene went), say plainly that you do not know it and "
    "what would decide it. Never supply an outcome for it, and never use a "
    "roll to settle it.\n"
    "- When he has stated a house rule, it is the rule: answer by it. You may "
    "add at most one short sentence that the book differs, never an "
    "instruction to follow the book's version and never an answer worked "
    "the book's way."
)

#: The same reminder for play, where the model runs the game.
PLAY_MANNER = (
    "You are running this game: narrate what happened and what the "
    "characters now face, briefly, and leave the next choice to the player."
)


@dataclass(frozen=True)
class TurnPolicy:
    """The rules one turn runs under.

    Attributes:
        mode: :data:`gm.engine.MODE_PLAY` or :data:`gm.engine.MODE_AIDE`.
        is_game_master: Whether the session owner's account carries Game
            Master authority, read off the account (``battle.access.is_dm``).
        message_mentions_dice: Whether the message being answered talks about
            rolling dice. In aide mode a planned roll runs only when it does
            (issue #598).
        dice_asked_for: Which of :data:`AIDE_DICE_ACTIONS` the message asks
            for; in aide mode the others are refused (issue #906).
    """

    mode: str
    is_game_master: bool
    message_mentions_dice: bool = True
    dice_asked_for: frozenset[str] = AIDE_DICE_ACTIONS

    @property
    def is_aide(self) -> bool:
        """Whether the model assists a Game Master rather than running a game."""
        return self.mode == MODE_AIDE

    @property
    def speaker_line(self) -> str:
        """The state block's first line: who sent the latest message."""
        if self.is_game_master:
            return "This message is from the Game Master."
        return "This message is from a player (not the Game Master)."

    @property
    def planning_guidance(self) -> str:
        """Mode-specific planning rules for the plan message; ``""`` in play."""
        return AIDE_PLANNING_GUIDANCE if self.is_aide else ""

    @property
    def manner(self) -> str:
        """How the narration answers, for the narrate message."""
        return AIDE_MANNER if self.is_aide else PLAY_MANNER

    def refusal_reason(self, action: str, *, states_scores: bool = False) -> str:
        """Which refusal this turn gives ``action``: a ``REASON_*`` key, or
        ``""`` when it may run.

        Args:
            action: One of :data:`ACTION_NAMES`.
            states_scores: Whether the action writes down values somebody
                chose -- attribute scores, or a Fatigue roll -- rather than
                rolling them.
        """
        if (
            action in AIDE_DICE_ACTIONS
            and self.is_aide
            and action not in self.dice_asked_for
        ):
            return REASON_AIDE_UNASKED_ROLL
        if self.is_game_master:
            return ""
        if action in HOUSE_RULE_ACTIONS:
            return REASON_HOUSE_RULE
        if action == "rule_literacy":
            return REASON_LITERACY_RULING
        if action in GAME_MASTER_RECORD_ACTIONS:
            return REASON_GAME_MASTER_RECORDS
        if action in GAME_MASTER_ONLY_ACTIONS:
            return REASON_GAME_MASTER_ONLY
        if states_scores and action == "roll_fatigue":
            return REASON_PLAYER_STATED_FATIGUE
        if states_scores:
            return REASON_PLAYER_STATED_SCORES
        return ""

    def refusal_for(self, action: str, *, states_scores: bool = False) -> str:
        """Why this turn may not run ``action``, in the configured words, or
        ``""`` when it may (see :meth:`refusal_reason`)."""
        return refusal_text(self.refusal_reason(action, states_scores=states_scores))

    @classmethod
    def for_turn(
        cls, mode: str, *, is_game_master: bool, latest: str | None = None
    ) -> "TurnPolicy":
        """The policy for a turn in ``mode``; an unknown mode plays.

        Args:
            mode: The session's mode.
            is_game_master: The account's Game Master authority.
            latest: The message the turn answers. ``None`` when there is no
                chat message to read, as when a test or tool runs actions
                directly; such a turn may roll.
        """
        return cls(
            mode=mode if mode in (MODE_PLAY, MODE_AIDE) else MODE_PLAY,
            is_game_master=bool(is_game_master),
            message_mentions_dice=latest is None or bool(_DICE_WORDS.search(latest)),
            dice_asked_for=dice_actions_asked_for(latest),
        )
