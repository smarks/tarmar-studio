"""The battle event log renders once, on the server (#504).

battle.js used to rebuild each event in buildEventNode(), mirroring the
markup _arena.html wrote, so a change to either copy left the live log and a
reloaded page saying different things. The page and the advance, restart, act
and poll endpoints now render the same partials (battle/_event.html inside
battle/_turn_group.html) and the browser inserts what it is sent.

What the Jest tests used to pin about buildEventNode (the sequence anchor,
a decision's scored candidates, score_text and its fallback, a death's
fatal-chain links, grouping by turn) is pinned here against the partials.
The insertion itself stays in Jest (appendTurns).
"""

import re
from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.urls import reverse

from battle.access import Actor
from battle.models import BattleEvent
from battle.services import advance_turn, create_battle
from battle.views import (
    LOG_TURN_WINDOW,
    arena_context,
    event_turns,
    rendered_turns,
    windowed_events,
)
from common.test_helpers import BaseTestCase


def make_event(sequence, event_type="phase", message="", payload=None, turn=1):
    return SimpleNamespace(
        turn=turn,
        sequence=sequence,
        event_type=event_type,
        message=message,
        payload=payload or {},
    )


def squash(html: str) -> str:
    return " ".join(html.split())


def render_event(event) -> str:
    return squash(render_to_string("battle/_event.html", {"event": event}))


class EventPartialTest(SimpleTestCase):
    def test_a_roll_shows_its_message_under_its_sequence_anchor(self):
        html = render_event(
            make_event(
                12, "roll", "A attack 1d20: [14] +3 = 17 vs TN 15", {"faces": [14]}
            )
        )
        self.assertTrue(
            html.startswith('<li id="event-12" class="battle-event event-roll">')
        )
        self.assertIn("[14] +3 = 17 vs TN 15", html)
        self.assertNotIn("<details>", html)

    def test_a_decision_expands_into_scored_candidates(self):
        html = render_event(
            make_event(
                5,
                "decision",
                "A chooses ATTACK (j): P(hit) 60%",
                {
                    "candidates": [
                        {
                            "letter": "j",
                            "name": "ATTACK",
                            "score": 3.2,
                            "score_text": "3.200",
                            "rationale": "P(hit) 60%",
                        },
                        {
                            "letter": "k",
                            "name": "DEFEND",
                            "score": 0.5,
                            "score_text": "0.500",
                            "rationale": "+4 TN",
                        },
                    ]
                },
            )
        )
        self.assertIn(
            "<details> <summary>A chooses ATTACK (j): P(hit) 60%</summary> <ul>", html
        )
        rows = re.findall(r"<li> (.*?) </li>", html)
        self.assertEqual(
            rows,
            ["(j) ATTACK — score 3.200: P(hit) 60%", "(k) DEFEND — score 0.500: +4 TN"],
        )

    def test_a_candidate_prints_the_score_its_rationale_was_written_to(self):
        """#301: score_text, not the bare float, sits beside the option."""
        html = render_event(
            make_event(
                6,
                "decision",
                "Forecast — A chooses MOVE (a)",
                {
                    "candidates": [
                        {
                            "letter": "a",
                            "name": "MOVE",
                            "score": 0.5,
                            "score_text": "0.500",
                            "rationale": "within a 7-hex jog, so half of 1.000 = 0.500",
                        }
                    ]
                },
            )
        )
        self.assertIn("score 0.500: within a 7-hex jog", html)
        self.assertNotIn("score 0.5:", html)

    def test_a_candidate_logged_before_score_text_existed_still_renders(self):
        html = render_event(
            make_event(
                7,
                "decision",
                "A chooses ATTACK (j)",
                {
                    "candidates": [
                        {
                            "letter": "j",
                            "name": "ATTACK",
                            "score": 3.2,
                            "rationale": "old",
                        }
                    ]
                },
            )
        )
        self.assertIn("(j) ATTACK — score 3.2: old", html)

    def test_a_death_links_its_fatal_roll_chain_in_order(self):
        html = render_event(
            make_event(90, "death", "B dies", {"fatal_chain": [80, 81, 89]}, turn=2)
        )
        self.assertIn(
            '<span class="fatal-chain">(fatal rolls: <a href="#event-80">#80</a>, '
            '<a href="#event-81">#81</a>, <a href="#event-89">#89</a> ) </span>',
            html,
        )

    def test_a_death_without_a_chain_is_its_message_alone(self):
        for payload in ({}, {"fatal_chain": []}):
            with self.subTest(payload=payload):
                html = render_event(make_event(91, "death", "C dies", payload))
                self.assertEqual(
                    html,
                    '<li id="event-91" class="battle-event event-death"> C dies </li>',
                )

    def test_text_from_the_log_is_escaped(self):
        html = render_event(
            make_event(
                8,
                "decision",
                "<script>x</script> chooses",
                {
                    "candidates": [
                        {
                            "letter": "j",
                            "name": "<b>ATTACK</b>",
                            "score": 1,
                            "rationale": 'a & "b"',
                        }
                    ]
                },
            )
        )
        self.assertNotIn("<script>", html)
        self.assertNotIn("<b>", html)
        self.assertIn("&lt;script&gt;x&lt;/script&gt; chooses", html)
        self.assertIn("a &amp; &quot;b&quot;", html)


class TurnGroupPartialTest(SimpleTestCase):
    def test_event_turns_groups_consecutive_events_by_turn(self):
        events = [
            make_event(1, turn=1),
            make_event(2, turn=1),
            make_event(3, turn=2),
        ]
        groups = event_turns(events)
        self.assertEqual([group["turn"] for group in groups], [1, 2])
        self.assertEqual(
            [[event.sequence for event in group["events"]] for group in groups],
            [[1, 2], [3]],
        )

    def test_a_turn_renders_its_heading_and_its_events_in_order(self):
        turns = rendered_turns(
            [
                make_event(1, message="Phase 1: Initiative", turn=3),
                make_event(2, "roll", "A initiative 1d6: [4] = 4", turn=3),
                make_event(3, message="Phase 1: Initiative", turn=4),
            ]
        )
        self.assertEqual([turn["turn"] for turn in turns], [3, 4])
        html = squash(turns[0]["html"])
        self.assertTrue(
            html.startswith(
                '<section class="turn-group" data-turn="3"> <h3>Turn 3</h3> '
                '<ol class="turn-events">'
            )
        )
        self.assertLess(html.index('id="event-1"'), html.index('id="event-2"'))
        self.assertNotIn('id="event-3"', html)

    def test_no_events_render_no_turns(self):
        self.assertEqual(rendered_turns([]), [])


class EndpointsReturnThePagesMarkupTest(BaseTestCase):
    """The fragments the endpoints send are the page's own log, byte for byte
    apart from whitespace, so an appended turn equals a reloaded one."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.login_as(self.dm)
        self.battle = create_battle(self.dm, name="Logged", count=2, seed=9)

    def page_log(self) -> str:
        response = self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        return squash(response.content.decode())

    def test_advance_returns_the_turn_the_page_renders(self):
        response = self.client.post(reverse("battle:advance", args=[self.battle.pk]))
        data = response.json()
        self.assertEqual([turn["turn"] for turn in data["turns_html"]], [1])
        sent = squash(data["turns_html"][0]["html"])
        self.assertIn("<details>", sent)
        self.assertEqual(sent.count('class="battle-event'), len(data["events"]))
        self.assertIn(sent, self.page_log())

    def test_the_poll_returns_only_turns_after_the_marker(self):
        advance_turn(self.battle.pk)
        marker = BattleEvent.objects.filter(battle=self.battle).latest("sequence")
        advance_turn(self.battle.pk)
        url = reverse("battle:state", args=[self.battle.pk])
        data = self.client.get(url, {"after": marker.sequence}).json()
        self.assertEqual([turn["turn"] for turn in data["turns_html"]], [2])
        self.assertIn(squash(data["turns_html"][0]["html"]), self.page_log())
        self.assertEqual(self.client.get(url).json()["turns_html"], [])

    def test_restart_returns_no_turns(self):
        advance_turn(self.battle.pk)
        response = self.client.post(reverse("battle:restart", args=[self.battle.pk]))
        self.assertEqual(response.json()["turns_html"], [])

    def test_restart_sends_the_empty_log_notice_a_fresh_page_renders(self):
        """#508: the notice restart leaves in the log is the reloaded page's."""
        advance_turn(self.battle.pk)
        response = self.client.post(reverse("battle:restart", args=[self.battle.pk]))
        sent = squash(response.json()["empty_log_html"])
        self.assertEqual(
            sent,
            '<p id="battle-log-empty">No turns run yet. Advance the first turn.</p>',
        )
        page = self.page_log()
        notice = re.search(r'<p id="battle-log-empty">.*?</p>', page)
        if notice is None:
            self.fail("the reloaded page renders no empty-log notice")
        self.assertEqual(sent, notice.group(0))


class LogWindowTest(BaseTestCase):
    """The page opens on the log's last few turns, not all of it (#662).

    ``arena_context`` used to fetch every ``BattleEvent`` row for the battle
    and the template rendered all of them, with no cap, for every viewer on
    every load — spectators included, for whom the replay is meant to cost
    nothing. A plain six-fighter battle run to its natural end wrote 1,834
    events over 83 turns, and a battle may hold twelve combatants.

    What is on screen is a URL rather than a control that forgets itself, so
    a reader who opened the whole log still has it after a reload.
    """

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.login_as(self.dm)
        self.battle = create_battle(self.dm, name="Long", count=2, seed=9)

    def write_turns(self, count: int) -> None:
        """Put ``count`` turns of log on the battle, one event each."""
        BattleEvent.objects.bulk_create(
            BattleEvent(
                battle=self.battle,
                turn=turn,
                phase=1,
                sequence=turn,
                event_type="info",
                actor="",
                payload={},
                message=f"Turn {turn} happened",
            )
            for turn in range(1, count + 1)
        )

    def rendered_turn_numbers(self, context) -> list[int]:
        return [group["turn"] for group in context["event_turns"]]

    def test_a_short_battles_whole_log_is_on_the_page(self):
        self.write_turns(LOG_TURN_WINDOW)
        context = arena_context(self.battle, Actor(user=self.dm, guest_key=""))
        self.assertEqual(len(self.rendered_turn_numbers(context)), LOG_TURN_WINDOW)
        self.assertEqual(context["earlier_turns"], 0)

    def test_a_long_battle_renders_only_the_window(self):
        self.write_turns(LOG_TURN_WINDOW + 25)
        context = arena_context(self.battle, Actor(user=self.dm, guest_key=""))
        self.assertEqual(
            self.rendered_turn_numbers(context),
            list(range(26, LOG_TURN_WINDOW + 26)),
        )
        self.assertEqual(context["earlier_turns"], 25)
        self.assertFalse(context["showing_all_turns"])

    def test_the_withheld_turns_are_never_fetched(self):
        """Capping the render alone would still pull every row out of the
        database for every viewer, which is half of what the issue is."""
        self.write_turns(LOG_TURN_WINDOW + 25)
        events, _earlier = windowed_events(self.battle, show_all=False)
        self.assertEqual(len(events), LOG_TURN_WINDOW)

    def test_asking_for_the_whole_log_gets_it(self):
        self.write_turns(LOG_TURN_WINDOW + 25)
        context = arena_context(
            self.battle, Actor(user=self.dm, guest_key=""), show_all_turns=True
        )
        self.assertEqual(len(self.rendered_turn_numbers(context)), LOG_TURN_WINDOW + 25)
        self.assertEqual(context["earlier_turns"], 25)
        self.assertTrue(context["showing_all_turns"])

    def test_a_battle_with_no_log_at_all_is_not_a_special_case(self):
        events, earlier = windowed_events(self.battle, show_all=False)
        self.assertEqual((events, earlier), ([], 0))

    def test_the_page_offers_the_earlier_turns_and_says_how_many(self):
        self.write_turns(LOG_TURN_WINDOW + 25)
        response = self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        self.assertContains(response, "Show 25 earlier turns")
        self.assertContains(response, "?log=all")
        self.assertNotContains(response, "Turn 1 happened")
        self.assertContains(response, f"Turn {LOG_TURN_WINDOW + 25} happened")

    def test_the_choice_is_a_url_so_a_reload_keeps_it(self):
        self.write_turns(LOG_TURN_WINDOW + 25)
        url = reverse("battle:detail", args=[self.battle.pk])
        response = self.client.get(url, {"log": "all"})
        self.assertContains(response, "Turn 1 happened")
        self.assertContains(response, "Showing every turn")
        # And the way back is offered, so neither end is a trap.
        self.assertContains(response, f"Show only the last {LOG_TURN_WINDOW} turns")
        # A second request with the same URL says the same thing: this is
        # state the reader carries, not a toggle the page forgot.
        self.assertContains(self.client.get(url, {"log": "all"}), "Turn 1 happened")

    def test_a_short_battle_is_offered_no_window_control_at_all(self):
        self.write_turns(3)
        response = self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        self.assertNotContains(response, "earlier turn")
        self.assertNotContains(response, "Showing every turn")

    def test_a_spectator_gets_the_window_too(self):
        """The docstring calls the replay free for spectators; it was the
        most expensive thing they could ask for."""
        self.write_turns(LOG_TURN_WINDOW + 25)
        watcher = self.create_player()
        self.login_as(watcher)
        response = self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        self.assertNotContains(response, "Turn 1 happened")
        self.assertContains(response, "Show 25 earlier turns")
