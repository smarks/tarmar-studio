"""The last message of each model call a turn makes (issue #590).

Everything before a call's last message is the same bytes on the plan and
narrate calls, and from turn to turn, so the model's prefix cache holds. What
changes -- the state block, the planning guidance, the results, the latest
message -- lives only in the last message, and is never saved to the history.
"""

import functools
from collections.abc import Sequence

from characters.rules import character_rules
from characters.skills import MAX_EXPERIENCE_YEARS_PER_RUN
from reference.rules_file import derived_from_rules

from .policy import TurnPolicy

#: How the model is told what a plan is for.
PLANNING_GUIDANCE = (
    "Plan this turn. Before the latest message is answered, decide what must "
    "be recorded, rolled or played for it, and write that as a JSON plan in "
    "the format your instructions give. Name characters by the ids listed "
    "above. A character who is not listed is started by the action that "
    'first changes them, by giving it a "new_character" field instead of '
    'a "character_id"; there is no action called new_character. A '
    "later action in the same plan refers to that character by the same "
    'name, in "character_id" or in "new_character_ref".\n'
    "- Take every value from the latest message or the list above. Never "
    "invent a value that is missing: when the message needs one it does not "
    "give (which track, how many years, which character), plan ask_gm with "
    "the question instead of that action.\n"
    "- Record the values the message states exactly as stated. Roll the ones "
    "nobody gave, with roll_attributes, roll or skill_check.\n"
    "- When the message asks for a roll, plan it now: an attack, damage or a "
    "reaction never waits on a target number or another detail (issue #376).\n"
    "- Setting a scene is recording, not only prose. When the message says "
    "where the party is, who is with them, what is in play, or how much time "
    "has passed, plan move_party, record_npc, record_object, advance_time or "
    "open_thread for it, even though no dice are needed. Record a thing the "
    "first time it is named, with the state it is in -- loose, locked, "
    "chained -- because you are handed that state back every turn and must "
    "not invent another.\n"
    "- A weapon or an object somebody is carrying is still a thing in play, "
    "not a detail of the person. Record it with record_object and say who "
    'holds it in "where" ("held by the hooded figure"). Never put what '
    "somebody is carrying in their own record: a staff changes hands and "
    "they do not.\n"
    "- add_equipment is for a character on the list, and puts an item on "
    "their own sheet. A thing somebody picks up who is not a character "
    "record moves with record_object instead: record it again with the new "
    'holder in "where".\n'
    "- When the fiction changes something already listed -- a thing is "
    "dropped, taken, opened or broken, a person is wounded, calmed or turns "
    "on the party, the light goes out -- record it again with what is now "
    "true, in the same turn it happens. A record you leave alone is read "
    "back to you next turn as though nothing happened, and you will narrate "
    "it as though nothing did (issue #615).\n"
    "- A figure about a character -- skill points, XP, age, Fatigue, what "
    "they carry -- is in the list above. Read it from there and say it. "
    "rules_lookup is for the rules of the game, never for a character's own "
    "record, and asking it for one gets you nothing (issue #614).\n"
    "- A message disputing a figure asks for no action. Somebody saying a "
    "number is wrong, or asking you to recalculate, wants to be told what "
    "the record holds -- not to have it changed. Plan nothing and say the "
    "figure. Never spend, roll or record anything the message did not ask "
    "for (issue #356).\n"
    "- When somebody asks what a rule is, plan rules_lookup and answer "
    "from the result, unless the table's rulings already hold one on that "
    "subject, which is the answer. That includes a rule a player states "
    "wrongly: look it up and say what it actually is, rather than declining "
    "to quote a number. A rule the Game Master states is his table's "
    "ruling, recorded as the house-rule line below says, not corrected. You "
    "may state any figure a lookup or a ruling gave you; you still may not "
    "state one neither did (issues #605, #760).\n"
    "- An action listed as already refused must not be planned again. The "
    "reason will not change by asking twice. Do what the refusal points to "
    "instead -- if stated scores were refused because a player's are rolled, "
    "plan roll_attributes -- or say plainly what only the Game Master can "
    "do (issue #614).\n"
    "- Small talk, questions about the rules or the world, and story that "
    "establishes nothing and needs no dice need no actions and no ask_gm: "
    "the plan is "
    '{"actions": []}.\n'
    '- When the Game Master states how something works at his table ("at my '
    'table...", "we play that...", "house rule:"), record it with '
    "record_house_rule, even when the same message also asks a question. "
    "When a ruling on that topic is already listed under the table's "
    "rulings, record the new one under the same topic and it replaces the "
    "old. A ruling he takes back is retired with retire_house_rule."
)


@derived_from_rules
@functools.cache
def planning_rules() -> str:
    """The short rules line the plan message carries.

    The attribute and Fatigue dice are the loaded canon's
    ``character-rules.json`` (issue #68), so it is built the first time a plan
    is asked for, never at import, and held for the process like the system
    prompt. The years a run may play are the code's
    (:data:`characters.skills.MAX_EXPERIENCE_YEARS_PER_RUN`).
    """
    rules = character_rules()
    return (
        f"Rules: attributes are {rules.attribute_roll.dice} each; Fatigue needs "
        f"all six attributes and the {rules.fatigue_roll.dice} Fatigue roll, "
        "which is rolled when a character is made; years of experience are "
        f"played 1 to {MAX_EXPERIENCE_YEARS_PER_RUN} at a time on a track, after "
        "the attributes are recorded, and each year can kill."
    )


#: The plan message's last instruction.
PLAN_ONLY = 'Reply with only the JSON plan, {"actions": [...]}.'

#: What a narrate message says when nothing ran.
NOTHING_RAN = "nothing was recorded or rolled this turn."

#: What the results say when the planning reply could not be read, twice.
PLAN_UNREADABLE = (
    "nothing was recorded or rolled this turn, because what this message asked "
    "for could not be worked out. Say so plainly and ask for it again in other "
    "words."
)

#: The narrate message's last instruction.
NARRATE = "Narrate this turn from the results. Write prose only, never JSON."

#: Said in the narrate message when the table has rulings (issue #600).
RULINGS_APPLY = (
    "Answer using the rulings at this table where they apply; they replace "
    "the book's rule. At most one short sentence may note that the book "
    "differs; never say to follow the book instead."
)


def plan_message(state: str, latest: str, policy: TurnPolicy) -> dict[str, str]:
    """The planning call's last message.

    Args:
        state: The state block, from :func:`gm.turn_engine.state.state_block`.
        latest: The message being answered.
        policy: The turn's policy, whose mode adds its own planning rules.
    """
    guidance = PLANNING_GUIDANCE
    if policy.planning_guidance:
        guidance = f"{guidance}\n{policy.planning_guidance}"
    return {
        "role": "user",
        "content": (
            f"{state}\n\n{guidance}\n{planning_rules()}\n{PLAN_ONLY}\n\n"
            f"Latest message: {latest}"
        ),
    }


def replan_message(errors: Sequence[str]) -> dict[str, str]:
    """The one re-ask after a planning reply that was not a plan.

    Args:
        errors: What was wrong with it, from
            :func:`gm.turn_engine.plan.read_plan`.
    """
    listed = "\n".join(f"- {error}" for error in errors)
    return {
        "role": "user",
        "content": (
            f"That reply is not a usable plan:\n{listed}\n{PLAN_ONLY} Use only "
            "the actions and fields your instructions give."
        ),
    }


def results_text(results: Sequence[str]) -> str:
    """The results as the narrate message lists them."""
    if not results:
        return NOTHING_RAN
    return "\n" + "\n".join(f"- {result}" for result in results)


def narrate_message(
    state: str,
    results: Sequence[str],
    latest: str,
    policy: TurnPolicy,
    *,
    has_rulings: bool = False,
) -> dict[str, str]:
    """The narrate call's last message.

    Args:
        state: The state block, rebuilt after the actions ran.
        results: Plain facts, refusals included, one per line.
        latest: The message being answered.
        policy: The turn's policy, whose manner closes the message.
        has_rulings: Whether the state block lists table rulings, which the
            narration is then told to answer by.
    """
    instruction = f"{NARRATE} {RULINGS_APPLY}" if has_rulings else NARRATE
    return {
        "role": "user",
        "content": (
            f"{state}\n\nServer results: {results_text(results)}\n\n"
            f"Latest message: {latest}\n\n{instruction} {policy.manner}"
        ),
    }
