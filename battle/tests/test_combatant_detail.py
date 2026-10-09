"""A combatant's full attributes, on the battle page itself (issue #302).

The page used to surface a combatant's condition and nothing else — fatigue
and body, or a classic figure's ST. Everything that decides how the fight
goes (attributes, the readied weapon and its damage, armour and stops,
shield, move rates, the DEX bonus a to-hit roll adds) lived only on the
character sheet, a page away.

Three things these tests hold down, in the order they matter:

1. **The numbers come from the battle's stored state.** A combatant is a
   snapshot taken at seat time (``services.snapshot_character``); the sheet
   it came from may have been edited since. A panel that read the
   ``Character`` row would show a DM numbers the engine is not using, which
   is worse than showing nothing.
2. **Who may open it.** Spencer's ruling on the issue's open question: your
   own combatant always, everyone's if you are the DM, nothing extra for
   another player's figure. ``battle.access.detail_visible_ids`` is the one
   statement of that, and #273's private characters must not leak through it.
3. **Every kind of combatant answers.** Generated fighters, seated saved
   characters and catalog beasts all render, under both rules profiles.
"""

import json

from django.urls import reverse

from battle import profiles, viewmodel
from battle.access import Actor, detail_visible_ids
from battle.classic_adaptation import classic_state
from battle.engine.state import BattleState, WeaponState
from battle.models import Battle
from battle.services import (
    advance_turn,
    claim_seat,
    create_battle,
    set_seat_ready,
    start_battle,
)
from battle.tests.test_services import make_beast
from battle.tests.test_viewmodel import classic_snapshot, tarmar_combatant
from characters.models import Character
from common.test_helpers import BaseTestCase, create_character


def group_named(detail, label):
    """The one group in ``detail`` with this heading, or None."""
    for group in detail["groups"]:
        if group["label"] == label:
            return group
    return None


def rows_source(detail, label):
    """A group's raw rows — empty when the group is not in the panel."""
    group = group_named(detail, label)
    return group["rows"] if group else []


def rows_of(detail, label):
    """A group's rows as a ``{label: value}`` mapping, for readable asserts."""
    return {row["label"]: row["value"] for row in rows_source(detail, label)}


class TarmarDetailShapeTest(BaseTestCase):
    """What a Tarmar combatant's panel says, and how it is grouped."""

    def detail_of(self, combatant, **kwargs):
        state = BattleState(arena_radius=4, combatants=[combatant])
        view = viewmodel.tarmar_view(
            state, next_sequence=1, detail_ids=frozenset({combatant.combatant_id})
        )
        return view["combatants"][0]["detail"]

    def test_no_detail_without_an_explicit_permission(self):
        """The default is nothing: a caller that forgets cannot leak."""
        state = BattleState(arena_radius=4, combatants=[tarmar_combatant()])
        view = viewmodel.tarmar_view(state, next_sequence=1)
        self.assertIsNone(view["combatants"][0]["detail"])

    def test_groups_run_from_who_it_is_to_what_it_can_do(self):
        detail = self.detail_of(tarmar_combatant())
        self.assertEqual(
            [group["label"] for group in detail["groups"]],
            ["Attributes", "Attack", "Defence", "Movement"],
        )

    def test_attributes_are_the_five_the_snapshot_carries(self):
        detail = self.detail_of(
            tarmar_combatant(
                strength=13, dexterity=11, intelligence=9, wisdom=15, constitution=12
            )
        )
        self.assertEqual(
            rows_of(detail, "Attributes"),
            {"STR": "13", "DEX": "11", "INT": "9", "WIS": "15", "CON": "12"},
        )

    def test_the_dex_row_says_it_is_the_combat_value(self):
        """``CombatantState.dexterity`` already has the armour penalty in it,
        so an unqualified "DEX" would misreport the sheet's own number."""
        detail = self.detail_of(tarmar_combatant())
        notes = {row["label"]: row["note"] for row in rows_source(detail, "Attributes")}
        self.assertIn("armour", notes["DEX"])

    def test_attack_carries_the_weapon_its_damage_and_the_to_hit_bonus(self):
        detail = self.detail_of(
            tarmar_combatant(
                dexterity=16,
                weapon_skill_level=3,
                weapon=WeaponState(
                    name="Longbow",
                    weapon_class="Missile Bow",
                    damage="1d6+2",
                    str_req=11,
                    is_missile=True,
                ),
            )
        )
        rows = rows_of(detail, "Attack")
        self.assertEqual(rows["Weapon"], "Longbow")
        self.assertEqual(rows["Damage"], "1d6+2")
        self.assertEqual(rows["Class"], "Missile Bow")
        self.assertEqual(rows["Skill"], "level 3")
        self.assertEqual(rows["STR needed"], "11")
        self.assertEqual(rows["Reach"], "Missile")
        # tarmar_rules.dex_modifier(16) — the bonus every to-hit roll adds.
        self.assertEqual(rows["To hit"], "+3")

    def test_a_thrown_weapon_says_so_and_a_melee_one_says_melee(self):
        thrown = self.detail_of(
            tarmar_combatant(weapon=WeaponState(name="Javelin", is_thrown=True))
        )
        self.assertEqual(rows_of(thrown, "Attack")["Reach"], "Thrown")
        melee = self.detail_of(tarmar_combatant(weapon=WeaponState(name="Broadsword")))
        self.assertEqual(rows_of(melee, "Attack")["Reach"], "Melee")

    def test_defence_is_the_tier_the_stops_and_the_shield(self):
        detail = self.detail_of(
            tarmar_combatant(armour_tier="Chain", stops=3, shield_bonus=2)
        )
        self.assertEqual(
            rows_of(detail, "Defence"),
            {"Armour": "Chain", "Stops": "3", "Shield": "+2"},
        )

    def test_no_shield_reads_as_none_rather_than_a_blank(self):
        detail = self.detail_of(tarmar_combatant(shield_bonus=0))
        self.assertEqual(rows_of(detail, "Defence")["Shield"], "None")

    def test_movement_is_the_four_gaits(self):
        # Was test_movement_is_the_three_rates: Sprint is a gait the board
        # plays since tarmar-engine 0.9.5 (#818), so the card shows it.
        detail = self.detail_of(
            tarmar_combatant(move_walk=5, move_jog=8, move_run=14, move_sprint=20)
        )
        rows = rows_of(detail, "Movement")
        self.assertEqual(rows["Walk"], "5")
        self.assertEqual(rows["Jog"], "8")
        self.assertEqual(rows["Run"], "14")
        self.assertEqual(rows["Sprint"], "20")
        self.assertNotIn("Size", rows)

    def test_a_barred_gait_reads_n_a_as_on_the_sheet(self):
        # Chainmail: "Cannot Run or Sprint", seated as 0 (#778).
        chainmail = rows_of(
            self.detail_of(
                tarmar_combatant(move_walk=5, move_jog=8, move_run=0, move_sprint=0)
            ),
            "Movement",
        )
        self.assertEqual(
            chainmail, {"Walk": "5", "Jog": "8", "Run": "n/a", "Sprint": "n/a"}
        )
        # A heavy load bars Jog too; the seat gives it the Walk distance.
        heavy = rows_of(
            self.detail_of(
                tarmar_combatant(move_walk=1, move_jog=1, move_run=0, move_sprint=0)
            ),
            "Movement",
        )
        self.assertEqual(heavy["Jog"], "n/a")
        self.assertEqual(heavy["Walk"], "1")

    def test_a_multi_hex_figure_reports_its_footprint(self):
        detail = self.detail_of(tarmar_combatant(size_hexes=3))
        self.assertEqual(rows_of(detail, "Movement")["Size"], "3 hexes")

    def test_magic_appears_only_for_a_combatant_that_has_any(self):
        self.assertIsNone(group_named(self.detail_of(tarmar_combatant()), "Magic"))
        detail = self.detail_of(
            tarmar_combatant(
                max_mana=6, mana=4, spells=["blur", "flame"], active_spells=["blur"]
            )
        )
        rows = rows_of(detail, "Magic")
        self.assertEqual(rows["Mana"], "4 / 6")
        self.assertEqual(rows["Spells"], "blur, flame")
        self.assertEqual(rows["In effect"], "blur")


class ClassicDetailShapeTest(BaseTestCase):
    """The same panel over a classic *Melee* figure, in its own vocabulary."""

    def detail_of(self, snapshot):
        state, _roster = classic_state([snapshot], seed=3)
        view = viewmodel.classic_view(
            state,
            turn=0,
            next_sequence=1,
            detail_ids=frozenset({snapshot.combatant_id}),
        )
        return view["combatants"][0]["detail"]

    def test_a_classic_figure_answers_in_classic_terms(self):
        detail = self.detail_of(classic_snapshot(3, "Kestra"))
        self.assertEqual(
            [group["label"] for group in detail["groups"]],
            ["Attributes", "Attack", "Defence", "Movement"],
        )
        attributes = rows_of(detail, "Attributes")
        self.assertIn("ST", attributes)
        self.assertIn("DX", attributes)
        self.assertIn("IQ", attributes)
        self.assertIn("Adjusted DX", attributes)

    def test_the_classic_weapon_and_armour_cross_over(self):
        detail = self.detail_of(classic_snapshot(3, "Kestra"))
        self.assertTrue(rows_of(detail, "Attack")["Weapon"])
        self.assertTrue(rows_of(detail, "Attack")["Damage"])
        self.assertTrue(rows_of(detail, "Defence")["Armour"])
        self.assertTrue(rows_of(detail, "Movement")["Move"])

    def test_no_detail_without_an_explicit_permission(self):
        snapshot = classic_snapshot(3, "Kestra")
        state, _roster = classic_state([snapshot], seed=3)
        view = viewmodel.classic_view(state, turn=0, next_sequence=1)
        self.assertIsNone(view["combatants"][0]["detail"])


class SnapshotNotTheSheetTest(BaseTestCase):
    """The panel shows the numbers being fought with, not the current sheet."""

    def test_editing_the_character_afterwards_does_not_move_the_panel(self):
        dm = self.create_dm()
        character = create_character(dm, name="Vane", strength=14)
        battle = create_battle(
            dm, name="Seated", count=1, seed=3, character_ids=(character.pk,)
        )
        seated = battle.combatants.get(character=character)

        character.strength = 25
        character.save()

        view = self.view_for(dm, battle)
        entry = self.entry_for(view, seated.pk)
        # 14, the snapshot's effective strength — not the 25 now on the sheet.
        self.assertEqual(rows_of(entry["detail"], "Attributes")["STR"], "14")
        self.assertEqual(Character.objects.get(pk=character.pk).strength, 25)

    def view_for(self, user, battle):
        return profiles.view_of(
            battle,
            detail_ids=detail_visible_ids(Actor(user=user, guest_key=""), battle),
        )

    def entry_for(self, view, combatant_id):
        return [
            entry
            for entry in view["combatants"]
            if entry["combatant_id"] == combatant_id
        ][0]


class EveryKindOfCombatantTest(BaseTestCase):
    """Generated fighters, seated characters and beasts all answer."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()

    def details(self, battle):
        view = profiles.view_of(
            battle,
            detail_ids=detail_visible_ids(Actor(user=self.dm, guest_key=""), battle),
        )
        return [entry["detail"] for entry in view["combatants"]]

    def test_a_generated_fighter_has_a_panel(self):
        battle = create_battle(self.dm, name="Pit", count=2, seed=11)
        for detail in self.details(battle):
            self.assertTrue(detail["groups"])

    def test_a_seated_character_has_a_panel(self):
        character = create_character(self.dm, name="Vane")
        battle = create_battle(
            self.dm, name="Duel", count=1, seed=4, character_ids=(character.pk,)
        )
        self.assertEqual(len(self.details(battle)), 2)
        for detail in self.details(battle):
            self.assertTrue(rows_of(detail, "Attributes"))

    def test_a_beast_has_a_panel(self):
        make_beast()
        battle = create_battle(
            self.dm, name="Hunt", count=1, seed=6, beast_slugs=("test-wolf",)
        )
        self.assertEqual(len(self.details(battle)), 2)
        for detail in self.details(battle):
            self.assertTrue(rows_of(detail, "Attack")["Weapon"])


class WhoMaySeeItTest(BaseTestCase):
    """Spencer's ruling, as a queryable rule (``detail_visible_ids``)."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.player = self.create_player()
        self.watcher = self.create_user(username="watcher")

    def ids_for(self, user, battle, guest_key=""):
        actor = (
            Actor(user=user, guest_key=guest_key)
            if user
            else Actor(user=None, guest_key=guest_key)
        )
        return detail_visible_ids(actor, battle)

    def test_the_dm_sees_every_combatant(self):
        battle = create_battle(self.dm, name="Pit", count=3, seed=8)
        self.assertEqual(
            self.ids_for(self.dm, battle),
            {combatant.pk for combatant in battle.combatants.all()},
        )

    def test_a_watching_player_sees_nobody(self):
        battle = create_battle(self.dm, name="Pit", count=3, seed=8)
        self.assertEqual(self.ids_for(self.watcher, battle), frozenset())

    def test_a_seated_player_sees_their_own_combatant_only(self):
        battle = create_battle(self.dm, name="Open", count=2, seed=9, player_seats=1)
        seat = battle.combatants.filter(is_open_seat=True).first()
        seat = claim_seat(battle.pk, seat.pk, self.player, archetype="archer")
        self.assertEqual(self.ids_for(self.player, battle), {seat.pk})

    def test_a_players_own_character_is_visible_wherever_it_fights(self):
        """The DM seated it; its owner is watching. It is still their sheet."""
        character = create_character(self.player, name="Vane", is_public=True)
        battle = create_battle(
            self.dm, name="Duel", count=1, seed=4, character_ids=(character.pk,)
        )
        seated = battle.combatants.get(character=character)
        self.assertEqual(self.ids_for(self.player, battle), {seated.pk})

    def test_another_players_private_character_never_becomes_visible(self):
        """#273's rule, at the one new place a sheet could be read out."""
        character = create_character(self.player, name="Hidden")
        battle = create_battle(
            self.dm, name="Duel", count=1, seed=4, character_ids=(character.pk,)
        )
        seated = battle.combatants.get(character=character)
        self.assertNotIn(seated.pk, self.ids_for(self.watcher, battle))
        self.assertNotIn(seated.pk, self.ids_for(None, battle, guest_key="abc"))

    def test_whoever_made_the_battle_sees_the_figures_that_belong_to_nobody(self):
        """A guest's own arena: fighters with no ``Character`` row behind them.

        Outside Spencer's three cases and deliberately so — these figures are
        nobody's saved character, and without this the feature is dead for the
        guest arena, where every combatant is ownerless by construction.
        """
        battle = create_battle(None, guest_key="guest-1", name="Mine", count=2, seed=5)
        self.assertEqual(
            self.ids_for(None, battle, guest_key="guest-1"),
            {combatant.pk for combatant in battle.combatants.all()},
        )
        self.assertEqual(self.ids_for(None, battle, guest_key="guest-2"), frozenset())

    def test_a_guest_with_no_key_owns_nothing(self):
        battle = create_battle(None, guest_key="guest-1", name="Mine", count=2, seed=5)
        self.assertEqual(self.ids_for(None, battle, guest_key=""), frozenset())


class DetailReachesThePageTest(BaseTestCase):
    """The rule, enforced where the numbers actually leave the server."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.watcher = self.create_user(username="watcher")
        self.battle = create_battle(self.dm, name="Pit", count=2, seed=8)

    def rendered_view(self, response):
        html = response.content.decode()
        marker = '<script id="battle-view" type="application/json">'
        start = html.index(marker) + len(marker)
        return json.loads(html[start : html.index("</script>", start)])

    def test_the_dm_page_carries_every_panel(self):
        self.login_as(self.dm)
        view = self.rendered_view(
            self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        )
        for entry in view["combatants"]:
            self.assertIsNotNone(entry["detail"])

    def test_a_spectators_page_carries_none(self):
        self.login_as(self.watcher)
        response = self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        view = self.rendered_view(response)
        for entry in view["combatants"]:
            self.assertIsNone(entry["detail"])
        self.assertNotContains(response, "Adjusted DEX")

    def test_the_poll_endpoint_applies_the_same_rule(self):
        url = reverse("battle:state", args=[self.battle.pk])
        self.login_as(self.dm)
        payload = self.client.get(url).json()
        self.assertTrue(all(e["detail"] for e in payload["view"]["combatants"]))
        self.logout()
        self.login_as(self.watcher)
        payload = self.client.get(url).json()
        self.assertTrue(all(e["detail"] is None for e in payload["view"]["combatants"]))

    def test_advancing_a_turn_answers_with_the_viewers_own_panels(self):
        self.login_as(self.dm)
        response = self.client.post(reverse("battle:advance", args=[self.battle.pk]))
        self.assertEqual(response.status_code, 200)
        for entry in response.json()["view"]["combatants"]:
            self.assertIsNotNone(entry["detail"])

    def test_renaming_answers_with_the_viewers_own_panels(self):
        self.login_as(self.dm)
        combatant = self.battle.combatants.first()
        response = self.post_json(
            reverse("battle:rename", args=[self.battle.pk]),
            {"combatant_id": combatant.pk, "name": "Renamed One"},
        )
        self.assertEqual(response.status_code, 200)
        for entry in response.json()["view"]["combatants"]:
            self.assertIsNotNone(entry["detail"])

    def test_a_seated_player_polls_only_their_own_panel(self):
        battle = create_battle(self.dm, name="Open", count=1, seed=9, player_seats=1)
        player = self.create_player()
        seat = battle.combatants.filter(is_open_seat=True).first()
        seat = claim_seat(battle.pk, seat.pk, player, archetype="archer")
        set_seat_ready(battle.pk, seat.pk, player, ready=True)
        start_battle(battle.pk)
        advance_turn(battle.pk)
        self.login_as(player)
        payload = self.client.get(reverse("battle:state", args=[battle.pk])).json()
        visible = {
            entry["combatant_id"]
            for entry in payload["view"]["combatants"]
            if entry["detail"]
        }
        self.assertEqual(visible, {seat.pk})

    def test_a_lobby_has_nothing_to_detail_and_says_so_calmly(self):
        battle = create_battle(self.dm, name="Waiting", count=1, seed=2, player_seats=1)
        self.assertEqual(battle.status, Battle.STATUS_LOBBY)
        self.login_as(self.dm)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertEqual(response.status_code, 200)
