"""Literacy in a Games Master session (issue #742).

A character built in a session is a ``Character`` row, but the session could
neither see nor roll its literacy: the ledger the Characters pane and the
model read had no literacy line, the ``/character`` extraction schema named
no literacy roll, and the plan had no action for it. The roll goes through
the same service the sheet's control does (``services.roll_literacy``), held
to the same rules: a player rolls once, a Game Master may roll again.
"""

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from characters.constants import LITERACY_ROLL_MAXIMUM, LITERACY_ROLL_MINIMUM
from common.test_helpers import BaseTestCase

from .character_schema import (
    CHARACTER_FIELDS,
    character_json_schema,
    extraction_to_import_payload,
)
from .engine import MODE_PLAY, SECTION_PLAN_SCHEMA
from .ledger import DRAFT_DEFAULTS, DraftLedger
from .test_turn_engine_executor import ExecutorTestCase, plan
from .testing import add_character, recorded, session_with_character
from .turn_engine.plan import read_plan
from .turn_engine.policy import (
    LITERACY_RULING_REFUSAL,
    SETTLED_REFUSALS,
    TurnPolicy,
)
from .turn_engine.state import key_figures


def rolled(total: int):
    """Patch the literacy service's 3d6 to come up ``total``."""
    return patch(
        "characters.services.roll_3d6_logged",
        return_value=(total, f"3d6: [{total - 2}, 1, 1] = {total}"),
    )


class LiteracyLedgerTest(TestCase):
    """The ledger carries the literacy line the saved sheet already does."""

    def test_an_unrolled_character_reads_its_check_and_not_rolled(self):
        ledger = DraftLedger.from_draft({**DRAFT_DEFAULTS, "intelligence": 11})
        self.assertEqual(ledger.literacy, "3d6 ≤ INT − 2, Not rolled")

    def test_a_rolled_character_reads_its_roll_against_the_check(self):
        ledger = DraftLedger.from_draft(
            {
                **DRAFT_DEFAULTS,
                "intelligence": 11,
                "literacy_roll": 7,
                "literacy_rolled": True,
            }
        )
        self.assertEqual(ledger.literacy, "3d6 ≤ INT − 2, Literate (rolled 7 ≤ 11 − 2)")

    def test_a_game_masters_ruling_reads_with_its_provenance(self):
        """A session character ruled at Consult GM (#743) shows the ruling,
        not "Not rolled": the snapshot carries the ruling field."""
        ledger = DraftLedger.from_draft(
            {**DRAFT_DEFAULTS, "location": "Consult GM", "literacy_ruling": False}
        )
        self.assertEqual(
            ledger.literacy, "Consult GM, Illiterate (Game Master's ruling)"
        )

    def test_an_unrolled_check_is_left_out_of_the_state_block(self):
        """Listed as "Not rolled", the check drew the model into announcing
        it in replies about something else, and the Game Master's stated 18s
        went unsaid (dm_stated_attributes, 4/8 runs on 2026-09-28). A check
        nobody has rolled is not news; the model learns of it once it is."""
        from .ledger import draft_character

        figures = key_figures(draft_character(recorded("Gorn", score=10)))
        self.assertNotIn("literacy", figures)

    def test_the_state_block_tells_the_model_whether_a_character_can_read(self):
        values = {**recorded("Gorn", score=10), "literacy_roll": 12}
        from .ledger import draft_character

        figures = key_figures(draft_character(values))
        self.assertIn(
            "literacy: 3d6 ≤ INT − 2, Illiterate (rolled 12 > 10 − 2)", figures
        )


class LiteracyPanelTest(BaseTestCase):
    """The Characters pane shows a session-built character's literacy."""

    def test_the_pane_prints_the_literacy_line(self):
        user = self.create_user(username="literacy-panel")
        self.login_as(user)
        session = session_with_character(
            user, {**recorded("Gorn", score=10), "literacy_roll": 6}, name="S"
        )

        response = self.client.get(f"{reverse('gm:chat')}?session={session.pk}")

        self.assertContains(response, "gm-draft-literacy")
        self.assertContains(response, "3d6 ≤ INT − 2, Literate (rolled 6 ≤ 10 − 2)")


class LiteracySchemaTest(SimpleTestCase):
    """The ``/character`` extraction asks for the literacy roll as rolled."""

    def test_both_schemas_ask_for_the_literacy_roll_or_null(self):
        for for_game_master in (False, True):
            with self.subTest(for_game_master=for_game_master):
                field = character_json_schema(for_game_master=for_game_master)[
                    "properties"
                ]["literacy_roll"]
                self.assertEqual(field["type"], ["integer", "null"])
                self.assertEqual(
                    (field["minimum"], field["maximum"]),
                    (LITERACY_ROLL_MINIMUM, LITERACY_ROLL_MAXIMUM),
                )

    def test_the_roll_reaches_the_import_payload_by_its_export_name(self):
        """The importer reads export field names, so the schema uses one."""
        self.assertIn("literacy_roll", {field.name for field in CHARACTER_FIELDS})
        payload = extraction_to_import_payload({"name": "Gorn", "literacy_roll": 9})
        self.assertEqual(payload["data"]["literacy_roll"], 9)


class LiteracyPlanTest(SimpleTestCase):
    """The plan reads a literacy roll and the prompt teaches it."""

    policy = TurnPolicy.for_turn(MODE_PLAY, is_game_master=False)

    def test_roll_literacy_names_its_character(self):
        reading = read_plan(
            plan({"action": "roll_literacy", "character_id": 41}),
            roster_ids={41},
            policy=self.policy,
        )
        self.assertTrue(reading.ok, reading.errors)
        self.assertEqual(reading.actions[0].character_id, 41)
        self.assertFalse(reading.actions[0].refused)

    def test_roll_literacy_without_a_character_is_no_plan(self):
        reading = read_plan(
            plan({"action": "roll_literacy"}), roster_ids={41}, policy=self.policy
        )
        self.assertFalse(reading.ok)

    def test_the_plan_schema_teaches_the_roll(self):
        self.assertIn(
            '{"action": "roll_literacy", "character_id": 41}', SECTION_PLAN_SCHEMA
        )


class GameMasterLiteracyRollTest(ExecutorTestCase):
    """A Game Master's plan rolls literacy, and may roll it again."""

    def test_the_roll_is_recorded_and_may_be_made_again(self):
        character = add_character(self.session, recorded("Gorn", score=10))

        with rolled(9):
            first, *_rest = self.play(
                plan({"action": "roll_literacy", "character_id": character.pk})
            )
        with rolled(5):
            second, *_rest = self.play(
                plan({"action": "roll_literacy", "character_id": character.pk})
            )

        self.assertTrue(first.results[0].ran, first.results[0].error)
        self.assertIn(
            "rolled the literacy check (3d6 ≤ INT − 2)", first.results[0].note
        )
        self.assertTrue(second.results[0].ran, second.results[0].error)
        character.refresh_from_db()
        self.assertEqual(
            (character.literacy_roll, character.literacy_rolled), (5, True)
        )
        self.assertEqual(character.history[-1]["field"], "literacy_roll")

    def test_consult_gm_is_refused_with_the_services_reason(self):
        character = add_character(
            self.session, {**recorded("Gorn", score=10), "location": "Consult GM"}
        )

        outcome, *_rest = self.play(
            plan({"action": "roll_literacy", "character_id": character.pk})
        )

        self.assertFalse(outcome.results[0].ran)
        self.assertIn("publish no literacy check", outcome.results[0].error)


class PlayerLiteracyRollTest(ExecutorTestCase):
    """A player's plan rolls literacy once, as on the sheet."""

    game_master = False
    mode = MODE_PLAY

    def test_a_player_rolls_once_and_the_second_is_refused(self):
        character = add_character(self.session, recorded("Gorn", score=10))

        with rolled(12):
            outcome, _yielded, model, _turn = self.play(
                plan(
                    {"action": "roll_literacy", "character_id": character.pk},
                    {"action": "roll_literacy", "character_id": character.pk},
                )
            )

        self.assertEqual([result.ran for result in outcome.results], [True, False])
        self.assertIn("only a Game Master can roll again", outcome.results[1].error)
        character.refresh_from_db()
        self.assertEqual(character.literacy_roll, 12)
        self.assertIn(
            "Illiterate (rolled 12 > 10 − 2)", model.requests[1][-1]["content"]
        )


class LiteracyRulingPlanTest(SimpleTestCase):
    """``rule_literacy``: the Game Master's ruling at Consult GM (issue #743)."""

    def read(self, entry: dict, *, is_game_master: bool):
        return read_plan(
            plan(entry),
            roster_ids={41},
            policy=TurnPolicy.for_turn(MODE_PLAY, is_game_master=is_game_master),
        )

    def test_the_ruling_is_true_or_false(self):
        for literate in (True, False):
            with self.subTest(literate=literate):
                reading = self.read(
                    {
                        "action": "rule_literacy",
                        "character_id": 41,
                        "literate": literate,
                    },
                    is_game_master=True,
                )
                self.assertTrue(reading.ok, reading.errors)
                self.assertIs(reading.actions[0].literate, literate)
                self.assertIs(reading.actions[0].as_payload()["literate"], literate)

    def test_anything_else_is_no_plan(self):
        for said in ("yes", 1, None):
            with self.subTest(literate=said):
                reading = self.read(
                    {"action": "rule_literacy", "character_id": 41, "literate": said},
                    is_game_master=True,
                )
                self.assertFalse(reading.ok)

    def test_a_player_is_refused_for_good(self):
        reading = self.read(
            {"action": "rule_literacy", "character_id": 41, "literate": True},
            is_game_master=False,
        )
        self.assertTrue(reading.ok, reading.errors)
        self.assertEqual(reading.actions[0].refusal, LITERACY_RULING_REFUSAL)
        self.assertIn(LITERACY_RULING_REFUSAL, SETTLED_REFUSALS)

    def test_the_plan_schema_teaches_the_ruling(self):
        self.assertIn(
            '{"action": "rule_literacy", "character_id": 41, "literate": true}',
            SECTION_PLAN_SCHEMA,
        )


class GameMasterLiteracyRulingTest(ExecutorTestCase):
    """A Game Master's plan records a ruling through the sheet's service."""

    def test_the_ruling_is_recorded_with_its_provenance(self):
        character = add_character(
            self.session, {**recorded("Gorn", score=10), "location": "Consult GM"}
        )

        outcome, _yielded, model, _turn = self.play(
            plan(
                {
                    "action": "rule_literacy",
                    "character_id": character.pk,
                    "literate": True,
                }
            )
        )

        self.assertTrue(outcome.results[0].ran, outcome.results[0].error)
        character.refresh_from_db()
        self.assertIs(character.literacy_ruling, True)
        self.assertIn(
            "literacy: Consult GM, Literate (Game Master's ruling)",
            model.requests[1][-1]["content"],
        )

    def test_an_origin_with_a_check_is_refused(self):
        character = add_character(self.session, recorded("Gorn", score=10))

        outcome, *_rest = self.play(
            plan(
                {
                    "action": "rule_literacy",
                    "character_id": character.pk,
                    "literate": False,
                }
            )
        )

        self.assertFalse(outcome.results[0].ran)
        self.assertIn("has a literacy check", outcome.results[0].error)


class PlayerLiteracyRulingTest(ExecutorTestCase):
    """A player's plan cannot rule, and nothing is written."""

    game_master = False
    mode = MODE_PLAY

    def test_the_ruling_is_refused_and_the_record_is_untouched(self):
        character = add_character(
            self.session, {**recorded("Gorn", score=10), "location": "Consult GM"}
        )

        outcome, *_rest = self.play(
            plan(
                {
                    "action": "rule_literacy",
                    "character_id": character.pk,
                    "literate": True,
                }
            )
        )

        self.assertFalse(outcome.results[0].ran)
        self.assertEqual(outcome.results[0].error, LITERACY_RULING_REFUSAL)
        character.refresh_from_db()
        self.assertIsNone(character.literacy_ruling)
