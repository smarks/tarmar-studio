"""Sending a GM session to Claude for review (issue #402).

The queue has three parties: the Game Master who sends a session, the reviewer
on the LLM host that claims and completes it over ssh, and the page that shows
the findings. These tests hold each to its part: only a Game Master can send;
the session is frozen when sent; a review is claimed once; findings only land
on a claimed review; the page shows them sanitised, to the sender alone.
"""

import json
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.utils import timezone

from common.test_helpers import BaseTestCase

from .models import GMReviewRequest, GMSession
from .refusal_words import refusal_words

CREATE_URL = "/tarmar/gm/reviews/"


def _session(user, name: str = "The twins") -> GMSession:
    session = GMSession.objects.create(user=user, name=name)
    session.history = [
        {"role": "user", "content": "Build the twins."},
        {"role": "assistant", "content": "Both twins are built."},
    ]
    session.save()
    return session


def _review(user, **fields) -> GMReviewRequest:
    session = _session(user)
    return GMReviewRequest.objects.create(
        session=session,
        requested_by=user,
        note="look at the ledger",
        snapshot={"history": session.history},
        **fields,
    )


class ReviewQueueModelTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_admin()

    def test_a_pending_review_is_claimed_once(self):
        review = _review(self.user)

        self.assertTrue(GMReviewRequest.claim(review.pk, "studio"))
        self.assertFalse(GMReviewRequest.claim(review.pk, "another"))

        review.refresh_from_db()
        self.assertEqual(review.status, GMReviewRequest.STATUS_CLAIMED)
        self.assertEqual(review.runner, "studio")
        self.assertIsNotNone(review.claimed_at)

    def test_a_claim_abandoned_for_an_hour_can_be_taken_again(self):
        review = _review(self.user)
        GMReviewRequest.claim(review.pk, "studio")
        GMReviewRequest.objects.filter(pk=review.pk).update(
            claimed_at=timezone.now()
            - timedelta(minutes=GMReviewRequest.STALE_CLAIM_MINUTES + 1)
        )

        self.assertTrue(GMReviewRequest.claim(review.pk, "studio-again"))

    def test_done_and_failed_reviews_are_not_claimable(self):
        done = _review(self.user, status=GMReviewRequest.STATUS_DONE)
        failed = _review(self.user, status=GMReviewRequest.STATUS_FAILED)

        self.assertNotIn(done, GMReviewRequest.claimable())
        self.assertNotIn(failed, GMReviewRequest.claimable())

    def test_complete_and_fail_record_the_outcome(self):
        review = _review(self.user, status=GMReviewRequest.STATUS_CLAIMED)

        review.complete("## Summary\nClean.")
        self.assertEqual(review.status, GMReviewRequest.STATUS_DONE)
        self.assertIsNotNone(review.completed_at)

        review.fail("timed out")
        review.refresh_from_db()
        self.assertEqual(
            (review.status, review.failure),
            (GMReviewRequest.STATUS_FAILED, "timed out"),
        )


class ReviewCommandTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_admin()

    def _run(self, *args: str, stdin: str = "") -> str:
        out = StringIO()
        with patch("sys.stdin", StringIO(stdin)):
            call_command("gm_review", *args, stdout=out)
        return out.getvalue()

    def test_list_shows_claimable_ids_oldest_first(self):
        first, second = _review(self.user), _review(self.user)
        _review(self.user, status=GMReviewRequest.STATUS_DONE)

        self.assertEqual(self._run("list").split(), [str(first.pk), str(second.pk)])

    def test_show_gives_the_note_and_the_frozen_session(self):
        review = _review(self.user)

        payload = json.loads(self._run("show", str(review.pk)))

        self.assertEqual(payload["review"]["note"], "look at the ledger")
        self.assertEqual(
            payload["snapshot"]["history"][0]["content"], "Build the twins."
        )

    def test_claim_then_complete_with_findings_from_stdin(self):
        review = _review(self.user)

        self.assertIn(
            "claimed", self._run("claim", str(review.pk), "--runner", "studio")
        )
        self.assertIn(
            "completed",
            self._run("complete", str(review.pk), stdin="1. **The twins merged.**\n"),
        )

        review.refresh_from_db()
        self.assertEqual(review.findings, "1. **The twins merged.**")

    def test_a_second_claim_is_an_error(self):
        review = _review(self.user)
        self._run("claim", str(review.pk), "--runner", "studio")

        with self.assertRaisesMessage(CommandError, "is not claimable"):
            self._run("claim", str(review.pk), "--runner", "other")

    def test_findings_only_land_on_a_claimed_review(self):
        review = _review(self.user)

        with self.assertRaisesMessage(CommandError, "claim it first"):
            self._run("complete", str(review.pk), stdin="findings")

    def test_empty_findings_are_refused(self):
        review = _review(self.user, status=GMReviewRequest.STATUS_CLAIMED)

        with self.assertRaisesMessage(CommandError, "No findings"):
            self._run("complete", str(review.pk), stdin="   ")

    def test_fail_records_the_reason(self):
        review = _review(self.user, status=GMReviewRequest.STATUS_CLAIMED)

        self._run("fail", str(review.pk), "--reason", "the model run timed out")

        review.refresh_from_db()
        self.assertEqual(review.failure, "the model run timed out")

    def test_an_unknown_review_is_an_error(self):
        with self.assertRaisesMessage(CommandError, "No review 999999"):
            self._run("show", "999999")


class SendForReviewViewTest(BaseTestCase):
    def test_a_game_master_sends_a_session_and_it_is_frozen(self):
        admin = self.create_admin()
        self.login_as(admin)
        session = _session(admin)

        response = self.post_json(
            CREATE_URL,
            {"session_id": session.pk, "note": "  the death went unnoticed  "},
        )

        self.assertEqual(response.status_code, 200)
        review = GMReviewRequest.objects.get()
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "review_id": review.pk,
                "url": f"/tarmar/gm/reviews/{review.pk}/",
            },
        )
        self.assertEqual(review.note, "the death went unnoticed")
        self.assertEqual(review.requested_by, admin)
        self.assertEqual(review.status, GMReviewRequest.STATUS_PENDING)
        self.assertEqual(
            [entry["message"]["content"] for entry in review.snapshot["history"]],
            ["Build the twins.", "Both twins are built."],
        )

        # Play carries on; the review keeps what was sent.
        session.history = [*session.history, {"role": "user", "content": "later"}]
        session.save()
        review.refresh_from_db()
        self.assertEqual(len(review.snapshot["history"]), 2)

    def test_a_player_cannot_send_one(self):
        player = self.create_user()
        self.login_as(player)
        session = _session(player)

        response = self.post_json(CREATE_URL, {"session_id": session.pk, "note": "x"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["error"],
            refusal_words().views.send_to_claude_game_master_only.format(),
        )
        self.assertFalse(GMReviewRequest.objects.exists())

    def test_someone_elses_session_cannot_be_sent(self):
        other = self.create_user(username="other")
        session = _session(other)
        self.login_as(self.create_admin())

        response = self.post_json(CREATE_URL, {"session_id": session.pk})

        self.assertEqual(response.status_code, 404)

    def test_a_note_that_is_not_text_is_refused(self):
        admin = self.create_admin()
        self.login_as(admin)

        response = self.post_json(
            CREATE_URL, {"session_id": _session(admin).pk, "note": ["x"]}
        )

        self.assertEqual(response.status_code, 400)

    def test_the_chat_page_offers_it_to_a_game_master_only(self):
        admin = self.create_admin()
        self.login_as(admin)
        admin_page = self.client.get("/tarmar/gm/")
        self.logout()
        self.login_as(self.create_user())
        player_page = self.client.get("/tarmar/gm/")

        self.assertEqual(admin_page.context["chat_config"]["reviewUrl"], CREATE_URL)
        self.assertContains(admin_page, 'id="gm-review-button"')
        self.assertIsNone(player_page.context["chat_config"]["reviewUrl"])
        # What the page says instead is the endpoint's own refusal (#94).
        self.assertEqual(
            player_page.context["chat_config"]["reviewGameMasterOnly"],
            refusal_words().views.send_to_claude_game_master_only,
        )
        self.assertNotContains(player_page, 'id="gm-review-button"')


class ReviewPageTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.create_admin()
        self.login_as(self.admin)

    def test_when_it_was_sent_is_written_in_the_readers_zone(self):
        """The sent times are <time> elements the page rewrites (issue #62)."""
        review = _review(self.admin)
        other = GMReviewRequest.objects.create(
            session=review.session,
            requested_by=self.admin,
            note="again",
            snapshot={"history": review.session.history},
        )

        response = self.client.get(f"/tarmar/gm/reviews/{review.pk}/")

        for sent in (review, other):
            self.assertContains(
                response,
                f'<time class="local-time" datetime="{sent.created_at.isoformat()}" '
                'data-local-time-style="datetime">',
            )

    def test_a_waiting_review_says_so(self):
        review = _review(self.admin)

        response = self.client.get(f"/tarmar/gm/reviews/{review.pk}/")

        self.assertContains(response, "look at the ledger")
        self.assertContains(response, 'id="gm-review-waiting"')
        self.assertContains(response, "Waiting for Claude")

    def test_findings_are_rendered_and_sanitised(self):
        review = _review(self.admin, status=GMReviewRequest.STATUS_CLAIMED)
        review.complete("1. **The twins merged.**\n\n<script>alert('x')</script>")

        response = self.client.get(f"/tarmar/gm/reviews/{review.pk}/")

        self.assertContains(response, "<strong>The twins merged.</strong>")
        self.assertNotContains(response, "<script>alert")
        self.assertContains(response, f"discuss GM review {review.pk}")

    def test_a_failed_review_gives_the_reason(self):
        review = _review(self.admin, status=GMReviewRequest.STATUS_CLAIMED)
        review.fail("the model run timed out")

        response = self.client.get(f"/tarmar/gm/reviews/{review.pk}/")

        self.assertContains(
            response, "The review could not be done: the model run timed out"
        )

    def test_other_reviews_of_the_session_are_listed(self):
        first = _review(self.admin)
        second = GMReviewRequest.objects.create(
            session=first.session, requested_by=self.admin, snapshot={}
        )

        response = self.client.get(f"/tarmar/gm/reviews/{first.pk}/")

        self.assertContains(response, f"/tarmar/gm/reviews/{second.pk}/")

    def test_nobody_else_can_read_it(self):
        review = _review(self.admin)
        self.logout()
        self.login_as(self.create_admin(username="another-admin"))

        self.assertEqual(
            self.client.get(f"/tarmar/gm/reviews/{review.pk}/").status_code, 404
        )


LIST_URL = "/tarmar/gm/reviews/"
STATUSES_URL = "/tarmar/gm/reviews/statuses/"


class ReviewListPageTest(BaseTestCase):
    """The list of every review a Game Master sent (issue #572)."""

    def setUp(self):
        super().setUp()
        self.admin = self.create_admin()
        self.login_as(self.admin)

    def test_a_game_master_sees_each_review_with_its_links(self):
        review = _review(self.admin, status=GMReviewRequest.STATUS_CLAIMED)
        review.runner = "quantumfoam"
        review.save()

        response = self.client.get(LIST_URL)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="/tarmar/gm/reviews/{review.pk}/"')
        self.assertContains(response, f'href="/tarmar/gm/?session={review.session_id}"')
        self.assertContains(response, "The twins")
        self.assertContains(response, "look at the ledger")
        self.assertContains(response, "Claude is reviewing it")
        self.assertContains(response, "quantumfoam")
        self.assertContains(response, self.admin.username)
        self.assertContains(response, 'class="local-time"')

    def test_a_failed_review_says_why_on_the_list(self):
        """ "Failed" alone told the Game Master nothing (issue #606).

        The reviewer could not log in, every queued review was claimed and
        marked failed in four seconds, and the only trace of the reason was a
        log file on one machine. He asked in chat whether the transcript had
        arrived; nothing in the app could have answered him.
        """
        _review(
            self.admin,
            status=GMReviewRequest.STATUS_FAILED,
            failure="OAuth session expired and could not be refreshed",
        )

        response = self.client.get(LIST_URL)

        self.assertContains(response, "OAuth session expired")

    def test_a_review_that_has_not_failed_shows_no_reason(self):
        _review(self.admin, status=GMReviewRequest.STATUS_CLAIMED)

        response = self.client.get(LIST_URL)

        self.assertNotContains(response, "gm-reviews-failure")

    def test_a_player_is_refused(self):
        self.logout()
        player = self.create_user()
        self.login_as(player)

        self.assertEqual(self.client.get(LIST_URL).status_code, 403)
        self.assertEqual(self.client.get(STATUSES_URL).status_code, 403)

    def test_a_dm_account_counts_as_a_game_master(self):
        self.logout()
        self.login_as(self.create_dm())

        self.assertEqual(self.client.get(LIST_URL).status_code, 200)

    def test_only_the_readers_own_reviews_are_listed(self):
        mine = _review(self.admin)
        theirs = _review(self.create_admin(username="another-admin"))

        reviews = list(self.client.get(LIST_URL).context["reviews"])

        self.assertEqual(reviews, [mine])
        self.assertNotIn(theirs, reviews)

    def test_newest_first(self):
        older = _review(self.admin)
        newer = _review(self.admin)
        GMReviewRequest.objects.filter(pk=older.pk).update(
            created_at=timezone.now() - timedelta(days=1)
        )

        reviews = list(self.client.get(LIST_URL).context["reviews"])

        self.assertEqual(reviews, [newer, older])

    def test_the_status_filter(self):
        pending = _review(self.admin)
        claimed = _review(self.admin, status=GMReviewRequest.STATUS_CLAIMED)
        done = _review(self.admin, status=GMReviewRequest.STATUS_DONE)
        failed = _review(self.admin, status=GMReviewRequest.STATUS_FAILED)

        def listed(choice):
            response = self.client.get(LIST_URL, {"status": choice})
            return set(response.context["reviews"])

        self.assertEqual(listed("all"), {pending, claimed, done, failed})
        self.assertEqual(listed("open"), {pending, claimed})
        self.assertEqual(listed("done"), {done})
        self.assertEqual(listed("failed"), {failed})
        self.assertEqual(listed("nonsense"), {pending, claimed, done, failed})
        self.assertContains(
            self.client.get(LIST_URL, {"status": "done"}),
            '<option value="done" selected>',
        )

    def test_an_empty_filter_says_so(self):
        _review(self.admin)

        response = self.client.get(LIST_URL, {"status": "failed"})

        self.assertContains(response, "No reviews match this filter.")

    def test_pagination_keeps_the_filter(self):
        from .views import REVIEWS_PER_PAGE

        for _ in range(REVIEWS_PER_PAGE + 1):
            _review(self.admin)

        first = self.client.get(LIST_URL, {"status": "open"})
        second = self.client.get(LIST_URL, {"status": "open", "page": 2})

        self.assertEqual(len(first.context["reviews"]), REVIEWS_PER_PAGE)
        self.assertContains(first, "Page 1 of 2")
        self.assertContains(first, "?status=open&amp;page=2")
        self.assertEqual(len(second.context["reviews"]), 1)

    def test_status_words_and_how_long_a_finished_review_took(self):
        review = _review(self.admin, status=GMReviewRequest.STATUS_DONE)
        claimed_at = timezone.now() - timedelta(minutes=5)
        GMReviewRequest.objects.filter(pk=review.pk).update(
            claimed_at=claimed_at,
            completed_at=claimed_at + timedelta(minutes=4, seconds=12),
        )
        _review(self.admin, status=GMReviewRequest.STATUS_FAILED)
        _review(self.admin)

        response = self.client.get(LIST_URL)

        self.assertContains(response, "4 min 12 s")
        self.assertContains(response, "Reviewed")
        self.assertContains(response, "The review failed")
        self.assertContains(response, "Waiting for Claude")

    def test_it_watches_only_while_a_review_is_open(self):
        done = _review(self.admin, status=GMReviewRequest.STATUS_DONE)
        response = self.client.get(LIST_URL)
        self.assertEqual(response.context["open_review_ids"], [])
        self.assertNotContains(response, 'id="gm-reviews-watching"')

        pending = _review(self.admin)
        response = self.client.get(LIST_URL)
        self.assertEqual(response.context["open_review_ids"], [pending.pk])
        self.assertContains(response, 'id="gm-reviews-watching"')
        self.assertNotIn(done.pk, response.context["open_review_ids"])

    def test_a_deleted_session_is_named_as_such(self):
        review = _review(self.admin)
        review.session.delete()

        self.assertContains(self.client.get(LIST_URL), "Deleted session")

    def test_the_chat_page_and_each_review_link_to_the_list(self):
        review = _review(self.admin)

        self.assertContains(self.client.get("/tarmar/gm/"), 'id="gm-reviews-link"')
        self.assertContains(
            self.client.get(f"/tarmar/gm/reviews/{review.pk}/"),
            f'<a href="{LIST_URL}" id="gm-review-all-link">All reviews</a>',
            html=True,
        )

    def test_the_status_endpoint_answers_for_the_readers_own_reviews(self):
        pending = _review(self.admin)
        done = _review(self.admin, status=GMReviewRequest.STATUS_DONE)
        theirs = _review(self.create_admin(username="another-admin"))

        response = self.client.get(
            STATUSES_URL, {"ids": f"{pending.pk},{done.pk},{theirs.pk},x"}
        )

        self.assertEqual(
            response.json(),
            {"reviews": {str(pending.pk): "pending", str(done.pk): "done"}},
        )

    def test_the_status_endpoint_with_no_ids_answers_nothing(self):
        self.assertEqual(self.client.get(STATUSES_URL).json(), {"reviews": {}})


class ReviewDurationTest(BaseTestCase):
    def test_duration_text(self):
        user = self.create_admin()
        review = _review(user)
        self.assertEqual(review.review_duration_text, "")

        start = timezone.now()
        for elapsed, expected in [
            (timedelta(seconds=9), "9 s"),
            (timedelta(minutes=3, seconds=1), "3 min 1 s"),
            (timedelta(hours=1, minutes=2), "1 h 2 min"),
            (timedelta(seconds=-5), "0 s"),
        ]:
            review.claimed_at = start
            review.completed_at = start + elapsed
            self.assertEqual(review.review_duration_text, expected)
