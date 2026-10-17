"""Test helpers for sessions whose characters are records (issue #571).

A session's characters are ``characters.Character`` rows attached to it, each
with what the conversation calls it (``SessionCharacter``). Tests describe a
character as a mapping of its field values (the shape ``gm.ledger`` renders)
and these helpers turn that into the rows the code reads.
"""

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any
from unittest.mock import patch

from characters.constants import CHARACTER_ATTRIBUTE_FIELDS
from characters.models import Character

from .ledger import (
    DRAFT_FIELDS,
    GAME_MASTER_SKILL_POINTS_KEY,
    LABEL_KEY,
    draft_defaults,
)
from .maps import MAP_AREAS, MapScope
from .models import GMSession, SessionCharacter
from .roster import roster
from .turn_engine.messages import PLAN_ONLY


def add_character(
    session: GMSession, values: Mapping[str, Any] | None = None, *, label: str = ""
) -> Character:
    """Create a character from ``values`` and attach it to ``session``.

    Args:
        session: The session; its owner owns the character.
        values: Field values; anything missing takes the unset default, so
            attributes and the Fatigue roll stay unknown unless given.
        label: What the conversation calls the character; also read from
            ``values[LABEL_KEY]``.

    Returns:
        The saved, attached character.
    """
    values = dict(values or {})
    label = label or str(values.get(LABEL_KEY) or "")
    defaults = draft_defaults()
    fields = {field: values.get(field, defaults[field]) for field in DRAFT_FIELDS}
    fields["name"] = fields["name"] or label or "Unnamed character"
    character = Character.objects.create(user=session.user, **fields)
    session.characters.add(character)
    SessionCharacter.objects.create(
        session=session,
        character=character,
        label=label,
        game_master_skill_points=int(values.get(GAME_MASTER_SKILL_POINTS_KEY, 0) or 0),
    )
    return character


def session_with_character(
    user: Any,
    values: Mapping[str, Any] | None = None,
    *,
    label: str = "",
    **session_fields: Any,
) -> GMSession:
    """A session owned by ``user`` holding the characters ``values`` describes."""
    session_fields.setdefault("name", "s")
    session = GMSession.objects.create(user=user, **session_fields)
    values = dict(values or {})
    # Several characters, written the way the retired JSON roster nested them.
    others = values.pop("_others", {}) or {}
    defaults = draft_defaults()
    started = (
        label
        or values.get(LABEL_KEY)
        or any(
            field in values and values[field] != defaults[field]
            for field in DRAFT_FIELDS
        )
    )
    if started:
        # An unstarted mapping is a session with nobody in it yet, as an
        # empty draft was.
        add_character(session, values, label=label)
    for other in others.values():
        add_character(session, other)
    return session


def character_values(session: GMSession, who: str = "") -> dict[str, Any]:
    """The field values of the session's character ``who`` names, or its first.

    Read fresh from the database, so a test sees what a turn saved.
    """
    entries = roster(GMSession.objects.get(pk=session.pk))
    if who:
        entries = [
            entry
            for entry in entries
            if who.casefold()
            in {entry.character.name.casefold(), entry.label.casefold()}
        ]
    assert entries, f"no character {who!r} in session {session.pk}"
    return entries[0].values


def recorded(who: str = "", score: int = 18) -> dict[str, Any]:
    """A character's values once all six attributes were recorded as ``score``.

    Named and labelled ``who``, as a record started by that recording is.
    """
    return {
        **draft_defaults(),
        **dict.fromkeys(CHARACTER_ATTRIBUTE_FIELDS, score),
        "name": who,
        LABEL_KEY: who,
    }


def gm_replies(
    *narrations: Any, plans: Sequence[Any] = ()
) -> Callable[..., Iterator[str]]:
    """A stand-in for ``ollama_client.stream_chat`` in a plan-engine turn (#590).

    A turn makes a planning call and a narrate call. Planning calls take the
    next of ``plans`` (no actions once they run out); every other call takes
    the next of ``narrations``. A reply is a string, a list of tokens, or an
    exception to raise. Every message list sent is kept on ``.calls``.

    Args:
        narrations: The replies to narrate calls, in order.
        plans: The replies to planning calls, in order.
    """
    calls: list[list[dict[str, str]]] = []
    narration_replies = iter(narrations)
    plan_replies = iter(plans)

    def stream_chat(
        model: str, messages: list[dict[str, str]], **kwargs: Any
    ) -> Iterator[str]:
        calls.append(messages)
        if PLAN_ONLY in str(messages[-1].get("content", "")):
            reply = next(plan_replies, NO_ACTIONS)
        else:
            reply = next(narration_replies)
        if isinstance(reply, BaseException):
            raise reply
        return iter([reply] if isinstance(reply, str) else list(reply))

    stream_chat.calls = calls  # type: ignore[attr-defined]
    return stream_chat


#: A plan with nothing to do.
NO_ACTIONS = '{"actions": []}'


def run_actions(
    session: GMSession, *actions: dict[str, Any], is_game_master: bool | None = None
) -> list[Any]:
    """Run plan actions on ``session`` the way a turn's executor runs them (#590).

    Args:
        session: The session; its owner is the actor.
        actions: Action objects, as a plan writes them.
        is_game_master: The authority to run with; the owner's account's when
            omitted.

    Returns:
        One :class:`gm.turn_engine.engine.ActionResult` per action.
    """
    from battle.access import is_dm

    from .engine import MODE_AIDE
    from .turn_engine.executor import RecordsExecutor
    from .turn_engine.plan import read_plan
    from .turn_engine.policy import TurnPolicy
    from .turn_engine.state import open_thread_ids, roster_lines

    authority = is_dm(session.user) if is_game_master is None else is_game_master
    policy = TurnPolicy.for_turn(MODE_AIDE, is_game_master=authority)
    lines = roster_lines(session)
    reading = read_plan(
        json.dumps({"actions": list(actions)}),
        roster_ids={line.character_id for line in lines},
        policy=policy,
        thread_ids=open_thread_ids(session),
    )
    assert reading.ok, reading.errors
    executor = RecordsExecutor(policy, session, lambda: session, session.user)
    # As a turn does (TurnEngine._execute): the executor reads the plan first.
    executor.begin(reading.actions)
    return [executor.run(action, lambda event: None) for action in reading.actions]


def game_master_sets_the_foes_asked_for() -> AbstractContextManager[Any]:
    """Let a player's ``/fight`` build what they typed, in a test (issue #841).

    A player's foes are the Game Master's to set: the model answers their
    request with a ``set_foes`` action. For a test about something other than
    who chooses the foes -- the board, the battle log, seating -- this stands
    in for that answer with the request itself, read the way a Game Master's
    typed foes are.
    """
    from . import fight

    return patch(
        "gm.fight.request_set_foes",
        side_effect=lambda history, room, requested, stop_at=None: (
            fight.parse_foe_text(requested)
        ),
    )


def a_mapped_place() -> str:
    """A place name on the loaded canon's regional chart (issue #918).

    Tests that need "some place the map shows" take it from the registry
    rather than naming a place from one canon, so they hold under any canon.

    Returns:
        The first place name on the regional chart.
    """
    region = next(area for area in MAP_AREAS if area.scope is MapScope.REGION)
    return region.place_names[0]
