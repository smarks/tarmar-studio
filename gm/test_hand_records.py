"""The Game Master sets a session's records by hand (issues #789, #790, #791).

House rules, people, things, threads and the fold's recap were written only
when the model planned them. A Game Master may now add, correct, retire,
resolve, reopen or remove them from the page. A player sees them and may
not: the page draws no controls for his account and the endpoints refuse
him (coordinator's ruling under the standing rule; Spencer may overrule).
Every hand edit is logged with who made it, and the transcript shows it.
"""

import json

from django.urls import reverse
from django.utils import timezone

from common.test_helpers import BaseTestCase

from .hand_records import (
    GAME_MASTER_ONLY,
    RECAP_WHILE_TURN_RUNNING,
    HandEditError,
    add_ruling,
    correct_recap,
    edit_ruling,
    open_thread,
    remove_entity,
    reopen_thread,
    resolve_thread,
    retire_ruling,
    save_entity,
)
from .models import (
    GMRecordEdit,
    GMSession,
    GMTurnRecord,
    SceneEntity,
    StoryThread,
    TableRuling,
)
from .testing import run_actions
from .transcript import render_transcript_text, transcript_payload
from .turn_engine.state import ruling_lines


class HandRecordsTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm(username="hand-gm")
        self.session = GMSession.objects.create(
            user=self.game_master, name="Crypt", mode="aide"
        )

    # -- house rules ---------------------------------------------------------

    def test_a_ruling_added_by_hand_is_one_the_model_reads(self):
        done = add_ruling(self.session, self.game_master, "critical hits", "a 20 crits")

        ruling = TableRuling.objects.get(session=self.session, active=True)
        self.assertEqual((ruling.topic, ruling.text), ("critical hits", "a 20 crits"))
        self.assertIsNone(ruling.turn)
        self.assertIn(ruling.line, ruling_lines(self.session))
        self.assertEqual(done.edit.record_kind, GMRecordEdit.RULING)
        self.assertEqual(done.edit.record_id, ruling.pk)
        self.assertEqual(done.edit.user, self.game_master)
        self.assertTrue(done.edit.is_game_master)
        self.assertEqual(
            done.edit.line, f"Set by hand by hand-gm: house rule {ruling.pk} added"
        )

    def test_a_ruling_on_a_topic_already_ruled_retires_the_old_one(self):
        run_actions(
            self.session,
            {"action": "record_house_rule", "topic": "fumbles", "rule": "a 3 drops it"},
        )
        add_ruling(self.session, self.game_master, "Fumbles", "a 3 is only a miss")

        active = TableRuling.objects.filter(session=self.session, active=True)
        self.assertEqual([ruling.text for ruling in active], ["a 3 is only a miss"])

    def test_correcting_a_ruling_keeps_the_old_one_readable(self):
        first = add_ruling(self.session, self.game_master, "crits", "a 20 crits")
        old_id = first.edit.record_id

        done = edit_ruling(
            self.session, self.game_master, old_id, "critical hits", "19 and 20 crit"
        )

        old = TableRuling.objects.get(pk=old_id)
        self.assertFalse(old.active)
        new = TableRuling.objects.get(pk=done.edit.record_id)
        self.assertTrue(new.active)
        self.assertEqual((new.topic, new.text), ("critical hits", "19 and 20 crit"))
        self.assertEqual(done.edit.before["ruling_id"], old_id)
        self.assertEqual(
            TableRuling.objects.filter(session=self.session, active=True).count(), 1
        )

    def test_retiring_a_ruling(self):
        ruling_id = add_ruling(
            self.session, self.game_master, "crits", "x"
        ).edit.record_id

        retire_ruling(self.session, self.game_master, ruling_id)

        self.assertFalse(TableRuling.objects.get(pk=ruling_id).active)
        self.assertEqual(ruling_lines(self.session), [])
        with self.assertRaisesMessage(HandEditError, "no such ruling"):
            retire_ruling(self.session, self.game_master, ruling_id)

    def test_a_ruling_is_held_to_the_limits_a_plan_is(self):
        with self.assertRaisesMessage(
            HandEditError, "The topic must be 60 characters or fewer"
        ):
            add_ruling(self.session, self.game_master, "x" * 61, "rule")
        with self.assertRaisesMessage(HandEditError, "The rule is needed"):
            add_ruling(self.session, self.game_master, "crits", "   ")
        self.assertFalse(GMRecordEdit.objects.exists())

    # -- people and things ---------------------------------------------------

    def test_adding_correcting_and_removing_a_person(self):
        added = save_entity(
            self.session,
            self.game_master,
            SceneEntity.PERSON,
            {"name": "Brannoc", "where": "the shrine", "wants": "his box back"},
        )
        brannoc = SceneEntity.objects.get(pk=added.edit.record_id)
        self.assertEqual(brannoc.where, "the shrine")

        # A wrong "where" can be cleared, not only replaced.
        save_entity(
            self.session,
            self.game_master,
            SceneEntity.PERSON,
            {"name": "Brannoc", "where": "", "wants": "his box back"},
            entity_id=brannoc.pk,
        )
        brannoc.refresh_from_db()
        self.assertEqual(brannoc.where, "")

        remove_entity(self.session, self.game_master, brannoc.pk)
        self.assertFalse(SceneEntity.objects.filter(pk=brannoc.pk).exists())
        changes = list(
            GMRecordEdit.objects.filter(record_id=brannoc.pk).values_list(
                "change", flat=True
            )
        )
        self.assertEqual(
            changes,
            [GMRecordEdit.ADDED, GMRecordEdit.EDITED, GMRecordEdit.REMOVED],
        )

    def test_a_name_is_one_row(self):
        run_actions(self.session, {"action": "record_object", "name": "Iron box"})
        with self.assertRaisesMessage(HandEditError, "already recorded"):
            save_entity(
                self.session, self.game_master, SceneEntity.THING, {"name": "iron BOX"}
            )

    def test_a_record_of_another_session_is_not_found(self):
        other = GMSession.objects.create(user=self.game_master, name="Other")
        stranger = SceneEntity.objects.create(
            session=other, kind=SceneEntity.PERSON, name="Vane"
        )
        with self.assertRaisesMessage(HandEditError, "no such person or thing"):
            remove_entity(self.session, self.game_master, stranger.pk)

    # -- threads -------------------------------------------------------------

    def test_a_thread_opened_corrected_resolved_and_reopened(self):
        thread_id = open_thread(
            self.session, self.game_master, "who took the box"
        ).edit.record_id

        edit_thread_done = resolve_thread(
            self.session, self.game_master, thread_id, "Brannoc had it"
        )
        thread = StoryThread.objects.get(pk=thread_id)
        self.assertTrue(thread.resolved)
        self.assertEqual(thread.resolution, "Brannoc had it")
        self.assertEqual(edit_thread_done.edit.change, GMRecordEdit.RESOLVED)
        with self.assertRaisesMessage(HandEditError, "already resolved"):
            resolve_thread(self.session, self.game_master, thread_id, "")

        reopen_thread(self.session, self.game_master, thread_id)
        thread.refresh_from_db()
        self.assertFalse(thread.resolved)
        self.assertEqual(thread.resolution, "")
        self.assertIsNone(thread.resolved_at)

    # -- the recap -----------------------------------------------------------

    def test_the_recap_is_corrected_where_the_model_reads_it(self):
        self.session.summary = "The fake Game Master considers it."
        self.session.summary_covers = 14
        self.session.save()

        correct_recap(self.session, self.game_master, "The party opened the crypt.")

        self.session.refresh_from_db()
        self.assertEqual(self.session.summary, "The party opened the crypt.")
        self.assertEqual(self.session.summary_covers, 14)
        edit = GMRecordEdit.objects.get(record_kind=GMRecordEdit.RECAP)
        self.assertEqual(edit.before["summary"], "The fake Game Master considers it.")

    def test_a_recap_keeps_its_lines_and_paragraphs(self):
        """Review of #791: the correction came back as one line."""
        self.session.summary = "x"
        self.session.summary_covers = 4
        self.session.save()

        correct_recap(
            self.session,
            self.game_master,
            "  First.\r\n\r\nSecond:  \n- one\n- two  \n",
        )

        self.session.refresh_from_db()
        self.assertEqual(self.session.summary, "First.\n\nSecond:\n- one\n- two")

    def test_a_recap_is_not_corrected_while_a_turn_runs(self):
        """Review of #791: the running turn's fold would overwrite it, unseen."""
        self.session.summary = "The turn's recap."
        self.session.summary_covers = 4
        self.session.save()
        running = GMTurnRecord.objects.create(
            session=self.session,
            user=self.game_master,
            outcome=GMTurnRecord.OUTCOME_RUNNING,
            started_at=timezone.now(),
        )

        with self.assertRaisesMessage(HandEditError, RECAP_WHILE_TURN_RUNNING):
            correct_recap(self.session, self.game_master, "Mine.")

        self.session.refresh_from_db()
        self.assertEqual(self.session.summary, "The turn's recap.")
        self.assertFalse(GMRecordEdit.objects.exists())
        # Once the turn has ended, it goes through.
        running.outcome = GMTurnRecord.OUTCOME_OK
        running.save()
        correct_recap(self.session, self.game_master, "Mine.")
        self.session.refresh_from_db()
        self.assertEqual(self.session.summary, "Mine.")

    def test_there_is_no_recap_to_correct_before_a_fold(self):
        with self.assertRaisesMessage(HandEditError, "not been folded"):
            correct_recap(self.session, self.game_master, "anything")

    # -- who may -------------------------------------------------------------

    def test_a_player_may_not_set_any_of_them(self):
        player = self.create_player(username="hand-player")
        session = GMSession.objects.create(user=player, name="Mine")
        thread = StoryThread.objects.create(session=session, text="a debt")
        attempts = (
            lambda: add_ruling(session, player, "crits", "a 20 crits"),
            lambda: save_entity(session, player, SceneEntity.PERSON, {"name": "Vane"}),
            lambda: open_thread(session, player, "a thing"),
            lambda: resolve_thread(session, player, thread.pk, ""),
            lambda: correct_recap(session, player, "x"),
        )
        for index, attempt in enumerate(attempts):
            with (
                self.subTest(attempt=index),
                self.assertRaisesMessage(HandEditError, GAME_MASTER_ONLY),
            ):
                attempt()
        self.assertFalse(GMRecordEdit.objects.exists())
        self.assertFalse(TableRuling.objects.exists())

    def test_edits_are_append_only(self):
        edit = add_ruling(self.session, self.game_master, "crits", "x").edit
        with self.assertRaises(ValueError):
            edit.save()


class HandRecordEndpointsTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm(username="endpoint-gm")
        self.session = GMSession.objects.create(user=self.game_master, name="Crypt")
        self.login_as(self.game_master)

    def post(self, name: str, body: dict, session: GMSession | None = None):
        return self.client.post(
            reverse(name, args=[(session or self.session).pk]),
            json.dumps(body),
            content_type="application/json",
        )

    def test_each_endpoint_records_a_change(self):
        ruling = self.post(
            "gm:rulings", {"change": "add", "topic": "crits", "text": "a 20 crits"}
        )
        person = self.post(
            "gm:entities",
            {"change": "save", "kind": "person", "name": "Brannoc", "where": "shrine"},
        )
        thread = self.post("gm:threads", {"change": "open", "text": "who took it"})

        for response in (ruling, person, thread):
            with self.subTest(response=response.json()):
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["ok"])
                self.assertIn("Set by hand by endpoint-gm", response.json()["set_by"])
        self.assertEqual(GMRecordEdit.objects.filter(session=self.session).count(), 3)

    def test_a_refusal_says_why(self):
        response = self.post("gm:rulings", {"change": "retire", "ruling_id": 999})
        self.assertEqual(response.status_code, 400)
        self.assertIn("no such ruling", response.json()["error"])
        unknown = self.post("gm:threads", {"change": "burn"})
        self.assertEqual(unknown.status_code, 400)
        self.assertIn("change must be one of", unknown.json()["error"])
        junk = self.post("gm:entities", {"change": "remove", "entity_id": [1]})
        self.assertEqual(junk.status_code, 400)

    def test_a_player_is_refused_every_change_and_nothing_is_written(self):
        """All ten endpoint and change pairs (review of #789, #790, #791)."""
        player = self.create_player(username="endpoint-all")
        session = GMSession.objects.create(user=player, name="Mine")
        session.summary = "Their recap."
        session.summary_covers = 2
        session.save()
        ruling = TableRuling.objects.create(session=session, topic="crits", text="x")
        person = SceneEntity.objects.create(
            session=session, kind=SceneEntity.PERSON, name="Vane"
        )
        thread = StoryThread.objects.create(session=session, text="a debt")
        closed = StoryThread.objects.create(session=session, text="old", resolved=True)
        attempts = (
            ("gm:rulings", {"change": "add", "topic": "t", "text": "r"}),
            (
                "gm:rulings",
                {"change": "edit", "ruling_id": ruling.pk, "topic": "t", "text": "r"},
            ),
            ("gm:rulings", {"change": "retire", "ruling_id": ruling.pk}),
            ("gm:entities", {"change": "save", "kind": "person", "name": "Someone"}),
            ("gm:entities", {"change": "remove", "entity_id": person.pk}),
            ("gm:threads", {"change": "open", "text": "x"}),
            ("gm:threads", {"change": "edit", "thread_id": thread.pk, "text": "y"}),
            ("gm:threads", {"change": "resolve", "thread_id": thread.pk}),
            ("gm:threads", {"change": "reopen", "thread_id": closed.pk}),
            ("gm:recap", {"change": "edit", "text": "Mine now."}),
        )
        self.logout()
        self.login_as(player)

        for name, body in attempts:
            with self.subTest(endpoint=name, change=body["change"]):
                response = self.post(name, body, session)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["error"], GAME_MASTER_ONLY)

        self.assertFalse(GMRecordEdit.objects.exists())
        ruling.refresh_from_db()
        self.assertTrue(ruling.active)
        self.assertEqual(TableRuling.objects.filter(session=session).count(), 1)
        self.assertEqual(
            list(
                SceneEntity.objects.filter(session=session).values_list(
                    "name", flat=True
                )
            ),
            ["Vane"],
        )
        self.assertEqual(
            sorted(
                StoryThread.objects.filter(session=session).values_list(
                    "text", "resolved"
                )
            ),
            [("a debt", False), ("old", True)],
        )
        session.refresh_from_db()
        self.assertEqual(session.summary, "Their recap.")

    def test_a_player_is_refused_and_sees_no_controls(self):
        player = self.create_player(username="endpoint-player")
        session = GMSession.objects.create(user=player, name="Mine")
        StoryThread.objects.create(session=session, text="a debt owed")
        self.logout()
        self.login_as(player)

        response = self.post("gm:threads", {"change": "open", "text": "x"}, session)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], GAME_MASTER_ONLY)

        panel = self.client.get(reverse("gm:campaign_panel", args=[session.pk]))
        self.assertContains(panel, "a debt owed")
        self.assertContains(panel, "thread ")
        self.assertNotContains(panel, "gm-hand-form")
        self.assertNotContains(panel, "gm-hand-action")

    def test_the_game_master_sees_the_controls_and_who_set_a_row(self):
        self.post(
            "gm:entities", {"change": "save", "kind": "thing", "name": "Iron box"}
        )
        run_actions(self.session, {"action": "record_npc", "name": "Vane"})

        panel = self.client.get(reverse("gm:campaign_panel", args=[self.session.pk]))

        self.assertContains(panel, 'id="gm-add-person"')
        self.assertContains(panel, 'id="gm-add-ruling"')
        self.assertContains(panel, 'id="gm-open-thread"')
        self.assertContains(panel, "Set by hand by endpoint-gm: thing")
        # The model's record carries no such mark.
        self.assertEqual(panel.content.decode().count("gm-hand-mark"), 1)

    def test_another_users_session_is_not_found(self):
        stranger = self.create_dm(username="endpoint-stranger")
        session = GMSession.objects.create(user=stranger, name="Theirs")
        response = self.post("gm:threads", {"change": "open", "text": "x"}, session)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(StoryThread.objects.exists())


class FoldIsVisibleTest(BaseTestCase):
    """The fold is shown in the chat and the transcript (issue #791)."""

    RECAP = "The fake Game Master considers it. The crypt is quiet."

    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm(username="fold-gm")
        self.login_as(self.game_master)
        history = []
        for turn in range(6):
            history.append({"role": "user", "content": f"message {turn}"})
            history.append({"role": "assistant", "content": f"reply {turn}"})
        self.session = GMSession.objects.create(
            user=self.game_master, name="Long", history_json=json.dumps(history)
        )
        self.session.summary = self.RECAP
        self.session.summary_covers = 4
        self.session.save()

    def test_the_chat_shows_the_recap_where_the_fold_is(self):
        page = self.client.get(f"/tarmar/gm/?session={self.session.pk}")

        shown = page.context["history"]
        self.assertEqual(shown[4]["kind"], "fold_recap")
        self.assertEqual(shown[4]["content"], self.RECAP)
        self.assertEqual(shown[3]["content"], "reply 1")
        self.assertEqual(shown[5]["content"], "message 2")
        self.assertContains(page, 'id="gm-fold-recap"')
        self.assertContains(page, "Recap so far")
        self.assertContains(page, 'id="gm-fold-recap-form"')
        # The marker is the page's: the count and the stored history are not changed.
        self.assertEqual(self.session.message_count, 12)

    def test_a_player_reads_it_without_the_control(self):
        player = self.create_player(username="fold-player")
        self.session.user = player
        self.session.save()
        self.logout()
        self.login_as(player)

        page = self.client.get(f"/tarmar/gm/?session={self.session.pk}")

        self.assertContains(page, "Recap so far")
        self.assertNotContains(page, 'id="gm-fold-recap-form"')

    def test_an_unfolded_session_shows_no_marker(self):
        self.session.summary = ""
        self.session.summary_covers = 0
        self.session.save()
        page = self.client.get(f"/tarmar/gm/?session={self.session.pk}")
        self.assertNotContains(page, 'id="gm-fold-recap"')

    def test_a_correction_shows_who_made_it(self):
        response = self.client.post(
            reverse("gm:recap", args=[self.session.pk]),
            json.dumps({"change": "edit", "text": "The party opened the crypt."}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        page = self.client.get(f"/tarmar/gm/?session={self.session.pk}")

        self.assertContains(page, "The party opened the crypt.")
        self.assertContains(page, "Set by hand by fold-gm: recap corrected")

    def test_the_transcript_carries_the_recap_at_the_fold(self):
        text = render_transcript_text(self.session)

        self.assertIn("Folded: entries #0-#3 are read by the model", text)
        fold = text.index("[fold] ENTRIES #0-#3 ARE FOLDED")
        self.assertLess(text.index("reply 1"), fold)
        self.assertLess(fold, text.index("message 2"))
        self.assertIn(self.RECAP, text[fold:])
        payload = transcript_payload(self.session)
        self.assertEqual(payload["session"]["summary"], self.RECAP)
        self.assertEqual(payload["session"]["summary_covers"], 4)

    def test_the_transcript_lists_what_was_set_by_hand(self):
        correct_recap(self.session, self.game_master, "Corrected.")
        text = render_transcript_text(self.session)

        section = text[text.index("RECORDS SET BY HAND") :]
        self.assertIn(
            "Game Master authority · Set by hand by fold-gm: recap corrected", section
        )
        self.assertIn(self.RECAP, section)
        self.assertEqual(
            transcript_payload(self.session)["hand_edits"][0]["change"],
            GMRecordEdit.EDITED,
        )
