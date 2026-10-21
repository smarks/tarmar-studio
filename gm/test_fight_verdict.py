"""How a session fight ends, told to the player (issue #785).

At the end of a session fight the Battle pane said "Finished" and the
roster read "Unconscious"; who won, and what became of the party, was only
in a hidden entry the model read on the player's next message, and the chat
said nothing. The verdict is now filed into the chat as a notice the moment
the board ends the fight -- or on the next load of the page, when nobody
was watching -- and the pane shows the same line, both read from the
battle as it stands.

The fights here are a Game Master's, whose typed foes are built as typed.
"""

import datetime
import json
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from battle.models import Battle
from battle.services import advance_turn, restart_battle
from common.test_helpers import BaseTestCase, create_character, says

from . import summary
from .battle_report import ending_of, fight_verdict, file_fight_verdict
from .fight import FOE_TEAM, PARTY_TEAM, start_fight
from .models import GMSession
from .refusal_words import refusal_words
from .retrieval import FIGHT_VERDICT_KIND, model_messages
from .test_fight import play_turn
from .testing import gm_replies, with_fixed_gm_refusals
from .turn_engine.history import model_history

CHAT_URL = "/tarmar/gm/"
VERDICT_URL = "/tarmar/gm/fight/verdict/"
STREAM_URL = "/tarmar/gm/stream/"

#: More turns than a lone gray wolf and a fighter ever need.
MAX_TURNS = 80


class FightVerdictTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm(username="verdict-gm")
        self.login_as(self.game_master)
        self.aldwin = create_character(self.game_master, name="Aldwin")
        self.session = GMSession.objects.create(
            name="The Wolf Road", user=self.game_master
        )
        self.session.characters.add(self.aldwin)
        self.battle = start_fight(self.session, foes_text="gray wolf", seed=11).battle
        self.session.refresh_from_db()

    def fight_to_the_end(self) -> Battle:
        battle = self.battle
        for _turn in range(MAX_TURNS):
            if battle.status in (Battle.STATUS_FINISHED, Battle.STATUS_STALEMATE):
                return battle
            battle = play_turn(battle, self.game_master)
        self.fail(f"the fight did not end in {MAX_TURNS} turns")

    def verdicts(self) -> list[dict]:
        self.session.refresh_from_db()
        return [m for m in self.session.history if m.get("kind") == FIGHT_VERDICT_KIND]

    def ask(self) -> dict:
        """What the Battle tab reads back after the board ends the fight."""
        return self.client.get(VERDICT_URL, {"session_id": self.session.pk}).json()

    def test_nothing_is_filed_while_the_fight_goes_on(self):
        self.assertIsNone(fight_verdict(self.battle))
        self.assertIsNone(file_fight_verdict(self.session))
        self.assertEqual(
            self.ask(), {"ok": True, "verdict": None, "battle_id": None, "notice": None}
        )
        self.assertEqual(self.verdicts(), [])

    def test_a_finished_fight_names_the_winner_and_the_party(self):
        battle = self.fight_to_the_end()

        text = fight_verdict(battle)

        assert text is not None
        words = refusal_words().battle_report
        self.assertTrue(text.startswith(f"{words.fight_over}: "))
        self.assertIn("Aldwin: ", text)
        # The verdict is the session's sentence for the winning side, not the
        # battle's own line for the team.
        verdicts = (words.foes_victorious, words.party_victorious)
        self.assertTrue(any(says(text, verdict) for verdict in verdicts), text)
        # Only the party's figures are listed after the verdict.
        self.assertNotIn("Gray Wolf: ", text)

    def test_the_turn_that_ends_the_fight_files_the_notice_once(self):
        """Filed by the advance path, not by a page (review of #785)."""
        battle = self.fight_to_the_end()

        filed = self.verdicts()
        self.assertEqual(len(filed), 1)
        self.assertEqual(filed[0]["role"], "assistant")
        self.assertEqual(filed[0]["battle_id"], battle.pk)
        self.assertEqual(filed[0]["content"], fight_verdict(battle))
        # The end of the history as it stood when the fight ended.
        self.assertEqual(self.session.history[-1], filed[0])
        # Advancing a finished battle again files nothing more.
        advance_turn(battle.pk)
        self.assertEqual(len(self.verdicts()), 1)

    def test_the_tab_reads_it_back_and_writes_nothing(self):
        self.fight_to_the_end()
        self.session.refresh_from_db()
        before = (self.session.history_json, self.session.updated_at)

        answer = self.ask()

        self.assertTrue(
            answer["verdict"].startswith(
                f"{refusal_words().battle_report.fight_over}: "
            )
        )
        self.assertEqual(answer["battle_id"], self.battle.pk)
        self.session.refresh_from_db()
        self.assertEqual((self.session.history_json, self.session.updated_at), before)
        refused = self.client.post(VERDICT_URL, {"session_id": self.session.pk})
        self.assertEqual(refused.status_code, 405)

    def test_the_page_shows_it_in_the_chat_and_the_pane(self):
        self.fight_to_the_end()

        page = self.client.get(f"{CHAT_URL}?session={self.session.pk}")

        verdict = fight_verdict(Battle.objects.get(pk=self.battle.pk))
        shown = [
            m for m in page.context["history"] if m.get("kind") == FIGHT_VERDICT_KIND
        ]
        self.assertEqual([m["content"] for m in shown], [verdict])
        key = f"{self.battle.pk}:{ending_of(Battle.objects.get(pk=self.battle.pk))}"
        self.assertContains(page, f'data-battle-verdict="{key}"')
        self.assertContains(page, 'id="gm-battle-verdict"')
        self.assertEqual(page.context["panel_verdict"], verdict)
        # The app speaking, not the Game Master (#339 item 11).
        html = page.content.decode()
        notice = html[html.index(f'data-battle-verdict="{key}"') :]
        self.assertIn('<div class="message-role">Notice</div>', notice[:400])
        self.assertEqual(len(self.verdicts()), 1)
        self.assertEqual(self.session.message_count, 2)

    def test_an_old_session_loads_with_no_write(self):
        """A fight that ended before notices were filed is not backfilled.

        The review's probe: a session whose fight ended, six more entries,
        untouched for thirty days. Loading it appended the notice after the
        later turns, moved ``updated_at`` (reordering the session list) and
        changed the count.
        """
        battle = self.fight_to_the_end()
        self.session.refresh_from_db()
        # As a session saved before the notice existed holds it.
        without = [
            m for m in self.session.history if m.get("kind") != FIGHT_VERDICT_KIND
        ]
        later = [
            {
                "role": "user" if turn % 2 == 0 else "assistant",
                "content": f"later {turn}",
            }
            for turn in range(6)
        ]
        self.session.history = [*without, *later]
        self.session.save()
        old = timezone.now() - datetime.timedelta(days=30)
        GMSession.objects.filter(pk=self.session.pk).update(updated_at=old)
        before = GMSession.objects.get(pk=self.session.pk)

        page = self.client.get(f"{CHAT_URL}?session={self.session.pk}")
        self.ask()

        after = GMSession.objects.get(pk=self.session.pk)
        self.assertEqual(after.history_json, before.history_json)
        self.assertEqual(after.updated_at, old)
        self.assertEqual(after.message_count, before.message_count)
        self.assertEqual(self.verdicts(), [])
        # The pane still says how it ended, read from the battle.
        self.assertEqual(page.context["panel_verdict"], fight_verdict(battle))

    def test_a_battle_no_session_holds_files_nothing(self):
        other = create_character(self.game_master, name="Loner")
        self.session.characters.remove(self.aldwin)
        self.session.battle = None
        self.session.save()
        self.session.characters.add(other)
        self.fight_to_the_end()
        self.assertEqual(self.verdicts(), [])

    def test_the_model_reads_the_summary_not_the_notice(self):
        self.fight_to_the_end()
        self.session.refresh_from_db()
        self.assertEqual(len(self.verdicts()), 1)

        self.assertEqual(
            [
                m
                for m in model_history(self.session.history)
                if m.get("kind") == FIGHT_VERDICT_KIND
            ],
            [],
        )

        stream_calls = gm_replies("The wolf lies still.")
        with patch(
            "gm.turn_engine.model.ollama_client.stream_chat", side_effect=stream_calls
        ):
            response = self.client.post(
                STREAM_URL,
                json.dumps({"session_id": self.session.pk, "message": "What now?"}),
                content_type="application/json",
            )
            b"".join(response.streaming_content)
        sent = json.dumps(stream_calls.calls[-1])
        self.assertNotIn("The fight is over: ", sent)
        self.assertIn("Battle over", sent)

    def test_the_pane_says_nothing_before_the_end(self):
        page = self.client.get(f"{CHAT_URL}?session={self.session.pk}")
        self.assertIsNone(page.context["panel_verdict"])
        self.assertContains(page, 'id="gm-battle-verdict" role="status" hidden')

    def test_another_players_session_is_not_found(self):
        stranger = self.create_player(username="verdict-stranger")
        self.logout()
        self.login_as(stranger)
        response = self.client.get(VERDICT_URL, {"session_id": self.session.pk})
        self.assertEqual(response.status_code, 404)


@with_fixed_gm_refusals
class VerdictWordingTest(BaseTestCase):
    """The side a session fight calls Foes stands with a plural verb (#785).

    Built from the victory's record, its team and the figures it left
    standing, in the configured words -- never by matching the battle's own
    line, whose words are configuration too (#136). The line here is written
    as nothing the session could match.
    """

    def setUp(self):
        super().setUp()
        game_master = self.create_dm(username="wording-gm")
        self.session = GMSession.objects.create(name="W", user=game_master)
        self.session.characters.add(create_character(game_master, name="Mira"))
        self.battle = start_fight(self.session, foes_text="gray wolf", seed=3).battle

    #: The battle's own line, as no configured sentence would write it.
    LINE = "the line the battle wrote"

    def end_with(self, team: str) -> str:
        self.battle.status = Battle.STATUS_FINISHED
        self.battle.save(update_fields=["status"])
        mira = self.battle.combatants.get(display_name="Mira")
        self.battle.events.create(
            turn=1,
            phase=6,
            sequence=900,
            event_type="info",
            actor=team,
            payload={"winner_team": team, "winners": [mira.pk]},
            message=self.LINE,
        )
        text = fight_verdict(self.battle)
        assert text is not None
        return text

    def test_the_foes(self):
        self.assertTrue(
            self.end_with(FOE_TEAM).startswith(
                "battle_report.fight_over: battle_report.foes_victorious Mira."
            )
        )

    def test_the_party(self):
        self.assertTrue(
            self.end_with(PARTY_TEAM).startswith(
                "battle_report.fight_over: battle_report.party_victorious Mira."
            )
        )

    def test_any_other_team_is_the_battles_line(self):
        self.assertTrue(
            self.end_with("Red").startswith(f"battle_report.fight_over: {self.LINE}.")
        )


class TheNoticeNeverReachesTheModelTest(BaseTestCase):
    """Not through a recap, a continuation recap, or any call's wire (#785 review).

    The notice is the reader's. The fold's recap read it as "You: The fight
    is over: ...", the Game Master's own words, and so did the recap a new
    session continuing this one opens with.
    """

    NOTICE = {
        "role": "assistant",
        "kind": FIGHT_VERDICT_KIND,
        "content": "The fight is over: The foes stand victorious.",
        "battle_id": 1,
    }

    def history(self) -> list[dict]:
        turns: list[dict] = []
        for turn in range(40):
            turns.append({"role": "user", "content": f"We go on {turn}."})
            turns.append({"role": "assistant", "content": f"Room {turn}."})
        turns.insert(10, self.NOTICE)
        return turns

    def test_the_fold_leaves_it_out(self):
        asked: list[str] = []

        def ask_model(prompt: str) -> str:
            asked.append(prompt)
            return "A recap."

        summary.fold(
            self.history(), previous_summary="", already_folded=0, ask_model=ask_model
        )

        self.assertEqual(len(asked), 1)
        self.assertNotIn("The fight is over", asked[0])
        self.assertIn("Room 3.", asked[0])

    def test_the_continuation_recap_leaves_it_out(self):
        asked: list[str] = []

        def ask_model(prompt: str) -> str:
            asked.append(prompt)
            return "A recap."

        summary.recap_of_whole_session(
            self.history(),
            previous_summary="",
            already_folded=0,
            ask_model=ask_model,
            num_ctx=65536,
        )

        self.assertEqual(len(asked), 1)
        self.assertNotIn("The fight is over", asked[0])

    def test_no_call_sends_it(self):
        """``model_messages`` is every call's wire: the turn, /fight's foes, the CLI."""
        sent = model_messages(self.history(), num_ctx=65536)
        self.assertNotIn("The fight is over", json.dumps(sent))
        self.assertEqual(len(sent), 80)


class VerdictAfterRestartTest(BaseTestCase):
    """One notice per ending; a restart played to an end again gets its own (L6)."""

    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm(username="restart-gm")
        self.session = GMSession.objects.create(name="Again", user=self.game_master)
        self.session.characters.add(create_character(self.game_master, name="Aldwin"))
        self.battle = start_fight(self.session, foes_text="gray wolf", seed=11).battle

    def play_out(self) -> Battle:
        battle = Battle.objects.get(pk=self.battle.pk)
        for _turn in range(MAX_TURNS):
            if battle.status in (Battle.STATUS_FINISHED, Battle.STATUS_STALEMATE):
                return battle
            battle = play_turn(battle, self.game_master)
        self.fail(f"the fight did not end in {MAX_TURNS} turns")

    def notices(self) -> list[dict]:
        self.session.refresh_from_db()
        return [m for m in self.session.history if m.get("kind") == FIGHT_VERDICT_KIND]

    def test_a_restart_that_ends_differently_is_told_again(self):
        first_end = self.play_out()
        (first,) = self.notices()
        restart_battle(first_end.pk, is_game_master=True)
        # Another roll of the dice, so the replay does not repeat the first.
        Battle.objects.filter(pk=first_end.pk).update(seed=first_end.seed + 997)

        second_end = self.play_out()

        told = self.notices()
        self.assertEqual(len(told), 2)
        self.assertEqual(told[0], first)
        words = refusal_words().battle_report
        self.assertTrue(first["content"].startswith(f"{words.fight_over}: "))
        self.assertTrue(told[1]["content"].startswith(f"{words.after_restart}: "))
        self.assertNotEqual(told[0]["battle_ending"], told[1]["battle_ending"])
        self.assertEqual(told[1]["battle_ending"], ending_of(second_end))
        # The pane and the page's read-back follow the latest ending.
        self.login_as(self.game_master)
        page = self.client.get(f"{CHAT_URL}?session={self.session.pk}")
        self.assertEqual(page.context["panel_verdict"], told[1]["content"])
        answer = self.client.get(VERDICT_URL, {"session_id": self.session.pk}).json()
        self.assertEqual(answer["verdict"], told[1]["content"])
        self.assertEqual(
            answer["notice"], f"{self.battle.pk}:{told[1]['battle_ending']}"
        )

    def test_a_restart_not_yet_ended_reads_back_nothing(self):
        ended = self.play_out()
        restart_battle(ended.pk, is_game_master=True)
        self.login_as(self.game_master)
        answer = self.client.get(VERDICT_URL, {"session_id": self.session.pk}).json()
        self.assertIsNone(answer["verdict"])
        self.assertEqual(len(self.notices()), 1)


class AFailedNoticeDoesNotFailTheTurnTest(BaseTestCase):
    """The turn has committed; a notice that cannot be filed is logged (L5)."""

    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm(username="raise-gm")
        self.login_as(self.game_master)
        self.session = GMSession.objects.create(name="Raise", user=self.game_master)
        self.session.characters.add(create_character(self.game_master, name="Aldwin"))
        self.battle = start_fight(self.session, foes_text="gray wolf", seed=11).battle

    def test_the_advance_answers_normally_and_the_failure_is_logged(self):
        with (
            patch(
                "gm.battle_report.file_fight_verdict",
                side_effect=RuntimeError("the notice could not be written"),
            ),
            self.assertLogs("gm.battle_report", level="ERROR") as logged,
        ):
            battle = Battle.objects.get(pk=self.battle.pk)
            for _turn in range(MAX_TURNS):
                if battle.status in (Battle.STATUS_FINISHED, Battle.STATUS_STALEMATE):
                    break
                if battle.status == Battle.STATUS_AWAITING:
                    battle = play_turn(battle, self.game_master)
                    continue
                response = self.client.post(reverse("battle:advance", args=[battle.pk]))
                self.assertEqual(response.status_code, 200)
                battle = Battle.objects.get(pk=battle.pk)

        self.assertIn(battle.status, (Battle.STATUS_FINISHED, Battle.STATUS_STALEMATE))
        self.assertIn("the turn stands", logged.output[0])
        self.assertIn("RuntimeError: the notice could not be written", logged.output[0])
        self.session.refresh_from_db()
        self.assertEqual(
            [m for m in self.session.history if m.get("kind") == FIGHT_VERDICT_KIND], []
        )

    def test_the_player_action_path_answers_normally_too(self):
        """``record_player_action`` finishes the turn through ``advance_turn``."""
        with (
            patch("gm.battle_report.file_fight_verdict", side_effect=RuntimeError("x")),
            self.assertLogs("gm.battle_report", level="ERROR"),
        ):
            battle = Battle.objects.get(pk=self.battle.pk)
            for _turn in range(MAX_TURNS):
                if battle.status in (Battle.STATUS_FINISHED, Battle.STATUS_STALEMATE):
                    break
                battle = play_turn(battle, self.game_master)
        self.assertIn(battle.status, (Battle.STATUS_FINISHED, Battle.STATUS_STALEMATE))
