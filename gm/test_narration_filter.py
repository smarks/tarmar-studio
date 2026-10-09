"""What the server guarantees about a narration (issues #603, #342, #622)."""

from django.test import SimpleTestCase

from gm.turn_engine.narration import (
    PlanLeakFilter,
    figures_raised,
    with_open_choices,
    with_record_figure,
)
from gm.turn_engine.state import (
    AGE_TOPIC,
    BODY_TOPIC,
    EXPERIENCE_POINTS_TOPIC,
    LEVEL_TOPIC,
    SKILL_POINTS_TOPIC,
    LedgerFigure,
    RosterLine,
)


def feed_all(tokens: list[str]) -> tuple[str, PlanLeakFilter]:
    """Stream tokens through a filter; return what a player would have seen."""
    leak_filter = PlanLeakFilter()
    shown = "".join(
        visible for token in tokens if (visible := leak_filter.feed(token)) is not None
    )
    return shown + leak_filter.close(), leak_filter


class PlanLeakFilterTest(SimpleTestCase):
    def test_ordinary_prose_passes_through_token_by_token(self):
        leak_filter = PlanLeakFilter()
        for token in ["The well ", "is dark ", "and cold."]:
            self.assertEqual(leak_filter.feed(token), token)
        self.assertFalse(leak_filter.leaked)

    def test_a_plan_at_the_end_is_withheld(self):
        shown, leak_filter = feed_all(
            [
                "To begin, I will roll her attributes.\n\n",
                '{"actions": [{"action": "roll_attributes"}]}',
            ]
        )
        self.assertEqual(shown, "To begin, I will roll her attributes.\n\n")
        self.assertTrue(leak_filter.leaked)
        self.assertIn("roll_attributes", leak_filter.removed)

    def test_a_plan_split_across_tokens_is_withheld(self):
        """The brace and the key routinely arrive in different tokens."""
        shown, leak_filter = feed_all(["Rolling now.\n", "{", '"act', 'ions"', ": []}"])
        self.assertEqual(shown, "Rolling now.\n")
        self.assertTrue(leak_filter.leaked)

    def test_whitespace_between_the_brace_and_the_key_is_still_a_plan(self):
        shown, leak_filter = feed_all(['Done.\n{ "actions": []}'])
        self.assertEqual(shown, "Done.\n")
        self.assertTrue(leak_filter.leaked)

    def test_everything_after_a_plan_is_withheld(self):
        _, leak_filter = feed_all(['{"actions": []}', "\n\nAnd then she ran."])
        self.assertTrue(leak_filter.leaked)
        self.assertIn("And then she ran.", leak_filter.removed)

    def test_a_brace_in_prose_is_not_a_plan(self):
        """A brace the model uses in prose must not swallow the narration."""
        shown, leak_filter = feed_all(
            ["The rune {the spiral one} glows, and the air turns cold."]
        )
        self.assertEqual(
            shown, "The rune {the spiral one} glows, and the air turns cold."
        )
        self.assertFalse(leak_filter.leaked)

    def test_a_trailing_brace_is_released_when_the_stream_ends(self):
        """Text held for a decision that never comes is still the player's."""
        shown, leak_filter = feed_all(["She opens the box {"])
        self.assertEqual(shown, "She opens the box {")
        self.assertFalse(leak_filter.leaked)

    def test_a_brace_then_a_real_plan(self):
        shown, leak_filter = feed_all(['A {curious} sight.\n{"actions": [1]}'])
        self.assertEqual(shown, "A {curious} sight.\n")
        self.assertTrue(leak_filter.leaked)


class OpenChoicesTest(SimpleTestCase):
    """A numbered list is never the whole of what a player may do (#342, #372).

    The guidelines already say to tell them so, and the model says it about
    two turns in three. A player shown four options and nothing else reads
    them as the only ones, which is the opposite of "present situations, not
    solutions", so the server adds the line when the model leaves it out.
    """

    MENU = "The tavern is loud.\n\n1. Order a drink\n2. Talk to the barkeep\n3. Leave"

    def test_a_menu_gains_the_promise(self):
        said = with_open_choices(self.MENU)

        self.assertTrue(said.startswith(self.MENU))
        self.assertIn("any other action", said)

    def test_prose_is_left_alone(self):
        said = "She pushes the door open and steps out of the rain."

        self.assertEqual(with_open_choices(said), said)

    def test_it_is_not_said_twice(self):
        already = f"{self.MENU}\n\nOr describe any other action."

        self.assertEqual(with_open_choices(already), already)

    def test_one_numbered_line_is_not_a_menu(self):
        said = "There is one way down:\n1. The rope."

        self.assertEqual(with_open_choices(said), said)

    def test_bold_numbering_counts(self):
        said = with_open_choices("Choose:\n\n**1.** Go left\n**2.** Go right")

        self.assertIn("any other action", said)

    def test_an_empty_reply_is_left_alone(self):
        self.assertEqual(with_open_choices(""), "")

    def test_every_wording_the_harness_accepts_counts_as_already_said(self):
        """The server and the check must agree on what the promise is."""
        for wording in ("anything else", "something else", "any other", "not the only"):
            with self.subTest(wording=wording):
                already = f"{self.MENU}\n\nYou may do {wording}."
                self.assertEqual(with_open_choices(already), already)


class RecordFigureTest(SimpleTestCase):
    """A reply asked for a ledger figure carries one (issue #622).

    The record is never wrong; what varied was whether the narration quoted
    it. The server appends the record's own figure when the model leaves it
    out, and says nothing at all otherwise.
    """

    WREN = RosterLine(
        character_id=41,
        name="Wren",
        label="",
        figures="age 19; 8 free skill points",
        ledger=(
            LedgerFigure(SKILL_POINTS_TOPIC, 8, "8 free skill points"),
            LedgerFigure(AGE_TOPIC, 19, "age 19"),
        ),
    )
    ASKED = "How many skill points does Wren have available to spend right now?"

    def figure_in(self, narration: str, message: str = "", topics=None) -> str:
        """The narration as a player would read it, guarantee applied."""
        message = message or self.ASKED
        return with_record_figure(
            narration,
            topics=topics if topics is not None else figures_raised(message),
            lines=[self.WREN],
            message=message,
        )

    def test_a_reply_with_no_figure_gains_the_records(self):
        said = self.figure_in("Wren considers what she might learn next.")

        self.assertTrue(said.startswith("Wren considers"))
        self.assertIn("Wren's record: 8 free skill points.", said)

    def test_a_reply_that_already_states_the_figure_is_left_alone(self):
        said = "She has 8 skill points still to spend."

        self.assertEqual(self.figure_in(said), said)

    def test_a_turn_that_raised_no_figure_is_left_alone(self):
        told = "The rain has not let up since morning."

        self.assertEqual(
            with_record_figure(
                told,
                topics=figures_raised("I walk down to the river."),
                lines=[self.WREN],
                message="I walk down to the river.",
            ),
            told,
        )

    def test_a_figure_word_with_no_question_raises_nothing(self):
        """ "She braces her body against the door" is not asking for Body."""
        self.assertEqual(figures_raised("She braces her body against the door."), ())

    def test_being_told_the_wrong_total_raises_the_topic(self):
        pushed = (
            "That's not right. She has nine skill points available. Recalculate it."
        )

        self.assertEqual(figures_raised(pushed), (SKILL_POINTS_TOPIC,))

    def test_spending_raises_the_balance_even_without_a_question(self):
        self.assertEqual(
            figures_raised(
                "Wren spends one skill point on sword.", ["spend_skill_points"]
            ),
            (SKILL_POINTS_TOPIC,),
        )

    def test_an_action_that_sets_no_figure_raises_nothing(self):
        self.assertEqual(figures_raised("Wren draws her sword.", ["roll"]), ())

    def test_only_one_line_is_appended(self):
        said = self.figure_in(
            "She thinks it over.",
            message="How many skill points does she have, and what is her age?",
        )

        self.assertEqual(said.count("Wren's record:"), 1)
        self.assertIn("8 free skill points", said)

    def test_the_second_topic_is_quoted_when_the_first_is_already_there(self):
        said = self.figure_in(
            "She has 8 skill points to spend, and has seen some summers.",
            message="How many skill points does she have, and what is her age?",
        )

        self.assertIn("Wren's record: age 19.", said)

    def test_a_bare_number_away_from_the_topic_no_longer_silences_the_line(self):
        """The reply used to be read as stating the balance (issue #727).

        "8 points" was counted because the digit stood somewhere in the
        reply, which is the same reading that let "+1 to her attacks" pass
        for a balance of 1. The number now has to stand beside the topic's
        own words, so the balance is quoted rather than assumed stated.
        """
        said = self.figure_in(
            "She has 8 to spend, and has seen some summers.",
            message="How many skill points does she have, and what is her age?",
        )

        self.assertIn("Wren's record: 8 free skill points.", said)

    def test_a_figure_the_record_does_not_hold_is_not_invented(self):
        said = "She is tired."

        self.assertEqual(
            self.figure_in(said, message="What is her Fatigue right now?"), said
        )

    def test_a_second_character_leaves_an_unnamed_question_alone(self):
        told = "Both of them have points left."
        other = RosterLine(
            character_id=42,
            name="Bram",
            label="",
            figures="3 free skill points",
            ledger=(LedgerFigure(SKILL_POINTS_TOPIC, 3, "3 free skill points"),),
        )
        message = "How many skill points are left?"

        self.assertEqual(
            with_record_figure(
                told,
                topics=figures_raised(message),
                lines=[self.WREN, other],
                message=message,
            ),
            told,
        )

    def test_a_named_character_settles_a_session_with_several(self):
        other = RosterLine(
            character_id=42,
            name="Bram",
            label="",
            figures="3 free skill points",
            ledger=(LedgerFigure(SKILL_POINTS_TOPIC, 3, "3 free skill points"),),
        )
        message = "How many skill points does Bram have left?"

        said = with_record_figure(
            "He looks over what he has learned.",
            topics=figures_raised(message),
            lines=[self.WREN, other],
            message=message,
        )

        self.assertIn("Bram's record: 3 free skill points.", said)

    def test_an_empty_reply_is_left_alone(self):
        self.assertEqual(self.figure_in(""), "")

    def test_the_appended_line_reads_as_the_free_total_the_harness_looks_for(self):
        """The server and ``gm_behaviour_check``'s reader must agree.

        The check counts a figure as a claim about the free total when an
        availability word sits near it and no "earned" or "spent" does. The
        appended line has to pass that reading, or the guarantee would put a
        figure on the screen that the measurement cannot see.
        """
        said = self.figure_in("Wren weighs what to learn.")
        appended = said.rsplit("\n", 1)[-1]

        self.assertRegex(appended, r"\b8\s+(?:\w+\s+){0,3}?points?\b")
        self.assertIn("free", appended.lower())
        self.assertNotRegex(appended, r"\bearned\b|\bspent\b|\bmore\b")


class MenuNumbersAreNotFiguresTest(SimpleTestCase):
    """A place in a numbered list is not a figure about anybody (issue #622).

    The guarantee's first live measurement lost two of its three runs here:
    the record held one free skill point, the reply ended in a five-item
    menu, and the "1." of "1. Rest for the night" was read as the reply
    having already stated the figure, so nothing was appended.
    """

    ONE_POINT = RosterLine(
        character_id=42,
        name="Wren",
        label="the laborer",
        figures="1 free skill point",
        ledger=(LedgerFigure(SKILL_POINTS_TOPIC, 1, "1 free skill point"),),
    )
    ASKED = "How many skill points does Wren have available to spend right now?"

    def said(self, narration: str) -> str:
        return with_record_figure(
            narration,
            topics=figures_raised(self.ASKED),
            lines=[self.ONE_POINT],
            message=self.ASKED,
        )

    def test_a_menu_item_numbered_one_does_not_count_as_the_figure(self):
        written = (
            "Wren has no free skill points available to spend.\n\n"
            "What does she do?\n\n"
            "1. Rest for the night.\n2. Visit the tavern.\n3. Leave the camp."
        )

        self.assertIn("Wren's record: 1 free skill point.", self.said(written))

    def test_a_thousands_separator_is_read_as_one_number(self):
        """``2,500 XP`` is 2500, not a 2 beside a 500."""
        held = RosterLine(
            character_id=42,
            name="Wren",
            label="",
            figures="XP 2500",
            ledger=(LedgerFigure(EXPERIENCE_POINTS_TOPIC, 500, "XP 500"),),
        )
        message = "What is her XP?"

        said = with_record_figure(
            "Her ledger shows 2,500 experience.",
            topics=figures_raised(message),
            lines=[held],
            message=message,
        )

        self.assertIn("Wren's record: XP 500.", said)

    def test_the_figure_in_the_prose_still_silences_it(self):
        written = "She has 1 free skill point left.\n\n1. Rest.\n2. Go."

        self.assertEqual(self.said(written), written)

    def test_a_weapon_modifier_does_not_count_as_the_balance(self):
        """The measured reply from issue #727, word for word.

        Wren's balance was 1 free skill point and the reply stated it in
        words alone, which ``skill_point_claims`` does not count; the "+1"
        of a weapon modifier two sentences later was read as the figure, so
        the server stayed silent and the question went unanswered in any
        form anyone checks.
        """
        written = (
            "Wren stands with a single skill point in her hand. The record "
            "shows she has exactly one free skill point available to spend."
            "\n\nShe may use this point to raise her existing Sword skill to "
            "Level II, gaining **+1** to her attacks with a broadsword and "
            "**+1** to any other Striking weapon."
        )

        self.assertIn("Wren's record: 1 free skill point.", self.said(written))

    def test_the_figure_a_sentence_away_from_its_own_words_is_not_it(self):
        """ "She counts 1 arrow" is not the skill-point balance."""
        written = "She counts 1 arrow. Her skill points are her own business."

        self.assertIn("Wren's record: 1 free skill point.", self.said(written))


class TheFigureLineIsAboutTheRightThingTest(SimpleTestCase):
    """The #622 line answered about the dungeon, or about the wrong one (#649).

    ``figures_raised`` fired when any figure word appeared and any asking cue
    matched *anywhere* in the message. The cue set held a bare ``?``, which
    ends every question, so "What is on the next level down?" was a question
    about a character's level and "What do we do with the body?" a question
    about the Body pool. ``_about`` then returned the one roster line
    whatever the message named, so a question about an NPC was answered with
    the player's own record, under an attribution that reads as the server
    speaking.
    """

    WREN = RosterLine(
        character_id=41,
        name="Wren",
        label="",
        figures="level 2; Body 14",
        ledger=(
            LedgerFigure(LEVEL_TOPIC, 2, "level 2"),
            LedgerFigure(BODY_TOPIC, 14, "Body 14"),
        ),
    )
    NAMES = ("Wren",)
    MET = ("Mother Vane",)

    def answered(self, narration: str, message: str) -> str:
        """The narration as a player would read it, guarantee applied."""
        return with_record_figure(
            narration,
            topics=figures_raised(message, names=self.NAMES),
            lines=[self.WREN],
            message=message,
            others=self.MET,
        )

    def test_a_question_about_the_dungeon_raises_nothing(self):
        message = "What is on the next level down?"

        self.assertEqual(figures_raised(message, names=self.NAMES), ())
        said = "Stone stairs fall away into the dark."
        self.assertEqual(self.answered(said, message), said)

    def test_a_question_about_a_corpse_raises_nothing(self):
        message = "What do we do with the body?"

        self.assertEqual(figures_raised(message, names=self.NAMES), ())
        said = "It lies where it fell."
        self.assertEqual(self.answered(said, message), said)

    def test_a_question_about_somebody_else_is_not_answered_with_this_record(self):
        message = "What level is Mother Vane?"
        said = "Mother Vane keeps her own counsel, and will not be drawn on it."

        self.assertEqual(self.answered(said, message), said)

    def test_a_question_about_somebody_else_by_an_unambiguous_figure(self):
        """Even a figure word that cannot mean the dungeon (issue #649)."""
        message = "How much XP does Mother Vane have?"
        said = "She does not measure herself that way."

        self.assertEqual(self.answered(said, message), said)

    def test_the_character_named_is_still_answered(self):
        said = self.answered("She has come a long way.", "What is Wren's level?")

        self.assertIn("Wren's record: level 2.", said)

    def test_the_player_asking_about_their_own_is_still_answered(self):
        said = self.answered("She is hale enough.", "How much Body do I have left?")

        self.assertIn("Wren's record: Body 14.", said)

    def test_the_ledger_numbers_scenario_still_raises_its_figure(self):
        """The six-conversation result this must not cost (issue #622)."""
        for message in (
            "How many skill points does Wren have available to spend right now?",
            "That's not right. She has nine skill points available. Recalculate it.",
        ):
            with self.subTest(message=message):
                self.assertIn(
                    SKILL_POINTS_TOPIC, figures_raised(message, names=self.NAMES)
                )

    def test_a_question_using_a_pronoun_is_still_about_a_character(self):
        for message in (
            "Which level is she?",
            "What level is he?",
            "How much Body do they have?",
        ):
            with self.subTest(message=message):
                self.assertTrue(figures_raised(message, names=self.NAMES))

    def test_the_party_asking_where_they_are_is_not_asking_about_a_record(self):
        for message in (
            "What level are we on?",
            "She climbs down to the next level.",
            "What level is the next floor?",
        ):
            with self.subTest(message=message):
                self.assertEqual(figures_raised(message, names=self.NAMES), ())
