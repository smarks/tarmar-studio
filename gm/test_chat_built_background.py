"""A character built in a Games Master session rolls its background (issue #754).

``gm/roster.py:start_character`` rolled appearance, height and weight only, and
saved the model's defaults for the rest: Commoner / Laborer, Village,
Moderate, 100 coin, and no skill from the Origin. Nobody rolled them and the
dice log did not say so (the rescan's
``PlayerChatBackgroundTest.test_chat_built_character_has_no_location_skill``).

Now the session rolls origin, provenance and wealth when it makes the
character, through the services the sheet's roll controls use and in the same
dice-log lines as the creation page, and grants the Origin's skill. A
``roll_background`` action rolls one that is missing: once for a player, and
again whenever a Game Master asks (Spencer's rulings on #749).
"""

from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from characters.models import Character
from characters.services import BACKGROUND_ALREADY_ROLLED
from common.test_helpers import BaseTestCase

from . import roster
from .engine import MODE_PLAY, SECTION_PLAN_SCHEMA
from .ledger import DRAFT_DEFAULTS, DraftLedger, draft_character
from .models import GMSession
from .test_turn_engine_executor import ExecutorTestCase, plan
from .testing import add_character, recorded, session_with_character
from .turn_engine.executor import ACTION_STATUS
from .turn_engine.plan import read_plan
from .turn_engine.policy import ACTION_NAMES, TurnPolicy
from .turn_engine.state import key_figures

#: d100 faces for provenance (class, detail), origin and wealth, in the order
#: the creation page rolls them: Commoner, a Laborer-range detail, Rural,
#: Moderate.
RURAL_MODERATE_COMMONER = (40, 3, 80, 50)

#: The same four for a City Merchant: merchant class, a detail, City, then
#: Merchant wealth and its 100 + d100 coin.
CITY_MERCHANT = (20, 3, 10, 80, 25)


def d100(*faces: int):
    """Patch every percentile roll to come up ``faces``, in order."""
    return patch("characters.dice.roll_d100", side_effect=list(faces))


def unrolled_background() -> dict:
    """A character started before #754: attributes in, background never rolled."""
    return {
        **recorded("Wren", score=10),
        "provenance_recorded": False,
        "location_recorded": False,
        "wealth_recorded": False,
    }


class StartCharacterRollsTheBackgroundTest(BaseTestCase):
    """``start_character`` makes a record like the creation page's."""

    def test_origin_provenance_and_wealth_are_rolled_and_logged(self):
        session = GMSession.objects.create(user=self.create_user(), name="s")

        with d100(*RURAL_MODERATE_COMMONER):
            character = roster.start_character(session, "Wren")

        character.refresh_from_db()
        self.assertEqual(
            (character.provenance_class, character.location, character.wealth_level),
            ("commoner", "Rural", "Moderate"),
        )
        self.assertTrue(character.provenance_recorded)
        self.assertTrue(character.location_recorded)
        self.assertTrue(character.wealth_recorded)
        self.assertEqual(character.skills, {"survival": 1})
        log = character.dice_log.splitlines()
        # The Fatigue roll is made with the physical features, as the
        # creation page makes it (#859): this list pinned the older design,
        # which left it for a later plan to ask for.
        self.assertEqual(
            [line.split(":")[0] for line in log],
            [
                "Appearance",
                "Height",
                "Weight",
                "Fatigue",
                "Provenance",
                "Location",
                "Location effects",
                "Wealth",
            ],
        )
        self.assertEqual(log[5], "Location: d100=80 -> Rural")
        self.assertEqual(log[3], f"Fatigue: 2d6: {log[3].split('2d6: ')[1]}")
        self.assertIsNotNone(character.fatigue_roll)

    def test_a_merchant_rolls_its_coin_once(self):
        session = GMSession.objects.create(user=self.create_user(), name="s")

        with d100(*CITY_MERCHANT):
            character = roster.start_character(session, "Wren")

        character.refresh_from_db()
        self.assertEqual(
            (character.wealth_level, character.starting_coin), ("Merchant", 125)
        )
        self.assertEqual(character.skills, {"street smarts": 1})


class PlayerChatBuiltBackgroundTest(ExecutorTestCase):
    """A player's session: rolled at creation, and never a second time."""

    game_master = False
    mode = MODE_PLAY

    def test_a_chat_built_character_has_its_origins_skill(self):
        """The rescan's reproduction, asserted the right way round."""
        with d100(*RURAL_MODERATE_COMMONER):
            outcome, *_rest = self.play(
                plan(
                    {
                        "action": "roll_attributes",
                        "new_character": {"name": "Wren", "label": "Wren"},
                    }
                )
            )

        self.assertTrue(outcome.results[0].ran, outcome.results[0].error)
        wren = Character.objects.get(name="Wren")
        self.assertEqual(wren.location, "Rural")
        self.assertEqual(wren.skills, {"survival": 1})
        # The narration is told what was rolled for the new character.
        self.assertIn(
            "background: origin Rural; provenance Commoner", outcome.results[0].result
        )

    def test_a_player_cannot_roll_a_chat_built_background_again(self):
        with d100(*RURAL_MODERATE_COMMONER):
            self.play(
                plan({"action": "roll_attributes", "new_character": {"name": "Wren"}})
            )
        wren = Character.objects.get(name="Wren")

        outcome, *_rest = self.play(
            plan({"action": "roll_background", "character_id": wren.pk})
        )

        self.assertFalse(outcome.results[0].ran)
        self.assertIn(BACKGROUND_ALREADY_ROLLED, outcome.results[0].error)
        wren.refresh_from_db()
        self.assertEqual((wren.location, wren.wealth_level), ("Rural", "Moderate"))

    def test_a_background_asked_for_as_the_character_is_made_is_not_rolled_twice(
        self,
    ):
        """The live model lists the creation rolls after making a character
        (the #754 board: roll_attributes, roll_fatigue, roll_background). The
        background was rolled as the character was made; the second action
        says so rather than rolling again or being refused."""
        with d100(*RURAL_MODERATE_COMMONER):
            outcome, *_rest = self.play(
                plan(
                    {"action": "roll_attributes", "new_character": {"name": "Wren"}},
                    {"action": "roll_background", "new_character_ref": "Wren"},
                )
            )

        made, asked = outcome.results
        self.assertTrue(made.ran, made.error)
        self.assertTrue(asked.ran, asked.error)
        self.assertIn(
            "background rolled when the character was made: origin Rural", asked.note
        )
        wren = Character.objects.get(name="Wren")
        self.assertEqual(wren.dice_log.count("Provenance: d100="), 1)

    def test_a_player_rolls_a_background_nobody_rolled(self):
        """A character a session started before #754 has none: its player
        rolls it once, as on the sheet."""
        character = add_character(self.session, unrolled_background())

        with d100(*RURAL_MODERATE_COMMONER):
            outcome, *_rest = self.play(
                plan({"action": "roll_background", "character_id": character.pk})
            )

        self.assertTrue(outcome.results[0].ran, outcome.results[0].error)
        character.refresh_from_db()
        self.assertTrue(character.location_recorded)
        self.assertEqual(character.skills, {"survival": 1})
        self.assertIn("rolled provenance", outcome.results[0].note)


class PlayerLiteracyWaitsForTheOriginTest(ExecutorTestCase):
    """The chat holds a player's literacy roll to a recorded Origin (the
    coordinator's ruling on the #754 review)."""

    game_master = False
    mode = MODE_PLAY

    def test_a_player_is_told_to_roll_or_choose_the_origin_first(self):
        character = add_character(self.session, unrolled_background())

        outcome, *_rest = self.play(
            plan({"action": "roll_literacy", "character_id": character.pk})
        )

        self.assertFalse(outcome.results[0].ran)
        self.assertIn("roll or choose the Origin first", outcome.results[0].error)
        character.refresh_from_db()
        self.assertFalse(character.literacy_rolled)


class GameMasterLiteracyBeforeTheOriginTest(ExecutorTestCase):
    """A Game Master's literacy roll is not held to it."""

    mode = MODE_PLAY

    def test_a_game_master_rolls_literacy_before_the_origin(self):
        character = add_character(self.session, unrolled_background())

        outcome, *_rest = self.play(
            plan({"action": "roll_literacy", "character_id": character.pk})
        )

        self.assertTrue(outcome.results[0].ran, outcome.results[0].error)


class GameMasterChatBuiltBackgroundTest(ExecutorTestCase):
    """A Game Master's session may roll the background again."""

    mode = MODE_PLAY

    def test_a_game_master_rerolls_and_the_origins_skill_moves(self):
        character = add_character(
            self.session,
            {
                **recorded("Wren", score=10),
                "location": "City",
                "skills": {"street smarts": 1},
                "origin_skill": "street smarts",
                "provenance_class": "merchant",
                "provenance_detail": "Trader",
            },
        )

        with d100(*RURAL_MODERATE_COMMONER):
            outcome, *_rest = self.play(
                plan({"action": "roll_background", "character_id": character.pk})
            )

        self.assertTrue(outcome.results[0].ran, outcome.results[0].error)
        character.refresh_from_db()
        self.assertEqual(
            (character.provenance_class, character.location), ("commoner", "Rural")
        )
        self.assertEqual(character.skills, {"survival": 1})
        self.assertIn("rolled the origin", outcome.results[0].note)
        self.assertIn("rolled wealth", outcome.results[0].note)

    def test_set_background_moves_the_origins_skill(self):
        character = add_character(
            self.session,
            {
                **recorded("Wren", score=10),
                "location": "Rural",
                "skills": {"survival": 1},
                "origin_skill": "survival",
            },
        )

        outcome, *_rest = self.play(
            plan(
                {
                    "action": "set_background",
                    "character_id": character.pk,
                    "location": "City",
                }
            )
        )

        self.assertTrue(outcome.results[0].ran, outcome.results[0].error)
        character.refresh_from_db()
        self.assertEqual(character.skills, {"street smarts": 1})


class BackgroundPlanTest(SimpleTestCase):
    """The plan reads ``roll_background`` and the prompt teaches it."""

    policy = TurnPolicy.for_turn(MODE_PLAY, is_game_master=False)

    def test_roll_background_names_its_character(self):
        reading = read_plan(
            plan({"action": "roll_background", "character_id": 41}),
            roster_ids={41},
            policy=self.policy,
        )
        self.assertTrue(reading.ok, reading.errors)
        self.assertEqual(reading.actions[0].character_id, 41)
        self.assertFalse(reading.actions[0].refused)

    def test_roll_background_does_not_start_a_character(self):
        """A new character's background is rolled when it is made."""
        reading = read_plan(
            plan({"action": "roll_background", "new_character": {"name": "Wren"}}),
            roster_ids=set(),
            policy=self.policy,
        )
        self.assertFalse(reading.ok)

    def test_the_action_is_listed_named_and_taught(self):
        self.assertIn("roll_background", ACTION_NAMES)
        self.assertEqual(ACTION_STATUS["roll_background"], "rolling the background")
        self.assertIn(
            '{"action": "roll_background", "character_id": 41}', SECTION_PLAN_SCHEMA
        )


class BackgroundLedgerTest(SimpleTestCase):
    """The pane and the state block say what the background is, and whether
    it was rolled."""

    def test_the_ledger_carries_the_background_and_its_status(self):
        ledger = DraftLedger.from_draft(
            {**DRAFT_DEFAULTS, "location": "Rural", "wealth_recorded": False}
        )
        self.assertTrue(ledger.location_recorded)
        self.assertTrue(ledger.provenance_recorded)
        self.assertFalse(ledger.wealth_recorded)
        self.assertEqual(
            ledger.background,
            "origin Rural; provenance Commoner (Laborer); wealth Moderate, 100 "
            "starting coin (not rolled)",
        )

    def test_the_state_block_lists_the_background(self):
        figures = key_figures(draft_character(recorded("Wren", score=10)))
        self.assertIn(
            "background: origin Village; provenance Commoner (Laborer); wealth "
            "Moderate, 100 starting coin",
            figures,
        )

    def test_the_state_block_marks_what_is_not_rolled(self):
        figures = key_figures(draft_character(unrolled_background()))
        self.assertIn("origin Village (not rolled)", figures)


class BackgroundPanelTest(BaseTestCase):
    """The Characters pane marks a background nobody rolled."""

    def test_the_pane_marks_an_unrolled_background(self):
        user = self.create_user(username="background-panel")
        self.login_as(user)
        session = session_with_character(user, unrolled_background(), name="S")

        response = self.client.get(f"{reverse('gm:chat')}?session={session.pk}")

        self.assertContains(response, 'class="gm-draft-background"')
        self.assertContains(response, "Village (not rolled)")
        self.assertContains(response, "100 coin (not rolled)")
