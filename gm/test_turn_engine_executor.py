"""Running plan actions against the character records (issue #590)."""

import json
import time
from collections.abc import Sequence
from unittest.mock import patch

from characters import services
from characters.models import Character
from common.test_helpers import BaseTestCase, create_character

from .engine import MODE_AIDE, MODE_PLAY
from .models import (
    CampaignState,
    GMEvent,
    GMSession,
    GMTurnRecord,
    SceneEntity,
    SessionCharacter,
    StoryThread,
    SupplyItem,
)
from .testing import a_mapped_place, add_character, gm_replies, recorded
from .turn_engine import events
from .turn_engine.engine import TurnEngine, TurnRequest
from .turn_engine.executor import NOT_YOURS_REFUSAL, RecordsExecutor
from .turn_engine.model import FakeModel
from .turn_engine.plan import NewCharacter, PlannedAction, read_plan
from .turn_engine.policy import (
    GAME_MASTER_RECORDS_REFUSAL,
    PLAYER_STATED_FATIGUE_REFUSAL,
    PLAYER_STATED_SCORES_REFUSAL,
    TurnPolicy,
)
from .turn_engine.state import refusal_lines, settled_refusals

ATTRIBUTES = (
    "strength",
    "dexterity",
    "intelligence",
    "wisdom",
    "constitution",
    "charisma",
)


def plan(*actions: dict) -> str:
    return json.dumps({"actions": list(actions)})


class ExecutorTestCase(BaseTestCase):
    """A session, an executor for it, and a way to run a plan through a turn."""

    game_master = True
    mode = MODE_AIDE

    def setUp(self):
        super().setUp()
        self.user = self.create_dm() if self.game_master else self.create_user()
        self.session = GMSession.objects.create(
            user=self.user, name="s", mode=self.mode
        )
        self.policy = TurnPolicy.for_turn(self.mode, is_game_master=self.game_master)

    def executor(self, session=None, turn_id=None) -> RecordsExecutor:
        return RecordsExecutor(
            self.policy,
            self.session if session is None else session,
            ensure_session=lambda: self.session,
            actor=self.user,
            turn_id=turn_id,
        )

    def play(self, raw_plan: str, *, session=None, narration="Done.", replan=None):
        """Run a whole turn whose plan is ``raw_plan``.

        Args:
            replan: The second planning reply, for a turn whose first plan is
                rejected and asked again (issue #614).

        Returns:
            ``(outcome, events, model, turn)``.
        """
        # The session is set as gm/turn_record.py sets it, so records that
        # hang off the turn -- rulings, threads, and the refusals a later
        # turn reads back (issue #614) -- are reachable from the session.
        turn = GMTurnRecord.objects.create(
            user=self.user,
            session=self.session if session is None else session,
            outcome="running",
            mode=self.mode,
            # As gm_stream records it: the authority the turn was taken with,
            # which the refusal memory is keyed by (issue #835).
            is_game_master=self.policy.is_game_master,
        )
        replies = (
            [raw_plan, narration] if replan is None else [raw_plan, replan, narration]
        )
        model = FakeModel(replies)
        # The turn is passed as the view passes it, so records that point at
        # the turn that made them (rulings, threads) are exercised here too.
        executor = self.executor(session, turn_id=turn.pk)
        engine = TurnEngine(
            model,
            events.DatabaseSink(turn.pk),
            executor,
            started_at=time.monotonic(),
            budget_seconds=60,
        )
        generator = engine.run(
            TurnRequest(
                policy=self.policy,
                prefix=[{"role": "system", "content": "S"}],
                latest="go",
                commit=lambda text: self.session.pk,
            )
        )
        yielded = []
        while True:
            try:
                yielded.append(next(generator))
            except StopIteration as stop:
                return stop.value, yielded, model, turn

    def characters(self):
        return list(self.session.attached_characters())


class GameMasterActionsTest(ExecutorTestCase):
    def test_twins_with_stated_scores_are_two_records_in_one_plan(self):
        outcome, yielded, model, turn = self.play(
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
            )
        )

        twain, thread = self.characters()
        self.assertEqual((twain.name, thread.name), ("Twain", "Thread"))
        for character in (twain, thread):
            self.assertEqual({getattr(character, name) for name in ATTRIBUTES}, {18})
        self.assertEqual(
            SessionCharacter.objects.get(character=twain).label, "the brother"
        )
        self.assertTrue(all(result.ran for result in outcome.results))
        narrate = model.requests[1][-1]["content"]
        self.assertIn(f"- id {twain.pk}: Twain (the brother)", narrate)
        # The background start_character rolled is named with it (#754).
        self.assertIn(
            f"Twain (the brother) is a new character, id {twain.pk}, with a rolled "
            f"background: {twain.background_summary}. Twain (the brother): all six "
            "attributes recorded as 18.",
            narrate,
        )
        frames = [
            event.payload["frame"] for event in yielded if event.kind == events.ACTION
        ]
        self.assertEqual([frame["ran"] for frame in frames], [True, True])
        stored = GMEvent.objects.filter(turn=turn, kind=events.ACTION)
        self.assertEqual(stored.count(), 2)
        self.assertTrue(all(event.visible for event in stored))

    def test_years_can_follow_a_character_started_in_the_same_plan(self):
        outcome, yielded, model, _turn = self.play(
            plan(
                {
                    "action": "record_attributes",
                    "new_character": {"name": "Twain"},
                    "all": 18,
                },
                {
                    "action": "play_years",
                    "character_id": "twain",
                    "track": "laborer",
                    "years": 2,
                },
            )
        )

        (twain,) = self.characters()
        self.assertGreaterEqual(twain.experience_years, 1)
        years = outcome.results[1]
        self.assertTrue(years.ran)
        self.assertTrue(
            years.procedure["lines"][0].startswith("⚙️ laborer year 1 survival: 3d6")
        )
        self.assertIn("Survival check: 3d6 rolled", years.result)
        statuses = [
            event.payload["status"] for event in yielded if event.kind == events.STATUS
        ]
        self.assertIn("playing years of experience for Twain", statuses)

    def test_a_refused_first_change_creates_no_character(self):
        outcome, *_rest = self.play(
            plan(
                {
                    "action": "record_attributes",
                    "new_character": {"name": "Giant"},
                    "all": 40,
                },
                {
                    "action": "play_years",
                    "character_id": "Giant",
                    "track": "laborer",
                    "years": 1,
                },
            )
        )

        self.assertEqual(self.characters(), [])
        self.assertFalse(Character.objects.filter(name="Giant").exists())
        first, second = outcome.results
        self.assertIn("between 1 and", first.error)
        self.assertIn("Giant was not created", second.error)
        self.assertTrue(
            first.procedure["lines"][0].startswith(
                "⚙️ Not done: recording attributes for Giant, because"
            )
        )

    def test_every_character_action_changes_the_record(self):
        character = add_character(self.session, recorded("Gorn"))
        character.is_alive = False
        character.death_cause = "a fall"
        character.save()
        with patch("characters.services.roll_expression", return_value=([3, 4], 7)):
            outcome, *_rest = self.play(
                plan(
                    {"action": "revive_character", "character_id": character.pk},
                    {"action": "roll_fatigue", "character_id": character.pk},
                    {
                        "action": "add_equipment",
                        "character_id": character.pk,
                        "item": "rope",
                        "quantity": 2,
                    },
                    {
                        "action": "rename_character",
                        "character_id": character.pk,
                        "name": "Gorn the Second",
                    },
                    {
                        "action": "set_background",
                        "character_id": character.pk,
                        "provenance": "Merchant",
                        "location": "city",
                        "wealth": "rich",
                    },
                    {
                        "action": "spend_skill_points",
                        "character_id": character.pk,
                        "skill": "Sword",
                        "points": 2,
                    },
                    {"action": "roll_attributes", "character_id": character.pk},
                )
            )

        character.refresh_from_db()
        self.assertTrue(character.is_alive)
        self.assertEqual(character.fatigue_roll, 7)
        self.assertEqual(character.equipment, [{"name": "rope", "quantity": 2}])
        self.assertEqual(character.name, "Gorn the Second")
        self.assertEqual(
            (character.provenance_class, character.location, character.wealth_level),
            ("merchant", "City", "Rich"),
        )
        self.assertEqual(character.skills.get("sword"), 2)
        self.assertEqual(
            SessionCharacter.objects.get(character=character).game_master_skill_points,
            2,
        )
        self.assertEqual([result.ran for result in outcome.results], [True] * 7)
        self.assertIn("granted by the Game Master", outcome.results[5].note)
        self.assertIn("rolled the six attributes", outcome.results[6].note)
        self.assertIn("strength: 3d6: [", character.dice_log)

    def test_removing_takes_the_character_out_of_the_session(self):
        character = add_character(self.session, {"name": "Temp"})

        outcome, *_rest = self.play(
            plan({"action": "remove_character", "character_id": character.pk})
        )

        self.assertEqual(self.characters(), [])
        self.assertIn("removed Temp from this session", outcome.results[0].note)

    def test_rules_refusals_are_reported_in_words(self):
        character = add_character(self.session, {"name": "Blank"})

        outcome, *_rest = self.play(
            plan(
                {
                    "action": "play_years",
                    "character_id": character.pk,
                    "track": "laborer",
                    "years": 1,
                },
                {
                    "action": "set_background",
                    "character_id": character.pk,
                    "wealth": "Loaded",
                },
                {
                    "action": "rename_character",
                    "character_id": character.pk,
                    "name": "<b>",
                },
                {"action": "roll_fatigue", "character_id": 999},
            )
        )

        years, background, rename, stranger = outcome.results
        self.assertIn("six core attributes", years.error)
        self.assertIn("wealth_level must be one of", background.error)
        self.assertIn("Name may contain", rename.error)
        self.assertIn("no character with id 999", stranger.error)
        self.assertFalse(any(result.ran for result in outcome.results))
        self.assertIn(
            "Not done: playing years of experience for Blank, because", years.result
        )

    def test_a_character_that_vanished_mid_turn_is_refused(self):
        character = add_character(self.session, {"name": "Ghost"})
        executor = self.executor()
        (action,) = read_plan(
            plan({"action": "roll_fatigue", "character_id": character.pk}),
            roster_ids={character.pk},
            policy=self.policy,
        ).actions
        recorded_events = []

        with patch("characters.services.changing", side_effect=Character.DoesNotExist):
            result = executor.run(action, recorded_events.append)

        self.assertEqual(result.error, "that character no longer exists")
        self.assertEqual(len(recorded_events), 1)

    def test_dice_are_rolled_and_shown_without_changing_anyone(self):
        character = add_character(self.session, recorded("Gorn"))

        outcome, yielded, model, _turn = self.play(
            plan(
                {
                    "action": "roll",
                    "dice": "3d6 x2",
                    "purpose": "a test of nerve",
                    "character_id": character.pk,
                },
                {"action": "roll", "dice": "0d6"},
            )
        )

        rolls = [event.payload for event in yielded if event.kind == events.ROLL]
        self.assertEqual(len(rolls), 3)
        self.assertTrue(
            rolls[0]["line"].startswith("🎲 Gorn rolls 3d6 for a test of nerve: [")
        )
        nerve, broken = outcome.results
        self.assertTrue(nerve.ran)
        self.assertEqual(len(nerve.rolls), 2)
        self.assertFalse(broken.ran)
        self.assertTrue(broken.error)
        self.assertIn(
            "Gorn rolls 3d6 for a test of nerve", model.requests[1][-1]["content"]
        )

    def test_a_question_is_passed_on_not_run(self):
        outcome, yielded, model, turn = self.play(
            plan({"action": "ask_gm", "question": "Which track?"})
        )

        self.assertFalse(outcome.results[0].ran)
        self.assertIn(
            "Question to ask first: Which track?", model.requests[1][-1]["content"]
        )
        self.assertFalse(GMEvent.objects.get(turn=turn, kind=events.ACTION).visible)

    def test_the_first_action_of_a_new_session_creates_it(self):
        created = []

        def ensure():
            created.append(True)
            return self.session

        executor = RecordsExecutor(
            self.policy, None, ensure_session=ensure, actor=self.user
        )
        self.assertEqual(executor.roster(), [])
        (action,) = read_plan(
            plan(
                {
                    "action": "record_attributes",
                    "new_character": {"label": "the sister"},
                    "all": 12,
                }
            ),
            roster_ids=set(),
            policy=self.policy,
        ).actions

        result = executor.run(action, lambda event: None)

        self.assertTrue(result.ran)
        self.assertEqual(created, [True])
        self.assertEqual(executor.label(action), "recording attributes for the sister")
        self.assertEqual(len(executor.roster()), 1)


class GameMasterOriginChangeTest(ExecutorTestCase):
    """A Game Master's plan that moves a rolled character's Origin rolls
    literacy again against the new one (#741), as the sheet does."""

    def test_set_background_rerolls_literacy(self):
        character = add_character(self.session, recorded("Gorn", score=10))
        character.location = "Village"
        character.literacy_roll = 9
        character.literacy_rolled = True
        character.save()

        with patch(
            "characters.services.roll_3d6_logged",
            return_value=(5, "3d6: [1, 1, 3] = 5"),
        ):
            outcome, *_rest = self.play(
                plan(
                    {
                        "action": "set_background",
                        "character_id": character.pk,
                        "location": "rural",
                    }
                )
            )

        self.assertTrue(outcome.results[0].ran, outcome.results[0].error)
        character.refresh_from_db()
        self.assertEqual((character.location, character.literacy_roll), ("Rural", 5))
        self.assertEqual(character.literacy_result, "Literate (rolled 5 ≤ 10 − 4)")
        self.assertEqual(character.history[-1]["field"], "literacy_roll")


class PlayerActionsTest(ExecutorTestCase):
    game_master = False
    mode = MODE_PLAY

    def test_a_players_session_buys_catalog_gear_from_the_purse(self):
        """The #757 review ruling: for a player, add_equipment is a purchase."""
        character = add_character(self.session, recorded("Buyer"))
        character.starting_coin = 100
        character.save()
        outcome, *_rest = self.play(
            plan(
                {
                    "action": "add_equipment",
                    "character_id": character.pk,
                    "item": "broadsword",
                    "quantity": 1,
                },
                {
                    "action": "add_equipment",
                    "character_id": character.pk,
                    "item": "chainmail",
                    "quantity": 1,
                },
            )
        )

        character.refresh_from_db()
        self.assertEqual(character.purse, 20)
        self.assertEqual(
            character.equipment, [{"item_id": "broadsword", "quantity": 1}]
        )
        bought, refused = outcome.results
        self.assertTrue(bought.ran)
        self.assertIn("Not enough coin", refused.error)

    def test_a_players_stated_scores_are_refused_and_nobody_is_created(self):
        outcome, yielded, model, _turn = self.play(
            plan(
                {
                    "action": "record_attributes",
                    "new_character": {"name": "Bob"},
                    "all": 18,
                }
            )
        )

        self.assertEqual(self.characters(), [])
        self.assertEqual(outcome.results[0].error, PLAYER_STATED_SCORES_REFUSAL)
        self.assertIn("This message is from a player", model.requests[1][-1]["content"])
        self.assertIn("rolled, not stated", model.requests[1][-1]["content"])

    def test_a_player_rolls_attributes_and_fatigue_once(self):
        outcome, *_rest = self.play(
            plan(
                {"action": "roll_attributes", "new_character": {"name": "Bob"}},
                {"action": "roll_fatigue", "character_id": "Bob"},
                {"action": "roll_fatigue", "character_id": "Bob"},
                {"action": "roll_attributes", "character_id": "Bob"},
            )
        )

        (bob,) = self.characters()
        self.assertTrue(bob.is_complete)
        self.assertEqual(
            [result.ran for result in outcome.results], [True, True, False, False]
        )
        self.assertIn("only a Game Master can roll again", outcome.results[2].error)
        self.assertIn("The attributes are already recorded", outcome.results[3].error)

    def test_a_player_cannot_move_the_origin_under_a_rolled_literacy_check(self):
        """The Origin lock (#741) holds in a player's session as on the sheet:
        set_background is open to players, and moved a rolled-illiterate
        Village character to City, where the same roll read as literate."""
        character = add_character(self.session, recorded("Gorn", score=10))
        character.location = "Village"
        character.literacy_roll = 9
        character.literacy_rolled = True
        character.save()

        outcome, _yielded, model, _turn = self.play(
            plan(
                {
                    "action": "set_background",
                    "character_id": character.pk,
                    "location": "city",
                }
            )
        )

        self.assertFalse(outcome.results[0].ran)
        self.assertEqual(outcome.results[0].error, services.ORIGIN_FIXED_REASON)
        self.assertIn(services.ORIGIN_FIXED_REASON, model.requests[1][-1]["content"])
        character.refresh_from_db()
        self.assertEqual((character.location, character.literacy_roll), ("Village", 9))
        self.assertFalse(character.is_literate)

    def test_a_player_cannot_revive(self):
        character = add_character(self.session, recorded("Gorn"))
        outcome, *_rest = self.play(
            plan({"action": "revive_character", "character_id": character.pk})
        )
        self.assertIn("only a Game Master", outcome.results[0].error)


class CampaignStateTest(ExecutorTestCase):
    """Where the party is, who and what is in play (issue #579).

    Driven through a whole turn, because the point of the issue is that these
    change only through the plan -- never from prose, and never from what the
    model remembers of a transcript it can no longer see.
    """

    def state(self):
        return CampaignState.objects.get(session=self.session)

    def test_the_party_moves_and_the_clock_runs(self):
        self.play(
            plan(
                {"action": "move_party", "place": "the well beneath Farport"},
                {"action": "advance_time", "days": 2, "time_of_day": "evening"},
            )
        )

        state = self.state()
        self.assertEqual(state.place, "the well beneath Farport")
        self.assertEqual((state.day, state.time_of_day), (3, "evening"))

    def test_supplies_are_a_row_each_and_change_one_at_a_time(self):
        """The free-text line could not spend torches without risking the rope."""
        self.play(
            plan(
                {"action": "adjust_supplies", "item": "torches", "amount": "3"},
                {
                    "action": "adjust_supplies",
                    "item": "rope",
                    "amount": "40ft",
                    "note": "hemp",
                },
            )
        )

        self.play(plan({"action": "adjust_supplies", "item": "Torches", "amount": "1"}))

        carried = list(SupplyItem.objects.filter(session=self.session))
        self.assertEqual([item.name for item in carried], ["torches", "rope"])
        self.assertEqual(carried[0].amount, "1")
        self.assertEqual((carried[1].amount, carried[1].note), ("40ft", "hemp"))

    def test_a_note_survives_a_change_of_amount(self):
        self.play(
            plan(
                {
                    "action": "adjust_supplies",
                    "item": "rations",
                    "amount": "4 days",
                    "note": "shared between them",
                }
            )
        )

        self.play(
            plan({"action": "adjust_supplies", "item": "rations", "amount": "2 days"})
        )

        rations = SupplyItem.objects.get(session=self.session, name="rations")
        self.assertEqual(
            (rations.amount, rations.note), ("2 days", "shared between them")
        )

    def test_the_last_of_a_thing_takes_its_row_away(self):
        self.play(plan({"action": "adjust_supplies", "item": "torches", "amount": "1"}))

        self.play(plan({"action": "adjust_supplies", "item": "torches", "spent": True}))

        self.assertFalse(SupplyItem.objects.filter(session=self.session).exists())

    def test_spending_what_they_never_carried_is_refused(self):
        outcome, *_rest = self.play(
            plan({"action": "adjust_supplies", "item": "torches", "spent": True})
        )

        self.assertIn("not carrying torches", outcome.results[0].error)
        self.assertFalse(SupplyItem.objects.filter(session=self.session).exists())

    def test_the_clock_only_ever_moves_forward_by_what_it_was_given(self):
        self.play(plan({"action": "advance_time", "days": 1}))
        self.play(plan({"action": "advance_time", "days": 1}))

        self.assertEqual(self.state().day, 3)

    def test_a_mapped_place_is_recognised_and_an_invented_one_is_marked(self):
        place = a_mapped_place()
        self.play(plan({"action": "move_party", "place": place}))
        mapped = self.state().place_key

        self.play(plan({"action": "move_party", "place": "Grandma's cellar"}))

        self.assertTrue(mapped, f"{place} is in the gazetteer and should be keyed")
        self.assertEqual(self.state().place_key, "")
        self.assertIn("not a mapped place", self.state().where)

    def test_an_npc_is_recorded_once_and_updated_in_place(self):
        self.play(
            plan(
                {
                    "action": "record_npc",
                    "name": "Mother Vane",
                    "where": "the Farport shrine",
                    "wants": "the box sealed",
                    "condition": "wary",
                }
            )
        )
        self.play(
            plan(
                {
                    "action": "record_npc",
                    "name": "mother vane",
                    "condition": "grateful",
                }
            )
        )

        people = SceneEntity.objects.filter(
            session=self.session, kind=SceneEntity.PERSON
        )
        self.assertEqual(people.count(), 1, "the same person twice is one record")
        vane = people.get()
        self.assertEqual(vane.condition, "grateful")
        self.assertEqual(vane.wants, "the box sealed", "what it did not say is kept")

    def test_the_box_keeps_the_state_it_was_given(self):
        """The Game Master's complaint, as a test (session 36).

        The box was on a ledge, then chained, then fused to the rock, with
        nothing in between that would have changed it. Now a turn that says
        where it is does not quietly forget what state it is in, and the
        state the narration is handed is the state that was recorded.
        """
        self.play(
            plan(
                {
                    "action": "record_object",
                    "name": "iron box",
                    "where": "a stone ledge above the water",
                    "condition": "resting loose, unfastened",
                }
            )
        )

        _outcome, _yielded, model, _turn = self.play(
            plan(
                {
                    "action": "record_object",
                    "name": "iron box",
                    "where": "the well floor",
                }
            )
        )

        box = SceneEntity.objects.get(session=self.session, name="iron box")
        self.assertEqual(box.where, "the well floor")
        self.assertEqual(box.condition, "resting loose, unfastened")
        # And the narrating call was told so, rather than left to remember.
        narrate = model.requests[-1][-1]["content"]
        self.assertIn("iron box", narrate)
        self.assertIn("resting loose, unfastened", narrate)

    def test_a_thing_and_a_person_do_not_share_a_name(self):
        self.play(plan({"action": "record_object", "name": "Shadow"}))

        _outcome, yielded, _model, _turn = self.play(
            plan({"action": "record_npc", "name": "Shadow"})
        )

        refusals = [
            event.payload["frame"]["error"]
            for event in yielded
            if event.kind == events.ACTION
            and "frame" in event.payload
            and not event.payload["frame"].get("ran")
        ]
        self.assertTrue(refusals)
        self.assertIn("already recorded as a thing", refusals[0])
        self.assertEqual(SceneEntity.objects.filter(session=self.session).count(), 1)

    def test_a_thread_is_opened_and_closed(self):
        self.play(plan({"action": "open_thread", "text": "the Sleeper must not wake"}))
        thread = StoryThread.objects.get(session=self.session)

        self.play(
            plan(
                {
                    "action": "resolve_thread",
                    "thread_id": thread.pk,
                    "resolution": "they sealed it with blood",
                }
            )
        )

        thread.refresh_from_db()
        self.assertTrue(thread.resolved)
        self.assertEqual(thread.resolution, "they sealed it with blood")
        self.assertIsNotNone(thread.resolved_at)

    def test_a_thread_that_is_not_open_is_sent_back_to_the_model(self):
        """A code guarantee rather than another prompt bullet (issue #627).

        The model planned ``resolve_thread`` with an invented id after
        putting the party's oath inside an NPC's ``condition`` field instead
        of opening a thread for it. Refusing the action left the narration
        free to say the promise was kept with nothing recording it, so a
        thread id the session does not hold now makes the plan unreadable:
        the model is asked once more, told the ids it may actually close,
        and told to open one when there are none.
        """
        _outcome, yielded, _model, _turn = self.play(
            plan({"action": "resolve_thread", "thread_id": 4321}),
            replan=plan({"action": "open_thread", "text": "seal the iron box"}),
        )

        rejected = [
            event.payload for event in yielded if event.kind == events.PLAN_REJECTED
        ]
        self.assertTrue(rejected)
        said = " ".join(rejected[0]["errors"])
        self.assertIn("there is no thread 4321", said)
        self.assertIn("no open threads", said)
        self.assertIn("open_thread", said)
        self.assertEqual(StoryThread.objects.filter(session=self.session).count(), 1)

    def test_the_open_thread_ids_are_named_when_there_are_some(self):
        for text in ("the Sleeper must not wake", "the debt to Vane"):
            StoryThread.objects.create(session=self.session, text=text)
        open_ids = sorted(
            StoryThread.objects.filter(session=self.session).values_list(
                "pk", flat=True
            )
        )

        _outcome, yielded, _model, _turn = self.play(
            plan({"action": "resolve_thread", "thread_id": 4321}),
            replan=plan(),
        )

        rejected = [
            event.payload for event in yielded if event.kind == events.PLAN_REJECTED
        ]
        said = " ".join(rejected[0]["errors"])
        self.assertIn(f"{open_ids[0]}, {open_ids[1]}", said)

    def test_a_thread_closed_between_planning_and_running_is_still_refused(self):
        """The race the plan check cannot see: it closed while the turn ran."""
        thread = StoryThread.objects.create(
            session=self.session, text="the Sleeper must not wake", resolved=True
        )

        result = self.executor().run(
            PlannedAction(action="resolve_thread", thread_id=thread.pk),
            lambda _event: None,
        )

        self.assertFalse(result.ran)
        self.assertIn(f"no open thread with id {thread.pk}", result.error)

    def test_an_opened_thread_remembers_the_turn_that_opened_it(self):
        _outcome, _yielded, _model, turn = self.play(
            plan({"action": "open_thread", "text": "find the key"})
        )

        self.assertEqual(
            StoryThread.objects.get(session=self.session).opened_turn_id, turn.pk
        )

    def test_the_campaign_reaches_the_planning_call_too(self):
        """Not only the narration: the plan is made knowing where they are."""
        self.play(plan({"action": "move_party", "place": "the well beneath Farport"}))

        _outcome, _yielded, model, _turn = self.play(plan())

        planning = model.requests[0][-1]["content"]
        self.assertIn("the well beneath Farport", planning)


class PlanWrittenIntoTheNarrationTest(ExecutorTestCase):
    """A plan the model writes while narrating still runs (issue #603).

    The model sometimes answers with the plan it wanted to run next instead
    of planning it -- most often after seeing results it wants to act on.
    Suppressing the text keeps the machinery off the player's screen; before
    this, it also meant the actions never ran and the turn recorded nothing.
    """

    def play_two(self, raw_plan: str, narration: str, then: str = "Done."):
        """A turn whose narration carries a plan, and the reply that follows."""
        turn = GMTurnRecord.objects.create(
            user=self.user, session=self.session, outcome="running", mode=self.mode
        )
        model = FakeModel([raw_plan, narration, then])
        engine = TurnEngine(
            model,
            events.DatabaseSink(turn.pk),
            self.executor(turn_id=turn.pk),
            started_at=time.monotonic(),
            budget_seconds=60,
        )
        generator = engine.run(
            TurnRequest(
                policy=self.policy,
                prefix=[{"role": "system", "content": "S"}],
                latest="go",
                commit=lambda text: self.session.pk,
            )
        )
        yielded = []
        while True:
            try:
                yielded.append(next(generator))
            except StopIteration as stop:
                return stop.value, yielded, model, engine

    def test_the_actions_in_it_run(self):
        outcome, _yielded, _model, _engine = self.play_two(
            plan(),
            "To begin, I will note where we are.\n\n"
            + plan({"action": "move_party", "place": "the well beneath Farport"}),
        )

        self.assertEqual(
            CampaignState.objects.get(session=self.session).place,
            "the well beneath Farport",
        )
        self.assertEqual([action.action for action in outcome.actions], ["move_party"])

    def test_the_player_never_sees_the_plan(self):
        outcome, yielded, _model, _engine = self.play_two(
            plan(),
            "Noting it.\n" + plan({"action": "move_party", "place": "Farport"}),
            then="The party stands in Farport.",
        )

        self.assertNotIn("actions", outcome.narration)
        self.assertEqual(outcome.narration, "The party stands in Farport.")
        tokens = "".join(
            event.payload["token"] for event in yielded if event.kind == events.TOKEN
        )
        self.assertNotIn('{"actions"', tokens)

    def test_the_narration_is_written_again_knowing_what_ran(self):
        """The player reads a reply that accounts for the follow-up."""
        _outcome, _yielded, model, _engine = self.play_two(
            plan(),
            "Noting it.\n" + plan({"action": "move_party", "place": "Farport"}),
        )

        self.assertEqual(len(model.requests), 3, "plan, narrate, narrate again")
        last = model.requests[-1][-1]["content"]
        self.assertIn("Farport", last)

    def test_only_one_follow_up_a_turn(self):
        """A second leak is dropped: a follow-up is not a loop."""
        _outcome, _yielded, model, engine = self.play_two(
            plan(),
            "Noting it.\n" + plan({"action": "move_party", "place": "Farport"}),
            then="Still going.\n"
            + plan({"action": "move_party", "place": "Elsewhere"}),
        )

        self.assertEqual(len(model.requests), 3)
        self.assertEqual(
            CampaignState.objects.get(session=self.session).place, "Farport"
        )
        self.assertTrue(engine.followed_up)

    def test_a_leak_that_is_not_a_plan_is_dropped(self):
        _outcome, _yielded, model, _engine = self.play_two(
            plan(), 'Here is a thought.\n{"actions": "not a list"}'
        )

        self.assertEqual(len(model.requests), 2, "no follow-up narration")
        self.assertFalse(CampaignState.objects.filter(session=self.session).exists())

    def test_an_empty_leaked_plan_changes_nothing(self):
        _outcome, _yielded, model, _engine = self.play_two(
            plan(), "All quiet.\n" + plan()
        )

        self.assertEqual(len(model.requests), 2)

    def test_a_refused_action_in_the_follow_up_is_reported_not_run(self):
        """The follow-up is held to the same policy as any other plan."""
        self.play_two(
            plan(),
            "Noting it.\n" + plan({"action": "resolve_thread", "thread_id": 999}),
        )

        self.assertFalse(StoryThread.objects.filter(session=self.session).exists())


class _Deferred(Sequence):
    """Scripted replies, any of which may be written when it is asked for.

    A follow-up plan has to name the id of a character the first plan
    created, which does not exist when the script is written.
    """

    def __init__(self, *replies):
        self._replies = replies

    def __len__(self) -> int:
        return len(self._replies)

    def __getitem__(self, index):
        reply = self._replies[index]
        return reply() if callable(reply) else reply


class FollowUpReadsTheRecordsAfterTheFirstPlanTest(ExecutorTestCase):
    """The follow-up is held to the session as the first plan left it (#834).

    The ids a plan may name were read once, before the first plan ran, and
    handed unchanged to the plan the narration carried. A character the first
    plan started was then "no character with id N in this session" -- which
    the second narration read as fact and told the player -- and a thread it
    opened could not be closed.
    """

    game_master = False
    mode = MODE_PLAY

    def play_script(self, *replies):
        turn = GMTurnRecord.objects.create(
            user=self.user, session=self.session, outcome="running", mode=self.mode
        )
        model = FakeModel(_Deferred(*replies))
        engine = TurnEngine(
            model,
            events.DatabaseSink(turn.pk),
            self.executor(turn_id=turn.pk),
            started_at=time.monotonic(),
            budget_seconds=60,
        )
        generator = engine.run(
            TurnRequest(
                policy=self.policy,
                prefix=[{"role": "system", "content": "S"}],
                latest="make Wren",
                commit=lambda text: self.session.pk,
            )
        )
        while True:
            try:
                next(generator)
            except StopIteration as stop:
                return stop.value, model

    def test_a_character_the_first_plan_started_can_be_named(self):
        def leaked() -> str:
            wren = self.session.attached_characters().get(name="Wren")
            return "Wren stands. " + plan(
                {"action": "roll_fatigue", "character_id": wren.pk}
            )

        outcome, model = self.play_script(
            plan({"action": "roll_attributes", "new_character": {"name": "Wren"}}),
            leaked,
            "Wren is ready.",
        )

        wren = self.session.attached_characters().get(name="Wren")
        self.assertEqual(
            [action.action for action in outcome.actions],
            ["roll_attributes", "roll_fatigue"],
        )
        self.assertTrue(all(result.ran for result in outcome.results))
        self.assertIsNotNone(wren.fatigue_roll)
        self.assertNotIn("there is no character", model.requests[-1][-1]["content"])

    def test_a_thread_the_first_plan_opened_can_be_closed(self):
        def leaked() -> str:
            thread = StoryThread.objects.get(session=self.session)
            return "Settled. " + plan(
                {"action": "resolve_thread", "thread_id": thread.pk}
            )

        outcome, _model = self.play_script(
            plan({"action": "open_thread", "text": "Owes the ferryman a coin"}),
            leaked,
            "The ferryman is paid.",
        )

        thread = StoryThread.objects.get(session=self.session)
        self.assertTrue(thread.resolved)
        self.assertEqual(
            [action.action for action in outcome.actions],
            ["open_thread", "resolve_thread"],
        )

    def test_a_thread_the_first_plan_closed_is_not_closed_again(self):
        thread = StoryThread.objects.create(session=self.session, text="The debt")
        closing = plan({"action": "resolve_thread", "thread_id": thread.pk})

        outcome, model = self.play_script(closing, "Paid. " + closing, "Paid.")

        self.assertEqual(
            [action.action for action in outcome.actions], ["resolve_thread"]
        )
        self.assertEqual(len(model.requests), 2, "the repeat is not a plan to run")
        self.assertEqual(len(outcome.results), 1)


class RefusalsReachTheNextTurnTest(ExecutorTestCase):
    """A refused action is not planned again (issue #614).

    Session 255 spent all five of its turns planning record_attributes for a
    player who had stated their own scores, being refused each time for a
    reason that could not change, and recorded nothing. The refusal was
    invisible to the next turn, so the model had no way to know it had
    already tried.
    """

    game_master = False
    mode = MODE_PLAY

    def test_a_refusal_is_listed_in_the_next_turns_state(self):
        self.play(
            plan(
                {
                    "action": "record_attributes",
                    "new_character": {"name": "Wren", "label": ""},
                    "scores": {"strength": 13},
                }
            )
        )

        _outcome, _yielded, model, _turn = self.play(plan())

        planning = model.requests[0][-1]["content"]
        self.assertIn("Already refused in this session", planning)
        self.assertIn("record_attributes", planning)
        self.assertIn("rolled, not stated", planning)

    def test_the_narrating_call_is_told_too(self):
        """So the reply explains the refusal instead of ignoring it."""
        self.play(
            plan(
                {
                    "action": "record_attributes",
                    "new_character": {"name": "Wren", "label": ""},
                    "scores": {"strength": 13},
                }
            )
        )

        _outcome, _yielded, model, _turn = self.play(plan())

        self.assertIn(
            "Already refused in this session", model.requests[-1][-1]["content"]
        )

    def test_planning_a_settled_refusal_again_is_no_plan_at_all(self):
        """The turn is not spent asking a question already answered (#614).

        Session 517 planned record_attributes for a player's stated scores on
        two turns running, was refused both times for a reason that cannot
        change, and arrived at the fourth turn two turns behind itself. The
        repeat now makes the reply no plan, so the model is asked again
        inside the same turn with the reason and what to do instead.
        """
        stated = plan(
            {
                "action": "record_attributes",
                "new_character": {"name": "Wren", "label": ""},
                "scores": {"strength": 13},
            }
        )
        self.play(stated)

        _outcome, yielded, model, _turn = self.play(
            stated,
            replan=plan(
                {"action": "roll_attributes", "new_character": {"name": "Wren"}}
            ),
            narration="Rolling her attributes instead.",
        )

        rejected = [event for event in yielded if event.kind == events.PLAN_REJECTED]
        self.assertTrue(rejected, "the repeat should have been rejected")
        errors = " ".join(rejected[0].payload["errors"])
        self.assertIn("already refused in this session", errors)
        self.assertIn("plan roll_attributes", errors)
        # And the second try, inside the same turn, did the work.
        self.assertEqual(len(model.requests), 3, "plan, replan, narrate")
        wren = self.session.attached_characters().get()
        self.assertIsNotNone(wren.strength, "the attributes were rolled")

    def test_the_same_refusal_is_listed_once_however_often_it_happened(self):
        stated = plan(
            {
                "action": "record_attributes",
                "new_character": {"name": "Wren", "label": ""},
                "scores": {"strength": 13},
            }
        )
        self.play(stated)

        _outcome, _yielded, model, _turn = self.play(plan())

        planning = model.requests[0][-1]["content"]
        listed = planning.split("Already refused in this session")[1]
        self.assertEqual(listed.count("record_attributes"), 1)

    def test_a_session_with_nothing_refused_carries_no_section(self):
        _outcome, _yielded, model, _turn = self.play(plan())

        self.assertNotIn(
            "Already refused in this session", model.requests[0][-1]["content"]
        )

    def test_an_action_that_ran_is_not_listed(self):
        self.play(plan({"action": "roll", "dice": "3d6", "purpose": "a test"}))

        _outcome, _yielded, model, _turn = self.play(plan())

        self.assertNotIn(
            "Already refused in this session", model.requests[0][-1]["content"]
        )

    def test_one_session_does_not_see_anothers_refusals(self):
        other = GMSession.objects.create(user=self.user, name="other", mode=self.mode)
        self.play(
            plan(
                {
                    "action": "record_attributes",
                    "new_character": {"name": "Wren", "label": ""},
                    "scores": {"strength": 13},
                }
            )
        )

        self.assertEqual(refusal_lines(other), [])
        self.assertTrue(refusal_lines(self.session))


class RefusalMemoryIsKeyedByRoleTest(ExecutorTestCase):
    """A refusal settled while the account was a player is not a Game Master's.

    Issue #835: the settled-refusal memory rested on "an account's role does
    not change inside a session", but the role is read off the account every
    turn and an administrator may grant it at any time. A player made Game
    Master was still told "already refused ... That will not change by asking
    again" for stated scores, reviving and house rules, for the next 80
    events of the session.
    """

    game_master = False
    mode = MODE_PLAY

    STATED = plan(
        {
            "action": "record_attributes",
            "new_character": {"name": "Wren", "label": ""},
            "scores": {"strength": 13},
        }
    )

    def become_game_master(self) -> None:
        self.user.is_dm = True
        self.user.save(update_fields=["is_dm"])
        self.policy = TurnPolicy.for_turn(self.mode, is_game_master=True)

    def test_a_player_refusal_is_not_held_against_the_game_master(self):
        self.play(self.STATED)
        self.become_game_master()

        outcome, yielded, model, _turn = self.play(self.STATED)

        self.assertFalse(
            [event for event in yielded if event.kind == events.PLAN_REJECTED]
        )
        self.assertTrue(outcome.results[0].ran)
        self.assertEqual(self.session.attached_characters().get().strength, 13)
        self.assertNotIn(
            "Already refused in this session", model.requests[0][-1]["content"]
        )

    def test_the_player_is_still_held_to_it(self):
        """The neighbouring case #614 fixed stays fixed."""
        self.play(self.STATED)

        _outcome, yielded, model, _turn = self.play(
            self.STATED,
            replan=plan(
                {"action": "roll_attributes", "new_character": {"name": "Wren"}}
            ),
        )

        self.assertTrue(
            [event for event in yielded if event.kind == events.PLAN_REJECTED]
        )
        self.assertIn(
            "Already refused in this session", model.requests[0][-1]["content"]
        )

    def test_the_memory_reads_only_turns_taken_with_the_role(self):
        self.play(self.STATED)

        self.assertTrue(refusal_lines(self.session, is_game_master=False))
        self.assertEqual(refusal_lines(self.session, is_game_master=True), [])
        self.assertTrue(refusal_lines(self.session), "None reads every turn")
        self.assertEqual(settled_refusals(self.session, is_game_master=True), {})
        self.assertIn(
            "record_attributes", settled_refusals(self.session, is_game_master=False)
        )

    def test_a_settled_reason_the_policy_no_longer_gives_is_not_held(self):
        reading = read_plan(
            self.STATED,
            roster_ids=set(),
            policy=TurnPolicy.for_turn(MODE_PLAY, is_game_master=True),
            settled={"record_attributes": PLAYER_STATED_SCORES_REFUSAL},
        )

        self.assertTrue(reading.ok)
        self.assertEqual(reading.actions[0].refusal, "")


class AnotherPlayersCharacterTest(ExecutorTestCase):
    """The role is checked when the chat writes a character, not only when it
    is attached (issue #837).

    A Game Master may attach any player's character. The executor passed no
    ``authorize`` to the services, so after the account lost the role its
    session went on renaming, equipping and playing years for the other
    player's character, now under player rules.
    """

    game_master = True
    mode = MODE_PLAY

    def setUp(self):
        super().setUp()
        self.other = self.create_user(username="their_player")
        self.theirs = create_character(self.other, name="Brenna")
        self.session.characters.add(self.theirs)
        self.mine = create_character(self.user, name="Gorm")
        self.session.characters.add(self.mine)

    def lose_the_role(self) -> None:
        self.user.is_dm = False
        self.user.save(update_fields=["is_dm"])
        self.policy = TurnPolicy.for_turn(self.mode, is_game_master=False)

    def test_a_game_master_writes_another_players_character(self):
        """The neighbouring case: the role still carries its power."""
        outcome, *_rest = self.play(
            plan(
                {
                    "action": "rename_character",
                    "character_id": self.theirs.pk,
                    "name": "Brenna the Bold",
                }
            )
        )

        self.assertTrue(outcome.results[0].ran)
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.name, "Brenna the Bold")

    def test_after_losing_the_role_every_write_to_it_is_refused(self):
        self.lose_the_role()
        before = Character.objects.get(pk=self.theirs.pk)

        outcome, *_rest = self.play(
            plan(
                {
                    "action": "rename_character",
                    "character_id": self.theirs.pk,
                    "name": "Mine now",
                },
                {
                    "action": "add_equipment",
                    "character_id": self.theirs.pk,
                    "item": "rope",
                },
                {
                    "action": "play_years",
                    "character_id": self.theirs.pk,
                    "track": "random",
                    "years": 1,
                },
                {
                    "action": "set_background",
                    "character_id": self.theirs.pk,
                    "wealth": "Poor",
                },
            )
        )

        self.assertEqual([result.ran for result in outcome.results], [False] * 4)
        self.assertTrue(
            all(NOT_YOURS_REFUSAL in result.error for result in outcome.results),
            [result.error for result in outcome.results],
        )
        after = Character.objects.get(pk=self.theirs.pk)
        self.assertEqual(
            (after.name, after.equipment, after.experience_years, after.wealth_level),
            (
                before.name,
                before.equipment,
                before.experience_years,
                before.wealth_level,
            ),
        )

    def test_their_own_character_is_still_theirs_to_change(self):
        self.lose_the_role()

        outcome, *_rest = self.play(
            plan(
                {
                    "action": "rename_character",
                    "character_id": self.mine.pk,
                    "name": "Gorm the Younger",
                }
            )
        )

        self.assertTrue(outcome.results[0].ran)

    def test_the_role_is_read_when_the_write_happens(self):
        """Not from the turn's policy: a role withdrawn mid-turn holds."""
        executor = self.executor()
        self.user.is_dm = False
        self.user.save(update_fields=["is_dm"])

        result = executor.run(
            PlannedAction(
                action="rename_character",
                character_id=self.theirs.pk,
                name="Taken",
            ),
            lambda event: None,
        )

        self.assertFalse(result.ran)
        self.assertIn(NOT_YOURS_REFUSAL, result.error)


class NoSecondCharacterOfTheSameNameTest(ExecutorTestCase):
    """A plan may not start a character the session already holds (#856).

    On the #760 board a player's second turn planned ``roll_attributes`` with
    ``new_character: {"name": "Wren"}`` for the Wren made the turn before;
    the server rolled a second Wren and played five years on her, and the
    first Wren stayed at nought. Players and Game Masters alike: a Game
    Master who wants two of a name can name the second differently.
    """

    game_master = False
    mode = MODE_PLAY

    SECOND_WREN = plan(
        {
            "action": "roll_attributes",
            "new_character": {"name": "Wren", "label": "the sister"},
        },
        {"action": "roll_fatigue", "new_character_ref": "Wren"},
        {
            "action": "play_years",
            "new_character_ref": "Wren",
            "track": "campaigner",
            "years": 5,
        },
    )

    def make_wren(self):
        self.play(
            plan({"action": "roll_attributes", "new_character": {"name": "Wren"}})
        )
        return self.session.attached_characters().get(name="Wren")

    def test_the_plan_is_sent_back_naming_the_one_held(self):
        wren = self.make_wren()

        _outcome, yielded, model, _turn = self.play(
            self.SECOND_WREN,
            replan=plan(
                {"action": "roll_fatigue", "character_id": wren.pk},
                {
                    "action": "play_years",
                    "character_id": wren.pk,
                    "track": "campaigner",
                    "years": 5,
                },
            ),
        )

        rejected = [event for event in yielded if event.kind == events.PLAN_REJECTED]
        self.assertTrue(rejected)
        errors = " ".join(rejected[0].payload["errors"])
        self.assertIn(
            f"Wren is already a character in this session, id {wren.pk}", errors
        )
        self.assertIn(f'"character_id": {wren.pk}', errors)
        self.assertEqual(self.session.attached_characters().count(), 1)
        wren.refresh_from_db()
        self.assertGreater(wren.experience_years, 0, "the years went to the one Wren")

    def test_a_game_master_is_held_to_it_too(self):
        # A Game Master's account, not a player's handed the authority: the
        # executor reads the role off the account (second review of #837).
        self.user.is_dm = True
        self.user.save(update_fields=["is_dm"])
        self.policy = TurnPolicy.for_turn(self.mode, is_game_master=True)
        wren = self.make_wren()

        reading = read_plan(
            self.SECOND_WREN,
            roster_ids={wren.pk},
            policy=self.policy,
            held_names={"wren": wren.pk},
        )

        self.assertFalse(reading.ok)

    def test_a_different_name_is_a_new_character(self):
        """The neighbouring case: a sister of her own is still made."""
        self.make_wren()

        self.play(
            plan({"action": "roll_attributes", "new_character": {"name": "Wrenna"}})
        )

        self.assertEqual(
            sorted(character.name for character in self.characters()),
            ["Wren", "Wrenna"],
        )

    def test_one_plan_cannot_start_the_same_name_twice(self):
        reading = read_plan(
            plan(
                {"action": "roll_attributes", "new_character": {"name": "Wren"}},
                {"action": "roll_attributes", "new_character": {"name": "wren"}},
            ),
            roster_ids=set(),
            policy=self.policy,
        )

        self.assertFalse(reading.ok)
        self.assertIn("already started earlier in this plan", reading.errors[0])

    def test_the_executor_refuses_it_even_unread(self):
        wren = self.make_wren()

        result = self.executor().run(
            PlannedAction(
                action="roll_attributes", new_character=NewCharacter(name="WREN")
            ),
            lambda event: None,
        )

        self.assertFalse(result.ran)
        self.assertIn(
            f"already has a character called Wren (id {wren.pk})", result.error
        )
        self.assertEqual(self.session.attached_characters().count(), 1)


class GameMasterRecordsTest(ExecutorTestCase):
    """A Game Master's death, XP, coin and stated Fatigue in chat (issue #802).

    All four were his on the sheet and missing from the chat: an aide told
    "Gorn died" or "give them 500 XP" could record neither, and a Fatigue
    roll made at his table could not be stated.
    """

    game_master = True
    mode = MODE_AIDE

    def setUp(self):
        super().setUp()
        self.gorn = create_character(self.user, name="Gorn", starting_coin=100)
        self.session.characters.add(self.gorn)

    def run_one(self, **fields):
        outcome, *_rest = self.play(plan({"character_id": self.gorn.pk, **fields}))
        self.gorn.refresh_from_db()
        return outcome.results[0]

    def test_a_death_is_marked_with_its_cause(self):
        result = self.run_one(action="mark_dead", cause="fell from the tower")

        self.assertTrue(result.ran, result.error)
        self.assertFalse(self.gorn.is_alive)
        self.assertEqual(self.gorn.death_cause, "fell from the tower")
        self.assertIn("is_alive", self.gorn.history[-1]["field"])

    def test_the_dead_are_not_marked_twice(self):
        self.run_one(action="mark_dead", cause="the fall")

        result = self.run_one(action="mark_dead", cause="again")

        self.assertFalse(result.ran)
        self.assertIn("already dead", result.error)
        self.assertEqual(self.gorn.death_cause, "the fall")

    def test_xp_is_added_to_the_lifetime_total(self):
        before = self.gorn.total_xp

        result = self.run_one(action="award_xp", xp=500)

        self.assertTrue(result.ran, result.error)
        self.assertEqual(self.gorn.total_xp, before + 500)
        self.assertIn("awarded 500 XP", result.result)

    def test_xp_awarded_in_chat_earns_the_skill_points_the_sheet_would(self):
        """Through the sheet's change_field, so #769's grant comes with it."""
        before = self.gorn.free_skill_points

        result = self.run_one(action="award_xp", xp=3000)

        self.assertTrue(result.ran, result.error)
        # prior-experience.md: "at 3,000 XP you would have earned 2 skill points"
        self.assertEqual(self.gorn.free_skill_points, before + 2)

    def test_coin_is_given_taken_and_set(self):
        given = self.run_one(action="set_coin", change=50, reason="the bounty")
        self.assertTrue(given.ran, given.error)
        self.assertEqual(self.gorn.purse, 150)

        taken = self.run_one(action="set_coin", change=-30)
        self.assertTrue(taken.ran, taken.error)
        self.assertEqual(self.gorn.purse, 120)

        set_to = self.run_one(action="set_coin", purse=10)
        self.assertTrue(set_to.ran, set_to.error)
        self.assertEqual(self.gorn.purse, 10)

    def test_a_take_the_purse_cannot_cover_is_refused(self):
        result = self.run_one(action="set_coin", change=-500)

        self.assertFalse(result.ran)
        self.assertIn("Not enough coin", result.error)
        self.assertEqual(self.gorn.purse, 100)

    def test_a_stated_fatigue_roll_is_recorded(self):
        result = self.run_one(action="roll_fatigue", value=9)

        self.assertTrue(result.ran, result.error)
        self.assertEqual(self.gorn.fatigue_roll, 9)

    def test_a_skill_check_rolls_through_the_sheets_rule(self):
        self.gorn.skills = {"stealth": 3}
        self.gorn.save()

        result = self.run_one(action="skill_check", skill="Stealth", difficulty=10)

        self.assertTrue(result.ran, result.error)
        self.assertIn("stealth check: 2d6", result.result)
        self.assertIn("vs 10", result.result)
        self.assertEqual(self.gorn.history[-1]["field"], "skill_check")

    def test_the_game_master_states_the_dice_and_the_attribute(self):
        result = self.run_one(
            action="skill_check",
            skill="stealth",
            total=12,
            attribute="wisdom",
            difficulty=30,
        )

        self.assertTrue(result.ran, result.error)
        self.assertIn("2d6 stated by a Game Master = 12", result.result)
        self.assertIn("WIS named by a Game Master", result.result)
        self.assertIn("success", result.result, "a natural 12 always succeeds")


class PlayersAreHeldToTheRecordsRulesTest(ExecutorTestCase):
    """A player's session gets none of the Game Master's records (issue #802)."""

    game_master = False
    mode = MODE_PLAY

    def setUp(self):
        super().setUp()
        self.wren = create_character(self.user, name="Wren", starting_coin=100)
        self.session.characters.add(self.wren)

    def test_death_xp_and_coin_are_refused_and_change_nothing(self):
        before = Character.objects.get(pk=self.wren.pk)

        outcome, *_rest = self.play(
            plan(
                {"action": "mark_dead", "character_id": self.wren.pk, "cause": "x"},
                {"action": "award_xp", "character_id": self.wren.pk, "xp": 500},
                {"action": "set_coin", "character_id": self.wren.pk, "change": 50},
            )
        )

        self.assertEqual([result.ran for result in outcome.results], [False] * 3)
        self.assertTrue(
            all(
                result.error == GAME_MASTER_RECORDS_REFUSAL
                for result in outcome.results
            )
        )
        after = Character.objects.get(pk=self.wren.pk)
        self.assertEqual(
            (after.is_alive, after.total_xp, after.purse),
            (before.is_alive, before.total_xp, before.purse),
        )

    def test_a_players_fatigue_is_rolled_not_stated(self):
        self.wren.fatigue_roll = None
        self.wren.save()

        outcome, *_rest = self.play(
            plan({"action": "roll_fatigue", "character_id": self.wren.pk, "value": 12})
        )

        self.assertFalse(outcome.results[0].ran)
        self.assertEqual(outcome.results[0].error, PLAYER_STATED_FATIGUE_REFUSAL)
        self.wren.refresh_from_db()
        self.assertIsNone(self.wren.fatigue_roll)

    def test_a_stated_fatigue_refusal_does_not_hold_back_a_roll(self):
        """Settled for the value, not for rolling: the next turn may roll."""
        self.wren.fatigue_roll = None
        self.wren.save()
        self.play(
            plan({"action": "roll_fatigue", "character_id": self.wren.pk, "value": 12})
        )

        outcome, yielded, _model, _turn = self.play(
            plan({"action": "roll_fatigue", "character_id": self.wren.pk})
        )

        self.assertFalse(
            [event for event in yielded if event.kind == events.PLAN_REJECTED]
        )
        self.assertTrue(outcome.results[0].ran, outcome.results[0].error)

    def test_a_player_rolls_a_skill_check_with_no_difficulty(self):
        self.wren.skills = {"stealth": 3}
        self.wren.save()

        outcome, *_rest = self.play(
            plan(
                {
                    "action": "skill_check",
                    "character_id": self.wren.pk,
                    "skill": "stealth",
                }
            )
        )

        self.assertTrue(outcome.results[0].ran, outcome.results[0].error)
        self.assertNotIn(" vs ", outcome.results[0].result)

    def test_a_player_may_not_set_the_difficulty(self):
        """Sent back inside the turn, so the check the player asked for rolls."""
        self.wren.skills = {"stealth": 3}
        self.wren.save()
        check = {
            "action": "skill_check",
            "character_id": self.wren.pk,
            "skill": "stealth",
        }

        outcome, yielded, _model, _turn = self.play(
            plan({**check, "difficulty": 2}), replan=plan(check)
        )

        rejected = [event for event in yielded if event.kind == events.PLAN_REJECTED]
        self.assertIn("carries no difficulty", " ".join(rejected[0].payload["errors"]))
        self.assertTrue(outcome.results[0].ran, outcome.results[0].error)
        self.assertNotIn(" vs ", outcome.results[0].result)

    def test_the_service_refuses_a_players_difficulty_too(self):
        """Behind the plan reader, the sheet's own rule holds."""
        self.wren.skills = {"stealth": 3}
        self.wren.save()

        result = self.executor().run(
            PlannedAction(
                action="skill_check",
                character_id=self.wren.pk,
                skill="stealth",
                difficulty=2,
            ),
            lambda event: None,
        )

        self.assertFalse(result.ran)
        self.assertIn("Only a Game Master can set the difficulty", result.error)


class FatigueIsRolledAtCreationTest(ExecutorTestCase):
    """A chat-built character's Fatigue is rolled as it is made (issue #859).

    The creation page rolls the 2d6 with everything else; a session left it
    null until a plan asked. Under the standing rule, as the coordinator
    ruled it, the chat rolls it at creation, and a plan that then lists the
    roll is told it was made rather than refused.
    """

    game_master = False
    mode = MODE_PLAY

    def test_a_roll_planned_with_the_character_is_reported_not_rerolled(self):
        outcome, *_rest = self.play(
            plan(
                {"action": "roll_attributes", "new_character": {"name": "Wren"}},
                {"action": "roll_fatigue", "new_character_ref": "Wren"},
            )
        )

        wren = self.session.attached_characters().get()
        self.assertTrue(all(result.ran for result in outcome.results))
        self.assertIn(
            f"Fatigue roll made when the character was made: {wren.fatigue_roll}",
            outcome.results[1].result,
        )
        self.assertEqual(wren.dice_log.count("Fatigue:"), 1, "rolled once")
        self.assertTrue(wren.is_complete)

    def test_a_players_later_roll_is_a_second_roll(self):
        self.play(
            plan({"action": "roll_attributes", "new_character": {"name": "Wren"}})
        )
        wren = self.session.attached_characters().get()
        first = wren.fatigue_roll

        outcome, *_rest = self.play(
            plan({"action": "roll_fatigue", "character_id": wren.pk})
        )

        self.assertFalse(outcome.results[0].ran)
        self.assertIn("only a Game Master can roll again", outcome.results[0].error)
        wren.refresh_from_db()
        self.assertEqual(wren.fatigue_roll, first)

    def test_a_game_masters_stated_roll_replaces_the_creation_roll(self):
        self.policy = TurnPolicy.for_turn(self.mode, is_game_master=True)
        self.user.is_dm = True
        self.user.save(update_fields=["is_dm"])

        outcome, *_rest = self.play(
            plan(
                {"action": "roll_attributes", "new_character": {"name": "Wren"}},
                {"action": "roll_fatigue", "new_character_ref": "Wren", "value": 11},
            )
        )

        self.assertTrue(outcome.results[1].ran, outcome.results[1].error)
        self.assertEqual(self.session.attached_characters().get().fatigue_roll, 11)


class DemotedMidTurnTest(ExecutorTestCase):
    """A Game Master demoted part-way through a turn loses the role's writes.

    Review of #837: the account was re-read for ownership on every write, but
    the Game Master authority still came from the policy the turn started
    with, so a Game Master demoted mid-turn went on awarding XP, moving coin
    and marking deaths on his own characters.
    """

    game_master = True
    mode = MODE_AIDE

    def setUp(self):
        super().setUp()
        self.gorn = create_character(self.user, name="Gorn", starting_coin=100)
        self.gorn.skills = {"stealth": 3}
        self.gorn.is_alive = True
        self.gorn.save()
        self.session.characters.add(self.gorn)

    def demoted_executor(self):
        executor = self.executor()
        self.user.is_dm = False
        self.user.save(update_fields=["is_dm"])
        return executor

    def test_every_game_master_write_is_refused_after_the_demotion(self):
        executor = self.demoted_executor()
        gorn = self.gorn.pk
        for action in (
            PlannedAction(action="award_xp", character_id=gorn, xp=5000),
            PlannedAction(action="set_coin", character_id=gorn, coin_change="500.00"),
            PlannedAction(action="mark_dead", character_id=gorn, cause="a fall"),
            PlannedAction(action="roll_fatigue", character_id=gorn, fatigue_value=12),
            PlannedAction(
                action="skill_check", character_id=gorn, skill="stealth", difficulty=2
            ),
            PlannedAction(action="revive_character", character_id=gorn),
            PlannedAction(action="record_house_rule", topic="crits", rule="no crits"),
        ):
            with self.subTest(action=action.action):
                result = executor.run(action, lambda event: None)
                self.assertFalse(result.ran, result.result)

        after = Character.objects.get(pk=gorn)
        self.assertEqual((after.total_xp, after.purse, after.is_alive), (0, 100, True))

    def test_later_actions_run_under_a_players_rules(self):
        """Not refused outright: a plain skill check still rolls, as a player's."""
        executor = self.demoted_executor()

        result = executor.run(
            PlannedAction(
                action="skill_check", character_id=self.gorn.pk, skill="stealth"
            ),
            lambda event: None,
        )

        self.assertTrue(result.ran, result.error)
        self.assertNotIn(" vs ", result.result)

    def test_a_game_master_who_keeps_the_role_keeps_its_writes(self):
        """The neighbouring case: no demotion, nothing refused."""
        result = self.executor().run(
            PlannedAction(action="award_xp", character_id=self.gorn.pk, xp=5000),
            lambda event: None,
        )

        self.assertTrue(result.ran, result.error)
        self.assertEqual(Character.objects.get(pk=self.gorn.pk).total_xp, 5000)


class DemotedBeforeTheExecutorTest(ExecutorTestCase):
    """A role withdrawn between the policy and the executor is caught.

    Second review of #837: the turn's policy is read when the request
    arrives, the executor is built only after a fold that may take the whole
    budget, and the executor took its own first read as the baseline. An
    admin demoting the Game Master during the fold left the policy a Game
    Master's, the baseline a player's, and nothing withdrawn, so every Game
    Master action in the turn ran.
    """

    game_master = True
    mode = MODE_AIDE

    def test_the_refusals_hold_when_the_demotion_lands_before_the_executor(self):
        gorn = create_character(self.user, name="Gorn", starting_coin=100)
        self.session.characters.add(gorn)
        # The policy (self.policy) was built with the role; then it goes.
        self.user.is_dm = False
        self.user.save(update_fields=["is_dm"])
        executor = self.executor()

        for action in (
            PlannedAction(action="award_xp", character_id=gorn.pk, xp=5000),
            PlannedAction(
                action="set_coin", character_id=gorn.pk, coin_change="500.00"
            ),
            PlannedAction(action="mark_dead", character_id=gorn.pk, cause="a fall"),
            PlannedAction(
                action="roll_fatigue", character_id=gorn.pk, fatigue_value=12
            ),
            PlannedAction(
                action="skill_check",
                character_id=gorn.pk,
                skill="stealth",
                difficulty=2,
            ),
            PlannedAction(action="revive_character", character_id=gorn.pk),
            PlannedAction(action="record_house_rule", topic="crits", rule="no crits"),
        ):
            with self.subTest(action=action.action):
                self.assertFalse(executor.run(action, lambda event: None).ran)

        after = Character.objects.get(pk=gorn.pk)
        self.assertEqual((after.total_xp, after.purse, after.is_alive), (0, 100, True))

    def test_a_turn_through_the_view_demoted_during_the_fold_is_refused(self):
        """The same, end to end: the demotion lands while the recap is written."""
        from .test_summary import turns

        gorn = create_character(self.user, name="Gorn", starting_coin=100)
        self.session.characters.add(gorn)
        self.session.history = turns(400, words=120)
        self.session.save()
        self.login_as(self.user)

        from . import services as gm_services

        real_save_fold = gm_services.save_fold

        def demote_while_folding(*args, **kwargs):
            # On the request's own thread and connection, where the recap's
            # worker thread's writes would not be seen inside a test case.
            type(self.user).objects.filter(pk=self.user.pk).update(is_dm=False)
            return real_save_fold(*args, **kwargs)

        award = plan({"action": "award_xp", "character_id": gorn.pk, "xp": 5000})
        with (
            patch("gm.services.write_session_recap", return_value="A recap."),
            patch("gm.views.save_fold", side_effect=demote_while_folding),
            patch(
                "gm.turn_engine.model.ollama_client.stream_chat",
                side_effect=gm_replies("Done.", plans=[award]),
            ),
        ):
            response = self.client.post(
                "/tarmar/gm/stream/",
                json.dumps(
                    {"session_id": self.session.pk, "message": "Give Gorn 5000 XP"}
                ),
                content_type="application/json",
            )
            body = b"".join(response.streaming_content).decode()

        self.assertIn("Not done: awarding XP", body)
        self.assertEqual(Character.objects.get(pk=gorn.pk).total_xp, 0)
