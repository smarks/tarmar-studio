"""Reading and checking a JSON plan (issue #590)."""

import json

from django.test import SimpleTestCase

from .engine import MODE_AIDE, MODE_PLAY
from .turn_engine.messages import (
    NOTHING_RAN,
    PLAN_ONLY,
    PLANNING_GUIDANCE,
    PLANNING_RULES,
    narrate_message,
    plan_message,
    replan_message,
)
from .turn_engine.plan import MAX_ACTIONS, NewCharacter, PlannedAction, read_plan
from .turn_engine.policy import (
    AIDE_MANNER,
    AIDE_PLANNING_GUIDANCE,
    AIDE_UNASKED_ROLL_REFUSAL,
    GAME_MASTER_ONLY_REFUSAL,
    PLAYER_STATED_SCORES_REFUSAL,
    TurnPolicy,
)

GAME_MASTER = TurnPolicy.for_turn(MODE_AIDE, is_game_master=True)
PLAYER = TurnPolicy.for_turn(MODE_PLAY, is_game_master=False)

#: The measured table-fact question from issue #598.
TABLE_FACT = (
    "Last night my players killed the innkeeper at the Broken Wheel. Did the "
    "town guard see them do it?"
)


def plan(*actions: dict) -> str:
    return json.dumps({"actions": list(actions)})


class ReadPlanTest(SimpleTestCase):
    def read(self, raw: str, policy: TurnPolicy = GAME_MASTER, ids=(41, 42)):
        return read_plan(raw, roster_ids=set(ids), policy=policy)

    def test_an_empty_plan_is_a_plan(self):
        reading = self.read('{"actions": []}')
        self.assertTrue(reading.ok)
        self.assertEqual(reading.actions, ())

    def test_a_fenced_plan_is_read(self):
        reading = self.read('```json\n{"actions": []}\n```')
        self.assertTrue(reading.ok)

    def test_prose_is_an_error_never_a_guess(self):
        reading = self.read("Sure! I will record the twins as 18s.")
        self.assertFalse(reading.ok)
        self.assertIn("not JSON", reading.errors[0])
        self.assertEqual(reading.actions, ())

    def test_an_empty_reply_is_an_error(self):
        self.assertIn("empty", self.read("   ").errors[0])

    def test_json_without_an_actions_list_is_an_error(self):
        for raw in ("[]", '{"plan": []}', '{"actions": {}}'):
            with self.subTest(raw=raw):
                self.assertIn('"actions" list', self.read(raw).errors[0])

    def test_twins_with_stated_scores_are_one_plan(self):
        reading = self.read(
            plan(
                {
                    "action": "record_attributes",
                    "new_character": {"name": "Twain", "label": "the brother"},
                    "all": 18,
                },
                {
                    "action": "record_attributes",
                    "new_character": {"name": "Thread"},
                    "all": 18,
                },
                {
                    "action": "play_years",
                    "character_id": "Twain",
                    "track": "Campaigner",
                    "years": 5,
                },
            )
        )
        self.assertTrue(reading.ok, reading.errors)
        first, second, years = reading.actions
        self.assertEqual(first.new_character, NewCharacter("Twain", "the brother"))
        self.assertEqual(first.all_score, 18)
        self.assertEqual(second.new_character, NewCharacter("Thread", ""))
        self.assertEqual(years.new_character_ref, "Twain")
        self.assertEqual(years.track, "campaigner")
        self.assertEqual(years.years, 5)
        self.assertFalse(any(action.refused for action in reading.actions))

    def test_scores_are_kept_in_sheet_order_by_full_name(self):
        reading = self.read(
            plan(
                {
                    "action": "record_attributes",
                    "character_id": 41,
                    "scores": {"Dexterity": 12, "strength": 22},
                }
            )
        )
        self.assertEqual(
            reading.actions[0].scores, (("strength", 22), ("dexterity", 12))
        )

    def test_bad_scores_are_errors(self):
        cases = {
            "abbreviated": {
                "action": "record_attributes",
                "character_id": 41,
                "scores": {"STR": 18},
            },
            "not a number": {
                "action": "record_attributes",
                "character_id": 41,
                "scores": {"strength": "18"},
            },
            "both": {
                "action": "record_attributes",
                "character_id": 41,
                "all": 18,
                "scores": {"strength": 1},
            },
            "neither": {"action": "record_attributes", "character_id": 41},
            "empty": {"action": "record_attributes", "character_id": 41, "scores": {}},
            "all out of range": {
                "action": "record_attributes",
                "character_id": 41,
                "all": 0,
            },
        }
        for name, action in cases.items():
            with self.subTest(name):
                self.assertFalse(self.read(plan(action)).ok)

    def test_new_character_needs_a_name_or_label_and_no_id(self):
        cases = [
            {"action": "roll_attributes", "new_character": {}},
            {"action": "roll_attributes", "new_character": "Twain"},
            {"action": "roll_attributes", "new_character": {"name": 3}},
            {
                "action": "roll_attributes",
                "new_character": {"name": "A"},
                "character_id": 41,
            },
        ]
        for action in cases:
            with self.subTest(action=action):
                self.assertFalse(self.read(plan(action)).ok)

    def test_an_unknown_action_is_an_error_naming_the_actions(self):
        reading = self.read(
            plan({"action": "add_experience_years", "character_id": 41})
        )
        self.assertIn("unknown action", reading.errors[0])
        self.assertIn("play_years", reading.errors[0])

    def test_an_action_that_is_not_an_object_is_an_error(self):
        self.assertIn("JSON object", self.read('{"actions": ["roll"]}').errors[0])

    def test_too_many_actions_is_an_error(self):
        actions = [{"action": "roll_fatigue", "character_id": 41}] * (MAX_ACTIONS + 1)
        self.assertIn("at most", self.read(plan(*actions)).errors[0])

    def test_every_field_error_is_listed(self):
        reading = self.read(
            plan(
                {
                    "action": "play_years",
                    "character_id": 41,
                    "track": "pirate",
                    "years": 40,
                },
                {"action": "roll", "dice": "a handful"},
            )
        )
        self.assertEqual(len(reading.errors), 3)
        self.assertTrue(reading.errors[0].startswith("action 1 (play_years)"))

    def test_a_character_id_must_be_an_id_or_a_new_character_named_earlier(self):
        for value in ("Twain", 4.5, True, None):
            with self.subTest(value=value):
                self.assertFalse(
                    self.read(
                        plan({"action": "roll_fatigue", "character_id": value})
                    ).ok
                )

    def test_an_unknown_id_is_refused_not_an_error(self):
        reading = self.read(plan({"action": "roll_fatigue", "character_id": 7}))
        self.assertTrue(reading.ok)
        self.assertIn("no character with id 7", reading.actions[0].refusal)

    def test_a_players_stated_scores_are_refused(self):
        reading = self.read(
            plan(
                {
                    "action": "record_attributes",
                    "new_character": {"name": "Bob"},
                    "all": 18,
                }
            ),
            policy=PLAYER,
        )
        self.assertTrue(reading.ok)
        self.assertEqual(reading.actions[0].refusal, PLAYER_STATED_SCORES_REFUSAL)

    def test_a_players_rolled_attributes_are_not_refused(self):
        reading = self.read(
            plan({"action": "roll_attributes", "new_character": {"name": "Bob"}}),
            policy=PLAYER,
        )
        self.assertFalse(reading.actions[0].refused)

    def test_reviving_and_removing_are_game_master_only(self):
        for action in ("revive_character", "remove_character"):
            with self.subTest(action):
                player = self.read(
                    plan({"action": action, "character_id": 41}), policy=PLAYER
                )
                master = self.read(plan({"action": action, "character_id": 41}))
                self.assertEqual(player.actions[0].refusal, GAME_MASTER_ONLY_REFUSAL)
                self.assertFalse(master.actions[0].refused)

    def test_every_action_reads(self):
        reading = self.read(
            plan(
                {"action": "roll_fatigue", "character_id": 41},
                {
                    "action": "spend_skill_points",
                    "character_id": 41,
                    "skill": "Sword",
                    "points": 2,
                },
                {"action": "add_equipment", "character_id": 41, "item": "rope"},
                {
                    "action": "roll",
                    "dice": "3d6 x6",
                    "purpose": "attributes",
                    "character_id": None,
                },
                {"action": "roll", "dice": "1d20 + 4"},
                {"action": "rename_character", "character_id": 42, "name": " Thread "},
                {
                    "action": "set_background",
                    "character_id": 42,
                    "provenance": "merchant",
                    "wealth": "Rich",
                },
                {"action": "ask_gm", "question": "Which track?"},
            )
        )
        self.assertTrue(reading.ok, reading.errors)
        fatigue, skill, gear, roll, attack, rename, background, ask = reading.actions
        self.assertEqual((skill.skill, skill.points), ("Sword", 2))
        self.assertEqual((gear.item, gear.quantity), ("rope", 1))
        self.assertEqual((roll.dice, roll.times, roll.character_id), ("3d6", 6, None))
        self.assertEqual(attack.dice, "1d20+4")
        self.assertEqual(rename.name, "Thread")
        self.assertEqual(
            background.background,
            (("provenance_class", "merchant"), ("wealth_level", "Rich")),
        )
        self.assertEqual(ask.question, "Which track?")

    def test_text_fields_are_checked(self):
        cases = [
            {"action": "rename_character", "character_id": 41, "name": ""},
            {"action": "rename_character", "character_id": 41, "name": "x" * 500},
            {"action": "set_background", "character_id": 41},
            {"action": "ask_gm"},
            {"action": "roll", "dice": "3d6 x9"},
            {"action": "roll", "dice": "3d6", "purpose": 4},
        ]
        for action in cases:
            with self.subTest(action=action):
                self.assertFalse(self.read(plan(action)).ok)

    def test_payload_keeps_only_what_was_set(self):
        action = PlannedAction(
            action="record_attributes",
            new_character=NewCharacter("Twain", "the brother"),
            scores=(("strength", 18),),
            background=(("location", "City"),),
            times=2,
            refusal="no",
        )
        self.assertEqual(
            action.as_payload(),
            {
                "action": "record_attributes",
                "refusal": "no",
                "times": 2,
                "new_character": {"name": "Twain", "label": "the brother"},
                "scores": {"strength": 18},
                "background": {"location": "City"},
            },
        )


class TurnMessagesTest(SimpleTestCase):
    def test_the_plan_message_ends_with_the_latest_message(self):
        message = plan_message("STATE", "twins, all 18s", GAME_MASTER)
        self.assertEqual(message["role"], "user")
        self.assertTrue(message["content"].startswith("STATE\n\n"))
        self.assertTrue(message["content"].endswith("Latest message: twins, all 18s"))

    def test_the_replan_message_lists_the_errors(self):
        content = replan_message(["one", "two"])["content"]
        self.assertIn("- one\n- two", content)

    def test_the_narrate_message_carries_results_and_manner(self):
        content = narrate_message("STATE", ["a", "b"], "hello", GAME_MASTER)["content"]
        self.assertIn("Server results: \n- a\n- b", content)
        self.assertIn(GAME_MASTER.manner, content)
        self.assertNotIn("[[", content)
        empty = narrate_message("STATE", [], "hello", PLAYER)["content"]
        self.assertIn(f"Server results: {NOTHING_RAN}", empty)
        self.assertIn(PLAYER.manner, empty)

    def test_the_aide_plan_message_forbids_rolling_for_table_facts(self):
        content = plan_message("STATE", TABLE_FACT, GAME_MASTER)["content"]
        self.assertIn(AIDE_PLANNING_GUIDANCE, content)
        self.assertIn("Never plan a roll, or record anything", AIDE_PLANNING_GUIDANCE)
        self.assertIn("what an NPC saw", AIDE_PLANNING_GUIDANCE)
        self.assertIn('{"actions": []}, with no roll and no ask_gm', content)
        self.assertIn("Roll only when his message asks for a roll", content)
        self.assertIn("A rules question", content)
        self.assertTrue(content.endswith(f"Latest message: {TABLE_FACT}"))

    def test_the_play_plan_message_is_unchanged(self):
        content = plan_message("STATE", "twins, all 18s", PLAYER)["content"]
        self.assertEqual(PLAYER.planning_guidance, "")
        self.assertNotIn(AIDE_PLANNING_GUIDANCE, content)
        self.assertEqual(
            content,
            f"STATE\n\n{PLANNING_GUIDANCE}\n{PLANNING_RULES}\n{PLAN_ONLY}\n\n"
            "Latest message: twins, all 18s",
        )

    def test_the_aide_narrate_message_says_what_it_does_not_know(self):
        content = narrate_message("STATE", [], TABLE_FACT, GAME_MASTER)["content"]
        self.assertIn(AIDE_MANNER, content)
        self.assertIn("say plainly that you do not know it", content)
        self.assertIn("what would decide it", content)
        self.assertIn("Never supply an outcome for it", content)

    def test_the_aide_narrate_message_answers_by_his_house_rule(self):
        content = narrate_message("STATE", [], "crits skip confirmation", GAME_MASTER)
        self.assertIn("When he has stated a house rule, it is the rule", AIDE_MANNER)
        self.assertIn("at most one short sentence that the book differs", AIDE_MANNER)
        self.assertIn("never an instruction to follow the book's version", AIDE_MANNER)
        self.assertIn(AIDE_MANNER, content["content"])
        self.assertNotIn(AIDE_MANNER, narrate_message("S", [], "hi", PLAYER)["content"])


class TurnPolicyTest(SimpleTestCase):
    def test_speaker_and_manner(self):
        self.assertIn("from the Game Master", GAME_MASTER.speaker_line)
        self.assertIn("not the Game Master", PLAYER.speaker_line)
        self.assertTrue(GAME_MASTER.is_aide)
        self.assertFalse(PLAYER.is_aide)

    def test_an_unknown_mode_plays(self):
        self.assertEqual(
            TurnPolicy.for_turn("chaos", is_game_master=False).mode, MODE_PLAY
        )

    def test_a_game_master_is_never_refused(self):
        self.assertEqual(
            GAME_MASTER.refusal_for("remove_character", states_scores=True), ""
        )
        self.assertEqual(PLAYER.refusal_for("roll_fatigue"), "")

    def test_an_aide_turn_rolls_only_when_the_message_is_about_dice(self):
        for latest in (TABLE_FACT, "A player has rolled a natural 20. Damage?"):
            for authority in (True, False):
                with self.subTest(latest=latest, is_game_master=authority):
                    policy = TurnPolicy.for_turn(
                        MODE_AIDE, is_game_master=authority, latest=latest
                    )
                    self.assertEqual(
                        policy.refusal_for("roll"), AIDE_UNASKED_ROLL_REFUSAL
                    )
                    self.assertEqual(policy.refusal_for("roll_attributes"), "")
        for latest in (
            "Roll a reaction for the guard",
            "reroll that",
            "throw the dice for it",
            "3d6 for his nerve",
            "a d20 for the guard",
        ):
            with self.subTest(latest=latest):
                policy = TurnPolicy.for_turn(
                    MODE_AIDE, is_game_master=True, latest=latest
                )
                self.assertEqual(policy.refusal_for("roll"), "")

    def test_every_inflection_of_asking_for_dice_counts(self):
        """Issue #794: "rolling", "rerolling" and a singular die were missed."""
        for latest in (
            "Rolling initiative for the goblins",
            "I throw a die for it",
            "rerolling that",
            "roll for initiative",
            "rolls for the guard",
            "throw the die",
            "2 die for the damage",
        ):
            with self.subTest(latest=latest):
                policy = TurnPolicy.for_turn(
                    MODE_AIDE, is_game_master=True, latest=latest
                )
                self.assertEqual(policy.refusal_for("roll"), "")
        for latest in (
            "He rolled a 14 on his attack.",
            "Would the guard die from that fall?",
            "If they die here, what happens to the box?",
        ):
            with self.subTest(latest=latest):
                policy = TurnPolicy.for_turn(
                    MODE_AIDE, is_game_master=True, latest=latest
                )
                self.assertEqual(policy.refusal_for("roll"), AIDE_UNASKED_ROLL_REFUSAL)

    def test_an_aide_turn_rolls_no_check_or_save_nobody_asked_for(self):
        """#906: skill_check and survival_save escaped the unasked-roll rule."""
        unasked = "How is Gorn doing after the fall?"
        for authority in (True, False):
            policy = TurnPolicy.for_turn(
                MODE_AIDE, is_game_master=authority, latest=unasked
            )
            for action in ("roll", "skill_check", "survival_save"):
                with self.subTest(action=action, is_game_master=authority):
                    self.assertEqual(
                        policy.refusal_for(action), AIDE_UNASKED_ROLL_REFUSAL
                    )
        for latest, action in (
            ("Check Gorn's stealth against 12", "skill_check"),
            ("Roll Gorn's survival save", "survival_save"),
            ("Gorn makes his saving throw", "survival_save"),
            ("roll a stealth check for Gorn", "skill_check"),
            ("Roll 3d6 for his nerve", "roll"),
        ):
            with self.subTest(latest=latest):
                policy = TurnPolicy.for_turn(
                    MODE_AIDE, is_game_master=True, latest=latest
                )
                self.assertEqual(policy.refusal_for(action), "")
        # The bare words ask for no dice (second review of #906).
        for latest in (
            "check the roster",
            "Gorn is saving up for a horse",
            "Can you check who is still in the party?",
            "They save the merchant from the bandits",
        ):
            with self.subTest(latest=latest):
                policy = TurnPolicy.for_turn(
                    MODE_AIDE, is_game_master=True, latest=latest
                )
                for action in ("roll", "skill_check", "survival_save"):
                    self.assertEqual(
                        policy.refusal_for(action), AIDE_UNASKED_ROLL_REFUSAL
                    )
        for latest, action in (
            ("A skill check for Gorn", "skill_check"),
            ("his perception check, please", "skill_check"),
            ("Gorn makes a save vs poison", "survival_save"),
            ("a saving throw for Gorn", "survival_save"),
        ):
            with self.subTest(latest=latest):
                policy = TurnPolicy.for_turn(
                    MODE_AIDE, is_game_master=True, latest=latest
                )
                self.assertEqual(policy.refusal_for(action), "")
        # A check asked for is not a raw roll (#598 still holds).
        checked = TurnPolicy.for_turn(
            MODE_AIDE, is_game_master=True, latest="check his stealth against 12"
        )
        self.assertEqual(checked.refusal_for("roll"), AIDE_UNASKED_ROLL_REFUSAL)
        self.assertEqual(
            checked.refusal_for("survival_save"), AIDE_UNASKED_ROLL_REFUSAL
        )
        # A play turn is not gated.
        playing = TurnPolicy.for_turn(MODE_PLAY, is_game_master=True, latest=unasked)
        for action in ("roll", "skill_check", "survival_save"):
            self.assertEqual(playing.refusal_for(action), "")

    def test_a_play_turn_rolls_whatever_the_message_says(self):
        policy = TurnPolicy.for_turn(MODE_PLAY, is_game_master=True, latest=TABLE_FACT)
        self.assertEqual(policy.refusal_for("roll"), "")
        self.assertEqual(PLAYER.refusal_for("roll"), "")

    def test_an_aide_plan_that_rolls_for_a_table_fact_is_refused(self):
        policy = TurnPolicy.for_turn(MODE_AIDE, is_game_master=True, latest=TABLE_FACT)
        reading = read_plan(
            plan({"action": "roll", "dice": "2d6", "purpose": "did the guard see"}),
            roster_ids=(),
            policy=policy,
        )
        self.assertTrue(reading.ok)
        (action,) = reading.actions
        self.assertEqual(action.refusal, AIDE_UNASKED_ROLL_REFUSAL)


class NamingACharacterThePlanStartedTest(SimpleTestCase):
    """Both keys name a character started earlier in the same plan (#608).

    ``PlannedAction.as_payload`` writes the name into ``new_character_ref``,
    but the reader only ever looked at ``character_id``, so a plan could not
    be read back in the shape it is written out in. The prompt was then
    changed to name the key the reader ignored, and every twin creation lost
    its Fatigue rolls and its years to "character_id is required".
    """

    @staticmethod
    def _twins(second_key: str) -> dict:
        """Two characters started, then each referred to by name."""
        return {
            "actions": [
                {
                    "action": "record_attributes",
                    "new_character": {"name": "Brother", "label": "the brother"},
                    "scores": {
                        "strength": 18,
                        "dexterity": 18,
                        "intelligence": 18,
                        "wisdom": 18,
                        "constitution": 18,
                        "charisma": 18,
                    },
                },
                {
                    "action": "record_attributes",
                    "new_character": {"name": "Sister", "label": "the sister"},
                    "scores": {
                        "strength": 18,
                        "dexterity": 18,
                        "intelligence": 18,
                        "wisdom": 18,
                        "constitution": 18,
                        "charisma": 18,
                    },
                },
                {"action": "roll_fatigue", second_key: "Brother"},
                {"action": "roll_fatigue", second_key: "Sister"},
            ]
        }

    def _read(self, plan: dict) -> object:
        return read_plan(json.dumps(plan), roster_ids=set(), policy=GAME_MASTER)

    def test_new_character_ref_names_a_character_the_plan_started(self):
        reading = self._read(self._twins("new_character_ref"))

        self.assertTrue(reading.ok, reading.errors)
        self.assertEqual(
            [action.new_character_ref for action in reading.actions[2:]],
            ["Brother", "Sister"],
        )

    def test_character_id_names_one_too(self):
        """The older shape keeps working."""
        reading = self._read(self._twins("character_id"))

        self.assertTrue(reading.ok, reading.errors)
        self.assertEqual(
            [action.new_character_ref for action in reading.actions[2:]],
            ["Brother", "Sister"],
        )

    def test_a_plan_reads_back_in_the_shape_it_is_written_out_in(self):
        """The round trip the missing key broke."""
        first = self._read(self._twins("character_id"))
        again = self._read({"actions": [a.as_payload() for a in first.actions]})

        self.assertTrue(again.ok, again.errors)
        self.assertEqual(
            [action.new_character_ref for action in again.actions[2:]],
            ["Brother", "Sister"],
        )

    def test_a_name_that_no_action_started_is_still_refused(self):
        plan = self._twins("new_character_ref")
        plan["actions"][2]["new_character_ref"] = "Cousin"

        reading = self._read(plan)

        self.assertFalse(reading.ok)
        self.assertIn("character_id must be an id", " ".join(reading.errors))

    def test_neither_key_is_still_refused(self):
        plan = self._twins("new_character_ref")
        del plan["actions"][2]["new_character_ref"]

        reading = self._read(plan)

        self.assertFalse(reading.ok)
        self.assertIn("new_character_ref", " ".join(reading.errors))


class OptionalFieldsTest(SimpleTestCase):
    """A field the model has nothing to say about must not cost the plan (#648).

    Writing ``null`` for an optional field is routine -- ``record_object`` is
    even told "a thing does not want anything" -- and an empty string is the
    same statement. Both used to read as "must be a non-empty string", and a
    plan with one error runs none of its actions, so the well-formed work
    beside it was thrown away too.
    """

    def read(self, *actions: dict):
        return read_plan(
            plan(*actions), roster_ids={41}, policy=GAME_MASTER, thread_ids={1}
        )

    def test_a_null_detail_is_left_unsaid_rather_than_refused(self):
        reading = self.read(
            {"action": "record_object", "name": "iron box", "wants": None}
        )

        self.assertEqual(reading.errors, ())
        self.assertEqual(reading.actions[0].name, "iron box")
        self.assertEqual(reading.actions[0].wants, "")

    def test_an_empty_detail_is_left_unsaid_rather_than_refused(self):
        reading = self.read(
            {
                "action": "record_npc",
                "name": "Mother Vane",
                "where": "the shrine",
                "condition": "",
            }
        )

        self.assertEqual(reading.errors, ())
        self.assertEqual(reading.actions[0].where, "the shrine")
        self.assertEqual(reading.actions[0].condition, "")

    def test_the_rest_of_the_plan_survives_one_empty_optional_field(self):
        reading = self.read(
            {"action": "move_party", "place": "Farport"},
            {"action": "open_thread", "text": "the box must be sealed"},
            {"action": "record_npc", "name": "Mother Vane", "wants": None},
        )

        self.assertEqual(reading.errors, ())
        self.assertEqual(
            [action.action for action in reading.actions],
            ["move_party", "open_thread", "record_npc"],
        )

    def test_every_optional_field_tolerates_null(self):
        for action in (
            {"action": "advance_time", "days": 1, "time_of_day": None},
            {"action": "adjust_supplies", "item": "torches", "amount": None},
            {"action": "adjust_supplies", "item": "rope", "note": None},
            {"action": "adjust_supplies", "item": "rope", "spent": None},
            {"action": "resolve_thread", "thread_id": 1, "resolution": None},
            {"action": "rules_lookup", "subject": "falling", "track": None},
            {
                "action": "add_equipment",
                "character_id": 41,
                "item": "a lamp",
                "quantity": None,
            },
            {
                "action": "set_background",
                "character_id": 41,
                "provenance": "highborn",
                "wealth": None,
            },
            {
                "action": "record_object",
                "name": "iron box",
                "condition": None,
                "where": None,
                "notes": None,
            },
        ):
            with self.subTest(action=action):
                self.assertEqual(self.read(action).errors, ())

    def test_a_detail_of_the_wrong_type_is_still_an_error(self):
        """Tolerating "nothing to say" is not tolerating a number."""
        reading = self.read({"action": "record_npc", "name": "Vane", "wants": 7})

        self.assertFalse(reading.ok)
        self.assertIn("wants must be a non-empty string", reading.errors[0])

    def test_a_required_field_left_null_is_still_an_error(self):
        reading = self.read({"action": "record_npc", "name": None})

        self.assertFalse(reading.ok)
        self.assertIn("name must be a non-empty string", reading.errors[0])

    def test_advance_time_with_nothing_to_advance_is_still_an_error(self):
        reading = self.read(
            {"action": "advance_time", "days": None, "time_of_day": None}
        )

        self.assertFalse(reading.ok)
        self.assertIn("advance_time needs days", reading.errors[0])

    def test_set_background_with_nothing_given_is_still_an_error(self):
        reading = self.read(
            {"action": "set_background", "character_id": 41, "provenance": None}
        )

        self.assertFalse(reading.ok)
        self.assertIn("at least one of", reading.errors[0])

    def test_a_null_beside_a_real_target_does_not_read_as_two_targets(self):
        reading = self.read(
            {
                "action": "record_attributes",
                "new_character": {"name": "Wren", "label": None},
                "character_id": None,
                "all": 18,
                "scores": None,
            }
        )

        self.assertEqual(reading.errors, ())
        self.assertEqual(reading.actions[0].all_score, 18)
        assert reading.actions[0].new_character is not None
        self.assertEqual(reading.actions[0].new_character.name, "Wren")


class EmptyReferencesTest(SimpleTestCase):
    """The #648 class in the readers that bypassed ``given()`` (issue #833).

    A roll's target is optional, a Game Master may state some scores and not
    others, and ``new_character`` beside an id is routine filler. Each was
    read as a bad value, so the whole plan was thrown away and re-planned, and
    a second slip ran nothing.
    """

    def read(self, *actions: dict):
        return read_plan(
            plan({"action": "record_npc", "name": "Vane"}, *actions),
            roster_ids={41},
            policy=GAME_MASTER,
        )

    def test_a_roll_with_an_empty_target_is_a_roll_by_nobody_in_particular(self):
        for target in (
            {"character_id": ""},
            {"character_id": None, "new_character_ref": ""},
            {"character_id": "  ", "new_character_ref": None},
        ):
            with self.subTest(target=target):
                reading = self.read(
                    {"action": "roll", "dice": "1d20", "purpose": "spot", **target}
                )

                self.assertEqual(reading.errors, ())
                self.assertIsNone(reading.actions[1].character_id)
                self.assertEqual(reading.actions[1].new_character_ref, "")

    def test_an_empty_character_id_beside_a_real_ref_names_the_ref(self):
        reading = read_plan(
            plan(
                {"action": "roll_attributes", "new_character": {"name": "Wren"}},
                {
                    "action": "roll_fatigue",
                    "character_id": "",
                    "new_character_ref": "Wren",
                },
            ),
            roster_ids={41},
            policy=GAME_MASTER,
        )

        self.assertEqual(reading.errors, ())
        self.assertEqual(reading.actions[1].new_character_ref, "Wren")

    def test_a_required_target_left_empty_is_still_an_error(self):
        reading = self.read({"action": "roll_fatigue", "character_id": ""})

        self.assertFalse(reading.ok)
        self.assertIn("character_id is required", " ".join(reading.errors))

    def test_a_null_score_states_nothing_for_that_attribute(self):
        reading = self.read(
            {
                "action": "record_attributes",
                "character_id": 41,
                "scores": {"strength": 14, "dexterity": None, "wisdom": ""},
            }
        )

        self.assertEqual(reading.errors, ())
        self.assertEqual(reading.actions[1].scores, (("strength", 14),))

    def test_scores_that_are_all_null_are_no_scores(self):
        reading = self.read(
            {
                "action": "record_attributes",
                "character_id": 41,
                "scores": {"strength": None},
            }
        )

        self.assertFalse(reading.ok)
        self.assertIn('give either "scores" or "all"', " ".join(reading.errors))

    def test_null_scores_beside_all_leave_all(self):
        reading = self.read(
            {
                "action": "record_attributes",
                "character_id": 41,
                "all": 12,
                "scores": {"strength": None},
            }
        )

        self.assertEqual(reading.errors, ())
        self.assertEqual(reading.actions[1].all_score, 12)

    def test_an_empty_new_character_beside_an_id_says_nothing(self):
        for empty in ({}, {"name": None, "label": ""}):
            with self.subTest(new_character=empty):
                reading = self.read(
                    {
                        "action": "roll_attributes",
                        "character_id": 41,
                        "new_character": empty,
                    }
                )

                self.assertEqual(reading.errors, ())
                self.assertEqual(reading.actions[1].character_id, 41)
                self.assertIsNone(reading.actions[1].new_character)

    def test_an_empty_new_character_alone_still_asks_for_a_name(self):
        reading = self.read({"action": "roll_attributes", "new_character": {}})

        self.assertFalse(reading.ok)
        self.assertIn("needs a name or a label", " ".join(reading.errors))

    def test_an_empty_quantity_takes_the_default(self):
        reading = self.read(
            {
                "action": "add_equipment",
                "character_id": 41,
                "item": "a lamp",
                "quantity": "",
            }
        )

        self.assertEqual(reading.errors, ())
        self.assertEqual(reading.actions[1].quantity, 1)

    def test_a_required_number_left_empty_is_still_an_error(self):
        reading = self.read(
            {"action": "play_years", "character_id": 41, "track": "random", "years": ""}
        )

        self.assertFalse(reading.ok)
        self.assertIn("years must be a whole number", " ".join(reading.errors))


class GameMasterRecordActionsReadTest(SimpleTestCase):
    """The new actions read strictly, like every other (issues #802, #843)."""

    def read(self, *actions: dict, policy: TurnPolicy = GAME_MASTER):
        return read_plan(plan(*actions), roster_ids={41}, policy=policy)

    def test_each_reads_into_its_fields(self):
        reading = self.read(
            {"action": "mark_dead", "character_id": 41, "cause": "the fall"},
            {"action": "award_xp", "character_id": 41, "xp": 500},
            {"action": "set_coin", "character_id": 41, "change": -12.5},
            {"action": "set_coin", "character_id": 41, "purse": 40, "reason": "fine"},
            {"action": "roll_fatigue", "character_id": 41, "value": 9},
            {
                "action": "skill_check",
                "character_id": 41,
                "skill": "Stealth",
                "difficulty": 0,
                "attribute": "Wisdom",
                "total": 7,
            },
        )

        self.assertEqual(reading.errors, ())
        dead, xp, take, purse, fatigue, check = reading.actions
        self.assertEqual(dead.cause, "the fall")
        self.assertEqual(xp.xp, 500)
        self.assertEqual((take.coin_change, take.coin_total), ("-12.50", ""))
        self.assertEqual((purse.coin_total, purse.reason), ("40.00", "fine"))
        self.assertEqual(fatigue.fatigue_value, 9)
        self.assertEqual(
            (check.skill, check.difficulty, check.attribute, check.stated_total),
            ("Stealth", 0, "wisdom", 7),
        )
        self.assertEqual(check.as_payload()["difficulty"], 0)
        for action in reading.actions:
            with self.subTest(action=action.action):
                self.assertEqual(
                    read_plan(
                        plan(action.as_payload()), roster_ids={41}, policy=GAME_MASTER
                    ).actions[0],
                    action,
                    "a plan reads back in its own shape",
                )

    def test_bad_values_are_errors(self):
        for action in (
            {"action": "mark_dead", "character_id": 41},
            {"action": "award_xp", "character_id": 41, "xp": 0},
            {"action": "award_xp", "character_id": 41, "xp": "500"},
            {"action": "set_coin", "character_id": 41},
            {"action": "set_coin", "character_id": 41, "change": 5, "purse": 5},
            {"action": "set_coin", "character_id": 41, "change": 0},
            {"action": "set_coin", "character_id": 41, "purse": -5},
            {"action": "set_coin", "character_id": 41, "change": "5"},
            {"action": "set_coin", "character_id": 41, "change": True},
            {"action": "roll_fatigue", "character_id": 41, "value": 13},
            {"action": "skill_check", "character_id": 41},
            {"action": "skill_check", "character_id": 41, "skill": "x", "total": 1},
            {
                "action": "skill_check",
                "character_id": 41,
                "skill": "x",
                "attribute": "WIS",
            },
        ):
            with self.subTest(action=action):
                self.assertFalse(self.read(action).ok)

    def test_a_players_records_are_refused_not_errors(self):
        reading = self.read(
            {"action": "mark_dead", "character_id": 41, "cause": "x"},
            {"action": "award_xp", "character_id": 41, "xp": 5},
            {"action": "set_coin", "character_id": 41, "change": 5},
            {"action": "roll_fatigue", "character_id": 41, "value": 9},
            {"action": "roll_fatigue", "character_id": 41},
            {"action": "skill_check", "character_id": 41, "skill": "stealth"},
            policy=PLAYER,
        )

        self.assertEqual(
            [bool(action.refusal) for action in reading.actions],
            [True, True, True, True, False, False],
        )


class NonFiniteCoinTest(SimpleTestCase):
    """Review of #833: Python's json reads NaN, Infinity and 1e400 as floats,
    and Decimal raised on each out of read_plan, losing the turn."""

    def test_non_finite_and_oversized_coin_are_errors_not_exceptions(self):
        for raw in (
            '{"actions": [{"action": "set_coin", "character_id": 41, "change": NaN}]}',
            '{"actions": [{"action": "set_coin", "character_id": 41, "purse": Infinity}]}',
            '{"actions": [{"action": "set_coin", "character_id": 41, "change": 1e400}]}',
            '{"actions": [{"action": "set_coin", "character_id": 41, "change": -Infinity}]}',
            '{"actions": [{"action": "set_coin", "character_id": 41, "purse": 1e300}]}',
        ):
            with self.subTest(raw=raw):
                reading = read_plan(raw, roster_ids={41}, policy=GAME_MASTER)
                self.assertFalse(reading.ok)
                self.assertIn("set_coin", reading.errors[0])

    def test_every_other_numeric_field_refuses_them_too(self):
        for field, action in (
            ("xp", "award_xp"),
            ("value", "roll_fatigue"),
            ("quantity", "add_equipment"),
            ("years", "play_years"),
            ("points", "spend_skill_points"),
            ("days", "advance_time"),
        ):
            for bad in ("NaN", "Infinity", "1e400"):
                with self.subTest(field=field, value=bad):
                    raw = (
                        f'{{"actions": [{{"action": "{action}", "character_id": 41, '
                        f'"item": "rope", "track": "random", "skill": "sword", '
                        f'"{field}": {bad}}}]}}'
                    )
                    reading = read_plan(raw, roster_ids={41}, policy=GAME_MASTER)
                    self.assertFalse(reading.ok)


class CustomItemPlanTest(SimpleTestCase):
    """What ``add_equipment`` carries for an item the catalog does not sell (#927).

    Who pays the price, and who may give combat numbers, is the service's
    ruling; the plan carries what was stated, and an unstated price is not 0.
    """

    def read_one(self, **fields):
        reading = read_plan(
            json.dumps(
                {"actions": [{"action": "add_equipment", "character_id": 41, **fields}]}
            ),
            roster_ids={41},
            policy=PLAYER,
        )
        return reading

    def test_a_stated_price_is_carried_and_an_unstated_one_is_not_zero(self):
        for raw, carried in ((15, "15.00"), (0, "0.00"), (None, "")):
            with self.subTest(raw=raw):
                reading = self.read_one(item="Heirloom ring", price=raw)

                self.assertEqual(reading.errors, ())
                self.assertEqual(reading.actions[0].price, carried)

    def test_combat_numbers_are_carried_and_written_back(self):
        reading = self.read_one(
            item="Dragonhide", price=5, category="Armor", stops=0, dex_penalty=-1
        )

        (action,) = reading.actions
        self.assertEqual(
            action.combat, (("category", "armor"), ("stops", 0), ("dex_penalty", -1))
        )
        self.assertEqual(
            action.as_payload(),
            {
                "action": "add_equipment",
                "character_id": 41,
                "item": "Dragonhide",
                "quantity": 1,
                "price": 5.0,
                "category": "armor",
                "stops": 0,
                "dex_penalty": -1,
            },
        )

    def test_a_bad_price_or_combat_number_is_an_error(self):
        for fields, said in (
            ({"price": -1}, "price must be between"),
            ({"price": "free"}, "price must be a number"),
            ({"category": "potion"}, "category must be one of"),
            ({"stops": 3}, "combat numbers need a category"),
            ({"category": "armor", "stops": 99}, "stops"),
            ({"category": "armor", "dex_penalty": 2}, "dex_penalty"),
        ):
            with self.subTest(fields=fields):
                reading = self.read_one(item="Odd thing", **fields)

                self.assertFalse(reading.ok)
                self.assertIn(said, reading.errors[0])
