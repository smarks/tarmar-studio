"""Saved characters as combatants, and the battle-setup draft (#270/#271).

Three seams under test: who may seat whom (the permission chokepoints),
what seating snapshots (seat-time copy, source row never touched), and how
a draft's muster compiles into ``create_battle``'s arguments.
"""

from battle.models import Battle, BattleDraft
from battle.persistence import CLASSIC_PROFILE
from battle.services import (
    MAX_COMBATANTS,
    add_draft_entry,
    advance_turn,
    claim_seat,
    claimable_characters,
    create_battle,
    draft_create_kwargs,
    remove_draft_entry,
    seatable_characters,
    start_draft,
)
from bestiary.models import Beast
from common.test_helpers import BaseTestCase, create_character


class SeatableCharactersTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.player = self.create_player()
        self.own = create_character(self.player, name="Aldwin")
        self.other = create_character(self.dm, name="Morwen")
        self.dead = create_character(self.player, name="Ghost", is_alive=False)
        self.deleted = create_character(self.player, name="Erased", is_deleted=True)

    def test_dm_may_seat_any_living_undeleted_character(self):
        names = set(seatable_characters(self.dm).values_list("name", flat=True))
        self.assertEqual(names, {"Aldwin", "Morwen"})

    def test_player_may_seat_only_their_own_living_characters(self):
        names = set(seatable_characters(self.player).values_list("name", flat=True))
        self.assertEqual(names, {"Aldwin"})

    def test_claimable_is_own_only_even_for_a_dm(self):
        names = set(claimable_characters(self.dm).values_list("name", flat=True))
        self.assertEqual(names, {"Morwen"})

    def test_dead_and_deleted_are_never_claimable(self):
        names = set(claimable_characters(self.player).values_list("name", flat=True))
        self.assertEqual(names, {"Aldwin"})


class CreateBattleWithCharactersTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.player = self.create_player()
        self.character = create_character(
            self.player, name="Aldwin", strength=12, fatigue_roll=12
        )

    def test_saved_character_joins_the_roster_under_its_own_name(self):
        battle = create_battle(
            self.dm, name="Muster", count=1, seed=5, character_ids=(self.character.pk,)
        )
        row = battle.combatants.get(character=self.character)
        self.assertEqual(row.display_name, "Aldwin")
        self.assertTrue(row.is_saved_character)
        state_names = [entry["name"] for entry in battle.state_json["combatants"]]
        self.assertIn("Aldwin", state_names)

    def test_generated_names_redraw_around_a_picked_characters_name(self):
        """The player's actual character keeps its exact name; a generated
        fighter that would collide is the one renamed."""
        battle = create_battle(
            self.dm, name="Muster", count=3, seed=5, character_ids=(self.character.pk,)
        )
        row = battle.combatants.get(character=self.character)
        self.assertEqual(row.display_name, "Aldwin")

    def test_character_not_seatable_by_user_is_refused(self):
        stranger_character = create_character(self.player, name="Private")
        # A second plain player cannot seat someone else's character even
        # through the service directly.
        other_player = self.create_player(username="second")
        with self.assertRaisesMessage(ValueError, "Not characters you can seat"):
            create_battle(
                other_player,
                name="Steal",
                count=1,
                seed=5,
                character_ids=(stranger_character.pk,),
            )

    def test_dead_or_deleted_characters_are_refused(self):
        dead = create_character(self.player, name="Ghost", is_alive=False)
        with self.assertRaises(ValueError):
            create_battle(
                self.dm, name="Necromancy", count=1, seed=5, character_ids=(dead.pk,)
            )

    def test_the_same_character_cannot_fight_twice(self):
        with self.assertRaisesMessage(ValueError, "cannot fight twice"):
            create_battle(
                self.dm,
                name="Twins",
                count=0,
                seed=5,
                character_ids=(self.character.pk, self.character.pk),
            )

    def test_characters_count_toward_the_field_bounds(self):
        with self.assertRaises(ValueError):
            create_battle(
                self.dm,
                name="Overfull",
                count=MAX_COMBATANTS,
                seed=5,
                character_ids=(self.character.pk,),
            )

    def test_snapshot_is_taken_at_seat_time_and_the_source_row_never_mutates(self):
        before = {
            "fatigue": self.character.fatigue,
            "body": self.character.body,
            "skills": dict(self.character.skills or {}),
            "strength": self.character.strength,
        }
        battle = create_battle(
            self.dm, name="Copy", count=1, seed=5, character_ids=(self.character.pk,)
        )
        for _turn in range(12):
            battle, _events = advance_turn(battle.pk)
            if battle.status == Battle.STATUS_FINISHED:
                break
        self.assertGreater(battle.turn_number, 0)
        self.character.refresh_from_db()
        self.assertEqual(self.character.fatigue, before["fatigue"])
        self.assertEqual(self.character.body, before["body"])
        self.assertEqual(dict(self.character.skills or {}), before["skills"])
        self.assertEqual(self.character.strength, before["strength"])

    def test_edits_after_creation_do_not_reach_the_battle(self):
        battle = create_battle(
            self.dm, name="Frozen", count=1, seed=5, character_ids=(self.character.pk,)
        )
        snapshot = next(
            entry
            for entry in battle.state_json["combatants"]
            if entry["name"] == "Aldwin"
        )
        self.character.strength = 3
        self.character.save()
        battle.refresh_from_db()
        unchanged = next(
            entry
            for entry in battle.state_json["combatants"]
            if entry["name"] == "Aldwin"
        )
        self.assertEqual(unchanged, snapshot)

    def test_saved_character_crosses_into_a_classic_battle(self):
        battle = create_battle(
            self.dm,
            name="Crossing",
            count=1,
            seed=5,
            character_ids=(self.character.pk,),
            rules_profile=CLASSIC_PROFILE,
        )
        self.assertEqual(battle.rules_profile, CLASSIC_PROFILE)
        figure_names = [figure["name"] for figure in battle.state_json["figures"]]
        self.assertIn("Aldwin", figure_names)

    def test_lobby_rows_for_picked_characters_carry_snapshots(self):
        battle = create_battle(
            self.dm,
            name="Lobby",
            count=0,
            seed=5,
            character_ids=(self.character.pk,),
            player_seats=1,
        )
        self.assertEqual(battle.status, Battle.STATUS_LOBBY)
        row = battle.combatants.get(character=self.character)
        self.assertEqual(row.snapshot_json["name"], "Aldwin")


class ClaimSeatEligibilityTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.player = self.create_player()
        self.battle = create_battle(
            self.dm, name="Open", count=1, seed=7, player_seats=1
        )
        self.seat = self.battle.combatants.get(is_open_seat=True)

    def test_dead_character_cannot_claim_a_seat(self):
        dead = create_character(self.player, name="Ghost", is_alive=False)
        with self.assertRaisesMessage(ValueError, "living characters"):
            claim_seat(self.battle.pk, self.seat.pk, self.player, character_id=dead.pk)

    def test_deleted_character_cannot_claim_a_seat(self):
        deleted = create_character(self.player, name="Erased", is_deleted=True)
        with self.assertRaises(ValueError):
            claim_seat(
                self.battle.pk, self.seat.pk, self.player, character_id=deleted.pk
            )

    def test_living_own_character_still_claims(self):
        alive = create_character(self.player, name="Aldwin")
        seat = claim_seat(
            self.battle.pk, self.seat.pk, self.player, character_id=alive.pk
        )
        self.assertEqual(seat.character, alive)
        self.assertEqual(seat.snapshot_json["name"], "Aldwin")
        self.assertTrue(seat.is_saved_character)


class DraftEntryTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.draft = BattleDraft.objects.create(created_by=self.dm, name="Draft")

    def test_fighter_beast_seat_and_character_entries_append_in_order(self):
        beast = Beast.objects.filter(is_generated=False).earliest("id")
        character = create_character(self.dm, name="Morwen")
        add_draft_entry(self.draft, self.dm, kind="fighter", archetype="wizard")
        add_draft_entry(self.draft, self.dm, kind="beast", slug=beast.slug)
        add_draft_entry(self.draft, self.dm, kind="open_seat")
        add_draft_entry(
            self.draft, self.dm, kind="character", character_id=character.pk
        )
        add_draft_entry(self.draft, self.dm, kind="random_beast", difficulty="easy")
        self.draft.refresh_from_db()
        self.assertEqual(
            [entry["kind"] for entry in self.draft.roster_json],
            ["fighter", "beast", "open_seat", "character", "random_beast"],
        )

    def test_unknown_archetype_is_refused(self):
        with self.assertRaises(ValueError):
            add_draft_entry(self.draft, self.dm, kind="fighter", archetype="ninja")

    def test_unknown_beast_slug_is_refused(self):
        with self.assertRaises(ValueError):
            add_draft_entry(self.draft, self.dm, kind="beast", slug="krakenoid")

    def test_unknown_random_filters_are_refused(self):
        with self.assertRaises(ValueError):
            add_draft_entry(self.draft, self.dm, kind="random_beast", category="mecha")
        with self.assertRaises(ValueError):
            add_draft_entry(
                self.draft, self.dm, kind="random_beast", difficulty="impossible"
            )

    def test_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            add_draft_entry(self.draft, self.dm, kind="siege_engine")

    def test_character_entry_respects_the_seatable_rule(self):
        player = self.create_player()
        dead = create_character(player, name="Ghost", is_alive=False)
        with self.assertRaises(ValueError):
            add_draft_entry(self.draft, self.dm, kind="character", character_id=dead.pk)

    def test_the_same_character_cannot_be_mustered_twice(self):
        character = create_character(self.dm, name="Morwen")
        add_draft_entry(
            self.draft, self.dm, kind="character", character_id=character.pk
        )
        with self.assertRaisesMessage(ValueError, "already on the muster"):
            add_draft_entry(
                self.draft, self.dm, kind="character", character_id=character.pk
            )

    def test_a_full_muster_refuses_more(self):
        for _slot in range(MAX_COMBATANTS):
            add_draft_entry(self.draft, self.dm, kind="open_seat")
        with self.assertRaisesMessage(ValueError, "full"):
            add_draft_entry(self.draft, self.dm, kind="open_seat")

    def test_remove_strikes_by_position(self):
        add_draft_entry(self.draft, self.dm, kind="fighter", archetype="wizard")
        add_draft_entry(self.draft, self.dm, kind="open_seat")
        remove_draft_entry(self.draft, 0)
        self.draft.refresh_from_db()
        self.assertEqual(
            [entry["kind"] for entry in self.draft.roster_json], ["open_seat"]
        )

    def test_remove_out_of_range_is_refused(self):
        with self.assertRaises(ValueError):
            remove_draft_entry(self.draft, 0)


class DraftCompileTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()

    def test_muster_compiles_into_create_battle_arguments(self):
        beast = Beast.objects.filter(is_generated=False).earliest("id")
        character = create_character(self.dm, name="Morwen")
        draft = BattleDraft.objects.create(
            created_by=self.dm, name=" Pit Fight ", seed=42
        )
        add_draft_entry(draft, self.dm, kind="fighter", archetype="archer")
        add_draft_entry(draft, self.dm, kind="fighter", archetype="wizard")
        add_draft_entry(draft, self.dm, kind="beast", slug=beast.slug)
        add_draft_entry(draft, self.dm, kind="random_beast", difficulty="easy")
        add_draft_entry(draft, self.dm, kind="character", character_id=character.pk)
        add_draft_entry(draft, self.dm, kind="open_seat")
        kwargs = draft_create_kwargs(draft)
        self.assertEqual(kwargs["name"], "Pit Fight")
        self.assertEqual(kwargs["seed"], 42)
        self.assertEqual(kwargs["count"], 2)
        self.assertEqual(kwargs["archetypes"], ("archer", "wizard"))
        self.assertEqual(kwargs["beast_slugs"], (beast.slug,))
        self.assertEqual(kwargs["random_beast_specs"], (("", "easy"),))
        self.assertEqual(kwargs["character_ids"], (character.pk,))
        self.assertEqual(kwargs["player_seats"], 1)

    def test_start_draft_creates_the_battle_and_retires_the_draft(self):
        draft = BattleDraft.objects.create(created_by=self.dm, name="Go", seed=9)
        add_draft_entry(draft, self.dm, kind="fighter", archetype="archer")
        add_draft_entry(draft, self.dm, kind="fighter", archetype="wizard")
        battle = start_draft(draft)
        self.assertEqual(battle.name, "Go")
        self.assertEqual(battle.combatants.count(), 2)
        self.assertFalse(BattleDraft.objects.filter(pk=draft.pk).exists())

    def test_a_nameless_draft_refuses_to_start(self):
        draft = BattleDraft.objects.create(created_by=self.dm, name="  ")
        add_draft_entry(draft, self.dm, kind="fighter", archetype="archer")
        add_draft_entry(draft, self.dm, kind="fighter", archetype="wizard")
        with self.assertRaisesMessage(ValueError, "needs a name"):
            start_draft(draft)
        self.assertTrue(BattleDraft.objects.filter(pk=draft.pk).exists())

    def test_a_failed_start_keeps_the_draft(self):
        draft = BattleDraft.objects.create(created_by=self.dm, name="Thin")
        add_draft_entry(draft, self.dm, kind="fighter", archetype="archer")
        with self.assertRaises(ValueError):
            start_draft(draft)
        self.assertTrue(BattleDraft.objects.filter(pk=draft.pk).exists())

    def test_same_seed_same_draft_same_roster(self):
        names = []
        for _round in range(2):
            draft = BattleDraft.objects.create(
                created_by=self.dm, name="Twice", seed=99
            )
            add_draft_entry(draft, self.dm, kind="fighter", archetype="archer")
            add_draft_entry(draft, self.dm, kind="random_beast")
            battle = start_draft(draft)
            names.append(list(battle.combatants.values_list("display_name", flat=True)))
        self.assertEqual(names[0], names[1])
