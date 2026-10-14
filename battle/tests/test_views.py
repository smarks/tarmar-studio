"""Battle views: DM gating, creation form, detail rendering, advance JSON."""

from unittest import mock

from django.urls import reverse

from battle import profiles, views
from battle.access import Actor, detail_visible_ids
from battle.models import Battle, BattleEvent
from battle.services import create_battle
from common.test_helpers import BaseTestCase, logged_exception


class BattleAccessTest(BaseTestCase):
    def test_plain_player_sees_every_battle_and_may_start_one(self):
        """Since #250 players reach the list to find open lobbies and, with
        the spectator flow, every battle to watch. Since #305 they may also
        open one of their own — a guest can, so a player must."""
        dm = self.create_dm()
        create_battle(dm, name="Closed Pit", count=2, seed=1)
        player = self.create_player()
        self.login_as(player)
        response = self.client.get(reverse("battle:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "New battle")
        self.assertContains(response, "Closed Pit")

    def test_plain_player_watches_anothers_started_battle_read_only(self):
        """The spectator flow (#250): the page opens, the controls do not."""
        dm = self.create_dm()
        battle = create_battle(dm, name="Private", count=2, seed=1)
        player = self.create_player()
        self.login_as(player)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Advance turn")

    def test_anonymous_visitors_reach_the_list(self):
        """The arena is playable on arrival (#305) — no account, no gate."""
        response = self.client.get(reverse("battle:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Battle Royale Simulator")
        self.assertContains(response, "New battle")

    def test_dm_sees_the_list(self):
        dm = self.create_dm()
        self.login_as(dm)
        response = self.client.get(reverse("battle:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Battle Royale Simulator")
        self.assertContains(response, "New battle")

    def test_player_may_create_but_not_run_someone_elses_battle(self):
        """Control is ownership, not role, since #305: anyone may open a
        battle, and nobody gains a say over anybody else's."""
        dm = self.create_dm()
        battle = create_battle(dm, name="Gated", count=2, seed=1)
        player = self.create_player()
        self.login_as(player)
        create_response = self.client.post(reverse("battle:setup-create"))
        self.assertEqual(create_response.status_code, 302)
        advance_response = self.client.post(reverse("battle:advance", args=[battle.pk]))
        self.assertEqual(advance_response.status_code, 403)


class BattleDetailTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.login_as(self.dm)
        self.battle = create_battle(self.dm, name="Shown", count=2, seed=9)

    def test_detail_embeds_the_view_model_and_advance_button(self):
        response = self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="battle-view"')
        self.assertContains(response, 'id="advance-turn"')
        self.assertContains(response, "No turns run yet")

    def test_detail_renders_events_grouped_by_turn(self):
        from battle.services import advance_turn

        advance_turn(self.battle.pk)
        response = self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        self.assertContains(response, 'data-turn="1"')
        self.assertContains(response, "Phase 1: Initiative")
        self.assertContains(response, "initiative 1d6")
        self.assertContains(response, "<details>")  # a decision's candidates

    def test_detail_always_records_and_shows_the_dice(self):
        """#301: the dice are in the log, and the toggle only moves them.

        The control is a permanent checkbox, not a disclosure that appears
        when there is something to disclose, and every roll event is
        rendered whatever its position — nothing is dropped at write time.
        """
        from battle.models import BattleEvent
        from battle.services import advance_turn

        advance_turn(self.battle.pk)
        response = self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        self.assertContains(response, 'id="battle-log-dice"')
        self.assertContains(response, "Show every die roll")
        rolls = BattleEvent.objects.filter(battle=self.battle, event_type="roll")
        self.assertTrue(rolls.exists())
        for event in rolls:
            self.assertContains(response, event.message.split(":")[0])

    def test_detail_prints_a_candidate_score_at_its_rationale_width(self):
        """#301: the score beside an option matches the rationale's total."""
        from battle.models import BattleEvent
        from battle.services import advance_turn

        advance_turn(self.battle.pk)
        response = self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        decisions = list(
            BattleEvent.objects.filter(battle=self.battle, event_type="decision")
        )
        self.assertTrue(decisions)
        decision = decisions[0]
        self.assertTrue(decision.message.startswith("Forecast — "))
        for candidate in decision.payload["candidates"]:
            self.assertContains(
                response, f"score {candidate['score_text']}:", html=False
            )
            if "=" in candidate["rationale"]:
                self.assertTrue(
                    candidate["rationale"].endswith(candidate["score_text"]),
                    f"({candidate['letter']}) {candidate['rationale']!r} does not "
                    f"end in its own score {candidate['score_text']}",
                )

    def test_finished_battle_disables_the_button(self):
        self.battle.status = Battle.STATUS_FINISHED
        self.battle.save()
        response = self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        self.assertContains(response, "disabled")


class BattleAdvanceTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.login_as(self.dm)
        self.battle = create_battle(self.dm, name="Stepped", count=2, seed=11)

    def test_advance_returns_events_and_the_view(self):
        response = self.client.post(reverse("battle:advance", args=[self.battle.pk]))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["turn"], 1)
        self.assertIn(data["status"], ("paused", "finished"))
        self.assertEqual(len(data["view"]["combatants"]), 2)
        event_types = {event["event_type"] for event in data["events"]}
        self.assertIn("phase", event_types)
        self.assertIn("roll", event_types)
        self.assertIn("decision", event_types)
        roll_events = [
            event for event in data["events"] if event["event_type"] == "roll"
        ]
        payload = roll_events[0]["payload"]
        self.assertEqual(sum(payload["faces"]), payload["total"] - payload["modifier"])

    def _advance_failing_with(self, battle: Battle, secret: str):
        """Advance ``battle`` while the engine raises an error carrying ``secret``.

        Returns:
            The response and the records the battle views logged.
        """
        with (
            mock.patch("battle.views.advance_turn", side_effect=RuntimeError(secret)),
            self.assertLogs("battle.views", "ERROR") as logs,
        ):
            response = self.client.post(reverse("battle:advance", args=[battle.pk]))
        return response, logs.records

    def test_advance_error_answers_json_without_the_exception_text(self):
        # A failing engine turn still answers JSON, not an opaque HTML 500
        # (#184), but with a fixed sentence: the exception's text stays in
        # the log, with its traceback (#13).
        secret = "DATABASE_PASSWORD=hunter2 in /srv/tarmar/state_json.py"

        response, records = self._advance_failing_with(self.battle, secret)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"], views.ADVANCE_FAILED_MESSAGE)
        self.assertNotIn("hunter2", response.content.decode())
        self.assertNotIn("RuntimeError", response.content.decode())
        self.assertIn(secret, str(logged_exception(records[0])))

    def test_a_player_running_a_battle_is_not_shown_the_exception_text(self):
        """Players run battles of their own (#305): the leak was theirs (#13)."""
        player = self.create_player()
        self.login_as(player)
        own_battle = create_battle(player, name="Own", count=2, seed=12)
        secret = "SELECT secret_token FROM accounts_user"

        response, records = self._advance_failing_with(own_battle, secret)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"], views.ADVANCE_FAILED_MESSAGE)
        self.assertNotIn("secret_token", response.content.decode())
        self.assertIn(secret, str(logged_exception(records[0])))

    def test_advance_requires_post(self):
        response = self.client.get(reverse("battle:advance", args=[self.battle.pk]))
        self.assertEqual(response.status_code, 405)

    def test_advance_persists_the_same_events_it_returns(self):
        response = self.client.post(reverse("battle:advance", args=[self.battle.pk]))
        returned = [event["sequence"] for event in response.json()["events"]]
        stored = list(
            BattleEvent.objects.filter(battle=self.battle).values_list(
                "sequence", flat=True
            )
        )
        self.assertEqual(returned, stored)

    def test_unknown_battle_404s(self):
        response = self.client.post(reverse("battle:advance", args=[99999]))
        self.assertEqual(response.status_code, 404)


class BeastSetupFlowTest(BaseTestCase):
    """Beasts join through the setup flow (#271) — the muster's beast
    sources compile into the same creation the old dense form drove."""

    def setUp(self):
        super().setUp()
        from battle.models import BattleDraft

        from .test_services import make_beast

        self.dm = self.create_dm()
        self.login_as(self.dm)
        self.wolf = make_beast()
        self.bear = make_beast(
            slug="cave-bear", name="Cave Bear", strength=30, size_hexes=3
        )
        self.draft = BattleDraft.objects.create(created_by=self.dm)

    def add(self, **fields):
        return self.client.post(
            reverse("battle:setup-add", args=[self.draft.pk]), fields
        )

    def start(self, name, **terms):
        return self.client.post(
            reverse("battle:setup-start", args=[self.draft.pk]),
            {"name": name, **terms},
        )

    def test_setup_page_offers_the_beast_sources(self):
        response = self.client.get(reverse("battle:setup", args=[self.draft.pk]))
        self.assertContains(response, "Test Wolf")
        self.assertContains(response, 'name="slug"')
        self.assertContains(response, 'name="difficulty"')

    def test_create_with_specific_beasts(self):
        self.add(kind="fighter", archetype="archer")
        self.add(kind="fighter", archetype="wizard")
        self.add(kind="beast", slug="test-wolf")
        self.add(kind="beast", slug="cave-bear")
        response = self.start("Wolf Pit", seed="7")
        battle = Battle.objects.get(name="Wolf Pit")
        self.assertRedirects(response, reverse("battle:detail", args=[battle.pk]))
        self.assertEqual(battle.combatants.count(), 4)
        self.assertEqual(battle.combatants.exclude(beast=None).count(), 2)

    def test_create_with_random_beasts_by_category(self):
        self.add(kind="fighter", archetype="archer")
        self.add(kind="fighter", archetype="wizard")
        self.add(kind="random_beast", category="mammals")
        self.start("Random Pit", seed="7")
        battle = Battle.objects.get(name="Random Pit")
        beast_rows = battle.combatants.exclude(beast=None)
        self.assertEqual(beast_rows.count(), 1)
        self.assertEqual(beast_rows.first().beast.category, "mammals")

    def test_beasts_only_battle_is_allowed(self):
        self.add(kind="beast", slug="test-wolf")
        self.add(kind="beast", slug="cave-bear")
        self.start("Beasts Only", seed="7")
        battle = Battle.objects.get(name="Beasts Only")
        self.assertEqual(battle.combatants.count(), 2)
        self.assertEqual(battle.combatants.exclude(beast=None).count(), 2)

    def test_unknown_beast_slug_is_refused_at_the_add(self):
        response = self.add(kind="beast", slug="no-such-beast")
        self.assertEqual(response.status_code, 400)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.roster_json, [])

    def test_unknown_difficulty_band_is_refused_at_the_add(self):
        response = self.add(kind="random_beast", difficulty="impossible")
        self.assertEqual(response.status_code, 400)

    def test_too_small_total_is_refused_at_the_start(self):
        self.add(kind="beast", slug="test-wolf")
        response = self.start("Tiny Pit")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Battle.objects.filter(name="Tiny Pit").exists())

    def test_detail_page_shows_the_beast_roster(self):
        battle = create_battle(
            self.dm,
            name="Shown Pit",
            count=2,
            seed=9,
            beast_slugs=("test-wolf",),
        )
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Shown Pit")


class BattleRenameTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.battle = create_battle(self.dm, name="Renamable", count=2, seed=3)
        self.row = self.battle.combatants.first()
        self.login_as(self.dm)

    def rename(self, payload):
        return self.client.post(
            reverse("battle:rename", args=[self.battle.pk]),
            payload,
            content_type="application/json",
        )

    def test_rename_requires_post(self):
        response = self.client.get(reverse("battle:rename", args=[self.battle.pk]))
        self.assertEqual(response.status_code, 405)

    def test_player_is_forbidden(self):
        player = self.create_player()
        self.logout()  # switch users without dropping the passphrase gate
        self.login_as(player)
        response = self.rename({"combatant_id": self.row.pk, "name": "Sneaky Rename"})
        self.assertEqual(response.status_code, 403)

    def test_anonymous_cannot_rename_in_a_dms_battle(self):
        """404, not 403: a battle holding a DM's private characters is not
        even readable to an anonymous visitor (#273 carried into #305)."""
        self.logout()
        response = self.rename({"combatant_id": self.row.pk, "name": "Ghost"})
        self.assertEqual(response.status_code, 404)

    def test_unknown_battle_404s(self):
        response = self.client.post(
            reverse("battle:rename", args=[99999]),
            {"combatant_id": 1, "name": "Nobody"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_rename_persists_and_returns_the_new_state(self):
        response = self.rename(
            {"combatant_id": self.row.pk, "name": "Torin Blackbriar"}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["name"], "Torin Blackbriar")
        self.row.refresh_from_db()
        self.assertEqual(self.row.display_name, "Torin Blackbriar")
        view_names = [combatant["name"] for combatant in data["view"]["combatants"]]
        self.assertIn("Torin Blackbriar", view_names)
        self.battle.refresh_from_db()
        stored_names = [
            combatant["name"] for combatant in self.battle.state_json["combatants"]
        ]
        self.assertIn("Torin Blackbriar", stored_names)

    def test_blank_name_is_rejected(self):
        response = self.rename({"combatant_id": self.row.pk, "name": "   "})
        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.json()["error"].lower())

    def test_overlong_name_is_rejected(self):
        response = self.rename({"combatant_id": self.row.pk, "name": "N" * 121})
        self.assertEqual(response.status_code, 400)

    def test_invalid_characters_are_rejected(self):
        response = self.rename({"combatant_id": self.row.pk, "name": "Bad<script>Name"})
        self.assertEqual(response.status_code, 400)

    def test_duplicate_name_is_rejected(self):
        other = self.battle.combatants.exclude(pk=self.row.pk).first()
        response = self.rename(
            {"combatant_id": self.row.pk, "name": other.display_name}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("already", response.json()["error"].lower())

    def test_unknown_combatant_is_rejected(self):
        response = self.rename({"combatant_id": 424242, "name": "Nobody Home"})
        self.assertEqual(response.status_code, 400)

    def test_non_numeric_combatant_id_is_rejected(self):
        response = self.rename({"combatant_id": "abc", "name": "Nobody Home"})
        self.assertEqual(response.status_code, 400)


class BattleRestartViewTest(BaseTestCase):
    """POST restart rewinds the battle and returns the fresh state (issue #188)."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.login_as(self.dm)
        self.battle = create_battle(self.dm, name="Rewind", count=2, seed=5)

    def test_restart_returns_the_starting_view_and_clears_events(self):
        self.client.post(reverse("battle:advance", args=[self.battle.pk]))
        response = self.client.post(reverse("battle:restart", args=[self.battle.pk]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["turn"], 0)
        self.assertEqual(payload["status"], Battle.STATUS_SETUP)
        self.assertEqual(payload["events"], [])
        # The response draws the rewound battle: turn 0, the full roster back
        # on its feet — the view the page would get on a fresh load.
        self.battle.refresh_from_db()
        # The same view the page would render for this viewer — the DM, so
        # every combatant's attribute panel is in it (#302).
        self.assertEqual(
            payload["view"],
            profiles.view_of(
                self.battle,
                detail_ids=detail_visible_ids(
                    Actor(user=self.dm, guest_key=""), self.battle
                ),
            ),
        )
        self.assertEqual(payload["view"]["turn"], 0)
        self.assertFalse(BattleEvent.objects.filter(battle=self.battle).exists())

    def test_restart_without_a_snapshot_is_a_400(self):
        Battle.objects.filter(pk=self.battle.pk).update(initial_state_json={})
        response = self.client.post(reverse("battle:restart", args=[self.battle.pk]))
        self.assertEqual(response.status_code, 400)
        self.assertIn("no starting snapshot", response.json()["error"])

    def test_restart_of_a_missing_battle_is_a_404(self):
        response = self.client.post(reverse("battle:restart", args=[999999]))
        self.assertEqual(response.status_code, 404)

    def test_player_cannot_restart(self):
        player = self.create_player()
        # logout() first: login_as cannot swap an already-authenticated
        # session, and the helper keeps the passphrase gate satisfied.
        self.logout()
        self.login_as(player)
        response = self.client.post(reverse("battle:restart", args=[self.battle.pk]))
        self.assertEqual(response.status_code, 403)

    def test_get_is_not_allowed(self):
        response = self.client.get(reverse("battle:restart", args=[self.battle.pk]))
        self.assertEqual(response.status_code, 405)


class BattleDetailChromeTest(BaseTestCase):
    """The redesigned detail page: wide main, command bar, separated actions."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.login_as(self.dm)
        self.battle = create_battle(self.dm, name="Chrome", count=2, seed=3)

    def test_detail_page_carries_the_command_bar_and_all_three_actions(self):
        response = self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        self.assertContains(response, "battle-main")
        self.assertContains(response, "battle-command-bar")
        self.assertContains(response, 'id="advance-turn"')
        self.assertContains(response, 'id="restart-battle"')
        self.assertContains(response, reverse("battle:list"))

    def test_restart_is_disabled_without_a_starting_snapshot(self):
        Battle.objects.filter(pk=self.battle.pk).update(initial_state_json={})
        response = self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        self.assertContains(response, "no starting snapshot")
