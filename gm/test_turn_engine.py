"""The turn engine's states, driven by a fake model (issue #590)."""

import json
import time
from dataclasses import dataclass, field
from unittest.mock import ANY, patch

from django.test import SimpleTestCase, override_settings

from characters.magic import known_spells, learnable_spells
from characters.purse import format_purse
from common.services import ollama_client
from common.test_helpers import (
    ACT_ONCE_SET_SECONDS,
    STAND_IN_TIMEOUT,
    BaseTestCase,
    HandFiredDeadlines,
    StallingOllama,
)

from . import engine as prompts
from .models import GMEvent, GMSession, GMTurnRecord
from .refusal_words import refusal_words
from .testing import add_character
from .turn_engine import events
from .turn_engine.engine import (
    ActionResult,
    TurnBudgetExceeded,
    TurnEngine,
    TurnRequest,
    TurnState,
)
from .turn_engine.messages import PLAN_UNREADABLE
from .turn_engine.model import FakeModel, KeepAliveTokens, OllamaModel, Paused
from .turn_engine.plan import PlannedAction
from .turn_engine.policy import TurnPolicy
from .turn_engine.state import (
    BODY_TOPIC,
    FATIGUE_TOPIC,
    MAX_SHEET_ENTRIES_LISTED,
    NO_CHARACTERS,
    SKILL_POINTS_TOPIC,
    CampaignBlock,
    LedgerFigure,
    RosterLine,
    key_figures,
    roster_lines,
    state_block,
)

GAME_MASTER = TurnPolicy.for_turn(prompts.MODE_AIDE, is_game_master=True)
PLAYER = TurnPolicy.for_turn(prompts.MODE_PLAY, is_game_master=False)
SYSTEM = {"role": "system", "content": "SYSTEM PROMPT"}
PREFIX = [
    SYSTEM,
    {"role": "user", "content": "earlier"},
    {"role": "assistant", "content": "reply"},
]
TWAIN = RosterLine(character_id=41, name="Twain", label="the brother", figures="STR 18")
# The suite's conftest replaces ollama_client.open_stream for every test; the
# stalled-stream tests need a real socket (issue #904), so they take the real
# one, captured at import, before any fixture runs.
REAL_OPEN_STREAM = ollama_client.open_stream


@dataclass
class ScriptedExecutor:
    """Runs every action by saying so, and records one event each."""

    lines: list[RosterLine] = field(default_factory=lambda: [TWAIN])
    ran: list[PlannedAction] = field(default_factory=list)
    ruling_lines: list[str] = field(default_factory=list)
    campaign_block: CampaignBlock = field(default_factory=CampaignBlock)
    refusal_lines_seen: list[str] = field(default_factory=list)
    settled_refusals_seen: dict[str, str] = field(default_factory=dict)
    begun: list[list[str]] = field(default_factory=list)

    def label(self, action: PlannedAction) -> str:
        return f"running {action.action}"

    def run(self, action, record):
        self.ran.append(action)
        if action.refused:
            return ActionResult(
                result=f"refused: {action.refusal}", ran=False, error=action.refusal
            )
        record(events.TurnEvent(events.ACTION, {"action": action.action}, visible=True))
        return ActionResult(result=f"{action.action} ran", ran=True, note="ran")

    def begin(self, actions) -> None:
        # Each plan as it was handed over, and how many actions had run then.
        self.begun.append([action.action for action in actions] + [len(self.ran)])

    def roster(self) -> list[RosterLine]:
        return list(self.lines)

    def campaign(self) -> CampaignBlock:
        return self.campaign_block

    def refused(self) -> list[str]:
        return list(self.refusal_lines_seen)

    def settled(self) -> dict[str, str]:
        return dict(self.settled_refusals_seen)

    def rulings(self) -> list[str]:
        return list(self.ruling_lines)


def run_turn(engine: TurnEngine, request: TurnRequest):
    """Every event the turn yields, and how it ended."""
    generator = engine.run(request)
    yielded = []
    while True:
        try:
            yielded.append(next(generator))
        except StopIteration as stop:
            return yielded, stop.value


def request(policy=GAME_MASTER, **overrides) -> TurnRequest:
    fields = {
        "policy": policy,
        "prefix": list(PREFIX),
        "latest": "twins called Twain and Thread, all 18s",
        "commit": lambda narration: 7,
    }
    fields.update(overrides)
    return TurnRequest(**fields)


def engine_for(model, executor=None, sink=None, **options) -> TurnEngine:
    return TurnEngine(
        model,
        sink if sink is not None else events.MemorySink(),
        executor if executor is not None else ScriptedExecutor(),
        started_at=time.monotonic(),
        budget_seconds=options.pop("budget_seconds", 60),
        **options,
    )


# Two characters the roster does not already hold: the scripted roster lists
# Twain, and a plan starting a second Twain is sent back (issue #856).
TWINS_PLAN = json.dumps(
    {
        "actions": [
            {
                "action": "record_attributes",
                "new_character": {"name": "Wren"},
                "all": 18,
            },
            {
                "action": "record_attributes",
                "new_character": {"name": "Thread"},
                "all": 18,
            },
        ]
    }
)


class TurnEngineTest(SimpleTestCase):
    def test_a_turn_plans_runs_narrates_and_commits(self):
        model = FakeModel([TWINS_PLAN, ["Two figures ", "step forward."]])
        executor = ScriptedExecutor()
        sink = events.MemorySink()
        committed = []

        def commit(narration):
            committed.append(narration)
            return 7

        yielded, outcome = run_turn(
            engine_for(model, executor, sink), request(commit=commit)
        )

        states = [
            event.payload["state"]
            for event in sink.events
            if event.kind == events.STATE
        ]
        self.assertEqual(
            states,
            ["grounding", "planning", "executing", "narrating", "committing", "done"],
        )
        self.assertEqual(len(executor.ran), 2)
        self.assertEqual(outcome.narration, "Two figures step forward.")
        self.assertEqual(outcome.session_id, 7)
        self.assertEqual(committed, ["Two figures step forward."])
        tokens = [
            event.payload["token"] for event in yielded if event.kind == events.TOKEN
        ]
        self.assertEqual(tokens, ["Two figures ", "step forward."])
        statuses = [
            event.payload["status"] for event in yielded if event.kind == events.STATUS
        ]
        self.assertEqual(
            statuses,
            [
                "planning",
                "running record_attributes",
                "running record_attributes",
                "writing",
            ],
        )
        self.assertIn(events.ACTION, sink.kinds())
        self.assertNotIn(
            events.TOKEN, [event.kind for event in sink.events if event.persist]
        )

    def test_the_executor_reads_the_whole_plan_before_its_first_action(self):
        """#970: a plan that states a new character's Origin starts it with
        the Origin unrolled, which the executor can know only from the plan."""
        executor = ScriptedExecutor()

        run_turn(engine_for(FakeModel([TWINS_PLAN, "Done."]), executor), request())

        self.assertEqual(
            executor.begun, [["record_attributes", "record_attributes", 0]]
        )

    def test_plan_and_narrate_calls_share_the_prefix_byte_for_byte(self):
        model = FakeModel([TWINS_PLAN, "Done."])

        run_turn(engine_for(model), request())

        plan_call, narrate_call = model.requests
        self.assertEqual(plan_call[:-1], PREFIX)
        self.assertEqual(narrate_call[:-1], PREFIX)
        self.assertIn("This message is from the Game Master.", plan_call[-1]["content"])
        self.assertIn("- id 41: Twain (the brother) — STR 18", plan_call[-1]["content"])
        self.assertTrue(
            plan_call[-1]["content"].endswith(
                "Latest message: twins called Twain and Thread, all 18s"
            )
        )
        narrate = narrate_call[-1]["content"]
        self.assertIn(
            "Server results: \n- record_attributes ran\n- record_attributes ran",
            narrate,
        )
        self.assertIn("Narrate this turn from the results.", narrate)
        for word in ("[[", "procedure", "marker"):
            self.assertNotIn(word, narrate)

    def test_the_request_log_keeps_the_prefix_once(self):
        sink = events.MemorySink()
        run_turn(engine_for(FakeModel([TWINS_PLAN, "Done."]), sink=sink), request())

        plan_request, narrate_request = [
            event.payload for event in sink.events if event.kind == events.MODEL_REQUEST
        ]
        self.assertEqual(plan_request["messages"][0]["role"], "system")
        self.assertNotIn("content", plan_request["messages"][0])
        self.assertEqual(len(plan_request["messages"][0]["prompt_sha256"]), 64)
        self.assertEqual(len(plan_request["messages"]), len(PREFIX) + 1)
        self.assertEqual(narrate_request["prefix"], "unchanged")
        self.assertEqual(narrate_request["prefix_messages"], len(PREFIX))
        self.assertEqual(len(narrate_request["messages"]), 1)
        responses = [
            event.payload
            for event in sink.events
            if event.kind == events.MODEL_RESPONSE
        ]
        self.assertEqual(
            [response["raw"] for response in responses], [TWINS_PLAN, "Done."]
        )

    def test_a_prose_plan_is_asked_again_once_with_its_errors(self):
        model = FakeModel(["I shall record them.", TWINS_PLAN, "Done."])
        sink = events.MemorySink()
        executor = ScriptedExecutor()

        _yielded, outcome = run_turn(engine_for(model, executor, sink), request())

        replan = model.requests[1]
        self.assertEqual(replan[: len(PREFIX)], PREFIX)
        self.assertEqual(
            replan[-2], {"role": "assistant", "content": "I shall record them."}
        )
        self.assertIn("not a usable plan", replan[-1]["content"])
        self.assertIn("not JSON", replan[-1]["content"])
        self.assertEqual(len(executor.ran), 2)
        self.assertFalse(outcome.plan_unreadable)
        self.assertEqual(sink.kinds().count(events.PLAN_REJECTED), 1)

    def test_a_second_bad_plan_runs_nothing_and_says_so(self):
        model = FakeModel(
            ["prose", '{"actions": [{"action": "fly"}]}', "Nothing happened."]
        )
        sink = events.MemorySink()
        executor = ScriptedExecutor()

        _yielded, outcome = run_turn(engine_for(model, executor, sink), request())

        self.assertEqual(executor.ran, [])
        self.assertTrue(outcome.plan_unreadable)
        self.assertIn(PLAN_UNREADABLE, model.requests[2][-1]["content"])
        self.assertEqual(sink.kinds().count(events.PLAN_REJECTED), 2)
        self.assertNotIn(events.PLAN, sink.kinds())

    def test_refused_actions_are_reported_in_order(self):
        raw = json.dumps(
            {
                "actions": [
                    {"action": "record_attributes", "character_id": 41, "all": 18}
                ]
            }
        )
        model = FakeModel([raw, "No."])

        _yielded, outcome = run_turn(engine_for(model), request(policy=PLAYER))

        self.assertIn("This message is from a player", model.requests[0][-1]["content"])
        self.assertFalse(outcome.results[0].ran)
        self.assertIn(
            refusal_words().policy.player_stated_scores,
            model.requests[1][-1]["content"],
        )

    def test_a_turn_with_known_results_skips_planning(self):
        model = FakeModel(["You rolled well."])
        sink = events.MemorySink()

        run_turn(
            engine_for(model, sink=sink),
            request(plan=False, known_results=("2d6 rolled 7",)),
        )

        self.assertEqual(len(model.requests), 1)
        self.assertIn("- 2d6 rolled 7", model.requests[0][-1]["content"])
        self.assertNotIn(
            "planning", [event.payload.get("state") for event in sink.events]
        )

    def test_a_turn_that_does_not_plan_runs_no_plan_its_narration_carries(self):
        """A Retry of a turn whose actions ran must not run them again (#782).

        The narrate message of a resumed turn repeats the original request
        ("play five years for Twain"), which invites the model to write the
        plan into its reply. That plan used to run, so the years were played
        twice across the break, and the second narration lost the results
        the turn was resumed with.
        """
        leaked = "Twain grows older. " + json.dumps(
            {
                "actions": [
                    {
                        "action": "play_years",
                        "character_id": 41,
                        "track": "random",
                        "years": 5,
                    }
                ]
            }
        )
        model = FakeModel([leaked])
        executor = ScriptedExecutor()
        sink = events.MemorySink()

        _yielded, outcome = run_turn(
            engine_for(model, executor=executor, sink=sink),
            request(
                policy=PLAYER,
                plan=False,
                known_results=("Twain: played 5 of 5 year(s) on the soldier track.",),
            ),
        )

        self.assertEqual(executor.ran, [])
        self.assertEqual(outcome.actions, ())
        self.assertEqual(len(model.requests), 1, "narrated once, not again")
        self.assertIn("played 5 of 5", model.requests[0][-1]["content"])
        self.assertEqual(outcome.narration, "Twain grows older.")
        self.assertNotIn(
            events.PLAN, [event.kind for event in sink.events], "no plan was taken"
        )

    def test_a_follow_up_narration_keeps_what_was_known_before_the_turn(self):
        """The second narration is told everything the first was (#782)."""
        leaked = "Noting it. " + json.dumps(
            {"actions": [{"action": "move_party", "place": "Farport"}]}
        )
        model = FakeModel([json.dumps({"actions": []}), leaked, "In Farport."])

        run_turn(
            engine_for(model),
            request(known_results=("The player rolled 2d6: [3, 4] = 7.",)),
        )

        self.assertEqual(len(model.requests), 3)
        second = model.requests[2][-1]["content"]
        self.assertIn("The player rolled 2d6: [3, 4] = 7.", second)
        self.assertIn("move_party ran", second)

    def test_thinking_is_never_narrated(self):
        model = FakeModel(
            ['{"actions": []}', ["<think>hmm</think>", "The door opens."]]
        )

        yielded, outcome = run_turn(engine_for(model), request())

        self.assertEqual(outcome.narration, "The door opens.")
        self.assertNotIn("hmm", "".join(e.payload.get("token", "") for e in yielded))

    def test_silence_is_reported_as_waiting(self):
        model = FakeModel([Paused(0.3, '{"actions": []}'), "Fine."])

        yielded, _outcome = run_turn(
            engine_for(model, keepalive_seconds=0.05), request()
        )

        waiting = [event for event in yielded if event.kind == events.WAITING]
        self.assertTrue(waiting)
        self.assertEqual(waiting[0].payload["status"], "planning")
        self.assertFalse(waiting[0].persist)

    def test_running_out_of_time_fails_the_turn(self):
        model = FakeModel([Paused(0.2, "late")])
        sink = events.MemorySink()
        engine = engine_for(
            model, sink=sink, budget_seconds=0.05, keepalive_seconds=0.02
        )

        with self.assertRaises(TurnBudgetExceeded):
            run_turn(engine, request())

        self.assertEqual(engine.state, TurnState.FAILED)
        failed = [event for event in sink.events if event.kind == events.FAILED]
        self.assertEqual(
            failed[0].payload,
            {"state": "planning", "error": "the turn ran out of time"},
        )

    def test_a_model_error_fails_the_turn_and_is_raised(self):
        sink = events.MemorySink()
        engine = engine_for(FakeModel([ConnectionError("gone")]), sink=sink)

        with self.assertRaises(ConnectionError):
            run_turn(engine, request())

        self.assertEqual(sink.events[-1].payload["state"], "failed")
        self.assertIn("gone", sink.events[-2].payload["error"])

    def test_a_failure_to_record_the_failure_is_logged(self):
        class BrokenSink(events.MemorySink):
            def record(self, event):
                if event.kind == events.FAILED:
                    raise RuntimeError("database gone")
                super().record(event)

        engine = engine_for(FakeModel([ValueError("")]), sink=BrokenSink())
        with (
            self.assertLogs("gm.turn_engine.engine", "ERROR"),
            self.assertRaises(ValueError),
        ):
            run_turn(engine, request())


class FakeModelTest(SimpleTestCase):
    def test_a_call_past_the_script_fails_loudly(self):
        with self.assertRaises(AssertionError):
            FakeModel([]).stream([])


class OllamaModelTest(SimpleTestCase):
    @patch("gm.turn_engine.model.ollama_client.stream_chat", return_value=iter(["hi"]))
    def test_streams_with_thinking_off(self, stream):
        model = OllamaModel(name="qwen", options={"num_ctx": 4})
        self.assertEqual(list(model.stream([SYSTEM], stop_at=12.5)), ["hi"])
        stream.assert_called_once_with(
            "qwen",
            [SYSTEM],
            options={"num_ctx": 4},
            think=False,
            stop_at=12.5,
            hang_up=ANY,
        )


class KeepAliveTokensTest(SimpleTestCase):
    def test_tokens_pass_through_in_order(self):
        self.assertEqual(
            list(KeepAliveTokens(iter(["a", "b"]), interval=1.0)), ["a", "b"]
        )

    def test_a_stream_that_fails_to_close_still_ends_and_says_so(self):
        class Unclosable:
            def __iter__(self):
                return iter(["a"])

            def close(self):
                raise RuntimeError("cannot close")

        seen = []
        with (
            self.assertLogs("gm.turn_engine.model", level="ERROR") as logged,
            self.assertRaisesMessage(RuntimeError, "cannot close"),
        ):
            for item in KeepAliveTokens(Unclosable(), interval=1.0):
                seen.append(item)
        self.assertEqual(seen, ["a"])
        # Logged too: after close() nobody may be reading the queue.
        self.assertIn("failed to close", logged.output[0])

    @patch("gm.turn_engine.model.ollama_client.stream_chat", return_value=["a", "b"])
    def test_a_stand_in_with_nothing_to_close_ends(self, _stream):
        # A stand-in for stream_chat may give a plain list; closing it must
        # not stop the reader handing over the end (issue #904).
        tokens = KeepAliveTokens(OllamaModel(name="m").stream([]), interval=1.0)
        self.assertEqual(list(tokens), ["a", "b"])
        tokens.close()


class TurnDeadlineReachesTheModelTest(SimpleTestCase):
    def test_every_call_is_bounded_by_the_turn_deadline(self):
        model = FakeModel([TWINS_PLAN, "Done."])
        engine = engine_for(model)

        run_turn(engine, request())

        self.assertEqual(model.stops, [engine.deadline, engine.deadline])

    def test_a_call_cut_off_at_the_deadline_is_the_budget_running_out(self):
        cut = ollama_client.StreamDeadlinePassed("cut off at the deadline")
        model = FakeModel([cut])
        engine = engine_for(model)

        with self.assertRaises(TurnBudgetExceeded) as raised:
            run_turn(engine, request())

        self.assertIs(raised.exception.__cause__, cut)
        self.assertEqual(engine.state, TurnState.FAILED)


def _stalled_reader(tokens: KeepAliveTokens) -> list[str]:
    """Read until the stream stalls after its first token, as a turn does.

    The consumer keeps asking, so the reader thread is left blocked in a read
    on the stalled stream when this returns.
    """
    seen: list[str] = []
    gaps = 0
    for item in tokens:
        if item is not None:
            seen.append(item)
        elif seen:
            gaps += 1
            if gaps == 3:
                break
    return seen


class StalledStreamReleasesTheModelTest(SimpleTestCase):
    """A turn that gives up on a stalled stream hangs it up (issue #904).

    These run a real local stand-in for Ollama, because only a real socket
    shows the hang-up. The stand-in holds its response until it is hung up
    on, and the read outlasts that hold, so before #904 the server saw no
    hang-up at all; nothing here waits on the clock (issue #955).
    """

    def test_closing_the_tokens_hangs_up_a_stalled_stream(self):
        with (
            patch.object(ollama_client, "open_stream", REAL_OPEN_STREAM),
            patch.object(ollama_client, "CHAT_TIMEOUT", STAND_IN_TIMEOUT),
            StallingOllama(["A "]) as server,
            override_settings(OLLAMA_BASE_URL=server.base_url),
        ):
            tokens = KeepAliveTokens(OllamaModel(name="m").stream([]), interval=0.05)
            self.assertEqual(_stalled_reader(tokens), ["A "])
            tokens.close()
            self.assertTrue(server.closed.wait(5))

        # Before #904: close() gave up on its join with the reader still
        # blocked in the read, and the server saw no hang-up.
        tokens._reader.join(ACT_ONCE_SET_SECONDS)
        self.assertFalse(tokens._reader.is_alive())
        self.assertEqual(server.hang_ups, ["stall"])

    def test_closing_the_tokens_during_the_prefill_hangs_up_at_once(self):
        # Issue #935: Ollama sends its headers with the first token, so a
        # hang-up that waited for them held the model through the prefill.
        with (
            patch.object(ollama_client, "open_stream", REAL_OPEN_STREAM),
            patch.object(ollama_client, "CHAT_TIMEOUT", STAND_IN_TIMEOUT),
            StallingOllama(["A "], prefill=True) as server,
            override_settings(OLLAMA_BASE_URL=server.base_url),
        ):
            tokens = KeepAliveTokens(OllamaModel(name="m").stream([]), interval=0.05)
            waiting = iter(tokens)
            # Keep-alives only: the reader is blocked in the prefill.
            self.assertIsNone(next(waiting))
            self.assertTrue(server.requested.wait(5))
            self.assertIsNone(next(waiting))
            tokens.close()
            self.assertTrue(server.closed.wait(5))

        # Before #935: the hang-up waited for the headers, which the
        # stand-in holds back until it is hung up on.
        tokens._reader.join(ACT_ONCE_SET_SECONDS)
        self.assertFalse(tokens._reader.is_alive())
        self.assertEqual(server.hang_ups, ["prefill"])
        self.assertFalse(server.responding.is_set())

    def test_a_turn_out_of_time_hangs_up_at_its_deadline(self):
        with (
            patch.object(ollama_client, "open_stream", REAL_OPEN_STREAM),
            patch.object(ollama_client, "CHAT_TIMEOUT", STAND_IN_TIMEOUT),
            StallingOllama(["A "]) as server,
            override_settings(OLLAMA_BASE_URL=server.base_url),
            HandFiredDeadlines() as deadlines,
        ):
            engine = engine_for(OllamaModel(name="m"), keepalive_seconds=0.05)
            deadlines.pass_deadline_once(server.responding)
            with self.assertRaises(TurnBudgetExceeded):
                run_turn(engine, request())
            self.assertTrue(server.closed.wait(5))

        self.assertEqual(engine.state, TurnState.FAILED)
        # The call's deadline is the turn's.
        (timer,) = deadlines.timers
        self.assertAlmostEqual(timer.due, engine.deadline, delta=1.0)
        self.assertEqual(server.hang_ups, ["stall"])


class StateBlockTest(SimpleTestCase):
    def test_an_empty_roster_says_so(self):
        block = state_block(PLAYER, [])
        self.assertEqual(
            block,
            f"This message is from a player (not the Game Master).\nSession characters:\n{NO_CHARACTERS}",
        )

    def test_a_label_matching_the_name_is_not_repeated(self):
        line = RosterLine(character_id=3, name="Twain", label="twain", figures="x")
        self.assertEqual(line.line, "- id 3: Twain — x")


class RosterStateTest(BaseTestCase):
    def test_lines_come_from_the_records(self):
        user = self.create_dm()
        session = GMSession.objects.create(user=user, name="s")
        add_character(
            session,
            {
                "name": "Twain",
                **dict.fromkeys(
                    (
                        "strength",
                        "dexterity",
                        "intelligence",
                        "wisdom",
                        "constitution",
                        "charisma",
                    ),
                    18,
                ),
            },
            label="the brother",
        )
        add_character(
            session, {"name": "Thread", "is_alive": False, "death_cause": "a fall"}
        )

        twain, thread = roster_lines(session)

        self.assertTrue(
            twain.line.startswith(
                f"- id {twain.character_id}: Twain (the brother) — STR 18, DEX 18"
            )
        )
        self.assertIn("Fatigue roll not made yet", twain.figures)
        self.assertIn("needs Fatigue roll", twain.figures)
        self.assertIn("STR not recorded", thread.figures)
        self.assertIn("dead (a fall)", thread.figures)

    def test_pools_show_once_complete(self):
        user = self.create_user()
        session = GMSession.objects.create(user=user, name="s")
        character = add_character(
            session,
            {
                "name": "A",
                "fatigue_roll": 7,
                **dict.fromkeys(
                    (
                        "strength",
                        "dexterity",
                        "intelligence",
                        "wisdom",
                        "constitution",
                        "charisma",
                    ),
                    10,
                ),
            },
        )
        # Current against maximum, with the injury table's row (issue #751).
        self.assertIn(
            f"Fatigue {character.fatigue}/{character.fatigue}, "
            f"Body {character.body}/{character.body} (unhurt)",
            key_figures(character),
        )
        character.constitution = None
        self.assertIn("Fatigue roll 7", key_figures(character))

    def test_no_session_has_no_lines(self):
        self.assertEqual(roster_lines(None), [])

    def test_a_hurt_character_is_listed_as_hurt(self):
        # Issue #751: after a lost fight the line said "Fatigue 44, Body 30"
        # beside a battle summary saying unconscious.
        session = GMSession.objects.create(user=self.create_user(), name="s")
        character = add_character(
            session,
            {
                "name": "Down",
                "fatigue_roll": 7,
                **dict.fromkeys(
                    (
                        "strength",
                        "dexterity",
                        "intelligence",
                        "wisdom",
                        "constitution",
                        "charisma",
                    ),
                    10,
                ),
            },
        )
        fatigue, body = character.pools()
        character.fatigue_damage = fatigue + 17
        character.body_damage = 18
        character.save()
        figures = key_figures(character)
        self.assertIn(
            f"Fatigue -17/{fatigue}, Body {body - 18}/{body} (unconscious)",
            figures,
        )
        ledger = {figure.topic: figure for figure in roster_lines(session)[0].ledger}
        self.assertEqual(ledger[FATIGUE_TOPIC].value, -17)
        self.assertEqual(ledger[FATIGUE_TOPIC].phrase, f"Fatigue -17 of {fatigue}")
        self.assertEqual(ledger[BODY_TOPIC].value, body - 18)
        # A caster's line carries the Mana left, which also stays spent.
        character.free_spell_points = 4
        character.mana_spent = 3
        self.assertIn("(unconscious), Mana 1/4;", key_figures(character))


class TheModelReadsTheSheetTest(BaseTestCase):
    """The state block carries what the Game Master's panel shows (#771, #843).

    The per-turn line listed base attributes, pools, age, years, XP and free
    points only. A Game Master asking the aide "what does her Stealth add?"
    or "can she afford it?" got a model with neither figure, while the panel
    beside it -- whose comment said the model read the same thing -- had both.
    """

    def character(self, **values):
        session = GMSession.objects.create(user=self.create_user(), name="s")
        return add_character(
            session,
            {
                "name": "Wren",
                "fatigue_roll": 7,
                **dict.fromkeys(
                    (
                        "strength",
                        "dexterity",
                        "intelligence",
                        "wisdom",
                        "constitution",
                        "charisma",
                    ),
                    12,
                ),
                **values,
            },
        )

    def test_skills_are_listed_with_their_levels(self):
        wren = self.character(skills={"Stealth": 5, "Sword": 1, "Unused": 0})

        figures = key_figures(wren)

        self.assertIn("skills: Stealth II (+2), Sword I", figures)
        self.assertNotIn("Unused", figures)

    def test_what_she_carries_and_what_she_holds_in_coin(self):
        wren = self.character(
            equipment=[
                {"name": "Lantern", "quantity": 1},
                {"name": "Arrow", "quantity": 20},
            ],
            starting_coin=100,
            money_earned=25,
        )

        figures = key_figures(wren)

        self.assertIn("carries: Lantern, Arrow ×20", figures)
        self.assertIn(f"purse: {format_purse(wren)}", figures)

    def test_an_aged_score_is_given_beside_the_base_one(self):
        wren = self.character()
        # In memory: saving reconciles the aging columns from the age (#680).
        wren.aging_str_penalty = 2

        figures = key_figures(wren)

        self.assertIn("STR 12 (aged 10)", figures)
        self.assertIn("DEX 12,", figures, "an unaged score stands alone")

    def test_spells_are_listed(self):
        wren = self.character()
        wren.spells = {learnable_spells()[0].key: 1}
        wren.save()

        figures = key_figures(wren)

        spell = known_spells(wren)[0]
        self.assertIn(f"spells: {spell.name} ({spell.mastery_display})", figures)

    def test_long_lists_are_capped_and_say_so(self):
        """The line rides where the context guard cannot reach (#647)."""
        many = {f"Skill{index:02d}": 1 for index in range(MAX_SHEET_ENTRIES_LISTED + 3)}
        wren = self.character(skills=many)

        figures = key_figures(wren)

        self.assertIn("and 3 more", figures)
        self.assertNotIn(f"Skill{MAX_SHEET_ENTRIES_LISTED + 2:02d}", figures)

    def test_the_cap_keeps_the_skills_a_fight_and_a_check_read(self):
        """Review of #771: a cap by name alone cut the Weapon skills first."""
        many = {
            f"aaa custom {index:02d}": 1 for index in range(MAX_SHEET_ENTRIES_LISTED)
        }
        many.update({"weapon (broadsword)": 3, "weapon parry": 1, "stealth": 3})
        wren = self.character(skills=many)

        skills = key_figures(wren).split("skills: ")[1].split(";")[0]

        self.assertTrue(
            skills.startswith("weapon (broadsword) II, weapon parry I"), skills
        )
        self.assertIn("stealth II", skills)
        self.assertIn("and 3 more", skills)
        self.assertNotIn("aaa custom 11", skills)

    def test_a_character_with_nothing_lists_no_empty_parts(self):
        figures = key_figures(self.character())

        self.assertNotIn("skills:", figures)
        self.assertNotIn("carries:", figures)
        self.assertNotIn("spells:", figures)
        self.assertIn("purse:", figures, "the purse is a figure even when empty")


class DatabaseSinkTest(BaseTestCase):
    def test_persistent_events_are_numbered_and_append_only(self):
        user = self.create_user()
        turn = GMTurnRecord.objects.create(
            user=user, outcome=GMTurnRecord.OUTCOME_RUNNING, mode="play"
        )
        sink = events.DatabaseSink(turn.pk)

        sink.record(events.TurnEvent(events.STATE, {"state": "grounding"}))
        sink.record(events.TurnEvent(events.TOKEN, {"token": "x"}, persist=False))
        sink.record(events.TurnEvent(events.NARRATION, {"text": "hi"}, visible=True))

        stored = list(GMEvent.objects.filter(turn=turn))
        self.assertEqual(
            [(event.seq, event.kind, event.visible) for event in stored],
            [(1, "state", False), (2, "narration", True)],
        )
        self.assertIn("narration", str(stored[1]))
        with self.assertRaises(ValueError):
            stored[0].save()


class PlanPromptTest(SimpleTestCase):
    def test_prompts_hold_no_markers_and_end_with_the_schema(self):
        for (
            is_game_master,
            mode,
        ), prompt in prompts.system_prompts_by_authority_and_mode().items():
            with self.subTest(is_game_master=is_game_master, mode=mode):
                self.assertNotIn("[[", prompt)
                self.assertNotIn("procedure", prompt.lower())
                self.assertNotIn("server", prompt.lower())
                self.assertTrue(prompt.startswith(prompts.system_prompt()))
                self.assertIn(prompts.section_plan_schema(), prompt)
                self.assertIs(prompts.system_prompt_for(is_game_master, mode), prompt)
                self.assertEqual(
                    prompts.section_dm_authority() in prompt, is_game_master
                )
                self.assertEqual(
                    prompts.SECTION_DM_AIDE in prompt, mode == prompts.MODE_AIDE
                )


WREN = RosterLine(
    character_id=41,
    name="Wren",
    label="",
    figures="age 19; 8 free skill points",
    ledger=(LedgerFigure(SKILL_POINTS_TOPIC, 8, "8 free skill points"),),
)


class RecordFigureInATurnTest(SimpleTestCase):
    """A turn asked for a ledger figure ends carrying one (issue #622).

    The narration filter decides the wording; what is checked here is that
    the turn applies it at all, in both manners, and that a turn which was
    not about a figure is left exactly as the model wrote it.
    """

    ASKED = "How many skill points does Wren have available to spend?"

    def narration_for(self, reply: str, message: str, policy=PLAYER) -> str:
        """The narration a turn commits, with the guarantee applied."""
        _yielded, outcome = run_turn(
            engine_for(FakeModel([[reply]]), ScriptedExecutor(lines=[WREN])),
            request(policy=policy, latest=message, plan=False),
        )
        return outcome.narration

    def test_a_reply_with_no_figure_gains_the_records(self):
        said = self.narration_for("She weighs what to learn next.", self.ASKED)

        self.assertEqual(
            said,
            "She weighs what to learn next.\n\nWren's record: 8 free skill points.",
        )

    def test_a_reply_that_states_the_figure_is_committed_as_written(self):
        written = "Wren has 8 skill points left to spend."

        self.assertEqual(self.narration_for(written, self.ASKED), written)

    def test_a_turn_about_no_figure_is_committed_as_written(self):
        written = "The door creaks open onto the rain."

        self.assertEqual(self.narration_for(written, "I open the door."), written)

    def test_the_aide_gets_the_guarantee_too(self):
        said = self.narration_for(
            "She weighs what to learn next.", self.ASKED, policy=GAME_MASTER
        )

        self.assertIn("Wren's record: 8 free skill points.", said)

    def test_a_character_with_no_ledger_figures_is_left_alone(self):
        """Twain's line carries no figures one by one; nothing is invented."""
        _yielded, outcome = run_turn(
            engine_for(FakeModel([["He shrugs."]]), ScriptedExecutor()),
            request(policy=PLAYER, latest=self.ASKED, plan=False),
        )

        self.assertEqual(outcome.narration, "He shrugs.")
