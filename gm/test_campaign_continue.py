"""Continuing a campaign in a new session (issue #761).

Every campaign record belongs to one session, so a Game Master running a
table weekly started each new session with none of the last one: no place,
no Brannoc, no iron box, no open favour, no house rules. The new-session page
now offers to continue the last played session's campaign, and when asked the
server copies its records into the new session with its fold recap as the
opening context. Nothing carries over unless the owner asks.
"""

import json
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from common.test_helpers import BaseTestCase

from .models import (
    PLACEHOLDER_SESSION_NAME,
    CampaignState,
    GMSession,
    SceneEntity,
    StoryThread,
    SupplyItem,
    TableRuling,
)
from .retrieval import RECAP_KIND, is_hidden_message
from .testing import gm_replies
from .turn_engine.state import (
    CARRIED_FOLD_RECAP_PREFIX,
    CARRIED_RECAP_PREFIX,
    CampaignCarry,
    CarriedRecap,
    carried_recap,
    continue_campaign,
    previous_campaign,
    recap_pending,
    write_carried_recap,
)

CONTINUE_URL = "/tarmar/gm/continue/"
STREAM_URL = "/tarmar/gm/stream/"

RECAP = "The party took the iron box from the well and owe Mother Vane a favour."


def played_session(user, name: str = "The well", **fields) -> GMSession:
    """A session somebody has played, holding one of every campaign record."""
    session = GMSession.objects.create(user=user, name=name, mode="aide", **fields)
    session.history = [
        {"role": "user", "content": "We climb down the well."},
        {"role": "assistant", "content": "The water is cold."},
    ]
    session.save()
    CampaignState.objects.create(
        session=session,
        place="Farport",
        place_key="farport",
        day=3,
        time_of_day="evening",
    )
    SupplyItem.objects.create(session=session, name="torches", amount="1", note="damp")
    SceneEntity.objects.create(
        session=session,
        kind=SceneEntity.PERSON,
        name="Brannoc",
        where="the harbour",
        wants="his debt paid",
        condition="wary of the party",
        notes="a fisherman",
    )
    SceneEntity.objects.create(
        session=session, kind=SceneEntity.THING, name="iron box", condition="sealed"
    )
    StoryThread.objects.create(session=session, text="a favour owed to Mother Vane")
    StoryThread.objects.create(
        session=session,
        text="seal the box",
        resolved=True,
        resolution="sealed with lead",
        resolved_at=timezone.now(),
    )
    TableRuling.objects.create(
        session=session, topic="criticals", text="a 20 just crits"
    )
    TableRuling.objects.create(
        session=session, topic="old rule", text="retired", active=False
    )
    return session


def records(session: GMSession) -> dict[str, list[tuple]]:
    """Every campaign record of ``session``, as comparable values."""
    return {
        "place": list(
            CampaignState.objects.filter(session=session).values_list(
                "place", "place_key", "day", "time_of_day", "updated_at"
            )
        ),
        "supplies": list(
            SupplyItem.objects.filter(session=session).values_list(
                "name", "amount", "note", "created_at", "updated_at"
            )
        ),
        "entities": list(
            SceneEntity.objects.filter(session=session).values_list(
                "kind", "name", "where", "wants", "condition", "notes", "created_at"
            )
        ),
        "threads": list(
            StoryThread.objects.filter(session=session).values_list(
                "text", "resolved", "resolution", "resolved_at", "opened_turn"
            )
        ),
        "rulings": list(
            TableRuling.objects.filter(session=session, active=True).values_list(
                "topic", "text", "turn"
            )
        ),
    }


class ContinueCampaignTest(BaseTestCase):
    """The copy itself."""

    def setUp(self):
        super().setUp()
        self.user = self.create_dm(username="gamemaster")

    def test_every_record_is_copied_into_the_new_session(self):
        previous = played_session(self.user)

        session = continue_campaign(previous, None)

        self.assertNotEqual(session.pk, previous.pk)
        self.assertEqual(records(session), records(previous))
        self.assertEqual(len(records(session)["rulings"]), 1)
        self.assertEqual(len(records(session)["threads"]), 2)

    def test_the_copies_are_the_new_sessions_own(self):
        previous = played_session(self.user)
        before = records(previous)
        session = continue_campaign(previous, None)

        SceneEntity.objects.filter(session=session, name="iron box").update(
            condition="open"
        )
        SupplyItem.objects.filter(session=session).delete()

        self.assertEqual(records(previous), before)

    def test_a_retired_ruling_is_left_behind(self):
        session = continue_campaign(played_session(self.user), None)

        self.assertEqual(
            list(
                TableRuling.objects.filter(session=session).values_list(
                    "topic", flat=True
                )
            ),
            ["criticals"],
        )

    def test_the_recap_opens_the_new_conversation_and_is_its_recap_so_far(self):
        previous = played_session(self.user, summary=RECAP, summary_covers=30)

        session = continue_campaign(previous, CarriedRecap(RECAP, whole=True))

        (opening,) = session.history
        self.assertEqual(opening["kind"], RECAP_KIND)
        self.assertTrue(is_hidden_message(opening))
        self.assertEqual(opening["content"], f"{CARRIED_RECAP_PREFIX}\n{RECAP}")
        self.assertEqual(session.summary, RECAP)
        self.assertEqual(session.summary_covers, 0)

    def test_without_a_recap_the_records_carry_alone(self):
        session = continue_campaign(played_session(self.user), None)

        self.assertEqual(session.history, [])
        self.assertEqual(session.summary, "")
        self.assertEqual(len(records(session)["entities"]), 2)

    def test_the_new_session_names_itself_keeps_the_mode_and_says_what_it_continues(
        self,
    ):
        previous = played_session(self.user)

        session = continue_campaign(previous, None)

        self.assertEqual(session.name, PLACEHOLDER_SESSION_NAME)
        self.assertEqual(session.mode, previous.mode)
        self.assertEqual(session.continued_from, previous)
        self.assertEqual(session.user, self.user)


WHOLE_RECAP = "The party took the box, sealed it, and rode north with Brannoc."
NUM_CTX = 65536


class CarriedRecapTest(BaseTestCase):
    """The story a continuation opens with (review of #761).

    The fold recap stops at the last fold, and the page promises the story
    so far, so the whole of the last session is recapped once, at continue
    time, by the fold's own summariser.
    """

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.asked: list[str] = []

    def ask(self, reply: str | BaseException):
        def ask_model(prompt: str) -> str:
            self.asked.append(prompt)
            if isinstance(reply, BaseException):
                raise reply
            return reply

        return ask_model

    def test_the_whole_session_is_recapped_taking_the_fold_recap_in(self):
        previous = played_session(self.user, summary=RECAP, summary_covers=1)

        recap = write_carried_recap(previous, self.ask(WHOLE_RECAP), NUM_CTX)

        self.assertEqual(recap, CarriedRecap(WHOLE_RECAP, whole=True))
        (prompt,) = self.asked
        self.assertIn(RECAP, prompt)
        # The turn after the fold is in it; the one before is the recap's.
        self.assertIn("The water is cold.", prompt)
        self.assertNotIn("We climb down the well.", prompt)

    def test_a_failed_call_carries_the_fold_recap_and_says_so(self):
        previous = played_session(self.user, summary=RECAP, summary_covers=1)

        recap = write_carried_recap(
            previous, self.ask(ConnectionError("down")), NUM_CTX
        )
        session = continue_campaign(previous, recap)

        self.assertEqual(recap, CarriedRecap(RECAP, whole=False))
        self.assertTrue(
            session.history[0]["content"].startswith(CARRIED_FOLD_RECAP_PREFIX)
        )
        self.assertEqual(carried_recap(session), recap)

    def test_a_session_that_never_folded_is_recapped_whole(self):
        recap = write_carried_recap(
            played_session(self.user), self.ask(WHOLE_RECAP), NUM_CTX
        )

        self.assertEqual(recap, CarriedRecap(WHOLE_RECAP, whole=True))
        self.assertIn("We climb down the well.", self.asked[0])

    def test_a_tail_too_long_for_the_window_is_not_sent(self):
        """The fold's own budget bounds the request (second review of #761)."""
        previous = played_session(self.user, summary=RECAP, summary_covers=1)

        recap = write_carried_recap(previous, self.ask(WHOLE_RECAP), num_ctx=600)

        self.assertEqual(self.asked, [])
        self.assertEqual(recap, CarriedRecap(RECAP, whole=False))

    def test_a_session_that_never_folded_and_a_failed_call_carry_no_recap(self):
        recap = write_carried_recap(played_session(self.user), self.ask(""), NUM_CTX)

        self.assertIsNone(recap)


class PreviousCampaignTest(BaseTestCase):
    """Which session the new-session page offers to continue."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()

    def test_the_most_recent_played_session_with_records_is_offered(self):
        older = played_session(self.user, name="Older")
        newer = played_session(self.user, name="Newer")
        GMSession.objects.filter(pk=older.pk).update(
            updated_at=timezone.now() - timedelta(days=7)
        )

        offer = previous_campaign(self.user)

        assert offer is not None
        self.assertEqual(offer.session, newer)

    def test_a_session_with_no_records_is_passed_over(self):
        with_records = played_session(self.user, name="With records")
        empty = GMSession.objects.create(user=self.user, name="Chat only")
        empty.history = [{"role": "user", "content": "hello"}]
        empty.save()

        offer = previous_campaign(self.user)

        assert offer is not None
        self.assertEqual(offer.session, with_records)

    def test_an_unplayed_continuation_is_not_offered(self):
        previous = played_session(self.user, summary=RECAP, summary_covers=30)
        continue_campaign(previous, CarriedRecap(RECAP, whole=True))

        offer = previous_campaign(self.user)

        assert offer is not None
        self.assertEqual(offer.session, previous)

    def test_nothing_is_offered_without_a_campaign(self):
        self.assertIsNone(previous_campaign(self.user))

    def test_another_persons_campaign_is_never_offered(self):
        played_session(self.create_user(username="someone"))

        self.assertIsNone(previous_campaign(self.user))

    def test_the_offer_says_what_would_carry(self):
        offer = CampaignCarry.of(played_session(self.user, summary=RECAP))

        self.assertEqual(
            offer.parts,
            [
                "where and when the party is",
                "1 person",
                "1 thing",
                "2 threads",
                "1 thing carried",
                "1 table ruling",
                "the story so far",
            ],
        )


class ContinueEndpointTest(BaseTestCase):
    """The control on the new-session page, and what it leaves on the page."""

    def setUp(self):
        super().setUp()
        recap_writer = patch("gm.views.write_session_recap", return_value=WHOLE_RECAP)
        self.write_recap = recap_writer.start()
        self.addCleanup(recap_writer.stop)
        self.user = self.create_dm(username="gamemaster")
        self.login_as(self.user)
        self.previous = played_session(self.user, summary=RECAP, summary_covers=1)

    def continue_from(self, session_id):
        return self.client.post(CONTINUE_URL, {"session_id": session_id})

    def test_continuing_opens_the_new_session_holding_the_copies(self):
        response = self.continue_from(self.previous.pk)

        session = GMSession.objects.get(continued_from=self.previous)
        self.assertRedirects(
            response, f"/tarmar/gm/?session={session.pk}", fetch_redirect_response=False
        )
        self.assertEqual(records(session), records(self.previous))

    def test_a_second_press_lands_on_the_same_unplayed_continuation(self):
        self.continue_from(self.previous.pk)
        response = self.continue_from(self.previous.pk)

        (session,) = GMSession.objects.filter(continued_from=self.previous)
        self.assertRedirects(
            response, f"/tarmar/gm/?session={session.pk}", fetch_redirect_response=False
        )
        # Continuing writes no recap at all: that is the first turn's.
        self.write_recap.assert_not_called()

    def test_a_continuation_left_before_the_source_was_played_again_is_stale(self):
        self.continue_from(self.previous.pk)
        stale = GMSession.objects.get(continued_from=self.previous)
        GMSession.objects.filter(pk=stale.pk).update(
            created_at=timezone.now() - timedelta(days=1)
        )
        self.previous.save()  # played since: its updated_at moves on

        response = self.continue_from(self.previous.pk)

        fresh = GMSession.objects.filter(continued_from=self.previous).exclude(
            pk=stale.pk
        )
        self.assertEqual(fresh.count(), 1)
        self.assertRedirects(
            response,
            f"/tarmar/gm/?session={fresh.get().pk}",
            fetch_redirect_response=False,
        )

    def test_another_persons_session_cannot_be_continued(self):
        theirs = played_session(self.create_user(username="someone"))

        response = self.continue_from(theirs.pk)

        self.assertRedirects(response, "/tarmar/gm/", fetch_redirect_response=False)
        self.assertFalse(GMSession.objects.filter(continued_from=theirs).exists())

    def test_only_a_post_continues(self):
        response = self.client.get(CONTINUE_URL, {"session_id": self.previous.pk})

        self.assertEqual(response.status_code, 405)
        self.assertFalse(
            GMSession.objects.filter(continued_from=self.previous).exists()
        )

    def test_the_new_session_page_offers_the_last_campaign(self):
        response = self.client.get("/tarmar/gm/")

        self.assertContains(response, 'id="gm-campaign-continue"')
        self.assertContains(response, f'name="session_id" value="{self.previous.pk}"')
        self.assertContains(response, "<strong>The well</strong>")

    def test_a_session_page_carries_no_offer(self):
        response = self.client.get(f"/tarmar/gm/?session={self.previous.pk}")

        self.assertNotContains(response, 'id="gm-campaign-continue"')

    def play_first_turn(self, session: GMSession) -> list[dict]:
        """Send the continued session's first message; return its frames."""
        response = self.client.post(
            STREAM_URL,
            json.dumps({"session_id": session.pk, "message": "We return to Farport."}),
            content_type="application/json",
        )
        raw = b"".join(response.streaming_content).decode()
        return [
            json.loads(chunk[len("data: ") :])
            for chunk in raw.split("\n\n")
            if chunk.startswith("data: ")
        ]

    def test_continuing_calls_no_model_and_says_the_recap_is_to_come(self):
        """The form post is quick: the recap is the first turn's (review of #761)."""
        self.continue_from(self.previous.pk)
        session = GMSession.objects.get(continued_from=self.previous)

        response = self.client.get(f"/tarmar/gm/?session={session.pk}")

        self.write_recap.assert_not_called()
        self.assertEqual(session.history, [])
        self.assertContains(response, 'id="gm-session-continued"')
        self.assertContains(response, "Continues the campaign of")
        self.assertContains(response, "Brannoc")
        self.assertContains(
            response, "a recap of it is written when you send your first message"
        )

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_the_first_turn_writes_the_recap_and_reads_it(self, stream):
        stream.side_effect = replies = gm_replies("Brannoc waits on the quay.")
        self.continue_from(self.previous.pk)
        session = GMSession.objects.get(continued_from=self.previous)

        frames = self.play_first_turn(session)

        self.assertIn({"status": "reading back over the last session"}, frames)
        self.write_recap.assert_called_once()
        sent = json.dumps(replies.calls[-1])
        self.assertIn(WHOLE_RECAP, sent)
        self.assertIn("Brannoc", sent)
        self.assertIn("criticals", sent)
        session.refresh_from_db()
        self.assertEqual(session.name, "We return to Farport.")
        self.assertEqual(session.summary, WHOLE_RECAP)
        self.assertEqual(carried_recap(session), CarriedRecap(WHOLE_RECAP, whole=True))
        self.assertEqual(
            [entry["content"] for entry in session.history[1:]],
            ["We return to Farport.", "Brannoc waits on the quay."],
        )
        page = self.client.get(f"/tarmar/gm/?session={session.pk}")
        self.assertNotContains(page, WHOLE_RECAP)
        self.assertContains(page, "a recap of the whole of it opens this conversation")

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_second_turn_writes_no_second_recap(self, stream):
        stream.side_effect = gm_replies("On the quay.", "The tide turns.")
        self.continue_from(self.previous.pk)
        session = GMSession.objects.get(continued_from=self.previous)
        self.play_first_turn(session)

        self.play_first_turn(session)

        self.write_recap.assert_called_once()
        session.refresh_from_db()
        self.assertEqual(
            sum(1 for entry in session.history if entry.get("kind") == "recap"), 1
        )

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_fold_recap_carried_after_a_failed_call_is_labelled_as_one(self, stream):
        stream.side_effect = gm_replies("On the quay.")
        self.write_recap.side_effect = ConnectionError("down")
        self.continue_from(self.previous.pk)
        session = GMSession.objects.get(continued_from=self.previous)
        self.play_first_turn(session)

        response = self.client.get(f"/tarmar/gm/?session={session.pk}")

        self.assertContains(response, "its recap up to its last fold opens")

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_fallback_recap_is_tried_again_while_the_session_is_unplayed(
        self, stream
    ):
        """A failed first turn keeps the whole recap pending (third review)."""
        stream.side_effect = gm_replies(ConnectionError("model down"), "On the quay.")
        self.write_recap.side_effect = [ConnectionError("down"), WHOLE_RECAP]
        self.continue_from(self.previous.pk)
        session = GMSession.objects.get(continued_from=self.previous)

        self.play_first_turn(session)
        session.refresh_from_db()
        self.assertEqual(carried_recap(session), CarriedRecap(RECAP, whole=False))
        self.assertTrue(recap_pending(session))

        self.play_first_turn(session)
        session.refresh_from_db()
        self.assertEqual(carried_recap(session), CarriedRecap(WHOLE_RECAP, whole=True))
        self.assertEqual(
            sum(1 for entry in session.history if entry.get("kind") == "recap"), 1
        )
        self.assertFalse(recap_pending(session))

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_the_turns_clock_starts_after_the_catch_up(self, stream):
        """Writing the recap is not the turn's time to spend (third review).

        The recap takes longer than the whole turn budget on a fake clock; the
        turn still finishes, because its clock starts once the recap is filed.
        """
        stream.side_effect = gm_replies("On the quay.")
        now = [1000.0]

        def slow_recap(prompt: str) -> str:
            now[0] += 400.0
            return WHOLE_RECAP

        self.write_recap.side_effect = slow_recap
        self.continue_from(self.previous.pk)
        session = GMSession.objects.get(continued_from=self.previous)

        with (
            self.settings(GM_STREAM_BUDGET_SECONDS=300),
            patch("time.monotonic", side_effect=lambda: now[0]),
        ):
            frames = self.play_first_turn(session)

        self.assertTrue(any(frame.get("done") for frame in frames), frames)

    def test_continuing_is_rate_limited(self):
        with self.settings(RATE_LIMIT_ENABLED=True):
            codes = []
            for _ in range(12):
                GMSession.objects.filter(continued_from=self.previous).delete()
                codes.append(self.continue_from(self.previous.pk).status_code)
        self.assertIn(429, codes)

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_new_session_carries_nothing_unless_asked(self, stream):
        stream.side_effect = gm_replies("A new road.")

        response = self.client.post(
            STREAM_URL,
            json.dumps({"message": "A new game."}),
            content_type="application/json",
        )
        b"".join(response.streaming_content)

        session = GMSession.objects.exclude(pk=self.previous.pk).get()
        self.assertIsNone(session.continued_from)
        self.assertEqual(
            records(session),
            {"place": [], "supplies": [], "entities": [], "threads": [], "rulings": []},
        )
