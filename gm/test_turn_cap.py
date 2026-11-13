"""Only so many Games Master turns at once, per account and per site (issue #174).

A turn holds a request thread for as long as it runs, up to its budget, and
goes on after its page leaves (issue #515); a message sent with no session
started a fresh turn every time. One account could therefore hold every thread
gunicorn has. ``gm.turn_record.claim_turn`` now counts the account's running
records and the site's, and refuses the turn past either cap in the words of
``config/gm-refusals.toml`` ``[views]``; the page shows them as a notice that
stays, with the message back in the box.

The turns here are claimed through the recorder, as the stream and the fight
claim theirs, or recorded as running outright; no model is called.
"""

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import override_settings
from django.utils import timezone

from common.test_helpers import BaseTestCase

from .models import GMSession, GMTurnRecord
from .refusal_words import refusal_words
from .testing import gm_replies
from .turn_record import (
    REFUSED_ACCOUNT_CAP,
    REFUSED_SESSION_BUSY,
    REFUSED_SITE_CAP,
    RUNNING_TURN_GRACE_SECONDS,
    TURN_CLAIM_LOCK_KEY,
    TURN_CLAIM_LOCK_SQL,
    TurnRecorder,
    claim_turn,
    running_turn_cap,
    running_turns_of,
    running_turns_site_wide,
)

STREAM_URL = "/tarmar/gm/stream/"
FIGHT_URL = "/tarmar/gm/fight/"

PLAYER_CAP = 2
GAME_MASTER_CAP = 3
SITE_CAP = 4

capped = override_settings(
    GM_MAX_RUNNING_TURNS_PER_PLAYER=PLAYER_CAP,
    GM_MAX_RUNNING_TURNS_PER_GAME_MASTER=GAME_MASTER_CAP,
    GM_MAX_RUNNING_TURNS_SITE_WIDE=SITE_CAP,
)


def recorder_for(user, session_id: int | None = None) -> TurnRecorder:
    """A turn ``user`` is about to send, with or without a session."""
    return TurnRecorder(
        user=user,
        session_id=session_id,
        command="",
        mode="play",
        is_game_master=bool(user.can_manage_users),
        user_message="I look around",
        history_start=0,
        system_prompt="the rules",
        model="fake",
        options={},
    )


def record_running_turn(user, *, started_at=None) -> GMTurnRecord:
    """A turn of ``user`` recorded as running, as a live stream leaves one."""
    return GMTurnRecord.objects.create(
        user=user,
        outcome=GMTurnRecord.OUTCOME_RUNNING,
        mode="play",
        started_at=started_at or timezone.now(),
    )


@capped
class RunningTurnCapTest(BaseTestCase):
    """What ``claim_turn`` refuses, and what it still admits."""

    def setUp(self):
        super().setUp()
        self.player = self.create_player()
        self.game_master = self.create_dm()

    def test_the_cap_is_the_role_s(self):
        self.assertEqual(running_turn_cap(is_game_master=False), PLAYER_CAP)
        self.assertEqual(running_turn_cap(is_game_master=True), GAME_MASTER_CAP)

    def test_a_player_may_run_turns_up_to_the_cap_and_no_further(self):
        """Sessionless turns included: each used to start without bound."""
        for _turn in range(PLAYER_CAP):
            self.assertIsNone(claim_turn(recorder_for(self.player)))

        refusal = claim_turn(recorder_for(self.player))

        self.assertIsNotNone(refusal)
        assert refusal is not None
        self.assertEqual(refusal.reason, REFUSED_ACCOUNT_CAP)
        self.assertIsNone(refusal.running)
        self.assertEqual(running_turns_of(self.player), PLAYER_CAP)

    def test_a_game_master_has_the_larger_cap(self):
        for _turn in range(GAME_MASTER_CAP):
            self.assertIsNone(claim_turn(recorder_for(self.game_master)))

        refusal = claim_turn(recorder_for(self.game_master))

        assert refusal is not None
        self.assertEqual(refusal.reason, REFUSED_ACCOUNT_CAP)

    def test_a_session_turn_counts_against_the_account_too(self):
        """A turn in a session and a sessionless one share the one budget."""
        session = GMSession.objects.create(user=self.player, name="Keep", mode="play")
        self.assertIsNone(claim_turn(recorder_for(self.player, session.pk)))
        self.assertIsNone(claim_turn(recorder_for(self.player)))

        other_session = GMSession.objects.create(
            user=self.player, name="Other", mode="play"
        )
        refusal = claim_turn(recorder_for(self.player, other_session.pk))

        assert refusal is not None
        self.assertEqual(refusal.reason, REFUSED_ACCOUNT_CAP)

    def test_the_session_s_own_running_turn_is_still_the_first_refusal(self):
        """Issue #515's answer comes first, with the turn to wait for."""
        session = GMSession.objects.create(user=self.player, name="Keep", mode="play")
        first = recorder_for(self.player, session.pk)
        self.assertIsNone(claim_turn(first))

        refusal = claim_turn(recorder_for(self.player, session.pk))

        assert refusal is not None and refusal.running is not None
        self.assertEqual(refusal.reason, REFUSED_SESSION_BUSY)
        self.assertEqual(refusal.running.pk, first.record_id)

    def test_a_finished_turn_frees_its_slot(self):
        recorders = [recorder_for(self.player) for _turn in range(PLAYER_CAP)]
        for recorder in recorders:
            self.assertIsNone(claim_turn(recorder))
        assert claim_turn(recorder_for(self.player)) is not None

        recorders[0].finish(GMTurnRecord.OUTCOME_OK)

        self.assertIsNone(claim_turn(recorder_for(self.player)))

    def test_another_account_s_turns_do_not_count_against_this_one(self):
        for _turn in range(PLAYER_CAP):
            record_running_turn(self.game_master)

        self.assertIsNone(claim_turn(recorder_for(self.player)))

    def test_a_turn_left_running_by_a_dead_worker_frees_its_slot_in_time(self):
        """The slot is held no longer than a turn can run: past the budgets
        and the grace the row no longer counts, and is marked as failed."""
        too_long_ago = timezone.now() - timedelta(
            seconds=600 + RUNNING_TURN_GRACE_SECONDS + 1
        )
        with override_settings(
            GM_STREAM_BUDGET_SECONDS=300.0, GM_RECAP_BUDGET_SECONDS=300.0
        ):
            stale = [
                record_running_turn(self.player, started_at=too_long_ago)
                for _turn in range(PLAYER_CAP)
            ]
            self.assertEqual(running_turns_of(self.player), 0)

            self.assertIsNone(claim_turn(recorder_for(self.player)))

        for record in stale:
            record.refresh_from_db()
            self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_ERROR)

    def test_the_site_cap_counts_every_account(self):
        """Registration is open, so the per-account cap alone is N accounts
        times the cap; the site's keeps request threads free for pages."""
        others = [
            self.create_user(username=f"table{number}") for number in range(SITE_CAP)
        ]
        for other in others:
            record_running_turn(other)
        self.assertEqual(running_turns_site_wide(), SITE_CAP)

        refusal = claim_turn(recorder_for(self.player))

        assert refusal is not None
        self.assertEqual(refusal.reason, REFUSED_SITE_CAP)
        self.assertIsNone(refusal.running)

    def test_the_site_cap_holds_a_game_master_too(self):
        for number in range(SITE_CAP):
            record_running_turn(self.create_user(username=f"table{number}"))

        refusal = claim_turn(recorder_for(self.game_master))

        assert refusal is not None
        self.assertEqual(refusal.reason, REFUSED_SITE_CAP)


@capped
class TurnClaimLockTest(BaseTestCase):
    """The site-wide count is serialized across processes (issue #174).

    The suite runs on SQLite, which has no advisory locks, so the PostgreSQL
    step is pinned against a stand-in connection: the lock statement, its
    key, and its place between the account's count and the site's.
    """

    def setUp(self):
        super().setUp()
        self.player = self.create_player()

    def _connection(self, vendor: str) -> MagicMock:
        """A ``django.db.connection`` whose cursor records what it ran."""
        fake = MagicMock()
        fake.vendor = vendor
        return fake

    def test_on_postgresql_the_lock_is_taken_between_the_counts(self):
        steps: list[object] = []
        connection = self._connection("postgresql")
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.execute.side_effect = lambda sql, params: steps.append((sql, params))

        def counted(name: str, result: int = 0):
            def count(*_args) -> int:
                steps.append(name)
                return result

            return count

        with (
            patch("gm.turn_record.connection", connection),
            patch("gm.turn_record.running_turns_of", side_effect=counted("account")),
            patch(
                "gm.turn_record.running_turns_site_wide", side_effect=counted("site")
            ),
        ):
            self.assertIsNone(claim_turn(recorder_for(self.player)))

        self.assertEqual(
            steps,
            ["account", (TURN_CLAIM_LOCK_SQL, [TURN_CLAIM_LOCK_KEY]), "site"],
        )
        self.assertIn("pg_advisory_xact_lock(%s::bigint)", TURN_CLAIM_LOCK_SQL)

    def test_on_postgresql_an_account_at_its_cap_never_takes_the_lock(self):
        """Refused under the user's row alone: the site's lock is not held
        for a claim that was never going to start."""
        for _turn in range(PLAYER_CAP):
            record_running_turn(self.player)
        connection = self._connection("postgresql")

        with patch("gm.turn_record.connection", connection):
            refusal = claim_turn(recorder_for(self.player))

        assert refusal is not None
        self.assertEqual(refusal.reason, REFUSED_ACCOUNT_CAP)
        connection.cursor.assert_not_called()

    def test_on_sqlite_no_lock_is_taken(self):
        connection = self._connection("sqlite")

        with patch("gm.turn_record.connection", connection):
            self.assertIsNone(claim_turn(recorder_for(self.player)))

        connection.cursor.assert_not_called()


@capped
class RunningTurnCapAnsweredTest(BaseTestCase):
    """How the stream and the fight answer a capped account (issue #174)."""

    def setUp(self):
        super().setUp()
        self.player = self.create_player()
        self.login_as(self.player)
        for _turn in range(PLAYER_CAP):
            record_running_turn(self.player)

    def _send(self, body: dict):
        return self.client.post(
            STREAM_URL, json.dumps(body), content_type="application/json"
        )

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_message_past_the_cap_is_refused_in_the_configured_words(self, stream):
        stream.side_effect = gm_replies("Never.", "Never.")

        response = self._send({"message": "Begin a new game"})

        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            response.json(), {"error": refusal_words().views.too_many_turns_running}
        )
        # The model was never asked, and no turn started.
        stream.assert_not_called()
        self.assertEqual(
            GMTurnRecord.objects.filter(outcome=GMTurnRecord.OUTCOME_RUNNING).count(),
            PLAYER_CAP,
        )

    def test_the_refusal_is_recorded(self):
        """A rejected record, as a busy session's refusal leaves (issue #515),
        so the turn's debugging trail says why nothing was answered."""
        self._send({"message": "Begin a new game"})

        refused = GMTurnRecord.objects.get(user_message="Begin a new game")
        self.assertEqual(refused.outcome, GMTurnRecord.OUTCOME_REJECTED)
        self.assertEqual(refused.error, refusal_words().views.too_many_turns_running)

    def test_a_message_to_a_session_past_the_cap_is_refused_too(self):
        session = GMSession.objects.create(user=self.player, name="Keep", mode="play")

        response = self._send({"session_id": session.pk, "message": "Look"})

        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            response.json()["error"], refusal_words().views.too_many_turns_running
        )

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_fight_past_the_cap_is_refused_in_the_same_words(self, stream):
        session = GMSession.objects.create(user=self.player, name="Keep", mode="play")

        response = self.client.post(
            FIGHT_URL,
            json.dumps({"session_id": session.pk, "foes": "two wolves"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            response.json(), {"error": refusal_words().views.too_many_turns_running}
        )
        stream.assert_not_called()

    def test_the_site_s_fullness_has_its_own_words(self):
        GMTurnRecord.objects.filter(user=self.player).delete()
        for number in range(SITE_CAP):
            record_running_turn(self.create_user(username=f"table{number}"))

        response = self._send({"message": "Begin a new game"})

        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            response.json(), {"error": refusal_words().views.site_turns_full}
        )

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_under_the_cap_a_message_is_answered(self, stream):
        """The cap refuses the turn past it and nothing before it."""
        oldest = GMTurnRecord.objects.filter(user=self.player).order_by("pk").first()
        assert oldest is not None
        oldest.delete()
        stream.side_effect = gm_replies("You look.", "You look around.")

        response = self._send({"message": "Begin a new game"})

        self.assertEqual(response.status_code, 200)
        list(response.streaming_content)
        self.assertEqual(stream.call_count, 2)
