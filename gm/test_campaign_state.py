"""The campaign is a set of records, changed only by actions (issue #579).

The Game Master sent session 36 for review with one complaint: "It seems that
the game master can't decide if the box is fastened or not." An iron box was
resting on a stone ledge on turn 12, had a chain by turn 13, and was fused to
the rock by turn 16, with no event in between that would have changed it. He
spent five turns trying to take a thing the fiction had already made
immovable, and the reason it would not move kept changing.

Nothing held the box's state, so the narration reinvented it every turn once
the older turns fell out of the context window. The same thing happened to
where the party was, what hour it was, and who they had met. These tests are
about the records that now hold all of it.
"""

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from common.test_helpers import BaseTestCase
from gm.models import CampaignState, GMSession, SceneEntity, StoryThread, SupplyItem
from gm.retrieval import REPLY_RESERVE_TOKENS
from gm.summary import (
    FOLD_AT_FRACTION,
    history_tokens,
    should_fold,
    usable_budget,
)
from gm.test_summary import turns
from gm.turn_engine.plan import read_plan
from gm.turn_engine.policy import CAMPAIGN_ACTIONS, TurnPolicy
from gm.turn_engine.state import (
    MAX_CAMPAIGN_LINES_LISTED,
    CampaignBlock,
    entity_lines,
    state_block,
    state_block_tokens,
)

from .engine import MODE_PLAY

GAME_MASTER = TurnPolicy.for_turn(MODE_PLAY, is_game_master=True)

#: The migrations either side of supplies becoming rows (issue #579).
BEFORE_SUPPLY_ITEMS = "0017_gmsession_summary_gmsession_summary_covers"
WITH_SUPPLY_ITEMS = "0018_supply_items"


class ReadingCampaignActionsTest(TestCase):
    """What a plan may say about the campaign, and what it may not."""

    def read(self, *actions: dict, thread_ids=(2,)):
        import json

        return read_plan(
            json.dumps({"actions": list(actions)}),
            roster_ids=set(),
            policy=GAME_MASTER,
            thread_ids=thread_ids,
        )

    def test_every_campaign_action_is_a_known_action(self):
        """The schema, the reader and the executor agree on the names."""
        from gm.turn_engine.executor import ACTION_STATUS
        from gm.turn_engine.policy import ACTION_NAMES

        for name in CAMPAIGN_ACTIONS:
            with self.subTest(action=name):
                self.assertIn(name, ACTION_NAMES)
                self.assertIn(name, ACTION_STATUS)

    def test_the_party_moves_to_a_place(self):
        reading = self.read({"action": "move_party", "place": "the well at Farport"})

        self.assertTrue(reading.ok, reading.errors)
        self.assertEqual(reading.actions[0].place, "the well at Farport")

    def test_the_clock_takes_days_a_time_of_day_or_both(self):
        reading = self.read(
            {"action": "advance_time", "days": 1},
            {"action": "advance_time", "time_of_day": "evening"},
            {"action": "advance_time", "days": 2, "time_of_day": "dawn"},
        )

        self.assertTrue(reading.ok, reading.errors)
        self.assertEqual(
            [(action.days, action.time_of_day) for action in reading.actions],
            [(1, ""), (0, "evening"), (2, "dawn")],
        )

    def test_the_clock_needs_something_to_move(self):
        reading = self.read({"action": "advance_time"})

        self.assertFalse(reading.ok)
        self.assertIn("days, time_of_day, or both", " ".join(reading.errors))

    def test_an_hour_that_is_not_a_time_of_day_is_refused(self):
        reading = self.read({"action": "advance_time", "time_of_day": "teatime"})

        self.assertFalse(reading.ok)
        self.assertIn("time_of_day must be one of", " ".join(reading.errors))

    def test_a_year_in_one_action_is_refused(self):
        """A typo is not a season (MAX_DAYS_ADVANCED)."""
        reading = self.read({"action": "advance_time", "days": 500})

        self.assertFalse(reading.ok)

    def test_supplies_are_named_one_thing_at_a_time(self):
        reading = self.read(
            {
                "action": "adjust_supplies",
                "item": "rations",
                "amount": "4 days",
                "note": "shared between them",
            },
            {"action": "adjust_supplies", "item": "torches", "spent": True},
        )

        self.assertTrue(reading.ok, reading.errors)
        first, second = reading.actions
        self.assertEqual(
            (first.item, first.amount, first.notes),
            ("rations", "4 days", "shared between them"),
        )
        self.assertEqual((second.item, second.spent), ("torches", True))
        self.assertEqual(
            second.as_payload(),
            {"action": "adjust_supplies", "item": "torches", "spent": True},
        )

    def test_a_supply_with_no_name_is_refused(self):
        reading = self.read({"action": "adjust_supplies", "amount": "4 days"})

        self.assertFalse(reading.ok)
        self.assertIn("item must be a non-empty string", " ".join(reading.errors))

    def test_spent_is_true_or_false_and_nothing_else(self):
        reading = self.read(
            {"action": "adjust_supplies", "item": "torches", "spent": "yes"}
        )

        self.assertFalse(reading.ok)
        self.assertIn("spent must be true or false", " ".join(reading.errors))

    def test_the_planning_guidance_teaches_the_action_it_reads(self):
        """A bullet naming an action the reader does not know is a dead letter."""
        from gm.engine import section_plan_schema

        self.assertIn('"action": "adjust_supplies"', section_plan_schema())
        self.assertNotIn("record_supplies", section_plan_schema())

    def test_a_thing_does_not_want_anything(self):
        reading = self.read(
            {"action": "record_object", "name": "iron box", "wants": "to be opened"}
        )

        self.assertFalse(reading.ok)
        self.assertIn("use condition", " ".join(reading.errors))

    def test_a_thread_is_opened_with_words_and_closed_by_id(self):
        reading = self.read(
            {"action": "open_thread", "text": "the Sleeper must not wake"},
            {
                "action": "resolve_thread",
                "thread_id": 2,
                "resolution": "they sealed it",
            },
        )

        self.assertTrue(reading.ok, reading.errors)
        self.assertEqual(reading.actions[0].text, "the Sleeper must not wake")
        self.assertEqual(reading.actions[1].thread_id, 2)

    def test_a_thread_closed_by_name_is_refused(self):
        reading = self.read({"action": "resolve_thread", "thread_id": "the Sleeper"})

        self.assertFalse(reading.ok)
        self.assertIn("thread_id must be a thread id", " ".join(reading.errors))


class CampaignBlockTest(BaseTestCase):
    """What the model is shown about the campaign, every turn."""

    def setUp(self):
        super().setUp()
        self.session = GMSession.objects.create(user=self.create_user(), name="s")

    def test_a_session_with_no_campaign_shows_no_sections(self):
        block = CampaignBlock.of(self.session)

        self.assertFalse(block)
        self.assertEqual(
            state_block(GAME_MASTER, [], (), block).count("\n"),
            state_block(GAME_MASTER, []).count("\n"),
        )

    def test_the_block_says_where_when_who_what_and_what_is_open(self):
        CampaignState.objects.create(
            session=self.session,
            place="the well beneath Farport",
            day=3,
            time_of_day="evening",
        )
        SupplyItem.objects.create(
            session=self.session, name="torches", amount="2", note="an hour each"
        )
        SupplyItem.objects.create(session=self.session, name="rope", amount="40ft")
        SceneEntity.objects.create(
            session=self.session,
            kind=SceneEntity.PERSON,
            name="Mother Vane",
            where="the Farport shrine",
            wants="the box sealed",
            condition="wary",
        )
        SceneEntity.objects.create(
            session=self.session,
            kind=SceneEntity.THING,
            name="iron box",
            where="a stone ledge in the well",
            condition="resting, unfastened",
        )
        StoryThread.objects.create(
            session=self.session, text="the Sleeper must not wake"
        )

        written = state_block(GAME_MASTER, [], (), CampaignBlock.of(self.session))

        self.assertIn("- where: the well beneath Farport (not a mapped place)", written)
        self.assertIn("- when: day 3, evening", written)
        self.assertIn("- torches: 2 (an hour each)", written)
        self.assertIn("- rope: 40ft", written)
        self.assertIn("Mother Vane", written)
        self.assertIn("wants the box sealed", written)
        self.assertIn("iron box", written)
        self.assertIn("resting, unfastened", written)
        self.assertIn("the Sleeper must not wake", written)

    def test_a_resolved_thread_leaves_the_block(self):
        """It stays a record; it stops crowding what is still open."""
        open_thread = StoryThread.objects.create(
            session=self.session, text="find the key"
        )
        StoryThread.objects.create(
            session=self.session, text="pay the ferryman", resolved=True
        )

        block = CampaignBlock.of(self.session)

        self.assertEqual(len(block.threads), 1)
        self.assertIn(str(open_thread.pk), block.threads[0])
        self.assertEqual(StoryThread.objects.filter(session=self.session).count(), 2)

    def test_an_unmapped_place_says_so(self):
        """The canon rule: a place the gazetteer does not know is marked."""
        CampaignState.objects.create(session=self.session, place="Grandma's cellar")

        self.assertIn("(not a mapped place)", CampaignBlock.of(self.session).where[0])

    def test_the_block_is_the_same_for_the_same_records(self):
        """Rebuilt each turn, so it must not wobble between calls."""
        CampaignState.objects.create(session=self.session, place="Farport")
        SceneEntity.objects.create(
            session=self.session, kind=SceneEntity.THING, name="iron box"
        )

        first = state_block(GAME_MASTER, [], (), CampaignBlock.of(self.session))
        again = state_block(GAME_MASTER, [], (), CampaignBlock.of(self.session))

        self.assertEqual(first, again)


class CampaignPanelTest(BaseTestCase):
    """What the Game Master sees, beside what the model was told.

    The pane renders from the same records as the state block, so the two
    cannot disagree about where the party is or what state a thing is in.
    That disagreement was the complaint.
    """

    def setUp(self):
        super().setUp()
        self.user = self.create_dm()
        self.login_as(self.user)
        self.session = GMSession.objects.create(user=self.user, name="the well")

    def url(self, session=None) -> str:
        return f"/tarmar/gm/sessions/{(session or self.session).pk}/campaign/"

    def test_the_tab_shows_where_who_what_and_what_is_open(self):
        CampaignState.objects.create(
            session=self.session,
            place="the well beneath Farport",
            day=3,
            time_of_day="evening",
        )
        SupplyItem.objects.create(session=self.session, name="torches", amount="2")
        SceneEntity.objects.create(
            session=self.session,
            kind=SceneEntity.THING,
            name="iron box",
            where="a stone ledge",
            condition="resting loose, unfastened",
        )
        SceneEntity.objects.create(
            session=self.session,
            kind=SceneEntity.PERSON,
            name="Mother Vane",
            wants="the box sealed",
        )
        StoryThread.objects.create(
            session=self.session, text="the Sleeper must not wake"
        )

        response = self.client.get(self.url())

        self.assertEqual(response.status_code, 200)
        for expected in (
            "the well beneath Farport",
            "day 3, evening",
            "torches",
            "iron box",
            "resting loose, unfastened",
            "Mother Vane",
            "the box sealed",
            "the Sleeper must not wake",
        ):
            with self.subTest(shown=expected):
                self.assertContains(response, expected)

    def test_a_resolved_thread_is_folded_away_not_dropped(self):
        StoryThread.objects.create(
            session=self.session,
            text="pay the ferryman",
            resolved=True,
            resolution="they paid in silver",
        )

        response = self.client.get(self.url())

        self.assertContains(response, "1 resolved")
        self.assertContains(response, "they paid in silver")

    def test_an_empty_campaign_says_so_rather_than_showing_nothing(self):
        response = self.client.get(self.url())

        self.assertContains(response, "Nobody recorded yet")
        self.assertContains(response, "Nothing recorded yet")
        self.assertContains(response, "Nothing left hanging")

    def test_another_persons_session_is_not_readable(self):
        theirs = GMSession.objects.create(user=self.create_user(), name="theirs")

        self.assertEqual(self.client.get(self.url(theirs)).status_code, 404)

    def test_the_chat_page_carries_the_tab_and_its_refresh_url(self):
        response = self.client.get(f"/tarmar/gm/?session={self.session.pk}")

        self.assertContains(response, 'data-panel-tab="campaign"')
        self.assertContains(response, 'id="gm-panel-campaign"')
        self.assertContains(response, "campaignPanelUrlTemplate")

    def test_the_pane_and_the_state_block_agree(self):
        """The whole point: one set of records, two readers."""
        CampaignState.objects.create(
            session=self.session, place="Farport", day=2, time_of_day="night"
        )
        SceneEntity.objects.create(
            session=self.session,
            kind=SceneEntity.THING,
            name="iron box",
            condition="fused to the rock",
        )

        shown = self.client.get(self.url()).content.decode()
        told = state_block(GAME_MASTER, [], (), CampaignBlock.of(self.session))

        for fact in ("Farport", "day 2, night", "iron box", "fused to the rock"):
            with self.subTest(fact=fact):
                self.assertIn(fact, shown)
                self.assertIn(fact, told)


class SupplyMigrationTest(TransactionTestCase):
    """The free-text supplies line becomes rows, and rows become it again.

    Production carries sessions whose supplies are one sentence, so the
    change has to bring them across and be able to put them back. The
    executor is driven over the real migrations rather than the models, which
    is the only way to see what a rollback would actually do.
    """

    #: Both migrations are in this app; the executor needs the whole plan.
    available_apps = None

    def migrate_to(self, target: str):
        """Run the gm app to ``target`` and return its project state."""
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        state = executor.migrate([("gm", target)])
        executor.loader.build_graph()
        return state

    def tearDown(self):
        """Leave the database where the rest of the suite expects it."""
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        newest = [name for app, name in executor.loader.graph.leaf_nodes("gm")]
        self.migrate_to(newest[0])
        super().tearDown()

    def test_a_supplies_line_splits_into_rows_and_joins_back(self):
        before = self.migrate_to(BEFORE_SUPPLY_ITEMS)
        UserAtThen = before.apps.get_model(settings.AUTH_USER_MODEL)
        GMSessionAtThen = before.apps.get_model("gm", "GMSession")
        CampaignStateAtThen = before.apps.get_model("gm", "CampaignState")
        user = UserAtThen.objects.create(username="carrier")
        session = GMSessionAtThen.objects.create(user=user, name="the well")
        CampaignStateAtThen.objects.create(
            session=session, supplies="3 torches, 40ft rope; waterskin"
        )

        after = self.migrate_to(WITH_SUPPLY_ITEMS)
        SupplyItemAtThen = after.apps.get_model("gm", "SupplyItem")
        carried = [
            (item.name, item.amount)
            for item in SupplyItemAtThen.objects.order_by("created_at", "pk")
        ]

        rolled_back = self.migrate_to(BEFORE_SUPPLY_ITEMS)
        state = rolled_back.apps.get_model("gm", "CampaignState").objects.get()

        # "40ft rope" keeps its measure: only a leading count becomes the
        # amount, because "40ft" is part of what the thing is.
        self.assertEqual(
            carried, [("torches", "3"), ("40ft rope", ""), ("waterskin", "")]
        )
        self.assertEqual(state.supplies, "3 torches, 40ft rope, waterskin")


class TheStateBlockIsBoundedTest(BaseTestCase):
    """Nothing bounded the campaign sections, and the fold never saw them (#647).

    The block is rebuilt whole for both of a turn's model calls and rides in
    the call's *last* message, appended after ``messages_for_model`` has run.
    The context guard only ever drops canon entries from the prefix, so it
    never sees the block; ``should_fold`` measured the history alone. With
    150 recorded things the block alone was larger than the default window,
    and the model server truncated it without a word -- the failure folding
    exists to prevent.
    """

    def setUp(self):
        super().setUp()
        self.session = GMSession.objects.create(user=self.create_user(), name="long")

    def record_people(self, count: int) -> None:
        for index in range(count):
            SceneEntity.objects.create(
                session=self.session,
                kind=SceneEntity.PERSON,
                name=f"Person {index}",
                where="the shrine",
            )

    def test_a_short_section_is_listed_whole(self):
        self.record_people(MAX_CAMPAIGN_LINES_LISTED)

        lines = entity_lines(self.session, SceneEntity.PERSON)

        self.assertEqual(len(lines), MAX_CAMPAIGN_LINES_LISTED)
        self.assertFalse(any("not listed here" in line for line in lines))

    def test_a_long_section_is_capped_and_says_so(self):
        self.record_people(MAX_CAMPAIGN_LINES_LISTED + 30)

        lines = entity_lines(self.session, SceneEntity.PERSON)

        self.assertEqual(len(lines), MAX_CAMPAIGN_LINES_LISTED + 1)
        self.assertIn("and 30 older ones, not listed here", lines[-1])

    def test_the_newest_are_the_ones_kept_in_recorded_order(self):
        self.record_people(MAX_CAMPAIGN_LINES_LISTED + 3)

        lines = entity_lines(self.session, SceneEntity.PERSON)

        self.assertIn("Person 3", lines[0])
        self.assertIn(f"Person {MAX_CAMPAIGN_LINES_LISTED + 2}", lines[-2])
        self.assertNotIn("Person 0", "\n".join(lines))

    def test_every_campaign_section_is_capped(self):
        for index in range(MAX_CAMPAIGN_LINES_LISTED + 5):
            SupplyItem.objects.create(session=self.session, name=f"torch {index}")
            StoryThread.objects.create(session=self.session, text=f"thread {index}")
            SceneEntity.objects.create(
                session=self.session, kind=SceneEntity.THING, name=f"box {index}"
            )
        self.record_people(MAX_CAMPAIGN_LINES_LISTED + 5)

        block = CampaignBlock.of(self.session)

        for name, lines in (
            ("supplies", block.supplies),
            ("people", block.people),
            ("things", block.things),
            ("threads", block.threads),
        ):
            with self.subTest(section=name):
                self.assertEqual(len(lines), MAX_CAMPAIGN_LINES_LISTED + 1)
                self.assertIn("not listed here", lines[-1])

    def test_the_block_stays_inside_the_window_however_much_is_recorded(self):
        """The bound the issue asks for: 150 things no longer overflow it."""
        detail = "x" * SceneEntity.MAX_DETAIL_CHARS
        for index in range(150):
            SceneEntity.objects.create(
                session=self.session,
                kind=SceneEntity.PERSON,
                name=f"Person {index}",
                where=detail,
                wants=detail,
                condition=detail,
                notes=detail,
            )

        self.assertLess(
            state_block_tokens(self.session),
            usable_budget(settings.GM_NUM_CTX) * FOLD_AT_FRACTION,
        )

    def test_the_fold_budget_counts_the_block(self):
        """A history short of the threshold folds once the block is added."""
        history = turns(160, words=60)
        self.record_people(MAX_CAMPAIGN_LINES_LISTED)
        measured = state_block_tokens(self.session)
        num_ctx = (
            int((history_tokens(history) + measured / 2) / FOLD_AT_FRACTION)
            + REPLY_RESERVE_TOKENS
        )

        self.assertFalse(should_fold(history, num_ctx=num_ctx))
        self.assertTrue(should_fold(history, num_ctx=num_ctx, state_tokens=measured))

    def test_a_session_with_nothing_recorded_costs_almost_nothing(self):
        self.assertLess(state_block_tokens(self.session), 20)


class TheRetiredSuppliesColumnIsGoneTest(BaseTestCase):
    """The contract half of #575's expand/contract pair (issue #623).

    Supplies became one row per thing in #579 and ``0018_supply_items`` kept
    the old free-text column so the colour still serving during that deploy
    could still select it. Two releases have shipped since, neither of them
    reading it, so it goes.
    """

    def test_the_record_no_longer_carries_the_retired_line(self):
        names = [field.name for field in CampaignState._meta.get_fields()]

        self.assertNotIn("supplies", names)
        self.assertFalse(hasattr(CampaignState, "MAX_SUPPLIES_CHARS"))

    def test_what_the_party_carries_is_still_recorded_one_row_at_a_time(self):
        session = GMSession.objects.create(user=self.create_user(), name="s")
        SupplyItem.objects.create(session=session, name="torches", amount="3")

        self.assertEqual(
            [item.line for item in SupplyItem.objects.filter(session=session)],
            ["- torches: 3"],
        )

    def test_the_drop_is_listed_as_a_deliberate_contract_step(self):
        """The safety guard refuses a RemoveField that is not (issue #575)."""
        import json
        from pathlib import Path

        baseline = json.loads(
            (
                Path(__file__).resolve().parent.parent
                / "tarmar_studio"
                / "migration_baseline.json"
            ).read_text()
        )

        self.assertIn("gm.0019_drop_retired_supplies_column", baseline["allowed"])
