"""The Game Master's prompt makes the model an aide, not a GM (issue #353).

Spencer runs his game at a physical table and consults this application the
way he would consult a book on the shelf. The prompt he was getting cast the
model as *the* Game Master: it set scenes at him, narrated, asked "what do you
do?", offered numbered choices, and ruled on things he had already decided.
This module holds the wording that changed that, the place it sits in the
prompt (last, and after the player's ten sections, so the cached prefix does
not move), and the mode indicator the chat page shows a Game Master.

The behaviour itself was tuned against the live model; the transcripts are on
the pull request. What tests can hold is the wording that produced it and the
structure that keeps first-token time flat.
"""

from common.test_helpers import BaseTestCase

from .engine import (
    DM_SYSTEM_PROMPT,
    SECTION_DM_AIDE,
    SECTION_DM_AUTHORITY,
    SECTION_GUIDELINES,
    SECTION_ROLE,
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_SECTIONS,
    system_prompt_for,
)
from .services import get_system_message

CHAT_URL = "/tarmar/gm/"


class AidePromptShapeTest(BaseTestCase):
    """Where the section sits, and what that costs the prompt cache."""

    def test_the_aide_section_is_last(self):
        """It overrides the manner set out above it, so it reads last."""
        self.assertEqual(
            DM_SYSTEM_PROMPT,
            "\n\n".join(
                (*SYSTEM_PROMPT_SECTIONS, SECTION_DM_AUTHORITY, SECTION_DM_AIDE)
            ),
        )

    def test_the_dm_prompt_still_extends_the_players_prompt(self):
        """Issues #330/#331: the cached prefix must stay byte-identical.

        Both prompts are still whole-string constants built once at import,
        and the DM's is still the player's plus text at the end -- so the
        MLX runner's restore point for the shared prefix survives, and the
        first token after this change costs what it cost before.
        """
        self.assertTrue(DM_SYSTEM_PROMPT.startswith(SYSTEM_PROMPT))

    def test_nothing_is_assembled_per_call(self):
        self.assertIs(system_prompt_for(True), DM_SYSTEM_PROMPT)
        self.assertIs(system_prompt_for(False), SYSTEM_PROMPT)

    def test_the_player_prompt_is_untouched(self):
        """Deliverable 3: a player still gets the GM who runs the game."""
        self.assertNotIn(SECTION_DM_AIDE, SYSTEM_PROMPT)
        self.assertNotIn("Be The Aide", SYSTEM_PROMPT)
        # The player-facing manner the aide overrides is still there for them.
        self.assertIn("Give vivid but concise descriptions", SECTION_ROLE)
        self.assertIn("When offering numbered choices", SECTION_GUIDELINES)
        self.assertEqual(
            get_system_message(self.create_player())["content"], SYSTEM_PROMPT
        )

    def test_the_addendum_costs_a_readable_amount_of_context(self):
        """Roughly 1.5k tokens at ~4 chars/token, on top of a ~9-10k prompt.

        Long enough to say all of it concretely -- the live model needed the
        named examples, not a summary -- and far short of crowding the window
        the conversation and the canon excerpts share. The whole prompt with
        it is held to its share of GM_NUM_CTX by
        ``test_prompt_size_stays_within_the_context_budget`` (issue #907).
        """
        self.assertLess(len(SECTION_DM_AIDE), 6_000)


class AideWordingTest(BaseTestCase):
    """The instructions that changed the live model's behaviour.

    Each phrase below is one the transcripts turned on: dropping it is how
    this regresses, so each is named rather than checked as a block.
    """

    def test_it_says_who_is_running_the_game(self):
        for phrase in (
            "You are not running this one",
            "a physical table you cannot see",
            "consult a book on the shelf",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, SECTION_DM_AIDE)

    def test_it_says_answer_and_stop(self):
        for phrase in (
            "and then stop",
            "One question gets one answer",
            "No scene-setting",
            "What do you do?",
            "No numbered menu of options unless options are what was asked",
            "No preamble",
            "no closing recap",
            "Do not think out loud",
            "Do not steer",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, SECTION_DM_AIDE)

    def test_it_hands_the_rulings_to_the_game_master(self):
        for phrase in (
            "You do not adjudicate here",
            "that is the rule from then on",
            "once, in one short sentence",
            "then never again",
            "quietly answer by the book anyway",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, SECTION_DM_AIDE)

    def test_it_forbids_inventing_the_table(self):
        for phrase in (
            "Never invent what happened at the table",
            "say plainly that you do not know",
            "Track only the state you were handed",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, SECTION_DM_AIDE)

    def test_it_says_what_the_aide_is_for(self):
        """Deliverable 2: quiet in manner, not stingy with substance."""
        for phrase in (
            "Rules lookups",
            "the number and the citation",
            "NPCs on demand",
            "Read-aloud description",
            "What would this creature do?",
            "Consequences and complications on request",
            "Continuity against the canon",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, SECTION_DM_AIDE)

    def test_it_bans_the_offer_itself_not_a_list_of_phrasings(self):
        """#377: #353's named examples were not the wordings it reached for.

        The live harness caught "Let me know if you want that roll" and, once
        the literal phrases were banned, "If you wish, I can roll..." -- the
        same offer wearing a different conjunction. So the section names the
        phrases *and* rules out the conditional form of any of them.
        """
        for phrase in (
            "The ban is on offering, not on a list of phrasings",
            '"What would you like"',
            '"Would you like me to"',
            '"Do you want me to"',
            '"If you would like, I can help..."',
            '"Let me know if"',
            '"Let me know how"',
            '"Shall I"',
            '"Just say the word"',
            '"Happy to help"',
            "never the conditional form of the same offer",
            '"If you wish, I can roll..."',
            "The last sentence of a reply never ends in a question mark",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, SECTION_DM_AIDE)

    def test_it_says_what_to_do_with_an_undecided_thing_instead(self):
        """The offer is banned, so the section has to say what replaces it."""
        for phrase in (
            "do not offer to roll it",
            "is a finished reply",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, SECTION_DM_AIDE)

    def test_it_names_the_announced_list_openers_it_forbids(self):
        for phrase in (
            '"To recap"',
            '"To summarise"',
            '"Here are three ways to handle it"',
            '"Here is a quick rundown"',
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, SECTION_DM_AIDE)

    def test_the_ban_is_also_the_last_thing_the_section_says(self):
        """#377: the ban held at 7/10 while it sat mid-section. It is repeated
        as the section's closing line -- the last words before the
        conversation -- because that is the position the failure is about."""
        self.assertTrue(
            SECTION_DM_AIDE.rstrip().endswith("He will ask."),
            "the closing line must stay last; recency is the point of it",
        )
        self.assertIn("no question in the final line", SECTION_DM_AIDE)

    def test_it_forbids_announcing_what_it_is_doing(self):
        """#392, #590: replies opened "I am recording the names" and "I am now
        running 5 years". The rule sits in the aide section itself, ahead of
        everything else it says about manner."""
        for phrase in (
            "Never announce what you are doing or about to do",
            '"I am recording"',
            '"I am now running"',
            "When something was refused, say only what is now true",
            "what he can ask for",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(
                    " ".join(phrase.split()), " ".join(SECTION_DM_AIDE.split())
                )
        self.assertLess(
            SECTION_DM_AIDE.index("Never announce what you are doing"),
            SECTION_DM_AIDE.index("### Stop When The Answer Is Finished"),
        )
        self.assertIn(SECTION_DM_AIDE, system_prompt_for(True, "aide"))

    def test_the_board_still_resolves_a_fight(self):
        """Deliverable 4: ``/fight`` stays useful; narration is on request."""
        self.assertIn("/fight", SECTION_DM_AIDE)
        self.assertIn("narrate it only when narration is asked for", SECTION_DM_AIDE)


class ModeIndicatorTest(BaseTestCase):
    """Deliverable 5: the chat page says which GM you are talking to."""

    def _page(self, user) -> str:
        self.login_as(user)
        response = self.client.get(CHAT_URL)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_a_game_master_sees_the_aide_badge(self):
        page = self._page(self.create_dm())
        self.assertIn('class="gm-mode-indicator"', page)
        self.assertIn("GM aide", page)
        self.assertIn("You are running the game; it is not.", page)

    def test_an_admin_sees_it_too(self):
        self.assertIn('class="gm-mode-indicator"', self._page(self.create_admin()))

    def test_a_player_sees_the_indicator_naming_their_own_mode(self):
        """Superseded by issue #372, deliberately.

        This asserted that a player saw no indicator at all, because their
        mode was the only one they could have. It is now a control as well as
        a badge and a player may switch, so the thing to hold is that it names
        *their* mode rather than the Game Master's.
        """
        page = self._page(self.create_player())
        self.assertIn('class="gm-mode-indicator"', page)
        self.assertIn("Game Master", page)
        self.assertNotIn("GM aide</span>", page)

    def test_the_badge_is_part_of_the_page_not_a_toast(self):
        """It renders in the served HTML and nothing dismisses it."""
        page = self._page(self.create_dm())
        self.assertIn('<div class="gm-mode-indicator" id="gm-mode-indicator">', page)
        indicator = page.split('id="gm-mode-indicator"')[1].split("</div>")[0]
        # ``onchange`` submits the mode form, which is a state change the
        # server renders back; nothing here hides or times out the strip.
        for dismissal in ("onclick", "setTimeout", "aria-live", "dismiss"):
            with self.subTest(dismissal=dismissal):
                self.assertNotIn(dismissal, indicator)

    def test_the_page_asks_a_game_master_what_the_aide_answers(self):
        """The chrome cannot invite the GM into a scene the aide won't run."""
        page = self._page(self.create_dm())
        self.assertIn("Ask a rules question", page)
        self.assertIn('placeholder="Ask the aide"', page)
        self.assertNotIn("What do you do?", page)
        self.assertNotIn("Describe your character concept", page)

    def test_a_players_page_keeps_its_own_words(self):
        page = self._page(self.create_player())
        self.assertIn("Describe your character concept", page)
        self.assertIn('placeholder="What do you do?"', page)
        self.assertNotIn("Ask a rules question", page)

    def test_the_badge_follows_the_same_role_test_as_the_dm_prompt(self):
        """The page cannot claim one mode while the model runs in the other."""
        dm = self.create_dm()
        self.login_as(dm)
        self.assertTrue(self.client.get(CHAT_URL).context["is_game_master"])
        self.assertEqual(get_system_message(dm)["content"], DM_SYSTEM_PROMPT)

    def test_a_players_page_and_prompt_agree_the_other_way(self):
        player = self.create_player()
        self.login_as(player)
        self.assertFalse(self.client.get(CHAT_URL).context["is_game_master"])
        self.assertEqual(get_system_message(player)["content"], SYSTEM_PROMPT)
