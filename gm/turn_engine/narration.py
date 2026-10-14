"""Keep a plan the model wrote during narration off the player's screen (#603).

The narrate call is asked for prose. Sometimes -- most often when its plan was
rejected and it is trying again in the only channel it has left -- the model
ends its reply with the plan it wanted to run:

    To begin, I will roll the six core attributes for Wren.

    {"actions": [{"action": "roll_attributes", "new_character": {"name": "Wren"}}]}

Two things are wrong there, and this module fixes only the first: the player
should not see the machinery. The second -- that the actions in it never run,
so the turn quietly does nothing -- belongs to the engine, which reads
:attr:`PlanLeakFilter.leaked` and re-plans instead of narrating a dead end.

Filtering has to happen token by token, before the tokens go out over SSE.
By the time a whole reply could be scanned the player has already read it.
"""

import re
from bisect import bisect_right
from collections.abc import Iterable, Sequence

from .state import (
    AGE_TOPIC,
    BODY_TOPIC,
    EXPERIENCE_POINTS_TOPIC,
    EXPERIENCE_YEARS_TOPIC,
    FATIGUE_TOPIC,
    LEVEL_TOPIC,
    SKILL_POINTS_TOPIC,
    LedgerFigure,
    RosterLine,
)

#: A JSON object that opens with an ``actions`` key, however it is spaced.
PLAN_OPENING = re.compile(r'\{\s*"actions"')

#: How much text after a ``{`` is enough to tell a plan from ordinary prose.
#: ``len('{  "actions"')`` with room for the whitespace a model may insert.
_DECIDING_LENGTH = 14


class PlanLeakFilter:
    """Token-level suppressor for a JSON plan written into the narration.

    Feed each streamed token; it returns the text to show the player, or
    ``None`` when there is nothing to show yet. Once a plan starts, everything
    from its opening brace to the end of the reply is withheld and
    :attr:`leaked` is set.

    A brace can arrive in one token and the key in the next, so text from a
    ``{`` is held until there is enough of it to decide, then either released
    unchanged or dropped. Prose almost never contains a brace, so in the
    ordinary case nothing is held and the stream flows token by token.
    """

    def __init__(self) -> None:
        self.leaked = False
        self.removed = ""
        self._held = ""

    def feed(self, token: str) -> str | None:
        """Take one streamed token; return what the player may see."""
        if self.leaked:
            self.removed += token
            return None
        text = self._held + token
        self._held = ""
        shown: list[str] = []
        while text:
            brace = text.find("{")
            if brace == -1:
                shown.append(text)
                break
            shown.append(text[:brace])
            rest = text[brace:]
            if PLAN_OPENING.match(rest):
                self.leaked = True
                self.removed = rest
                break
            if len(rest) < _DECIDING_LENGTH:
                # Not enough yet to tell a plan from a brace in prose.
                self._held = rest
                break
            # An ordinary brace: release it and go on looking after it.
            shown.append(rest[0])
            text = rest[1:]
        visible = "".join(shown)
        return visible or None

    def close(self) -> str:
        """Release anything still held when the stream ends."""
        if self.leaked:
            return ""
        held, self._held = self._held, ""
        return held


#: What the Game Master's own guidance promises a player: a numbered list is
#: never the whole of what they may do (``gm.engine``'s guidelines section).
CHOICES_ARE_NOT_EXHAUSTIVE = (
    "You may also describe any other action — these choices are not the only ones."
)

#: Ways the narration may already have said it, so the line is not doubled.
_ALREADY_SAID = re.compile(
    r"anything else|something else|any other|other action|not limited|"
    r"not the only|your own idea|or do something",
    re.IGNORECASE,
)

#: A numbered choice. The model writes "1.", "1)", "**1.**" and "**1**."
#: on different turns, and the live transcript that prompted this used
#: the third.
#:
#: The reply is the model's, so this runs in linear time (issue #11). The
#: indentation before the number stays on its own line. It was ``\s*``,
#: which also takes line breaks, so every line start in a run of blank lines
#: scanned the rest of the run: quadratic, 3.9 s on 20,000 blank lines. A
#: match now starts on the item's own line rather than at the blank lines
#: above it, which leaves the count the same.
_NUMBERED_ITEM = re.compile(
    r"^[^\S\n]*(?:\*\*)?\d+(?:\*\*)?[.)](?:\*\*)?\s+\S", re.MULTILINE
)

#: Below this, a list is prose ("1. " in a sentence), not a menu.
_MENU_LENGTH = 2


def offers_a_menu(text: str) -> bool:
    """Whether the reply ends by offering the player a numbered list."""
    return len(_NUMBERED_ITEM.findall(text)) >= _MENU_LENGTH


def with_open_choices(text: str) -> str:
    """Promise the player the list is not exhaustive, if it does not already.

    The guidelines already tell the GM to say this, and it says it about two
    turns in three: a player shown four numbered options and nothing else
    reads them as the whole of what is allowed, which is the opposite of
    "present situations, not solutions". Prompting harder does not fix a
    rule the model already has, so the server adds the line when the model
    leaves it out (issues #342, #372).

    Only for a turn that is running the game. The aide answers and stops, and
    is told not to offer menus at all.
    """
    if not text.strip() or not offers_a_menu(text) or _ALREADY_SAID.search(text):
        return text
    return f"{text.rstrip()}\n\n{CHOICES_ARE_NOT_EXHAUSTIVE}"


#: How a message names each ledger figure. Only wordings that can be about a
#: character's record: "level" alone is a dungeon level as often as a
#: character's, which is why the cue below is required beside it.
_FIGURE_WORDS: dict[str, tuple[str, ...]] = {
    SKILL_POINTS_TOPIC: ("skill point", "skill points"),
    EXPERIENCE_POINTS_TOPIC: ("xp", "experience point", "experience points"),
    LEVEL_TOPIC: ("level",),
    EXPERIENCE_YEARS_TOPIC: (
        "years of experience",
        "year of experience",
        "years experience",
    ),
    AGE_TOPIC: ("age", "how old", "years old"),
    FATIGUE_TOPIC: ("fatigue",),
    BODY_TOPIC: ("body",),
}

#: A message asking for a figure rather than mentioning one in passing. A
#: figure word with none of these beside it ("she swings her body round the
#: post") raises nothing, and the server stays silent.
#:
#: The bare ``\?`` that used to head this list is gone (issue #649). A
#: question mark sits at the end of the sentence, so it is beside every word
#: in it, and with the cue matched anywhere in the message almost any
#: question qualified: "What do we do with the body?" was read as a question
#: about the Body pool.
_ASKS_FOR_A_FIGURE = re.compile(
    r"\bhow many\b|\bhow much\b|\bhow old\b|\bwhat\b|\bwhich\b|\btell me\b|"
    r"\bremind me\b|\brecalculate\b|\brecount\b|\bnot right\b|\bthat's wrong\b|"
    r"\bthat is wrong\b|\bshe has\b|\bhe has\b|\bthey have\b",
    re.IGNORECASE,
)

#: How many words may stand between the cue and the figure word and still be
#: one question about that figure (issue #649). Four separates "what is
#: Wren's level" from "what is on the next level down", and "how many skill
#: points" from "what do we do with the body", which a cue matched anywhere
#: in the message could not.
_CUE_WINDOW_WORDS = 4

#: Figure words that are about the world as often as about a record: a
#: dungeon's level, a corpse in the fiction. They are read as a question
#: about a record only when the message also says whose -- one of the
#: session's characters by name or label, or the player speaking of their
#: own (issue #649). The comment beside LEVEL_TOPIC has said a cue was
#: required beside it since #622; the cue never told the two senses apart.
_AMBIGUOUS_FIGURE_TOPICS = frozenset({LEVEL_TOPIC, BODY_TOPIC})

#: The message speaking of a person rather than a place, for the topics
#: above: first person singular, the third-person pronouns, and the
#: possessives. Deliberately not "we" or "us", which is the party somewhere
#: ("what level are we on?" is a floor of the dungeon).
_SPEAKS_OF_A_CHARACTER = re.compile(
    r"\bi\b|\bme\b|\bmy\b|\bmine\b|\bshe\b|\bher\b|\bhers\b|\bhe\b|"
    r"\bhis\b|\bthey\b|\btheir\b|\btheirs\b",
    re.IGNORECASE,
)


def _names(message: str, *words: str) -> bool:
    """Whether ``message`` uses any of ``words`` as a whole word."""
    return any(
        word and re.search(rf"\b{re.escape(word)}\b", message, re.IGNORECASE)
        for word in words
    )


def _asked_beside(message: str, word: str) -> bool:
    """Whether an asking cue stands within a few words before ``word``.

    A figure word is a question about that figure when the asking is next to
    it. The cue used to be matched anywhere in the message, so the question
    mark ending any sentence made every figure word in it a question (issue
    #649).

    Args:
        message: The message the turn is answering.
        word: One of the wordings in :data:`_FIGURE_WORDS`.

    Returns:
        Whether any occurrence of ``word`` is asked about.
    """
    found = list(re.finditer(rf"\b{re.escape(word)}\b", message, re.IGNORECASE))
    if not found:
        return False
    if _ASKS_FOR_A_FIGURE.search(word):
        # "how old" is the asking and the figure word at once.
        return True
    for occurrence in found:
        before = message[: occurrence.start()].split()
        if _ASKS_FOR_A_FIGURE.search(" ".join(before[-_CUE_WINDOW_WORDS:])):
            return True
    return False


#: Actions whose whole point is a figure the player then wants to know. Only
#: this one: the year-playing actions already put their numbers on the screen
#: as roll chips, and a turn that adds a line beside every chip is noise
#: rather than a guarantee.
FIGURE_SETTING_ACTIONS: dict[str, str] = {
    "spend_skill_points": SKILL_POINTS_TOPIC,
}


def figures_raised(
    message: str, actions: Iterable[str] = (), *, names: Iterable[str] = ()
) -> tuple[str, ...]:
    """The ledger figures this turn is about, in the order it raised them.

    A turn is about a figure when the player asked for one -- a figure word
    with an asking cue within a few words of it -- or when an action that
    sets one ran. A word that names a record as readily as it names the
    world, "level" and "body", additionally needs the message to say whose
    (issue #649).

    Args:
        message: The message the turn is answering.
        actions: The kinds of the actions the turn planned, refused ones
            included: a refused spend still leaves the player wanting the
            balance.
        names: Every name and label the session's characters answer to, so
            "what level is Mother Vane?" is not read as a question about the
            player's own character.

    Returns:
        The topics, deduplicated, message first.
    """
    topics: list[str] = []
    whose = _names(message, *names) or bool(_SPEAKS_OF_A_CHARACTER.search(message))
    for topic, words in _FIGURE_WORDS.items():
        if topic in _AMBIGUOUS_FIGURE_TOPICS and not whose:
            continue
        if any(_asked_beside(message, word) for word in words):
            topics.append(topic)
    for action in actions:
        topic = FIGURE_SETTING_ACTIONS.get(action)
        if topic is not None and topic not in topics:
            topics.append(topic)
    return tuple(topics)


def _about(
    lines: Sequence[RosterLine], message: str, others: Sequence[str] = ()
) -> RosterLine | None:
    """Whose record the reply is about, or ``None`` when that is not clear.

    A name the message itself uses settles it. Failing that, one character
    in the session is the answer -- unless the message names somebody else
    the session knows, in which case it is a question about that person and
    the player's own record is not what was asked for. A one-character
    session used to answer with its one character whoever the message named
    (issue #649).

    Args:
        lines: The session's characters as the records stand now.
        message: The message the turn is answering.
        others: The people this session has recorded, by name.
    """
    named = [line for line in lines if _names(message, line.name, line.label)]
    if named:
        return named[0] if len(named) == 1 else None
    if len(lines) == 1 and not _names(message, *others):
        return lines[0]
    return None


#: The number at the head of a menu item, which is a place in a list and not
#: a figure about anybody. The first measurement of this guarantee was cost
#: two of its three runs by exactly that: the reply ended in a five-item menu,
#: the record held one free skill point, and the "1." of "1. Rest for the
#: night" was read as the reply having stated it.
#:
#: Linear for the same reason as :data:`_NUMBERED_ITEM` (issue #11). Removing
#: the number now leaves the blank lines above the item, which the old
#: pattern removed with it; a line break stays between the item and the text
#: before it either way, so the sentences and words read around each number
#: are the same.
_ITEM_NUMBER = re.compile(
    r"^[^\S\n]*(?:\*\*)?\d+(?:\*\*)?[.)](?:\*\*)?(?=\s)", re.MULTILINE
)

#: A whole number as prose writes it, thousands separators included, so
#: ``2,500 XP`` is the number 2500 and not a 2 followed by a 500.
_WRITTEN_NUMBER = re.compile(r"\d[\d,]*")

#: How far from the topic's own words a number may stand and still be read
#: as stating that figure: the whole wording has to fall within the four
#: words either side of the number, in its sentence. That carries "1 free
#: skill point" and "her free skill points number 1", and stops well short
#: of the next clause. It does not carry "skill points available to spend:
#: 1", where "skill" is the fifth word back; the tests pin that reply as not
#: stating the figure. The same adjacency discipline #649 applied to the
#: asking cue.
_FIGURE_WINDOW_WORDS = 4

#: Where one sentence stops. A number in one sentence does not state a
#: figure word standing in the next. Deliberately not a colon, which is how
#: a reply writes "free skill points: 1".
_SENTENCE_BREAK_CHARACTERS = ".!?\n"

#: A reply as the figure check reads it: words, as ``str.split`` sees the
#: text between two sentence breaks, and the breaks themselves.
_WORD_OR_BREAK = re.compile(
    rf"[^\s{re.escape(_SENTENCE_BREAK_CHARACTERS)}]+"
    rf"|[{re.escape(_SENTENCE_BREAK_CHARACTERS)}]"
)

#: A number written as an adjustment rather than a total -- "+1 to her
#: attacks", "-2 to the roll". A bonus is not a balance. Matched on the
#: reversed reply from where the number starts: any whitespace, then a sign.
_MODIFIER_SIGN_BEFORE = re.compile(r"\s*+[-+−]")


class _FigureReading:
    """One reply, indexed once so each number is checked in constant time.

    A number is read beside the words around it: up to
    :data:`_FIGURE_WINDOW_WORDS` either side in its sentence, the part of
    its own word before and after it counting as one word each, joined by
    single spaces with a space where the number was. The topic's wording has
    to appear in that text as a whole word (``\\b`` either side).

    Building that text for every number and searching it was quadratic in
    the reply's length: the words around one number can be most of the
    reply, as in "1a1a1a..." where every number sits inside one long word
    (issue #51). So the reply is read once. Its words are laid end to end,
    one space between words of a sentence (the ``collapsed`` text), and every
    place a wording occurs there is found once. A number's text is then two
    stretches of the collapsed text, before and after the number, with a
    space between, and the wording occurs in it if it occurs inside either
    stretch or across the space. Each of those is a lookup. Every wording
    starts and ends with a letter, so ``\\b`` holds wherever one starts or
    ends at the edge of the text a number is read beside.
    """

    def __init__(self, prose: str) -> None:
        self.pieces = list(_WORD_OR_BREAK.finditer(prose))
        self.piece_starts = [piece.start() for piece in self.pieces]
        self.is_word = [
            piece.group() not in _SENTENCE_BREAK_CHARACTERS for piece in self.pieces
        ]
        parts: list[str] = []
        self.collapsed_starts: list[int] = []
        length = 0
        for index, piece in enumerate(self.pieces):
            if index and self.is_word[index] and self.is_word[index - 1]:
                parts.append(" ")
                length += 1
            self.collapsed_starts.append(length)
            parts.append(piece.group())
            length += len(piece.group())
        self.collapsed = "".join(parts)
        word_characters = bytearray(len(self.collapsed) + 2)
        for run in re.finditer(r"\w+", self.collapsed):
            word_characters[run.start() + 1 : run.end() + 1] = b"\x01" * len(
                run.group()
            )
        #: ``boundary[p]``: whether ``\b`` holds at ``p`` in the collapsed
        #: text, as it does at either end of a text when the character inside
        #: is a word character.
        self.boundary = [
            word_characters[position] != word_characters[position + 1]
            for position in range(len(self.collapsed) + 1)
        ]

    def stretches(
        self, number: re.Match[str]
    ) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
        """The collapsed text read before and after ``number``, or ``None``."""
        index = bisect_right(self.piece_starts, number.start()) - 1
        piece = self.pieces[index]
        piece_start = self.collapsed_starts[index]
        number_start = piece_start + number.start() - piece.start()
        number_end = piece_start + number.end() - piece.start()

        before = None
        words = 0
        if piece.start() < number.start():
            before = (piece_start, number_start)
            words = 1
        earlier = index - 1
        while words < _FIGURE_WINDOW_WORDS and earlier >= 0 and self.is_word[earlier]:
            end = before[1] if before else self._end(earlier)
            before = (self.collapsed_starts[earlier], end)
            words += 1
            earlier -= 1

        after = None
        words = 0
        if number.end() < piece.end():
            after = (number_end, piece_start + len(piece.group()))
            words = 1
        later = index + 1
        while (
            words < _FIGURE_WINDOW_WORDS
            and later < len(self.pieces)
            and self.is_word[later]
        ):
            start = after[0] if after else self.collapsed_starts[later]
            after = (start, self._end(later))
            words += 1
            later += 1
        return before, after

    def _end(self, index: int) -> int:
        return self.collapsed_starts[index] + len(self.pieces[index].group())


def _same_letters(wording: str, text: str, start: int) -> bool:
    """Whether ``wording`` stands at ``start`` in ``text``, ignoring case.

    Case is ignored as a pattern compiled with ``re.IGNORECASE`` ignores it.
    """
    return (
        re.compile(re.escape(wording), re.IGNORECASE).match(
            text, start, start + len(wording)
        )
        is not None
    )


class _WordingIndex:
    """Every place one wording occurs in a reply's collapsed text."""

    def __init__(self, reading: _FigureReading, wording: str) -> None:
        self.reading = reading
        self.length = len(wording)
        collapsed = reading.collapsed
        boundary = reading.boundary
        self.starts = {
            found.start()
            for found in re.finditer(
                rf"(?={re.escape(wording)})", collapsed, re.IGNORECASE
            )
        }
        #: ``whole_before[p]``: how many occurrences start before ``p`` with a
        #: word boundary at both ends in the collapsed text itself.
        self.whole_before = [0] * (len(collapsed) + 2)
        for position in range(len(collapsed) + 1):
            whole = (
                position in self.starts
                and boundary[position]
                and boundary[position + self.length]
            )
            self.whole_before[position + 1] = self.whole_before[position] + whole
        #: The wording cut at each of its spaces: the words that end the text
        #: before a number and the words that start the text after it.
        self.splits = [
            (wording[:space], wording[space + 1 :])
            for space, character in enumerate(wording)
            if character == " "
        ]

    def within(self, stretch: tuple[int, int]) -> bool:
        """Whether the wording is a whole word inside the stretch alone.

        At the stretch's own ends ``\\b`` holds whatever the collapsed text
        has beyond them: the stretch is read with a space or nothing there.
        """
        start, end = stretch
        last_start = end - self.length
        if last_start < start:
            return False
        if self.whole_before[last_start + 1] - self.whole_before[start] > 0:
            return True
        boundary = self.reading.boundary
        if start in self.starts and (
            start + self.length == end or boundary[start + self.length]
        ):
            return True
        return last_start in self.starts and (
            last_start == start or boundary[last_start]
        )

    def across(self, before: tuple[int, int], after: tuple[int, int]) -> bool:
        """Whether the wording runs from the end of ``before`` into ``after``."""
        collapsed = self.reading.collapsed
        boundary = self.reading.boundary
        for head, tail in self.splits:
            head_start = before[1] - len(head)
            tail_end = after[0] + len(tail)
            if head_start < before[0] or tail_end > after[1]:
                continue
            if not (head_start == before[0] or boundary[head_start]):
                continue
            if not (tail_end == after[1] or boundary[tail_end]):
                continue
            if _same_letters(head, collapsed, head_start) and _same_letters(
                tail, collapsed, after[0]
            ):
                return True
        return False


def _states_figure(text: str, figure: LedgerFigure) -> bool:
    """Whether ``text`` already states ``figure`` as a figure about the record.

    The number has to sit beside the topic's own words, in the same sentence,
    and must not be an adjustment. Matching the digit anywhere in the reply
    silenced the guarantee whenever the prose happened to carry that number
    for another reason: a measured turn wrote "+1 to her attacks" while the
    balance was 1 free skill point, so the server said nothing and the reply
    answered the question in words alone (issue #727). The smaller the
    figure the likelier that is -- 1, 2 and 3 are all over ordinary prose.

    Args:
        text: The narration as the model wrote it.
        figure: The figure the record holds for the topic in question.

    Returns:
        Whether the reply already says it, so the server should stay silent.
    """
    prose = _ITEM_NUMBER.sub("", text)
    backwards = prose[::-1]
    reading: _FigureReading | None = None
    wordings: list[_WordingIndex] = []
    for number in _WRITTEN_NUMBER.finditer(prose):
        if number.group().replace(",", "") != str(figure.value):
            continue
        if _MODIFIER_SIGN_BEFORE.match(backwards, len(prose) - number.start()):
            continue
        if reading is None:
            reading = _FigureReading(prose)
            wordings = [
                _WordingIndex(reading, wording)
                for wording in _FIGURE_WORDS.get(figure.topic, ())
            ]
        before, after = reading.stretches(number)
        for wording in wordings:
            if before and wording.within(before):
                return True
            if after and wording.within(after):
                return True
            if before and after and wording.across(before, after):
                return True
    return False


def with_record_figure(
    text: str,
    *,
    topics: Sequence[str],
    lines: Sequence[RosterLine],
    message: str,
    others: Sequence[str] = (),
) -> str:
    """Quote the record's own figure when a reply asked for one states none.

    A player who asks how many skill points are left is answered about two
    turns in three with a figure, and the rest of the time with prose that
    names none -- or, once, with the subtraction done in the model's head and
    got wrong. The record is never wrong; what varies is whether the
    narration quotes it. Prompting harder is what was already tried, twice,
    with two five-run measurements that moved the wrong way, so the server
    appends the figure instead (issue #622, the shape of #342's open-choices
    line).

    Deliberately narrow, in three ways. It appends nothing when the turn
    raised no figure; nothing when the narration already states that figure
    beside the topic's own words; and nothing about a character the message
    did not settle. It quotes the record and never computes, so the appended
    line cannot be the wrong answer the way an arithmetic line could.

    The second of those used to be any occurrence of the digit, which meant
    a weapon modifier in the prose passed for the balance (issue #727); see
    :func:`_states_figure`.

    Args:
        text: The narration as the model wrote it.
        topics: What the turn raised, from :func:`figures_raised`.
        lines: The session's characters as the records stand now.
        message: The message the turn is answering, which says whose record
            a multi-character session is being asked about.
        others: The people this session has recorded, by name: a question
            about one of them is not a question about the player's own
            character (issue #649).

    Returns:
        The narration, with at most one line appended. One: a reply followed
        by a column of figures reads as a sheet, not as an answer.
    """
    if not text.strip() or not topics:
        return text
    line = _about(lines, message, others)
    if line is None:
        return text
    held = {figure.topic: figure for figure in line.ledger}
    for topic in topics:
        figure = held.get(topic)
        if figure is None or _states_figure(text, figure):
            continue
        return f"{text.rstrip()}\n\n{line.name}'s record: {figure.phrase}."
    return text
