"""Running a plan's actions against the character records (issue #590).

Every action that changes a character goes through :mod:`characters.services`,
which locks the row, applies the rules and keeps the change history. The
action's event is recorded inside the same transaction, so a change and its
log line commit or roll back together. A character a plan starts is created
inside that transaction too: if the rules refuse its first change, it is never
created.

Each action comes back as an :class:`~gm.turn_engine.engine.ActionResult`: a
plain sentence for the narration, and the chip lines the page shows. The
sentences never mention markers, procedures or the machinery; a refusal says
what was not done and why.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import replace
from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from battle.access import is_dm
from characters import services
from characters.models import Character
from characters.purse import PurseError, format_purse, move_coin_for_reason
from characters.skill_checks import (
    attribute_check,
    skill_check,
    unconscious_refusal,
)
from characters.skills import normalize_skill_name
from common.helpers import is_owner_or_manager

from .. import maps, roster, rules_lookup
from ..models import (
    CampaignState,
    GMSession,
    SceneEntity,
    StoryThread,
    SupplyItem,
    TableRuling,
)
from ..roll_service import RollRequest, resolve_roll_series, roll_frame_payload
from . import events
from .engine import ActionResult
from .events import TurnEvent
from .outcomes import outcomes_from_run
from .plan import PlannedAction, policy_refusal
from .policy import CAMPAIGN_ACTIONS, GAME_MASTER_RECORDS_REFUSAL, TurnPolicy
from .state import (
    CampaignBlock,
    RosterLine,
    refusal_lines,
    roster_lines,
    ruling_lines,
    settled_refusals,
)

logger = logging.getLogger(__name__)

#: The prefix of a chip line for a character change.
CHIP_PREFIX = "⚙️"

#: The prefix of a chip line for a table ruling (issue #600).
RULING_CHIP_PREFIX = "📜"

#: The chip for a rule read out of the code (issue #605).
RULE_CHIP_PREFIX = "📖"

#: What each action is called while it runs, before the character's name.
ACTION_STATUS: dict[str, str] = {
    "record_attributes": "recording attributes",
    "roll_attributes": "rolling attributes",
    "roll_fatigue": "rolling Fatigue",
    "roll_literacy": "rolling literacy",
    "rule_literacy": "recording a literacy ruling",
    "roll_background": "rolling the background",
    "play_years": "playing years of experience",
    "spend_skill_points": "spending skill points",
    "add_equipment": "adding equipment",
    "roll": "rolling dice",
    "rename_character": "renaming a character",
    "set_background": "recording a background",
    "revive_character": "bringing a character back",
    "remove_character": "removing a character",
    "record_house_rule": "recording a house rule",
    "retire_house_rule": "retiring a house rule",
    "move_party": "recording where the party is",
    "advance_time": "moving the clock on",
    "adjust_supplies": "changing what they carry",
    "record_npc": "recording someone met",
    "record_object": "recording something in play",
    "open_thread": "opening a thread",
    "resolve_thread": "closing a thread",
    "rules_lookup": "looking the rule up",
    "ask_gm": "noting a question",
    "mark_dead": "recording a death",
    "award_xp": "awarding XP",
    "set_coin": "changing the purse",
    "skill_check": "rolling a skill check",
    "survival_save": "rolling a survival save",
    "attribute_check": "rolling an attribute check",
}


#: Why a session may no longer change a character another player owns
#: (issue #837). A Game Master may attach anybody's character; an account
#: that has since lost the role is held to its own.
NOT_YOURS_REFUSAL = (
    "that character belongs to another player, and only a Game Master may "
    "change another player's character"
)


class _Refused(Exception):
    """An action this turn will not run; ``str()`` says why."""


def _choice(field_name: str, value: str) -> str:
    """``value`` spelled as the field's choice it names, ignoring case.

    Anything that names no choice is returned as given, for the character's
    own validation to refuse.
    """
    for choice, _label in Character._meta.get_field(field_name).choices or ():
        if str(choice).casefold() == value.casefold():
            return str(choice)
    return value


def _called(character: Character, label: str) -> str:
    """A character's name, with the conversation's label when it differs."""
    if label and label.casefold() != character.name.casefold():
        return f"{character.name} ({label})"
    return character.name


class RecordsExecutor:
    """Runs actions for one session's turn.

    Args:
        policy: The turn's policy.
        session: The session, or ``None`` when this turn will create it.
        ensure_session: Creates the session the first time an action needs
            one, and returns it.
        actor: The account the changes are made by.
        turn_id: The ``GMTurnRecord`` this turn writes, which a ruling it
            records points back to; ``None`` when there is no record.
    """

    def __init__(
        self,
        policy: TurnPolicy,
        session: GMSession | None,
        ensure_session: Callable[[], GMSession],
        actor: Any,
        *,
        turn_id: int | None = None,
    ) -> None:
        self.policy = policy
        self.session = session
        self._ensure_session = ensure_session
        self.actor = actor
        self.turn_id = turn_id
        # Characters this plan started, by every name a later action may use.
        self._started: dict[str, int] = {}
        # The Origin a plan's set_background states, by character name (#970).
        self._stated_origins: dict[str, str] = {}
        # Those whose creation Fatigue roll a plan has already been told of.
        self._fatigue_reported: set[int] = set()
        # The Game Master authority the running action has (#837): the
        # turn's policy's, until the account is read without the role.
        self._is_game_master = policy.is_game_master

    # -- the engine's port -------------------------------------------------

    def begin(self, actions: Sequence[PlannedAction]) -> None:
        """Note the Origin the plan states for each character it starts.

        A ``set_background`` naming a ``location`` for a character the same
        plan starts (``new_character_ref``) states its Origin, which is
        recorded as a choice, not rolled (#860, #970): starting that character
        records it (``roster.start_character``), and the ``set_background``
        then restates it. A plan that states none, or names no Origin the
        table has, rolls it as the creation page does, and the roll is final
        for a player. A ``set_background`` the plan reader already refused
        states nothing: it will not run.
        """
        origins = dict(Character._meta.get_field("location").choices or ())
        self._stated_origins = {}
        for action in actions:
            if (
                action.action != "set_background"
                or action.refused
                or not action.new_character_ref
            ):
                continue
            for field_name, value in action.background:
                origin = _choice(field_name, value)
                if field_name == "location" and origin in origins:
                    self._stated_origins[action.new_character_ref.casefold()] = origin

    def roster(self) -> list[RosterLine]:
        return roster_lines(self.session)

    def campaign(self) -> CampaignBlock:
        """What the campaign holds now, for the state block (issue #579)."""
        return CampaignBlock.of(self.session)

    def rulings(self) -> list[str]:
        return ruling_lines(self.session)

    def refused(self) -> list[str]:
        """What this session already tried and was refused (issue #614)."""
        return refusal_lines(self.session, is_game_master=self.policy.is_game_master)

    def settled(self) -> dict[str, str]:
        """Actions refused for a reason that cannot change (issue #614)."""
        return settled_refusals(self.session, is_game_master=self.policy.is_game_master)

    def label(self, action: PlannedAction) -> str:
        words = ACTION_STATUS[action.action]
        who = self._who_for_status(action)
        return f"{words} for {who}" if who else words

    def run(
        self, action: PlannedAction, record: Callable[[TurnEvent], None]
    ) -> ActionResult:
        if action.action == "ask_gm":
            result = ActionResult(
                result=f"Question to ask first: {action.question}", ran=False
            )
            record(TurnEvent(events.ACTION, self._payload(action, result)))
            return result
        try:
            if action.refused:
                raise _Refused(action.refusal)
            self._check_role_still_held(action)
            if action.action == "roll":
                return self._roll(action, record)
            # A first message's session is made here, outside the action's
            # transaction (issue #908). Made inside it, as the first action
            # to need one used to make it, an action refused after that -- a
            # name the name rule refuses, torches the party is not carrying
            # -- rolled the new session back with the refused writes, while
            # the view kept the unsaved instance: the turn's commit found no
            # session and the stream raised, leaving the turn "running". The
            # turn commits a session whatever its actions do, so making it
            # first changes nothing else.
            self._session()
            with transaction.atomic():
                if action.action in ("record_house_rule", "retire_house_rule"):
                    result = self._house_rule(action, record)
                elif action.action == "rules_lookup":
                    result = self._rules_lookup(action)
                elif action.action in CAMPAIGN_ACTIONS:
                    result = self._campaign(action)
                else:
                    result = self._change(action)
                record(
                    TurnEvent(
                        events.ACTION, self._payload(action, result), visible=True
                    )
                )
            return result
        except _Refused as refusal:
            result = self._refused(action, str(refusal))
        except services.CharacterChangeError as error:
            result = self._refused(action, str(error))
        except ValidationError as error:
            result = self._refused(action, "; ".join(error.messages))
        except Character.DoesNotExist:
            result = self._refused(action, "that character no longer exists")
        logger.info("GM plan action %s refused: %s", action.action, result.error)
        record(TurnEvent(events.ACTION, self._payload(action, result), visible=True))
        return result

    # -- targets ------------------------------------------------------------

    def _entry(self, character_id: int) -> roster.RosterEntry:
        for entry in roster.roster(self.session):
            if entry.character.pk == character_id:
                return entry
        raise _Refused(f"there is no character with id {character_id} in this session")

    def _target(self, action: PlannedAction) -> tuple[roster.RosterEntry, bool]:
        """The roster entry ``action`` acts on, and whether it was just started."""
        if action.new_character is not None:
            new = action.new_character
            # The plan reader sends such a plan back; this holds when the
            # character was started after the plan was read, or by a caller
            # that read it without the names (issue #856).
            for entry in roster.roster(self.session):
                if entry.character.name.casefold() == new.called.casefold():
                    raise _Refused(
                        f"this session already has a character called "
                        f"{entry.character.name} (id {entry.character.pk}), and "
                        "a second one needs a name of its own"
                    )
            session = self._session()
            stated = [
                self._stated_origins[key]
                for key in new.keys
                if key in self._stated_origins
            ]
            character = roster.start_character(
                session, new.label, name=new.name, origin=stated[0] if stated else ""
            )
            return roster.RosterEntry(character=character, label=new.label), True
        if action.new_character_ref:
            character_id = self._started.get(action.new_character_ref.casefold())
            if character_id is None:
                raise _Refused(
                    f"{action.new_character_ref} was not created, so this was not done"
                )
            return self._entry(character_id), False
        if action.character_id is None:  # pragma: no cover - the plan requires one
            raise _Refused("no character was named")
        return self._entry(action.character_id), False

    def _session(self) -> GMSession:
        if self.session is None:
            self.session = self._ensure_session()
        return self.session

    def _started_this_turn(self, character_id: int) -> bool:
        """Whether an earlier action of this turn created the character.

        Its creation rolls -- background (#754) and Fatigue (#859) -- were
        made as it was made. Named by ``new_character_ref`` or by the id the
        turn gave it, it is the same character (issue #834).
        """
        return character_id in self._started.values()

    def _owner_now(self) -> Any:
        """The session owner's account as the database holds it now."""
        owner = self.session.user if self.session is not None else self.actor
        return get_user_model().objects.get(pk=owner.pk)

    def _check_role_still_held(self, action: PlannedAction) -> None:
        """Hold each write to the role the account has as it runs (#837).

        The turn's policy is built when the turn starts, and a turn with a
        dozen actions runs for a minute. A Game Master's authority is read
        off the account again before each action, so one demoted mid-turn is
        refused the Game Master's actions -- a death, XP, coin, a stated
        Fatigue roll, a house rule, a revival -- and his later actions run
        under a player's rules (``_is_game_master``).

        Raises:
            _Refused: The account has lost the role, and a player's policy
                refuses this action.
        """
        # Decided from the policy itself, never from a read taken when the
        # executor was built: a turn's policy is read when the request
        # arrives, and the executor is built only after a fold that may take
        # the whole budget, so a role withdrawn in between was missed
        # (second review of #837).
        self._is_game_master = self.policy.is_game_master and is_dm(self._owner_now())
        if self.policy.is_game_master and not self._is_game_master:
            as_player = replace(self.policy, is_game_master=False)
            refusal = policy_refusal(action, as_player)
            if refusal:
                raise _Refused(refusal)

    def _authorize(self, character: Character) -> None:
        """Whether this session may still write ``character`` (issue #837).

        Called by :func:`characters.services.changing` on the locked row, on
        every write, with the account's role read from the database at that
        moment. Attaching another player's character is checked once, when it
        is attached (a Game Master may); without this the session went on
        writing to it after the account lost the role.

        Raises:
            _Refused: The character is another player's and the account is
                no longer a Game Master.
        """
        if not is_owner_or_manager(self._owner_now(), character.user):
            raise _Refused(NOT_YOURS_REFUSAL)

    def _who_for_status(self, action: PlannedAction) -> str:
        if action.new_character is not None:
            return action.new_character.called
        if action.new_character_ref:
            return action.new_character_ref
        if action.character_id is None:
            return ""
        for line in self.roster():
            if line.character_id == action.character_id:
                return line.called
        return ""

    # -- actions --------------------------------------------------------------

    def _change(self, action: PlannedAction) -> ActionResult:
        """Run one character action inside the caller's transaction."""
        entry, started = self._target(action)
        character_id = entry.character.pk
        is_game_master = self._is_game_master
        common: dict[str, Any] = {"actor": self.actor, "authorize": self._authorize}
        lines: list[str] = []
        details = ""
        name = action.action
        if name == "record_attributes":
            scores = (
                {"all": action.all_score}
                if action.all_score is not None
                else dict(action.scores)
            )
            changed = services.record_attributes(
                character_id, scores, is_game_master=is_game_master, **common
            )
        elif name == "roll_attributes":
            changed = services.roll_attributes(
                character_id, is_game_master=is_game_master, **common
            )
        elif (
            name == "roll_fatigue"
            and action.fatigue_value is None
            and self._started_this_turn(character_id)
            and character_id not in self._fatigue_reported
        ):
            # A character this turn made had its Fatigue rolled as it was
            # made, as the creation page rolls it (#859, the standing rule
            # as the coordinator ruled it). The model lists the creation
            # rolls after making one; that is not a second roll, so nothing
            # is rolled again and nothing is refused. Asked for again in the
            # same turn, it is a second roll, which only a Game Master gets.
            self._fatigue_reported.add(character_id)
            return self._ran(
                action,
                _called(entry.character, entry.label),
                "Fatigue roll made when the character was made: "
                f"{entry.character.fatigue_roll}",
                [],
            )
        elif name == "roll_fatigue":
            changed = services.roll_fatigue(
                character_id,
                # A Game Master may state the roll made at his table (#802);
                # a player's plan stating one was refused before it ran.
                value=action.fatigue_value if is_game_master else None,
                once_for_players=True,
                is_game_master=is_game_master,
                **common,
            )
        elif name == "mark_dead":
            changed = services.mark_dead(
                character_id, action.cause, is_game_master=is_game_master, **common
            )
        elif name == "award_xp":
            changed = services.award_xp(
                character_id, action.xp, is_game_master=is_game_master, **common
            )
        elif name == "set_coin":
            changed = self._set_coin(action, character_id)
        elif name == "skill_check":
            changed = self._skill_check(action, character_id)
        elif name == "attribute_check":
            changed = self._attribute_check(action, character_id)
        elif name == "survival_save":
            # The sheet's own save (#839, #886): code rolls it once into the
            # dice log; a player gets one for each state of the wounds and a
            # Game Master may call another, and a failure marks the death.
            changed = services.roll_survival_save(
                character_id, is_game_master=is_game_master, **common
            )
        elif name == "roll_literacy":
            # The sheet's own service (#742): a player rolls once and a Game
            # Master may roll again, and "Consult GM" has no check to roll.
            changed = services.roll_literacy(
                character_id, is_game_master=is_game_master, **common
            )
        elif name == "rule_literacy":
            # The Game Master's ruling at "Consult GM" (#743), through the
            # sheet's own service, which refuses a player and an origin with
            # a check to roll whatever the plan says.
            changed = services.rule_literacy(
                character_id,
                action.literate,
                is_game_master=is_game_master,
                **common,
            )
        elif name == "roll_background" and self._started_this_turn(character_id):
            # A character this plan made had its background rolled as it was
            # made (#754). The model lists the creation rolls after making
            # one; that is not a second roll, so nothing is rolled twice and
            # nothing is refused: the result names what was rolled.
            return self._ran(
                action,
                _called(entry.character, entry.label),
                "background rolled when the character was made: "
                + entry.character.background_summary,
                [],
            )
        elif name == "roll_background":
            # The background a character is made with (#754), rolled again: a
            # player rolls only what nobody has recorded, once, and a Game
            # Master may roll it all again (Spencer's rulings on #749).
            changed = services.roll_background(
                character_id, is_game_master=is_game_master, **common
            )
        elif name == "play_years":
            changed = services.add_experience_years(
                character_id,
                action.track,
                action.years,
                is_game_master=is_game_master,
                **common,
            )
            outcomes = outcomes_from_run(changed.run_result or {})
            lines = [outcome.line for outcome in outcomes]
            details = " ".join(outcome.detail for outcome in outcomes)
        elif name == "spend_skill_points":
            changed = services.allocate_skill_points(
                character_id,
                action.skill,
                action.points,
                is_game_master=is_game_master,
                **common,
            )
            if changed.granted:
                roster.grant_skill_points(
                    self._session(), changed.character, changed.granted
                )
        elif name == "add_equipment":
            # A player's session buys gear from the purse, a custom item at
            # its stated price and never with combat numbers; a Game Master's
            # gives it (the #757 review, #845, #927).
            changed = services.add_equipment(
                character_id,
                action.item,
                action.quantity,
                purchase=not is_game_master,
                price=Decimal(action.price) if action.price else None,
                combat=dict(action.combat),
                **common,
            )
        elif name == "rename_character":
            changed = services.rename(
                character_id,
                str(Character.clean_field_value("name", action.name)),
                **common,
            )
        elif name == "set_background":
            changed = services.set_background(
                character_id,
                {field: _choice(field, value) for field, value in action.background},
                # The Origin lock and the literacy reroll (#741) hold here as
                # they do on the sheet.
                is_game_master=is_game_master,
                **common,
            )
            if not changed.note:
                # Every part it names was already on the record: the Origin a
                # plan states for a character it starts is recorded as the
                # character is made (#970). Said, rather than an empty note.
                changed = services.ChangeResult(
                    changed.character,
                    "background already recorded as stated: "
                    + changed.character.background_summary,
                )
        elif name == "revive_character":
            changed = services.revive(character_id, **common)
        else:  # remove_character
            # Taking a character out of the session changes the session, not
            # the character, so another player's may always be sent home.
            note = roster.remove(self._session(), entry.character)
            return self._ran(action, _called(entry.character, entry.label), note, [])
        who = _called(changed.character, entry.label)
        introduced = ""
        if started and action.new_character is not None:
            for key in action.new_character.keys:
                self._started[key] = character_id
            # What start_character rolled for it (#754), so the narration
            # has the dice's answer rather than a guess. An Origin the plan
            # states is left for its set_background to record (#970).
            rolled = (
                "a rolled background"
                if services.field_rolled(changed.character, "location")
                else "the Origin the plan states, and a rolled provenance and wealth"
            )
            introduced = (
                f"{who} is a new character, id {character_id}, with {rolled}: "
                f"{changed.character.background_summary}. "
            )
        return self._ran(action, who, changed.note, lines, details, introduced)

    def _set_coin(
        self, action: PlannedAction, character_id: int
    ) -> services.ChangeResult:
        """Give or take coin, or leave the purse at a figure (issue #802).

        Through :func:`characters.purse.move_coin_for_reason`, as the
        sheet's Game Master control is, which holds the reason to its limit
        (#928) and moves the coin with ``move_coin``, the one thing that
        changes a purse. A purse
        stated as a total is moved by the difference, worked out here from
        the record rather than by the model.

        Raises:
            _Refused: A player asking, a reason past its limit, or a purse
                that cannot cover a take.
        """
        if not self._is_game_master:  # pragma: no cover - refused before it runs
            raise _Refused(GAME_MASTER_RECORDS_REFUSAL)
        with services.changing(
            character_id, actor=self.actor, authorize=self._authorize
        ) as change:
            character = change.character
            amount = (
                Decimal(action.coin_change)
                if action.coin_change
                else Decimal(action.coin_total or 0) - character.purse
            )
            if not amount:
                change.discard()
                return services.ChangeResult(
                    character, f"the purse already holds {format_purse(character)}"
                )
            try:
                note = move_coin_for_reason(
                    character, amount, reason=action.reason or "Game Master"
                )
            except PurseError as refusal:
                raise _Refused(str(refusal)) from refusal
            change.note("purse", "", note)
        return services.ChangeResult(change.character, note)

    def _skill_check(
        self, action: PlannedAction, character_id: int
    ) -> services.ChangeResult:
        """Roll a skill check through the sheet's own rule (issue #843).

        :func:`characters.skill_checks.skill_check` rolls the 2d6 with the
        dice module, reads the aged score, applies the naturals and refuses
        Luck and unknown skills; only a Game Master sets the difficulty,
        names another attribute or states the dice. Kept in the character's
        history, as the sheet's control keeps it.
        """
        with services.changing(
            character_id, actor=self.actor, authorize=self._authorize
        ) as change:
            check = skill_check(
                change.character,
                normalize_skill_name(action.skill),
                difficulty=action.difficulty,
                attribute=action.attribute or None,
                stated_total=action.stated_total,
                is_game_master=self._is_game_master,
            )
            change.note("skill_check", "", check.line)
        return services.ChangeResult(change.character, check.line)

    def _attribute_check(
        self, action: PlannedAction, character_id: int
    ) -> services.ChangeResult:
        """Roll 3d6 ≤ an attribute through the sheet's own rule (issue #911).

        :func:`characters.skill_checks.attribute_check` reads the aged score
        and lowers the target by the injury table's penalty, so no direction
        the model writes can turn the penalty into a bonus. Only a Game
        Master states the dice, sets a modifier on the target (#953) or calls
        a check for an unconscious character. Kept in the character's
        history, as a skill check is.
        """
        with services.changing(
            character_id, actor=self.actor, authorize=self._authorize
        ) as change:
            check = attribute_check(
                change.character,
                action.attribute,
                stated_total=action.stated_total,
                modifier=action.modifier,
                is_game_master=self._is_game_master,
            )
            change.note("attribute_check", "", check.line)
        return services.ChangeResult(change.character, check.line)

    def _rules_lookup(self, action: PlannedAction) -> ActionResult:
        """Read a rule out of the code so the narration may state it (#605).

        Nothing is recorded: this changes no records and touches no dice. It
        exists so a number the model may not say from memory becomes a number
        the server handed it, which is what the prohibition in the outcomes
        section actually asks for.
        """
        try:
            said = rules_lookup.look_up(action.subject, action.track)
        except rules_lookup.UnknownRule as unknown:
            raise _Refused(str(unknown)) from unknown
        procedure = {
            "kind": action.action,
            "label": f"Rule: {said}",
            "who": "",
            "ran": True,
            "lines": [f"{RULE_CHIP_PREFIX} {said}"],
            "note": said,
        }
        return ActionResult(result=said, ran=True, note=said, procedure=procedure)

    # -- campaign state (issue #579) ----------------------------------------

    def _campaign(self, action: PlannedAction) -> ActionResult:
        """Change what the campaign holds, inside the caller's transaction.

        Where the party is, when it is, who and what is in play, and what is
        still open. Each of these was prose in the transcript before, which
        is why the narration kept reinventing them once the older turns fell
        out of the window.
        """
        session = self._session()
        if action.action in ("move_party", "advance_time"):
            return self._campaign_state(action, session)
        if action.action == "adjust_supplies":
            return self._supplies(action, session)
        if action.action in ("record_npc", "record_object"):
            return self._scene_entity(action, session)
        if action.action == "open_thread":
            thread = StoryThread.objects.create(
                session=session, text=action.text, opened_turn_id=self.turn_id
            )
            return self._noted(action, f"opened thread {thread.pk}: {thread.text}")
        return self._resolve_thread(action, session)

    def _campaign_state(
        self, action: PlannedAction, session: GMSession
    ) -> ActionResult:
        state, _ = CampaignState.objects.select_for_update().get_or_create(
            session=session
        )
        if action.action == "move_party":
            state.place = action.place
            detected = maps.detect_place(action.place)
            state.place_key = detected.place if detected else ""
            note = f"the party is at {state.where}"
            fields = ["place", "place_key"]
        else:
            if action.days:
                state.day += action.days
            if action.time_of_day:
                state.time_of_day = action.time_of_day
            note = f"it is now {state.when}"
            fields = ["day", "time_of_day"]
        state.updated_at = timezone.now()
        state.save(update_fields=[*fields, "updated_at"])
        return self._noted(action, note)

    def _supplies(self, action: PlannedAction, session: GMSession) -> ActionResult:
        """Record, change or drop one thing the party carries (issue #579).

        One row per thing, so a turn that spends the torches leaves the rope
        alone -- which the single free-text line it replaced could not
        promise. Naming a thing the session already carries changes that row,
        and only the details the action gives: an action that says how much
        there is must not silently forget the note beside it.
        """
        existing = (
            SupplyItem.objects.select_for_update()
            .filter(session=session, name__iexact=action.item)
            .first()
        )
        if action.spent:
            if existing is None:
                raise _Refused(f"the party is not carrying {action.item}")
            existing.delete()
            return self._noted(action, f"{action.item} is gone")
        given = {
            field: value
            for field, value in (("amount", action.amount), ("note", action.notes))
            if value
        }
        if existing is None:
            item = SupplyItem.objects.create(session=session, name=action.item, **given)
            return self._noted(action, f"carrying {item}")
        for field, value in given.items():
            setattr(existing, field, value)
        existing.updated_at = timezone.now()
        existing.save(update_fields=[*given, "updated_at"])
        return self._noted(action, f"carrying {existing}")

    def _scene_entity(self, action: PlannedAction, session: GMSession) -> ActionResult:
        """Record a person or a thing, updating the row when the name is known.

        Recording a name the session already holds updates that row rather
        than adding a second, so the table has one iron box and not four
        (issue #579). Only the details the action gives are changed: a turn
        that says where the box is must not silently forget its condition.
        """
        kind = (
            SceneEntity.PERSON if action.action == "record_npc" else SceneEntity.THING
        )
        existing = (
            SceneEntity.objects.select_for_update()
            .filter(session=session, name__iexact=action.name)
            .first()
        )
        given = {
            field: getattr(action, field)
            for field in ("where", "wants", "condition", "notes")
            if getattr(action, field)
        }
        if existing is None:
            entity = SceneEntity.objects.create(
                session=session, kind=kind, name=action.name, **given
            )
            return self._noted(action, entity.line.removeprefix("- "))
        if existing.kind != kind:
            raise _Refused(
                f"{existing.name} is already recorded as a {existing.get_kind_display().lower()}"
            )
        for field, value in given.items():
            setattr(existing, field, value)
        existing.updated_at = timezone.now()
        existing.save(update_fields=[*given, "updated_at"])
        return self._noted(action, existing.line.removeprefix("- "))

    def _resolve_thread(
        self, action: PlannedAction, session: GMSession
    ) -> ActionResult:
        thread = (
            StoryThread.objects.select_for_update()
            .filter(session=session, pk=action.thread_id, resolved=False)
            .first()
        )
        if thread is None:
            raise _Refused(
                f"there is no open thread with id {action.thread_id} in this session"
            )
        thread.resolved = True
        thread.resolution = action.resolution
        thread.resolved_at = timezone.now()
        thread.save(update_fields=["resolved", "resolution", "resolved_at"])
        return self._noted(action, f"closed thread {thread.pk}: {thread.text}")

    def _noted(self, action: PlannedAction, note: str) -> ActionResult:
        """A campaign change, as a chip and as a result the narration reads."""
        sentence = note[0].upper() + note[1:]
        procedure = {
            "kind": action.action,
            "label": sentence,
            "who": "",
            "ran": True,
            "lines": [f"{CHIP_PREFIX} {sentence}"],
            "note": sentence,
        }
        return ActionResult(
            result=f"{sentence}.", ran=True, note=sentence, procedure=procedure
        )

    def _house_rule(
        self, action: PlannedAction, record: Callable[[TurnEvent], None]
    ) -> ActionResult:
        """Record or retire a table ruling inside the caller's transaction.

        A ruling on a topic the table already has a ruling for retires the
        earlier one, so a topic has one active ruling at most (issue #600).
        """
        if action.action == "retire_house_rule":
            ruling = (
                TableRuling.objects.filter(
                    session=self.session, pk=action.ruling_id, active=True
                ).first()
                if self.session is not None
                else None
            )
            if ruling is None:
                raise _Refused(
                    f"there is no active ruling with id {action.ruling_id} at this "
                    "table"
                )
            self._retire(ruling, record)
            note = f"retired the ruling on {ruling.topic}: {ruling.text}"
            return self._ruling_result(action, note, f"Ruling retired, {ruling.topic}")
        session = self._session()
        replaced = [
            ruling
            for ruling in TableRuling.objects.select_for_update().filter(
                session=session, active=True
            )
            if TableRuling.same_topic(ruling.topic, action.topic)
        ]
        for ruling in replaced:
            self._retire(ruling, record)
        ruling = TableRuling.objects.create(
            session=session, topic=action.topic, text=action.rule, turn_id=self.turn_id
        )
        record(TurnEvent(events.RULING, self._ruling_payload(ruling, "recorded")))
        note = (
            f"recorded the ruling on {ruling.topic} (ruling {ruling.pk}): {ruling.text}"
        )
        if replaced:
            note += "; it replaces " + "; ".join(
                f"ruling {old.pk}: {old.text}" for old in replaced
            )
        return self._ruling_result(
            action, note, f"Table ruling, {ruling.topic}: {ruling.text}"
        )

    def _retire(self, ruling: TableRuling, record: Callable[[TurnEvent], None]) -> None:
        ruling.active = False
        ruling.retired_at = timezone.now()
        ruling.save(update_fields=["active", "retired_at"])
        record(TurnEvent(events.RULING, self._ruling_payload(ruling, "retired")))

    @staticmethod
    def _ruling_payload(ruling: TableRuling, change: str) -> dict[str, Any]:
        return {
            "change": change,
            "ruling_id": ruling.pk,
            "topic": ruling.topic,
            "rule": ruling.text,
        }

    @staticmethod
    def _ruling_result(action: PlannedAction, note: str, chip: str) -> ActionResult:
        sentence = note[0].upper() + note[1:]
        procedure = {
            "kind": action.action,
            "label": sentence,
            "who": "",
            "ran": True,
            "lines": [f"{RULING_CHIP_PREFIX} {chip}"],
            "note": sentence,
        }
        return ActionResult(
            result=f"{sentence}.", ran=True, note=sentence, procedure=procedure
        )

    def _roll(
        self, action: PlannedAction, record: Callable[[TurnEvent], None]
    ) -> ActionResult:
        """Roll the dice a plan asked for; nothing about a character changes.

        A character's roll is held to their condition as the sheet's rolls
        are (#886): the dead roll nothing, and an unconscious character
        rolls only when a Game Master calls it (named on the line). It takes
        no injury penalty (#911): nothing says which way a free-form roll is
        read, so the -1 or -2 could only be applied in a direction the model
        chose, and a backwards one made it a bonus. A roll against a score
        is a ``skill_check``, ``survival_save`` or ``attribute_check``.

        Raises:
            _Refused: A dead character, or a player's unconscious one.
        """
        roller = ""
        called_past = ""
        if action.character_id is not None:
            entry = self._entry(action.character_id)
            roller = _called(entry.character, entry.label)
            unconscious = unconscious_refusal(entry.character)
            if unconscious is not None:
                if not self._is_game_master or not entry.character.is_alive:
                    raise _Refused(unconscious)
                called_past = "unconscious"
        # A roll is kept if the turn breaks off afterwards (issue #410), and
        # keeping it needs a session the page knows about.
        self._session()
        request = RollRequest(
            expression=action.dice,
            purpose=action.purpose,
            roller=roller,
            times=action.times,
            game_master_call=called_past,
        )
        frames = []
        sentences = []
        for resolution in resolve_roll_series(request):
            frame = roll_frame_payload(resolution)
            frames.append(frame)
            record(TurnEvent(events.ROLL, frame, visible=True))
            sentences.append(str(frame["line"]).removeprefix("🎲 "))
        rolled = all("error" not in frame for frame in frames)
        return ActionResult(
            result="; ".join(sentences) + ".",
            ran=rolled,
            note="; ".join(sentences) if rolled else "",
            error=""
            if rolled
            else "; ".join(str(frame.get("error")) for frame in frames),
            rolls=tuple(frames),
        )

    # -- results --------------------------------------------------------------

    def _ran(
        self,
        action: PlannedAction,
        who: str,
        note: str,
        lines: list[str],
        details: str = "",
        introduced: str = "",
    ) -> ActionResult:
        sentence = f"{introduced}{who}: {note}."
        procedure = {
            "kind": action.action,
            "label": f"{who}: {note}",
            "who": who,
            "ran": True,
            "lines": lines or [f"{CHIP_PREFIX} {who}: {note}"],
            "note": f"{who}: {note}",
        }
        return ActionResult(
            result=f"{sentence} {details}".strip(),
            ran=True,
            note=f"{who}: {note}",
            procedure=procedure,
        )

    def _refused(self, action: PlannedAction, reason: str) -> ActionResult:
        who = self._who_for_status(action)
        what = ACTION_STATUS[action.action] + (f" for {who}" if who else "")
        procedure = {
            "kind": action.action,
            "label": what,
            "who": who,
            "ran": False,
            "lines": [f"{CHIP_PREFIX} Not done: {what}, because {reason}"],
            "error": reason,
        }
        return ActionResult(
            result=f"Not done: {what}, because {reason}.",
            ran=False,
            error=reason,
            procedure=procedure,
        )

    def _payload(self, action: PlannedAction, result: ActionResult) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": action.as_payload(),
            "ran": result.ran,
            "result": result.result,
        }
        if result.procedure is not None:
            payload["frame"] = result.procedure
        return payload
