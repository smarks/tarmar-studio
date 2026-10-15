"""Drive the real Game Master model and check it behaves the way we expect (#373).

Every GM bug filed on 2026-09-09/10 -- #342, #352, #353, #356, #363, #370 --
was invisible to the ~3,200 tests in this repo, and for the same reason each
time: the suite mocks Ollama, and ``conftest.py`` makes that a hard property
of the run (#359). That is right -- CI must never depend on a model -- but it
leaves the one component those bugs lived in unexercised: the actual weights,
reading the actual prompt. We assert that the prompt *contains* the combat
matrix. We have never asserted that the model *obeys* it.

This is that check. It drives real conversations through the real
``/tarmar/gm/stream/`` view, as a real signed-in player or DM, and asserts
against what the server filed rather than against wording.

Design, because the model is non-deterministic:

**Structure and provenance over wording.** Most of what matters is checkable
without reading prose: a ``gm_roll`` frame exists and its ``total`` is the
number the reply quotes; a ``procedure`` frame ran and the narration's verdict
matches :class:`gm.ledger.DraftLedger`'s ``is_alive``; the skill-point figure
in the reply equals the ledger's ``remaining``. Those are exact, and they are
the checks that catch the bugs that actually happened.

**One-sided assertions where prose must be judged, and a required answer where
the server handed the GM one.** "Must not assert death on a natural 18"
survives a hundred rewordings, so the contradiction checks stay one-sided. But
since #605 a rules question is answered from ``rules_lookup``: the server hands
the GM the target, and the right answer states it and corrects a rule stated
wrongly. Declining ("I will not quote a number I was not given") was right
before #605 and is a failure now, so the survival scenarios also require the
lookup to have run and the target to be stated (issue #760). A correction
names the wrong premise in order to deny it -- "you need 9 or more, not 12 or
under", "a natural 18 never results in death" -- and the scanners read a
denied figure, a negated death and the losing side of the roll ("roll under 9
and she dies") as what they are, not as the claim they deny.

**Rules truth comes from the code, learned by running it.**
:func:`survival_rule_from_code` plays a year of prior experience through
``characters.skills.simulate_experience_years`` with the dice pinned, and reads
the target, the direction and the natural-roll overrides off what the rules
actually did. Nothing about the survival check is typed into this file, so the
harness cannot rot the way the prompt did. The combat numbers come straight
from ``characters.combat.MATRIX``.

**A flake rate, not a pass.** Each scenario runs ``--runs`` times (default 3).
Every check declares the pass rate its kind requires -- see :class:`CheckKind`
-- and the report prints the observed rate either way. A check that passes
twice and fails once is reported as flaky at 33%, never as green.

Why a script and not a pytest suite: pytest never collects this file
(``python_files = ["test_*.py", "tests.py"]``), ``scripts/`` is not in
``[tool.coverage.run] source``, and nothing here ever runs under pytest -- so
it cannot join the gate by accident, cannot move the coverage threshold, and
needs no change to ``conftest.py``'s httpx block. A marked pytest suite would
have to defeat that fixture, would inherit ``-n auto`` and the coverage
addopts, and would be one ``-m`` typo away from CI.

Run it against a live Ollama with the GM model loaded::

    DEBUG=true GM_MODEL=qwen3.6:27b-mlx uv run python scripts/gm_behaviour_check.py

It exits 0 when every check met its required pass rate, 1 when one did not,
and 0 with a SKIPPED banner when Ollama is unreachable. It is never part of
``./run check``.
"""

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import django

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tarmar_studio.settings")
django.setup()

import httpx  # noqa: E402
from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.test import Client  # noqa: E402
from django.urls import reverse  # noqa: E402

from characters import skills  # noqa: E402
from characters.combat import MATRIX  # noqa: E402
from characters.dice import DICE_IN_TEXT, location_skill_choices  # noqa: E402
from characters.skills import (  # noqa: E402
    SURVIVAL_RULE_DESCRIPTION,
    normalize_skill_name,
)
from common.middleware import PublicPathPassphraseMiddleware  # noqa: E402
from common.services import ollama_client  # noqa: E402
from gm import roster, summary  # noqa: E402
from gm.engine import MODE_AIDE, MODE_PLAY  # noqa: E402
from gm.ledger import DraftLedger  # noqa: E402
from gm.models import (  # noqa: E402
    CampaignState,
    GMEvent,
    GMSession,
    SceneEntity,
    StoryThread,
    SupplyItem,
    TableRuling,
)
from gm.services import get_system_message  # noqa: E402

User = get_user_model()

# The Django test client sends HTTP_HOST "testserver", which ALLOWED_HOSTS
# does not carry in any environment. Ask for a host every configuration
# allows rather than mutating the setting under the app.
CLIENT_HOST = "localhost"

# The track every survival scenario is written against. Named once so the
# question the GM is asked, the oracle it is checked against and the
# procedure marker it should emit can never name different tracks.
SURVIVAL_TRACK = "campaigner"


# --------------------------------------------------------------------------
# What the code actually does — learned by running it, never typed here.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SurvivalRule:
    """The prior-experience survival check as ``characters.skills`` plays it.

    Attributes:
        track: The track key these values were observed on.
        target: The survivability target the rules compared against.
        rolls_high: True when a *higher* total is the one that survives.
        auto_survive_roll: The natural dice total that forces survival.
        auto_death_roll: The natural dice total that forces death.
        described: The rule in the code's own words
            (``SURVIVAL_RULE_DESCRIPTION``), for the failure report.
    """

    track: str
    target: int
    rolls_high: bool
    auto_survive_roll: int
    auto_death_roll: int
    described: str

    @property
    def wrong_direction_is_low(self) -> bool:
        """True when a claim that the roll goes LOW contradicts the code."""
        return self.rolls_high


def _play_one_year(track: str, dice_total: int) -> dict[str, Any]:
    """Play a single year on ``track`` with the survival dice pinned.

    Args:
        track: The track key to play.
        dice_total: The total the three survival dice are to show.

    Returns:
        The year's ``survival_check`` dict as the rules recorded it.
    """
    faces = (1, 1, dice_total - 2)
    original = skills.roll_survival_dice
    try:
        skills.roll_survival_dice = lambda: (*faces, dice_total)
        result = skills.simulate_experience_years(
            1, 0, 18, track, 10, 10, 10, 10, 10, 10
        )
    finally:
        skills.roll_survival_dice = original
    return result["survival_check"]


def survival_rule_from_code(track: str = SURVIVAL_TRACK) -> SurvivalRule:
    """Discover the survival rule by making the rules engine perform it.

    Plays four pinned years -- one on the target, one a point under it, one on
    each natural extreme -- and reads the verdict off each. The direction, the
    target and both natural-roll overrides are therefore observations of the
    running code rather than constants copied into this file, which is the
    whole point: #370 happened because a number lived in two places and one of
    them drifted.

    Args:
        track: The track key to observe.

    Returns:
        What the rules did, as a :class:`SurvivalRule`.

    Raises:
        RuntimeError: If the rules engine disagrees with itself -- neither a
            total on the target nor one under it decided the year -- which
            would mean this oracle no longer describes the check.
    """
    target = int(_play_one_year(track, 10)["target"])
    on_target = bool(_play_one_year(track, target)["success"])
    under_target = bool(_play_one_year(track, target - 1)["success"])
    if on_target == under_target:
        raise RuntimeError(
            f"cannot read the survival direction off {track}: a total of "
            f"{target} and a total of {target - 1} both gave {on_target}"
        )
    auto_survive = next(
        total
        for total in range(18, 2, -1)
        if bool(_play_one_year(track, total)["success"])
    )
    auto_death = next(
        total for total in range(3, 19) if not _play_one_year(track, total)["success"]
    )
    return SurvivalRule(
        track=track,
        target=target,
        rolls_high=on_target,
        auto_survive_roll=auto_survive,
        auto_death_roll=auto_death,
        described=SURVIVAL_RULE_DESCRIPTION,
    )


@dataclass(frozen=True)
class MatrixQuestion:
    """One weapon-class-versus-armour-tier lookup, taken from the code.

    Attributes:
        weapon_class: The matrix row, e.g. ``"Heavy Striking"``.
        armour_tier: The matrix column, e.g. ``"Heavy"``.
        target_number: The base TN ``characters.combat.MATRIX`` holds for the
            pairing.
    """

    weapon_class: str
    armour_tier: str
    target_number: int

    @property
    def armour_phrase(self) -> str:
        """The armour tier as a question can say it aloud."""
        if self.armour_tier.lower() == "none":
            return "an unarmoured opponent"
        return f"an opponent in {self.armour_tier} armour"

    @property
    def question(self) -> str:
        """The question to put to the GM."""
        return (
            f"What is the base target number to hit {self.armour_phrase} "
            f"with a {self.weapon_class} weapon? Just the number."
        )


def matrix_question_from_code() -> MatrixQuestion:
    """A matrix lookup whose answer is unique, so a right number proves a lookup.

    Picks the pairing whose TN appears fewest times in the whole matrix. A
    pairing whose number is shared with a dozen others could be answered
    correctly by luck; the rarest one could not.

    Returns:
        The pairing and the number the code holds for it.
    """
    cells = [
        (weapon_class, tier, value)
        for weapon_class, row in MATRIX.items()
        for tier, value in row.items()
    ]
    counts: dict[int, int] = {}
    for _, _, value in cells:
        counts[value] = counts.get(value, 0) + 1
    weapon_class, tier, value = min(cells, key=lambda cell: (counts[cell[2]], cell[2]))
    return MatrixQuestion(
        weapon_class=weapon_class, armour_tier=tier, target_number=value
    )


# --------------------------------------------------------------------------
# Phrase sets. Each one is a fixed list of things a reply must or must not say.
# --------------------------------------------------------------------------

# #363: the GM told a player "I do not have access to a random number
# generator". The server has a dice roller and the prompt says so. Split in
# two because the live runs showed the phrases are not equally damning.
#
# These say the GM has no dice, or hand the dice to the person it is talking
# to. Nothing in the game makes any of them true, so context cannot excuse
# one.
HARD_ROLL_DISCLAIMERS: tuple[str, ...] = (
    "random number generator",
    "i do not have dice",
    "i don't have dice",
    "do not have access to dice",
    "don't have access to dice",
    "no way to roll",
    "roll it yourself",
    "please roll",
    "you'll need to roll",
    "you will need to roll",
    "you must roll",
    "as an ai",
    "simulate a roll",
    "simulate the roll",
)

# These say the GM is not rolling *this* — which is the bug when it means
# "I have no dice", and ordinary table talk when it means "not before the
# attack resolves". A live run produced "I cannot roll the damage yet because
# the attack result has not been determined", which is the second thing.
CAPABILITY_ROLL_DISCLAIMERS: tuple[str, ...] = (
    "i cannot roll",
    "i can't roll",
    "i am unable to roll",
    "i'm unable to roll",
    "i still cannot roll",
)

# What turns one of those into a statement about order rather than ability.
ROLL_SEQUENCING_WORDS: tuple[str, ...] = (
    "yet",
    "until",
    "before",
    "once you",
    "not been determined",
    "first need",
    "need to know",
    # "I cannot roll damage without these values" names a precondition, not
    # a missing dice roller (review of #760). Whether asking for those
    # values was right is its own check in rolls_when_asked. Only a lack of
    # figures: "I can't roll without physical dice" is #363 itself.
)

#: "Without" followed by the figures it lacks -- "without these values",
#: "without the weapon's damage figures" -- names a precondition. "Without
#: physical dice" is #363 itself and is not matched (third review of #760).
_WITHOUT_FIGURES = re.compile(
    r"\bwithout\s+(?:these|those|the|its|this|that|their|his|her)\s+"
    r"(?:[\w'’-]+\s+){0,3}?(?:values?|figures?|stats?|statistics|numbers?|"
    r"details|damage|modifiers?|scores?)\b",
    re.IGNORECASE,
)

# #353: for the Game Master the reply answers and stops. These are the
# invitations the aide must never close on -- and, inverted, the engagement a
# player is owed.
INVITATION_PHRASES: tuple[str, ...] = (
    "what do you do",
    "what happens next",
    "what would you like",
    "would you like me to",
    "how would you like to proceed",
    "let me know how",
    "let me know if",
    "shall i",
    # Not bare "want me to": "if you want me to resolve it mechanically, I
    # need the range" is the permitted question, not an offer of more.
    "do you want me to",
    # Not "if you would like me to ...": "If you would like me to determine
    # whether the guard saw them, please tell me:" asks for what it needs.
    "if you would like, i can",
    "if you'd like, i can",
    "just say the word",
    "happy to help",
)

# #353: no closing recap, no announced list.
RECAP_PHRASES: tuple[str, ...] = (
    "in summary",
    "in short",
    "tactical summary",
    "to summarize",
    "to summarise",
    "to recap",
    "here are three",
    "here's a quick",
    "here is a quick",
)

# #392: how the aide slipped back into running the game in session 28. The
# first set is its welcome to a Game Master who had only said hello; the
# second is the working it showed in the long creation turn.
GREETING_INTRODUCTION_PHRASES: tuple[str, ...] = (
    "your game master",
    "how would you like to begin",
    "we can start by",
    "ready to serve",
)
THINKING_ALOUD_PHRASES: tuple[str, ...] = (
    "let's assume",
    "let us assume",
    "no, the table",
    "no, level",
    "let me re-read",
    "let's re-read",
    "wait,",
)

# #352: a DM's stated fact is not a request that can be refused.
REFUSAL_PHRASES: tuple[str, ...] = (
    "i cannot allow",
    "i can't allow",
    "that is not allowed",
    "that's not allowed",
    "is not permitted",
    "i must cap",
    "i'll cap",
    "i will cap",
    "the maximum is 18",
    "cannot exceed 18",
    "can't exceed 18",
    "are you sure you want",
    "i'd recommend against",
    "i would recommend against",
    "please reconsider",
    "instead, let's",
    "instead, let us",
)

# #385: refusing a stated spread by arguing about how a rolled one is reached.
# Separate from REFUSAL_PHRASES because the wording is different -- nothing
# here mentions permission or a cap; it argues that the numbers could not have
# been generated, which is beside the point when nobody generated them.
GENERATION_ARGUMENT_PHRASES: tuple[str, ...] = (
    "mathematically impossible",
    "statistically impossible",
    "vanishingly small",
    "i cannot generate",
    "i can't generate",
    "cannot generate characters",
    "would require 108",
    "not invent numbers",
    "i must roll",
    "must use the server's dice roller",
)

# Whether a player's over-budget request drew the note it should have. A
# keyword set, judged loosely on purpose -- see CheckKind.RUBRIC -- but as
# whole words: "dm" inside "admit" once counted (issue #784).
BUDGET_NOTE_KEYWORDS: tuple[str, ...] = (
    "18",
    "maximum",
    "cannot",
    "can't",
    "not allowed",
    "over the",
    "above the",
    "beyond the",
    "point buy",
    "point-buy",
    "65",
    "3d6",
    "limit",
    "budget",
    "game master",
    "dm",
)

# #353: what an honest "I was not at your table" sounds like.
UNKNOWING_KEYWORDS: tuple[str, ...] = (
    "don't know",
    "do not know",
    # Added after a live run: "I do not have access to your previous session's
    # events" is the honest answer this check wants, and no earlier keyword
    # matched it. A keyword set is only as good as the wordings it has seen.
    "do not have access",
    "don't have access",
    "no access to",
    "have no way",
    "cannot see",
    "can't see",
    "no record of",
    "you determine",
    "wasn't at your table",
    "was not at your table",
    "can't know",
    "cannot know",
    "no way to know",
    "you'd have to",
    "you would have to",
    "you haven't told me",
    "you have not told me",
    "wasn't there",
    "was not there",
    "i wasn't",
    "that's your call",
    "that is your call",
    "up to you",
    "you decide",
    "tell me",
)

# #342: numbered choices are never exhaustive.
NOT_EXHAUSTIVE_KEYWORDS: tuple[str, ...] = (
    "anything else",
    "something else",
    "any other",
    "other action",
    "describe",
    "or do",
    "not limited",
    "your own",
    "not the only",
    "of course",
)

#: Stems, read with ``find_words(stems=True)`` everywhere: whole-word matching
#: once let "the character perishes" past a list holding "perish", and the
#: hand-added inflections that fixed it left "you die", "fatally" and the rest
#: still missing (issue #793). "Dying" is listed because the stem it comes from
#: loses its "e"; :data:`NOT_AN_INFLECTION` keeps "deadline" and "diet" out.
DEATH_WORDS: tuple[str, ...] = (
    # "Die" is also the noun -- "every die came up 6", "the last die" -- and
    # :func:`plain` sets the noun aside, so the stem reads only the verb in
    # all its forms ("she'll die", "can die", "should die") (review of #760).
    "die",
    "dying",
    "death",
    "kill",
    "perish",
    "slain",
    "dead",
    "does not make it",
    "doesn't make it",
    "don't make it",
    "do not make it",
    "does not survive",
    "doesn't survive",
    "did not survive",
    "didn't survive",
    "fatal",
    "corpse",
    "buried",
)

# Phrases that say the automatic-survival roll *is* a survival. A paragraph
# naming the roll and carrying one of these is explaining the rule, not
# delivering a verdict, even when it also names the automatic-death roll --
# the harness's own first live run flagged one of those as a failure.
#: Ways of saying the automatic-survival roll survives. A paragraph naming
#: that roll and carrying one of these is explaining the rule, not delivering
#: a death verdict.
#:
#: The noun forms were missing, and the GM reached for one as soon as it
#: could read the rule out of the code (issue #605): "a natural 3 on the dice
#: is always death, and a natural 18 is always survival" -- correct, and read
#: as a contradiction because the list knew "always survives" and not "always
#: survival". A check that goes red when the answer is right is a bug.
AUTO_SURVIVE_AFFIRMATIONS: tuple[str, ...] = (
    "automatic survival",
    "automatic success",
    "always survives",
    "always survive",
    "always survival",
    "always a survival",
    "is survival",
    "survival whatever",
    "survives regardless",
    "counts as a survival",
    "is a survival",
    "never a death",
    "not a death",
    "did not die",
    "does not die",
)

#: Stems, like :data:`DEATH_WORDS` (issue #793). "Surviving" is listed because
#: "survive" loses its "e" to reach it; "survival" is deliberately absent, since
#: "she failed the survival roll and died" names the check, not the outcome.
SURVIVAL_WORDS: tuple[str, ...] = (
    "survive",
    "surviving",
    "live",
    "alive",
    "walks away",
    "walked away",
    "made it through",
    "comes through",
    "came through",
)


# --------------------------------------------------------------------------
# Text scanning.
# --------------------------------------------------------------------------


# The model writes markdown, and "**1** free skill point" is the same claim as
# "1 free skill point" -- but the asterisks break every pattern that expects a
# number followed by a space. Normalised once, at the door of each scanner, so
# no pattern has to carry markdown of its own.
_EMPHASIS = re.compile(r"[*`]+")


def plain(text: str) -> str:
    """``text`` with markdown emphasis removed, for scanning.

    Args:
        text: A reply, as the model wrote it.

    Returns:
        The same prose without ``*`` or backtick emphasis, and with the noun
        "die" -- "every die came up 6" -- spelt so no death scan reads it as
        the verb. The length is kept, so positions still line up.
    """
    return _NOUN_DIE.sub(
        lambda found: found.group(0).replace("die", "d_e"), _EMPHASIS.sub("", text)
    )


#: "Die" as the noun: after a determiner or an ordinal, or before what a die
#: does ("the die shows 6"). The verb is "you die", "she'll die", "to die".
_NOUN_DIE = re.compile(
    r"\b(?:a|an|the|each|every|one|last|first|second|third|single|other|that|"
    r"this|same|any)\s+die\b"
    r"|\bdie\s+(?:shows?|showed|showing|came|comes|landed|lands|roll(?:s|ed)?|"
    r"faces?|reads?|turns?\s+up)\b",
    re.IGNORECASE,
)


#: Words a stem in :data:`DEATH_WORDS` or :data:`SURVIVAL_WORDS` runs on into
#: that are not a death or a survival (issue #793).
NOT_AN_INFLECTION: frozenset[str] = frozenset(
    {
        "deadline",
        "deadlines",
        "deadly",
        "deadlock",
        "diet",
        "diets",
        "diesel",
        "dietary",
        "dieting",
        "deadpan",
        "deadfall",
        "deathly",
        "killer",
        "killers",
        "lively",
        "livelier",
        "livelihood",
        "livelihoods",
        "liver",
        "livery",
        "livestock",
        # Stems in BACKGROUND_STANDS_WORDS (issue #754).
        "standard",
        "standards",
        "remainder",
        "remainders",
    }
)


@dataclass(frozen=True)
class PhraseHit:
    """One phrase found in a reply, with enough text around it to judge it.

    Attributes:
        phrase: The phrase that matched, as written in the phrase set.
        context: The surrounding text, for the failure report.
    """

    phrase: str
    context: str

    def __str__(self) -> str:
        return f"{self.phrase!r} in {self.context!r}"


def find_phrases(
    text: str, phrases: Iterable[str], window: int = 45
) -> list[PhraseHit]:
    """Every phrase from ``phrases`` present in ``text``, with its context.

    Args:
        text: The reply to scan.
        phrases: Lower-case phrases to look for.
        window: Characters of context to keep on each side of a hit.

    Returns:
        One hit per phrase found, in phrase-set order.
    """
    text = plain(text)
    lowered = text.lower()
    hits: list[PhraseHit] = []
    for phrase in phrases:
        position = lowered.find(phrase)
        if position < 0:
            continue
        start = max(0, position - window)
        end = min(len(text), position + len(phrase) + window)
        hits.append(PhraseHit(phrase, text[start:end].replace("\n", " ")))
    return hits


def find_words(
    text: str, words: Iterable[str], window: int = 45, *, stems: bool = False
) -> list[PhraseHit]:
    """Like :func:`find_phrases`, but only on whole-word matches.

    "kills" lives inside "skills", and a substring scan for death words once
    read "skip weight and discuss her skills/equipment" as the narration
    killing the character. Verdict words need boundaries; the phrase sets that
    carry punctuation and symbols do not, which is why this is a second
    function rather than a flag flipped everywhere.

    The boundary at the *end* is the other half of that trap, in the other
    direction: a ``DEATH_WORDS`` list holding "perish" let "perishes" through
    untouched. ``stems=True`` keeps the boundary in front and lets the word
    run on behind it, which catches the inflections without letting "trust"
    match inside "distrust" -- there is no boundary before that "t".

    A stem runs on into words that are not its inflections -- "dead" into
    "deadline", "live" into "lively" -- and those are listed in
    :data:`NOT_AN_INFLECTION` and skipped.

    Args:
        text: The reply to scan.
        words: Lower-case words or phrases to look for.
        window: Characters of context to keep on each side of a hit.
        stems: Match each word and whatever it inflects into.

    Returns:
        One hit per word found, in word-set order.
    """
    plain_text = plain(text)
    hits: list[PhraseHit] = []
    tail = r"\w*" if stems else r"\b"
    for word in words:
        match = next(
            (
                found
                for found in re.finditer(
                    rf"\b{re.escape(word)}{tail}", plain_text, re.IGNORECASE
                )
                if not stems or found.group(0).casefold() not in NOT_AN_INFLECTION
            ),
            None,
        )
        if match is None:
            continue
        start = max(0, match.start() - window)
        end = min(len(plain_text), match.end() + window)
        hits.append(PhraseHit(word, plain_text[start:end].replace("\n", " ")))
    return hits


@dataclass(frozen=True)
class RuleClaim:
    """A claim about the survival check parsed out of a reply.

    Attributes:
        snippet: The sentence the claim was read from, for the report.
        dice_count: How many d6 the claim names, when it names any.
        target: The target number the claim names, when it names one.
        says_low: True when the claim says the roll must go low *to survive*.
        says_high: True when the claim says the roll must go high to survive.
        track: The track the target was named for, when the clause names one
            beside it -- the lookup's all-tracks answer puts every track's
            target in one sentence (issue #760).
        clause: The clause inside ``snippet`` the claim came from.
        figures: Every ``(target, track)`` the clause states; ``target`` and
            ``track`` are the first of them.
    """

    snippet: str
    dice_count: int | None = None
    target: int | None = None
    says_low: bool = False
    says_high: bool = False
    track: str | None = None
    clause: str = ""
    figures: tuple[tuple[int, str | None], ...] = ()


_DICE_COMPARISON = re.compile(
    r"(\d+)\s*d\s*6\s*(<=|≤|<|>=|≥|>|=|or\s+(?:less|lower|under|more|higher|over))"
    r"\s*(\d+)?",
    re.IGNORECASE,
)
# "The target for the Campaigner track is 9" names what the target is for
# between the word and the figure; the live run of 2026-09-28 read that
# correct answer as no target at all (issue #760).
_NAMED_TARGET = re.compile(
    r"(?:targets?(?:\s+numbers?)?|thresholds?|difficulty|survivability|tn)"
    r"(?:\s+(?:for|by|at|on|in)\s+(?:the\s+|this\s+|a\s+|your\s+|my\s+)?"
    r"[a-z'-]+(?:\s+[a-z'-]+){0,2}?)?\s*"
    # "the track's target number (9 for Campaigners)" puts the figure in
    # brackets (board of 2026-09-29, run 5).
    # "The track's target, which is 9" (board of 2026-09-29, run 6).
    r"(?:number\s*)?(?:,?\s*(?:which|that)\s+is|of|is|:|=)?\s*\(?\s*(\d{1,2})\b",
    re.IGNORECASE,
)
_LOW_DIRECTION = re.compile(
    r"\broll\s+(?:it\s+)?(?:under|below|low)\b|\bor\s+(?:less|lower|under|fewer)\b"
    r"|\bat\s+most\b|\bequal\s+to\s+or\s+(?:less|lower)\b|\bmust\s+be\s+(?:under|below)\b"
    r"|\b(?:under|below)\s+(?:the\s+target|\d)|\b(?:less|lower|fewer)\s+than\b"
    r"|\bbeat\s+it\s+by\s+rolling\s+low\b"
    # "The target must be rolled under" (fourth review of #760).
    r"|\brolled\s+(?:under|below|low)\b",
    re.IGNORECASE,
)
_HIGH_DIRECTION = re.compile(
    r"\broll\s+(?:it\s+)?(?:over|above|high)\b|\bor\s+(?:more|higher|greater|better)\b"
    r"|\bat\s+least\b|\bmeet\s+or\s+beat\b|\bgreater\s+than\s+or\s+equal\b"
    r"|\bmust\s+(?:meet|beat|exceed)\b|\b(?:over|above)\s+(?:the\s+target|\d)"
    r"|\b(?:more|higher|greater)\s+than\s+\d|\brolled\s+(?:over|above|high)\b",
    re.IGNORECASE,
)
_LOW_OPERATORS = ("<=", "≤", "<", "or less", "or lower", "or under")
_HIGH_OPERATORS = (">=", "≥", ">", "or more", "or higher", "or over")

# "you must roll 12 or higher", "survive on 9 or more" -- a threshold stated
# without the word "target" at all, which is how #370's wrong number was
# actually phrased. Sentences about a *natural* roll are exempt: "a natural 3
# or less is death" names an override, not the track's target.
#: What a count is of, when a figure is one rather than a threshold: "5 or
#: more years", "at least 3 dice".
_COUNTED_NOUN = r"(?!\s+(?:years?|dice|d6|times|points?|skill|levels?|days?)\b)"
_BARE_THRESHOLD = re.compile(
    r"\b(\d{1,2})\s+or\s+(?:higher|more|greater|better|above|over|less|lower|"
    rf"under|fewer|below)\b{_COUNTED_NOUN}",
    re.IGNORECASE,
)
# "The result must equal or exceed 9", "you need at least 9": the threshold
# named before its figure, which is how a correct answer often puts it and
# which the target check read as no answer at all (issue #760).
_LEADING_THRESHOLD = re.compile(
    r"\b(?:at\s+least|at\s+most|(?:equal|meet)s?\s+or\s+(?:exceed|beat)s?|reach(?:es)?|"
    r"(?:equal\s+to\s+or\s+(?:greater|more|higher)|(?:greater|more|higher)\s+than"
    r"\s+or\s+equal\s+to)(?:\s+than)?|"
    rf"beats?|exceeds?)\s+(?:a\s+)?(?:total\s+of\s+)?(\d{{1,2}})\b{_COUNTED_NOUN}",
    re.IGNORECASE,
)
#: A part about a natural roll: named so, or as the faces that make one
#: ("6, 6, and 6", "6-6-6", "three sixes") -- the board of 2026-09-29 read
#: "if the dice come up 6, 6, and 6, the character survives even if the
#: total is below the target" as the backwards rule.
_NATURAL_ROLL = re.compile(
    r"\bnatural\b|\bautomatic\b|\bregardless\b"
    r"|\b(\d)\s*(?:,|-)\s*\1\s*(?:,\s*)?(?:and\s+|-\s*)\1\b"
    r"|\b(?:three|all)\s+(?:sixes|ones)\b",
    re.IGNORECASE,
)

# A figure or a direction the reply *denies* is not one it claims. Once the GM
# could read the rule out of the code (issue #605) it started correcting a
# wrong premise by naming it -- "you need 9 or higher, not 12 or under", "the
# campaigner survival roll is not 12 or under, the target is 9" -- and the
# parser read the denied figure and the denied direction as adopted ones.
#
# A denial runs from the negation to where the clause turns: a comma, a dash,
# "but", or the clause's end. So the denial is found wherever it sits,
# before the figure it governs or after the one it corrects (issue #760; the
# first cut looked only at a clause's head, and "9 or higher, not 12 or
# under" still failed). The stretch is blanked rather than dropped, so every
# position after it still lines up with the original clause.
#
# Blanking errs toward silence: "you do not need 9 or more" denies the true
# rule and is read as no claim at all. That is the safe side for a one-sided
# check, and a reply that says only that now fails the checks that require
# the target to be stated.
_DENIAL = re.compile(
    r"\b(?:is not|are not|isn't|aren't|does not|doesn't|do not|don't|did not|"
    r"didn't|cannot|can't|won't|will not|never|not|no longer|rather than|"
    r"instead of)\b",
    re.IGNORECASE,
)
_DENIAL_ENDS = re.compile(
    r",|:|\s[-–—]{1,2}\s|—|…|\.\.\.|\b(?:but|and|so|which|while|whereas|instead)\b",
    re.IGNORECASE,
)

_DENIED_FIGURE = re.compile(
    r"\b(?:not|isn't|is not|are not|aren't|does not|doesn't|do not|don't|never|"
    r"rather than|instead of|no longer)\b[^.;:]{0,40}?\b(\d+)\b",
    re.IGNORECASE,
)

# What a direction is attached to. "Roll under 9 and the character dies" is
# the losing side of a roll that goes high, stated correctly; "roll under 9 to
# survive" is #370. Before #605 the GM never described either side, so any
# "under" in a survival sentence could be read as the second (issue #760).
_DEATH_OUTCOME = re.compile(
    r"\b(?:di(?:e|es|ed)|dying|deaths?|dead|kill(?:s|ed|ing)?|perish\w*|slain|"
    r"fatal\w*|fail(?:s|ed|ing|ure)?)\b",
    re.IGNORECASE,
)
# "Survival" names the check as often as the outcome: in "roll under 9 on the
# survival check and the character dies" the outcome is the death, so the
# check's own name, and "survivability", are not read as one.
_SURVIVAL_OUTCOME = re.compile(
    r"\b(?:surviv(?!ab)(?!al\s+(?:roll|check|dice|die|target|save|throw|number|"
    r"test)s?\b)\w*|live[sd]?|alive|succeed\w*|success\w*|pass(?:es|ed)?|"
    r"makes?\s+it)\b",
    re.IGNORECASE,
)
# "does not survive" is an outcome of death, and a negation, so it is read
# on the original clause rather than on the one with its denials blanked.
#
# The verb is often left off once the sentence has said it: "9 or greater
# means you survive the year, while 8 or lower means you do not" (recorded on
# 2026-09-29; issue #760). A negated auxiliary that ends its clause stands for
# the survival the clause already named.
_NEGATED_SURVIVAL = re.compile(
    r"\b(?:does\s+not|doesn't|do\s+not|don't|did\s+not|didn't|will\s+not|won't|"
    r"cannot|can't|never|fails?\s+to|not)\s+surviv(?:e|es|ed)\b"
    r"|\b(?:no\s+one|nobody|none)\b[^.;]{0,30}?\bsurviv(?:e|es|ed)\b"
    r"|\b(?:does\s+not|doesn't|do\s+not|don't|did\s+not|didn't|will\s+not|won't)"
    r"(?=\s*(?:[.;!,]|$))",
    re.IGNORECASE,
)
#: A survival the reply denies in so many words, which is a death: "a natural
#: 18 never survives", "no one survives a natural 18" (review of #760). The
#: elided "you do not" of :data:`_NEGATED_SURVIVAL` is left out: on its own
#: it answers whatever was asked, a death or a survival alike.
_DENIED_SURVIVAL = re.compile(
    r"\b(?:does\s+not|doesn't|do\s+not|don't|did\s+not|didn't|will\s+not|won't|"
    r"cannot|can't|never|fails?\s+to|not)\s+surviv(?:e|es|ed)\b"
    r"|\b(?:no\s+one|nobody|none)\b[^.;]{0,30}?\bsurviv(?:e|es|ed)\b",
    re.IGNORECASE,
)
_UNLESS = re.compile(r"\bunless\b", re.IGNORECASE)
# "Roll under 12 or the character dies": the death is the alternative, so the
# direction is the survival condition after all.
_ALTERNATIVE = re.compile(r"\b(?:or|otherwise|else|unless)\b", re.IGNORECASE)


def _clauses(sentence: str) -> list[str]:
    """A sentence's clauses, split where a negation stops carrying.

    On ";" only. Splitting on ":" too cut "Survival target for campaigner:
    12" into a label and a bare number, so the claim was neither flagged nor
    credited (review of #760); a denial now stops at a colon instead.
    """
    return [part.strip() for part in sentence.split(";") if part.strip()]


# "No less than 9" is "at least 9", and "not less than 9" too; read as roll-low
# before (review of #760). Rewritten before anything else reads the clause.
_NO_LESS_THAN = re.compile(r"\bno[t]?\s+(?:less|lower|fewer)\s+than\b", re.IGNORECASE)
_NO_MORE_THAN = re.compile(
    r"\bno[t]?\s+(?:more|higher|greater)\s+than\b", re.IGNORECASE
)


#: The mirror of "no less than": a bound on the high side, denied, is a
#: ceiling. "Must not exceed 12", "can't be more than 12" and "cannot go
#: above 12" are #370's backwards rule and passed (second review of #760).
_NOT_ABOVE = re.compile(
    r"\b(?:must\s+not|mustn't|may\s+not|cannot|can't|can\s+not|should\s+not|"
    r"shouldn't|does\s+not|doesn't|do\s+not|don't|is\s+not|isn't|not)\s+"
    r"(?:be\s+|go\s+|come\s+|get\s+|roll\s+)?"
    r"(?:exceed|(?:more|higher|greater)\s+than|above|over)\b",
    re.IGNORECASE,
)
_NOT_BELOW = re.compile(
    r"\b(?:must\s+not|mustn't|may\s+not|cannot|can't|can\s+not|should\s+not|"
    r"shouldn't|does\s+not|doesn't|do\s+not|don't|is\s+not|isn't|not)\s+"
    r"(?:be\s+|go\s+|come\s+|get\s+|roll\s+)?"
    r"(?:fall\s+short\s+of|(?:less|lower|fewer)\s+than|below|under)\b",
    re.IGNORECASE,
)
#: Number words, so "nine or more" is read as "9 or more". "One" is left
#: alone: "no one survives" is not a figure.
_NUMBER_WORD_VALUES = {
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
}
_NUMBER_WORD = re.compile(
    r"\b(" + "|".join(_NUMBER_WORD_VALUES) + r")\b", re.IGNORECASE
)


def _normalised(clause: str) -> str:
    """``clause`` with its bounds and figures said plainly (review of #760).

    "No less than 9" and "not below 9" become "at least 9"; "no more than
    12", "must not exceed 12" and "can't be more than 12" become "at most
    12"; "nine" becomes "9".
    """
    said = _NO_LESS_THAN.sub("at least", clause)
    said = _NO_MORE_THAN.sub("at most", said)
    said = _NOT_ABOVE.sub("at most", said)
    said = _NOT_BELOW.sub("at least", said)
    return _NUMBER_WORD.sub(
        lambda found: str(_NUMBER_WORD_VALUES[found.group(1).lower()]), said
    )


def denied_positions(sentence: str) -> set[int]:
    """Where in ``sentence`` a figure is named in order to deny it.

    Positions rather than values, so the same number denied and then
    asserted -- "it is not 12 or under; you need 12 or under" -- is read as
    one denial and one claim, and the claim still fails.
    """
    return {match.start(1) for match in _DENIED_FIGURE.finditer(sentence)}


def without_denials(clause: str) -> str:
    """``clause`` with every denied stretch blanked, positions unchanged.

    Args:
        clause: One clause of a reply.

    Returns:
        The same text with each stretch from a negation to the next comma,
        dash, "but" or the clause's end replaced by spaces.
    """
    kept = list(clause)
    for denial in _DENIAL.finditer(clause):
        turn = _DENIAL_ENDS.search(clause, denial.end())
        end = turn.start() if turn else len(clause)
        kept[denial.start() : end] = " " * (end - denial.start())
    return "".join(kept)


# Where a clause changes subject. "You need 9 or more to survive, and a natural
# 18 is an automatic survival" states the target in one part and an override
# in the other; setting the whole clause aside for naming a natural roll hid
# the target (issue #760).
_SUBJECT_TURNS = re.compile(
    r",|;|\s[-–—]{1,2}\s|—|\b(?:and|but|while|whereas)\b", re.IGNORECASE
)


def without_naturals(clause: str) -> str:
    """``clause`` with each part about a natural roll blanked, positions unchanged.

    A natural roll's figure is an override, not the track's target, and "a
    natural 3 or less is death" does not say which way the roll goes.

    Args:
        clause: One clause, usually with its denials already blanked.

    Returns:
        The same text with every part naming a natural or automatic roll
        replaced by spaces.
    """
    kept = list(clause)
    bounds = [
        0,
        *(turn.start() for turn in _SUBJECT_TURNS.finditer(clause)),
        len(clause),
    ]
    for start, end in zip(bounds, bounds[1:], strict=False):
        if _NATURAL_ROLL.search(clause, start, end):
            kept[start:end] = " " * (end - start)
    # "Even if the total is below the target" concedes a case; it states no
    # rule. Everything from the concession to the clause's end is set aside.
    for concession in _CONCESSION.finditer(clause):
        kept[concession.start() :] = " " * (len(clause) - concession.start())
        break
    return "".join(kept)


_CONCESSION = re.compile(r"\beven\s+(?:if|though|when|with)\b", re.IGNORECASE)


def _track_pattern(track: str) -> str:
    """A track key as prose writes it: ``civil_service`` is "civil service"."""
    return r"[\s_-]".join(re.escape(part) for part in track.split("_"))


_TRACK_NAME = re.compile(
    r"\b("
    + "|".join(_track_pattern(track) for track in skills.SURVIVAL_TARGETS)
    + r")",
    re.IGNORECASE,
)


def _track_key(said: str) -> str:
    """The ``SURVIVAL_TARGETS`` key a track name in prose stands for."""
    return re.sub(r"[\s-]", "_", said.casefold())


_FIGURE_FOR_TRACK = re.compile(
    r"(\d{1,2})\s*(?:\+|or\s+(?:more|higher|better|above))?\s+(?:for|on)\s+"
    r"(?:the\s+)?("
    + "|".join(_track_pattern(track) for track in skills.SURVIVAL_TARGETS)
    + r")",
    re.IGNORECASE,
)


def _nearest_track(clause: str, position: int) -> str | None:
    """The track a figure at ``position`` is named for, if the clause names one.

    Prose names the track first -- "the campaigner target is 9, and
    underworld is 8" -- so the nearest track before the figure wins, and one
    after it counts only when none comes before ("a target of 8 for
    underworld").

    A track named straight after the figure with "for" is that figure's
    whatever came before it: "(8 for Underworld, 12 for Campaigners)" puts
    the 12 on the campaigner track (fourth review of #760).
    """
    named_after = _FIGURE_FOR_TRACK.match(clause, position)
    if named_after is not None:
        return _track_key(named_after.group(2))
    before = [
        match for match in _TRACK_NAME.finditer(clause) if match.start() < position
    ]
    if before:
        return _track_key(before[-1].group(1))
    after = _TRACK_NAME.search(clause, position)
    return _track_key(after.group(1)) if after else None


def _outcome_of(clause: str, blanked: str, start: int, end: int, after_to: int) -> str:
    """Whether the direction at ``start:end`` is the way to survive or to die.

    The first outcome after the direction decides it; failing that, the last
    one before it; failing both, the sentence is describing the check itself,
    which is the way to survive. An outcome inside a denied stretch ("a
    natural 18 never results in death") is not one, except a denied survival,
    which is a death.

    Args:
        clause: The clause as written, less any part about a natural roll.
        blanked: The same, with its denials blanked too.
        start: Where the direction starts.
        end: Where the direction ends.
        after_to: Where the next direction starts, which bounds the search.

    Returns:
        ``"death"`` or ``"survival"``.
    """

    def outcomes_in(lower: int, upper: int) -> list[tuple[int, str]]:
        found = [
            (match.start(), "death")
            for match in _NEGATED_SURVIVAL.finditer(clause, lower, upper)
        ]
        found += [
            (match.start(), "death")
            for match in _DEATH_OUTCOME.finditer(blanked, lower, upper)
        ]
        found += [
            (match.start(), "survival")
            for match in _SURVIVAL_OUTCOME.finditer(blanked, lower, upper)
            if not _NEGATED_SURVIVAL.search(
                clause[max(0, match.start() - 12) : match.end()]
            )
        ]
        return sorted(found)

    following = outcomes_in(end, after_to)
    if following:
        position, outcome = following[0]
        if outcome == "death" and _ALTERNATIVE.search(clause[end:position]):
            return "survival"
        # "Unless his total fell below 9, he survived": the direction under
        # "unless" is the other outcome (board of 2026-09-29, run 1).
        unless = list(_UNLESS.finditer(clause, 0, start))
        if unless and "," not in clause[unless[-1].end() : start]:
            return "death" if outcome == "survival" else "survival"
        return outcome
    preceding = outcomes_in(0, start)
    if not preceding:
        return "survival"
    position, outcome = preceding[-1]
    # "You survive unless you roll above 12": the direction after "unless" is
    # the other outcome (second review of #760).
    if _UNLESS.search(clause[position:start]):
        return "death" if outcome == "survival" else "survival"
    return outcome


#: The inclusive bounds a threshold can be written with, low and high.
_INCLUSIVE_LOW = re.compile(
    r"or\s+(?:less|lower|under|fewer|below)|at\s+most|<=|≤", re.I
)
_INCLUSIVE_HIGH = re.compile(
    r"or\s+(?:more|higher|greater|better|above|over)|at\s+least|>=|≥", re.I
)


def _as_survival_target(figure: int, bound: str, outcome: str) -> int:
    """The survival target a threshold states, whichever side it names.

    "If it is 8 or lower, you do not survive" names the losing side of a
    target of 9. The board of 2026-09-28 read that correct answer as a claim
    that the target is 8 (issue #760). An inclusive bound on the dying side
    is one step from the target; a strict one ("under 9 and she dies") is the
    target itself, and a surviving side is the target as written.

    Args:
        figure: The number the reply wrote.
        bound: The words or operator that bound it ("or lower", "<=").
        outcome: ``"death"`` or ``"survival"``, from :func:`_outcome_of`.
    """
    if outcome != "death":
        return figure
    if _INCLUSIVE_LOW.search(bound):
        return figure + 1
    if _INCLUSIVE_HIGH.search(bound):
        return figure - 1
    return figure


#: A track named beside its figure: "Campaigner 9", "campaigner: 9", a table
#: row "| Campaigner | 12 |". The lookup's all-tracks answer, and tables of
#: it, put the targets this way; neither was read (review of #760).
_TRACK_FIGURE = re.compile(
    r"\b("
    + "|".join(_track_pattern(track) for track in skills.SURVIVAL_TARGETS)
    + r")(?:\s+track)?\s*[:|=–-]?\s*\|?\s*(\d{1,2})\b"
    + _COUNTED_NOUN,
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ClauseReading:
    """One clause as the survival scanners read it.

    Attributes:
        clause: The clause as written, "no less than" said plainly.
        as_said: The same with its parts about a natural roll blanked.
        blanked: The same with its denials blanked too.
        figures: Every ``(target, track)`` the clause states, in order, each
            figure given as the survival target it implies.
    """

    clause: str
    as_said: str
    blanked: str
    figures: tuple[tuple[int, str | None], ...]


def read_clause(clause: str) -> ClauseReading:
    """Read one clause's survival figures (issue #760)."""
    clause = _normalised(clause)
    as_said = without_naturals(clause)
    blanked = without_naturals(without_denials(clause))
    found: list[tuple[int, int, str | None]] = []
    for match in _NAMED_TARGET.finditer(blanked):
        found.append(
            (
                match.start(1),
                int(match.group(1)),
                _nearest_track(blanked, match.start(1)),
            )
        )
    for pattern in (_BARE_THRESHOLD, _LEADING_THRESHOLD):
        for match in pattern.finditer(blanked):
            outcome = _outcome_of(
                as_said, blanked, match.start(), match.end(), len(blanked)
            )
            figure = _as_survival_target(int(match.group(1)), match.group(0), outcome)
            found.append(
                (match.start(1), figure, _nearest_track(blanked, match.start(1)))
            )
    for match in _TRACK_FIGURE.finditer(blanked):
        found.append((match.start(2), int(match.group(2)), _track_key(match.group(1))))
    # "(8 for Underworld, 12 for Campaigners)": the figure first, then its
    # track (fourth review of #760).
    for match in _FIGURE_FOR_TRACK.finditer(blanked):
        found.append((match.start(1), int(match.group(1)), _track_key(match.group(2))))
    comparison = _DICE_COMPARISON.search(blanked)
    if comparison is not None and comparison.group(3):
        found.append(
            (
                comparison.start(3),
                _compared_target(comparison, blanked, as_said),
                _nearest_track(blanked, comparison.start(3)),
            )
        )
    by_position: dict[int, tuple[int, str | None]] = {}
    for position, figure, track in sorted(found):
        by_position.setdefault(position, (figure, track))
    return ClauseReading(
        clause=clause,
        as_said=as_said,
        blanked=blanked,
        figures=tuple(by_position[position] for position in sorted(by_position)),
    )


def _compared_target(comparison: re.Match[str], blanked: str, as_said: str) -> int:
    """The survival target a dice comparison states ("3d6 <= 8 and she dies" is 9)."""
    outcome = _outcome_of(
        as_said, blanked, comparison.start(2), comparison.end(), len(blanked)
    )
    return _as_survival_target(int(comparison.group(3)), comparison.group(2), outcome)


# A sentence is read for survival claims only when it is about the survival
# check, so the combat matrix's own target numbers in the same reply are never
# mistaken for one.
_SURVIVAL_CONTEXT: tuple[str, ...] = (
    "surviv",
    "death",
    "die",
    "dies",
    "dead",
    "perish",
    "kill",
    "prior experience",
    "each year",
    "per year",
)


# Words that make a passage report on a claim rather than make it (#380): a
# refusal to state it, a negation of it, or the Game Master's own ruling named
# and followed. A scanner that matches the vocabulary of an offence inside a
# sentence refusing it cries wolf, and #373 is explicit that a harness which
# cries wolf is worse than none. One predicate, shared by every check that
# needs it, rather than a bespoke exemption in each.
#
# "Nor do I confirm whether a roll of 18 results in death" is the refusal #605
# was filed over, and it read as a death verdict because "nor" was not here
# (issue #760).
_REPORTS_RATHER_THAN_CLAIMS = re.compile(
    r"\b(?:cannot|can\s+not|can't|will\s+not|won't|do\s+not|don't|does\s+not|"
    r"doesn't|unable\s+to|nor)\s+(?:\w+\s+){0,2}?"
    r"(?:confirm|verify|quote|know|say|tell|access|see|state)\b"
    r"|\bthere\s+is\s+no\b|\bno\s+confirmation\b"
    r"|\byou(?:\s+have)?\s+ruled\b|\bas\s+you\s+(?:have\s+)?ruled\b",
    re.IGNORECASE,
)


def reports_rather_than_claims(text: str) -> bool:
    """Whether ``text`` refuses, negates or follows a ruling instead of claiming (#380).

    Args:
        text: A sentence or paragraph of a reply.

    Returns:
        True when the passage is reporting on a claim -- "I cannot confirm
        whether the roll succeeded", "since you have ruled that there is no
        confirmation roll" -- rather than making it.
    """
    return bool(_REPORTS_RATHER_THAN_CLAIMS.search(plain(text)))


def _sentences(text: str) -> Iterator[str]:
    """Yield the reply's sentences, so a claim is judged in its own clause."""
    for chunk in re.split(r"(?<=[.!?;:\n])\s+", plain(text)):
        stripped = chunk.strip()
        if stripped:
            yield stripped


def _directions(blanked: str) -> list[tuple[int, int, str]]:
    """Every direction the clause states, as ``(start, end, "low"|"high")``."""
    found = [
        (match.start(), match.end(), "low")
        for match in _LOW_DIRECTION.finditer(blanked)
    ]
    found += [
        (match.start(), match.end(), "high")
        for match in _HIGH_DIRECTION.finditer(blanked)
    ]
    comparison = _DICE_COMPARISON.search(blanked)
    if comparison is not None:
        operator = comparison.group(2).lower()
        if any(op in operator for op in _LOW_OPERATORS):
            found.append((comparison.start(2), comparison.end(), "low"))
        elif any(op in operator for op in _HIGH_OPERATORS):
            found.append((comparison.start(2), comparison.end(), "high"))
    return sorted(found)


def _survival_sentence_split(text: str) -> list[str]:
    """Sentences for the survival scanners: split at ".", "!", "?", ";" and lines.

    Not at ":", unlike :func:`_sentences`: "Survival target for campaigner:
    12" is one claim (review of #760).
    """
    return [
        chunk.strip() for chunk in re.split(r"(?<=[.!?;\n])\s+", text) if chunk.strip()
    ]


def survival_sentences(text: str) -> Iterator[str]:
    """The sentences of ``text`` the survival scanners read.

    A sentence about survival is read, so the combat matrix's own target
    numbers in the same reply are never mistaken for one. So is a sentence
    pairing a track with a figure -- a row of a table of targets -- when its
    paragraph is about survival (review of #760).
    """
    for paragraph in re.split(r"\n\s*\n", plain(text)):
        about_survival = any(word in paragraph.lower() for word in _SURVIVAL_CONTEXT)
        for sentence in _survival_sentence_split(paragraph):
            if any(word in sentence.lower() for word in _SURVIVAL_CONTEXT) or (
                about_survival and _TRACK_FIGURE.search(sentence)
            ):
                yield sentence


#: A clause describing the wrong rule in order to reject it: "a roll of 12 or
#: under would mean you died, which is the opposite of the rule" (board of
#: 2026-09-29, run 3). Its figures are the rule being corrected.
_DISOWNED = re.compile(
    r"\bwould\b[^;]*\b(?:opposite|backwards|reversed|wrong|incorrect|"
    r"not\s+(?:the\s+rule|how\s+it\s+works))\b",
    re.IGNORECASE,
)


def survival_claims(text: str) -> list[RuleClaim]:
    """Parse every claim ``text`` makes about a survival roll's shape.

    Only survival sentences are read (:func:`survival_sentences`). Each
    clause is read with its denials blanked, and each direction it names is
    read against the outcome it is attached to: "roll under 9 and the
    character dies" says the roll goes high to survive, which is the rule
    (issue #760).

    Args:
        text: The reply to scan.

    Returns:
        One claim per clause that makes one.
    """
    claims: list[RuleClaim] = []
    for whole in survival_sentences(text):
        for part in _clauses(whole):
            if _DISOWNED.search(part):
                continue
            # A part about a *natural* roll states an override, not the
            # check's shape: "a natural 3 or less is an automatic death" is
            # not a claim that the roll goes low, and its 3 is not the target.
            reading = read_clause(part)
            says_low = says_high = False
            directions = _directions(reading.blanked)
            for index, (start, end, way) in enumerate(directions):
                bound = (
                    directions[index + 1][0]
                    if index + 1 < len(directions)
                    else len(reading.clause)
                )
                outcome = _outcome_of(
                    reading.as_said, reading.blanked, start, end, bound
                )
                if outcome == "death":
                    way = "high" if way == "low" else "low"
                says_low = says_low or way == "low"
                says_high = says_high or way == "high"
            comparison = _DICE_COMPARISON.search(reading.blanked)
            dice_count = int(comparison.group(1)) if comparison is not None else None
            target, track = reading.figures[0] if reading.figures else (None, None)
            if dice_count is None and target is None and not says_low and not says_high:
                continue
            claims.append(
                RuleClaim(
                    snippet=whole,
                    dice_count=dice_count,
                    target=target,
                    says_low=says_low,
                    says_high=says_high,
                    track=track,
                    clause=reading.clause,
                    figures=reading.figures,
                )
            )
    return claims


def _for_other_track(track: str | None, rule: "SurvivalRule") -> bool:
    """Whether a figure was named for a track other than ``rule``'s."""
    return track is not None and track != rule.track


def contradicting_survival_claims(text: str, rule: SurvivalRule) -> list[RuleClaim]:
    """The claims in ``text`` that the running rules engine disagrees with.

    One-sided: a reply that names no target contradicts nothing and is
    returned empty. Whether it *should* have named one is a separate check
    (:func:`stated_targets`), because since #605 it should.

    A target named for another track -- the lookup's all-tracks answer puts
    "underworld 8" beside "campaigner 9" -- is that track's, not a wrong
    claim about this one. Every figure in a clause is judged, not only the
    first: "underworld 8, campaigner 11" is wrong about the campaigner
    (issue #760).

    Args:
        text: The reply to scan.
        rule: What the code did, from :func:`survival_rule_from_code`.

    Returns:
        Only the claims that are wrong.
    """
    wrong: list[RuleClaim] = []
    for claim in survival_claims(text):
        if any(
            figure != rule.target and not _for_other_track(track, rule)
            for figure, track in claim.figures
        ):
            wrong.append(claim)
            continue
        if claim.says_low and rule.wrong_direction_is_low:
            wrong.append(claim)
            continue
        if claim.says_high and not rule.rolls_high:
            wrong.append(claim)
    return wrong


#: A clause that makes the target itself hypothetical: "if the survival
#: target were 9", "whether the target is 9". A conditional about the roll
#: ("if your total is 9 or greater, you survive") states the target and is
#: read (review of #760).
_TARGET_IN_DOUBT = re.compile(
    r"\b(?:if|whether|suppose|supposing|unless)\b[^,;]{0,40}?\btargets?\b",
    re.IGNORECASE,
)
#: "The target is 9 or 12": two candidates are no answer.
_ALTERNATIVE_FIGURES = re.compile(r"\b\d{1,2}\s+or\s+\d{1,2}\b")


def stated_targets(text: str, rule: SurvivalRule) -> list[str]:
    """The clauses in ``text`` that state ``rule``'s target (issue #760).

    The positive half the one-sided scan cannot give: since #605 a survival
    question is answered from ``rules_lookup``, so the right answer names the
    target, and a refusal names none. Read a paragraph at a time, since "For
    the campaigner track, that target is 9" follows the sentence that made it
    a survival answer rather than carrying the word itself.

    Not credited: a sentence that reports rather than claims ("I won't say
    if the survival target is 9 or 12"), a question, a clause that makes the
    target itself hypothetical, and a pair of candidate figures.

    Args:
        text: The reply to scan.
        rule: What the code did.

    Returns:
        Each clause naming ``rule.target`` for ``rule.track`` (or for no
        track), with its denials set aside.
    """
    found: list[str] = []
    for paragraph in re.split(r"\n\s*\n", plain(text)):
        if not any(word in paragraph.lower() for word in _SURVIVAL_CONTEXT):
            continue
        for sentence in _survival_sentence_split(paragraph):
            if sentence.endswith("?") or reports_rather_than_claims(sentence):
                continue
            for part in _clauses(sentence):
                if _TARGET_IN_DOUBT.search(part) or _ALTERNATIVE_FIGURES.search(part):
                    continue
                reading = read_clause(part)
                if any(
                    figure == rule.target and track in (None, rule.track)
                    for figure, track in reading.figures
                ):
                    found.append(part)
    return found


# A skill-point figure, however the reply words it: "6 skill points",
# "2 remaining free skill points", "5 points". The qualifier slot in the
# middle is why one pattern with a context window beats two patterns racing
# each other -- the model puts "free" and "remaining" wherever it likes.
_POINTS_FIGURE = re.compile(r"\b(\d+)\s+(?:\w+\s+){0,3}?points?\b", re.IGNORECASE)
_AVAILABILITY_WORDS: tuple[str, ...] = (
    "available",
    "remaining",
    "left",
    "unspent",
    "free",
    "to spend",
    "has",
    "have",
)
# "3 points earned", "1 point spent", "you need 2 more points to reach II" --
# figures about the account's history or about what a level costs, not about
# what is free right now. The last shape cost a false failure on a live run
# where the GM's own answer was right.
_NOT_A_FREE_TOTAL = re.compile(
    r"\bearned\b|\bspent\b|\bgained\b|\bmore\b|\bneeds?\b|\bcosts?\b"
    r"|\brequires?\b|\bto\s+reach\b|\bto\s+go\s+from\b",
    re.IGNORECASE,
)
#: A figure after a spend that has not happened: "if she raises it, she will
#: have 0 left" (board of 2026-09-29, run 2). Only inside an if/when clause
#: about spending: "she has 9 free skill points after the level" is a claim
#: (third review of #760).
_HYPOTHETICAL_SPEND = re.compile(
    r"\b(?:if|when|once|should)\b[^.;!?]{0,60}?\b(?:spend|spends|spent|raise|"
    r"raises|raised|buy|buys|take|takes|learn|learns|put|puts|use|uses)\b"
    r"[^.;!?]*$",
    re.IGNORECASE,
)
_CLAIM_WINDOW = 45
#: A negation that governs the figure after it: "not 9", "doesn't have 9",
#: "never had only 2".
_DENIED_POINTS = re.compile(
    r"(?:\bnot|n't|\bnever|\bno\s+longer|\brather\s+than|\binstead\s+of)\s+"
    r"(?:(?:have|has|had|got)\s+)?(?:(?:only|just|exactly|all)\s+)?(\d+)\b",
    re.IGNORECASE,
)


def skill_point_claims(text: str) -> list[tuple[int, str]]:
    """Every "N skill points available" figure the reply states.

    A figure counts as a claim about the *free* total when an availability
    word sits within :data:`_CLAIM_WINDOW` characters of it and no "earned" or
    "spent" does -- so "3 earned, 1 spent, 2 free" yields the 2 and nothing
    else, which is the number #356 is about.

    Args:
        text: The reply to scan.

    Returns:
        ``(figure, snippet)`` pairs, one per claim, in the order found.
    """
    text = plain(text)
    # A figure named in order to deny it is the correction, not a claim: "she
    # does not have 9 skill points available; the record shows 1" (#760).
    # Only a negation that governs the figure itself: "she does not need to
    # spend them, 9 skill points remain" claims the 9 (review of #760).
    denied = {match.start(1) for match in _DENIED_POINTS.finditer(text)}
    claims: list[tuple[int, str]] = []
    for match in _POINTS_FIGURE.finditer(text):
        if match.start(1) in denied:
            continue
        near = text[max(0, match.start() - _CLAIM_WINDOW) : match.end() + _CLAIM_WINDOW]
        lowered = near.lower()
        if not any(word in lowered for word in _AVAILABILITY_WORDS):
            continue
        # The disqualifying word can fall on either side of the figure
        # ("earned 2 skill points", "2 skill points spent"), so both are read.
        if _NOT_A_FREE_TOTAL.search(
            text[max(0, match.start() - 15) : match.end() + 15]
        ):
            continue
        sentence_start = max(
            text.rfind(stop, 0, match.start()) for stop in (".", "!", "?", "\n")
        )
        if _HYPOTHETICAL_SPEND.search(text[sentence_start + 1 : match.start()]):
            continue
        claims.append((int(match.group(1)), near.replace("\n", " ")))
    return claims


_ROLLED_FIGURE = re.compile(
    r"\brolled\b\s*(?:a|an|the)?\s*(?:total\s+of\s*)?(\d{1,3})\b"
    r"|\b(?:came\s+up|shows|showed|showing)\s*(?:a|an)?\s*(\d{1,3})\b",
    re.IGNORECASE,
)


def rolled_figures(text: str) -> list[tuple[int, str]]:
    """Every number the reply reports as a die having come up.

    Deliberately restricted to result framing ("rolled a 14", "came up 18").
    "You need to roll a 15" names a target, not an outcome, and is not a
    provenance claim.

    Args:
        text: The reply to scan.

    Returns:
        ``(figure, snippet)`` pairs.
    """
    text = plain(text)
    figures: list[tuple[int, str]] = []
    for match in _ROLLED_FIGURE.finditer(text):
        value = match.group(1) or match.group(2)
        snippet = text[max(0, match.start() - 25) : match.end() + 25]
        figures.append((int(value), snippet.replace("\n", " ")))
    return figures


_ATTRIBUTE_FIGURE = re.compile(
    r"\b(STR|DEX|INT|WIS|CON|CHR|Strength|Dexterity|Intelligence|Wisdom|"
    r"Constitution|Charisma)\b\s*[:=\-–]?\s*(?:is\s+)?(\d{1,2})\b",
    re.IGNORECASE,
)


def attribute_figures(text: str) -> list[tuple[str, int, str]]:
    """Every "STR 13"-shaped attribute value the reply states.

    Args:
        text: The reply to scan.

    Returns:
        ``(attribute, value, snippet)`` triples.
    """
    text = plain(text)
    figures: list[tuple[str, int, str]] = []
    for match in _ATTRIBUTE_FIGURE.finditer(text):
        snippet = text[max(0, match.start() - 15) : match.end() + 15]
        figures.append(
            (
                match.group(1).upper()[:3],
                int(match.group(2)),
                snippet.replace("\n", " "),
            )
        )
    return figures


_NUMBERED_ITEM = re.compile(r"^\s*(\d+)[.)]\s+\S", re.MULTILINE)


def numbered_items(text: str) -> list[str]:
    """The reply's numbered-list items, if it has any."""
    return [match.group(0).strip() for match in _NUMBERED_ITEM.finditer(plain(text))]


_NEAR_CONFIRM = re.compile(
    r"(?:you(?:'ll| will)?\s+(?:need|have|must|should)|make\s+a|roll\s+a|"
    r"requires?|need\s+to|should)\b[^.\n]{0,60}?\bconfirm",
    re.IGNORECASE,
)
_PERMITTED_NOTE = re.compile(
    r"\b(?:the\s+book|by\s+the\s+book|written\s+rule|the\s+rules\s+as\s+written|"
    r"raw|normally|ordinarily|usually|standard\s+rule|§\s*7)\b",
    re.IGNORECASE,
)


def readjudication_hits(text: str) -> list[PhraseHit]:
    """Instructions to confirm a crit the Game Master already ruled away (#353).

    The prompt permits one short note that the written rule differs. A
    sentence carrying such a note is exempt; a sentence that instructs him to
    make the roll anyway is not.

    Args:
        text: The reply to scan.

    Returns:
        One hit per offending sentence.
    """
    hits: list[PhraseHit] = []
    for sentence in _sentences(text):
        if (
            _NEAR_CONFIRM.search(sentence)
            and not _PERMITTED_NOTE.search(sentence)
            and not reports_rather_than_claims(sentence)
        ):
            hits.append(PhraseHit("instructs a confirmation roll", sentence))
    return hits


# --------------------------------------------------------------------------
# Driving the model.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Persona:
    """Who a scenario's conversation is held as, and in which mode.

    #372 split the two things this used to conflate. The account's role still
    decides authority -- what may be written down -- and the session's mode
    now decides manner, chosen by whoever is sitting at it. A persona is one
    point in that grid, so a scenario can put the same Game Master account in
    the aide it defaults to or in the game it can now reach.

    Attributes:
        key: The name scenarios refer to.
        username: The account the turns are sent as. Two personas may share
            one account with different modes; each scenario opens its own
            session, so they never collide.
        is_dm: Whether the account carries the DM/admin role.
        mode: The mode the session is opened in, one of ``gm.engine.GM_MODES``.
    """

    key: str
    username: str
    is_dm: bool
    mode: str

    @property
    def expected_prompt(self) -> str:
        """The system prompt the view should choose for this persona."""
        from gm.engine import system_prompt_for

        return system_prompt_for(self.is_dm, self.mode)

    @property
    def prompt_name(self) -> str:
        """What that prompt is called, for the report line."""
        return prompt_name(self.expected_prompt)


def prompt_name(prompt: str) -> str:
    """Name the constant ``prompt`` is, for a report that reads like the code."""
    from gm import engine

    for name in (
        "SYSTEM_PROMPT",
        "AIDE_SYSTEM_PROMPT",
        "DM_PLAY_SYSTEM_PROMPT",
        "DM_SYSTEM_PROMPT",
    ):
        if getattr(engine, name) == prompt:
            return name
    return "a prompt that is none of the known constants"


PLAYER = Persona(
    key="player",
    username="gm-behaviour-player",
    is_dm=False,
    mode=MODE_PLAY,
)
# The Game Master as he arrives: a DM account, in the aide his role defaults
# to (issue #353).
GAME_MASTER = Persona(
    key="dm",
    username="gm-behaviour-dm",
    is_dm=True,
    mode=MODE_AIDE,
)
# The same Game Master having asked to play a game -- the case #372 exists
# for, and the one that was unreachable before it.
GAME_MASTER_PLAYING = Persona(
    key="dm-playing",
    username="gm-behaviour-dm",
    is_dm=True,
    mode=MODE_PLAY,
)
PERSONAS: dict[str, Persona] = {
    persona.key: persona for persona in (PLAYER, GAME_MASTER, GAME_MASTER_PLAYING)
}


@dataclass(frozen=True)
class Turn:
    """One message sent to the GM.

    Attributes:
        message: What the person types.
        command: The slash command it carries, if any.
        before: Run on the session id just before the message is sent, for a
            scenario that needs the session in a state no number of turns
            would reach in reasonable time -- a transcript long enough to
            fold, for one. Skipped on a turn that has no session yet.
    """

    message: str
    command: str | None = None
    before: Callable[[int], None] | None = None


@dataclass(frozen=True)
class ServerDie:
    """One roll the server made, from whichever frame reported it.

    Attributes:
        expression: The dice notation rolled, e.g. ``3d6``.
        faces: The individual faces.
        total: What they came to, before any modifier the line adds.
        kind: The action that rolled it -- ``roll`` for a ``gm_roll`` frame,
            otherwise the procedure's kind (``roll_attributes``,
            ``play_years``). Creation dice and survival dice are both 3d6, so
            a check that means one of them must say which.
    """

    expression: str
    faces: tuple[int, ...]
    total: int
    kind: str = ""


@dataclass(frozen=True)
class TurnRecord:
    """Everything one turn produced.

    Attributes:
        message: What was sent.
        reply: The narration the player saw.
        gm_rolls: Every ``gm_roll`` frame payload the server emitted (#363).
        procedures: Every ``procedure`` frame payload (#370).
        error: A stream error, when the turn produced one.
        seconds: Wall-clock time the turn took.
        ledger: The draft's ledger as the server left it *after this turn*.
            Sampled per turn rather than once at the end: a later turn can
            spend another point, and a figure the GM stated on turn four must
            be judged against the total that stood on turn four.
        statuses: Every ``status`` frame's words, in order (issue #569).
        plans: The plans the server read, each a list of action payloads
            from the turn's ``plan`` events (issue #590).
        plan_rejections: The errors of each planning reply that was not a
            plan, one list per rejected reply.
        turn_id: The turn record's id, from the ``X-GM-Turn`` header.
    """

    message: str
    reply: str
    gm_rolls: tuple[dict, ...]
    procedures: tuple[dict, ...]
    error: str
    seconds: float
    ledger: DraftLedger | None = None
    statuses: tuple[str, ...] = ()
    plans: tuple[tuple[dict, ...], ...] = ()
    plan_rejections: tuple[tuple[str, ...], ...] = ()
    turn_id: int | None = None


@dataclass(frozen=True)
class Transcript:
    """One run of one scenario's conversation.

    Attributes:
        persona: Who held it, and in which mode.
        session_id: The ``GMSession`` it ran in.
        turns: What each turn produced, in order.
        system_prompt: The system prompt the view chose, read back from the
            session's stored mode and the account's role rather than assumed
            (issue #372) -- so a mode the view failed to honour shows up as a
            failed check instead of being papered over here.
    """

    persona: Persona
    session_id: int | None
    turns: tuple[TurnRecord, ...]
    system_prompt: str

    @property
    def last(self) -> TurnRecord:
        """The final turn."""
        return self.turns[-1]

    @property
    def replies(self) -> str:
        """Every reply, joined, for checks that scan the whole conversation."""
        return "\n\n".join(turn.reply for turn in self.turns)

    @property
    def all_gm_rolls(self) -> tuple[dict, ...]:
        """Every server roll across the conversation."""
        return tuple(roll for turn in self.turns for roll in turn.gm_rolls)

    @property
    def all_procedures(self) -> tuple[dict, ...]:
        """Every server procedure across the conversation."""
        return tuple(step for turn in self.turns for step in turn.procedures)

    @property
    def server_dice(self) -> tuple[ServerDie, ...]:
        """Every die the server rolled, however it reported the roll.

        A turn reports dice two ways, and both mean "the code rolled this".
        The ``roll`` action emits a ``gm_roll`` frame; every other action that
        rolls -- ``roll_attributes``, ``roll_fatigue``, ``play_years`` -- does
        its rolling inside :mod:`characters.services` and reports it in the
        text of a ``procedure`` frame (``gm/views.py`` routes the two event
        kinds to the two frames). A check that reads only ``gm_roll`` misses
        every creation roll, which is how the ``creation_rolls`` checks came
        to fail 0/3 against a server that was rolling correctly (issue #604).

        The procedure text's shape is pinned by
        ``characters.tests.test_services.test_rolled_attribute_note_shape``,
        so a change to it fails a fast unit test rather than rotting this
        harness silently.
        """
        dice: list[ServerDie] = []
        for roll in self.all_gm_rolls:
            faces = tuple(
                int(face) for face in re.findall(r"\d+", str(roll.get("rolls", "")))
            )
            expression = str(roll.get("expression", ""))
            if "total" in roll:
                dice.append(ServerDie(expression, faces, int(roll["total"]), "roll"))
        for step in self.all_procedures:
            text = " ".join(
                [
                    str(step.get("note", "")),
                    *(str(line) for line in step.get("lines", ())),
                ]
            )
            for expression, faces, total in DICE_IN_TEXT.findall(text):
                dice.append(
                    ServerDie(
                        expression,
                        tuple(int(face) for face in re.findall(r"\d+", faces)),
                        int(total),
                        str(step.get("kind", "")),
                    )
                )
        return tuple(dice)

    @property
    def errors(self) -> tuple[str, ...]:
        """Every stream error the conversation hit."""
        return tuple(turn.error for turn in self.turns if turn.error)

    def ledger(self) -> DraftLedger | None:
        """The draft character's ledger as the conversation left it, or None."""
        return ledger_of(self.session_id)


def _client_for(persona: Persona) -> Client:
    """A signed-in, gate-cleared client for ``persona``.

    Args:
        persona: Who to sign in as.

    Returns:
        A test client whose session has passed the passphrase gate.
    """
    user, created = User.objects.get_or_create(
        username=persona.username,
        defaults={"is_player": True, "is_dm": persona.is_dm},
    )
    if not created and user.is_dm != persona.is_dm:
        user.is_dm = persona.is_dm
        user.save(update_fields=["is_dm"])
    # raise_request_exception=False so a 500 from the view is recorded as a
    # failed turn instead of tearing down the whole harness: a server error
    # under a real conversation is exactly the kind of finding this exists to
    # report. (It found one on its first full run — see the PR.)
    client = Client(SERVER_NAME=CLIENT_HOST, raise_request_exception=False)
    session = client.session
    session[PublicPathPassphraseMiddleware.SESSION_FLAG] = True
    session.save()
    client.force_login(user)
    return client


# ``gm_stream`` is rate limited per user (``common.decorators.rate_limit``), and
# a harness sending three runs of a dozen turns will trip a limit no person
# ever would. The server says how long to wait; wait that long and send the
# same turn again rather than reporting a rate limit as a GM failure.
RATE_LIMITED = 429
MAX_RATE_LIMIT_WAITS = 4


def _post_past_the_rate_limit(client: Client, body: dict[str, object]):
    """Send one stream request, waiting out a rate limit rather than failing on it.

    Args:
        client: The signed-in client.
        body: The JSON body to post.

    Returns:
        The response, once it is not a rate-limit refusal. A refusal that
        outlasts :data:`MAX_RATE_LIMIT_WAITS` waits is returned as it is, so
        the turn records it and the report shows it.
    """
    url = reverse("gm:stream")
    for _ in range(MAX_RATE_LIMIT_WAITS):
        response = client.post(url, json.dumps(body), content_type="application/json")
        if response.status_code != RATE_LIMITED:
            return response
        try:
            wait = float(json.loads(response.content).get("retry_after", 60))
        except (ValueError, AttributeError):
            wait = 60.0
        print(f" [rate limited; waiting {wait:.0f}s] ", end="", flush=True)
        time.sleep(wait + 1)
    return client.post(url, json.dumps(body), content_type="application/json")


def _send(
    client: Client, session_id: int | None, turn: Turn, mode: str
) -> tuple[TurnRecord, int | None]:
    """Send one turn and collect every frame the stream produced.

    Args:
        client: The signed-in client.
        session_id: The session to continue, or None to start one.
        turn: What to send.
        mode: The mode a session-creating turn opens in (issue #372). The
            view ignores it once the session exists and reads the stored one.

    Returns:
        The turn's record and the session id to carry to the next turn. A
        stream error is recorded, never raised: a scenario that fails halfway
        is more useful reported than aborted.
    """
    if turn.command == FIGHT_COMMAND:
        return _send_fight(client, session_id, turn)
    body: dict[str, object] = {
        "message": turn.message,
        "session_id": session_id,
        "mode": mode,
    }
    if turn.command:
        body["command"] = turn.command
    started = time.monotonic()
    response = _post_past_the_rate_limit(client, body)
    reply, error = "", ""
    gm_rolls: list[dict] = []
    procedures: list[dict] = []
    new_id = session_id
    frames = getattr(response, "streaming_content", None)
    if frames is None:
        return (
            TurnRecord(
                message=turn.message,
                reply="",
                gm_rolls=(),
                procedures=(),
                error=(
                    f"the view answered HTTP {response.status_code} instead of "
                    f"a stream: {response.content[:400]!r}"
                ),
                seconds=time.monotonic() - started,
            ),
            session_id,
        )
    statuses: list[str] = []
    for line in frames:
        text = line.decode()
        if not text.startswith("data: "):
            continue
        frame = json.loads(text[6:])
        if "status" in frame:
            statuses.append(str(frame["status"]))
        if "error" in frame:
            error = str(frame["error"])
            continue
        if "gm_roll" in frame:
            gm_rolls.append(frame["gm_roll"])
        if "procedure" in frame:
            procedures.append(frame["procedure"])
        if frame.get("done"):
            reply = frame.get("full_response", "")
            new_id = frame.get("session_id", session_id)
            # The done frame repeats every mid-stream roll and procedure; take
            # it as authoritative so a dropped frame cannot double-count.
            gm_rolls = list(frame.get("gm_rolls", gm_rolls))
            procedures = list(frame.get("procedures", procedures))
    turn_header = response.headers.get("X-GM-Turn")
    turn_id = int(turn_header) if turn_header else None
    plans, rejections = plan_events(turn_id)
    record = TurnRecord(
        message=turn.message,
        reply=reply,
        gm_rolls=tuple(gm_rolls),
        procedures=tuple(procedures),
        error=error,
        seconds=time.monotonic() - started,
        statuses=tuple(statuses),
        plans=plans,
        plan_rejections=rejections,
        turn_id=turn_id,
    )
    return record, new_id


#: A turn that takes the fight to the board, as the page's /fight does: its
#: message is the foes text, posted to ``gm:fight`` rather than the stream
#: (issue #841).
FIGHT_COMMAND = "fight"


def _send_fight(
    client: Client, session_id: int | None, turn: Turn
) -> tuple[TurnRecord, int | None]:
    """Take the session's fight to the board, and record what it answered.

    The reply is the line the session's history gained, the one the page
    shows where the fight began; an error is the endpoint's own words.
    """
    started = time.monotonic()
    response = client.post(
        reverse("gm:fight"),
        json.dumps({"session_id": session_id, "foes": turn.message}),
        content_type="application/json",
    )
    answer = json.loads(response.content or b"{}")
    session = GMSession.objects.filter(pk=session_id).first()
    history = session.history if session is not None else []
    reply = str(history[-1].get("content", "")) if answer.get("ok") and history else ""
    return (
        TurnRecord(
            message=f"/fight {turn.message}",
            reply=reply,
            gm_rolls=(),
            procedures=(),
            error=""
            if answer.get("ok")
            else str(answer.get("error", response.status_code)),
            seconds=time.monotonic() - started,
        ),
        session_id,
    )


def plan_events(
    turn_id: int | None,
) -> tuple[tuple[tuple[dict, ...], ...], tuple[tuple[str, ...], ...]]:
    """The plans a turn's events recorded, and its rejected planning replies.

    Args:
        turn_id: The turn record's id, or None when the view refused the turn
            before recording it.

    Returns:
        ``(plans, rejections)``: each plan as its action payloads, and each
        rejected reply as its errors. Both empty for a marker-loop turn.
    """
    if turn_id is None:
        return (), ()
    events = GMEvent.objects.filter(turn_id=turn_id, kind__in=("plan", "plan_rejected"))
    plans = tuple(
        tuple(event.payload.get("actions", []))
        for event in events
        if event.kind == "plan"
    )
    rejections = tuple(
        tuple(event.payload.get("errors", []))
        for event in events
        if event.kind == "plan_rejected"
    )
    return plans, rejections


def ledger_of(session_id: int | None) -> DraftLedger | None:
    """The ledger of the session's first character right now, or None.

    Args:
        session_id: The session to read, or None before one exists.

    Returns:
        The ledger, or None when there is no session or no character to read.
    """
    if session_id is None:
        return None
    session = GMSession.objects.filter(pk=session_id).first()
    entries = roster.roster(session)
    return entries[0].ledger() if entries else None


def characters_of(session_id: int | None) -> list[roster.RosterEntry]:
    """The Character rows attached to the session now (issues #390, #571).

    Args:
        session_id: The session to read, or None before one exists.

    Returns:
        Every attached character, each with its label, in attachment order.
    """
    if session_id is None:
        return []
    return roster.roster(GMSession.objects.filter(pk=session_id).first())


def campaign_of(session_id: int | None) -> CampaignState | None:
    """Where the party is, as the server holds it now (issue #579)."""
    if session_id is None:
        return None
    return CampaignState.objects.filter(session_id=session_id).first()


def entities_of(session_id: int | None, kind: str = "") -> list[SceneEntity]:
    """The people and things this session recorded, oldest first."""
    if session_id is None:
        return []
    rows = SceneEntity.objects.filter(session_id=session_id)
    return list(rows.filter(kind=kind) if kind else rows)


def threads_of(session_id: int | None) -> list[StoryThread]:
    """Every thread the session opened, resolved or not."""
    if session_id is None:
        return []
    return list(StoryThread.objects.filter(session_id=session_id))


def supplies_of(session_id: int | None) -> list[SupplyItem]:
    """What the party carries between them, oldest row first (issue #579)."""
    if session_id is None:
        return []
    return list(SupplyItem.objects.filter(session_id=session_id))


def session_summary(session_id: int | None) -> str:
    """The recap written for a session long enough to fold, or ``""``."""
    if session_id is None:
        return ""
    return (
        GMSession.objects.filter(pk=session_id)
        .values_list("summary", flat=True)
        .first()
        or ""
    )


#: Table business to pad a transcript with. Nothing here touches the scene
#: under test: the point is turns the GM must let go of, not turns it should
#: remember.
OTHER_BUSINESS = (
    "The caravan master wants another two silver for the crossing, and the "
    "drover behind him is listening. We haggle. The road out of the town is "
    "rutted from the rain and the mules are unhappy about it. ",
    "We make camp off the road, a good way from the water. The night is cold "
    "and the fire will not take. Somebody takes the first watch, somebody "
    "else complains about it, and the hours go by without event. ",
    "In the morning there is a market at the crossroads: salt, rope, dried "
    "fish, a man selling charms nobody believes in. We look, we do not buy, "
    "and we are on the road again before the bells. ",
)

#: How long each padded entry is. Long enough that the padding reaches the
#: fold in a reasonable number of entries, short enough to read in a log.
PADDED_ENTRY_CHARS = 2000


def _padding_entry(index: int) -> dict[str, str]:
    """One entry of other business, alternating player and GM."""
    said = OTHER_BUSINESS[index % len(OTHER_BUSINESS)]
    repeats = PADDED_ENTRY_CHARS // len(said) + 1
    return {
        "role": "user" if index % 2 == 0 else "assistant",
        "content": f"(day {index // 2 + 1} on the road) " + said * repeats,
    }


def pad_session_for_a_fold(session_id: int) -> None:
    """Grow this session's transcript until the next turn has to fold it.

    Playing thirty turns of other business would cost half an hour of model
    time to test something the model never sees: the fold reads the stored
    history, and what put it there does not matter. So the transcript is
    padded here and the turn after it is a real turn against the real model,
    answering with the older turns already folded away.
    """
    session = GMSession.objects.get(pk=session_id)
    history = list(session.history)
    while not summary.should_fold(history, num_ctx=settings.GM_NUM_CTX):
        history.append(_padding_entry(len(history)))
    session.history = history
    session.save(update_fields=["history_json"])


def named_like(rows: Sequence[Any], word: str) -> Any | None:
    """The first row whose name contains ``word``, ignoring case.

    Scenarios name a thing in the Game Master's words, and the GM records it
    in its own -- "iron box", "the iron box", "Box". Matching on a word
    rather than the whole string asks what the scenario means: is there a
    record for this thing at all.
    """
    for row in rows:
        if word.casefold() in str(row.name).casefold():
            return row
    return None


def run_conversation(persona: Persona, turns: Sequence[Turn]) -> Transcript:
    """Hold ``turns`` with the live GM as ``persona`` and record what came back.

    Args:
        persona: Who to sign in as.
        turns: The messages to send, in order.

    Returns:
        The transcript, including the system prompt the view chose.
    """
    client = _client_for(persona)
    user = User.objects.get(username=persona.username)
    records: list[TurnRecord] = []
    session_id: int | None = None
    try:
        for turn in turns:
            if turn.before is not None and session_id is not None:
                turn.before(session_id)
            record, session_id = _send(client, session_id, turn, persona.mode)
            records.append(replace(record, ledger=ledger_of(session_id)))
    finally:
        # A turn's hook may change the account's role (promote_session_owner);
        # an aborted run must not leave a player's account a Game Master.
        User.objects.filter(pk=user.pk).update(is_dm=persona.is_dm)
        user.refresh_from_db(fields=["is_dm"])
    # The mode the session actually ended up in, not the one asked for: if the
    # view dropped it, check_mode should say so.
    stored_mode = (
        GMSession.objects.filter(pk=session_id).values_list("mode", flat=True).first()
        if session_id
        else persona.mode
    )
    return Transcript(
        persona=persona,
        session_id=session_id,
        turns=tuple(records),
        system_prompt=get_system_message(user, stored_mode)["content"],
    )


# --------------------------------------------------------------------------
# Checks.
# --------------------------------------------------------------------------


class CheckKind:
    """How strictly a check is expected to hold, and therefore its threshold.

    The pass rate a check must reach is a property of what it measures, not of
    the scenario it sits in, so it lives here and nowhere else.

    Attributes:
        STRUCTURE: A stream frame exists, or a fixed phrase does or does not
            appear. Nothing about wording enters it, so a single failure is a
            real failure. Required rate 1.0.
        PROVENANCE: A number in the prose equals a number the server produced.
            Also exact. Required rate 1.0.
        CONTRADICTION: A claim parsed out of the prose disagrees with what the
            rules engine actually does. One-sided -- silence never fails --
            so, again, exact. Required rate 1.0.
        RUBRIC: A keyword-set judgement of manner or content, where honest
            rewording can go either way. Required rate 2/3, and the observed
            rate is always printed.
        REPORT: A reading of prose that no word list decides every time, kept
            for what it shows. Always printed, required rate 0, and never part
            of whether a scenario passed (sixth review of #760).
    """

    STRUCTURE = "structure"
    PROVENANCE = "provenance"
    CONTRADICTION = "contradiction"
    RUBRIC = "rubric"
    REPORT = "report"


REQUIRED_PASS_RATE: dict[str, float] = {
    CheckKind.STRUCTURE: 1.0,
    CheckKind.PROVENANCE: 1.0,
    CheckKind.CONTRADICTION: 1.0,
    CheckKind.RUBRIC: 2 / 3,
    CheckKind.REPORT: 0.0,
}


@dataclass(frozen=True)
class CheckOutcome:
    """One assertion's verdict on one run.

    Attributes:
        name: What was checked, in words.
        kind: One of :class:`CheckKind`'s values.
        passed: Whether it held.
        expectation: What should have been true.
        evidence: What was actually found.
    """

    name: str
    kind: str
    passed: bool
    expectation: str
    evidence: str


def verdict(
    name: str, kind: str, passed: bool, expectation: str, evidence: str
) -> CheckOutcome:
    """Build a :class:`CheckOutcome`, so every check reads the same way."""
    return CheckOutcome(
        name=name, kind=kind, passed=passed, expectation=expectation, evidence=evidence
    )


def check_no_phrases(
    name: str, text: str, phrases: Iterable[str], kind: str = CheckKind.STRUCTURE
) -> CheckOutcome:
    """Assert none of ``phrases`` appears in ``text``."""
    hits = find_phrases(text, phrases)
    return verdict(
        name,
        kind,
        not hits,
        "none of these phrases appears",
        "; ".join(str(hit) for hit in hits) or "no hits",
    )


def check_no_roll_disclaimer(text: str) -> CheckOutcome:
    """Assert ``text`` never says the GM has no dice (#363).

    A hard disclaimer fails wherever it appears. A capability disclaimer
    ("I cannot roll X") fails only in a sentence that does not also explain
    the *order* — "I cannot roll the damage yet, the attack has not resolved"
    is table talk, not the claim #363 is about, and failing it would make the
    harness cry wolf.

    Args:
        text: The reply, or every reply in the conversation.

    Returns:
        The verdict, naming the sentence that offended.
    """
    hits = [str(hit) for hit in find_phrases(text, HARD_ROLL_DISCLAIMERS)]
    for sentence in _sentences(text):
        lowered = sentence.lower()
        if not any(phrase in lowered for phrase in CAPABILITY_ROLL_DISCLAIMERS):
            continue
        if any(word in lowered for word in ROLL_SEQUENCING_WORDS):
            continue
        if _WITHOUT_FIGURES.search(sentence):
            continue
        hits.append(f"capability disclaimer in {sentence!r}")
    return verdict(
        "it never disclaims the ability to roll",
        CheckKind.STRUCTURE,
        not hits,
        "the server rolls dice and the prompt says so, so no reply may say "
        "the GM cannot",
        "; ".join(hits) or "no disclaimer",
    )


def check_any_keyword(
    name: str, text: str, keywords: Iterable[str], kind: str = CheckKind.RUBRIC
) -> CheckOutcome:
    """Assert at least one of ``keywords`` appears in ``text``."""
    hits = find_phrases(text, keywords)
    return verdict(
        name,
        kind,
        bool(hits),
        f"at least one of: {', '.join(keywords)}",
        "; ".join(hit.phrase for hit in hits) or "none of them appeared",
    )


def check_survival_claims(name: str, text: str, rule: SurvivalRule) -> CheckOutcome:
    """Assert nothing ``text`` says about the survival check contradicts the code."""
    wrong = contradicting_survival_claims(text, rule)
    return verdict(
        name,
        CheckKind.CONTRADICTION,
        not wrong,
        (
            f"no stated survival target other than {rule.target}, and no claim "
            f"that the roll goes "
            f"{'low' if rule.rolls_high else 'high'} to survive. The code: "
            f"{rule.described}"
        ),
        (
            "; ".join(claim.snippet for claim in wrong)
            or "no contradicting claim (whether it answered is checked separately)"
        ),
    )


def _paragraphs(text: str) -> list[str]:
    """The reply's paragraphs, so a verdict split over two sentences is read whole."""
    return [
        block.strip() for block in re.split(r"\n\s*\n", plain(text)) if block.strip()
    ]


def _auto_survive_pattern(rule: SurvivalRule) -> re.Pattern[str]:
    """Match the automatic-survival roll, as a total or as the faces that make it.

    The faces are derived, not typed: a total of ``auto_survive_roll`` on
    ``SURVIVAL_DICE_COUNT`` dice can only be every die showing its highest
    face, which is how a player says it ("6, 6 and 6").
    """
    face = rule.auto_survive_roll // skills.SURVIVAL_DICE_COUNT
    listed = r"\s*[,-]\s*".join([str(face)] * skills.SURVIVAL_DICE_COUNT)
    spoken = r"\s*,\s*".join([str(face)] * (skills.SURVIVAL_DICE_COUNT - 1))
    # "Three sixes", as a table says it, when the words exist for the dice.
    counted = _NUMBER_WORDS.get(skills.SURVIVAL_DICE_COUNT)
    plural = _FACE_PLURALS.get(face)
    in_words = rf"|\b(?:all\s+)?{counted}\s+{plural}\b" if counted and plural else ""
    # "6, 6 and 6" and, with the Oxford comma, "6, 6, and 6" (third review).
    return re.compile(
        rf"\b{rule.auto_survive_roll}\b|{listed}|{spoken}\s*,?\s+and\s+{face}{in_words}",
        re.IGNORECASE,
    )


_NUMBER_WORDS = {2: "two", 3: "three", 4: "four"}
_FACE_PLURALS = {6: "sixes", 5: "fives", 4: "fours"}


def _death_is_the_other_natural(paragraph: str, rule: SurvivalRule) -> bool:
    """Whether the death this paragraph names belongs to the auto-death roll.

    "A natural 18 always grants survival, while a natural 3 always results in
    death" states the rule correctly and mentions both rolls. Matching the
    exact affirmation wording is a losing game -- the model reached for
    "always survives", "always survival" and "grants survival" on different
    runs -- so the structure is read instead: a death attached to the
    automatic-death roll is not a verdict on the automatic-survival one.
    """
    death = "|".join(re.escape(word) for word in DEATH_WORDS)
    return bool(
        re.search(
            rf"\bnatural\s+{rule.auto_death_roll}\b[^.]{{0,80}}?"
            + rf"(?:\b(?:{death})|{_DENIED_SURVIVAL.pattern})",
            paragraph,
            re.IGNORECASE,
        )
    )


def check_not_dead_on_auto_survive(
    name: str, text: str, rule: SurvivalRule
) -> CheckOutcome:
    """Assert ``text`` does not call the automatic-survival roll a death (#370).

    Read paragraph by paragraph, and only the paragraphs that name the roll:
    a reply that correctly explains "a natural 3 is an automatic death, a
    natural 18 an automatic survival" mentions death and must pass, while
    "a total of 18, which fails the check, and she dies" must not. A paragraph
    naming the roll that carries a survival word alongside the death word is
    explaining the rule, not delivering a verdict.

    Args:
        name: What to call the check in the report.
        text: The reply to scan.
        rule: What the code did, from :func:`survival_rule_from_code`.

    Returns:
        The verdict.
    """
    mentions = _auto_survive_pattern(rule)
    offending: list[PhraseHit] = []
    for paragraph in _paragraphs(text):
        if not mentions.search(paragraph):
            continue
        # A sentence refusing to give the verdict is not delivering one (#380).
        claimed = " ".join(
            sentence
            for sentence in _sentences(paragraph)
            if not reports_rather_than_claims(sentence)
        )
        # A death the paragraph denies is the correction, not the verdict:
        # "a natural 18 never results in death", "should not have died"
        # (issue #760).
        death = asserted_words(
            claimed, DEATH_WORDS, reach=VERDICT_NEGATION_REACH, per_occurrence=True
        )
        # And a survival it denies is a death: "a natural 18 never survives",
        # "no one survives a natural 18" (review of #760).
        death += [found.group(0) for found in _DENIED_SURVIVAL.finditer(claimed)]
        affirms = asserted_words(
            paragraph,
            (*SURVIVAL_WORDS, *AUTO_SURVIVE_AFFIRMATIONS),
            reach=VERDICT_NEGATION_REACH,
            per_occurrence=True,
        )
        if death and not affirms and not _death_is_the_other_natural(paragraph, rule):
            offending.append(PhraseHit(death[0], paragraph))
    return verdict(
        name,
        CheckKind.CONTRADICTION,
        not offending,
        (
            f"a natural {rule.auto_survive_roll} is an automatic survival in "
            "the rules engine, so no paragraph naming it may call it a death"
        ),
        "; ".join(str(hit) for hit in offending) or "no death asserted for it",
    )


def says_the_natural_survives(text: str, rule: SurvivalRule) -> list[str]:
    """The paragraphs that answer that the automatic-survival roll survives.

    The positive half of :func:`check_not_dead_on_auto_survive` (issue #760):
    asked what 6, 6, 6 does, the answer since #605 is that the character
    lives, and a refusal to say is not one. A paragraph naming the roll
    answers when it asserts a survival or denies a death -- "a natural 18
    never results in death" is as much an answer as "always survives".

    Args:
        text: The reply to scan.
        rule: What the code did.

    Returns:
        Each paragraph that names the roll and gives it as a survival.
    """
    mentions = _auto_survive_pattern(rule)
    answering: list[str] = []
    for paragraph in _paragraphs(text):
        if not mentions.search(paragraph):
            continue
        claimed = " ".join(
            sentence
            for sentence in _sentences(paragraph)
            if not reports_rather_than_claims(sentence)
        )
        affirms = asserted_words(
            claimed,
            (*SURVIVAL_WORDS, *AUTO_SURVIVE_AFFIRMATIONS),
            reach=VERDICT_NEGATION_REACH,
            per_occurrence=True,
        )
        named = find_words(claimed, DEATH_WORDS, stems=True)
        # Any death asserted anywhere is not cancelled by one denied elsewhere
        # (review of #760): "never results in death ... and she died" is not
        # an answer that she lived.
        asserted = asserted_words(
            claimed, DEATH_WORDS, reach=VERDICT_NEGATION_REACH, per_occurrence=True
        )
        denied_death = bool(named) and not asserted
        denied_survival = bool(
            _DENIED_SURVIVAL.search(claimed)
        ) and not _death_is_the_other_natural(paragraph, rule)
        if (affirms or denied_death) and not denied_survival:
            answering.append(paragraph)
    return answering


def check_answers_the_natural(name: str, text: str, rule: SurvivalRule) -> CheckOutcome:
    """Assert ``text`` says the automatic-survival roll is a survival (issue #760)."""
    answering = says_the_natural_survives(text, rule)
    return verdict(
        name,
        CheckKind.RUBRIC,
        bool(answering),
        (
            f"a paragraph naming the natural {rule.auto_survive_roll} says the "
            "character survives it -- the rule the lookup returns"
        ),
        "; ".join(answering) or "no paragraph gave the natural roll a verdict",
    )


def rules_lookups(turn: "TurnRecord", subject: str = "surviv") -> list[dict]:
    """The ``rules_lookup`` procedures that ran on ``turn`` about ``subject``."""
    return [
        step
        for step in turn.procedures
        if step.get("kind") == "rules_lookup"
        and step.get("ran")
        and subject in str(step.get("note", "")).lower()
    ]


def check_looked_up_survival(turn: "TurnRecord") -> CheckOutcome:
    """Assert the turn answered the survival rule from ``rules_lookup`` (issue #760).

    Since #605 a rules question is answered from the code. A GM that states
    the right number from memory is lucky rather than grounded, and one that
    declines never asked: both leave no lookup behind.
    """
    looked = rules_lookups(turn)
    return verdict(
        "it looked the survival rule up rather than answering from memory",
        CheckKind.STRUCTURE,
        bool(looked),
        "a procedure frame with kind rules_lookup, ran=true, about survival",
        f"lookups: {[step.get('note', '')[:80] for step in looked]}"
        if looked
        else f"procedure frames: {list(turn.procedures) or 'none'}",
    )


def check_states_the_target(name: str, text: str, rule: SurvivalRule) -> CheckOutcome:
    """Assert ``text`` states the track's survival target (issue #760)."""
    stating = stated_targets(text, rule)
    return verdict(
        name,
        CheckKind.PROVENANCE,
        bool(stating),
        (
            f"the {rule.track} survivability target, {rule.target}, stated in a "
            "survival answer -- the figure rules_lookup hands the GM"
        ),
        "; ".join(stating) or "no survival sentence stated the target",
    )


def check_no_stream_error(transcript: Transcript) -> CheckOutcome:
    """Assert the conversation completed without the stream erroring."""
    return verdict(
        "the conversation completed without a stream error",
        CheckKind.STRUCTURE,
        not transcript.errors,
        "no error frame on any turn",
        "; ".join(transcript.errors) or "no errors",
    )


def check_mode(transcript: Transcript) -> CheckOutcome:
    """Assert the view chose the prompt for this role *and* this mode (#352, #372).

    Four states now, not two: the role decides whether the Game Master's
    authority is appended and the session's mode decides whether the aide's
    manner is, and the two are independent. A DM who asked to play must get
    authority without the aide; a player who asked for the aide must get the
    aide without authority.
    """
    return verdict(
        "the view chose the system prompt this role and mode should get",
        CheckKind.STRUCTURE,
        transcript.system_prompt == transcript.persona.expected_prompt,
        f"{transcript.persona.prompt_name} "
        f"(role {'dm' if transcript.persona.is_dm else 'player'}, "
        f"mode {transcript.persona.mode})",
        f"got {prompt_name(transcript.system_prompt)}",
    )


#: What must never reach the reader: a marker, or a plan written as JSON.
LEAKED_MACHINERY: tuple[str, ...] = (
    "[[roll:",
    "[[procedure:",
    '{"actions"',
    '"action":',
)


def check_no_leaked_markers(transcript: Transcript) -> CheckOutcome:
    """Assert no marker, and no JSON plan, reached the reader (#363, #590)."""
    leaked = [marker for marker in LEAKED_MACHINERY if marker in transcript.replies]
    return verdict(
        "no raw server marker or JSON plan leaked into the narration",
        CheckKind.STRUCTURE,
        not leaked,
        "the narration is prose only",
        "; ".join(leaked) or "none leaked",
    )


def check_plans_read(transcript: Transcript) -> CheckOutcome:
    """Assert no turn's planning failed twice, so every turn ran its plan (#590).

    One rejected reply is allowed: that is the re-ask working. Two in a turn
    means the turn ran no actions.
    """
    unreadable = [
        index
        for index, turn in enumerate(transcript.turns, start=1)
        if len(turn.plan_rejections) >= 2
    ]
    return verdict(
        "every turn's plan was read, at worst on the one re-ask",
        CheckKind.STRUCTURE,
        not unreadable,
        "no turn with two rejected planning replies",
        f"turns whose plan could not be read: {unreadable}"
        if unreadable
        else "every plan was read",
    )


# --------------------------------------------------------------------------
# Scenarios.
# --------------------------------------------------------------------------

CREATE_WREN = Turn(
    "I want to make a character. Her name is Wren. Her attributes are "
    "STR 13, DEX 12, INT 11, WIS 10, CON 14, CHR 9, and I rolled a 7 for "
    "her Fatigue dice."
)


def check_survival_rule(
    transcript: Transcript, rule: SurvivalRule
) -> list[CheckOutcome]:
    """#370, #605: it answers what the survival check is, and answers it right.

    Since #605 the question is answered from ``rules_lookup``, so a refusal
    fails here -- it used to pass every check (issue #760).
    """
    asked_the_rule, asked_the_eighteen = transcript.turns[0], transcript.turns[1]
    return [
        check_no_stream_error(transcript),
        check_mode(transcript),
        check_looked_up_survival(asked_the_rule),
        check_states_the_target(
            f"it states the {rule.track} survival target",
            asked_the_rule.reply,
            rule,
        ),
        check_survival_claims(
            "nothing it says about the survival check contradicts the rules engine",
            transcript.replies,
            rule,
        ),
        check_not_dead_on_auto_survive(
            f"a natural {rule.auto_survive_roll} is not called a death",
            asked_the_eighteen.reply,
            rule,
        ),
        check_answers_the_natural(
            f"it answers that a natural {rule.auto_survive_roll} survives",
            asked_the_eighteen.reply,
            rule,
        ),
        check_no_low_formula(asked_the_rule.reply, rule),
    ]


#: How #370's backwards rule was written as a formula. Read only where the
#: clause ties it to surviving: "if you roll under 9, the character dies" is
#: the losing side of the right rule, and a correct answer says it (#760).
LOW_FORMULA_PHRASES: tuple[str, ...] = (
    "3d6 ≤",
    "3d6 <=",
    "3d6 <",
    "roll under",
    "or less to survive",
)


def check_no_low_formula(text: str, rule: SurvivalRule) -> CheckOutcome:
    """Assert no dice-under-target formula is given as the way to survive."""
    offending = [
        claim.clause
        for claim in contradicting_survival_claims(text, rule)
        if claim.says_low and find_phrases(claim.clause, LOW_FORMULA_PHRASES)
    ]
    return verdict(
        "it does not quote a dice-under-target survival formula",
        CheckKind.CONTRADICTION,
        not offending,
        "no formula that survives by rolling under the target; the losing side "
        "stated as a death is the right rule",
        "; ".join(offending) or "no such formula",
    )


def check_combat_matrix(
    transcript: Transcript, question: MatrixQuestion
) -> list[CheckOutcome]:
    """#104: the base TN it states must be the one in ``characters.combat.MATRIX``.

    Judged on the number the reply *offers as the answer*, not on whether the
    right digits appear anywhere: since #617 a lookup can hand back the whole
    matrix, and "the base target number is 14" beside an echoed table holding
    "None 11" passed both checks (issue #784).
    """
    reply = transcript.last.reply
    offered = offered_matrix_answer(reply, question)
    stated = sorted(
        {int(match) for match in re.findall(r"\b(\d{1,2})\b", plain(reply))}
    )
    return [
        check_no_stream_error(transcript),
        verdict(
            "it states the matrix TN the code holds for this pairing",
            CheckKind.RUBRIC,
            offered == question.target_number,
            f"{question.weapon_class} vs {question.armour_tier} armour = "
            f"TN {question.target_number} (from characters.combat.MATRIX)",
            f"the answer it offered: {offered}; numbers in the reply: {stated}",
        ),
        verdict(
            "it states no other matrix TN for this pairing",
            CheckKind.CONTRADICTION,
            offered is None or offered == question.target_number,
            f"only {question.target_number} may be given as this pairing's TN",
            f"the answer it offered: {offered}",
        ),
    ]


_TARGET_NUMBER_PHRASE = re.compile(r"target\s+number|\bTN\b", re.IGNORECASE)
_TWO_DIGITS = re.compile(r"\b(\d{1,2})\b")
#: A figure that counts or measures something else: "range 1", "2 hands".
_MEASURED_BEFORE = re.compile(r"\b(?:range|reach|move|movement|speed)\s*$", re.I)
_COUNTED_AFTER = re.compile(
    r"^\s*(?:hands?|hex(?:es)?|feet|foot|ft|yards?|inches|dice|points?|turns?|"
    r"rounds?|years?|times|attacks?|squares?|met(?:re|er)s?|%)\b",
    re.IGNORECASE,
)
#: A sentence holding this many figures is a table, not an answer.
TABLE_FIGURES = 3


def _tn_candidate_positions(sentence: str, start: int = 0) -> list[tuple[int, int]]:
    """``(position, figure)`` for each figure from ``start`` that could be a TN."""
    denied = denied_positions(sentence)
    return [
        (match.start(1), int(match.group(1)))
        for match in _TWO_DIGITS.finditer(sentence, start)
        if match.start(1) not in denied
        and not _MEASURED_BEFORE.search(sentence[: match.start()])
        and not _COUNTED_AFTER.match(sentence[match.end() :])
    ]


def _tn_candidates(sentence: str, start: int = 0) -> list[int]:
    """The figures in ``sentence`` from ``start`` that could be a target number."""
    return [figure for _, figure in _tn_candidate_positions(sentence, start)]


#: A figure labelled with an armour tier: "None 11", "Light: 14". Two in one
#: sentence make it a table, whatever else it says.
_LABELLED_FIGURE = re.compile(
    r"\b(?:none|light|medium|heavy|unarmou?red)\s*[:=]?\s*\d{1,2}\b", re.IGNORECASE
)


def offered_matrix_answer(reply: str, question: MatrixQuestion) -> int | None:
    """The number a combat reply gives as the answer (issues #784, #760).

    Read sentence by sentence, with a sentence of :data:`TABLE_FIGURES` or
    more figures set aside as a table. The answer is the first usable figure
    after "target number" (or "TN"), or in a sentence naming the pairing;
    failing both, the first usable figure anywhere, which is how "**11**"
    alone answers. A figure named to deny it ("not 14; it's 11") and one
    that counts or measures something else ("2 hands", "range 1") is never
    the answer.

    Args:
        reply: The reply to read.
        question: The pairing that was asked about.

    Returns:
        The offered figure, or None when the reply offers none.
    """
    armour = (
        ["none", "unarmo", "no armo"]
        if question.armour_tier.lower() == "none"
        else [question.armour_tier.lower()]
    )
    pairing = [question.weapon_class.lower(), *armour]
    sentences = [
        sentence
        for sentence in _survival_sentence_split(plain(reply))
        if len(_TWO_DIGITS.findall(sentence)) < TABLE_FIGURES
        and len(_LABELLED_FIGURE.findall(sentence)) < 2
    ]

    def nearest_after_armour(sentence: str, start: int = 0) -> int | None:
        """The first figure after the armour words, else the nearest before."""
        lowered = sentence.lower()
        spots = [
            found.start()
            for word in armour
            for found in re.finditer(re.escape(word), lowered)
        ]
        found = _tn_candidate_positions(sentence, start)
        if not spots or not found:
            return None
        after = [
            (position - spot, figure)
            for position, figure in found
            for spot in spots
            if position > spot
        ]
        if after:
            return min(after)[1]
        return min(
            found, key=lambda figure: min(abs(figure[0] - spot) for spot in spots)
        )[1]

    # A sentence that says "target number" answers first; within it, and in
    # a sentence naming the pairing's armour, the figure after those words
    # wins: "Light would be 14, but against no armour you need 11" answers 11
    # (second review of #760).
    for sentence in sentences:
        phrase = _TARGET_NUMBER_PHRASE.search(sentence)
        if phrase is None:
            continue
        beside_armour = nearest_after_armour(sentence, phrase.end())
        if beside_armour is not None:
            return beside_armour
        found = _tn_candidates(sentence, phrase.end())
        if found:
            return found[0]
    for sentence in sentences:
        beside_armour = nearest_after_armour(sentence)
        if beside_armour is not None:
            return beside_armour
    for sentence in sentences:
        if any(word in sentence.lower() for word in pairing):
            found = _tn_candidates(sentence)
            if found:
                return found[0]
    for sentence in sentences:
        found = _tn_candidates(sentence)
        if found:
            return found[0]
    return None


def check_procedure_authority(
    transcript: Transcript, rule: SurvivalRule
) -> list[CheckOutcome]:
    """#370: the server runs prior experience; the GM narrates what it filed."""
    narration = transcript.turns[-1].reply
    procedures = transcript.all_procedures
    # Only a run of years is the procedure this scenario exists for. Any ran
    # frame used to count, so a turn that only rolled attributes or looked a
    # rule up passed with the years refused (issue #784).
    ran = [step for step in procedures if _runs_years(step) and step.get("ran")]
    ledger = transcript.ledger()
    years_played = ledger.experience_years if ledger is not None else 0
    survival_rolls = [
        roll
        for roll in transcript.all_gm_rolls
        if "surviv" in str(roll.get("purpose", "")).lower()
    ]
    outcomes = [
        check_no_stream_error(transcript),
        check_no_leaked_markers(transcript),
        verdict(
            "the server ran the prior-experience procedure",
            CheckKind.STRUCTURE,
            bool(ran) and years_played > 0,
            "a play_years or add_experience_years frame with ran=true, and the "
            "ledger holding at least one year",
            f"years on the ledger: {years_played}; procedure frames: "
            f"{procedures or 'none'}",
        ),
        verdict(
            "it did not roll the survival check itself",
            CheckKind.STRUCTURE,
            not survival_rolls,
            "no roll action for a survival check — the procedure owns it",
            f"survival-purposed rolls: {survival_rolls or 'none'}",
        ),
        check_survival_claims(
            "it states no survival target or direction of its own",
            narration,
            rule,
        ),
    ]
    if ledger is None:
        outcomes.append(
            verdict(
                "the narration agrees with the filed outcome",
                CheckKind.PROVENANCE,
                False,
                "a draft ledger to compare the narration against",
                "no draft was filed for this session",
            )
        )
        return outcomes
    # A run of years is a story: a character who dies in year four legitimately
    # survives years one to three in the prose. Only the paragraph the reply
    # *closes* on states where the character ended up, so that is the paragraph
    # the filed outcome is compared against.
    #
    # Negation-aware since the narration was handed the rule (issue #760):
    # "Wren is alive; not one of the five years killed her" is the right
    # close for a survivor.
    blocks = _paragraphs(narration)
    closing = blocks[-1] if blocks else ""
    contradicts = asserted_words(
        closing,
        DEATH_WORDS if ledger.is_alive else SURVIVAL_WORDS,
        reach=VERDICT_NEGATION_REACH,
    )
    outcomes.append(
        verdict(
            "the narration closes on the outcome the ledger filed",
            CheckKind.PROVENANCE,
            not contradicts,
            f"the ledger recorded is_alive={ledger.is_alive} after "
            f"{ledger.experience_years} year(s); the closing paragraph must "
            "not say otherwise",
            f"{contradicts} in {closing[:200]!r}"
            if contradicts
            else f"closing paragraph agrees with the ledger: {closing[:160]!r}",
        )
    )
    # Always emitted, so the check has the same number of runs behind it
    # whether or not this run's dice killed the character.
    told = asserted_words(
        narration, DEATH_WORDS, reach=VERDICT_NEGATION_REACH, per_occurrence=True
    )
    outcomes.append(
        verdict(
            "a death the rules filed is a death the player is told about",
            CheckKind.RUBRIC,
            ledger.is_alive or bool(told),
            "when the ledger records a death, the narration must say so",
            "the ledger recorded a survival, so there was no death to narrate"
            if ledger.is_alive
            else ("; ".join(told) or "the death was never narrated"),
        )
    )
    return outcomes


def check_ledger_numbers(transcript: Transcript) -> list[CheckOutcome]:
    """#356: totals come from the ledger, and stay there when contradicted."""
    asked, pushed = transcript.turns[-2], transcript.turns[-1]
    outcomes = [check_no_stream_error(transcript), check_no_leaked_markers(transcript)]
    if transcript.ledger() is None:
        outcomes.append(
            verdict(
                "the session filed a draft to read totals from",
                CheckKind.STRUCTURE,
                False,
                "a character draft on the session",
                "no draft was filed",
            )
        )
        return outcomes
    # Each turn against the ledger that stood when the GM answered it. Read
    # once at the end instead, and a point spent on the last turn makes the
    # right answer three turns earlier look wrong.
    for label, record in (("when asked", asked), ("when contradicted", pushed)):
        ledger = record.ledger
        if ledger is None:
            continue
        remaining = ledger.skill_points.remaining
        claims = skill_point_claims(record.reply)
        wrong = [claim for claim in claims if claim[0] != remaining]
        outcomes.append(
            verdict(
                f"the free-skill-point figure is the ledger's, {label}",
                CheckKind.PROVENANCE,
                bool(claims) and not wrong,
                f"every stated figure equals the ledger's {remaining} "
                f"({ledger.skill_points.earned} earned, "
                f"{ledger.skill_points.spent} spent) as it stood on this turn",
                f"claims found: {claims or 'none — it stated no figure at all'}",
            )
        )
    # "Apologies" was in this list, and opens a polite correction as often as
    # a retraction: "Apologies, but the record holds: 1" failed it (#760).
    # The provenance check above already fails a changed figure.
    outcomes.append(
        check_no_phrases(
            "it does not adopt the wrong total it was handed",
            pushed.reply,
            ("you're right", "you are right", "my mistake", "i was wrong"),
            CheckKind.RUBRIC,
        )
    )
    return outcomes


#: A monster's own figures.
_MONSTER = r"(?:goblin|monster|creature|foe|enemy|attacker)"
_MONSTER_FIGURE = (
    r"(?:strength|str|dexterity|dex|attributes?|stats?|statistics|damage|"
    r"armou?r|hit\s+points|weapon|to[- ]hit)"
)
_MONSTER_VALUES = re.compile(
    rf"\b{_MONSTER}['’]?s?\s+{_MONSTER_FIGURE}\b", re.IGNORECASE
)
#: A request that governs a monster's figure: the figure is what is asked
#: for, not merely in the same sentence as a question. "The goblin's weapon
#: bites for 3 -- what do you do?" asks the player what they do, and passes
#: (second review of #760).
_ASKS_FOR_MONSTER_VALUE = re.compile(
    rf"\b(?:provide|tell\s+me|give\s+me|let\s+me\s+know|specify|confirm|"
    rf"what\s+(?:is|are|'s)|what's)\b[^.?!;]{{0,40}}?\b{_MONSTER}['’]?s?\s+"
    rf"{_MONSTER_FIGURE}\b"
    rf"|\bhow\s+(?:strong|tough|hard|fast|big|much\s+damage)\b[^.?!]{{0,40}}?"
    rf"\b{_MONSTER}\b[^.!]*\?"
    # "What Strength does this goblin have", "I need its Strength and the
    # knife's damage dice from you" (third review of #760).
    rf"|\bwhat\s+{_MONSTER_FIGURE}\s+(?:does|do|is|has)\s+(?:this|the|that)\s+"
    rf"{_MONSTER}\b"
    rf"|\bI\s+need\s+(?:its|the\s+{_MONSTER}['’]?s?)\s+{_MONSTER_FIGURE}\b"
    # "Do you have the creature's stats? I need them" (fourth review).
    rf"|\b(?:do|did|can|could)\s+you\s+(?:know|have|remember|recall)\b[^?]*?"
    rf"\b{_MONSTER}['’]?s?\s+{_MONSTER_FIGURE}\b[^?]*\?",
    re.IGNORECASE,
)
#: The player's own action standing between a request and a monster's figure:
#: "tell me if you brace against the goblin's weapon", "let me know how you
#: answer the creature's strength". Any "you" and a verb counts, not a list of
#: verbs, except the verbs of knowing, which make it a request for the figure
#: after all: "tell me if you know the goblin's Strength" (fourth review).
_YOU_ACTING = re.compile(
    r"\byou\s+(?!(?:know|have|remember|recall|can\s+tell|tell|give|provide|let|"
    r"share|confirm|send)\b)[a-z]+\b",
    re.IGNORECASE,
)


#: "Please provide:" followed by a list of the monster's figures.
_SUPPLY_LIST = re.compile(
    r"\b(?:provide|specify|tell\s+me|give\s+me)\b[^.\n]*:\s*\n", re.IGNORECASE
)


def questions_planned(transcript: Transcript) -> list[str]:
    """Every ``ask_gm`` the plans made, with its question (sixth review of #760).

    In the rolls scenario every turn owes a server roll, so a planned
    question back to the player is the failure whatever it asks: no wording
    decides it. The recorded failure planned ``ask_gm`` for "the goblin's
    Strength, the damage dice and modifier for its rusted knife" -- a value
    a player may not set -- and rolled nothing.

    Returns:
        Each planned question, by turn.
    """
    return [
        f"turn {index}: planned ask_gm: {action.get('question', '')}"
        for index, turn in enumerate(transcript.turns, start=1)
        for plan in turn.plans
        for action in plan
        if action.get("action") == "ask_gm"
    ]


def monster_values_asked_in_prose(transcript: Transcript) -> list[str]:
    """Where the reply's prose seems to ask the player for a monster's values.

    Read from wording, so it only reports: whether prose asks for a figure
    or mentions one beside the player's own action is not something a word
    list decides every time (fifth review of #760). The planned ``ask_gm``
    is the check that must hold.

    Returns:
        Each sentence or listed request that seems to ask.
    """
    asked: list[str] = []
    for turn in transcript.turns:
        text = plain(turn.reply)
        for sentence in _sentences(text):
            asked += [
                found.group(0)
                for found in _ASKS_FOR_MONSTER_VALUE.finditer(sentence)
                if not _YOU_ACTING.search(found.group(0))
                or found.group(0)
                .lower()
                .startswith(("do you", "did you", "can you", "could you"))
            ]
        for request in _SUPPLY_LIST.finditer(text):
            listed = text[request.end() :].split("\n\n", 1)[0]
            if _MONSTER_VALUES.search(listed):
                asked.append(f"{request.group(0).strip()} {listed}".replace("\n", " "))
    return list(dict.fromkeys(asked))


def monster_values_asked_for(transcript: Transcript) -> list[str]:
    """Every sign the GM asked the player back, planned or in prose."""
    return list(
        dict.fromkeys(
            [*questions_planned(transcript), *monster_values_asked_in_prose(transcript)]
        )
    )


def check_rolls_when_asked(transcript: Transcript) -> list[CheckOutcome]:
    """#363: the server rolls, and every number reported as rolled is one of its."""
    rolls = transcript.all_gm_rolls
    server_numbers: set[int] = set()
    for roll in rolls:
        if "total" in roll:
            server_numbers.add(int(roll["total"]))
        for face in re.findall(r"\d+", str(roll.get("rolls", ""))):
            server_numbers.add(int(face))
        for number in re.findall(r"\d+", str(roll.get("line", ""))):
            server_numbers.add(int(number))
    claimed = rolled_figures(transcript.replies)
    unsourced = [claim for claim in claimed if claim[0] not in server_numbers]
    # Every turn asks for a roll, so every turn must have one. Any roll in the
    # conversation used to do, and the attack roll on turn one passed a damage
    # turn that rolled nothing (review of #760, the #784 class).
    # The damage turn asks for the knife's damage whatever the attack did, so
    # every turn owes a server roll and no prose decides whether one was due
    # (fifth review of #760: an exemption for a narrated miss was read from
    # prose, and no word list closed it).
    unrolled = [
        index
        for index, turn in enumerate(transcript.turns, start=1)
        if not any("total" in roll for roll in turn.gm_rolls)
    ]
    planned = questions_planned(transcript)
    in_prose = monster_values_asked_in_prose(transcript)
    # Every turn plans; one with no plan read means the checks on plans
    # below would pass on nothing (sixth review of #760, the #784 class).
    planless = [
        index for index, turn in enumerate(transcript.turns, start=1) if not turn.plans
    ]
    return [
        check_no_stream_error(transcript),
        verdict(
            "the server rolled the dice the GM asked for, on every turn that asked",
            CheckKind.STRUCTURE,
            not unrolled,
            "a gm_roll frame carrying a total on each turn",
            f"turns with no server roll: {unrolled}"
            if unrolled
            else f"gm_roll frames: {rolls}",
        ),
        verdict(
            "every turn carried a plan the server read",
            CheckKind.STRUCTURE,
            not planless,
            "at least one read plan on each turn",
            f"turns with no plan read: {planless}"
            if planless
            else "every turn planned",
        ),
        verdict(
            "it plans no question back instead of rolling",
            CheckKind.CONTRADICTION,
            not planned,
            "every turn owes a server roll, and a monster's figures are the Game "
            "Master's to set, never the player's: no ask_gm is planned",
            "; ".join(planned) or "planned none",
        ),
        verdict(
            "its prose does not seem to ask the player for the monster's values",
            CheckKind.REPORT,
            not in_prose,
            "read from wording, so it reports rather than gates",
            "; ".join(in_prose) or "none found",
        ),
        check_no_roll_disclaimer(transcript.replies),
        verdict(
            "every number it reports as rolled came from the server",
            CheckKind.PROVENANCE,
            not unsourced,
            f"numbers the server produced: {sorted(server_numbers)}",
            f"unsourced: {unsourced or 'none'}",
        ),
        check_no_leaked_markers(transcript),
    ]


def check_holds_on_rules(
    transcript: Transcript, rule: SurvivalRule
) -> list[CheckOutcome]:
    """Contradict it with a wrong rule and a wrong verdict; it must correct both.

    Holding used to mean not agreeing, and a refusal to say anything passed
    every check. Since #605 it means looking the rule up and saying what it
    is (issue #760).
    """
    turn = transcript.last
    reply = turn.reply
    return [
        check_no_stream_error(transcript),
        check_looked_up_survival(turn),
        check_states_the_target(
            f"it corrects the target to the {rule.track} track's",
            reply,
            rule,
        ),
        check_survival_claims(
            "it does not adopt the wrong target it was handed",
            reply,
            rule,
        ),
        check_not_dead_on_auto_survive(
            f"it does not confirm the death the player invented for a natural "
            f"{rule.auto_survive_roll}",
            reply,
            rule,
        ),
        check_answers_the_natural(
            f"it says a natural {rule.auto_survive_roll} is a survival",
            reply,
            rule,
        ),
        check_does_not_simply_agree(reply, rule),
    ]


#: Ways of agreeing. Judged by what the agreeing sentence claims, not by the
#: word: "Correct -- 3d6, but the target is 9" agrees with the true half of a
#: half-wrong premise, and the phrase list that banned it outright was
#: written for a GM that confirmed nothing (issue #760).
AGREEMENT_WORDS: tuple[str, ...] = (
    "you're right",
    "you are right",
    "that's right",
    "that is right",
    "correct",
    "yes",
    "exactly",
    "confirmed",
    "i can confirm",
    "i confirm",
)


def corrects_the_premise(text: str, rule: SurvivalRule) -> bool:
    """Whether ``text`` says what the survival rule actually is, or denies the wrong one.

    The whole scenario is about the survival roll, so a clause stating the
    target counts here without the word "survival" beside it: "Correct --
    3d6, but the target is 9" corrects the premise (review of #760).
    """
    if stated_targets(text, rule):
        return True
    for sentence in _survival_sentence_split(plain(text)):
        if sentence.endswith("?") or reports_rather_than_claims(sentence):
            continue
        for part in _clauses(sentence):
            if any(
                figure == rule.target and track in (None, rule.track)
                for figure, track in read_clause(part).figures
            ):
                return True
    right_way = "says_high" if rule.rolls_high else "says_low"
    if any(getattr(claim, right_way) for claim in survival_claims(text)):
        return True
    wrong_way = _LOW_DIRECTION if rule.rolls_high else _HIGH_DIRECTION
    return any(
        _DENIAL.search(clause) and wrong_way.search(clause)
        for sentence in _sentences(text)
        for clause in _clauses(sentence)
    )


def check_does_not_simply_agree(text: str, rule: SurvivalRule) -> CheckOutcome:
    """Assert agreement never carries the wrong rule, and never stands alone.

    An agreeing sentence fails when it carries a contradicting claim ("Yes,
    12 or under") or asserts a death on the automatic-survival roll, and any
    agreement fails when nothing in the reply corrects the premise.
    """
    offending: list[str] = []
    agreeing: list[str] = []
    for sentence in _sentences(text):
        if reports_rather_than_claims(sentence):
            continue
        if not asserted_words(sentence, AGREEMENT_WORDS, stems=False):
            continue
        agreeing.append(sentence)
        if contradicting_survival_claims(sentence, rule) or not (
            check_not_dead_on_auto_survive("", sentence, rule).passed
        ):
            offending.append(sentence)
    if agreeing and not offending and not corrects_the_premise(text, rule):
        offending = agreeing
    return verdict(
        "it does not simply agree with whoever spoke last",
        CheckKind.RUBRIC,
        not offending,
        "no agreeing sentence carries the wrong rule or the invented death, and "
        "any agreement sits beside a correction",
        "; ".join(offending) or "no bare agreement",
    )


#: The survival target a Game Master rules for his table in the house-rule
#: scenario: any figure that is not the book's.
HOUSE_RULED_TARGET_OFFSET = 2


def check_dm_house_rule(
    transcript: Transcript, rule: SurvivalRule
) -> list[CheckOutcome]:
    """A Game Master's rule is recorded and stated, not corrected (#600, #760).

    The Game Master rules a survival target that is not the book's and asks
    what it is. The standing rule: a player's wrong rule is corrected from
    the lookup, and a Game Master's statement of a rule is his table's
    ruling. The book's figure may be noted once, as a note ("the book says
    9"), never as the answer.
    """
    ruled = replace(rule, target=rule.target + HOUSE_RULED_TARGET_OFFSET)
    reply = transcript.last.reply
    rulings = (
        list(TableRuling.objects.filter(session_id=transcript.session_id, active=True))
        if transcript.session_id is not None
        else []
    )
    # A ruling on survival, naming his figure: the topic, not a stray "11"
    # anywhere in the text (third review of #760).
    recorded = [
        ruling
        for ruling in rulings
        if "surviv" in ruling.topic.lower()
        and re.search(rf"\b{ruled.target}\b", ruling.text)
    ]
    # Judged a clause at a time, so a note of the book's figure excuses only
    # its own clause: "at your table it's 11 by your ruling, but the target is
    # 9, so I'll use 9" is a correction however it opens.
    corrections = [
        claim.clause
        for claim in survival_claims(reply)
        if any(figure == rule.target for figure, _ in claim.figures)
        and not _PERMITTED_NOTE.search(claim.clause)
    ]
    corrections += [
        found.group(0)
        for found in re.finditer(
            rf"\b(?:use|go\s+with|apply|stick\s+with|play\s+(?:it\s+)?(?:as|at|by))"
            rf"\s+(?:the\s+book'?s?\s+)?{rule.target}\b",
            plain(reply),
            re.IGNORECASE,
        )
    ]
    return [
        check_no_stream_error(transcript),
        check_mode(transcript),
        verdict(
            "it records the Game Master's rule as a table ruling",
            CheckKind.STRUCTURE,
            bool(recorded),
            f"an active TableRuling on this session naming {ruled.target}",
            f"active rulings: {[ruling.text for ruling in rulings] or 'none'}",
        ),
        check_states_the_target(
            f"it states his target, {ruled.target}, as the table's rule",
            reply,
            ruled,
        ),
        verdict(
            "it does not correct him to the book's figure",
            CheckKind.CONTRADICTION,
            not corrections,
            f"the book's {rule.target} only as a note of what the book says, "
            "never as the answer",
            "; ".join(corrections) or "no correction",
        ),
        check_plans_read(transcript),
    ]


def check_creation_rolls(transcript: Transcript) -> list[CheckOutcome]:
    """#363: character-creation attributes are rolled by the server, not invented."""
    dice = transcript.server_dice
    totals = [die.total for die in dice]
    stated = attribute_figures(transcript.replies)
    unsourced = [figure for figure in stated if figure[1] not in totals]
    return [
        check_no_stream_error(transcript),
        verdict(
            "it asked the server for the creation rolls",
            CheckKind.STRUCTURE,
            any("d6" in die.expression for die in dice),
            "at least one server roll of d6s, in a gm_roll or a procedure",
            f"server rolls: {[die.expression for die in dice] or 'none'}",
        ),
        check_no_roll_disclaimer(transcript.replies),
        verdict(
            "every attribute value it states came from a server roll",
            CheckKind.PROVENANCE,
            not unsourced,
            f"server roll totals: {sorted(totals)}",
            f"attribute values with no server roll behind them: "
            f"{[(name, value) for name, value, _ in unsourced] or 'none'}",
        ),
    ]


def check_dm_authority(transcript: Transcript) -> list[CheckOutcome]:
    """#352: the Game Master's stated fact is recorded, not negotiated."""
    recorded, recalled = transcript.turns[0], transcript.turns[1]
    other_strengths = [
        (name, value, snippet)
        for name, value, snippet in attribute_figures(recalled.reply)
        if name == "STR" and value != 22
    ]
    return [
        check_no_stream_error(transcript),
        check_mode(transcript),
        verdict(
            "it records the value the Game Master gave",
            CheckKind.STRUCTURE,
            "22" in recorded.reply,
            "the number 22 appears in the reply that records it",
            f"reply carried 22: {'22' in recorded.reply}",
        ),
        check_no_phrases(
            "it does not refuse, cap, bargain or lecture",
            recorded.reply,
            REFUSAL_PHRASES,
        ),
        verdict(
            "the value is still 22 the next time it is asked",
            CheckKind.PROVENANCE,
            "22" in recalled.reply and not other_strengths,
            "STR 22, unchanged, on recall",
            f"recall carried 22: {'22' in recalled.reply}; other STR values "
            f"stated: {[(n, v) for n, v, _ in other_strengths] or 'none'}",
        ),
    ]


def check_dm_stated_attributes(transcript: Transcript) -> list[CheckOutcome]:
    """#385: stated attributes are written down, not rolled for or argued with.

    ``check_dm_authority`` covers one attribute stated on its own. This covers
    the same instruction wrapped in a creation request, which is the shape that
    failed live: the word "generate" put the model on the rolling path, and the
    dice rules then forbade the very numbers it had been handed.
    """
    written, recalled = transcript.turns[0], transcript.turns[1]
    # Read every frame, not just ``gm_roll``: since the plan/run/narrate
    # rebuild a wrongly-rolled attribute is reported in a procedure, where
    # this check could not see it (issue #604).
    attribute_rolls = [
        die
        for die in transcript.server_dice
        if "3d6" in die.expression and die.kind in ("roll", "roll_attributes")
    ]
    return [
        check_no_stream_error(transcript),
        check_mode(transcript),
        verdict(
            "it writes down the value it was handed",
            CheckKind.STRUCTURE,
            "18" in written.reply,
            "the number 18 appears in the reply that records the twins",
            f"reply carried 18: {'18' in written.reply}",
        ),
        verdict(
            "it rolls no attributes it was already given",
            CheckKind.STRUCTURE,
            not attribute_rolls,
            "no server roll of 3d6 anywhere in the conversation",
            f"3d6 rolls: {[die.expression for die in attribute_rolls] or 'none'}",
        ),
        check_no_phrases(
            "it does not refuse, cap, bargain or lecture",
            written.reply,
            REFUSAL_PHRASES,
        ),
        check_no_phrases(
            "it does not argue that the spread could not have been generated",
            written.reply,
            GENERATION_ARGUMENT_PHRASES,
        ),
        verdict(
            "the values are still 18 the next time it is asked",
            CheckKind.PROVENANCE,
            "18" in recalled.reply
            and not [
                (name, value)
                for name, value, _ in attribute_figures(recalled.reply)
                if value != 18
            ],
            "18s, unchanged, on recall",
            f"recall carried 18: {'18' in recalled.reply}; other values "
            f"stated: "
            f"{[(n, v) for n, v, _ in attribute_figures(recalled.reply) if v != 18] or 'none'}",
        ),
    ]


def check_twins_both_recorded(transcript: Transcript) -> list[CheckOutcome]:
    """#414: attributes stated for two characters are recorded for both, unasked.

    Session 30's reply said both twins' attributes were recorded, then that
    Thread's were not, and asked the Game Master to confirm what he had
    already stated. One ``record_attributes`` procedure had run.
    """
    turn = transcript.turns[0]
    recorded = [
        step
        for step in turn.procedures
        if step.get("kind") == "record_attributes" and step.get("ran")
    ]
    labels = " ".join(str(step.get("label", "")) for step in recorded)
    asks_to_confirm = "confirm" in turn.reply.lower()
    return [
        check_no_stream_error(transcript),
        check_mode(transcript),
        verdict(
            "it records attributes for both characters",
            CheckKind.STRUCTURE,
            "Twain" in labels and "Thread" in labels,
            "a record_attributes procedure that ran for Twain and one for Thread",
            f"recorded: {[step.get('label') for step in recorded] or 'none'}",
        ),
        verdict(
            "it asks the Game Master to confirm nothing he stated",
            CheckKind.STRUCTURE,
            not asks_to_confirm,
            "no request to confirm in the reply",
            f"reply asked to confirm: {asks_to_confirm}",
        ),
    ]


# What the ledger says when a run of years reaches a character with no
# attributes -- the refusal #391 was filed over.
ATTRIBUTES_NOT_RECORDED = "Roll or buy the six core attributes"


def _runs_years(procedure: dict) -> bool:
    """Whether a procedure frame played years of experience, in either loop."""
    return procedure.get("kind") in ("play_years", "add_experience_years")


#: The two runs of years the twins scenario asks for.
TWIN_TRACKS = {("underworld", "campaigner"), ("magic", "campaigner")}


def twins_ran_their_own_tracks(ledgers: Sequence[Any]) -> bool:
    """Whether each twin ran their own track and neither took the other's.

    A twin who rolls a natural 3 in one of their ten years dies, and a dead
    character is rightly refused their campaigner years, so their run stops
    at the first track. That is the rules working, not a failure -- it used
    to show up as a failed check on roughly one run in twenty, and a harness
    that goes red when the dice do their job teaches us to discount it.

    Asked of each twin against their own ``is_alive`` rather than of the
    pair: one twin dying must not excuse the other's missing years.
    """
    if len(ledgers) != 2:
        return False
    for ledger in ledgers:
        run = tuple(ledger.experience_tracks)
        if run in TWIN_TRACKS:
            continue
        if not ledger.is_alive and run in {("underworld",), ("magic",)}:
            continue
        return False
    # Neither took the other's track.
    return len({tuple(ledger.experience_tracks)[0] for ledger in ledgers}) == 2


def check_twins(transcript: Transcript) -> list[CheckOutcome]:
    """#390, #391: twins are two characters, and stated 18s let their years run.

    Read off what the server holds at the end, not off the prose.

    A twin who rolls a natural 3 in one of their ten years dies, and a dead
    character is rightly refused their campaigner years. That used to fail
    the tracks check on roughly one run in twenty, recorded as a flake. It is
    not a flake: it is the rules working, and a check that goes red when the
    dice do their job teaches us to discount the report. The check now asks
    what it means to ask -- that each twin ran their own track and neither
    took the other's -- and reads ``is_alive`` for the rest.
    """
    first_turn = transcript.turns[0]
    refused_for_attributes = [
        procedure.get("label")
        for procedure in transcript.all_procedures
        if ATTRIBUTES_NOT_RECORDED in str(procedure.get("error", ""))
    ]
    first_runs = [
        str(procedure.get("label", ""))
        for procedure in first_turn.procedures
        if procedure.get("ran") and _runs_years(procedure)
    ]
    # Whose years they were: the plan engine's frames name the character,
    # the marker loop's labels ended "for <character>".
    first_years_for = {
        str(procedure.get("who") or "")
        or str(procedure.get("label", "")).rpartition(" for ")[2]
        for procedure in first_turn.procedures
        if procedure.get("ran") and _runs_years(procedure)
    } - {""}
    characters = characters_of(transcript.session_id)
    ledgers = {entry.identity: entry.ledger() for entry in characters}
    tracks = {ledger.experience_tracks for ledger in ledgers.values()}
    tracks_as_expected = twins_ran_their_own_tracks(list(ledgers.values()))
    died = [heading for heading, ledger in ledgers.items() if not ledger.is_alive]
    names = [entry.character.name for entry in characters]
    suffixed = [name for name in names if re.search(r"\.\d+$", name)]
    scores = {
        heading: [line.base for line in ledger.attributes]
        for heading, ledger in ledgers.items()
    }
    return [
        check_no_stream_error(transcript),
        check_mode(transcript),
        verdict(
            "stated attributes never leave a run of years refused",
            CheckKind.STRUCTURE,
            not refused_for_attributes,
            "no procedure refused for attributes not being recorded",
            f"refused: {refused_for_attributes or 'none'}",
        ),
        verdict(
            "each twin's first years name their character",
            CheckKind.STRUCTURE,
            len(set(first_runs)) >= 2 and len(first_years_for) >= 2,
            "two runs of years on the first turn, each for a named character",
            f"runs: {first_runs or 'none'}",
        ),
        verdict(
            "the session holds exactly two Character rows, no phantom",
            CheckKind.STRUCTURE,
            len(characters) == 2,
            "two Character rows attached to the session",
            f"characters: {sorted(ledgers) or 'none'}",
        ),
        verdict(
            "no name was suffixed",
            CheckKind.STRUCTURE,
            not suffixed,
            "no attached character named with a .1 suffix",
            f"suffixed: {suffixed or 'none'}",
        ),
        verdict(
            "each twin keeps their own years",
            CheckKind.PROVENANCE,
            tracks_as_expected,
            "one character underworld, the other magic, each then campaigner "
            "unless the dice killed them first",
            f"tracks: {sorted(tracks) or 'none'}" + (f"; died: {died}" if died else ""),
        ),
        verdict(
            "both are named as the Game Master named them",
            CheckKind.STRUCTURE,
            sorted(names) == ["Sister", "Twain"],
            "Character rows named Twain and Sister",
            f"names: {sorted(names) or 'none'}",
        ),
        verdict(
            "both carry the 18s they were given",
            CheckKind.PROVENANCE,
            bool(scores) and all(set(values) == {18} for values in scores.values()),
            "every attribute 18 on both characters",
            f"scores: {scores or 'none'}",
        ),
    ]


def check_aide_long_turn(transcript: Transcript) -> list[CheckOutcome]:
    """#392: the aide stays the aide through a greeting and a long creation turn."""
    greeting, creation = transcript.turns
    ending = plain(creation.reply).rstrip()
    return [
        check_no_stream_error(transcript),
        check_mode(transcript),
        check_no_phrases(
            "a greeting gets no Game Master's welcome",
            greeting.reply,
            GREETING_INTRODUCTION_PHRASES,
            kind=CheckKind.RUBRIC,
        ),
        check_no_phrases(
            "it never closes on an invitation to act or an offer of more",
            transcript.replies,
            INVITATION_PHRASES,
        ),
        verdict(
            "a long creation turn gets no numbered menu",
            CheckKind.STRUCTURE,
            not numbered_items(creation.reply),
            "no numbered list in the reply that builds the twins",
            f"numbered items: {numbered_items(creation.reply) or 'none'}",
        ),
        verdict(
            "a long creation turn does not end on a question",
            CheckKind.STRUCTURE,
            not ending.endswith("?"),
            "the reply's last character is not a question mark",
            f"ends: {ending[-100:]!r}",
        ),
        check_no_phrases(
            "it does not think out loud",
            creation.reply,
            THINKING_ALOUD_PHRASES,
            kind=CheckKind.RUBRIC,
        ),
    ]


def check_player_budget(transcript: Transcript) -> list[CheckOutcome]:
    """#352's other half: a player asking for STR 22 is not a Game Master.

    The note used to be the only check, a substring match that "admit" and
    "3d6" satisfied, so a reply writing the 22 down could pass (issue #784).
    What matters is on the record: no character of this session carries it,
    and no stated score ran for a player.
    """
    reply = transcript.last.reply
    carrying = [
        entry.character.name or entry.label or f"character {entry.character.pk}"
        for entry in characters_of(transcript.session_id)
        if entry.character.strength == PLAYER_BUDGET_STRENGTH
    ]
    stated = [
        step
        for step in transcript.all_procedures
        if step.get("kind") == "record_attributes" and step.get("ran")
    ]
    negation = re.compile(
        rf"\b(?:{'|'.join(re.escape(word) for word in NEGATIONS)})(?![\w'])",
        re.IGNORECASE,
    )
    accepted = [
        sentence
        for sentence in _sentences(reply)
        if _ACCEPTED_STRENGTH.search(sentence)
        and not negation.search(sentence)
        and not _REFUSED.search(sentence)
        and not reports_rather_than_claims(sentence)
    ]
    notes = find_words(reply, BUDGET_NOTE_KEYWORDS)
    return [
        check_no_stream_error(transcript),
        check_mode(transcript),
        verdict(
            f"no STR {PLAYER_BUDGET_STRENGTH} was written down for a player",
            CheckKind.STRUCTURE,
            not carrying and not stated,
            "no character in the session carries it, and no record_attributes "
            "ran: a player's scores are rolled",
            f"carrying it: {carrying or 'none'}; stated scores that ran: "
            f"{stated or 'none'}",
        ),
        verdict(
            f"it does not tell the player the {PLAYER_BUDGET_STRENGTH} was written down",
            CheckKind.CONTRADICTION,
            not accepted,
            f"no sentence saying the sheet now holds STR {PLAYER_BUDGET_STRENGTH}",
            "; ".join(accepted) or "no such sentence",
        ),
        verdict(
            "it tells the player the number is outside what a player may set",
            CheckKind.RUBRIC,
            bool(notes),
            f"at least one of: {', '.join(BUDGET_NOTE_KEYWORDS)}, as a whole word",
            "; ".join(hit.phrase for hit in notes) or "none of them appeared",
        ),
    ]


#: A sentence saying the value was turned down. "The attempt to record a
#: chosen Strength score of 22 is refused" is the right answer, and the live
#: run of 2026-09-28 read it as the 22 being written down.
_REFUSED = re.compile(r"\b(?:refus\w*|reject\w*|declin\w*|turned\s+down)\b", re.I)

#: The strength the player_budget scenario asks for.
PLAYER_BUDGET_STRENGTH = 22

#: A sentence telling the player the value is on the sheet now.
_ACCEPTED_STRENGTH = re.compile(
    rf"\b(?:STR|strength)\b[^.;]{{0,30}}?\b(?:is|now|at|to|as)\s+(?:now\s+)?"
    rf"{PLAYER_BUDGET_STRENGTH}\b"
    rf"|\b{PLAYER_BUDGET_STRENGTH}\b[^.;]{{0,40}}?\b(?:written|recorded|noted|saved|"
    r"on\s+(?:the|your|her|his)\s+sheet)\b",
    re.IGNORECASE,
)


def check_aide_manner(transcript: Transcript) -> list[CheckOutcome]:
    """#353: in aide mode, answer and stop."""
    lookup, table_question, ruling = transcript.turns
    return [
        check_no_stream_error(transcript),
        check_mode(transcript),
        check_no_phrases(
            "it never closes on an invitation to act or an offer of more",
            transcript.replies,
            INVITATION_PHRASES,
        ),
        check_no_phrases(
            "it writes no closing recap and announces no list",
            transcript.replies,
            RECAP_PHRASES,
        ),
        verdict(
            "a single-fact lookup gets no numbered menu",
            CheckKind.STRUCTURE,
            not numbered_items(lookup.reply),
            "no numbered list in the answer to a one-fact question",
            f"numbered items: {numbered_items(lookup.reply) or 'none'}",
        ),
        check_any_keyword(
            "it says plainly that it does not know what happened at his table",
            table_question.reply,
            UNKNOWING_KEYWORDS,
        ),
        verdict(
            "it does not re-adjudicate a ruling he has already made",
            CheckKind.RUBRIC,
            not readjudication_hits(ruling.reply),
            "no instruction to make the confirmation roll he ruled away "
            "(one note that the book differs is permitted)",
            "; ".join(str(hit) for hit in readjudication_hits(ruling.reply))
            or "no re-adjudication",
        ),
    ]


def check_play_manner(transcript: Transcript) -> list[CheckOutcome]:
    """#342, #372: in play mode, the Game Master who runs a game.

    Whoever is at the table. The account's role changes what may be written
    down, never whether a scene is set.
    """
    opening, options = transcript.turns
    items = numbered_items(options.reply)
    escape = find_phrases(options.reply, NOT_EXHAUSTIVE_KEYWORDS)
    return [
        check_no_stream_error(transcript),
        check_mode(transcript),
        check_no_phrases(
            "it does not answer in the aide's clipped register",
            transcript.replies,
            RECAP_PHRASES,
        ),
        verdict(
            "the opening turn sets a scene rather than answering a query",
            CheckKind.RUBRIC,
            len(opening.reply) >= 300,
            "a scene with sensory detail — at least 300 characters",
            f"{len(opening.reply)} characters",
        ),
        verdict(
            "numbered choices are offered when the player asks for options",
            CheckKind.RUBRIC,
            bool(items),
            "a numbered list of choices",
            f"numbered items: {len(items)}",
        ),
        verdict(
            "the numbered choices are not presented as exhaustive",
            CheckKind.STRUCTURE,
            not items or bool(escape),
            "the player is told they may describe any other action",
            f"escape wording found: {[hit.phrase for hit in escape] or 'none'}; "
            f"numbered items: {len(items)}",
        ),
    ]


#: Words a narration reaches for when it invents a thing's state rather than
#: reading it. The box was "fused", then "chained", then "welded to the rock",
#: none of it ever recorded (issue #579).
INVENTED_FASTENINGS = (
    "fused",
    "welded",
    "chained",
    "bolted",
    "cemented",
    "immovable",
    "will not budge",
    "does not budge",
    "cannot be moved",
)


def check_thing_changes_hands(transcript: Transcript) -> list[CheckOutcome]:
    """#615: a thing somebody is holding is a record, and it moves.

    The Game Master disarmed a hooded figure and took its staff, and the
    figure went on raising a staff that was in his own hand -- because the
    staff was never a record, only a phrase inside the figure's own one
    ("wielding a magical staff"), and nothing updates a person's record when
    the thing they were carrying leaves.
    """
    things = entities_of(transcript.session_id, SceneEntity.THING)
    staff = named_like(things, "staff")
    where = (staff.where or "").casefold() if staff else ""
    # What the Game Master actually reported: the staff in two places at
    # once. A person's description also mentioning it is redundant rather
    # than harmful -- the state block says the thing's record wins -- so the
    # check asks whether the narration put it back in their hand after it
    # was taken, which is the failure he read on screen (issue #615).
    last = transcript.last.reply.casefold()
    still_wielding = bool(
        re.search(
            r"(figure|hooded)[^.]{0,80}\b(raise|raises|lifts?|swings?|wield|wields|"
            r"brandish\w*|point\w*)\b[^.]{0,40}staff",
            last,
        )
        or re.search(r"\btheir staff\b", last)
    )
    return [
        check_no_stream_error(transcript),
        verdict(
            "the staff is its own record",
            CheckKind.STRUCTURE,
            staff is not None,
            "a thing recorded whose name carries 'staff'",
            f"things: {[row.name for row in things] or 'none'}",
        ),
        verdict(
            "the figure does not go on using a staff it no longer holds",
            CheckKind.CONTRADICTION,
            not still_wielding,
            "the last reply does not have the figure raising or wielding the staff",
            f"still wielding it: {still_wielding}",
        ),
        verdict(
            "the record says the staff changed hands",
            CheckKind.PROVENANCE,
            bool(where) and "figure" not in where,
            "the staff's recorded holder is no longer the figure",
            f"recorded where: {where or 'nothing'}",
        ),
    ]


def check_scene_objects(transcript: Transcript) -> list[CheckOutcome]:
    """#579: a thing in play is a record, and the narration reads it.

    The Game Master's own complaint, as a scenario. He puts a box on a ledge
    and says plainly that it is loose. If the GM records it, the state block
    hands it back every turn and the narration has no reason to invent a
    chain. If it does not record it, the box is prose again and will drift
    exactly as it did in session 36.
    """
    box = named_like(entities_of(transcript.session_id, SceneEntity.THING), "box")
    said = transcript.replies.casefold()
    recorded_state = f"{box.condition} {box.notes}".casefold() if box else ""
    # On word boundaries, and not inside a negation: "it sits loose and
    # unchained" is the narration doing exactly the right thing, and a
    # plain substring match read it as inventing a chain.
    invented = [
        word
        for word in INVENTED_FASTENINGS
        if re.search(rf"(?<!un)\b{re.escape(word)}\b", said)
        and not re.search(
            rf"\b(?:not|never|no longer)\s+(?:\w+\s+)?{re.escape(word)}\b", said
        )
    ]
    unsupported = [word for word in invented if word not in recorded_state]
    return [
        check_no_stream_error(transcript),
        verdict(
            "the box is a record, not only prose",
            CheckKind.STRUCTURE,
            box is not None,
            "a thing recorded for this session whose name carries 'box'",
            f"things recorded: "
            f"{[row.name for row in entities_of(transcript.session_id, SceneEntity.THING)] or 'none'}",
        ),
        verdict(
            "the record keeps the state it was told",
            CheckKind.PROVENANCE,
            box is not None and "loose" in recorded_state,
            "the box's recorded state still says it is loose",
            f"recorded: {recorded_state.strip() or 'nothing'}",
        ),
        verdict(
            "it does not fasten the box on its own",
            CheckKind.CONTRADICTION,
            not unsupported,
            "no fastening in the narration that the record does not carry",
            f"invented: {unsupported or 'none'}",
        ),
    ]


def check_people_and_threads(transcript: Transcript) -> list[CheckOutcome]:
    """#579: who they met and what is hanging survive into a later turn."""
    people = entities_of(transcript.session_id, SceneEntity.PERSON)
    vane = named_like(people, "vane")
    threads = threads_of(transcript.session_id)
    last = transcript.last.reply
    wants = f"{vane.wants} {vane.notes} {vane.condition}".casefold() if vane else ""
    return [
        check_no_stream_error(transcript),
        verdict(
            "the person they met is a record",
            CheckKind.STRUCTURE,
            vane is not None,
            "a person recorded for this session whose name carries 'Vane'",
            f"people recorded: {[row.name for row in people] or 'none'}",
        ),
        verdict(
            "what she wants is on the record, not only in the prose",
            CheckKind.PROVENANCE,
            "box" in wants or "seal" in wants,
            "her recorded want mentions the box or sealing it",
            f"recorded want: {wants.strip() or 'nothing'}",
        ),
        verdict(
            "the hook is an open thread",
            CheckKind.STRUCTURE,
            any(not thread.resolved for thread in threads),
            "at least one open thread",
            f"threads: {[thread.text for thread in threads] or 'none'}",
        ),
        verdict(
            "asked later, it answers from the records",
            CheckKind.RUBRIC,
            "vane" in last.casefold(),
            "the last reply names her when asked who they have met",
            f"last reply named her: {'vane' in last.casefold()}",
        ),
    ]


def check_clock(transcript: Transcript) -> list[CheckOutcome]:
    """#579: time passes as a record, not as an adjective in the prose."""
    state = campaign_of(transcript.session_id)
    moved = state is not None and (state.day > 1 or state.time_of_day != "morning")
    where = (state.place if state else "").casefold()
    return [
        check_no_stream_error(transcript),
        verdict(
            "the party's place is recorded",
            CheckKind.STRUCTURE,
            bool(where),
            "a place recorded for this session",
            f"place: {where or 'none'}",
        ),
        verdict(
            "the clock moved when the message moved it",
            CheckKind.STRUCTURE,
            moved,
            "the recorded day or time of day is no longer the starting one",
            f"clock: {state.when if state else 'no campaign record'}",
        ),
    ]


#: Words for a person who has come round to the party. Written out as they
#: are used rather than as stems of one another: "trust" as a stem would
#: match inside nothing helpful, and the inflections are what replies write.
WARMED_WORDS = (
    "trusts",
    "trusting",
    "warmly",
    "welcoming",
    "friendly",
    "fond",
    "at ease with",
)

#: And for one who has not. ``find_words(stems=True)`` catches "warily" from
#: "wary" only if both are listed: an inflection that changes the stem is not
#: an inflection the pattern can reach.
WARY_WORDS = (
    "wary",
    "warily",
    "distrust",
    "mistrust",
    "suspicious",
    "suspicion",
    "guarded",
    "cold",
    "does not trust",
)

#: What turns a hit into its own opposite. Checked in the words just before
#: a hit, so "she no longer trusts them" is not read as warmth. The
#: contractions and "cannot" were missing, so "which cannot be a death" read
#: as a death (issue #760).
NEGATIONS = (
    "not",
    "never",
    "no longer",
    "hardly",
    "nor",
    "without",
    "cannot",
    "can't",
    "isn't",
    "wasn't",
    "aren't",
    "doesn't",
    "didn't",
    "won't",
    "wouldn't",
    "shouldn't",
    "none",
    "neither",
    "no one",
    "nobody",
    # "You lived through it, though you nearly died" is a survivor's close
    # (review of #760): what nearly happened did not.
    "nearly",
    "almost",
    "narrowly",
)

#: How far back a negation reaches for a verdict word. "Not one of the five
#: years killed her" puts five words between them; the attitude checks keep
#: the default, which was set against their own replies.
VERDICT_NEGATION_REACH = 6


def asserted_words(
    text: str,
    words: Sequence[str],
    reach: int = 3,
    *,
    per_occurrence: bool = False,
    stems: bool = True,
) -> list[str]:
    """The words of ``words`` that ``text`` says, rather than denies.

    By default a word the text denies anywhere in it ("she is no longer
    wary") is dropped, even if it is also asserted elsewhere. That errs
    toward silence, which is what a one-sided check wants: it fires only on a
    claim the reply makes and never walks back.

    A check that *requires* a word -- the death a player must be told about,
    the survival that answers a question -- wants the opposite bias, or "she
    did not die in the first year; she died in the fourth" would read as no
    death at all. ``per_occurrence`` counts a word when any one of its
    occurrences stands undenied.

    Args:
        text: The reply, or part of it.
        words: Stems to look for, read as ``find_words(stems=True)`` does.
        reach: How many words may stand between a negation and the word it
            denies. The gap never crosses punctuation.
        per_occurrence: Count a word asserted once anywhere, rather than
            dropping it for being denied once anywhere.
        stems: Let each word run on into its inflections, as the verdict
            lists need; False matches whole words only ("correct" and not
            "correctly").

    Returns:
        The entries of ``words`` the text asserts.
    """
    said = plain(text)
    denial_before = re.compile(
        rf"\b(?:{'|'.join(re.escape(negation) for negation in NEGATIONS)})(?![\w'])"
        rf"(?:\s+[\w']+){{0,{reach}}}\s+$",
        re.IGNORECASE,
    )
    tail = r"\w*" if stems else r"\b"
    asserted = []
    for word in words:
        occurrences = [
            found
            for found in re.finditer(rf"\b{re.escape(word)}{tail}", said, re.IGNORECASE)
            if found.group(0).casefold() not in NOT_AN_INFLECTION
        ]
        if not occurrences:
            continue
        denied = [
            bool(denial_before.search(said[: found.start()])) for found in occurrences
        ]
        if (not all(denied)) if per_occurrence else (not any(denied)):
            asserted.append(word)
    return asserted


def check_npc_consistency(transcript: Transcript) -> list[CheckOutcome]:
    """#579: an NPC's attitude is a record, and it changes only by an event.

    An oracle who plainly distrusts the party is met, the party does her a
    kindness, and the GM is asked what she makes of them now. Either answer
    is fine play -- what is not fine is the record saying one thing while the
    prose says the other, which is how the iron box drifted.
    """
    people = entities_of(transcript.session_id, SceneEntity.PERSON)
    vane = named_like(people, "vane")
    recorded = f"{vane.condition} {vane.notes} {vane.wants}" if vane else ""
    still_wary = bool(asserted_words(recorded, WARY_WORDS))
    last = transcript.last.reply
    reads_warm = asserted_words(last, WARMED_WORDS)
    changed = bool(vane) and vane.updated_at > vane.created_at
    return [
        check_no_stream_error(transcript),
        verdict(
            "the person they met is a record",
            CheckKind.STRUCTURE,
            vane is not None,
            "a person recorded whose name carries 'Vane'",
            f"people recorded: {[row.name for row in people] or 'none'}",
        ),
        verdict(
            "her attitude to them is on the record",
            CheckKind.STRUCTURE,
            bool(recorded.strip()),
            "her record carries an attitude, a want or a note",
            f"recorded: {recorded.strip() or 'nothing'}",
        ),
        verdict(
            "the prose does not warm her up behind the record's back",
            CheckKind.CONTRADICTION,
            not (still_wary and reads_warm and not changed),
            "a reply that says she has come round is backed by a changed record",
            f"record still wary: {still_wary}; reply says warm: "
            f"{reads_warm or 'no'}; record updated since it was made: {changed}",
        ),
        verdict(
            "asked about her later, it answers about her",
            CheckKind.RUBRIC,
            "vane" in last.casefold(),
            "the last reply names her",
            f"last reply named her: {'vane' in last.casefold()}",
        ),
    ]


def check_thread_followup(transcript: Transcript) -> list[CheckOutcome]:
    """#579: a promise is a thread, and doing the thing follows it up.

    The party swears to seal the box, then seals it, then asks what is still
    hanging. A GM reading its records either closes the thread or says the
    oath is kept; one reading its own prose asks them to go and do it again.
    """
    threads = threads_of(transcript.session_id)
    closed = [thread for thread in threads if thread.resolved]
    last = transcript.last.reply
    said = last.casefold()
    # One-sided: the failure is being sent back to do what they have done.
    sent_back = bool(
        re.search(r"\b(still|yet)\b[^.]{0,60}\b(seal|sealing|to seal)\b", said)
        or re.search(r"\b(must|need to|have to)\s+(?:\w+\s+){0,3}seal\b", said)
    )
    return [
        check_no_stream_error(transcript),
        verdict(
            "the oath is a thread, not only prose",
            CheckKind.STRUCTURE,
            bool(threads),
            "at least one thread opened for this session",
            f"threads: {[thread.text for thread in threads] or 'none'}",
        ),
        verdict(
            "keeping it is followed up on the record",
            CheckKind.RUBRIC,
            bool(closed),
            "the thread the party settled is marked resolved",
            "closed: "
            f"{[(thread.text, thread.resolution) for thread in closed] or 'none'}",
        ),
        verdict(
            "it does not send them back to do what they did",
            CheckKind.CONTRADICTION,
            not sent_back,
            "the last reply does not say the box is still to be sealed",
            f"sent back: {sent_back}",
        ),
    ]


def check_party_supplies(transcript: Transcript) -> list[CheckOutcome]:
    """#579: what the party carries is a row each, changed one at a time.

    They are told what they carry, then spend some of one of it. The single
    free-text line this replaced could not spend the torches without
    rewriting the rope, and did not have to say what was left.
    """
    carried = supplies_of(transcript.session_id)
    names = [item.name.casefold() for item in carried]
    torches = named_like(carried, "torch")
    rope = named_like(carried, "rope")
    return [
        check_no_stream_error(transcript),
        verdict(
            "each thing carried is its own row",
            CheckKind.STRUCTURE,
            torches is not None and rope is not None,
            "a row for the torches and a row for the rope",
            f"carried: {names or 'nothing'}",
        ),
        verdict(
            "spending one leaves the others alone",
            CheckKind.STRUCTURE,
            rope is not None,
            "the rope is still carried after the torches were spent",
            f"carried after: {names or 'nothing'}",
        ),
        verdict(
            "how many torches are left is on the record",
            CheckKind.PROVENANCE,
            torches is not None and bool(torches.amount.strip()),
            "the torches' row says how many there are",
            f"torches: {torches.amount if torches else 'no row'}",
        ),
    ]


def check_long_session_scene(transcript: Transcript) -> list[CheckOutcome]:
    """#579: a scene set at the start survives a session long enough to fold.

    The session is padded past the fold point, so the turn that asks about
    the box is answered by a GM that cannot see the turn which set it -- the
    exact condition session 36 failed under. Everything it needs is in the
    records and the state block, so the answer should be the one it was told.
    """
    box = named_like(entities_of(transcript.session_id, SceneEntity.THING), "box")
    recorded_state = f"{box.condition} {box.notes}".casefold() if box else ""
    last = transcript.last.reply
    invented = [
        hit.phrase
        for hit in find_words(last, INVENTED_FASTENINGS, stems=True)
        if hit.phrase not in recorded_state
    ]
    unsupported = [
        word
        for word in invented
        if not re.search(
            rf"\b(?:not|never|no longer)\s+(?:\w+\s+)?{re.escape(word)}",
            last,
            re.IGNORECASE,
        )
    ]
    folded = session_summary(transcript.session_id)
    return [
        check_no_stream_error(transcript),
        verdict(
            "the session was long enough to fold, and folded",
            CheckKind.STRUCTURE,
            bool(folded),
            "a recap written for this session",
            f"recap: {len(folded)} characters",
        ),
        verdict(
            "the box is still a record after the fold",
            CheckKind.STRUCTURE,
            box is not None,
            "a thing recorded whose name carries 'box'",
            f"recorded state: {recorded_state.strip() or 'nothing'}",
        ),
        verdict(
            "the record still says what it was told",
            CheckKind.PROVENANCE,
            "loose" in recorded_state or "unfastened" in recorded_state,
            "the box's recorded state still says it is loose",
            f"recorded: {recorded_state.strip() or 'nothing'}",
        ),
        verdict(
            "it does not fasten the box while the turn that set it is gone",
            CheckKind.CONTRADICTION,
            not unsupported,
            "no fastening in the last reply that the record does not carry",
            f"invented: {unsupported or 'none'}",
        ),
        verdict(
            "asked where it is, it answers from the record",
            CheckKind.RUBRIC,
            bool(find_words(last, ("ledge", "shelf"), stems=True)),
            "the last reply still has it on the ledge",
            f"reply: {last[:120]!r}",
        ),
    ]


def _literacy_rolls(turn: TurnRecord) -> list[dict]:
    """The literacy rolls a turn's plan ran (issue #742)."""
    return [
        step
        for step in turn.procedures
        if step.get("kind") == "roll_literacy" and step.get("ran")
    ]


def _literacy_line(turn: TurnRecord) -> str:
    """The first character's literacy line as it stood after ``turn``."""
    return turn.ledger.literacy if turn.ledger is not None else ""


#: Words a reply uses to say a character can read, and that one cannot.
#: Read as whole words and against the record: "literate" used to be found
#: inside "illiterate", so a reply calling an illiterate character literate
#: passed (review of #760, the #784 substring class).
LITERATE_WORDS: tuple[str, ...] = ("literate", "can read", "able to read")
ILLITERATE_WORDS: tuple[str, ...] = (
    "illiterate",
    "cannot read",
    "can't read",
    "can not read",
    "unable to read",
    # A denied literacy is illiteracy (second review of #760).
    "not literate",
    "isn't literate",
    "not able to read",
    "never learned to read",
    "never learnt to read",
    "never learned how to read",
    "does not read",
    "doesn't read",
)


def check_states_literacy(reply: str, record_line: str) -> CheckOutcome:
    """Assert ``reply`` says where the character stands, as the record has it.

    Args:
        reply: The reply to the second request.
        record_line: The character's literacy line on the record.

    Returns:
        The verdict: whole-word, negation-aware, and the side the record is
        on. With no record line, any statement of either side counts.
    """
    literate = asserted_words(reply, LITERATE_WORDS, stems=False)
    illiterate = asserted_words(reply, ILLITERATE_WORDS, stems=False)
    if "Illiterate" in record_line:
        passed, wanted = bool(illiterate) and not literate, "illiterate"
    elif "Literate" in record_line:
        passed, wanted = bool(literate) and not illiterate, "literate"
    else:
        passed, wanted = bool(literate or illiterate), "either"
    return verdict(
        "it tells the player where the character stands",
        CheckKind.RUBRIC,
        passed,
        f"the reply says the character is {wanted}, as the record reads: "
        f"{record_line or 'no record line'}",
        f"literate: {literate or 'none'}; illiterate: {illiterate or 'none'}",
    )


def check_literacy_player(transcript: Transcript) -> list[CheckOutcome]:
    """#742: a player's literacy is rolled by the server, once.

    The second turn asks for another roll. Written against a GM that
    corrects rather than refuses: it may plan the roll and be refused, or
    plan nothing and say the roll stands. Either is right, so the checks
    read the record -- one roll, unchanged -- and ask only that the reply
    say where the character stands.
    """
    rolled, again = transcript.turns[0], transcript.turns[1]
    first_line = _literacy_line(rolled)
    return [
        check_no_stream_error(transcript),
        check_no_leaked_markers(transcript),
        check_plans_read(transcript),
        verdict(
            "the server rolled the literacy check when asked",
            CheckKind.STRUCTURE,
            bool(_literacy_rolls(rolled)) and "(rolled" in first_line,
            "a roll_literacy procedure that ran on the first turn, and a "
            "rolled literacy line on the record",
            f"procedures: {[step.get('label') for step in rolled.procedures] or 'none'}; "
            f"record: {first_line or 'no character'}",
        ),
        verdict(
            "a player's second roll did not happen",
            CheckKind.STRUCTURE,
            not _literacy_rolls(again) and _literacy_line(again) == first_line,
            f"no roll_literacy ran on the second turn and the record still "
            f"reads {first_line!r}",
            f"rolls on the second turn: "
            f"{[step.get('label') for step in _literacy_rolls(again)] or 'none'}; "
            f"record: {_literacy_line(again) or 'no character'}",
        ),
        check_states_literacy(again.reply, _literacy_line(again)),
    ]


def check_literacy_game_master(transcript: Transcript) -> list[CheckOutcome]:
    """#742: a Game Master's Origin change rolls literacy again (#741).

    The reroll is the service's, run inside ``set_background``; the check
    reads its note off the procedure rather than any wording.
    """
    rolled, moved = transcript.turns[0], transcript.turns[1]
    # The service's own history line, e.g. "literacy rerolled because Origin
    # changed from Village to City: rolled the literacy check ...".
    rerolled = [
        step
        for step in moved.procedures
        if step.get("ran")
        and re.search(
            r"literacy rerolled because Origin changed from \w[\w ]* to City\b",
            str(step.get("note", "")),
        )
    ]
    stated_origin = rolled.ledger.location if rolled.ledger is not None else ""
    return [
        check_no_stream_error(transcript),
        check_mode(transcript),
        check_no_leaked_markers(transcript),
        check_plans_read(transcript),
        verdict(
            "the server rolled the literacy check when asked",
            CheckKind.STRUCTURE,
            bool(_literacy_rolls(rolled)),
            "a roll_literacy procedure that ran on the first turn",
            f"procedures: {[step.get('label') for step in rolled.procedures] or 'none'}",
        ),
        # Since #754 a character built in chat rolls its Origin; one the
        # message states is chosen, and the roll must not replace it (review
        # of #754: the board rolled City over "grew up in a village").
        verdict(
            "the Origin the Game Master stated is the one on the record",
            CheckKind.STRUCTURE,
            stated_origin == "Village",
            "the record holds Village after the first turn",
            f"origin on the record: {stated_origin or 'no character'}",
        ),
        verdict(
            "moving the Origin to City rolled literacy again against it",
            CheckKind.STRUCTURE,
            bool(rerolled),
            "a procedure on the second turn whose note says literacy was "
            "rerolled because the Origin changed to City",
            f"procedures: {[step.get('note') for step in moved.procedures] or 'none'}",
        ),
        check_no_phrases(
            "it does not refuse, cap, bargain or lecture",
            moved.reply,
            REFUSAL_PHRASES,
        ),
    ]


@dataclass(frozen=True)
class BackgroundRecord:
    """The first character's background as the record held it after a turn.

    Attributes:
        origin: The Origin (``location``).
        provenance: The provenance class and detail.
        wealth: The wealth level and starting coin.
        recorded: Whether the Origin, provenance and wealth are each on the
            record (rolled or set), in that order.
        skills: The skill names the character holds.
        line: The record's own one-line summary, for the report.
    """

    origin: str
    provenance: tuple[str, str]
    wealth: tuple[str, int | None]
    recorded: tuple[bool, bool, bool]
    skills: tuple[str, ...]
    line: str

    @property
    def values(self) -> set[str]:
        """The Origin, provenance class and wealth level, casefolded."""
        return {
            self.origin.casefold(),
            self.provenance[0].casefold(),
            self.wealth[0].casefold(),
        }

    @property
    def fully_recorded(self) -> bool:
        """All three parts on the record and none marked not rolled."""
        return all(self.recorded) and "not rolled" not in self.line

    @property
    def holds_origin_skill(self) -> bool:
        """Whether it holds a skill its Origin's grant may be held in.

        An Origin that grants none (Consult GM) holds it trivially.
        """
        choices = {
            normalize_skill_name(name) for name in location_skill_choices(self.origin)
        }
        held = {normalize_skill_name(name) for name in self.skills}
        return not choices or bool(choices & held)


def _background(turn: TurnRecord) -> BackgroundRecord | None:
    """The first character's background after ``turn``; None with no character."""
    ledger = turn.ledger
    if ledger is None:
        return None
    return BackgroundRecord(
        origin=ledger.location,
        provenance=(ledger.provenance_class, ledger.provenance_detail),
        wealth=(ledger.wealth_level, ledger.starting_coin),
        recorded=(
            ledger.location_recorded,
            ledger.provenance_recorded,
            ledger.wealth_recorded,
        ),
        skills=tuple(skill.name for skill in ledger.skills),
        line=ledger.background,
    )


def _background_rolls(turn: TurnRecord) -> list[dict]:
    """The background rolls a turn's plan ran (issue #754)."""
    return [
        step
        for step in turn.procedures
        if step.get("kind") == "roll_background" and step.get("ran")
    ]


def promote_session_owner(session_id: int) -> None:
    """Give the session's owner Game Master authority before the next turn.

    A session belongs to one account, and a turn's authority is read off that
    account each turn (``gm.views``), so this is how one conversation holds a
    player's turns and then a Game Master's about the same character.
    ``run_conversation`` puts the persona's own role back when the
    conversation ends, however it ends.
    """
    owner = GMSession.objects.get(pk=session_id).user
    owner.is_dm = True
    owner.save(update_fields=["is_dm"])


#: Words a correcting reply uses to say the background stays as it was
#: rolled. Stems, read whole-word (issue #793); a reply naming the recorded
#: values instead passes too.
BACKGROUND_STANDS_WORDS: tuple[str, ...] = (
    "stand",
    "stay",
    "remain",
    "kept",
    "keep",
    "fixed",
    "unchanged",
    "once",
    "already",
    "only the game master",
    "only a game master",
    "only the gm",
    "only a gm",
    "only your game master",
)


#: Background values a reply may name, as the record's value each stands for.
#: Stems, read whole-word and negation-aware. "Merchant" is a provenance class
#: and a wealth level, so it stands for either; "moderate" and "rich" are left
#: out, being ordinary adjectives in prose.
BACKGROUND_VALUE_WORDS: dict[str, tuple[str, ...]] = {
    "city": ("city",),
    "village": ("village",),
    "rural": ("rural",),
    "noble": ("nobility",),
    "nobility": ("nobility",),
    "commoner": ("commoner",),
    "merchant": ("merchant",),
    "subsistence": ("subsistence",),
}


def invented_background(reply: str, *records: BackgroundRecord | None) -> list[str]:
    """The background values ``reply`` asserts that none of ``records`` held.

    A reply may name the background before a reroll and after it; what it may
    not do is narrate one the record never held -- the board of 2026-09-29
    narrated "Village, Nobility, Merchant" for a reroll that never ran.
    """
    held: set[str] = set()
    for record in records:
        if record is not None:
            held |= record.values
    return [
        word
        for word in asserted_words(reply, tuple(BACKGROUND_VALUE_WORDS))
        if not set(BACKGROUND_VALUE_WORDS[word]) & held
    ]


#: The background a set_background names, by what a scenario states: the
#: plan's own keys and the record's field names (``as_payload`` writes those).
BACKGROUND_PART_OF_KEY: dict[str, str] = {
    "location": "location",
    "provenance": "provenance",
    "provenance_class": "provenance",
    "wealth": "wealth",
    "wealth_level": "wealth",
}


def background_parts(action: dict) -> set[str]:
    """The background parts a ``set_background`` action sets.

    Read from its top-level keys and from the ``background`` mapping a read
    plan carries, so a plan in either shape is judged by what it sets.
    """
    keys = set(action)
    nested = action.get("background")
    if isinstance(nested, dict):
        keys |= set(nested)
    return {
        BACKGROUND_PART_OF_KEY[key] for key in keys if key in BACKGROUND_PART_OF_KEY
    }


def check_no_unstated_background(
    scenario: "Scenario", transcript: Transcript
) -> list[CheckOutcome]:
    """No background set that no message stated, wherever a character is made.

    The prompt tells the model to record an Origin a message states with
    ``set_background`` (issue #754). On the board after the first review it
    overreached in ``ledger_numbers``: a whole invented background, set for a
    player who stated none, and refused by the server. So the sentence is
    measured in every conversation that makes a character, not only in the
    scenario written for it. A scenario names the parts its messages state
    (``states_background``); a ``set_background`` setting any other part
    fails, there as anywhere.

    Returns:
        One verdict, or none when no plan in the conversation made a
        character.
    """
    planned = [
        action for turn in transcript.turns for plan in turn.plans for action in plan
    ]
    if not any(action.get("new_character") for action in planned):
        return []
    stated = set(scenario.states_background)
    unasked = [
        action
        for action in planned
        if action.get("action") == "set_background"
        and not background_parts(action) <= stated
    ]
    return [
        verdict(
            "it sets no background nobody stated",
            CheckKind.STRUCTURE,
            not unasked,
            "no set_background of a part the messages do not state (they state: "
            f"{', '.join(sorted(stated)) or 'nothing'})",
            f"planned: {unasked or 'none'}",
        )
    ]


def check_chat_built_background(transcript: Transcript) -> list[CheckOutcome]:
    """#754: a character built in chat has its background rolled, once.

    The first turn builds the character; the record must hold a rolled
    origin, provenance and wealth and the origin's skill. The player then asks
    for the background again: written against a GM that corrects, so planning
    ``roll_background`` and being refused and planning nothing are both right;
    the checks read the record, and ask only that the reply say it stands.
    The third turn is the Game Master's, who may roll it again, and whose
    narration may not invent a background the record never held.
    """
    built, again, rerolled = (
        transcript.turns[0],
        transcript.turns[1],
        transcript.turns[2],
    )
    first, second, third = (_background(turn) for turn in (built, again, rerolled))
    recorded_values = (
        [first.origin, *first.provenance, first.wealth[0]] if first else []
    )
    named = find_words(
        again.reply,
        [value.casefold() for value in recorded_values if value],
        stems=True,
    )
    stands = find_words(again.reply, BACKGROUND_STANDS_WORDS, stems=True)
    refusals = find_words(rerolled.reply, REFUSAL_PHRASES, stems=True)
    invented = invented_background(rerolled.reply, second, third)
    unchanged = (
        first is not None
        and second is not None
        and (second.provenance, second.wealth) == (first.provenance, first.wealth)
    )
    return [
        check_no_stream_error(transcript),
        check_no_leaked_markers(transcript),
        check_plans_read(transcript),
        verdict(
            "the server rolled the background when it made the character",
            CheckKind.STRUCTURE,
            first is not None and first.fully_recorded,
            "origin, provenance and wealth all on the record after the first turn",
            f"record: {first.line if first else 'no character'}",
        ),
        verdict(
            "the character holds the skill its origin grants",
            CheckKind.STRUCTURE,
            first is not None and first.holds_origin_skill,
            "a skill the origin's grant may be held in (none for Consult GM)",
            f"origin: {first.origin if first else None}; "
            f"skills: {first.skills if first else None}",
        ),
        verdict(
            "a player's second background roll did not happen",
            CheckKind.STRUCTURE,
            not _background_rolls(again) and unchanged,
            "no roll_background ran on the second turn and the provenance and "
            "wealth are as the first turn left them",
            f"rolls: {[step.get('note') for step in _background_rolls(again)] or 'none'}; "
            f"before: {first.line if first else None}; "
            f"after: {second.line if second else None}",
        ),
        verdict(
            "it tells the player the background stands",
            CheckKind.RUBRIC,
            bool(named or stands),
            "the reply names what the record holds, or says it stays as rolled",
            f"named: {[hit.phrase for hit in named] or 'none'}; "
            f"stands: {[hit.phrase for hit in stands] or 'none'}",
        ),
        verdict(
            "the Game Master's turn rolled the background again",
            CheckKind.STRUCTURE,
            bool(_background_rolls(rerolled)),
            "a roll_background procedure that ran on the third turn",
            f"procedures: {[step.get('label') for step in rerolled.procedures] or 'none'}",
        ),
        verdict(
            "after the reroll the record still holds its origin's skill",
            CheckKind.STRUCTURE,
            third is not None and third.fully_recorded and third.holds_origin_skill,
            "a recorded background and a skill the new origin's grant may be held in",
            f"record: {third.line if third else 'no character'}; "
            f"skills: {third.skills if third else None}",
        ),
        verdict(
            "the Game Master's turn narrates no background the record never held",
            CheckKind.CONTRADICTION,
            not invented,
            "every origin, provenance and wealth the reply states is one the "
            "record held before or after the turn",
            f"invented: {invented or 'none'}; record: {third.line if third else None}",
        ),
        verdict(
            "it does not refuse, cap, bargain or lecture the Game Master",
            CheckKind.STRUCTURE,
            not refusals,
            "none of the refusal phrases, read whole-word",
            "; ".join(str(hit) for hit in refusals) or "no hits",
        ),
    ]


# --------------------------------------------------------------------------
# The Games Master batch of 2026-09-29: a Game Master's records, the skill
# check, a player's fight, the aide's dice words, one character per name.
# Every check reads the record the server kept, so a GM that corrects the
# request -- declines, asks, or records something narrower -- is judged by
# what it recorded, not by how it phrased the reply.
# --------------------------------------------------------------------------


def _session_character(transcript: Transcript, word: str) -> Any | None:
    """The session's character whose name holds ``word``, fresh from the rows."""
    return named_like(
        [entry.character for entry in characters_of(transcript.session_id)], word
    )


def _procedures_of(turn: TurnRecord, kind: str) -> list[dict]:
    """The frames of ``kind`` a turn's plan ran."""
    return [
        step for step in turn.procedures if step.get("kind") == kind and step.get("ran")
    ]


def check_game_master_records(transcript: Transcript) -> list[CheckOutcome]:
    """#802: what a Game Master says happened is recorded, and not argued with."""
    gorn = _session_character(transcript, "Gorn")
    purse_moved = gorn.coin_adjustment if gorn is not None else None
    said = transcript.replies
    refusals = find_words(said, REFUSAL_PHRASES, stems=True)
    return [
        check_no_stream_error(transcript),
        check_no_leaked_markers(transcript),
        check_plans_read(transcript),
        verdict(
            "the XP he awarded is on the record",
            CheckKind.STRUCTURE,
            gorn is not None and gorn.total_xp == 500,
            "Gorn's lifetime XP is the 500 he stated",
            f"XP: {gorn.total_xp if gorn else 'no Gorn'}; award_xp frames: "
            f"{[step.get('note') for step in transcript.all_procedures if step.get('kind') == 'award_xp'] or 'none'}",
        ),
        verdict(
            "the coin he gave is in the purse",
            CheckKind.STRUCTURE,
            purse_moved is not None and int(purse_moved) == 40,
            "the purse moved by the 40 gold he stated",
            f"coin given or taken: {purse_moved}; set_coin frames: "
            f"{[step.get('note') for step in transcript.all_procedures if step.get('kind') == 'set_coin'] or 'none'}",
        ),
        verdict(
            "the Fatigue roll made at his table is recorded",
            CheckKind.STRUCTURE,
            gorn is not None and gorn.fatigue_roll == 9,
            "Gorn's Fatigue roll is the 9 he stated",
            f"Fatigue roll: {gorn.fatigue_roll if gorn else 'no Gorn'}",
        ),
        verdict(
            "the death he stated is on the record",
            CheckKind.STRUCTURE,
            gorn is not None and not gorn.is_alive,
            "Gorn marked dead, with a cause",
            f"alive: {gorn.is_alive if gorn else 'no Gorn'}; cause: "
            f"{gorn.death_cause if gorn else ''!r}",
        ),
        verdict(
            "it does not refuse, cap, bargain or lecture the Game Master",
            CheckKind.STRUCTURE,
            not refusals,
            "none of the refusal phrases, read whole-word",
            "; ".join(str(hit) for hit in refusals) or "no hits",
        ),
    ]


def _check_line_total(line: str) -> int | None:
    """The total a skill check's line reports: "... level = 11 vs 12: ..."."""
    found = re.search(r"level = (-?\d+)", line)
    return int(found.group(1)) if found else None


def check_game_master_skill_check(transcript: Transcript) -> list[CheckOutcome]:
    """#843: a check asked for in play is rolled by the server, at his difficulty."""
    asked = transcript.turns[-1]
    checks = _procedures_of(asked, "skill_check")
    line = str(checks[0].get("note", "")) if checks else ""
    total = _check_line_total(line)
    outcome = (
        "success" if ": success" in line else "failure" if ": failure" in line else ""
    )
    reply = asked.reply.casefold()
    said_it = total is not None and (str(total) in reply or outcome[:4] in reply)
    return [
        check_no_stream_error(transcript),
        check_no_leaked_markers(transcript),
        check_plans_read(transcript),
        verdict(
            "the server rolled the skill check",
            CheckKind.STRUCTURE,
            bool(checks),
            "a skill_check frame that ran on the turn that asked for it",
            f"frames: {[step.get('label') for step in asked.procedures] or 'none'}",
        ),
        verdict(
            "at the difficulty the Game Master set",
            CheckKind.STRUCTURE,
            " vs 12" in line,
            "the check's line reads vs 12",
            f"line: {line or 'no check'}",
        ),
        # Reported, not required: the check's line reaches him as a chip
        # either way, and the first board found a right reply ("Gorn slips
        # past unnoticed") that no word list reads as a success.
        verdict(
            "the reply gives the rolled total or its outcome",
            CheckKind.REPORT,
            said_it,
            f"the total {total} or the word {outcome or '(none)'} in the reply",
            f"reply: {asked.reply[:200]!r}",
        ),
    ]


def check_player_skill_check(transcript: Transcript) -> list[CheckOutcome]:
    """#843: a player's check is rolled by the server with no difficulty of theirs."""
    asked = transcript.turns[-1]
    checks = _procedures_of(asked, "skill_check")
    lines = [str(step.get("note", "")) for step in checks]
    return [
        check_no_stream_error(transcript),
        check_no_leaked_markers(transcript),
        check_plans_read(transcript),
        verdict(
            "the server rolled the check the player asked for",
            CheckKind.STRUCTURE,
            bool(checks),
            "a skill_check frame that ran",
            f"frames: {[step.get('label') for step in asked.procedures] or 'none'}",
        ),
        verdict(
            "with no difficulty, attribute or dice a player may not set",
            CheckKind.STRUCTURE,
            bool(checks)
            and not any(
                " vs " in line
                or "named by a Game Master" in line
                or "stated by a Game Master" in line
                for line in lines
            ),
            "the line carries the dice, the modifier and the level only",
            f"lines: {lines or 'none'}",
        ),
    ]


def check_player_fight_foes(transcript: Transcript) -> list[CheckOutcome]:
    """#841: a player's /fight is a request; the Game Master sets the foes."""
    fight = transcript.turns[-1]
    session = GMSession.objects.filter(pk=transcript.session_id).first()
    battle = session.battle if session is not None else None
    beasts = (
        list(battle.combatants.exclude(beast=None).select_related("beast"))
        if battle is not None
        else []
    )
    # Since #894 a set_foes answer may name a person by calling: a generated
    # fighter with neither a beast nor a saved character behind it.
    people = (
        list(battle.combatants.filter(beast=None, character=None))
        if battle is not None
        else []
    )
    names = sorted(
        {combatant.beast.name for combatant in beasts}
        | {combatant.display_name for combatant in people}
    )
    from bestiary.generation import catalog_queryset

    catalog = set(catalog_queryset().values_list("pk", flat=True))
    return [
        verdict(
            "the fight started",
            CheckKind.STRUCTURE,
            battle is not None and not fight.error,
            "a battle on the session, and no refusal from /fight",
            f"error: {fight.error or 'none'}; battle: {battle.pk if battle else None}",
        ),
        verdict(
            "every foe is a bestiary catalog beast or a person by calling",
            CheckKind.STRUCTURE,
            bool(beasts or people)
            and all(combatant.beast_id in catalog for combatant in beasts),
            "no stand-in or generated beast on the Foes side",
            f"foes: {names or 'none'}",
        ),
        verdict(
            "the player is told the Game Master set the foes",
            CheckKind.STRUCTURE,
            "the Game Master set the foes" in fight.reply,
            "the opening line says who chose",
            f"line: {fight.reply[:200]!r}",
        ),
        verdict(
            "the foes are the story's wolves, not the dragon the player asked for",
            CheckKind.RUBRIC,
            any("wolf" in name.casefold() for name in names)
            and not any("dragon" in name.casefold() for name in names),
            "a wolf among the foes and no dragon",
            f"foes: {names or 'none'}",
        ),
    ]


def check_aide_dice_words(transcript: Transcript) -> list[CheckOutcome]:
    """#794: "rolling initiative" and "throw a die" ask the aide for dice."""
    asked = transcript.turns[-1]
    return [
        check_no_stream_error(transcript),
        check_no_leaked_markers(transcript),
        check_plans_read(transcript),
        verdict(
            "the server rolled what the Game Master asked for",
            CheckKind.STRUCTURE,
            bool(asked.gm_rolls),
            "a gm_roll frame on the turn",
            f"rolls: {[roll.get('line') for roll in asked.gm_rolls] or 'none'}; "
            f"refused: {[step.get('error') for step in asked.procedures if not step.get('ran')] or 'none'}",
        ),
    ]


def check_one_character_per_name(transcript: Transcript) -> list[CheckOutcome]:
    """#856: a name the session holds is never started a second time."""
    wrens = [
        entry.character
        for entry in characters_of(transcript.session_id)
        if entry.character.name.casefold().startswith("wren")
    ]
    return [
        check_no_stream_error(transcript),
        check_plans_read(transcript),
        verdict(
            "the session holds one Wren",
            CheckKind.STRUCTURE,
            len(wrens) == 1,
            "one character called Wren",
            f"characters: {[character.name for character in wrens] or 'none'}",
        ),
        verdict(
            "the years went to her",
            CheckKind.STRUCTURE,
            len(wrens) == 1 and wrens[0].experience_years > 0,
            "the one Wren has years of experience",
            f"years: {[character.experience_years for character in wrens]}",
        ),
    ]


# --------------------------------------------------------------------------
# A session fight, played to its end on the board, then asked about (#888).
# Nothing the model read about a battle -- the "Battle log" blocks, the
# closing summary, the fight's name built from its sides -- was measured by
# any board. These play the fight through the battle app's own services,
# as the e2e specs play it through its endpoints, and judge the answer
# against the battle record: the outcome, the deaths, the wounds, and any
# roll the reply claims.
# --------------------------------------------------------------------------

#: How many board turns a scenario's fight may take before it is called
#: unfinished (reported by the "fight ended" check, never waited out).
FIGHT_TURN_LIMIT = 60


def finish_the_session_fight(session_id: int) -> None:
    """Play the session's fight to its end, every seated choice the AI's.

    Through ``battle.services.advance_turn`` and ``record_player_action`` --
    the services the arena's Advance and Confirm buttons post to -- so the
    board files its log, writes wounds and deaths to the records, and ends
    the fight exactly as a played one does. Leaves the fight as it is after
    :data:`FIGHT_TURN_LIMIT` turns, for the checks to report.
    """
    from battle.models import Battle
    from battle.services import advance_turn, record_player_action
    from gm.battle_report import TERMINAL_STATUSES

    session = GMSession.objects.get(pk=session_id)
    if session.battle_id is None:
        return
    battle_id = session.battle_id
    for _turn in range(FIGHT_TURN_LIMIT):
        battle = Battle.objects.get(pk=battle_id)
        if battle.status in TERMINAL_STATUSES:
            return
        battle, _events = advance_turn(battle_id)
        while battle.status == Battle.STATUS_AWAITING:
            pending = battle.pending_input_json or {}
            seat = battle.combatants.get(pk=pending["combatant_id"])
            battle, _events = record_player_action(
                battle_id,
                seat.player or session.user,
                combatant_id=pending["combatant_id"],
                turn=pending["turn"],
                letter="",
                as_ai=True,
            )


@dataclass(frozen=True)
class FightFigure:
    """One figure of a finished fight, as the battle record holds it.

    Attributes:
        name: What the reply may call it: a character's name, or a beast's
            head noun ("wolf" for Gray Wolf), lower-cased.
        team: ``Party`` or ``Foes``.
        dead: Whether the record holds the figure dead.
        hurt: Whether it took any damage, from the character's record for a
            party figure and from the board's pools for a foe.
    """

    name: str
    team: str
    dead: bool
    hurt: bool


@dataclass(frozen=True)
class FightRecord:
    """What a finished session fight left on the record.

    Attributes:
        battle_id: The battle, or None when no fight started.
        ended: Whether the board ended it.
        winner_team: The team the engine named victorious, or "" (a
            stalemate, or no ending).
        figures: Every figure on it.
        log: Every line of the battle's log, joined, for provenance.
    """

    battle_id: int | None
    ended: bool
    winner_team: str
    figures: tuple[FightFigure, ...]
    log: str


def fight_record(session_id: int | None) -> FightRecord:
    """Read the session's fight off the battle and the characters' records."""
    from characters.models import Character
    from gm.battle_report import TERMINAL_STATUSES

    session = GMSession.objects.filter(pk=session_id).first()
    battle = session.battle if session is not None else None
    if battle is None:
        return FightRecord(None, False, "", (), "")
    verdict_event = (
        battle.events.filter(event_type="info")
        .filter(payload__has_any_keys=["winner", "winner_team", "stalemate"])
        .order_by("-sequence")
        .first()
    )
    winner = (
        str((verdict_event.payload or {}).get("winner_team") or "")
        if verdict_event
        else ""
    )
    board = {
        figure.get("combatant_id"): figure
        for figure in (battle.state_json or {}).get("combatants", [])
    }
    figures: list[FightFigure] = []
    for seat in battle.combatants.select_related("character").order_by("pk"):
        figure = board.get(seat.pk, {})
        if seat.character is not None:
            character = Character.objects.get(pk=seat.character.pk)
            figures.append(
                FightFigure(
                    name=character.name.casefold(),
                    team=seat.team,
                    dead=not character.is_alive,
                    hurt=bool(character.fatigue_damage or character.body_damage),
                )
            )
            continue
        head = re.sub(r"\s*\(\d+\)$", "", seat.display_name).split()
        figures.append(
            FightFigure(
                name=(head[-1] if head else seat.display_name).casefold(),
                team=seat.team,
                dead=not figure.get("alive", True),
                hurt=(figure.get("fatigue"), figure.get("body"))
                != (figure.get("max_fatigue"), figure.get("max_body")),
            )
        )
    log = "\n".join(
        battle.events.order_by("sequence").values_list("message", flat=True)
    )
    return FightRecord(
        battle.pk, battle.status in TERMINAL_STATUSES, winner, tuple(figures), log
    )


#: A figure's name, allowing "the", "gray" and the like before a beast's
#: head noun ("the gray wolf").
def _named(name: str) -> str:
    return rf"(?:the\s+(?:\w+\s+){{0,2}})?{re.escape(name)}(?:'s)?"


#: Words that turn what follows into something that did not happen.
_UNREAL = re.compile(
    r"\b(?:not|never|no|nearly|almost|narrowly|would|could|might|may|if|"
    r"unless|without|escap\w*|avoid\w*|surviv\w*|spared)\b|n't\b",
    re.IGNORECASE,
)
_DEATH_OF = (
    r"\b{name}\s+(?:\w+\s+){{0,2}}?(?:dies|died|perish(?:es|ed)|falls?\s+dead|"
    r"fell\s+dead|lies\s+dead|lay\s+dead|is\s+dead|was\s+dead|is\s+slain|"
    r"was\s+slain|was\s+killed|is\s+killed|breathes?\s+(?:its|his|her|their)\s+last)\b"
    r"|\b(?:killed|slew|slays|slaughtered)\s+{name}\b"
    r"|\b{name}\s+(?:\w+\s+){{0,1}}?(?:corpse|body)\s+(?:lies|lay)\b"
)
_WOUND_OF = (
    r"\b{name}\s+(?:\w+\s+){{0,2}}?(?:is|was|lies|stands|left)\s+(?:\w+\s+){{0,1}}?"
    r"(?:wounded|injured|bleeding|bloodied|hurt|gashed|bitten)\b"
    r"|\b{name}\s+(?:bleeds|bled|took\s+(?:a\s+)?(?:wound|wounds|hit|hits|damage))\b"
    r"|\b{name}\s+wounds?\b"
)
_VICTORY_OF = (
    r"\b{name}\s+(?:\w+\s+){{0,2}}?(?:won|wins|triumph(?:s|ed)|prevail(?:s|ed)|"
    r"stands?\s+victorious|(?:is|was)\s+victorious|emerges?\s+victorious|"
    r"emerged\s+victorious)\b"
)


def _claims(pattern: str, name: str, text: str) -> list[str]:
    """Sentences of ``text`` that state ``pattern`` of ``name``, unhedged."""
    compiled = re.compile(pattern.format(name=_named(name)), re.IGNORECASE)
    said: list[str] = []
    for sentence in _sentences(plain(text)):
        for match in compiled.finditer(sentence):
            clause_start = max(
                sentence.rfind(mark, 0, match.start()) for mark in (",", ";", ":")
            )
            before = sentence[clause_start + 1 : match.start()]
            if _UNREAL.search(before) or _UNREAL.search(match.group()):
                continue
            said.append(sentence.strip())
            break
    return said


def death_claims(text: str, name: str) -> list[str]:
    """Sentences saying ``name`` died, read as statements, not hedges."""
    return _claims(_DEATH_OF, name, text)


def wound_claims(text: str, name: str) -> list[str]:
    """Sentences saying ``name`` was wounded."""
    return _claims(_WOUND_OF, name, text)


def victory_claims(text: str, name: str) -> list[str]:
    """Sentences saying ``name`` won the fight."""
    return _claims(_VICTORY_OF, name, text)


#: A roll as the battle log writes it: "Gorn attack 1d20: [10] +6 = 16",
#: "Gorn damage 1d6-1: [2] -1 = 1", "3d6: [3, 3, 3] = 9".
_LOGGED_ROLL = re.compile(
    r"\b\d*d\d+(?:[+-]\d+)?:\s*\[(?P<faces>[\d,\s]+)\]"
    r"(?:\s*[+-]\s*\d+)*\s*=\s*(?P<total>\d+)"
)


def log_dice_numbers(log: str) -> set[str]:
    """The faces and totals the battle log's rolls came to, and nothing else.

    A TN, a hex count, a pool or a forecast's figures are numbers on the log
    but not dice that came up, so a reply saying one "was rolled" is not
    borne out by it (review of #888).
    """
    numbers: set[str] = set()
    for rolled in _LOGGED_ROLL.finditer(log):
        numbers.update(re.findall(r"\d+", rolled.group("faces")))
        numbers.add(rolled.group("total"))
    return numbers


def check_session_fight(transcript: Transcript) -> list[CheckOutcome]:
    """#888: the GM tells the finished fight the board played, and no other."""
    from gm.fight import FOE_TEAM, PARTY_TEAM
    from gm.retrieval import is_battle_message

    record = fight_record(transcript.session_id)
    asked = transcript.last
    session = GMSession.objects.filter(pk=transcript.session_id).first()
    history = session.history if session is not None else []
    last_user = max(
        (
            index
            for index, message in enumerate(history)
            if message.get("role") == "user"
            and not is_battle_message(message)
            and not message.get("kind")
        ),
        default=-1,
    )
    ending_filed = [
        index
        for index, message in enumerate(history)
        if is_battle_message(message)
        and message.get("battle_id") == record.battle_id
        and message.get("battle_ended")
    ]
    reply = asked.reply
    wrong_deaths = [
        f"{figure.name}: {sentence}"
        for figure in record.figures
        if not figure.dead
        for sentence in death_claims(reply, figure.name)
    ]
    wrong_wounds = [
        f"{figure.name}: {sentence}"
        for figure in record.figures
        if figure.team == PARTY_TEAM and not figure.hurt and not figure.dead
        for sentence in wound_claims(reply, figure.name)
    ]
    losers = [
        figure
        for figure in record.figures
        if record.winner_team and figure.team != record.winner_team
    ]
    wrong_winner = [
        f"{figure.name}: {sentence}"
        for figure in losers
        for sentence in victory_claims(reply, figure.name)
    ]
    log_numbers = log_dice_numbers(record.log)
    unfounded_rolls = [
        f"{figure} ({snippet})"
        for figure, snippet in rolled_figures(reply)
        if str(figure) not in log_numbers
    ]
    party = [figure.name for figure in record.figures if figure.team == PARTY_TEAM]
    foes = [figure.name for figure in record.figures if figure.team == FOE_TEAM]
    return [
        check_no_stream_error(transcript),
        check_no_leaked_markers(transcript),
        check_plans_read(transcript),
        verdict(
            "the fight started on the board",
            CheckKind.STRUCTURE,
            record.battle_id is not None,
            "a battle on the session after /fight",
            "; ".join(turn.error for turn in transcript.turns if turn.error)
            or f"battle: {record.battle_id}",
        ),
        verdict(
            "the board played the fight to its end",
            CheckKind.STRUCTURE,
            record.ended,
            f"a finished or stalemated battle within {FIGHT_TURN_LIMIT} turns",
            f"ended: {record.ended}; winner: {record.winner_team or 'none'}; "
            f"party {party}, foes {foes}",
        ),
        verdict(
            "the ending was filed to the model before the question",
            CheckKind.STRUCTURE,
            bool(ending_filed) and ending_filed[-1] < last_user,
            "a hidden closing summary ahead of the last message",
            f"summary at {ending_filed or 'nowhere'}, question at {last_user}",
        ),
        verdict(
            "the turn after the fight rolled no dice of its own",
            CheckKind.STRUCTURE,
            not asked.gm_rolls,
            "no gm_roll frame: the board already rolled the fight",
            f"rolls: {[roll.get('line') for roll in asked.gm_rolls] or 'none'}",
        ),
        verdict(
            "the outcome the GM states matches the battle record",
            CheckKind.CONTRADICTION,
            not wrong_winner,
            f"no side but {record.winner_team or 'none (no winner)'} said to win",
            "; ".join(wrong_winner) or "no contrary winner claimed",
        ),
        verdict(
            "no death stated that the record does not hold",
            CheckKind.CONTRADICTION,
            not wrong_deaths,
            "a figure alive on the record is never said to die",
            "; ".join(wrong_deaths) or "no unrecorded death stated",
        ),
        verdict(
            "no wound stated that the record does not hold",
            CheckKind.CONTRADICTION,
            not wrong_wounds,
            "an unhurt party character is never said to be wounded",
            "; ".join(wrong_wounds) or "no unrecorded wound stated",
        ),
        verdict(
            "every roll the reply claims is on the battle log",
            CheckKind.PROVENANCE,
            not unfounded_rolls,
            "no 'rolled a N' whose N the board did not roll",
            "; ".join(unfounded_rolls) or "no roll claimed off the log",
        ),
    ]


@dataclass(frozen=True)
class Scenario:
    """One conversation and the assertions made about it.

    Attributes:
        key: What ``--only`` matches on.
        title: One line describing what it is for.
        issues: The bugs it exists to keep from coming back.
        persona: Which account holds the conversation.
        turns: The messages to send, in order.
        check: Builds the run's outcomes from its transcript.
    """

    key: str
    title: str
    issues: tuple[str, ...]
    persona: Persona
    turns: tuple[Turn, ...]
    check: Callable[[Transcript], list[CheckOutcome]]
    #: The background parts its messages state ("location", "provenance",
    #: "wealth"), which a plan records with set_background (issue #754); a
    #: set_background of any other part is a background nobody stated.
    states_background: tuple[str, ...] = ()


def build_scenarios() -> tuple[Scenario, ...]:
    """Every scenario, with the code-derived expectations bound into its checks.

    The oracles are read once, here, so a scenario's checks and the question
    the scenario asks are built from the same observation of the code.

    Returns:
        The scenarios, in the order they run.
    """
    rule = survival_rule_from_code()
    matrix = matrix_question_from_code()
    take_years = Turn(f"Wren takes five years on the {rule.track} track.")
    # The ledger scenario is about arithmetic, not about dying, so it plays
    # the kindest track the rules define rather than the one #370 was filed
    # against. Which track that is comes from the rules, not from this file.
    safest_track = min(skills.SURVIVAL_TARGETS, key=skills.SURVIVAL_TARGETS.__getitem__)
    take_safe_years = Turn(f"Wren takes five years on the {safest_track} track.")
    return (
        Scenario(
            key="thing_changes_hands",
            title="Whether a thing somebody is holding moves when it is taken",
            issues=("#615",),
            persona=GAME_MASTER_PLAYING,
            turns=(
                Turn(
                    "In the ruins near the Pillar, a hooded figure faces us, "
                    "holding a twisted staff that hums with sickly light. "
                    "Set the scene."
                ),
                Turn(
                    "I strike the figure's wrist hard. The staff falls from "
                    "their hand and clatters to the stone."
                ),
                Turn("I pick up the staff and hold it. What does the figure do?"),
            ),
            check=check_thing_changes_hands,
        ),
        Scenario(
            key="scene_objects",
            title="Whether a thing in play keeps the state it was given",
            issues=("#579",),
            persona=GAME_MASTER_PLAYING,
            turns=(
                Turn(
                    "We are at the bottom of a well beneath Farport. On a "
                    "stone ledge above the water there is a small iron box, "
                    "resting loose -- nothing holds it down. Set the scene."
                ),
                Turn("I listen for a moment. What do I hear?"),
                Turn("I reach out and try to lift the box off the ledge."),
            ),
            check=check_scene_objects,
        ),
        Scenario(
            key="people_and_threads",
            title="Whether who they met and what is hanging survive the turn",
            issues=("#579",),
            persona=GAME_MASTER_PLAYING,
            turns=(
                Turn(
                    "At the shrine in Farport we meet Mother Vane, an old "
                    "oracle. She wants the iron box in the well sealed before "
                    "the tide turns, and she does not trust us. She warns us "
                    "the Sleeper below the water must not wake."
                ),
                Turn("We leave the shrine and walk down to the harbour."),
                Turn("Who have we met so far, and what is still hanging over us?"),
            ),
            check=check_people_and_threads,
        ),
        Scenario(
            key="clock_and_place",
            title="Whether the party's place and the clock are records",
            issues=("#579",),
            persona=GAME_MASTER_PLAYING,
            turns=(
                Turn(
                    "We ride out of Farport and make camp on the road. Set "
                    "the scene -- we are a day out from the town."
                ),
                Turn("We sleep through the night and set off again at dawn."),
            ),
            check=check_clock,
        ),
        Scenario(
            key="npc_consistency",
            title="Whether an NPC's attitude changes only when something changes it",
            issues=("#579",),
            persona=GAME_MASTER_PLAYING,
            turns=(
                Turn(
                    "At the shrine in Farport we meet Mother Vane, an old "
                    "oracle. She does not trust us, and she says so plainly."
                ),
                Turn(
                    "We buy a sack of salt-fish down at the harbour, carry it "
                    "back up to the shrine and leave it at her door."
                ),
                Turn("What does Mother Vane make of us now?"),
            ),
            check=check_npc_consistency,
        ),
        Scenario(
            key="thread_followup",
            title="Whether a promise kept is followed up on the record",
            issues=("#579",),
            persona=GAME_MASTER_PLAYING,
            turns=(
                Turn(
                    "At the shrine in Farport, Mother Vane makes us swear to "
                    "seal the iron box in the well before the tide turns. We "
                    "swear it."
                ),
                Turn(
                    "We climb down the well, wedge the lid shut and hammer "
                    "the seal home. The box is sealed."
                ),
                Turn("Is there anything still hanging over us from the shrine?"),
            ),
            check=check_thread_followup,
        ),
        Scenario(
            key="party_supplies",
            title="Whether what the party carries is a row each, spent one at a time",
            issues=("#579",),
            persona=GAME_MASTER_PLAYING,
            turns=(
                Turn(
                    "Before we go down the well, write down what we carry "
                    "between us: three torches and a fifty-foot coil of rope."
                ),
                Turn(
                    "We burn two of the three torches waiting out the tide. "
                    "One torch left."
                ),
                Turn("What are we carrying now?"),
            ),
            check=check_party_supplies,
        ),
        Scenario(
            key="long_session_scene",
            title="Whether a scene survives a session long enough to fold",
            issues=("#579",),
            persona=GAME_MASTER_PLAYING,
            turns=(
                Turn(
                    "We are at the bottom of a well beneath Farport. On a "
                    "stone ledge above the water there is a small iron box, "
                    "resting loose -- nothing holds it down. Set the scene."
                ),
                Turn(
                    "Before I touch it: where is the iron box, and what state "
                    "is it in?",
                    before=pad_session_for_a_fold,
                ),
            ),
            check=check_long_session_scene,
        ),
        Scenario(
            key="literacy_player",
            title="Whether a player's literacy is rolled by the server, once",
            issues=("#742",),
            persona=PLAYER,
            turns=(
                Turn(
                    "Roll up a new character called Wren for me: roll her "
                    "six attributes, then roll her literacy check."
                ),
                Turn("That literacy roll was unlucky. Roll Wren's literacy again."),
            ),
            check=check_literacy_player,
        ),
        Scenario(
            key="literacy_game_master",
            title="Whether a Game Master's Origin change rolls literacy again",
            issues=("#742", "#741"),
            persona=GAME_MASTER_PLAYING,
            turns=(
                Turn(
                    "Make a character called Gorn: roll his six attributes "
                    "and his literacy check. He grew up in a village."
                ),
                Turn("Gorn actually grew up in the city. Change his Origin to City."),
            ),
            check=check_literacy_game_master,
            states_background=("location",),
        ),
        Scenario(
            key="chat_built_background",
            title="Whether a character built in chat has its background rolled, once",
            issues=("#754", "#749"),
            persona=PLAYER,
            turns=(
                Turn(
                    "Roll up a new character called Wren for me: roll her six "
                    "attributes."
                ),
                Turn(
                    "I don't like Wren's background. Roll her origin, "
                    "provenance and wealth again."
                ),
                Turn(
                    "Reroll Wren's whole background: her origin, provenance "
                    "and wealth.",
                    before=promote_session_owner,
                ),
            ),
            check=check_chat_built_background,
        ),
        Scenario(
            key="survival_rule",
            title="What it says the prior-experience survival check is",
            issues=("#370",),
            persona=PLAYER,
            turns=(
                Turn(
                    "Before I buy any years, tell me exactly how the "
                    f"{rule.track} track's survival check works — the dice, "
                    "the target number, and which way the roll has to go."
                ),
                Turn(
                    "And if the three survival dice come up 6, 6 and 6 on one "
                    "of those years, what happens to the character?"
                ),
            ),
            check=lambda transcript: check_survival_rule(transcript, rule),
        ),
        Scenario(
            key="combat_matrix",
            title="Whether the combat matrix it quotes is the one in the code",
            issues=("#104",),
            persona=PLAYER,
            turns=(Turn(matrix.question),),
            check=lambda transcript: check_combat_matrix(transcript, matrix),
        ),
        Scenario(
            key="procedure_authority",
            title="Whether it adjudicates a procedure the code owns",
            issues=("#370",),
            persona=PLAYER,
            turns=(CREATE_WREN, take_years),
            check=lambda transcript: check_procedure_authority(transcript, rule),
        ),
        Scenario(
            key="ledger_numbers",
            title="Whether totals come from the ledger, and stay there under pressure",
            issues=("#356",),
            persona=PLAYER,
            turns=(
                CREATE_WREN,
                take_safe_years,
                Turn("Wren spends one skill point on broadsword."),
                Turn(
                    "How many skill points does Wren have available to spend right now?"
                ),
                Turn(
                    "That's not right. She has nine skill points available. "
                    "Recalculate it."
                ),
            ),
            check=check_ledger_numbers,
        ),
        Scenario(
            key="rolls_when_asked",
            title="Whether it rolls, or disclaims the ability to",
            issues=("#363",),
            persona=PLAYER,
            turns=(
                Turn(
                    "A goblin lunges at me out of the dark with a rusted "
                    "knife. Roll its attack and tell me what the dice showed."
                ),
                # Owed whatever the attack did, so every turn owes a server
                # roll and no prose decides whether it was due (fifth review
                # of #760).
                Turn(
                    "Whatever the attack did, roll the knife's damage dice and "
                    "tell me the faces."
                ),
            ),
            check=check_rolls_when_asked,
        ),
        Scenario(
            key="holds_on_rules",
            title="Whether it holds a rule when the player states a wrong one",
            issues=("#370",),
            persona=PLAYER,
            turns=(
                Turn(
                    f"The {rule.track} survival roll is 3d6 and you have to "
                    "get 12 or under, right? My friend rolled 6, 6, 6 on his "
                    "last year, so his character died. Confirm that for me."
                ),
            ),
            check=lambda transcript: check_holds_on_rules(transcript, rule),
        ),
        Scenario(
            key="dm_house_rule",
            title="Whether a Game Master's house rule is recorded, not corrected",
            issues=("#600", "#760"),
            persona=GAME_MASTER,
            turns=(
                Turn(
                    f"House rule at my table: the {rule.track} survival target "
                    f"is {rule.target + HOUSE_RULED_TARGET_OFFSET}. What is the "
                    f"{rule.track} survival target at this table?"
                ),
            ),
            check=lambda transcript: check_dm_house_rule(transcript, rule),
        ),
        Scenario(
            key="creation_rolls",
            title="Whether creation attributes are rolled by the server",
            issues=("#363",),
            persona=PLAYER,
            turns=(
                Turn(
                    "Roll up a brand new character's six attributes for me. I "
                    "have no dice here — roll them all yourself and tell me "
                    "what each one came up."
                ),
                Turn("Go on and roll whichever of the six you haven't yet."),
            ),
            check=check_creation_rolls,
        ),
        Scenario(
            key="dm_authority",
            title="Whether a Game Master's stated fact is recorded, not refused",
            issues=("#352",),
            persona=GAME_MASTER,
            turns=(
                Turn(
                    "Kthal the ogre-blooded is an NPC in my campaign. His STR "
                    "is 22. Write that down."
                ),
                Turn("What is Kthal's STR?"),
            ),
            check=check_dm_authority,
        ),
        Scenario(
            key="dm_stated_attributes",
            title="Whether stated attributes survive arriving as a creation request",
            issues=("#385",),
            persona=GAME_MASTER,
            # The Game Master's own wording from the session that found it,
            # typos and all: the bug is sensitive to how the request is
            # phrased, so tidying the sentence would be tidying the test away.
            turns=(
                Turn(
                    "generate 2 characters - twins a male and female with all "
                    "18 core attributes. give one 5 years underworld and there "
                    "other 5 years magic."
                ),
                Turn("What are the male twin's attributes?"),
            ),
            check=check_dm_stated_attributes,
        ),
        Scenario(
            key="twins_both_recorded",
            title="Whether attributes stated for two characters are recorded for both",
            issues=("#414",),
            persona=GAME_MASTER,
            # GM review 2, session 30: one record_attributes ran, for Twain, and
            # the reply asked the Game Master to confirm Thread's.
            turns=(
                Turn(
                    "I am making twins called Twain and Thread. give them each "
                    "all 18 core attributes."
                ),
            ),
            check=check_twins_both_recorded,
        ),
        Scenario(
            key="twins",
            title="Whether twins asked for together are built as two characters",
            issues=("#390", "#391"),
            persona=GAME_MASTER,
            # The Game Master's own words from session 28, typos included.
            turns=(
                Turn(
                    "great, please create two characters, a brother and a sister, "
                    "who are twins. Make their core attributed be 18s. Give one 5 "
                    "years of underworld prior experience and one 5 years of magic "
                    "prior experience - thanks then we can play"
                ),
                Turn(
                    "the brother is called Twain the sister is called Sister. Give "
                    "them both 5 years has campaigners"
                ),
            ),
            check=check_twins,
        ),
        Scenario(
            key="aide_long_turn",
            title="Whether the aide stays the aide through a greeting and a long creation turn",
            issues=("#392",),
            persona=GAME_MASTER,
            # Session 28's opening two messages, in its own words.
            turns=(
                Turn("hello"),
                Turn(
                    "great, please create two characters, a brother and a sister, "
                    "who are twins. Make their core attributed be 18s. Give one 5 "
                    "years of underworld prior experience and one 5 years of magic "
                    "prior experience - thanks then we can play"
                ),
            ),
            check=check_aide_long_turn,
        ),
        Scenario(
            key="player_budget",
            title="Whether a player asking for the same number is told otherwise",
            issues=("#352",),
            persona=PLAYER,
            turns=(Turn("My character's STR is 22. Write that down on the sheet."),),
            check=check_player_budget,
        ),
        Scenario(
            key="aide_manner",
            title="Whether it answers the Game Master and stops",
            issues=("#353", "#372"),
            persona=GAME_MASTER,
            turns=(
                Turn("How much does a longsword cost?"),
                Turn(
                    "Last night my players killed the innkeeper at the Broken "
                    "Wheel. Did the town guard see them do it?"
                ),
                Turn(
                    "At my table a natural 20 just crits — no confirmation "
                    "roll. A player has rolled a natural 20 with a sword. "
                    "What damage does he take?"
                ),
            ),
            check=check_aide_manner,
        ),
        Scenario(
            key="play_manner",
            title="Whether a Game Master who asked to play gets a game run for him",
            issues=("#342", "#372"),
            # A DM in play mode: the case #372 exists for. Before it, this
            # scenario had to run as a player, because a DM could not reach
            # play mode at all -- and the checks below are exactly the ones
            # the aide prompt fails, so running them against the account that
            # used to be stuck with the aide is what proves the fix.
            persona=GAME_MASTER_PLAYING,
            turns=(
                Turn(
                    "I'm playing Wren, a hedge-witch with a walking staff. She "
                    "pushes open the tavern door and steps in out of the rain."
                ),
                Turn("I want to find out who runs this place. What are my options?"),
            ),
            check=check_play_manner,
        ),
        Scenario(
            key="game_master_records",
            title="Whether a death, XP, coin and a Fatigue roll he states are recorded",
            issues=("#802", "#859"),
            persona=GAME_MASTER,
            turns=(
                Turn("Make a character called Gorn with 14 in every attribute."),
                Turn("Gorn earned 500 XP tonight, and the baron paid him 40 gold."),
                Turn("At my table Gorn's Fatigue roll came up a 9."),
                Turn("Gorn fell from the tower and died."),
            ),
            check=check_game_master_records,
        ),
        Scenario(
            key="game_master_skill_check",
            title="Whether a skill check he asks for is rolled by the server",
            issues=("#843",),
            persona=GAME_MASTER,
            turns=(
                Turn(
                    "Make a character called Gorn with 14 in every attribute and "
                    "3 points in Stealth."
                ),
                Turn(
                    "Gorn tries to slip past the gate guard. Roll his Stealth "
                    "check against a difficulty of 12."
                ),
            ),
            check=check_game_master_skill_check,
        ),
        Scenario(
            key="player_skill_check",
            title="Whether a player's skill check is rolled with nothing of theirs set",
            issues=("#843",),
            persona=PLAYER,
            turns=(
                Turn("I want to make a character called Wren. Roll her attributes."),
                Turn(
                    "Wren tries to sneak past the sleeping guard. Roll her "
                    "Stealth check."
                ),
            ),
            check=check_player_skill_check,
        ),
        Scenario(
            key="player_fight_foes",
            title="Whether a player's /fight is turned into the story's foes",
            issues=("#841",),
            persona=PLAYER,
            turns=(
                Turn("I want to make a character called Wren. Roll her attributes."),
                Turn(
                    "Wren walks the forest road at dusk. Three gray wolves slip "
                    "out of the trees and circle her, snarling."
                ),
                Turn("an ancient red dragon", command=FIGHT_COMMAND),
            ),
            check=check_player_fight_foes,
        ),
        Scenario(
            key="aide_dice_words",
            title="Whether 'rolling initiative' and 'throw a die' ask for dice",
            issues=("#794",),
            persona=GAME_MASTER,
            turns=(
                Turn(
                    "Rolling initiative for the three goblins: throw a die for "
                    "each of them."
                ),
            ),
            check=check_aide_dice_words,
        ),
        Scenario(
            key="player_session_fight",
            title="Whether a player's GM tells the fight the board played",
            issues=("#888",),
            persona=PLAYER,
            turns=(
                Turn("I want to make a character called Wren. Roll her attributes."),
                Turn(
                    "Wren walks the forest road at dusk. A lone gray wolf slips "
                    "out of the trees and circles her, snarling."
                ),
                Turn("a gray wolf", command=FIGHT_COMMAND),
                Turn(
                    "The fight on the board is over. How did it end for Wren?",
                    before=finish_the_session_fight,
                ),
            ),
            check=check_session_fight,
        ),
        Scenario(
            key="game_master_session_fight",
            title="Whether the aide reports the fight the board played",
            issues=("#888",),
            persona=GAME_MASTER,
            turns=(
                Turn("Make a character called Gorn with 14 in every attribute."),
                Turn("a gray wolf", command=FIGHT_COMMAND),
                Turn(
                    "The fight on the board is over. How did it end, and what "
                    "state is Gorn in?",
                    before=finish_the_session_fight,
                ),
            ),
            check=check_session_fight,
        ),
        Scenario(
            key="one_character_per_name",
            title="Whether a second turn about Wren plays her, not a copy",
            issues=("#856",),
            persona=PLAYER,
            turns=(
                Turn(
                    "I want to make a character. Her name is Wren. Roll her attributes."
                ),
                take_years,
            ),
            check=check_one_character_per_name,
        ),
    )


# --------------------------------------------------------------------------
# Running and reporting.
# --------------------------------------------------------------------------


@dataclass
class CheckTally:
    """One check's results across every run of its scenario.

    Attributes:
        name: What was checked.
        kind: One of :class:`CheckKind`'s values.
        outcomes: The verdict from each run, in run order.
    """

    name: str
    kind: str
    outcomes: list[CheckOutcome] = field(default_factory=list)

    @property
    def passes(self) -> int:
        """How many runs it held in."""
        return sum(1 for outcome in self.outcomes if outcome.passed)

    @property
    def runs(self) -> int:
        """How many runs it was measured over."""
        return len(self.outcomes)

    @property
    def pass_rate(self) -> float:
        """The share of runs it held in."""
        return self.passes / self.runs if self.runs else 0.0

    @property
    def required_rate(self) -> float:
        """The share its kind requires."""
        return REQUIRED_PASS_RATE[self.kind]

    @property
    def met(self) -> bool:
        """Whether the observed rate reached the required one."""
        return self.pass_rate >= self.required_rate

    @property
    def reports_only(self) -> bool:
        """Whether this check is printed for what it shows and never gates."""
        return self.kind == CheckKind.REPORT

    @property
    def flaky(self) -> bool:
        """True when it neither always passed nor always failed."""
        return 0 < self.passes < self.runs

    @property
    def flake_rate(self) -> float:
        """The share of runs it failed in."""
        return 1.0 - self.pass_rate

    @property
    def first_failure(self) -> CheckOutcome | None:
        """A failing run's outcome, for the report."""
        return next((o for o in self.outcomes if not o.passed), None)


@dataclass
class ScenarioResult:
    """One scenario's runs and the checks tallied across them.

    Attributes:
        scenario: What was run.
        transcripts: One per run.
        tallies: One per check, in the order the checks were built.
    """

    scenario: Scenario
    transcripts: list[Transcript] = field(default_factory=list)
    tallies: list[CheckTally] = field(default_factory=list)

    @property
    def met(self) -> bool:
        """Whether every check reached its required pass rate.

        A report-only check is printed and never counted here.
        """
        return all(tally.met for tally in self.tallies if not tally.reports_only)

    @property
    def seconds(self) -> float:
        """Wall-clock time spent talking to the model."""
        return sum(turn.seconds for t in self.transcripts for turn in t.turns)


def run_scenario(scenario: Scenario, runs: int, echo: bool) -> ScenarioResult:
    """Run one scenario ``runs`` times and tally each check across them.

    Args:
        scenario: What to run.
        runs: How many times, so a flake rate can be reported.
        echo: Print each reply as it arrives.

    Returns:
        The scenario's result.
    """
    result = ScenarioResult(scenario=scenario)
    tallies: dict[str, CheckTally] = {}
    for run_index in range(1, runs + 1):
        print(f"  run {run_index}/{runs} …", end="", flush=True)
        transcript = run_conversation(scenario.persona, scenario.turns)
        result.transcripts.append(transcript)
        outcomes = [
            *scenario.check(transcript),
            check_plans_read(transcript),
            *check_no_unstated_background(scenario, transcript),
        ]
        for outcome in outcomes:
            tally = tallies.get(outcome.name)
            if tally is None:
                tally = CheckTally(name=outcome.name, kind=outcome.kind)
                tallies[outcome.name] = tally
                result.tallies.append(tally)
            tally.outcomes.append(outcome)
        failed = sum(1 for outcome in outcomes if not outcome.passed)
        print(f" {len(outcomes) - failed}/{len(outcomes)} checks held")
        if echo:
            _print_transcript(transcript)
    return result


def _print_transcript(transcript: Transcript) -> None:
    """Print a whole conversation, turn by turn, with the server's frames."""
    for index, turn in enumerate(transcript.turns, start=1):
        print(f"\n    ── turn {index} ({turn.seconds:.1f}s) ──")
        print(f"    SENT: {turn.message}")
        if turn.error:
            print(f"    STREAM ERROR: {turn.error}")
        for rejection in turn.plan_rejections:
            print(f"    [plan rejected] {'; '.join(rejection)}")
        for plan in turn.plans:
            print(f"    [plan] {json.dumps(list(plan), ensure_ascii=False)}")
        if turn.statuses:
            print(f"    [status] {' → '.join(dict.fromkeys(turn.statuses))}")
        for roll in turn.gm_rolls:
            print(f"    [server roll] {roll.get('line', roll)}")
        for step in turn.procedures:
            for line in step.get("lines", []):
                print(f"    [server procedure] {line}")
        # The ledger as it stood after the turn, sampled by run_conversation.
        # The done frame's "ledger" key this used to print went away with the
        # turn engine, so the line never printed (issue #784).
        if turn.ledger is not None:
            print(
                f"    [ledger] alive={turn.ledger.is_alive}, "
                f"years={turn.ledger.experience_years}, "
                f"free skill points={turn.ledger.skill_points.remaining}"
            )
        body = turn.reply.strip() or "(empty reply)"
        for line in body.splitlines():
            print(f"    GM: {line}")


def _print_failure(
    scenario: Scenario, tally: CheckTally, result: ScenarioResult
) -> None:
    """Print everything needed to act on a failure without re-running it."""
    outcome = tally.first_failure
    if outcome is None:
        return
    run_index = tally.outcomes.index(outcome)
    transcript = result.transcripts[run_index]
    print(f"\n{'─' * 78}")
    if tally.reports_only:
        print(f"Reported  {scenario.key} · {tally.name} (not counted)")
        print(f"        held in {tally.passes}/{tally.runs} runs")
    else:
        print(f"FAILED  {scenario.key} · {tally.name}")
        print(
            f"        kind {tally.kind}, held in {tally.passes}/{tally.runs} runs "
            f"(needs {tally.required_rate:.0%})"
        )
    print(f"        issues {' '.join(scenario.issues)}, run {run_index + 1}")
    print(f"  EXPECTED: {outcome.expectation}")
    print(f"  FOUND:    {outcome.evidence}")
    _print_transcript(transcript)
    print(f"{'─' * 78}")


def report(results: Sequence[ScenarioResult], runs: int) -> bool:
    """Print the run's report and say whether everything met its threshold.

    Args:
        results: Every scenario's result.
        runs: How many times each ran.

    Returns:
        True when every check reached its required pass rate.
    """
    print(f"\n{'=' * 78}\nRESULTS — {runs} run(s) per scenario\n{'=' * 78}")
    flaky: list[tuple[str, CheckTally]] = []
    for result in results:
        mark = "PASS" if result.met else "FAIL"
        print(
            f"\n{mark}  {result.scenario.key} — {result.scenario.title} "
            f"({' '.join(result.scenario.issues)}, {result.scenario.persona.key}, "
            f"{result.seconds:.0f}s)"
        )
        for tally in result.tallies:
            status = "rep" if tally.reports_only else ("ok " if tally.met else "FAIL")
            print(
                f"      {status} [{tally.kind:<13}] {tally.passes}/{tally.runs} "
                f"— {tally.name}"
            )
            # A report-only check's variation is shown with the reported
            # ones, not as a flake rate of the board (seventh review).
            if tally.flaky and not tally.reports_only:
                flaky.append((result.scenario.key, tally))
    print(f"\n{'=' * 78}\nFLAKE RATES\n{'=' * 78}")
    if flaky:
        for key, tally in flaky:
            print(
                f"  {tally.flake_rate:>5.0%} flaky  {key} · {tally.name} "
                f"({tally.passes}/{tally.runs} held, kind {tally.kind})"
            )
    else:
        print("  no check gave different answers on different runs")
    failures = [
        (result, tally)
        for result in results
        for tally in result.tallies
        if not tally.reports_only and not tally.met
    ]
    if failures:
        print(f"\n{'=' * 78}\nFAILURES IN DETAIL\n{'=' * 78}")
        for result, tally in failures:
            _print_failure(result.scenario, tally, result)
    reported = [
        (result, tally)
        for result in results
        for tally in result.tallies
        if tally.reports_only and tally.first_failure is not None
    ]
    if reported:
        print(f"\n{'=' * 78}\nREPORTED, NOT COUNTED\n{'=' * 78}")
        for result, tally in reported:
            _print_failure(result.scenario, tally, result)
    total_checks = sum(
        1 for result in results for tally in result.tallies if not tally.reports_only
    )
    print(f"\n{'=' * 78}")
    print(
        f"{total_checks - len(failures)}/{total_checks} checks met their "
        f"required pass rate across {runs} run(s)."
    )
    print(f"{'=' * 78}")
    return not failures


def ollama_status() -> tuple[bool, str]:
    """Whether the live model this harness needs is actually there.

    Returns:
        ``(reachable, explanation)``. Unreachable is a skip, not a failure:
        this harness is run on demand against a machine with the model loaded,
        and must be harmless everywhere else.
    """
    try:
        models = ollama_client.list_models(base_url=settings.OLLAMA_BASE_URL)
    except (httpx.HTTPError, OSError) as error:
        return False, f"{settings.OLLAMA_BASE_URL} is not answering: {error}"
    if settings.GM_MODEL not in models:
        return False, (
            f"{settings.OLLAMA_BASE_URL} is up but does not hold "
            f"{settings.GM_MODEL} (it has: {', '.join(models) or 'nothing'})"
        )
    return True, f"{settings.GM_MODEL} at {settings.OLLAMA_BASE_URL}"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the harness.

    Args:
        argv: Command-line arguments, or None for ``sys.argv``.

    Returns:
        0 when every check met its threshold or Ollama was absent, else 1.
    """
    scenarios = build_scenarios()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="how many times to run each scenario (default 3, for a flake rate)",
    )
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated scenario keys to run (default: all)",
    )
    parser.add_argument(
        "--list", action="store_true", help="list the scenarios and exit"
    )
    parser.add_argument(
        "--echo",
        action="store_true",
        help="print every conversation, not only the failing ones",
    )
    args = parser.parse_args(argv)

    if args.list:
        for scenario in scenarios:
            print(
                f"{scenario.key:<22} [{scenario.persona.key:<10}] "
                f"{' '.join(scenario.issues):<12} {scenario.title}"
            )
        return 0

    wanted = [key.strip() for key in args.only.split(",") if key.strip()]
    unknown = [key for key in wanted if key not in {s.key for s in scenarios}]
    if unknown:
        parser.error(f"unknown scenario(s): {', '.join(unknown)}")
    chosen = [s for s in scenarios if not wanted or s.key in wanted]

    rule = survival_rule_from_code()
    reachable, detail = ollama_status()
    print("=" * 78)
    print("GM BEHAVIOUR CHECK — driving the live Game Master (issue #373)")
    print("=" * 78)
    print(f"model      {detail}")
    print(f"started    {datetime.now(UTC).isoformat(timespec='seconds')}")
    print(f"scenarios  {len(chosen)} × {args.runs} run(s)")
    print(
        f"rules read from code: {rule.track} survival target {rule.target}, "
        f"roll goes {'HIGH' if rule.rolls_high else 'LOW'}, natural "
        f"{rule.auto_survive_roll} survives, natural {rule.auto_death_roll} dies"
    )
    if not reachable:
        print("\n" + "=" * 78)
        print("SKIPPED — nothing was checked.")
        print(f"  {detail}")
        print("  This harness only ever runs on demand, against a live model.")
        print("=" * 78)
        return 0

    results: list[ScenarioResult] = []
    for index, scenario in enumerate(chosen, start=1):
        print(
            f"\n[{index}/{len(chosen)}] {scenario.key} — {scenario.title} "
            f"({' '.join(scenario.issues)}, as {scenario.persona.key})"
        )
        results.append(run_scenario(scenario, args.runs, args.echo))
    return 0 if report(results, args.runs) else 1


if __name__ == "__main__":
    sys.exit(main())
