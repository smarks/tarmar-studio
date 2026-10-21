"""Who decides a combatant's action — as a setting, not an inference (#308).

Before this module, control was a side effect. A combatant was played by a
human when ``is_open_seat`` was true *and* somebody had claimed the seat;
everything else fell through to the AI, and clearing ``player`` was also how
a figure was handed back. So "play this whole battle for me, I just want to
watch" and "I'll drive my own fighter and let the AI run the rest" were not
things you could ask for — only things a particular roster arrangement
happened to produce.

Two named modes replace the inference:

* :data:`AUTOMATIC` — the AI decides. The only control is *Advance turn*.
* :data:`MANUAL` — the combatant's **owner** decides, and the turn stops at
  its decision point until they have said what it does.

Set at two levels, and the precedence between them is Spencer's ruling:
**the battle's mode is a default that every combatant inherits, and a
per-combatant mode overrides it.** A combatant row therefore holds three
values, not two — :data:`AUTOMATIC`, :data:`MANUAL`, or :data:`INHERIT`
(the empty string) meaning "whatever the battle says". "Set them all to X"
is expressed as an *action* (:func:`battle.services.set_battle_control_mode`
with ``apply_to_all``) rather than as a mode that locks the rows, so a
battle can be uniform without being unable to become mixed.

**Owner** is the other half of the answer, and it is deliberately wider than
"the player in the seat". A seated player or guest owns their own combatant.
Everything else in the battle — a generated fighter, a beast, a saved
character the DM mustered — is owned by whoever runs the battle, which
since #305 is its creator (account or guest) as well as any DM. That is what
makes a fully manual battle playable at all: without it, "manual" for an
unseated figure would name an owner who does not exist.
"""

from __future__ import annotations

from .refusal_words import refusal_words
from .refusals import BattleRefusal

#: The AI decides this combatant's action.
AUTOMATIC = "automatic"
#: This combatant's owner states its action at each decision point.
MANUAL = "manual"
#: A combatant row's "no opinion": follow the battle's own mode.
INHERIT = ""

#: The two real modes, for a battle-level field and for form validation.
MODE_CHOICES = [
    (AUTOMATIC, "Automatic — the AI plays it"),
    (MANUAL, "Manual — its owner plays it"),
]

#: A combatant row's field: the two modes plus "follow the battle".
COMBATANT_MODE_CHOICES = [
    (INHERIT, "Follow the battle"),
    *MODE_CHOICES,
]

MODES = {AUTOMATIC, MANUAL}
COMBATANT_MODES = MODES | {INHERIT}

#: What the AI is called in a log line or a roster card. One spelling.
AI_OWNER = "the AI"
#: What a guest owner is called where a username would go. Guests have no
#: name to print, and #305 established this spelling for the seat lists.
GUEST_OWNER = "guest"


def clean_mode(value) -> str:
    """The battle-level mode ``value`` names.

    Raises:
        BattleRefusal: for anything that is not one of the two real modes.
            Inherit is not among them: a *battle* has nothing to inherit
            from, so accepting the empty string here would store a battle
            whose default is undefined.
    """
    mode = str(value or "").strip()
    if mode not in MODES:
        raise BattleRefusal(refusal_words().control.battle_mode)
    return mode


def clean_combatant_mode(value) -> str:
    """The per-combatant mode ``value`` names, ``INHERIT`` included.

    Raises:
        BattleRefusal: for anything outside :data:`COMBATANT_MODES`.
    """
    mode = str(value or "").strip()
    if mode not in COMBATANT_MODES:
        raise BattleRefusal(refusal_words().control.combatant_mode)
    return mode


def effective_mode(battle, combatant) -> str:
    """The mode this combatant actually plays under.

    The whole precedence rule, in one line and in one place: the row's own
    mode when it has one, the battle's otherwise.
    """
    return combatant.control_mode or battle.control_mode


def is_manual(battle, combatant) -> bool:
    """True when this combatant waits for a person at its decision point."""
    return effective_mode(battle, combatant) == MANUAL


def seat_holder_label(combatant) -> str:
    """What to call the person sitting in this combatant's seat, or ``""``.

    Empty means nobody is sitting in it — which is not the same as nobody
    owning it (see :func:`owner_label`).
    """
    # Imported here, not at the top: battle.models imports this module for
    # its mode constants, and battle.access imports battle.models, so a
    # module-level import would be a cycle.
    from .access import seat_is_held

    if not seat_is_held(combatant):
        return ""
    # Held, by an account (named) or else by a guest. The caller selects
    # ``player`` related where it matters; a guest's seat has none to fetch.
    holder = combatant.player
    return holder.username if holder is not None else GUEST_OWNER


def owner_label(battle, combatant) -> str:
    """Who decides for this combatant when it is manual.

    The seat's holder if there is one; otherwise whoever runs the battle —
    its creator, named if they have an account and called a guest if they
    do not.
    """
    seated = seat_holder_label(combatant)
    if seated:
        return seated
    if battle.created_by_id is not None:
        return battle.created_by.username
    return GUEST_OWNER


def controller_label(battle, combatant) -> str:
    """Who decides for this combatant right now — a person, or the AI."""
    if is_manual(battle, combatant):
        return owner_label(battle, combatant)
    return AI_OWNER


def mode_line(battle, combatant) -> str:
    """The roster card's one line about control, computed server-side.

    The repo convention is that the server works out display and the
    browser only shows it, so the sentence lives here rather than in
    ``battle.js``.
    """
    if is_manual(battle, combatant):
        return f"Manual — played by {owner_label(battle, combatant)}"
    return f"Automatic — {AI_OWNER} plays it"


def battle_change_message(mode: str, *, applied_to_all: bool) -> str:
    """The log line for a change to the battle's own mode."""
    named = "the AI" if mode == AUTOMATIC else "their owners"
    if applied_to_all:
        return (
            f"Control: every combatant now follows the battle — {mode}, "
            f"decided by {named}"
        )
    return (
        f"Control: the battle's default is now {mode} — combatants without "
        f"a mode of their own are decided by {named}"
    )


def combatant_change_message(battle, combatant, mode: str) -> str:
    """The log line for a change to one combatant's mode.

    It says the effective outcome as well as the setting, because the
    setting alone ("follows the battle") does not explain the figure's next
    decision — and explaining the next decision is the whole reason this
    goes in the log.
    """
    name = combatant.display_name
    decided_by = controller_label(battle, combatant)
    if mode == INHERIT:
        return (
            f"Control: {name} follows the battle again "
            f"({effective_mode(battle, combatant)}) — decided by {decided_by}"
        )
    if mode == MANUAL:
        return f"Control: {name} is now played by hand — decided by {decided_by}"
    return f"Control: {name} now hands its decisions to {AI_OWNER}"
