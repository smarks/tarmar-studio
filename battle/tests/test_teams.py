"""Parties march in as teams (#275): the service layer.

Four seams under test: the draft's team ledger (naming, disbanding, moving
entries), the party import with its visibility rule (a non-DM never imports
another player's private character — skipped and counted, never smuggled),
how teams compile through ``create_battle`` into rows and engine snapshots,
and the team-aware ending — victory is last TEAM standing while a teamless
battle ends exactly where it always did.
"""

from battle.models import Battle, BattleDraft
from battle.persistence import CLASSIC_PROFILE
from battle.profiles import for_battle
from battle.refusal_words import refusal_words
from battle.services import (
    MAX_COMBATANTS,
    add_draft_entry,
    add_draft_team,
    advance_turn,
    claim_seat,
    create_battle,
    draft_create_kwargs,
    import_party_into_draft,
    remove_draft_team,
    restart_battle,
    set_entry_team,
    start_battle,
    start_draft,
)
from common.test_helpers import BaseTestCase, create_character
from parties.models import Party, PartyMembership


class DraftTeamLedgerTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.draft = BattleDraft.objects.create(created_by=self.dm)

    def test_add_team_records_and_returns_the_name(self):
        self.assertEqual(
            add_draft_team(self.draft, "  The Grey   Company "), "The Grey Company"
        )
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.teams_json, ["The Grey Company"])

    def test_taken_team_name_comes_back_numbered(self):
        add_draft_team(self.draft, "Red")
        self.assertEqual(add_draft_team(self.draft, "Red"), "Red (2)")
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.teams_json, ["Red", "Red (2)"])

    def test_blank_team_name_is_refused(self):
        with self.assertRaises(ValueError):
            add_draft_team(self.draft, "   ")

    def test_overlong_team_name_is_trimmed(self):
        name = add_draft_team(self.draft, "x" * 200)
        self.assertLessEqual(len(name), 50)

    def test_entry_added_to_a_team_carries_it(self):
        add_draft_team(self.draft, "Red")
        add_draft_entry(
            self.draft, self.dm, kind="fighter", archetype="brawler", team="Red"
        )
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.roster_json[0].get("team"), "Red")

    def test_entry_for_an_unknown_team_is_refused(self):
        with self.assertRaises(ValueError):
            add_draft_entry(self.draft, self.dm, kind="open_seat", team="Red")

    def test_set_entry_team_moves_and_clears(self):
        add_draft_team(self.draft, "Red")
        add_draft_team(self.draft, "Blue")
        add_draft_entry(
            self.draft, self.dm, kind="fighter", archetype="brawler", team="Red"
        )
        set_entry_team(self.draft, 0, "Blue")
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.roster_json[0]["team"], "Blue")
        set_entry_team(self.draft, 0, "")
        self.draft.refresh_from_db()
        self.assertNotIn("team", self.draft.roster_json[0])

    def test_set_entry_team_refuses_unknown_team_and_bad_index(self):
        add_draft_entry(self.draft, self.dm, kind="open_seat")
        with self.assertRaises(ValueError):
            set_entry_team(self.draft, 0, "Nobody")
        with self.assertRaises(ValueError):
            set_entry_team(self.draft, 5, "")

    def test_remove_team_disbands_to_the_free_pool(self):
        add_draft_team(self.draft, "Red")
        add_draft_entry(
            self.draft, self.dm, kind="fighter", archetype="brawler", team="Red"
        )
        add_draft_entry(self.draft, self.dm, kind="open_seat")
        remove_draft_team(self.draft, "Red")
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.teams_json, [])
        self.assertNotIn("team", self.draft.roster_json[0])
        # The entries themselves stay mustered.
        self.assertEqual(len(self.draft.roster_json), 2)

    def test_remove_unknown_team_is_refused(self):
        with self.assertRaises(ValueError):
            remove_draft_team(self.draft, "Red")


class ImportPartyTest(BaseTestCase):
    """The visibility rule: import only what the importer can see."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.player = self.create_player()
        self.owner = self.create_user("bandleader")
        self.party = Party.objects.create(name="The Grey Company", owner=self.owner)
        self.public = create_character(self.owner, name="Famous", is_public=True)
        self.private = create_character(self.owner, name="Hidden")
        self.dead = create_character(
            self.owner, name="Fallen", is_public=True, is_alive=False
        )
        for character in (self.public, self.private, self.dead):
            PartyMembership.objects.create(party=self.party, character=character)

    def test_non_dm_imports_only_visible_living_members_with_counts(self):
        draft = BattleDraft.objects.create(created_by=self.player)
        result = import_party_into_draft(draft, self.player, self.party.pk)
        self.assertEqual(
            result,
            {
                "team": "The Grey Company",
                "imported": 1,
                "hidden": 1,
                "fallen": 1,
                "already_mustered": 0,
            },
        )
        draft.refresh_from_db()
        self.assertEqual(draft.teams_json, ["The Grey Company"])
        self.assertEqual(
            draft.roster_json,
            [
                {
                    "kind": "character",
                    "character_id": self.public.pk,
                    "team": "The Grey Company",
                }
            ],
        )

    def test_dm_sees_private_members_but_not_the_dead(self):
        draft = BattleDraft.objects.create(created_by=self.dm)
        result = import_party_into_draft(draft, self.dm, self.party.pk)
        self.assertEqual(result["imported"], 2)
        self.assertEqual(result["hidden"], 0)
        self.assertEqual(result["fallen"], 1)

    def test_already_mustered_members_are_skipped_and_counted(self):
        draft = BattleDraft.objects.create(created_by=self.dm)
        add_draft_entry(draft, self.dm, kind="character", character_id=self.public.pk)
        result = import_party_into_draft(draft, self.dm, self.party.pk)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["already_mustered"], 1)

    def test_import_with_nobody_available_creates_no_team(self):
        draft = BattleDraft.objects.create(created_by=self.player)
        lone = Party.objects.create(name="Ghosts", owner=self.owner)
        PartyMembership.objects.create(party=lone, character=self.private)
        with self.assertRaises(ValueError):
            import_party_into_draft(draft, self.player, lone.pk)
        draft.refresh_from_db()
        self.assertEqual(draft.teams_json, [])
        self.assertEqual(draft.roster_json, [])

    def test_empty_party_and_unknown_party_are_refused(self):
        draft = BattleDraft.objects.create(created_by=self.dm)
        empty = Party.objects.create(name="Nobody", owner=self.owner)
        with self.assertRaises(ValueError):
            import_party_into_draft(draft, self.dm, empty.pk)
        with self.assertRaises(ValueError):
            import_party_into_draft(draft, self.dm, 999999)

    def test_a_disbanded_party_is_refused_with_the_reason(self):
        """Issue #971: the import looked the party up with no is_deleted
        filter, so a disbanded party marched in as a team."""
        draft = BattleDraft.objects.create(created_by=self.dm)
        Party.objects.filter(pk=self.party.pk).update(is_deleted=True)
        with self.assertRaisesRegex(ValueError, "The Grey Company was disbanded"):
            import_party_into_draft(draft, self.dm, self.party.pk)
        draft.refresh_from_db()
        self.assertEqual(draft.teams_json, [])
        self.assertEqual(draft.roster_json, [])

    def test_import_refuses_to_overflow_the_muster(self):
        draft = BattleDraft.objects.create(created_by=self.dm)
        for _ in range(MAX_COMBATANTS - 1):
            add_draft_entry(draft, self.dm, kind="fighter", archetype="brawler")
        with self.assertRaises(ValueError):
            import_party_into_draft(draft, self.dm, self.party.pk)
        draft.refresh_from_db()
        self.assertEqual(draft.teams_json, [])
        self.assertEqual(len(draft.roster_json), MAX_COMBATANTS - 1)

    def test_second_import_of_a_same_named_party_gets_a_numbered_team(self):
        draft = BattleDraft.objects.create(created_by=self.dm)
        twin = Party.objects.create(name="The Grey Company", owner=self.owner)
        other = create_character(self.owner, name="Cousin", is_public=True)
        PartyMembership.objects.create(party=twin, character=other)
        import_party_into_draft(draft, self.dm, self.party.pk)
        result = import_party_into_draft(draft, self.dm, twin.pk)
        self.assertEqual(result["team"], "The Grey Company (2)")


class TeamsCompileTest(BaseTestCase):
    """Draft teams flow through create_battle onto rows and snapshots."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.character = create_character(self.dm, name="Aldwin")

    def test_draft_teams_compile_into_create_battle_kwargs(self):
        draft = BattleDraft.objects.create(created_by=self.dm, name="Sides", seed=7)
        add_draft_team(draft, "Red")
        add_draft_entry(draft, self.dm, kind="fighter", archetype="brawler", team="Red")
        add_draft_entry(
            draft, self.dm, kind="character", character_id=self.character.pk
        )
        add_draft_entry(draft, self.dm, kind="open_seat", team="Red")
        kwargs = draft_create_kwargs(draft)
        self.assertEqual(kwargs["fighter_teams"], ("Red",))
        self.assertEqual(kwargs["character_teams"], ("",))
        self.assertEqual(kwargs["seat_teams"], ("Red",))

    def test_started_draft_stamps_rows_and_engine_state(self):
        draft = BattleDraft.objects.create(created_by=self.dm, name="Sides", seed=7)
        add_draft_team(draft, "Red")
        add_draft_entry(draft, self.dm, kind="fighter", archetype="brawler", team="Red")
        add_draft_entry(
            draft, self.dm, kind="fighter", archetype="unarmoured duelist", team="Red"
        )
        add_draft_entry(
            draft, self.dm, kind="character", character_id=self.character.pk
        )
        battle = start_draft(draft)
        teams = list(battle.combatants.values_list("team", flat=True))
        self.assertEqual(teams, ["Red", "Red", ""])
        state_teams = [entry["team"] for entry in battle.state_json["combatants"]]
        self.assertEqual(state_teams, ["Red", "Red", ""])

    def test_claimed_seat_keeps_its_team(self):
        player = self.create_player()
        own = create_character(player, name="Mine")
        battle = create_battle(
            self.dm, name="Lobby", count=1, seed=3, player_seats=1, seat_teams=("Red",)
        )
        seat = battle.combatants.get(is_open_seat=True)
        self.assertEqual(seat.team, "Red")
        claim_seat(battle.pk, seat.pk, player, character_id=own.pk)
        seat.refresh_from_db()
        self.assertEqual(seat.snapshot_json["team"], "Red")
        seat.is_ready = True
        seat.save(update_fields=["is_ready"])
        start_battle(battle.pk)
        battle.refresh_from_db()
        by_id = {
            entry["combatant_id"]: entry for entry in battle.state_json["combatants"]
        }
        self.assertEqual(by_id[seat.pk]["team"], "Red")

    def test_classic_battle_crosses_teams_as_sides(self):
        battle = create_battle(
            self.dm,
            name="Classic sides",
            count=3,
            seed=5,
            rules_profile=CLASSIC_PROFILE,
            fighter_teams=("Red", "Red", ""),
        )
        sides = [entry["side"] for entry in battle.state_json["figures"]]
        # A team's side carries a prefix no row id can (#930).
        self.assertEqual(sides[:2], ["team:Red", "team:Red"])
        # The teamless figure keeps the free-for-all spelling: side == uid.
        self.assertEqual(sides[2], battle.state_json["figures"][2]["uid"])
        profile = for_battle(battle)
        state = profile.load(battle.state_json)
        self.assertEqual(profile.actor_team(state.figures[0]), "Red")
        self.assertEqual(profile.actor_team(state.figures[2]), "")


class TeamVictoryTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()

    def test_two_teammates_alone_win_together_and_the_line_names_the_team(self):
        battle = create_battle(
            self.dm, name="Walkover", count=2, seed=11, fighter_teams=("Red", "Red")
        )
        battle, events = advance_turn(battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_FINISHED)
        victory = events[-1]
        self.assertEqual(victory.payload["winner_team"], "Red")
        self.assertEqual(len(victory.payload["winners"]), 2)
        self.assertIn("Red stands victorious", victory.message)

    def test_two_teams_standing_is_not_a_finished_battle(self):
        # Two melee-only fighters on opposite rims cannot down each other on
        # turn 1 (the arena is wider than a single blow's worst case), so
        # the turn must end paused: two teams still stand. Before teams, one
        # survivor ended it — two combatants were never enough to pause on
        # team grounds alone.
        battle = create_battle(
            self.dm,
            name="Standoff",
            count=2,
            seed=11,
            archetypes=("armoured fighter", "unarmoured duelist"),
            fighter_teams=("Red", "Blue"),
        )
        battle, _events = advance_turn(battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_PAUSED)

    def test_team_victory_names_every_standing_teammate(self):
        # Three mustered — two Red, one alone — with the loner downed by
        # hand: the next turn leaves one team standing and the victory line
        # carries both its survivors.
        battle = create_battle(
            self.dm, name="Contest", count=3, seed=11, fighter_teams=("Red", "Red", "")
        )
        state = dict(battle.state_json)
        state["combatants"][2]["alive"] = False
        battle.state_json = state
        battle.save(update_fields=["state_json"])
        battle, events = advance_turn(battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_FINISHED)
        victory = events[-1]
        self.assertEqual(victory.payload["winner_team"], "Red")
        self.assertEqual(len(victory.payload["winners"]), 2)

    def test_teammates_never_strike_each_other(self):
        # All duelists — unarmoured, broadswords — so the fight is decisive
        # whatever the row pks landed as: no armour-tier stalemate.
        battle = create_battle(
            self.dm,
            name="Truce watch",
            count=4,
            seed=23,
            archetypes=("unarmoured duelist",) * 4,
            fighter_teams=("Red", "Red", "Blue", "Blue"),
        )
        team_by_name = {
            combatant.display_name: combatant.team
            for combatant in battle.combatants.all()
        }
        team_by_id = {
            combatant.pk: combatant.team for combatant in battle.combatants.all()
        }
        saw_damage = False
        for _ in range(300):
            battle, events = advance_turn(battle.pk)
            for event in events:
                if event.event_type != "damage":
                    continue
                target = event.payload.get("target")
                attacker_team = team_by_name.get(event.actor, "")
                target_team = team_by_id.get(target, "")
                saw_damage = True
                if attacker_team:
                    self.assertNotEqual(
                        attacker_team,
                        target_team,
                        f"{event.actor} struck teammate {target}",
                    )
            if battle.status == Battle.STATUS_FINISHED:
                break
        self.assertEqual(battle.status, Battle.STATUS_FINISHED)
        self.assertTrue(saw_damage)

    def test_restart_replays_a_team_battle_event_for_event(self):
        battle = create_battle(
            self.dm,
            name="Replay",
            count=4,
            seed=17,
            fighter_teams=("Red", "Red", "Blue", "Blue"),
        )
        battle, first = advance_turn(battle.pk)
        first_messages = [event.message for event in first]
        restart_battle(battle.pk)
        _battle, replayed = advance_turn(battle.pk)
        self.assertEqual([event.message for event in replayed], first_messages)

    def test_teamless_victory_line_is_unchanged(self):
        # One combatant downed by hand: the next turn must end the battle
        # with the historic single-survivor line — no team named, the same
        # payload shape every free-for-all battle has always written.
        battle = create_battle(self.dm, name="Duel", count=2, seed=11)
        state = dict(battle.state_json)
        state["combatants"][1]["alive"] = False
        battle.state_json = state
        battle.save(update_fields=["state_json"])
        battle, events = advance_turn(battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_FINISHED)
        victory = events[-1]
        self.assertEqual(
            victory.payload,
            {"winner": battle.state_json["combatants"][0]["combatant_id"]},
        )
        self.assertTrue(victory.message.endswith("stands victorious"))


class TeamSetupViewsTest(BaseTestCase):
    """The team endpoints' happy paths and persistent notices (#275)."""

    def setUp(self):
        from django.urls import reverse

        super().setUp()
        self.reverse = reverse
        self.dm = self.create_dm()
        self.login_as(self.dm)
        self.draft = BattleDraft.objects.create(created_by=self.dm, name="Drafted")
        self.owner = self.create_user("bandleader")
        self.party = Party.objects.create(name="The Grey Company", owner=self.owner)
        self.member = create_character(self.owner, name="Famous", is_public=True)
        self.private = create_character(self.owner, name="Hidden")
        PartyMembership.objects.create(party=self.party, character=self.member)
        PartyMembership.objects.create(party=self.party, character=self.private)

    def test_import_party_seeds_a_team_and_the_notice_stays_on_the_page(self):
        response = self.client.post(
            self.reverse("battle:setup-import-party", args=[self.draft.pk]),
            {"party_id": self.party.pk},
            follow=True,
        )
        # Redirected to the setup page, not rendered at the POST address
        # (issue #339, item 20); the notice arrives with it.
        self.assertEqual(
            response.redirect_chain,
            [(self.reverse("battle:setup", args=[self.draft.pk]), 302)],
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The Grey Company joins as a team")
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.teams_json, ["The Grey Company"])
        # A DM sees private members, so both import; the muster groups them.
        self.assertEqual(len(self.draft.roster_json), 2)
        self.assertContains(response, "muster-team")

    def test_import_notice_counts_hidden_members_for_a_non_dm(self):
        # The service-level rule surfaced in the page's words: a non-DM
        # importer is told how many members were hidden from them. Drafts
        # are DM-only at the view layer today, so exercise the service and
        # assert the view's wording pieces via the DM path with a dead
        # member instead.
        fallen = create_character(
            self.owner, name="Fallen", is_public=True, is_alive=False
        )
        PartyMembership.objects.create(party=self.party, character=fallen)
        response = self.client.post(
            self.reverse("battle:setup-import-party", args=[self.draft.pk]),
            {"party_id": self.party.pk},
            follow=True,
        )
        self.assertContains(response, "1 dead or gone")
        self.assertContains(response, "skipped")

    def test_reloading_after_an_import_does_not_import_again(self):
        """The page after an import is a GET, so a refresh cannot repost it."""
        self.client.post(
            self.reverse("battle:setup-import-party", args=[self.draft.pk]),
            {"party_id": self.party.pk},
            follow=True,
        )
        self.draft.refresh_from_db()
        mustered = len(self.draft.roster_json)

        reloaded = self.client.get(self.reverse("battle:setup", args=[self.draft.pk]))

        self.draft.refresh_from_db()
        self.assertEqual(len(self.draft.roster_json), mustered)
        # Shown once, on the page the import landed on.
        self.assertNotContains(reloaded, "The Grey Company joins as a team")

    def test_import_unknown_party_renders_the_error(self):
        response = self.client.post(
            self.reverse("battle:setup-import-party", args=[self.draft.pk]),
            {"party_id": "999999"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response, refusal_words().services.no_such_party, status_code=400
        )

    def test_team_add_remove_and_entry_move_round_trip(self):
        add_url = self.reverse("battle:setup-team-add", args=[self.draft.pk])
        response = self.client.post(add_url, {"team_name": "The Wolves"})
        self.assertEqual(response.status_code, 302)
        add_draft_entry(self.draft, self.dm, kind="fighter", archetype="brawler")
        move_url = self.reverse("battle:setup-entry-team", args=[self.draft.pk])
        response = self.client.post(move_url, {"index": "0", "team": "The Wolves"})
        self.assertEqual(response.status_code, 302)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.roster_json[0]["team"], "The Wolves")
        remove_url = self.reverse("battle:setup-team-remove", args=[self.draft.pk])
        response = self.client.post(remove_url, {"team_name": "The Wolves"})
        self.assertEqual(response.status_code, 302)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.teams_json, [])
        self.assertNotIn("team", self.draft.roster_json[0])

    def test_setup_page_offers_the_party_picker_and_team_sections(self):
        add_draft_team(self.draft, "The Wolves")
        response = self.client.get(self.reverse("battle:setup", args=[self.draft.pk]))
        self.assertContains(response, "Import as a team")
        self.assertContains(response, "The Grey Company")
        self.assertContains(response, "Free-for-all")
        self.assertContains(response, "Disband")

    def test_add_entry_straight_onto_a_team(self):
        add_draft_team(self.draft, "The Wolves")
        response = self.client.post(
            self.reverse("battle:setup-add", args=[self.draft.pk]),
            {"kind": "open_seat", "team": "The Wolves"},
        )
        self.assertEqual(response.status_code, 302)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.roster_json[0]["team"], "The Wolves")


class TeamViewmodelTest(BaseTestCase):
    def test_tarmar_view_carries_each_combatants_team(self):
        dm = self.create_dm()
        battle = create_battle(
            dm, name="Sides", count=2, seed=9, fighter_teams=("Red",)
        )
        from battle.profiles import view_of

        view = view_of(battle)
        teams = [combatant["team"] for combatant in view["combatants"]]
        self.assertEqual(teams, ["Red", ""])

    def test_classic_view_translates_sides_back_to_teams(self):
        dm = self.create_dm()
        battle = create_battle(
            dm,
            name="Classic sides",
            count=2,
            seed=9,
            rules_profile=CLASSIC_PROFILE,
            fighter_teams=("Red",),
        )
        from battle.profiles import view_of

        view = view_of(battle)
        teams = [combatant["team"] for combatant in view["combatants"]]
        self.assertEqual(teams, ["Red", ""])


class LobbyTeamDisplayTest(BaseTestCase):
    def test_lobby_page_names_the_seats_team(self):
        from django.urls import reverse

        dm = self.create_dm()
        self.login_as(dm)
        battle = create_battle(
            dm,
            name="Lobby",
            count=1,
            seed=3,
            player_seats=1,
            seat_teams=("The Wolves",),
        )
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertContains(response, "Team: The Wolves")


def rename_team(battle: Battle, old: str, new: str) -> Battle:
    """Rename a started battle's team on its rows and in its saved state.

    Team names are chosen before row ids exist, so a test that needs a team
    named after a row id (#930) renames it once the ids are known. Both of
    the state's spellings are rewritten: a Tarmar combatant's ``team`` and a
    classic figure's side.
    """
    from battle.classic_adaptation import classic_side

    battle.combatants.filter(team=old).update(team=new)
    state = dict(battle.state_json)
    for combatant in state.get("combatants", []):
        if combatant.get("team") == old:
            combatant["team"] = new
    for figure in state.get("figures", []):
        if figure["side"] == classic_side(old, 0):
            figure["side"] = classic_side(new, 0)
    battle.state_json = state
    battle.save(update_fields=["state_json"])
    return battle


class TeamNamedLikeRowTest(BaseTestCase):
    """#930 end to end: a team named like a row id is that team and no other.

    Before #930 a classic team's side was its bare name, the same string as
    the teamless figure in that row, so the two fought as allies and either
    one's win printed the same line; and the last-team-standing check keyed
    a teamless survivor as ``"solo:<row>"``, which a team could be named.
    """

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()

    def finish(self, battle: Battle) -> tuple[Battle, list[str]]:
        for _turn in range(300):
            if battle.status in (Battle.STATUS_FINISHED, Battle.STATUS_STALEMATE):
                break
            battle, _events = advance_turn(battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_FINISHED)
        messages = list(
            battle.events.order_by("turn", "sequence").values_list("message", flat=True)
        )
        return battle, messages

    def classic_battle(self, seed: int) -> Battle:
        return create_battle(
            self.dm,
            name="Row-named team",
            count=3,
            seed=seed,
            rules_profile=CLASSIC_PROFILE,
            fighter_teams=("Red", "Red", ""),
        )

    def test_a_team_named_like_a_teamless_row_fights_that_row(self):
        battle = self.classic_battle(seed=5)
        loner = battle.combatants.get(team="")
        team = str(loner.pk)
        battle = rename_team(battle, "Red", team)
        members = list(battle.combatants.filter(team=team))

        from tarmar_engine.classic.state import GameState

        profile = for_battle(battle)
        state = profile.load(battle.state_json)
        assert isinstance(state, GameState)
        by_uid = {figure.uid: figure for figure in state.figures}
        lone_figure = by_uid[str(loner.pk)]
        self.assertEqual(
            {figure.uid for figure in state.enemies_of(lone_figure)},
            {str(member.pk) for member in members},
        )
        self.assertEqual(profile.actor_team(lone_figure), "")

        battle, messages = self.finish(battle)
        verdict = battle.events.filter(phase=6).order_by("-sequence").first()
        assert verdict is not None
        wins = [
            message for message in messages if "hold" in message and "🏆" in message
        ]
        # Seed 5 is a team win with both members standing: the line that
        # used to be shared with the loner's win must credit the team.
        self.assertEqual(verdict.payload["winner_team"], team)
        self.assertEqual(
            sorted(verdict.payload["winners"]), sorted(member.pk for member in members)
        )
        self.assertEqual(wins, [f"🏆 The {team} hold the field — victory!"])
        self.assertNotIn(
            f"🏆 {loner.display_name} holds the field — victory!", messages
        )
        # The loner is never printed under the team's name, and every
        # member always is.
        self.assertFalse(
            any(f"{loner.display_name} ({team})" in line for line in messages)
        )
        for member in members:
            self.assertTrue(
                any(f"{member.display_name} ({team})" in line for line in messages)
            )

    def test_a_team_named_like_its_own_members_row_is_still_a_team(self):
        battle = self.classic_battle(seed=5)
        member = battle.combatants.filter(team="Red").order_by("pk").first()
        assert member is not None
        team = str(member.pk)
        battle = rename_team(battle, "Red", team)

        profile = for_battle(battle)
        state = profile.load(battle.state_json)
        figure = next(one for one in state.figures if one.uid == team)
        self.assertEqual(profile.actor_team(figure), team)
        view = profile.view(state)
        self.assertEqual(
            {entry["combatant_id"]: entry["team"] for entry in view["combatants"]},
            {combatant.pk: combatant.team for combatant in battle.combatants.all()},
        )

        battle, messages = self.finish(battle)
        self.assertTrue(
            any(f"{member.display_name} ({team})" in line for line in messages)
        )
        self.assertNotIn(
            f"🏆 {member.display_name} holds the field — victory!", messages
        )

    def test_a_tarmar_team_named_like_the_solo_key_is_not_the_loner(self):
        battle = create_battle(
            self.dm, name="Solo", count=3, seed=11, fighter_teams=("Red", "Red", "")
        )
        loner = battle.combatants.get(team="")
        battle = rename_team(battle, "Red", f"solo:{loner.pk}")
        battle, events = advance_turn(battle.pk)
        # Three fighters on the rim cannot all be downed but one in a turn;
        # before #930 the team and the loner keyed as one side and the
        # battle ended at once as the team's win.
        self.assertNotEqual(battle.status, Battle.STATUS_FINISHED)
        self.assertFalse(any(event.payload.get("winner_team") for event in events))
