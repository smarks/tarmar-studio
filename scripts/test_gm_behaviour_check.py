"""Unit tests for the live GM harness's own scanners and oracles (issue #373).

The harness in ``scripts/gm_behaviour_check.py`` never runs under pytest and
never reaches Ollama -- these tests exercise the pure text scanning and the
rules oracle that decide its verdicts. That matters more here than for most
helpers: the harness exists because a rules number lived in two places and one
drifted (#370), so the part of it that reads rules out of the code, and the
part that decides whether a sentence contradicts them, need guarding
themselves. Every string below is prose, not a model call.
"""

import gm_behaviour_check as harness
import pytest

from characters.combat import MATRIX
from characters.skill_rules import skill_rules
from characters.skills import survival_targets
from characters.tests.rules_fixtures import seated_skill_rules
from characters.tests.test_skill_rules import INVENTED_SKILL_RULES
from reference.rules_file import caches_derived_from_rules

# The wrong rule the GM actually stated in #370, plus the rewordings a model
# reaches for when it is wrong the same way. Each must be caught.
WRONG_RULE_WORDINGS = (
    "The Campaigner track requires a survival roll of 3d6 <= 12.",
    "You must roll 12 or under on 3d6 to survive the year.",
    "Roll 3d6 and survive on a target of 12 or less.",
    "The survival threshold is 14; roll under it or the character dies.",
    "Survival is 3d6 ≤ 12 for this track.",
)

# The right answers. None may be flagged, or the harness fails correct
# behaviour and gets ignored. The refusal to name a number that used to sit
# here was right before #605 and is a failure now: see REFUSALS below.
RIGHT_RULE_WORDINGS = (
    "You roll 3d6, add your best attribute modifier, and must reach 9 or "
    "higher to survive.",
    "A natural 18 is an automatic survival and a natural 3 an automatic death.",
    "The Campaigner survivability target is 9 and the roll must meet or beat it.",
    "Each year of prior experience rolls its own survival check on the server.",
)


@pytest.fixture
def campaigner_rule():
    """The survival rule as the harness reads it off the running code."""
    return harness.survival_rule_from_code("campaigner")


def test_survival_oracle_matches_the_rules_module(campaigner_rule):
    """The oracle's observations agree with the constants the rules declare."""
    survival_roll = skill_rules().survival_roll
    assert campaigner_rule.target == survival_targets()["campaigner"]
    assert campaigner_rule.auto_survive_roll == survival_roll.automatic_survival_total
    assert campaigner_rule.auto_death_roll == survival_roll.automatic_death_total
    assert campaigner_rule.rolls_high is True


def test_survival_oracle_reads_each_track_separately():
    """A track with a different target is observed as having one."""
    laborer = harness.survival_rule_from_code("laborer")
    assert laborer.target == survival_targets()["laborer"]
    assert laborer.target != survival_targets()["campaigner"]


def test_the_natural_survival_faces_are_the_loaded_canons_dice():
    """The faces of a natural survival are counted on the canon's dice (#70).

    Under invented rules the survival roll is 2d10, so a natural 20 is two
    tens, never three.
    """
    rule = harness.SurvivalRule(
        track="smith",
        target=25,
        rolls_high=True,
        auto_survive_roll=20,
        auto_death_roll=2,
        described="",
    )
    with seated_skill_rules(INVENTED_SKILL_RULES):
        auto_survive = harness._auto_survive_pattern(rule)
    assert auto_survive.search("a 10 and 10")
    assert auto_survive.search("10, 10")
    # On the loaded canon's three dice the same total is no pair of tens.
    assert not harness._auto_survive_pattern(rule).search("10, 10")


def test_the_track_patterns_name_the_seated_canons_tracks_and_then_the_loaded():
    """The cached patterns are built from the rules in force (issue #70).

    Each registers with ``reference.rules_file.derived_from_rules``, so seated
    rules build their own and leave none behind.
    """
    patterns = (harness._track_name, harness._figure_for_track, harness._track_figure)
    assert harness._track_name().search("the campaigner track")
    with seated_skill_rules(INVENTED_SKILL_RULES):
        assert harness._track_name().search("the sailor track")
        assert not harness._track_name().search("the campaigner track")
        assert harness._figure_for_track().search("25 for the smith")
        assert harness._track_figure().search("Smith: 25")
    assert harness._track_name().search("the campaigner track")
    assert not harness._track_name().search("the sailor track")
    for pattern in patterns:
        assert pattern in caches_derived_from_rules()


@pytest.mark.parametrize("wording", WRONG_RULE_WORDINGS)
def test_wrong_survival_rules_are_caught(wording, campaigner_rule):
    """Every way of stating #370's wrong rule contradicts the code."""
    assert harness.contradicting_survival_claims(wording, campaigner_rule)


@pytest.mark.parametrize("wording", RIGHT_RULE_WORDINGS)
def test_right_survival_rules_are_not_flagged(wording, campaigner_rule):
    """Correct answers, and a correct refusal to guess, pass cleanly."""
    assert not harness.contradicting_survival_claims(wording, campaigner_rule)


def test_combat_target_numbers_are_not_read_as_survival_targets(campaigner_rule):
    """A matrix TN in the same reply is not mistaken for the survival target."""
    text = (
        "The base target number against Heavy armour with a Piercing weapon "
        "is 22. Separately, each year's survival check is run by the server."
    )
    assert not harness.contradicting_survival_claims(text, campaigner_rule)


def test_natural_roll_sentences_do_not_read_as_a_target(campaigner_rule):
    """ "A natural 3 or less is death" names an override, not the track's target."""
    text = "A natural 3 or less is an automatic death, whatever the modifier."
    assert not harness.contradicting_survival_claims(text, campaigner_rule)


def test_a_noun_form_affirmation_is_not_read_as_a_death(campaigner_rule):
    """The answer the GM gives once it can read the rule (issue #605).

    "A natural 3 is always death, and a natural 18 is always survival" is
    correct, and was read as a contradiction because the affirmation list
    knew "always survives" and not "always survival".
    """
    said = (
        "The campaigner track does not use a 12 or under roll. A natural 3 "
        "on the dice is always death, regardless of modifiers, and a natural "
        "18 is always survival."
    )

    outcome = harness.check_not_dead_on_auto_survive("n", said, campaigner_rule)

    assert outcome.passed, outcome.found


def test_the_verdict_it_guards_against_is_still_caught(campaigner_rule):
    """The sentence #370 actually produced must still fail."""
    said = "She rolled a total of 18, which fails the check, and she dies."

    outcome = harness.check_not_dead_on_auto_survive("n", said, campaigner_rule)

    assert not outcome.passed


def test_death_on_the_automatic_survival_roll_is_caught(campaigner_rule):
    """#370 itself: an 18 narrated as a death."""
    narration = (
        "She rolled 6, 6, 6 for a total of 18, which fails the check, and she "
        "dies on the road outside Vantage."
    )
    outcome = harness.check_not_dead_on_auto_survive("x", narration, campaigner_rule)
    assert not outcome.passed
    assert "dies" in outcome.evidence


def test_survival_narration_without_a_death_passes(campaigner_rule):
    """Prose that does not assert a death is not flagged."""
    narration = "The year closes with Wren still on her feet, road-worn."
    assert harness.check_not_dead_on_auto_survive(
        "x", narration, campaigner_rule
    ).passed


def test_matrix_question_comes_from_the_matrix():
    """The lookup the harness asks is a real cell, and its TN is the rare one."""
    question = harness.matrix_question_from_code()
    assert MATRIX[question.weapon_class][question.armour_tier] == question.target_number
    values = [value for row in MATRIX.values() for value in row.values()]
    assert values.count(question.target_number) == min(
        values.count(value) for value in values
    )


def test_matrix_question_says_unarmoured_rather_than_none_armour():
    """The "None" tier is asked about in words a person would use."""
    question = harness.MatrixQuestion("Piercing", "None", 11)
    assert "unarmoured" in question.question
    assert "None armour" not in question.question


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Wren has 6 skill points available.", [6]),
        ("There are 4 skill points remaining to spend.", [4]),
        ("She has 5 points left.", [5]),
        ("She spent one point on sword.", []),
    ],
)
def test_skill_point_claims(text, expected):
    """Only "available"-shaped figures are read as the free-point total."""
    assert [figure for figure, _ in harness.skill_point_claims(text)] == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The goblin rolled a 14.", [14]),
        ("The dice came up 18.", [18]),
        ("You need to roll a 15 to hit.", []),
        ("It rolled a total of 9.", [9]),
    ],
)
def test_rolled_figures_reads_outcomes_not_targets(text, expected):
    """ "Rolled a 14" is a provenance claim; "need to roll a 15" is a target."""
    assert [figure for figure, _ in harness.rolled_figures(text)] == expected


def test_attribute_figures():
    """Attribute values are read however the reply spells the attribute."""
    text = "STR 13, DEX: 12, Constitution is 14"
    assert [(name, value) for name, value, _ in harness.attribute_figures(text)] == [
        ("STR", 13),
        ("DEX", 12),
        ("CON", 14),
    ]


def test_numbered_items():
    """A numbered list is recognised; a bare paragraph is not."""
    assert len(harness.numbered_items("1. Ask\n2. Listen\n3. Leave\n")) == 3
    assert harness.numbered_items("You could ask, or listen, or leave.") == []


def test_readjudication_instruction_is_caught():
    """Telling the DM to make the confirmation roll he ruled away (#353)."""
    hits = harness.readjudication_hits(
        "You should still make a confirmation roll before applying the crit."
    )
    assert hits


def test_the_permitted_note_is_not_readjudication():
    """One note that the written rule differs is allowed, and not flagged."""
    assert not harness.readjudication_hits(
        "The book normally asks for a confirmation roll; by your ruling the "
        "damage is doubled outright."
    )


def test_following_the_ruling_is_not_readjudication():
    """Naming the Game Master's ruling and following it passes (#380)."""
    assert not harness.readjudication_hits(
        "Since you have ruled that there is no confirmation roll, the hit is a "
        "standard critical."
    )


def test_a_negated_instruction_does_not_hide_a_real_one():
    """The guard is per sentence: a real instruction beside it is still caught."""
    hits = harness.readjudication_hits(
        "You have ruled there is no confirmation roll. "
        "You will still need to make a confirmation roll first."
    )
    assert len(hits) == 1


def test_every_check_kind_declares_a_required_pass_rate():
    """No check can be added with a threshold nobody chose."""
    kinds = {
        value
        for name, value in vars(harness.CheckKind).items()
        if not name.startswith("_") and isinstance(value, str)
    }
    assert kinds == set(harness.REQUIRED_PASS_RATE)
    assert harness.REQUIRED_PASS_RATE[harness.CheckKind.STRUCTURE] == 1.0
    assert harness.REQUIRED_PASS_RATE[harness.CheckKind.RUBRIC] < 1.0


def test_scenarios_cover_every_bug_the_harness_exists_for():
    """The issues #373 lists are each carried by at least one scenario."""
    scenarios = harness.build_scenarios()
    covered = {issue for scenario in scenarios for issue in scenario.issues}
    assert {"#342", "#352", "#353", "#356", "#363", "#370"} <= covered
    assert len({scenario.key for scenario in scenarios}) == len(scenarios)


def test_play_mode_now_runs_as_a_game_master_who_asked_to_play():
    """#372 has landed: the play scenario points at the account that was stuck.

    This test used to assert the opposite -- that play scenarios had to run as
    a player, because a DM could not reach the play prompt at all. That was
    the bug. The scenario now runs as a DM in play mode, which is both the
    case Spencer reported and the case the aide prompt fails.
    """
    play = next(s for s in harness.build_scenarios() if s.key == "play_manner")
    assert play.persona is harness.GAME_MASTER_PLAYING
    assert play.persona.is_dm is True
    assert play.persona.mode == harness.MODE_PLAY
    assert "#372" in play.issues


def test_both_of_a_game_masters_modes_are_covered():
    """The role is held fixed and the mode varied, which is the point of #372."""
    scenarios = {scenario.key: scenario for scenario in harness.build_scenarios()}
    assert scenarios["aide_manner"].persona is harness.GAME_MASTER
    assert scenarios["aide_manner"].persona.mode == harness.MODE_AIDE
    assert scenarios["play_manner"].persona.mode == harness.MODE_PLAY
    assert (
        scenarios["aide_manner"].persona.username
        == scenarios["play_manner"].persona.username
    )


def test_each_persona_expects_the_prompt_its_role_and_mode_produce():
    """check_mode's four states, named the way the engine names them."""
    from gm.engine import (
        aide_system_prompt,
        dm_play_system_prompt,
        dm_system_prompt,
        system_prompt,
    )

    assert harness.PLAYER.expected_prompt is system_prompt()
    assert harness.GAME_MASTER.expected_prompt is dm_system_prompt()
    assert harness.GAME_MASTER_PLAYING.expected_prompt is dm_play_system_prompt()
    assert harness.prompt_name(aide_system_prompt()) == "aide_system_prompt()"
    assert harness.prompt_name(dm_play_system_prompt()) == "dm_play_system_prompt()"
    assert (
        harness.prompt_name("nothing") == "a prompt that is none of the known builders"
    )


def test_a_plan_that_failed_twice_fails_its_check():
    """#590: one rejected planning reply is the re-ask; two ran no actions."""
    once = harness.TurnRecord("m", "r", (), (), "", 1.0, plan_rejections=(("x",),))
    twice = harness.TurnRecord(
        "m", "r", (), (), "", 1.0, plan_rejections=(("x",), ("y",))
    )
    transcript = harness.Transcript(harness.PLAYER, 1, (once,), "p")
    assert harness.check_plans_read(transcript).passed
    failing = harness.Transcript(harness.PLAYER, 1, (once, twice), "p")
    outcome = harness.check_plans_read(failing)
    assert not outcome.passed
    assert "[2]" in outcome.evidence


def test_a_json_plan_in_the_reply_is_a_leak():
    turn = harness.TurnRecord("m", '{"actions": []}', (), (), "", 1.0)
    transcript = harness.Transcript(harness.PLAYER, 1, (turn,), "p")
    assert not harness.check_no_leaked_markers(transcript).passed


def test_check_tally_reports_a_flake_rate():
    """A check that held twice in three runs is reported flaky, not green."""
    tally = harness.CheckTally(name="x", kind=harness.CheckKind.RUBRIC)
    for passed in (True, False, True):
        tally.outcomes.append(
            harness.verdict("x", harness.CheckKind.RUBRIC, passed, "want", "got")
        )
    assert tally.passes == 2
    assert tally.flaky is True
    assert tally.flake_rate == pytest.approx(1 / 3)
    assert tally.met is True  # a rubric check needs two in three


def test_a_structural_check_that_flakes_once_does_not_meet_its_threshold():
    """Structure and provenance are exact: one failure is a failure."""
    tally = harness.CheckTally(name="x", kind=harness.CheckKind.STRUCTURE)
    for passed in (True, True, False):
        tally.outcomes.append(
            harness.verdict("x", harness.CheckKind.STRUCTURE, passed, "want", "got")
        )
    assert tally.met is False
    assert tally.flake_rate == pytest.approx(1 / 3)


def test_explaining_both_natural_rolls_is_not_a_death_verdict(campaigner_rule):
    """The GM listing both overrides mentions death, and must still pass.

    Caught as a false positive on the harness's first live run: a whole-reply
    scan flagged "a natural 3 is the only automatic death" beside a correct
    statement about the 18.
    """
    reply = (
        "The server holds the target for that track.\n\n"
        "*(Note: A natural 3 is the only automatic death. A roll of 18 is the "
        "only automatic survival.)*"
    )
    assert harness.check_not_dead_on_auto_survive("x", reply, campaigner_rule).passed


def test_refusing_to_give_a_verdict_is_not_a_death_claim(campaigner_rule):
    """The answer SECTION_PROCEDURES asks for passes (#380, second instance)."""
    reply = (
        "The server holds the specific survival thresholds for each experience "
        "track, and I cannot quote those numbers or confirm whether a roll "
        "succeeded or failed. I do not have access to the hidden targets, so I "
        "cannot verify if a roll of 18 on three sixes resulted in death or "
        "survival for your friend's character."
    )
    assert harness.check_not_dead_on_auto_survive("x", reply, campaigner_rule).passed


def test_a_refusal_beside_a_death_verdict_does_not_excuse_it(campaigner_rule):
    """Only the refusing sentence is set aside; the verdict next to it counts."""
    reply = "I cannot quote the target. She rolled an 18, which fails, and she dies."
    assert not harness.check_not_dead_on_auto_survive(
        "x", reply, campaigner_rule
    ).passed


def test_a_death_paragraph_that_never_names_the_roll_is_not_flagged(campaigner_rule):
    """The check is one-sided: it judges only paragraphs naming the roll."""
    reply = "Her fourth year went badly and she died on the road."
    assert harness.check_not_dead_on_auto_survive("x", reply, campaigner_rule).passed


def test_the_faces_that_make_the_automatic_roll_count_as_naming_it(campaigner_rule):
    """ "6, 6 and 6" is how a player says 18, and is read as naming it."""
    reply = "The dice showed 6, 6 and 6, so Wren dies there in the mud."
    assert not harness.check_not_dead_on_auto_survive(
        "x", reply, campaigner_rule
    ).passed


def test_markdown_emphasis_does_not_hide_a_figure():
    """ "**1** free skill point" is the same claim as "1 free skill point"."""
    assert harness.skill_point_claims(
        "Wren has **1** free skill point available to spend."
    ) == [(1, "Wren has 1 free skill point available to spend.")]


def test_earned_and_spent_figures_are_not_read_as_free_ones():
    """The account's other two numbers are not claims about what is free.

    From a live run: the GM answered correctly with 1 free, and a first cut of
    the scanner read the "earned 2" beside it as a contradicting claim.
    """
    reply = (
        "According to the ledger, Wren has **1** free skill point available "
        "to spend.\n\nShe has earned 2 skill points from her XP thresholds, "
        "spent 1 on **Sword I**, leaving 1 remaining."
    )
    assert [figure for figure, _ in harness.skill_point_claims(reply)] == [1]


def test_a_sequencing_statement_is_not_a_roll_disclaimer():
    """ "I cannot roll the damage yet" is table talk, not #363's claim."""
    reply = (
        "I cannot roll the damage yet because the attack result has not been "
        "determined."
    )
    assert harness.check_no_roll_disclaimer(reply).passed


def test_a_capability_disclaimer_without_sequencing_still_fails():
    """The #363 wording, with nothing to explain it away."""
    reply = "I cannot roll dice. The server is the only entity that rolls dice."
    assert not harness.check_no_roll_disclaimer(reply).passed


@pytest.mark.parametrize(
    "reply",
    [
        "I do not have access to a random number generator.",
        "Please roll 3d6 for Strength and tell me the result.",
        "As an AI, I can only record what you tell me.",
    ],
)
def test_hard_roll_disclaimers_are_never_excused(reply):
    """No context makes "the server has no dice" true, so none of these pass."""
    assert not harness.check_no_roll_disclaimer(f"{reply} Not yet, anyway.").passed


def test_a_conditional_request_for_information_is_not_an_offer_of_more():
    """ "If you want me to resolve it, I need the range" is the permitted question."""
    reply = "If you want me to resolve it mechanically, I need the range."
    assert harness.check_no_phrases("x", reply, harness.INVITATION_PHRASES).passed


def test_an_offer_of_more_is_still_caught():
    """The offers #353 forbids are unaffected by that narrowing."""
    for offer in (
        "Would you like me to roll that?",
        "What do you do?",
        "Shall I roll?",
    ):
        assert not harness.check_no_phrases(
            "x", offer, harness.INVITATION_PHRASES
        ).passed


def test_a_cost_figure_is_not_a_free_total():
    """ "You need 2 more points to reach II. You only have 1." states one free point.

    A live run failed on this: the cost figure beside a correct answer was
    read as a second, contradicting claim about the free total.
    """
    reply = (
        "Wren has 1 free skill point available to spend. Sword II is 3 pts "
        "total. So to go from I to II, you need 2 more points. You only have 1."
    )
    assert [figure for figure, _ in harness.skill_point_claims(reply)] == [1]


def test_no_access_to_your_session_counts_as_not_knowing():
    """The wording the GM actually used when it correctly did not know (#353)."""
    reply = (
        "I do not have access to your previous session's events or the "
        "specific circumstances of the murder."
    )
    assert harness.check_any_keyword("x", reply, harness.UNKNOWING_KEYWORDS).passed


def test_kills_inside_skills_is_not_a_death(campaigner_rule):
    """A substring scan read "her skills/equipment" as the character dying.

    Verdict words are matched on whole words for exactly this reason.
    """
    paragraph = "The dice gave 18. Skip weight and discuss her skills/equipment."
    assert harness.check_not_dead_on_auto_survive(
        "x", paragraph, campaigner_rule
    ).passed
    assert harness.find_words(paragraph, ("kills",)) == []
    assert harness.find_phrases(paragraph, ("kills",))  # the substring is there


def test_find_words_still_finds_the_word_on_its_own():
    """Whole-word matching does not lose the hits it is supposed to make."""
    assert harness.find_words("The blow kills him outright.", ("kills",))
    assert harness.find_words("She does not survive the year.", ("does not survive",))


# -- the twins tracks rule, and reading dice out of whatever frame carried
# them (issues #604 and the dice-death flake) ------------------------------


class FakeLedger:
    """Just the two fields the twins tracks rule reads."""

    def __init__(self, tracks: tuple[str, ...], is_alive: bool = True) -> None:
        self.experience_tracks = tracks
        self.is_alive = is_alive


def tracks_verdict(*ledgers: FakeLedger) -> bool:
    """The harness's own rule, called rather than copied."""
    return harness.twins_ran_their_own_tracks(list(ledgers))


def test_two_living_twins_each_run_their_own_track():
    assert tracks_verdict(
        FakeLedger(("underworld", "campaigner")),
        FakeLedger(("magic", "campaigner")),
    )


def test_a_twin_the_dice_killed_takes_no_campaigner_years():
    """A natural 3 kills, and a dead character is rightly refused more years.

    This is the rules working. It used to read as a failed check on roughly
    one run in twenty, which is how a harness teaches you to ignore it.
    """
    assert tracks_verdict(
        FakeLedger(("underworld",), is_alive=False),
        FakeLedger(("magic", "campaigner")),
    )


def test_a_living_twin_is_not_excused_by_the_other_twins_death():
    """The reason the rule is asked per character and not of the pair."""
    assert not tracks_verdict(
        FakeLedger(("underworld",), is_alive=False),
        FakeLedger(("magic",)),
    )


def test_both_twins_on_the_same_track_is_still_caught():
    """The bug the check exists for (#390, #391)."""
    assert not tracks_verdict(
        FakeLedger(("underworld", "campaigner")),
        FakeLedger(("underworld", "campaigner")),
    )


def test_a_twin_who_took_the_others_years_is_still_caught():
    assert not tracks_verdict(
        FakeLedger(("underworld", "magic", "campaigner")),
        FakeLedger(("campaigner",)),
    )


def test_a_twin_with_no_years_is_still_caught():
    assert not tracks_verdict(
        FakeLedger(()),
        FakeLedger(("magic", "campaigner")),
    )


def _transcript(gm_rolls=(), procedures=()) -> harness.Transcript:
    """One turn carrying the frames a check reads."""
    turn = harness.TurnRecord(
        message="",
        reply="",
        gm_rolls=tuple(gm_rolls),
        procedures=tuple(procedures),
        error="",
        seconds=0.0,
    )
    return harness.Transcript(
        persona=harness.Persona("dm", "dm", True, "aide"),
        session_id=None,
        turns=(turn,),
        system_prompt="",
    )


def test_server_dice_reads_a_gm_roll_frame():
    transcript = _transcript(
        gm_rolls=[{"expression": "2d6", "rolls": "[3, 4]", "total": 7}]
    )

    (die,) = transcript.server_dice
    assert (die.expression, die.faces, die.total, die.kind) == (
        "2d6",
        (3, 4),
        7,
        "roll",
    )


def test_server_dice_reads_the_dice_a_procedure_reported():
    """Creation rolls arrive this way, and were invisible before (#604)."""
    note = (
        "Wren: rolled the six attributes: strength 3d6: [5, 6, 1] = 12; "
        "dexterity 3d6: [5, 1, 5] = 11"
    )
    transcript = _transcript(procedures=[{"kind": "roll_attributes", "note": note}])

    dice = transcript.server_dice
    assert [die.total for die in dice] == [12, 11]
    assert {die.kind for die in dice} == {"roll_attributes"}


def test_server_dice_keeps_the_kind_so_survival_is_not_read_as_creation():
    """Both are 3d6; a check that means one of them has to say which."""
    survival = "underworld year 1 survival: 3d6 [4, 6, 2] = 12, modifier +5"
    transcript = _transcript(procedures=[{"kind": "play_years", "note": survival}])

    (die,) = transcript.server_dice
    assert die.kind == "play_years"
    assert die.total == 12


# Once the GM could read the rule out of the code (issue #605) it stopped
# declining and started correcting: "the campaigner survival roll is not 12
# or under, the target is 9". The parser read the denied 12 as an adopted
# target, so a correct answer failed the check that guards #370. Both
# directions are pinned here: a denial must not read as a claim, and every
# wrong claim the guard exists for must still fail.
DENIALS_THAT_ARE_NOT_CLAIMS = (
    "The campaigner survival roll is not 12 or under. For the campaigner "
    "track, that target is 9.",
    "The survival target is not 9.",
    "Survival does not use a 12 or under roll, the target is 9.",
    "A natural 3 is always death and a natural 18 is always survival.",
    "Survival is 3d6 plus your best modifier against the target of 9.",
    "To survive you need 9 or more.",
)

CLAIMS_THAT_ARE_STILL_WRONG = (
    "The campaigner track requires a survival roll of 3d6 <= 12.",
    "You must roll 12 or under to survive.",
    "Survival is not 12 or under; you survive on 12 or under.",
    "Survival is not 12 -- the survivability target is 14.",
    "For survival the target is 14.",
    "Survival means rolling 12 or under on 3d6.",
)


@pytest.mark.parametrize("said", DENIALS_THAT_ARE_NOT_CLAIMS)
def test_a_denied_figure_is_not_a_claim(said, campaigner_rule):
    assert harness.contradicting_survival_claims(said, campaigner_rule) == []


@pytest.mark.parametrize("said", CLAIMS_THAT_ARE_STILL_WRONG)
def test_a_wrong_claim_still_fails_however_it_is_worded(said, campaigner_rule):
    assert harness.contradicting_survival_claims(said, campaigner_rule)


# The automatic-survival roll, in both directions. The affirmations the model
# reaches for vary run to run ("always survives", "always survival", "grants
# survival"), so the structure is read rather than the wording: a death
# attached to the automatic-death roll is not a verdict on the survival one.
CORRECT_STATEMENTS_OF_THE_NATURAL_ROLLS = (
    "A natural 18 on the dice always grants survival, while a natural 3 "
    "always results in death, regardless of modifiers.",
    "A natural 3 is always death, and a natural 18 is always survival.",
    "On a natural 18 the character survives whatever the target.",
)

DEATH_VERDICTS_ON_THE_SURVIVAL_ROLL = (
    "She rolled a total of 18, which fails the check, and she dies.",
    "Your friend rolled 18. The character died.",
    "A natural 18 means the character perishes.",
    "A natural 18 leaves the character dead.",
)


@pytest.mark.parametrize("said", CORRECT_STATEMENTS_OF_THE_NATURAL_ROLLS)
def test_stating_both_natural_rolls_correctly_passes(said, campaigner_rule):
    assert harness.check_not_dead_on_auto_survive("n", said, campaigner_rule).passed


@pytest.mark.parametrize("said", DEATH_VERDICTS_ON_THE_SURVIVAL_ROLL)
def test_a_death_on_the_survival_roll_still_fails(said, campaigner_rule):
    """ "perishes" and "dead" were missed until 2026-09-19: the word list had
    "perish", and whole-word matching let the inflection through."""
    assert not harness.check_not_dead_on_auto_survive("n", said, campaigner_rule).passed


# -- literacy (issue #742) ---------------------------------------------------


class LiteracyLedger:
    """Just the literacy line and the Origin the literacy checks read."""

    def __init__(self, literacy: str, location: str = "Village") -> None:
        self.literacy = literacy
        self.location = location


def _literacy_turn(
    reply="", procedures=(), literacy="", location=""
) -> harness.TurnRecord:
    ledger = (
        LiteracyLedger(literacy, location or "Village")
        if literacy or location
        else None
    )
    return harness.TurnRecord(
        message="",
        reply=reply,
        gm_rolls=(),
        procedures=tuple(procedures),
        error="",
        seconds=0.0,
        ledger=ledger,  # type: ignore[arg-type]
    )


def _two_turns(first, second, persona=None) -> harness.Transcript:
    persona = persona or harness.PLAYER
    return harness.Transcript(
        persona=persona,
        session_id=None,
        turns=(first, second),
        system_prompt=persona.expected_prompt,
    )


ROLLED_LINE = "3d6 ≤ INT − 2, Illiterate (rolled 12 > 11 − 2)"
LITERACY_ROLL = {"kind": "roll_literacy", "ran": True, "label": "Wren: rolled"}


def _failed(outcomes) -> list[str]:
    return [outcome.name for outcome in outcomes if not outcome.passed]


def test_a_player_whose_second_roll_is_declined_passes():
    """Corrected, not refused: nothing planned, the roll stands, and said."""
    transcript = _two_turns(
        _literacy_turn(procedures=[LITERACY_ROLL], literacy=ROLLED_LINE),
        _literacy_turn(
            reply="Wren's roll stands: she is illiterate.", literacy=ROLLED_LINE
        ),
    )
    assert _failed(harness.check_literacy_player(transcript)) == []


def test_a_player_whose_second_roll_was_planned_and_refused_passes():
    refused = {"kind": "roll_literacy", "ran": False, "label": "rolling literacy"}
    transcript = _two_turns(
        _literacy_turn(procedures=[LITERACY_ROLL], literacy=ROLLED_LINE),
        _literacy_turn(
            reply="Only a Game Master can have it rolled again; Wren cannot read.",
            procedures=[refused],
            literacy=ROLLED_LINE,
        ),
    )
    assert _failed(harness.check_literacy_player(transcript)) == []


def test_a_second_roll_that_ran_is_caught():
    transcript = _two_turns(
        _literacy_turn(procedures=[LITERACY_ROLL], literacy=ROLLED_LINE),
        _literacy_turn(
            reply="Rolled again: literate now.",
            procedures=[LITERACY_ROLL],
            literacy="3d6 ≤ INT − 2, Literate (rolled 5 ≤ 11 − 2)",
        ),
    )
    assert _failed(harness.check_literacy_player(transcript)) == [
        "a player's second roll did not happen"
    ]


def test_a_literacy_roll_the_server_never_made_is_caught():
    transcript = _two_turns(
        _literacy_turn(reply="You rolled a 9, literate!"),
        _literacy_turn(reply="She is literate."),
    )
    assert "the server rolled the literacy check when asked" in _failed(
        harness.check_literacy_player(transcript)
    )


def test_a_game_masters_origin_change_that_rerolled_passes():
    moved = {
        "kind": "set_background",
        "ran": True,
        "note": "Gorn: location set to City; literacy rerolled because Origin "
        "changed from Village to City: rolled the literacy check (3d6 ≤ INT)",
    }
    transcript = _two_turns(
        _literacy_turn(procedures=[LITERACY_ROLL], location="Village"),
        _literacy_turn(reply="Gorn grew up in the city.", procedures=[moved]),
        persona=harness.GAME_MASTER_PLAYING,
    )
    assert _failed(harness.check_literacy_game_master(transcript)) == []


def test_a_rolled_origin_over_the_stated_village_is_caught():
    """The first #754 board: "He grew up in a village", and the record held
    the Origin the server rolled when it made him."""
    moved = {
        "kind": "set_background",
        "ran": True,
        "note": "location set to City; literacy rerolled because Origin "
        "changed from Rural to City: rolled",
    }
    transcript = _two_turns(
        _literacy_turn(procedures=[LITERACY_ROLL], location="Rural"),
        _literacy_turn(reply="Done.", procedures=[moved]),
        persona=harness.GAME_MASTER_PLAYING,
    )
    assert _failed(harness.check_literacy_game_master(transcript)) == [
        "the Origin the Game Master stated is the one on the record"
    ]


def test_an_origin_change_with_no_reroll_is_caught():
    moved = {"kind": "set_background", "ran": True, "note": "location set to City"}
    transcript = _two_turns(
        _literacy_turn(procedures=[LITERACY_ROLL], location="Village"),
        _literacy_turn(reply="Done.", procedures=[moved]),
        persona=harness.GAME_MASTER_PLAYING,
    )
    assert _failed(harness.check_literacy_game_master(transcript)) == [
        "moving the Origin to City rolled literacy again against it"
    ]


# -- a character built in chat rolls its background (issue #754) -------------


def _background_ledger(**values):
    from gm.ledger import DraftLedger, draft_defaults

    return DraftLedger.from_draft(
        {
            **draft_defaults(),
            "location": "Rural",
            "skills": {"survival": 1},
            **values,
        }
    )


def _background_turn(reply="", procedures=(), ledger=None) -> harness.TurnRecord:
    return harness.TurnRecord(
        message="",
        reply=reply,
        gm_rolls=(),
        procedures=tuple(procedures),
        error="",
        seconds=0.0,
        ledger=ledger,
    )


def _three_turns(first, second, third) -> harness.Transcript:
    return harness.Transcript(
        persona=harness.PLAYER,
        session_id=None,
        turns=(first, second, third),
        system_prompt=harness.PLAYER.expected_prompt,
    )


BACKGROUND_ROLL = {"kind": "roll_background", "ran": True, "label": "Wren: rolled"}
REROLLED = {"wealth_level": "Merchant", "starting_coin": 140}


def _background_transcript(second_reply, second_procedures=(), second_ledger=None):
    return _three_turns(
        _background_turn(ledger=_background_ledger()),
        _background_turn(
            reply=second_reply,
            procedures=second_procedures,
            ledger=second_ledger or _background_ledger(),
        ),
        _background_turn(
            reply="Wren's background is rolled again.",
            procedures=[BACKGROUND_ROLL],
            ledger=_background_ledger(**REROLLED),
        ),
    )


def test_a_background_reroll_declined_in_so_many_words_passes():
    transcript = _background_transcript(
        "Wren's background stays as it was rolled; only the Game Master can "
        "roll it again."
    )
    assert _failed(harness.check_chat_built_background(transcript)) == []


def test_a_background_reroll_planned_and_refused_passes():
    refused = {"kind": "roll_background", "ran": False, "label": "rolling"}
    transcript = _background_transcript(
        "She is a Rural commoner of Moderate means.", second_procedures=[refused]
    )
    assert _failed(harness.check_chat_built_background(transcript)) == []


def test_a_players_second_background_roll_that_ran_is_caught():
    transcript = _background_transcript(
        "Done: Wren is a merchant now.",
        second_procedures=[BACKGROUND_ROLL],
        second_ledger=_background_ledger(**REROLLED),
    )
    assert _failed(harness.check_chat_built_background(transcript)) == [
        "a player's second background roll did not happen",
        "it tells the player the background stands",
    ]


def test_a_background_nobody_rolled_is_caught():
    transcript = _three_turns(
        _background_turn(
            ledger=_background_ledger(
                location="Village", skills={}, wealth_recorded=False
            )
        ),
        _background_turn(reply="It stands.", ledger=_background_ledger()),
        _background_turn(procedures=[BACKGROUND_ROLL], ledger=_background_ledger()),
    )
    assert _failed(harness.check_chat_built_background(transcript)) == [
        "the server rolled the background when it made the character",
        "the character holds the skill its origin grants",
    ]


def test_a_game_master_turn_with_no_reroll_is_caught():
    transcript = _three_turns(
        _background_turn(ledger=_background_ledger()),
        _background_turn(reply="It stands.", ledger=_background_ledger()),
        _background_turn(
            reply="I cannot allow that.", ledger=_background_ledger(skills={})
        ),
    )
    assert _failed(harness.check_chat_built_background(transcript)) == [
        "the Game Master's turn rolled the background again",
        "after the reroll the record still holds its origin's skill",
        "it does not refuse, cap, bargain or lecture the Game Master",
    ]


def test_a_standard_answer_is_not_a_background_that_stands():
    """ "stand" is a stem, read whole-word; "standard" is not its inflection."""
    transcript = _background_transcript("That is a standard request.")
    assert _failed(harness.check_chat_built_background(transcript)) == [
        "it tells the player the background stands"
    ]


def _record(origin, skills=()):
    return harness.BackgroundRecord(
        origin=origin,
        provenance=("commoner", "Laborer"),
        wealth=("Moderate", 100),
        recorded=(True, True, True),
        skills=tuple(skills),
        line="",
    )


def _made(*actions) -> harness.TurnRecord:
    return harness.TurnRecord(
        message="",
        reply="",
        gm_rolls=(),
        procedures=(),
        error="",
        seconds=0.0,
        plans=(tuple(actions),),
    )


MAKES_WREN = {"action": "roll_attributes", "new_character": {"name": "Wren"}}
SETS_BACKGROUND = {
    "action": "set_background",
    "new_character_ref": "Wren",
    "location": "Village",
    "wealth": "Subsistence",
}


def _scenario(states_background=()):
    return harness.Scenario(
        key="k",
        title="t",
        issues=(),
        persona=harness.PLAYER,
        turns=(),
        check=lambda transcript: [],
        states_background=states_background,
    )


#: Board 3's ledger_numbers plan (#754), as the harness recorded it: a whole
#: background set for a player who stated none.
LEDGER_NUMBERS_PLAN = (
    {"action": "roll_attributes", "new_character": {"name": "Wren", "label": ""}},
    {"action": "roll_fatigue", "new_character_ref": "Wren"},
    {
        "action": "set_background",
        "new_character_ref": "Wren",
        "background": {
            "provenance_class": "Commoner",
            "location": "Village",
            "wealth_level": "Subsistence",
        },
    },
)


@pytest.mark.parametrize(
    "states_background", [(), ("location",)], ids=["states none", "states location"]
)
def test_board_3s_invented_background_fails_under_either_scenario(states_background):
    """Under a scenario stating nothing it is all invented; under one stating
    the Origin, the provenance and wealth still are."""
    transcript = _two_turns(_made(*LEDGER_NUMBERS_PLAN), _made())
    assert _failed(
        harness.check_no_unstated_background(_scenario(states_background), transcript)
    ) == ["it sets no background nobody stated"]


def test_a_stated_origin_recorded_alone_passes():
    stated = {
        "action": "set_background",
        "new_character_ref": "Gorn",
        "background": {"location": "Village"},
    }
    transcript = _two_turns(_made(MAKES_WREN, stated), _made())
    outcomes = harness.check_no_unstated_background(
        _scenario(("location",)), transcript
    )
    assert [outcome.passed for outcome in outcomes] == [True]


def test_a_background_nobody_stated_is_caught_in_any_scenario():
    """Board 3 of #754: the overreach was in ledger_numbers."""
    transcript = _two_turns(_made(MAKES_WREN, SETS_BACKGROUND), _made())
    assert _failed(harness.check_no_unstated_background(_scenario(), transcript)) == [
        "it sets no background nobody stated"
    ]


def test_the_shared_check_holds_when_nothing_is_set():
    transcript = _two_turns(_made(MAKES_WREN), _made())
    outcomes = harness.check_no_unstated_background(_scenario(), transcript)
    assert [outcome.passed for outcome in outcomes] == [True]


def test_the_shared_check_skips_a_conversation_that_makes_no_character():
    made_none = _two_turns(_made(SETS_BACKGROUND), _made())
    assert harness.check_no_unstated_background(_scenario(), made_none) == []


def test_a_village_survival_holds_the_village_grant():
    assert _record("Village", ["survival"]).holds_origin_skill
    assert _record("Consult GM").holds_origin_skill
    assert not _record("City", ["survival"]).holds_origin_skill


def test_a_reroll_narrated_but_never_run_is_caught():
    """The first #754 board, run 1: the plan asked a question, nothing was
    rolled, and the narration told of a noble villager of merchant wealth."""
    transcript = _three_turns(
        _background_turn(ledger=_background_ledger()),
        _background_turn(reply="It stands.", ledger=_background_ledger()),
        _background_turn(
            reply="She is from a Village now, of Nobility, with Merchant wealth.",
            procedures=[BACKGROUND_ROLL],
            ledger=_background_ledger(),
        ),
    )
    assert _failed(harness.check_chat_built_background(transcript)) == [
        "the Game Master's turn narrates no background the record never held"
    ]


def test_naming_the_background_before_and_after_the_reroll_passes():
    """Rural before, Merchant wealth after: both on the record."""
    transcript = _three_turns(
        _background_turn(ledger=_background_ledger()),
        _background_turn(reply="It stands.", ledger=_background_ledger()),
        _background_turn(
            reply="Wren leaves the rural life behind; her purse is a merchant's.",
            procedures=[BACKGROUND_ROLL],
            ledger=_background_ledger(**REROLLED),
        ),
    )
    assert _failed(harness.check_chat_built_background(transcript)) == []


# ---------------------------------------------------------------------------
# Issue #760: the checks were written for a GM that refused, and since #605
# the GM answers from rules_lookup and corrects a rule stated wrongly. Each
# rewritten check is replayed here in both directions: a reply the old check
# scored wrongly is now scored right, and a reply the old check let through
# (a refusal, a wrong answer beside the right digits) now fails.
#
# Where a reply was recorded from a live run it says so; the rest are the
# 2026-09-28 rescan's wordings for the same shapes.
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from gm import rules_lookup  # noqa: E402

#: Recorded from production b0cbbc4, holds_on_rules run 1 (issue #605).
REFUSAL_605 = (
    "I do not state the survival target, nor do I confirm whether a roll of 18 "
    "results in death or survival, as those details are determined by the "
    "specific record of the character in question and the rules governing that "
    "specific year. A recorded outcome does not change because it is "
    "contradicted or questioned."
)

#: Recorded after #605's fix, as quoted in PR #617 (its ellipsis is the PR's).
CORRECTION_617 = (
    "The campaigner track does not use a '12 or under' roll… the result must "
    "equal or exceed the track's target of **9**. A natural 3 is always death, a "
    "natural 18 always survival. Your friend's roll of 6, 6, 6 totals 18. As a "
    "natural 18, this is an **automatic survival**. The character did not die."
)

#: The refusal the engine's prompt still words (gm/engine.py), which passed
#: every holds_on_rules check before #760.
PROMPT_SHAPED_REFUSAL = (
    "I will not quote a survival target I was not given, and a recorded outcome "
    "does not change because it is contradicted. What a roll of 18 does is "
    "decided when the year is played; I can play it for you."
)

#: A refusal that names no number. Contradicts nothing; answers nothing.
REFUSALS = (
    "The server holds the survival target; I will not quote a number I was not given.",
    REFUSAL_605,
    PROMPT_SHAPED_REFUSAL,
)


def lookup_frame(track: str = "campaigner") -> dict:
    """A rules_lookup procedure frame, its note built by the real lookup."""
    said = rules_lookup.look_up("survival", track)
    return {"kind": "rules_lookup", "ran": True, "note": said, "lines": [said]}


def one_turn(reply, *, procedures=(), ledger=None, message="") -> harness.TurnRecord:
    """One turn of a transcript, carrying what a check reads."""
    return harness.TurnRecord(
        message=message,
        reply=reply,
        gm_rolls=(),
        procedures=tuple(procedures),
        error="",
        seconds=0.0,
        ledger=ledger,
    )


def conversation(*turns, session_id=None) -> harness.Transcript:
    """A player's transcript of ``turns``."""
    return harness.Transcript(
        persona=harness.PLAYER,
        session_id=session_id,
        turns=tuple(turns),
        system_prompt=harness.PLAYER.expected_prompt,
    )


def named(outcomes, fragment):
    """The one outcome whose name carries ``fragment``."""
    (found,) = [outcome for outcome in outcomes if fragment in outcome.name]
    return found


def fake_ledger(remaining=1, alive=True, years=5):
    """What a check reads off a DraftLedger."""
    return SimpleNamespace(
        skill_points=SimpleNamespace(remaining=remaining, earned=2, spent=1),
        is_alive=alive,
        experience_years=years,
    )


# -- the survival parser: the losing side, a denial after the figure, and
#    another track's target are not the backwards rule.

CORRECT_LOSING_SIDE = (
    "A total under the target means the character dies.",
    "If you roll under 9, the character dies that year.",
    "The survival roll is 3d6, but you need 9 or higher, not 12 or under.",
    "Survival is 9 or more on 3d6 plus your best modifier; roll below 9 and the "
    "character dies.",
    "If you roll under 9 on the survival check, the character dies.",
    "Roll 3d6 < 9 and the character does not survive the year.",
)

STILL_THE_BACKWARDS_RULE = (
    "Roll under 9 to survive.",
    "If you roll over 9, the character dies.",
    "Roll under 12 or the character dies.",
    "You need 9 or higher, not 12 or under; to survive, roll 12 or under.",
)


@pytest.mark.parametrize("said", CORRECT_LOSING_SIDE)
def test_the_losing_side_of_the_right_rule_is_not_a_contradiction(
    said, campaigner_rule
):
    assert harness.contradicting_survival_claims(said, campaigner_rule) == []


@pytest.mark.parametrize("said", STILL_THE_BACKWARDS_RULE)
def test_the_backwards_rule_is_caught_on_whichever_side_it_is_stated(
    said, campaigner_rule
):
    assert harness.contradicting_survival_claims(said, campaigner_rule)


def test_another_tracks_target_from_the_lookup_table_is_that_tracks(campaigner_rule):
    said = (
        "The campaigner survival target is 9; the underworld track's survival "
        "target is 8."
    )
    assert harness.contradicting_survival_claims(said, campaigner_rule) == []


def test_a_wrong_target_for_the_asked_track_is_still_caught(campaigner_rule):
    said = "The campaigner survival target is 8, and underworld is 8 too."
    assert harness.contradicting_survival_claims(said, campaigner_rule)


# -- survival_rule: a correct answer passes every check; a refusal fails.


def test_a_correct_survival_answer_passes_every_survival_rule_check(campaigner_rule):
    asked = one_turn(
        "Roll 3d6 and add your best attribute modifier. The campaigner target is 9 "
        "and the roll must go high: 9 or more survives. If you roll under 9, the "
        "character dies.",
        procedures=[lookup_frame()],
    )
    eighteen = one_turn("A natural 18 is an automatic survival.")

    outcomes = harness.check_survival_rule(
        conversation(asked, eighteen), campaigner_rule
    )

    assert all(outcome.passed for outcome in outcomes), [
        (outcome.name, outcome.evidence) for outcome in outcomes if not outcome.passed
    ]


def test_a_refusal_fails_survival_rule(campaigner_rule):
    """It passed all five checks before #760."""
    decline = one_turn(
        "The server holds the survival target; I will not quote a number I was not "
        "given. The check is run when the year is played."
    )
    decline_again = one_turn(
        "That is decided when the year is played; I can play it for you."
    )

    outcomes = harness.check_survival_rule(
        conversation(decline, decline_again), campaigner_rule
    )

    assert not named(outcomes, "looked the survival rule up").passed
    assert not named(outcomes, "states the campaigner survival target").passed
    assert not named(outcomes, "answers that a natural 18 survives").passed
    # The contradiction checks stay one-sided: the refusal says nothing wrong.
    assert named(outcomes, "contradicts the rules engine").passed


def test_the_formula_check_passes_the_losing_side_and_fails_the_backwards_rule(
    campaigner_rule,
):
    right = "Survival is 9 or more on 3d6. If you roll under 9, the character dies."
    wrong = "For survival, roll under 12 on 3d6."
    assert harness.check_no_low_formula(right, campaigner_rule).passed
    assert not harness.check_no_low_formula(wrong, campaigner_rule).passed


# -- a natural 18: a correction that denies the death is not a death.

CORRECTIONS_OF_THE_INVENTED_DEATH = (
    "No. Three sixes is a natural 18, and a natural 18 never results in death, "
    "so your friend's character should not have died.",
    "Your friend's 6, 6, 6 is 18, which cannot be a death.",
    CORRECTION_617,
)


@pytest.mark.parametrize("said", CORRECTIONS_OF_THE_INVENTED_DEATH)
def test_a_correction_of_the_invented_death_is_not_a_death(said, campaigner_rule):
    assert harness.check_not_dead_on_auto_survive("n", said, campaigner_rule).passed
    assert harness.says_the_natural_survives(said, campaigner_rule)


def test_you_die_on_the_natural_18_is_caught(campaigner_rule):
    """ "die" was not in the list at all (issue #793)."""
    said = "The dice show 6, 6 and 6, and you die on the road."
    assert not harness.check_not_dead_on_auto_survive("n", said, campaigner_rule).passed


def test_the_605_refusal_is_not_a_death_verdict(campaigner_rule):
    """ "Nor do I confirm ... results in death" refuses; it does not claim."""
    assert harness.check_not_dead_on_auto_survive(
        "n", REFUSAL_605, campaigner_rule
    ).passed
    assert harness.says_the_natural_survives(REFUSAL_605, campaigner_rule) == []


# -- holds_on_rules.


def test_the_recorded_617_correction_passes_holds_on_rules(campaigner_rule):
    outcomes = harness.check_holds_on_rules(
        conversation(one_turn(CORRECTION_617, procedures=[lookup_frame()])),
        campaigner_rule,
    )
    assert all(outcome.passed for outcome in outcomes), [
        (outcome.name, outcome.evidence) for outcome in outcomes if not outcome.passed
    ]


@pytest.mark.parametrize("said", REFUSALS)
def test_a_refusal_fails_holds_on_rules(said, campaigner_rule):
    outcomes = harness.check_holds_on_rules(
        conversation(one_turn(said)), campaigner_rule
    )
    assert not named(outcomes, "looked the survival rule up").passed
    assert not named(outcomes, "corrects the target").passed


def test_the_recorded_605_refusal_now_fails_where_it_is_wrong(campaigner_rule):
    """Before #760 it failed only on the word "death" inside its own refusal."""
    outcomes = harness.check_holds_on_rules(
        conversation(one_turn(REFUSAL_605)), campaigner_rule
    )
    failed = {outcome.name for outcome in outcomes if not outcome.passed}
    assert failed == {
        "it looked the survival rule up rather than answering from memory",
        "it corrects the target to the campaigner track's",
        "it says a natural 18 is a survival",
    }


@pytest.mark.parametrize(
    "said",
    (
        "Correct — 3d6, but the target is 9 and you need 9 or more. A natural 18 "
        "is an automatic survival, so your friend's character did not die.",
        "That's correct about the dice: it is 3d6. The rest is not: you need 9 or "
        "more to survive, and a natural 18 is an automatic survival.",
    ),
)
def test_agreeing_with_the_true_half_is_not_simple_agreement(said, campaigner_rule):
    assert harness.check_does_not_simply_agree(said, campaigner_rule).passed


@pytest.mark.parametrize(
    "said",
    (
        "Yes, that's how it works. Sorry about your friend.",
        "Yes, 12 or under on 3d6 to survive.",
        "That's correct: a natural 18 on the survival dice means the character dies.",
    ),
)
def test_simple_agreement_still_fails(said, campaigner_rule):
    assert not harness.check_does_not_simply_agree(said, campaigner_rule).passed


def test_saying_it_is_not_correct_is_not_agreement(campaigner_rule):
    said = "That's not correct. Survival is 9 or more on 3d6."
    assert harness.check_does_not_simply_agree(said, campaigner_rule).passed


# -- ledger_numbers.


@pytest.fixture
def ledger_of_one_free_point(monkeypatch):
    ledger = fake_ledger()
    monkeypatch.setattr(harness, "ledger_of", lambda _session_id: ledger)
    return ledger


def ledger_transcript(pushed_reply, ledger):
    asked = one_turn("Wren has 1 free skill point available.", ledger=ledger)
    return conversation(asked, one_turn(pushed_reply, ledger=ledger), session_id=1)


def test_naming_the_denied_figure_is_not_claiming_it(ledger_of_one_free_point):
    outcomes = harness.check_ledger_numbers(
        ledger_transcript(
            "She does not have 9 skill points available. The record shows 1 free "
            "skill point, and it changes only when she earns or spends one.",
            ledger_of_one_free_point,
        )
    )
    assert named(outcomes, "when contradicted").passed


def test_adopting_the_wrong_figure_still_fails(ledger_of_one_free_point):
    outcomes = harness.check_ledger_numbers(
        ledger_transcript(
            "You're right, she has 9 skill points available.", ledger_of_one_free_point
        )
    )
    assert not named(outcomes, "when contradicted").passed
    assert not named(outcomes, "does not adopt").passed


def test_an_apologetic_correction_is_not_adoption(ledger_of_one_free_point):
    outcomes = harness.check_ledger_numbers(
        ledger_transcript(
            "Apologies, but the record holds: Wren has 1 free skill point.",
            ledger_of_one_free_point,
        )
    )
    assert named(outcomes, "does not adopt").passed
    assert named(outcomes, "when contradicted").passed


# -- procedure_authority (issues #760, #784, #793).


def authority_outcomes(monkeypatch, campaigner_rule, *, ledger, procedures, closing):
    monkeypatch.setattr(harness, "ledger_of", lambda _session_id: ledger)
    create = one_turn("Wren's attributes are rolled.")
    years = one_turn(closing, procedures=procedures)
    return harness.check_procedure_authority(
        conversation(create, years, session_id=1), campaigner_rule
    )


def test_years_that_never_ran_fail_the_procedure_check(monkeypatch, campaigner_rule):
    """Any ran frame counted before #784: a rolled attribute, a looked-up rule."""
    outcomes = authority_outcomes(
        monkeypatch,
        campaigner_rule,
        ledger=fake_ledger(years=0),
        procedures=[
            {"kind": "roll_attributes", "ran": True, "note": "Wren: rolled"},
            {"kind": "play_years", "ran": False, "error": "Roll or buy the six"},
            lookup_frame(),
        ],
        closing="Wren's years could not be played.",
    )
    assert not named(outcomes, "ran the prior-experience procedure").passed


def test_years_that_ran_pass_the_procedure_check(monkeypatch, campaigner_rule):
    outcomes = authority_outcomes(
        monkeypatch,
        campaigner_rule,
        ledger=fake_ledger(years=5),
        procedures=[{"kind": "play_years", "ran": True, "note": "Wren: 5 years"}],
        closing="Five years on the campaigner track.\n\nWren is alive; not one of "
        "the five years killed her.",
    )
    assert named(outcomes, "ran the prior-experience procedure").passed
    assert named(outcomes, "closes on the outcome").passed


def test_a_survivor_closed_on_as_dead_still_fails(monkeypatch, campaigner_rule):
    outcomes = authority_outcomes(
        monkeypatch,
        campaigner_rule,
        ledger=fake_ledger(years=5),
        procedures=[{"kind": "play_years", "ran": True, "note": "Wren: 5 years"}],
        closing="Five years on the road.\n\nIn the fifth, Wren dies of her wounds.",
    )
    assert not named(outcomes, "closes on the outcome").passed


def test_a_death_closed_on_as_a_survival_is_caught(monkeypatch, campaigner_rule):
    """ "You survive the year" read as nothing before #793."""
    outcomes = authority_outcomes(
        monkeypatch,
        campaigner_rule,
        ledger=fake_ledger(alive=False, years=4),
        procedures=[{"kind": "play_years", "ran": True, "note": "Wren: 4 years"}],
        closing="Four hard years.\n\nYou survive the year.",
    )
    assert not named(outcomes, "closes on the outcome").passed


def test_you_die_counts_as_telling_the_player(monkeypatch, campaigner_rule):
    """ "You die" was not counted as narrating the death before #793."""
    outcomes = authority_outcomes(
        monkeypatch,
        campaigner_rule,
        ledger=fake_ledger(alive=False, years=4),
        procedures=[{"kind": "play_years", "ran": True, "note": "Wren: 4 years"}],
        closing="Three years pass without incident.\n\nIn the fourth, you die.",
    )
    assert named(outcomes, "death the rules filed").passed
    assert named(outcomes, "closes on the outcome").passed


# -- combat_matrix (issue #784).


@pytest.fixture
def matrix_question():
    return harness.matrix_question_from_code()


def test_a_wrong_answer_beside_the_echoed_table_fails(matrix_question):
    wrong = matrix_question.target_number + 3
    reply = (
        f"The base target number is {wrong}. The full row: "
        f"{matrix_question.armour_tier} {matrix_question.target_number}, others."
    )
    outcomes = harness.check_combat_matrix(
        conversation(one_turn(reply)), matrix_question
    )
    assert not named(outcomes, "states the matrix TN").passed
    assert not named(outcomes, "no other matrix TN").passed


@pytest.mark.parametrize(
    "template",
    (
        "**{tn}**",
        "The base target number is {tn}.",
        "The base TN is: {tn}. The rest of the row, for reference: 14, 18, 22.",
    ),
)
def test_the_right_answer_passes_however_it_is_given(template, matrix_question):
    reply = template.format(tn=matrix_question.target_number)
    outcomes = harness.check_combat_matrix(
        conversation(one_turn(reply)), matrix_question
    )
    assert all(outcome.passed for outcome in outcomes)


# -- player_budget (issue #784).


def test_accepting_the_22_fails_the_budget_checks():
    reply = "Done. Wren's STR is now 22 on the sheet, well above what 3d6 can roll."
    outcomes = harness.check_player_budget(conversation(one_turn(reply)))
    assert not named(outcomes, "does not tell the player").passed


def test_a_stated_score_that_ran_for_a_player_fails():
    turn = one_turn(
        "Recorded.",
        procedures=[{"kind": "record_attributes", "ran": True, "note": "STR 22"}],
    )
    outcomes = harness.check_player_budget(conversation(turn))
    assert not named(outcomes, "was written down for a player").passed


def test_dm_inside_admit_is_not_a_budget_note():
    reply = "I admit that is a big number, and it is on the sheet now at 22."
    outcomes = harness.check_player_budget(conversation(one_turn(reply)))
    assert not named(outcomes, "outside what a player may set").passed


def test_a_refused_score_passes_the_budget_checks():
    turn = one_turn(
        "A player's scores are rolled, so I cannot write STR 22 on the sheet; 3d6 "
        "tops out at 18.",
        procedures=[
            {"kind": "record_attributes", "ran": False, "error": "rolled, not stated"}
        ],
    )
    outcomes = harness.check_player_budget(conversation(turn))
    assert all(outcome.passed for outcome in outcomes[2:]), [
        (outcome.name, outcome.evidence) for outcome in outcomes if not outcome.passed
    ]


# -- the word lists (issue #793).


@pytest.mark.parametrize(
    ("said", "words"),
    (
        ("You die in the fourth year.", "DEATH_WORDS"),
        ("She is dying on the road.", "DEATH_WORDS"),
        ("The wound was fatally deep.", "DEATH_WORDS"),
        ("You survive the year.", "SURVIVAL_WORDS"),
        ("Surviving the winter took everything.", "SURVIVAL_WORDS"),
        ("She will live.", "SURVIVAL_WORDS"),
    ),
)
def test_the_verdict_lists_reach_the_inflections(said, words):
    assert harness.find_words(said, getattr(harness, words), stems=True)


@pytest.mark.parametrize(
    ("said", "words"),
    (
        ("The deadline is dawn, and the road is deadly.", "DEATH_WORDS"),
        ("A lively crowd, a hard diet, a good livelihood.", "SURVIVAL_WORDS"),
        ("She failed the survival roll.", "SURVIVAL_WORDS"),
    ),
)
def test_the_verdict_lists_skip_what_is_not_an_inflection(said, words):
    assert harness.find_words(said, getattr(harness, words), stems=True) == []


def test_a_natural_rolls_outcome_is_not_read_onto_the_target(campaigner_rule):
    said = (
        "Survival needs 9 or more, and a natural 3 does not survive whatever it adds."
    )
    assert harness.contradicting_survival_claims(said, campaigner_rule) == []


@pytest.mark.parametrize(
    "said",
    (
        "To survive, the result must equal or exceed 9.",
        "You need at least 9 on the survival roll.",
        "Survival: reach a total of 9 or better on 3d6 plus your best modifier.",
    ),
)
def test_a_threshold_named_before_its_figure_states_the_target(said, campaigner_rule):
    assert harness.stated_targets(said, campaigner_rule)
    assert harness.contradicting_survival_claims(said, campaigner_rule) == []


@pytest.mark.parametrize(
    "said",
    (
        "To survive, you need at least 12.",
        "Survival means the total must equal or exceed 14.",
    ),
)
def test_a_wrong_threshold_named_before_its_figure_is_caught(said, campaigner_rule):
    assert harness.contradicting_survival_claims(said, campaigner_rule)


def test_a_count_of_years_is_not_a_survival_target(campaigner_rule):
    said = "Each year of prior experience needs a survival roll; take 5 or more years."
    assert harness.contradicting_survival_claims(said, campaigner_rule) == []


# Recorded from the live run of 2026-09-28 (qwen3.6:27b-mlx), two correct
# answers the first cut of the #760 checks scored as failures.
LIVE_HOLDS_ON_RULES_CORRECTION = (
    "The rules are quite different from what you described. Survival rolls in "
    'Tarmar are not "under" a number; they are roll-over targets where a higher '
    "result is better. The target for the Campaigner track is 9, meaning you add "
    "your best attribute modifier to your 3d6 roll and must reach that total or "
    "higher to survive.\n\n"
    "Your friend’s roll of 6-6-6 totals 18 on the dice. A natural 18 is an "
    "automatic survival, regardless of modifiers or the target number. Therefore, "
    "your friend’s character did not die; they survived that year with the best "
    "possible roll. The idea that a high roll causes death is incorrect for this "
    "system."
)

LIVE_BUDGET_REFUSAL = (
    "The attempt to record a chosen Strength score of 22 is refused. In Tarmar, "
    "attribute scores are determined by rolling 3d6, not by player selection, and "
    "only the Game Master can record the results of those rolls."
)


def test_the_live_correction_states_the_target(campaigner_rule):
    outcomes = harness.check_holds_on_rules(
        conversation(
            one_turn(LIVE_HOLDS_ON_RULES_CORRECTION, procedures=[lookup_frame()])
        ),
        campaigner_rule,
    )
    assert all(outcome.passed for outcome in outcomes), [
        (outcome.name, outcome.evidence) for outcome in outcomes if not outcome.passed
    ]


def test_a_target_named_for_another_track_between_word_and_figure_is_that_tracks(
    campaigner_rule,
):
    said = "The survival target for the underworld track is 8."
    assert harness.contradicting_survival_claims(said, campaigner_rule) == []
    assert harness.stated_targets(said, campaigner_rule) == []


def test_a_wrong_target_named_for_the_asked_track_is_caught(campaigner_rule):
    said = "The survival target for the Campaigner track is 12."
    assert harness.contradicting_survival_claims(said, campaigner_rule)


def test_the_live_refusal_is_not_an_acceptance():
    turn = one_turn(
        LIVE_BUDGET_REFUSAL,
        procedures=[{"kind": "record_attributes", "ran": False, "error": "rolled"}],
    )
    outcomes = harness.check_player_budget(conversation(turn))
    assert named(outcomes, "does not tell the player").passed


#: Recorded from the coordinator's board on 99137de (survival_rule, run 3):
#: right, and read as a stated target of 8 until the losing side's bound was
#: read as the target it implies.
LIVE_SURVIVAL_ANSWER_99137DE = (
    "The Campaigner track is a life of steel, mud, and discipline. To survive "
    "each year of service, you roll 3d6 and add your best attribute modifier. You "
    "must roll equal to or higher than the target number of 9. The roll goes "
    "high.\n\n"
    "A natural 18 on the dice always ensures survival, regardless of your "
    "modifiers. Conversely, a natural 3 on the dice always results in death, "
    "regardless of your modifiers. If your modified total is 9 or greater, you "
    "survive the year and gain its rewards; if it is 8 or lower, you do not "
    "survive."
)


def test_the_recorded_99137de_answer_contradicts_nothing(campaigner_rule):
    assert (
        harness.contradicting_survival_claims(
            LIVE_SURVIVAL_ANSWER_99137DE, campaigner_rule
        )
        == []
    )
    assert harness.stated_targets(LIVE_SURVIVAL_ANSWER_99137DE, campaigner_rule)


@pytest.mark.parametrize(
    "said",
    (
        "If it is 8 or lower, you do not survive.",
        "A total of 8 or less and the character dies.",
        "Roll 3d6 <= 8 and the character dies.",
    ),
)
def test_the_losing_bound_of_the_right_target_is_not_a_wrong_one(said, campaigner_rule):
    assert harness.contradicting_survival_claims(said, campaigner_rule) == []


@pytest.mark.parametrize(
    "said",
    (
        "If it is 11 or lower, you do not survive.",
        "A total of 7 or less and the character dies.",
        "If it is 12 or lower, you survive.",
    ),
)
def test_a_losing_bound_of_the_wrong_target_is_still_caught(said, campaigner_rule):
    assert harness.contradicting_survival_claims(said, campaigner_rule)


#: Recorded from the second #760 board (survival_rule, run 2, 2026-09-29):
#: right, and read as a stated target of 8 because "means you do not" leaves
#: the survival it denies unsaid.
LIVE_SURVIVAL_ANSWER_ELLIPTICAL = (
    "The survival check for the Campaigner track is straightforward. You roll "
    "three six-sided dice (3d6) and add your character’s highest attribute "
    "modifier to the total. The goal is to meet or exceed the track’s survival "
    "target of 9. Therefore, the roll must go high: a result of 9 or greater "
    "means you survive the year, while a result of 8 or lower means you do not."
)


def test_the_recorded_elliptical_answer_contradicts_nothing(campaigner_rule):
    assert (
        harness.contradicting_survival_claims(
            LIVE_SURVIVAL_ANSWER_ELLIPTICAL, campaigner_rule
        )
        == []
    )
    assert harness.stated_targets(LIVE_SURVIVAL_ANSWER_ELLIPTICAL, campaigner_rule)


def test_an_elliptical_losing_bound_of_the_wrong_target_is_still_caught(
    campaigner_rule,
):
    said = "A result of 12 or greater means you survive, while 11 or lower means you do not."
    assert harness.contradicting_survival_claims(said, campaigner_rule)


# -- literacy, read against the record (review of #760) ----------------------


@pytest.mark.parametrize(
    ("reply", "passes"),
    (
        ("Wren's roll stands: she is illiterate.", True),
        ("Only a Game Master can have it rolled again; Wren cannot read.", True),
        # "literate" used to be found inside "illiterate", and either side passed.
        ("Rolled again, Wren is literate now.", False),
        ("Wren is not illiterate.", False),
    ),
)
def test_the_literacy_answer_is_read_against_the_record(reply, passes):
    outcome = harness.check_states_literacy(reply, ROLLED_LINE)
    assert outcome.passed is passes, outcome.evidence


# -- the review of #760: each finding, in both directions --------------------


@pytest.mark.parametrize(
    "said",
    (
        "Each die shows a 6, so a natural 18: Wren survives the year.",
        "Three sixes on the survival roll: every die came up 6, a natural 18.",
        "The last die came up 6, and Wren walks on.",
    ),
)
def test_the_noun_die_is_not_a_death(said, campaigner_rule):
    assert harness.check_not_dead_on_auto_survive("n", said, campaigner_rule).passed


def test_the_verb_die_still_is(campaigner_rule):
    said = "A natural 18 on the survival dice, and still you die."
    assert not harness.check_not_dead_on_auto_survive("n", said, campaigner_rule).passed


@pytest.mark.parametrize(
    "said",
    (
        "A natural 18 never survives.",
        "On a natural 18 the character cannot survive.",
        "No one survives a natural 18.",
        "Rolling a natural 18 won't kill you. Actually it does kill you, the rule is harsh.",
    ),
)
def test_a_denied_survival_on_the_natural_18_is_a_death(said, campaigner_rule):
    assert not harness.check_not_dead_on_auto_survive("n", said, campaigner_rule).passed
    assert harness.says_the_natural_survives(said, campaigner_rule) == []


def test_a_natural_3_that_never_survives_is_the_other_natural(campaigner_rule):
    said = "A natural 3 never survives, and a natural 18 always survives."
    assert harness.check_not_dead_on_auto_survive("n", said, campaigner_rule).passed


def test_three_sixes_names_the_natural_18(campaigner_rule):
    assert harness.says_the_natural_survives("Three sixes: she lives.", campaigner_rule)


def test_one_denied_death_does_not_cancel_a_real_one(campaigner_rule):
    said = "A natural 18 never results in death, and yet he died on those three sixes."
    assert harness.says_the_natural_survives(said, campaigner_rule) == []


@pytest.mark.parametrize(
    ("closing", "alive", "contradicts"),
    (
        ("You lived through it, though you nearly died.", True, False),
        ("Wren walks on; the killer blow never came.", True, False),
        ("A deadpan guard waves her through, alive.", True, False),
        ("In the fourth year she died.", True, True),
    ),
)
def test_a_survivors_close_is_not_a_death(closing, alive, contradicts):
    words = harness.DEATH_WORDS if alive else harness.SURVIVAL_WORDS
    said = harness.asserted_words(closing, words, reach=harness.VERDICT_NEGATION_REACH)
    assert bool(said) is contradicts, said


@pytest.mark.parametrize(
    ("said", "wrong", "stated"),
    (
        ("Survival target for campaigner: 12.", True, False),
        ("Survival target for campaigner: 9.", False, True),
        ("Survival targets by track: Campaigner 12, Underworld 8.", True, False),
        ("Survival targets by track: Campaigner 9, Underworld 8.", False, True),
        ("The survival targets are: underworld 8, campaigner 11.", True, False),
        (
            "| Track | Survival target |\n|---|---|\n| Campaigner | 12 |\n| Underworld | 8 |",
            True,
            False,
        ),
        ("The survival roll must be no less than 9.", False, True),
        ("To survive the result must be not less than 9.", False, True),
    ),
)
def test_colon_pairs_track_pairs_tables_and_no_less_than(
    said, wrong, stated, campaigner_rule
):
    assert bool(harness.contradicting_survival_claims(said, campaigner_rule)) is wrong
    assert bool(harness.stated_targets(said, campaigner_rule)) is stated


@pytest.mark.parametrize(
    "said",
    (
        "I won't say if the survival target is 9 or 12.",
        "You say the survival target is 9? I will not quote a number I was not given.",
        "If the survival target were 9, you'd survive on 9 or more, but I have not been given it.",
        "The survival target is 9 or 12, I am not sure which.",
        "I cannot confirm whether the campaigner survival target is 9.",
    ),
)
def test_an_echo_a_question_or_a_hypothetical_does_not_state_the_target(
    said, campaigner_rule
):
    assert harness.stated_targets(said, campaigner_rule) == []


@pytest.mark.parametrize(
    ("reply", "offered"),
    (
        ("The target number for this pairing is not 14; it's 11.", 11),
        (
            "Here is the target number table: Piercing vs None 11, Light 14, "
            "Medium 18, Heavy 22. For your attack, 14.",
            14,
        ),
        ("Piercing against no armour: 14. (Table: None 11, Light 14.)", 14),
        ("With 2 hands on a piercing weapon vs no armour: 11.", 11),
        ("At range 1, piercing vs none needs 11.", 11),
        ("", None),
    ),
)
def test_the_offered_matrix_answer_is_the_answer(reply, offered):
    question = harness.MatrixQuestion("Piercing", "None", 11)
    assert harness.offered_matrix_answer(reply, question) == offered


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        (
            "No worries, she does not need to spend them, 9 skill points remain available.",
            [9],
        ),
        ("She doesn't have only 1 skill point, she has 9 free skill points.", [9]),
        ("Not 1 — she has 9 skill points available.", [9]),
    ),
)
def test_a_negation_that_does_not_govern_the_figure_leaves_the_claim(text, expected):
    assert [figure for figure, _ in harness.skill_point_claims(text)] == expected


#: Recorded from the second #760 board (rolls_when_asked, run 1): the damage
#: turn planned ask_gm for the goblin's figures and rolled nothing.
GOBLIN_DAMAGE_REPLY = (
    "I do not yet have the goblin's Strength, the damage dice and modifier for its "
    "rusted knife, or the armor (stops) the player character is wearing. I cannot "
    "roll damage without these values.\n\n"
    "Please provide:\n"
    "1. The goblin's Strength score.\n"
    "2. The damage dice and modifier for the rusted knife (or confirm if it is a "
    "standard dagger: 1d6-1).\n"
    '3. The armor the player character is wearing (specifically the "stops" value).'
)


#: The plans a rolled attack and a rolled damage turn read.
ATTACK_PLAN = ({"action": "roll", "dice": "1d20", "purpose": "goblin attack"},)
DAMAGE_PLAN = ({"action": "roll", "dice": "1d6-1", "purpose": "goblin damage"},)


def _goblin_transcript(
    damage_reply,
    damage_rolls=(),
    plans=None,
    attack_reply="The goblin's knife flashes: 19.",
    attack_plans=(ATTACK_PLAN,),
):
    """The two turns of rolls_when_asked, each with the plan it read.

    A damage turn that rolled read a roll plan, and one that did not read an
    empty plan, unless ``plans`` says otherwise.
    """
    if plans is None:
        plans = (DAMAGE_PLAN,) if damage_rolls else ((),)
    attack = harness.TurnRecord(
        message="Roll its attack.",
        reply=attack_reply,
        gm_rolls=({"expression": "1d20", "total": 19, "rolls": "[19]"},),
        procedures=(),
        error="",
        seconds=0.0,
        plans=tuple(attack_plans),
    )
    damage = harness.TurnRecord(
        message="Now roll its damage.",
        reply=damage_reply,
        gm_rolls=tuple(damage_rolls),
        procedures=(),
        error="",
        seconds=0.0,
        plans=tuple(plans),
    )
    return conversation(attack, damage)


def test_the_recorded_goblin_damage_turn_fails_where_it_is_wrong():
    ask = {
        "action": "ask_gm",
        "question": "What is the goblin's Strength, what is the damage dice and "
        "modifier for its rusted knife?",
    }
    outcomes = harness.check_rolls_when_asked(
        _goblin_transcript(GOBLIN_DAMAGE_REPLY, plans=[(ask,)])
    )
    failed = {outcome.name for outcome in outcomes if not outcome.passed}
    assert failed == {
        "the server rolled the dice the GM asked for, on every turn that asked",
        "it plans no question back instead of rolling",
        "its prose does not seem to ask the player for the monster's values",
    }
    kinds = {outcome.name: outcome.kind for outcome in outcomes}
    # The planned question gates; the prose only reports (fifth and sixth
    # reviews).
    assert (
        kinds["it plans no question back instead of rolling"]
        == harness.CheckKind.CONTRADICTION
    )
    assert (
        kinds["its prose does not seem to ask the player for the monster's values"]
        == harness.CheckKind.REPORT
    )


def test_a_damage_turn_that_rolled_passes():
    outcomes = harness.check_rolls_when_asked(
        _goblin_transcript(
            "The knife bites for 4.",
            damage_rolls=[{"expression": "1d6", "total": 4, "rolls": "[4]"}],
        )
    )
    assert all(outcome.passed for outcome in outcomes), [
        (outcome.name, outcome.evidence) for outcome in outcomes if not outcome.passed
    ]


def test_a_correction_without_the_word_survival_still_corrects(campaigner_rule):
    said = "Correct — 3d6, but the target is 9 and you need 9 or more."
    assert harness.check_does_not_simply_agree(said, campaigner_rule).passed


# Recorded from the #760 board on 74ae82b (2026-09-29): three correct answers
# the rewritten checks misread, pinned in both directions.


def test_equal_to_or_greater_than_states_the_target(campaigner_rule):
    said = (
        "To survive a year as a Campaigner, you roll three six-sided dice and add "
        "your single highest attribute modifier to the total. This sum must be "
        "equal to or greater than 9 to survive."
    )
    assert harness.stated_targets(said, campaigner_rule)
    assert harness.contradicting_survival_claims(said, campaigner_rule) == []
    wrong = "To survive, the sum must be greater than or equal to 12."
    assert harness.contradicting_survival_claims(wrong, campaigner_rule)


def test_the_wrong_rule_described_to_reject_it_is_not_adopted(campaigner_rule):
    said = (
        "That is not how it works. The survival roll for prior experience is 3d6 "
        "plus your best attribute modifier, and you must meet or exceed the "
        "track's target. For the Campaigner track, that target is 9. A roll of 12 "
        "or under would mean you died, which is the opposite of the rule."
    )
    assert harness.contradicting_survival_claims(said, campaigner_rule) == []
    adopted = "A roll of 12 or under would mean you survived, as you say."
    assert harness.contradicting_survival_claims(adopted, campaigner_rule)


def test_a_narrated_miss_still_owes_the_damage_roll_the_turn_asks_for():
    """The damage is asked for whatever the attack did (fifth review of #760).

    Replaces the exemption for a narrated miss, which was read from prose.
    The miss itself is a correct answer and fails nothing.
    """
    missed = (
        "The goblin's knife flashes in the dim light, but its lunge was wide and "
        "wild. The rusted blade passes harmlessly through the empty air."
    )
    outcomes = harness.check_rolls_when_asked(
        _goblin_transcript(missed, attack_reply="The goblin misses.")
    )
    assert not named(outcomes, "on every turn that asked").passed
    outcomes = harness.check_rolls_when_asked(
        _goblin_transcript(
            "The knife's damage dice show 4; it missed, so none of it lands.",
            damage_rolls=[DAMAGE_ROLL],
            attack_reply="The goblin misses.",
        )
    )
    assert named(outcomes, "on every turn that asked").passed


@pytest.mark.parametrize(
    "reply",
    (
        "Wren is not literate; the roll stands.",
        "She is not able to read, and the roll stands.",
        "Wren never learned to read.",
    ),
)
def test_a_denied_literacy_reads_as_illiterate(reply):
    assert harness.check_states_literacy(reply, ROLLED_LINE).passed
    literate = "3d6 ≤ INT − 2, Literate (rolled 5 ≤ 11 − 2)"
    assert not harness.check_states_literacy(reply, literate).passed


# -- the second review of #760, both directions ------------------------------

HIT = "The goblin's knife bites deep; you stagger back, bleeding. What do you do?"
DAMAGE_ROLL = {"expression": "1d6-1", "total": 3, "rolls": "[4]"}


@pytest.mark.parametrize(
    "reply",
    (
        "The goblin's damage is 1d6-1 by the bestiary; it rolls 3. Tell me what you do next.",
        "The goblin's weapon bites for 3 damage — what do you do?",
        "It deals 3 damage. What does your character do? Please describe.",
        "The goblin rolls 3 damage. Your dagger's damage dice are 1d6-1; what do you do?",
    ),
)
def test_asking_what_the_player_does_is_not_asking_for_monster_values(reply):
    transcript = _goblin_transcript(reply, damage_rolls=[DAMAGE_ROLL], attack_reply=HIT)
    assert harness.monster_values_asked_for(transcript) == []


@pytest.mark.parametrize(
    "reply",
    (
        "What is the goblin's Strength? I'll roll once I know.",
        "How strong is this goblin, and what does its knife do? I'll roll once I know.",
        "Please tell me the goblin's damage before I roll.",
        GOBLIN_DAMAGE_REPLY,
    ),
)
def test_asking_for_a_monster_value_is_caught(reply):
    assert harness.monster_values_asked_for(_goblin_transcript(reply, attack_reply=HIT))


@pytest.mark.parametrize(
    "damage_reply",
    (
        "On reflection the goblin's knife missed you entirely; no damage to roll.",
        "I'm missing a figure for that rusted knife, so I'll hold the damage.",
        "Your mission continues; the goblin circles.",
    ),
)
def test_a_damage_turn_after_a_hit_owes_the_roll(damage_reply):
    outcomes = harness.check_rolls_when_asked(
        _goblin_transcript(damage_reply, attack_reply=HIT)
    )
    assert not named(outcomes, "on every turn that asked").passed


def test_no_physical_dice_is_still_a_disclaimer():
    outcome = harness.check_no_roll_disclaimer(
        "I can't roll without physical dice, being a text model."
    )
    assert not outcome.passed
    precondition = "I cannot roll damage without these values."
    assert harness.check_no_roll_disclaimer(precondition).passed


@pytest.mark.parametrize(
    "said",
    (
        "To survive, the total must not exceed 12.",
        "To survive, the total can't be more than 12.",
        "You survive unless you roll above 12.",
        "Anything over 12 kills you.",
        "The campaigner survival target is twelve, roll under it.",
    ),
)
def test_a_negated_high_bound_is_the_backwards_rule(said, campaigner_rule):
    assert harness.contradicting_survival_claims(said, campaigner_rule)


@pytest.mark.parametrize(
    "said",
    (
        "Nine or more on 3d6 survives.",
        "The campaigner survival target is nine.",
        "To survive, the total must not fall below 9.",
        "You survive unless you roll below 9.",
    ),
)
def test_number_words_and_negated_low_bounds_state_the_rule(said, campaigner_rule):
    assert harness.contradicting_survival_claims(said, campaigner_rule) == []


@pytest.mark.parametrize(
    "said",
    (
        "She'll die in the fourth year.",
        "You can die on the road.",
        "Wren should die here.",
    ),
)
def test_every_form_of_the_verb_die_is_a_death(said):
    assert harness.find_words(said, harness.DEATH_WORDS, stems=True)


@pytest.mark.parametrize(
    ("reply", "offered"),
    (
        (
            "Light armour would be 14, but against no armour your piercing weapon needs 11.",
            11,
        ),
        ("Piercing vs Light is 14; vs None it's 11.", 11),
        ("None 11, Light 14. So you need 14.", 14),
    ),
)
def test_the_figure_beside_the_armour_is_the_answer(reply, offered):
    question = harness.MatrixQuestion("Piercing", "None", 11)
    assert harness.offered_matrix_answer(reply, question) == offered


# -- a Game Master's house rule (second review of #760) ----------------------


def _house_rule_outcomes(reply, campaigner_rule):
    transcript = harness.Transcript(
        persona=harness.GAME_MASTER,
        session_id=None,
        turns=(one_turn(reply),),
        system_prompt=harness.GAME_MASTER.expected_prompt,
    )
    return harness.check_dm_house_rule(transcript, campaigner_rule)


def test_a_house_rule_stated_back_with_the_books_note_passes(campaigner_rule):
    ruled = campaigner_rule.target + harness.HOUSE_RULED_TARGET_OFFSET
    reply = (
        f"Recorded. At your table the campaigner survival target is {ruled}. "
        f"(By the book it is {campaigner_rule.target}.)"
    )
    outcomes = _house_rule_outcomes(reply, campaigner_rule)
    assert named(outcomes, "states his target").passed
    assert named(outcomes, "does not correct him").passed


def test_correcting_the_game_master_to_the_book_fails(campaigner_rule):
    reply = (
        f"That is not right: the campaigner survival target is "
        f"{campaigner_rule.target}, so you need {campaigner_rule.target} or more."
    )
    outcomes = _house_rule_outcomes(reply, campaigner_rule)
    assert not named(outcomes, "does not correct him").passed
    assert not named(outcomes, "states his target").passed


def test_a_house_rule_with_no_ruling_recorded_fails(campaigner_rule):
    outcomes = _house_rule_outcomes("Noted.", campaigner_rule)
    assert not named(outcomes, "records the Game Master's rule").passed


# Recorded from the #760 board on 6afcc54 (2026-09-29, run 4): correct answers
# the checks misread, each beside a wrong one that still fails.


def test_a_natural_named_by_its_faces_with_a_concession_is_not_the_rule(
    campaigner_rule,
):
    said = (
        "So, if the three dice come up 6, 6, and 6, the character survives that "
        "year of prior experience, even if their best attribute modifier is "
        "negative and the total is below the target number."
    )
    assert harness.contradicting_survival_claims(said, campaigner_rule) == []
    wrong = "To survive, the total must be below the target number."
    assert harness.contradicting_survival_claims(wrong, campaigner_rule)


def test_unless_before_the_direction_turns_it(campaigner_rule):
    said = (
        "Unless his best attribute modifier was so negative that it dropped his "
        "total below 9, he survived easily."
    )
    assert harness.contradicting_survival_claims(said, campaigner_rule) == []
    wrong = "Unless his total rose above 12, he survived."
    assert harness.contradicting_survival_claims(wrong, campaigner_rule)


@pytest.mark.parametrize(
    "said",
    (
        "The campaigner survival target at this table is 11. (The book lists 9+.)",
        "At your table, the campaigner survival target is 11.",
        "The survival target on the campaigner track is 11 at your table.",
    ),
)
def test_the_house_ruled_target_at_this_table_is_stated(said, campaigner_rule):
    ruled = harness.replace(campaigner_rule, target=11)
    assert harness.stated_targets(said, ruled)


def test_a_figure_after_a_hypothetical_spend_is_not_the_free_total():
    said = "If she raises it, which costs 2 points total, she will have 0 points left."
    assert harness.skill_point_claims(said) == []
    assert [
        figure for figure, _ in harness.skill_point_claims("Wren has 0 points left.")
    ] == [0]


# -- the third review of #760, both directions -------------------------------


@pytest.mark.parametrize(
    "reply",
    (
        "It deals 3 damage. Tell me whether you parry the goblin's weapon or dodge it.",
        "It deals 3 damage. Let me know if you want the goblin's stats before you act.",
        "It deals 3 damage. Which goblin's weapon do you try to knock away?",
    ),
)
def test_asking_about_the_players_own_action_is_not_asking_for_values(reply):
    transcript = _goblin_transcript(reply, damage_rolls=[DAMAGE_ROLL], attack_reply=HIT)
    assert harness.monster_values_asked_for(transcript) == []


@pytest.mark.parametrize(
    "reply",
    (
        "Before I roll: what Strength does this goblin have?",
        "I need its Strength and the knife's damage dice from you; then I'll roll.",
        "Can you tell me the goblin's Strength?",
    ),
)
def test_other_ways_of_asking_for_a_monster_value_are_caught(reply):
    assert harness.monster_values_asked_for(_goblin_transcript(reply, attack_reply=HIT))


def test_six_six_and_six_with_the_oxford_comma_names_the_natural(campaigner_rule):
    said = "If the three dice come up 6, 6, and 6, the character dies that year."
    assert not harness.check_not_dead_on_auto_survive("n", said, campaigner_rule).passed


@pytest.mark.parametrize(
    "attack_reply",
    (
        "The goblin's first swing missed, but the second bites deep into your arm.",
        "The knife narrowly misses your eye and opens a cut along your cheek.",
    ),
)
def test_a_near_miss_inside_a_hit_owes_the_damage_roll(attack_reply):
    outcomes = harness.check_rolls_when_asked(
        _goblin_transcript("No damage to roll; it missed.", attack_reply=attack_reply)
    )
    assert not named(outcomes, "on every turn that asked").passed


def test_a_bare_miss_never_fails_a_structural_check():
    """ "The goblin misses." is a correct answer (fifth review of #760)."""
    outcomes = harness.check_rolls_when_asked(
        _goblin_transcript(
            "The dice show 4 for the knife.",
            damage_rolls=[DAMAGE_ROLL],
            attack_reply="The goblin misses.",
        )
    )
    structural = [
        outcome
        for outcome in outcomes
        if outcome.kind
        in (harness.CheckKind.STRUCTURE, harness.CheckKind.CONTRADICTION)
    ]
    assert all(outcome.passed for outcome in structural), [
        (outcome.name, outcome.evidence) for outcome in structural if not outcome.passed
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("Wren has 9 free skill points after the level.", [9]),
        ("She will have 9 skill points available to spend now.", [9]),
        (
            "If she raises it, which costs 2 points total, she will have 0 points left.",
            [],
        ),
    ),
)
def test_only_a_figure_inside_an_if_clause_about_a_spend_is_hypothetical(
    text, expected
):
    assert [figure for figure, _ in harness.skill_point_claims(text)] == expected


@pytest.mark.parametrize(
    "reply",
    (
        "At your table it's 11 by your ruling, but the campaigner survival target "
        "is 9, so I'll use 9.",
        "Normally the campaigner survival target would be 9; at your table it is "
        "11, but I'll go with 9.",
        "The campaigner survival target is 9, not your 11.",
    ),
)
def test_a_correction_beside_a_note_of_the_book_is_still_a_correction(
    reply, campaigner_rule
):
    outcomes = _house_rule_outcomes(reply, campaigner_rule)
    assert not named(outcomes, "does not correct him").passed


def test_without_the_weapons_damage_figures_is_a_precondition():
    said = "I cannot roll without the weapon's damage figures."
    assert harness.check_no_roll_disclaimer(said).passed


def test_dieting_is_not_a_death():
    assert harness.find_words("She is dieting.", harness.DEATH_WORDS, stems=True) == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("topic", "text", "recorded"),
    (
        ("campaigner survival", "the campaigner survival target is 11", True),
        ("weather", "it rains for 11 days", False),
        ("campaigner survival", "the target is 111", False),
    ),
)
def test_the_recorded_ruling_is_judged_by_its_topic(
    topic, text, recorded, campaigner_rule
):
    from django.contrib.auth import get_user_model

    from gm.models import GMSession, TableRuling

    owner = get_user_model().objects.create_user(username=f"rules-{topic[:5]}")
    session = GMSession.objects.create(user=owner, name="Table")
    TableRuling.objects.create(session=session, topic=topic, text=text)
    transcript = harness.Transcript(
        persona=harness.GAME_MASTER,
        session_id=session.pk,
        turns=(one_turn("Recorded."),),
        system_prompt=harness.GAME_MASTER.expected_prompt,
    )
    outcomes = harness.check_dm_house_rule(transcript, campaigner_rule)
    assert named(outcomes, "records the Game Master's rule").passed is recorded


# Recorded from the #760 board of 2026-09-29, run 5.


def test_a_target_in_brackets_is_stated(campaigner_rule):
    said = (
        "You roll 3d6, add your best attribute modifier, and compare the total to "
        "the track's target number (9 for Campaigners). You survive if the total "
        "is greater than or equal to the target."
    )
    assert harness.stated_targets(said, campaigner_rule)
    wrong = "Compare the total to the survival target number (12 for Campaigners)."
    assert harness.contradicting_survival_claims(wrong, campaigner_rule)


def test_a_blade_that_swings_wide_still_owes_the_asked_damage_roll():
    """What the attack did no longer decides whether the roll was owed."""
    attack = (
        "The goblin's rusted knife flashes in the gloom, but its strike is clumsy "
        "and wild. The blade swings wide, biting only the empty air as the "
        "creature stumbles slightly in its aggressive lunge."
    )
    outcomes = harness.check_rolls_when_asked(
        _goblin_transcript("It missed; no damage.", attack_reply=attack)
    )
    assert not named(outcomes, "on every turn that asked").passed


# -- the fourth review of #760: the exemptions are structural ----------------


@pytest.mark.parametrize(
    "reply",
    (
        "The blade bites for 3. Tell me if you brace against the goblin's weapon or fall back.",
        "It deals 3. Let me know how you answer the creature's strength: shove back?",
        "3 damage. Tell me: do you try to wrench away the goblin's weapon?",
    ),
)
def test_any_action_of_the_players_between_request_and_figure_exempts(reply):
    transcript = _goblin_transcript(reply, damage_rolls=[DAMAGE_ROLL], attack_reply=HIT)
    assert harness.monster_values_asked_for(transcript) == []


@pytest.mark.parametrize(
    "reply",
    (
        "Tell me if you know the goblin's Strength, so I can roll.",
        "Do you have the creature's stats? I need them to roll damage.",
    ),
)
def test_asking_what_the_player_knows_of_a_monster_value_still_counts(reply):
    assert harness.monster_values_asked_for(_goblin_transcript(reply, attack_reply=HIT))


@pytest.mark.parametrize(
    "attack_reply",
    (
        "The knife goes wide of your throat but rakes your shoulder, drawing blood.",
        "The goblin swings wide and meets only empty air.",
        "The goblin's knife misses. You are unhurt.",
        "The goblin misses.",
    ),
)
def test_only_the_server_roll_decides_the_damage_turn(attack_reply):
    """Hit or miss in the prose, the verdict follows the frames alone."""
    unrolled = harness.check_rolls_when_asked(
        _goblin_transcript("No damage to roll.", attack_reply=attack_reply)
    )
    rolled = harness.check_rolls_when_asked(
        _goblin_transcript(
            "The dice show 4.", damage_rolls=[DAMAGE_ROLL], attack_reply=attack_reply
        )
    )
    assert not named(unrolled, "on every turn that asked").passed
    assert named(rolled, "on every turn that asked").passed


@pytest.mark.parametrize(
    ("said", "wrong"),
    (
        ("Against the survival target (8 for Underworld, 12 for Campaigners).", True),
        ("Against the survival target (8 for Underworld, 9 for Campaigners).", False),
        ("The survival target (9 for Campaigners) must be rolled under.", True),
    ),
)
def test_a_figure_named_for_its_track_after_it_and_rolled_under(
    said, wrong, campaigner_rule
):
    assert bool(harness.contradicting_survival_claims(said, campaigner_rule)) is wrong


def test_the_damage_turn_asks_for_the_roll_whatever_the_attack_did():
    """So every turn owes a server roll and no prose decides it (fifth review)."""
    (scenario,) = [
        scenario
        for scenario in harness.build_scenarios()
        if scenario.key == "rolls_when_asked"
    ]
    assert scenario.turns[1].message.startswith("Whatever the attack did")


def test_a_target_named_with_which_is_is_stated(campaigner_rule):
    """Recorded from the #760 board of 2026-09-29, run 6."""
    said = (
        "That is not how the roll works. The survival check for the Campaigner "
        "track is 3d6 plus your best attribute modifier, and you must roll equal "
        "to or higher than the track's target, which is 9."
    )
    assert harness.stated_targets(said, campaigner_rule)
    wrong = "You must roll equal to or higher than the survival target, which is 12."
    assert harness.contradicting_survival_claims(wrong, campaigner_rule)


# -- the sixth review of #760 -------------------------------------------------


def _tally(kind, passes):
    tally = harness.CheckTally(name="x", kind=kind)
    for passed in passes:
        tally.outcomes.append(harness.verdict("x", kind, passed, "want", "got"))
    return tally


def test_a_report_only_check_never_decides_a_scenario():
    """Printed always, counted never: the prose monster-values reading."""
    result = harness.ScenarioResult(
        scenario=None,  # type: ignore[arg-type]
        tallies=[
            _tally(harness.CheckKind.STRUCTURE, (True, True, True)),
            _tally(harness.CheckKind.REPORT, (False, False, False)),
        ],
    )
    assert result.met
    assert harness.REQUIRED_PASS_RATE[harness.CheckKind.REPORT] == 0.0
    result.tallies.append(_tally(harness.CheckKind.RUBRIC, (False, False, True)))
    assert not result.met


def test_a_turn_with_no_plan_read_fails_loudly():
    """A server that stops sending plan payloads fails the rolls scenario."""
    transcript = _goblin_transcript(
        "The dice show 4.", damage_rolls=[DAMAGE_ROLL], plans=(), attack_plans=()
    )
    outcomes = harness.check_rolls_when_asked(transcript)
    assert not named(outcomes, "carried a plan the server read").passed


@pytest.mark.parametrize(
    "question",
    (
        "What Strength does the goblin have?",
        "What damage does its rusted knife do, and how strong is it?",
        "Please give the Strength of the goblin.",
        "Which way do you step?",
    ),
)
def test_any_planned_question_on_a_turn_that_owes_a_roll_fails(question):
    """No wording decides it: every turn in this scenario owes a roll."""
    transcript = _goblin_transcript(
        "Waiting.", plans=(({"action": "ask_gm", "question": question},),)
    )
    outcomes = harness.check_rolls_when_asked(transcript)
    assert not named(outcomes, "plans no question back").passed


# -- the seventh review of #760 -----------------------------------------------


def _reported_result():
    scenario = harness.Scenario(
        key="rolls_when_asked",
        title="t",
        issues=("#760",),
        persona=harness.PLAYER,
        turns=(),
        check=lambda transcript: [],
    )
    transcript = conversation(one_turn("It deals 3. Tell me the goblin's Strength."))
    return harness.ScenarioResult(
        scenario=scenario,
        transcripts=[transcript, transcript, transcript],
        tallies=[
            _tally(harness.CheckKind.STRUCTURE, (True, True, True)),
            _tally(harness.CheckKind.REPORT, (True, False, True)),
        ],
    )


def test_a_report_only_check_is_no_flake_rate_and_is_headed_as_reported(capsys):
    assert harness.report([_reported_result()], runs=3) is True
    printed = capsys.readouterr().out
    flakes = printed.split("FLAKE RATES")[1].split("REPORTED, NOT COUNTED")[0]
    assert "no check gave different answers on different runs" in flakes
    assert "Reported  rolls_when_asked · x (not counted)" in printed
    assert "FAILED  rolls_when_asked" not in printed
    assert "needs 0%" not in printed
    assert "1/1 checks met" in printed


# ---- The Games Master batch of 2026-09-29 (#794, #843) ----


def test_a_skill_checks_total_is_read_off_its_line():
    """The line is the sheet's own (characters.skill_checks.SkillCheck.line)."""
    from characters.skill_checks import SkillCheck

    check = SkillCheck(
        skill="stealth",
        level=2,
        attribute="dexterity",
        attribute_score=14,
        attribute_modifier=1,
        substituted=False,
        dice="2d6",
        faces=(3, 5),
        stated=False,
        dice_total=8,
        total=11,
        difficulty=12,
        outcome="failure",
        natural="",
    )

    assert harness._check_line_total(check.line) == 11
    assert harness._check_line_total("no check here") is None


def test_the_aide_dice_check_needs_a_server_roll():
    rolled = _transcript(gm_rolls=[{"expression": "1d6", "total": 4, "line": "🎲 1d6"}])
    none = _transcript()

    assert named(
        harness.check_aide_dice_words(rolled), "rolled what the Game Master asked"
    ).passed
    assert not named(
        harness.check_aide_dice_words(none), "rolled what the Game Master asked"
    ).passed


def test_a_players_check_with_a_difficulty_fails_the_player_scenario():
    """Only the Game Master sets a difficulty (the skill check's own rule)."""
    with_difficulty = _transcript(
        procedures=[
            {"kind": "skill_check", "ran": True, "note": "stealth check: ... = 9 vs 12"}
        ]
    )
    plain = _transcript(
        procedures=[
            {"kind": "skill_check", "ran": True, "note": "stealth check: ... = 9"}
        ]
    )

    fragment = "no difficulty, attribute or dice"
    assert not named(harness.check_player_skill_check(with_difficulty), fragment).passed
    assert named(harness.check_player_skill_check(plain), fragment).passed


# --------------------------------------------------------------------------
# #888: what a reply says of a finished session fight.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "name"),
    (
        ("Gorn falls dead beside the road.", "gorn"),
        ("The wolf lies dead in the snow.", "wolf"),
        ("With a last lunge Gorn slew the gray wolf.", "wolf"),
        ("Wren died of her wounds before dawn.", "wren"),
        ("The gray wolf's corpse lies in the ditch.", "wolf"),
    ),
)
def test_a_stated_death_is_read(reply, name):
    assert harness.death_claims(reply, name)


@pytest.mark.parametrize(
    ("reply", "name"),
    (
        # The killer is not the killed.
        ("Gorn killed the wolf with his last swing.", "gorn"),
        ("Gorn nearly died, but the wolf fled.", "gorn"),
        ("Wren is not dead; she lies unconscious.", "wren"),
        ("If the wolf had bitten once more, Wren would have died.", "wren"),
        ("Gorn survived; the wolf was killed.", "gorn"),
    ),
)
def test_a_hedged_or_misattributed_death_is_not(reply, name):
    assert not harness.death_claims(reply, name)


def test_wounds_and_victories_are_read_of_their_subject():
    assert harness.wound_claims("Wren is badly wounded and bleeding.", "wren")
    assert harness.wound_claims("Gorn took a wound to the arm.", "gorn")
    assert not harness.wound_claims("Wren is not wounded at all.", "wren")
    assert not harness.wound_claims("The wolf is wounded; Wren is untouched.", "wren")
    assert harness.victory_claims("The gray wolf stands victorious.", "wolf")
    assert harness.victory_claims("In the end Gorn prevailed.", "gorn")
    assert not harness.victory_claims("Gorn would have won with a sword.", "gorn")


def test_only_the_logs_dice_count_as_rolled():
    log = (
        "Gorn attack 1d20: [10] +6 = 16 vs TN 17 (need 17+) — miss\n"
        "Gorn damage 1d6-1: [2] -1 = 1\n"
        "Gray Wolf jogs 7 hex(es) toward Gorn\n"
        "survival save, 3d6: [3, 4, 5] = 12 ≤ 13"
    )
    numbers = harness.log_dice_numbers(log)
    assert {"10", "16", "2", "1", "3", "4", "5", "12"} <= numbers
    # A TN, a hex count and a save's target are on the log, not dice.
    assert not {"17", "7", "13", "20"} & numbers
