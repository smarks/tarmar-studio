"""Service layer: creation snapshots, deterministic advance, golden battle."""

from copy import deepcopy
from unittest.mock import patch

from battle.engine import engine
from battle.engine.state import BattleState, GroundWeapon
from battle.generation import generate_character
from battle.models import Battle, BattleEvent
from battle.services import (
    _catalog_weapon,
    advance_turn,
    create_battle,
    rename_combatant,
    restart_battle,
    snapshot_character,
    snapshot_combatant,
    starting_positions,
)
from characters.equipment_rules import equipment_catalog
from characters.models import board_can_seat_weapon
from characters.skills import points_for_level
from characters.weapon_skill import resolve_weapon_skill
from common.rolling import Roller
from common.test_helpers import BaseTestCase, create_character
from common.test_helpers import make_beast as shared_make_beast

GOLDEN_SEED = 42


class StartingPositionsTest(BaseTestCase):
    def test_positions_are_distinct_and_on_the_rim(self):
        positions = starting_positions(6, arena_radius=6)
        self.assertEqual(len(set(positions)), 6)
        from battle.engine import hexes

        for position in positions:
            self.assertEqual(hexes.distance((0, 0), position), 5)


class SnapshotTest(BaseTestCase):
    def test_snapshot_freezes_character_into_engine_state(self):
        user = self.create_dm()
        # The full chainmail-and-large-shield kit the assertions below read,
        # whatever STR was rolled: a Tarmar fighter now sheds what its STR
        # cannot bear (#883), so the default would deal a lighter set.
        generated = generate_character(  # armoured fighter
            user, "Snapshot", 0, within_load=False
        )
        combatant = snapshot_combatant(generated, 7, (2, -1), 4)
        character = generated.character
        self.assertEqual(combatant.combatant_id, 7)
        self.assertEqual(combatant.name, generated.display_name)
        self.assertEqual(combatant.position, (2, -1))
        self.assertEqual(combatant.facing, 4)
        self.assertEqual(combatant.max_fatigue, character.fatigue)
        self.assertEqual(combatant.fatigue, character.fatigue)
        self.assertEqual(combatant.max_body, character.body)
        self.assertEqual(combatant.dexterity, character.combat_dexterity)
        self.assertEqual(combatant.armour_tier, "Medium")  # chainmail
        self.assertEqual(combatant.stops, character.armor_stops)
        self.assertEqual(combatant.shield_bonus, 2)  # large shield
        self.assertGreater(combatant.weapon_skill_level, 0)
        # Chainmail: "Cannot Run or Sprint" (armor-and-shields.md). This
        # asserted move_run >= 1, which pinned the seat's old floor of Run at
        # the Jog distance — a figure in chainmail running anyway (#778).
        self.assertEqual((combatant.move_run, combatant.move_sprint), (0, 0))
        self.assertEqual(combatant.movement_modifier, character.movement_modifier)

    def test_brawler_snapshot_holds_its_dagger(self):
        # Was test_brawler_snapshot_is_bare_handed, which pinned bare hands
        # at the "brawling" level: the brawler now carries a dagger (#867),
        # usable in hand-to-hand, at its Weapon skill with it.
        user = self.create_dm()
        generated = generate_character(user, "Snapshot", 2)  # brawler
        combatant = snapshot_combatant(generated, 1, (0, 0), 0)
        self.assertEqual(combatant.weapon.item_id, "dagger")
        self.assertTrue(combatant.weapon.hth_usable)
        self.assertGreater(combatant.weapon_skill_level, 0)
        self.assertEqual(
            combatant.weapon_skills["dagger"], combatant.weapon_skill_level
        )

    def test_wizard_snapshot_carries_mana_and_spells(self):
        user = self.create_dm()
        generated = generate_character(user, "Snapshot", 4)  # wizard
        combatant = snapshot_combatant(generated, 1, (0, 0), 0)
        self.assertEqual(combatant.mana, generated.mana)
        self.assertEqual(combatant.spells, list(generated.spells))


class SeatedFieldsTest(BaseTestCase):
    """The fields tarmar-engine 0.9.5 reads, filled from the character.

    The engine's README: "An adapter that fills none of the new fields does
    not play as before." Each is read from the record by the module that
    owns its rule.
    """

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("seated")

    def seat(self, **fields):
        character = create_character(self.owner, **fields)
        return character, snapshot_character(
            character, 1, display_name="Seated", archetype="", spells=(), mana=0
        )

    def test_gaits_are_the_sheets_with_a_barred_gait_at_zero(self):
        for armour, run_barred, sprint_barred in (
            ([], False, False),
            (
                [{"item_id": "leather_armor", "quantity": 1, "equipped": True}],
                False,
                True,
            ),
            ([{"item_id": "chainmail", "quantity": 1, "equipped": True}], True, True),
        ):
            with self.subTest(armour=armour):
                character, seated = self.seat(equipment=armour)
                self.assertEqual(seated.move_walk, character.walk_distance)
                # A load that bars jogging (the default body under chainmail)
                # charges and dodges at the walk, "Jog or less".
                self.assertEqual(
                    seated.move_jog, character.jog_distance or character.walk_distance
                )
                self.assertEqual(
                    seated.move_run, 0 if run_barred else character.run_distance
                )
                self.assertEqual(
                    seated.move_sprint,
                    0 if sprint_barred else character.sprint_distance,
                )
                self.assertGreater(seated.move_walk, 0)

    def test_movement_modifier_is_the_sheets(self):
        character, seated = self.seat(strength=16, dexterity=6, constitution=14)
        self.assertEqual(seated.movement_modifier, character.movement_modifier)

    def test_carried_weapons_are_spares_each_at_its_own_skill(self):
        _character, seated = self.seat(
            skills={
                "weapon (broadsword)": points_for_level(3),
                "weapon (dagger)": points_for_level(2),
            },
            equipment=[
                {"item_id": "broadsword", "quantity": 1},
                {"item_id": "dagger", "quantity": 2},
                {"item_id": "rope", "quantity": 1},
            ],
        )
        self.assertEqual(seated.weapon.item_id, "broadsword")
        self.assertFalse(seated.weapon.hth_usable)
        self.assertEqual(seated.weapon_skill_level, 3)
        self.assertEqual(
            [spare.item_id for spare in seated.spare_weapons], ["dagger", "dagger"]
        )
        self.assertTrue(all(spare.hth_usable for spare in seated.spare_weapons))
        # Two pieces, two weapons: throwing one leaves the other.
        self.assertIsNot(seated.spare_weapons[0], seated.spare_weapons[1])
        self.assertEqual(seated.weapon_skills["broadsword"], 3)
        self.assertEqual(seated.weapon_skills["dagger"], 2)
        # The whole map, so no wrong extra entry passes: every seatable
        # catalog weapon the resolver gives a level above 0, and no other.
        expected = {}
        for item_id in equipment_catalog().weapons:
            if not board_can_seat_weapon({"item_id": item_id}):
                continue
            level = resolve_weapon_skill(_character.skills, item_id).effective_level
            if level:
                expected[item_id] = level
        self.assertEqual(seated.weapon_skills, expected)
        # A saber is Striking like the broadsword: half of III, rounded down.
        self.assertEqual(expected["saber"], 1)

    def test_the_weapon_in_hand_is_readied_and_a_packed_one_is_not_a_spare(self):
        # The sheet's weapon in hand (#846) is the one seated; the others
        # carried outside the pack are spares, and a packed one is slung away.
        _character, seated = self.seat(
            equipment=[
                {"item_id": "broadsword", "quantity": 1},
                {"item_id": "dagger", "quantity": 1, "equipped": True},
                {"item_id": "mace", "quantity": 1, "in_pack": True},
            ],
        )
        self.assertEqual(seated.weapon.item_id, "dagger")
        self.assertEqual(
            [spare.item_id for spare in seated.spare_weapons], ["broadsword"]
        )
        # The packed mace is carried nowhere the board can reach, but the
        # skill map prices every seatable weapon (a mace could be picked
        # up), and no skill here reaches a mace.
        self.assertNotIn("mace", seated.weapon_skills)

    def test_missile_rates_come_off_the_weapons_notes(self):
        for item_id, expected in (
            ("small_bow", (15, 1, 0, 1)),
            ("longbow", (18, 1, 0, 1)),
            ("light_crossbow", (0, 2, 14, 1)),
            ("heavy_crossbow", (0, 3, 16, 2)),
            ("sling", (0, 1, 0, 1)),
            ("broadsword", (0, 1, 0, 1)),
        ):
            with self.subTest(item_id):
                _character, seated = self.seat(
                    equipment=[{"item_id": item_id, "quantity": 1}]
                )
                weapon = seated.weapon
                self.assertEqual(weapon.item_id, item_id)
                self.assertEqual(
                    (
                        weapon.double_shot_dex,
                        weapon.reload_turns,
                        weapon.quick_reload_dex,
                        weapon.quick_reload_turns,
                    ),
                    expected,
                )

    def test_bare_hands_are_seated_at_level_zero(self):
        _character, seated = self.seat(skills={"brawling": points_for_level(4)})
        self.assertEqual(seated.weapon.item_id, "")
        self.assertEqual(seated.weapon_skill_level, 0)
        self.assertEqual((seated.spare_weapons, seated.weapon_skills), ([], {}))

    def test_a_weapon_picked_up_is_used_at_the_sheets_level(self):
        """Review of the pin: the skill map held only the weapons carried, so
        a broadsword picked up off the field was swung at level 0 by a
        character with Weapon (broadsword) III, whether it started with a
        mace, with nothing, or with only a weapon the board passes over."""
        for start in (
            [{"item_id": "mace", "quantity": 1}],
            [],
            [{"item_id": "club", "quantity": 1}],
        ):
            with self.subTest(start=start):
                self.pick_up_at_the_sheets_level(start)

    def pick_up_at_the_sheets_level(self, start: list[dict]) -> None:
        character, seated = self.seat(
            skills={"weapon (broadsword)": points_for_level(3)},
            equipment=start,
        )
        for item_id in ("broadsword", "saber", "rapier"):
            with self.subTest(item_id):
                state = BattleState(
                    arena_radius=6,
                    combatants=[deepcopy(seated)],
                    ground_weapons=[
                        GroundWeapon(q=0, r=0, weapon=_catalog_weapon(item_id))
                    ],
                )
                figure = state.combatants[0]
                runner = engine.TurnRunner(state, Roller(seed=1), lambda _event: None)
                runner.pick_up_weapon(figure)
                self.assertEqual(figure.weapon.item_id, item_id)
                self.assertEqual(
                    figure.weapon_skill_level,
                    resolve_weapon_skill(character.skills, item_id).effective_level,
                )

    def test_spell_mastery_comes_from_the_spells_record(self):
        character = create_character(
            self.create_user("mage"),
            spells={
                "fire_missile": points_for_level(3),
                "shield": points_for_level(1),
            },
        )
        seated = snapshot_character(
            character,
            1,
            display_name="Mage",
            archetype="",
            spells=("fire_missile", "shield", "heal"),
            mana=6,
        )
        # heal is carried by the seat with no points on the record: left out,
        # and the engine reads it at level 1.
        self.assertEqual(seated.spell_mastery, {"fire_missile": 3, "shield": 1})


class CreateBattleTest(BaseTestCase):
    def test_creates_battle_with_combatants_and_state(self):
        user = self.create_dm()
        battle = create_battle(user, name="Genesis", count=4, seed=GOLDEN_SEED)
        self.assertEqual(battle.seed, GOLDEN_SEED)
        self.assertEqual(battle.status, Battle.STATUS_SETUP)
        self.assertEqual(battle.combatants.count(), 4)
        state = BattleState.from_dict(battle.state_json)
        self.assertEqual(len(state.combatants), 4)
        combatant_ids = {combatant.combatant_id for combatant in state.combatants}
        self.assertEqual(
            combatant_ids,
            set(battle.combatants.values_list("id", flat=True)),
        )
        for row in battle.combatants.all():
            self.assertIsNotNone(row.character)
            self.assertTrue(row.character.name.startswith("Genesis"))

    def test_random_seed_assigned_when_omitted(self):
        user = self.create_dm()
        with patch("battle.services.random.randrange", return_value=777):
            battle = create_battle(user, name="Seeded", count=2)
        self.assertEqual(battle.seed, 777)


class AdvanceTurnTest(BaseTestCase):
    def make_battle(self, count=3, seed=GOLDEN_SEED):
        user = self.create_dm()
        # The battle seed alone pins the roster since #261, so the
        # to-completion tests below finish well inside their turn caps
        # without a global ``random.seed`` pin.
        return create_battle(user, name="Advance", count=count, seed=seed)

    def test_one_turn_persists_events_and_state(self):
        battle = self.make_battle()
        battle, events = advance_turn(battle.pk)
        self.assertEqual(battle.turn_number, 1)
        self.assertIn(battle.status, (Battle.STATUS_PAUSED, Battle.STATUS_FINISHED))
        self.assertTrue(events)
        self.assertEqual(BattleEvent.objects.filter(battle=battle).count(), len(events))
        phase_events = [event for event in events if event.event_type == "phase"]
        self.assertEqual(len(phase_events), 6)
        state = BattleState.from_dict(battle.state_json)
        self.assertEqual(state.turn, 1)

    def test_finished_battle_advances_no_further(self):
        battle = self.make_battle(count=2)
        for _turn in range(60):
            battle, _events = advance_turn(battle.pk)
            if battle.status == Battle.STATUS_FINISHED:
                break
        self.assertEqual(battle.status, Battle.STATUS_FINISHED)
        count_before = BattleEvent.objects.filter(battle=battle).count()
        battle, events = advance_turn(battle.pk)
        self.assertEqual(events, [])
        self.assertEqual(
            BattleEvent.objects.filter(battle=battle).count(), count_before
        )

    def test_finish_emits_a_victory_event(self):
        battle = self.make_battle(count=2)
        for _turn in range(60):
            battle, _events = advance_turn(battle.pk)
            if battle.status == Battle.STATUS_FINISHED:
                break
        victory = BattleEvent.objects.filter(
            battle=battle, message__contains="stands victorious"
        )
        self.assertEqual(victory.count(), 1)


class GoldenBattleTest(BaseTestCase):
    """Same seed ⇒ identical battle, event stream included, end to end."""

    def run_battle(self, name):
        user = self.create_dm(username=f"dm-{name}")
        # No global ``random.seed`` pin: since #261 the battle seed governs
        # the rolled roster as well as the names, beasts and turn stream, so
        # golden-battle equality rests on the seed alone — which is the
        # promise this test is here to hold.
        battle = create_battle(user, name=name, count=4, seed=GOLDEN_SEED)
        turns = 0
        while battle.status != Battle.STATUS_FINISHED and turns < 60:
            battle, _events = advance_turn(battle.pk)
            turns += 1
        events = [
            (
                event.turn,
                event.phase,
                event.sequence,
                event.event_type,
                event.actor.replace(name, "X"),
                event.message.replace(name, "X"),
            )
            for event in BattleEvent.objects.filter(battle=battle)
        ]
        return battle, events

    def test_same_seed_same_event_stream(self):
        battle_a, events_a = self.run_battle("GoldA")
        battle_b, events_b = self.run_battle("GoldB")
        self.assertEqual(events_a, events_b)
        self.assertEqual(battle_a.turn_number, battle_b.turn_number)
        self.assertEqual(battle_a.status, battle_b.status)


def make_beast(slug="test-wolf", **overrides):
    """The shared test beast with this module's pools; ``model=`` short-circuits."""
    model = overrides.pop("model", None)
    if model is not None:
        return model
    return shared_make_beast(
        slug=slug, **{"fatigue_pool": 43, "body_pool": 29, **overrides}
    )


class BeastBattleTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.wolf = make_beast()
        self.bear = make_beast(
            slug="cave-bear",
            name="Cave Bear",
            strength=30,
            size_hexes=3,
            movement=8,
            attacks=[
                {"name": "Claws", "damage": "2d6+1", "weapon_class": "Heavy Striking"}
            ],
        )

    def test_create_battle_with_specific_beasts(self):
        user = self.create_dm()
        battle = create_battle(
            user,
            name="Menagerie",
            count=2,
            seed=GOLDEN_SEED,
            beast_slugs=("test-wolf", "cave-bear"),
        )
        self.assertEqual(battle.combatants.count(), 4)
        beast_rows = battle.combatants.exclude(beast=None)
        self.assertEqual(
            {row.beast.slug for row in beast_rows}, {"test-wolf", "cave-bear"}
        )
        for row in beast_rows:
            self.assertIsNone(row.character)
        state = BattleState.from_dict(battle.state_json)
        beasts = [combatant for combatant in state.combatants if combatant.is_beast]
        self.assertEqual(len(beasts), 2)
        by_name = {combatant.name: combatant for combatant in beasts}
        self.assertEqual(by_name["Cave Bear"].size_hexes, 3)
        self.assertEqual(by_name["Test Wolf"].weapon.name, "Bite")
        self.assertEqual(by_name["Test Wolf"].mana, 0)

    def test_random_beast_draft_is_seed_deterministic(self):
        user = self.create_dm()
        first = create_battle(
            user, name="Draft A", count=2, seed=GOLDEN_SEED, beast_count=2
        )
        second = create_battle(
            user, name="Draft B", count=2, seed=GOLDEN_SEED, beast_count=2
        )
        picks_a = [row.beast.slug for row in first.combatants.exclude(beast=None)]
        picks_b = [row.beast.slug for row in second.combatants.exclude(beast=None)]
        self.assertEqual(picks_a, picks_b)
        self.assertEqual(len(picks_a), 2)

    def test_duplicate_picks_get_numbered_display_names(self):
        user = self.create_dm()
        battle = create_battle(
            user,
            name="Pack",
            count=0,
            seed=GOLDEN_SEED,
            beast_slugs=("test-wolf", "test-wolf"),
        )
        names = [row.display_name for row in battle.combatants.all()]
        self.assertEqual(names, ["Test Wolf", "Test Wolf (2)"])

    def test_unknown_slug_raises(self):
        from bestiary.models import Beast

        user = self.create_dm()
        with self.assertRaises(Beast.DoesNotExist):
            create_battle(
                user, name="Ghost", count=2, seed=1, beast_slugs=("no-such-beast",)
            )

    def test_total_field_size_is_bounded(self):
        user = self.create_dm()
        with self.assertRaises(ValueError):
            create_battle(user, name="Lonely", count=1, seed=1)
        with self.assertRaises(ValueError):
            create_battle(
                user, name="Horde", count=12, seed=1, beast_slugs=("test-wolf",)
            )


class GoldenBeastBattleTest(BaseTestCase):
    """Same seed ⇒ identical mixed battle, and the beasts actually roll."""

    def run_battle(self, name):
        user = self.create_dm(username=f"dm-{name}")
        # The battle seed alone fixes this roster too (#261) — see
        # GoldenBattleTest.run_battle.
        battle = create_battle(
            user,
            name=name,
            count=2,
            seed=GOLDEN_SEED,
            beast_slugs=("test-wolf", "cave-bear"),
        )
        turns = 0
        while battle.status != Battle.STATUS_FINISHED and turns < 60:
            battle, _events = advance_turn(battle.pk)
            turns += 1
        events = [
            (
                event.turn,
                event.phase,
                event.sequence,
                event.event_type,
                event.actor.replace(name, "X"),
                event.message.replace(name, "X"),
            )
            for event in BattleEvent.objects.filter(battle=battle)
        ]
        return battle, events

    def setUp(self):
        super().setUp()
        make_beast()
        make_beast(
            slug="cave-bear",
            name="Cave Bear",
            strength=30,
            size_hexes=3,
            movement=8,
            attacks=[
                {"name": "Claws", "damage": "2d6+1", "weapon_class": "Heavy Striking"}
            ],
        )

    def test_same_seed_same_event_stream_with_beasts(self):
        battle_a, events_a = self.run_battle("BeastGoldA")
        battle_b, events_b = self.run_battle("BeastGoldB")
        self.assertEqual(events_a, events_b)
        self.assertEqual(battle_a.status, battle_b.status)
        beast_rolls = [
            event
            for event in events_a
            if event[3] == "roll" and event[4] in ("Test Wolf", "Cave Bear")
        ]
        self.assertTrue(beast_rolls, "no beast ever rolled")
        beast_decisions = [
            event
            for event in events_a
            if event[3] == "decision" and event[4] in ("Test Wolf", "Cave Bear")
        ]
        self.assertTrue(beast_decisions, "no beast decision was logged")


class RosterNamingTest(BaseTestCase):
    """Generated combatants get unique fantasy names, seed-deterministic."""

    def test_display_names_are_unique_within_a_battle(self):
        user = self.create_dm()
        make_beast()
        battle = create_battle(
            user,
            name="Named",
            count=4,
            seed=GOLDEN_SEED,
            beast_slugs=("test-wolf", "test-wolf"),
        )
        names = [row.display_name for row in battle.combatants.all()]
        self.assertEqual(len(names), 6)
        self.assertEqual(len(set(names)), 6)

    def test_same_seed_draws_the_same_display_names(self):
        user = self.create_dm()
        first = create_battle(user, name="Rerun A", count=4, seed=GOLDEN_SEED)
        second = create_battle(user, name="Rerun B", count=4, seed=GOLDEN_SEED)
        self.assertEqual(
            [row.display_name for row in first.combatants.all()],
            [row.display_name for row in second.combatants.all()],
        )

    def test_character_display_names_are_fantasy_names_not_archetype_labels(self):
        user = self.create_dm()
        battle = create_battle(user, name="Styled", count=2, seed=GOLDEN_SEED)
        for row in battle.combatants.all():
            self.assertNotIn("Styled", row.display_name)
            self.assertRegex(row.display_name, r"^[A-Z][a-z]+ [A-Z][a-z]+")

    def test_state_names_match_the_combatant_rows(self):
        user = self.create_dm()
        battle = create_battle(user, name="Mirror", count=3, seed=GOLDEN_SEED)
        state = BattleState.from_dict(battle.state_json)
        by_id = {row.pk: row.display_name for row in battle.combatants.all()}
        for combatant in state.combatants:
            self.assertEqual(combatant.name, by_id[combatant.combatant_id])


class RenameCombatantTest(BaseTestCase):
    def make_battle(self, count=2):
        import random as random_module

        user = self.create_dm()
        random_module.seed(99)
        return create_battle(user, name="Rename", count=count, seed=GOLDEN_SEED)

    def test_rename_updates_row_and_state(self):
        battle = self.make_battle()
        row = battle.combatants.first()
        rename_combatant(battle.pk, row.pk, "Grimble Thorn")
        row.refresh_from_db()
        self.assertEqual(row.display_name, "Grimble Thorn")
        battle.refresh_from_db()
        state = BattleState.from_dict(battle.state_json)
        self.assertEqual(state.by_id(row.pk).name, "Grimble Thorn")

    def test_rename_rejects_a_duplicate_name_in_the_battle(self):
        battle = self.make_battle()
        first, second = battle.combatants.all()
        with self.assertRaises(ValueError):
            rename_combatant(battle.pk, second.pk, first.display_name)

    def test_rename_rejects_a_combatant_from_another_battle(self):
        user = self.create_dm()
        battle = create_battle(user, name="Rename A", count=2, seed=GOLDEN_SEED)
        other_battle = create_battle(user, name="Rename B", count=2, seed=GOLDEN_SEED)
        foreign_row = other_battle.combatants.first()
        with self.assertRaises(ValueError):
            rename_combatant(battle.pk, foreign_row.pk, "Interloper")

    def test_rename_rejects_an_unknown_combatant(self):
        battle = self.make_battle()
        with self.assertRaises(ValueError):
            rename_combatant(battle.pk, 424242, "Interloper")

    def test_future_events_use_the_new_name_and_past_text_stays(self):
        battle = self.make_battle(count=3)
        battle, _events = advance_turn(battle.pk)
        self.assertNotEqual(battle.status, Battle.STATUS_FINISHED)
        past_messages = list(
            BattleEvent.objects.filter(battle=battle).values_list("pk", "message")
        )
        row = battle.combatants.first()
        old_name = row.display_name
        rename_combatant(battle.pk, row.pk, "Zanzibar Nightfield")
        battle, _events = advance_turn(battle.pk)
        new_name_events = BattleEvent.objects.filter(
            battle=battle, turn=battle.turn_number, actor="Zanzibar Nightfield"
        )
        self.assertTrue(new_name_events.exists())
        self.assertFalse(
            BattleEvent.objects.filter(
                battle=battle, turn=battle.turn_number, actor=old_name
            ).exists()
        )
        # Historical event text is untouched by the rename.
        for event_pk, message in past_messages:
            self.assertEqual(BattleEvent.objects.get(pk=event_pk).message, message)


class RestartBattleTest(BaseTestCase):
    """Restart replays from turn 0 with the same roster and seed (issue #188)."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.battle = create_battle(
            self.dm, name="Restarted", count=2, seed=GOLDEN_SEED
        )

    def test_creation_snapshots_the_starting_state(self):
        self.assertEqual(self.battle.initial_state_json, self.battle.state_json)

    def test_restart_rewinds_turn_status_and_state(self):
        advance_turn(self.battle.pk)
        advanced = Battle.objects.get(pk=self.battle.pk)
        self.assertGreater(advanced.turn_number, 0)
        self.assertTrue(BattleEvent.objects.filter(battle=advanced).exists())

        restarted = restart_battle(self.battle.pk)
        self.assertEqual(restarted.turn_number, 0)
        self.assertEqual(restarted.status, Battle.STATUS_SETUP)
        self.assertEqual(restarted.state_json, self.battle.initial_state_json)
        self.assertFalse(BattleEvent.objects.filter(battle=restarted).exists())

    def test_restart_replays_the_same_battle(self):
        """Same roster, same seed: turn 1 after a restart is turn 1 again."""
        _battle, first_events = advance_turn(self.battle.pk)
        first_messages = [event.message for event in first_events]

        restart_battle(self.battle.pk)
        _battle, replayed_events = advance_turn(self.battle.pk)

        self.assertEqual([event.message for event in replayed_events], first_messages)

    def test_restart_keeps_renamed_combatants(self):
        combatant_id = self.battle.combatants.first().pk
        rename_combatant(self.battle.pk, combatant_id, "Torin Blackbriar")
        advance_turn(self.battle.pk)

        restarted = restart_battle(self.battle.pk)

        state = BattleState.from_dict(restarted.state_json)
        self.assertEqual(state.by_id(combatant_id).name, "Torin Blackbriar")
        self.assertEqual(
            restarted.combatants.get(pk=combatant_id).display_name, "Torin Blackbriar"
        )

    def test_restart_without_a_snapshot_is_refused(self):
        """A battle created before the snapshot field has nothing to rewind to."""
        Battle.objects.filter(pk=self.battle.pk).update(initial_state_json={})
        with self.assertRaises(ValueError) as raised:
            restart_battle(self.battle.pk)
        self.assertIn("no starting snapshot", str(raised.exception))

    def test_restart_of_a_finished_battle_is_allowed(self):
        Battle.objects.filter(pk=self.battle.pk).update(
            status=Battle.STATUS_FINISHED, turn_number=9
        )
        restarted = restart_battle(self.battle.pk)
        self.assertEqual(restarted.status, Battle.STATUS_SETUP)
        self.assertEqual(restarted.turn_number, 0)


class RosterDeterminismTest(BaseTestCase):
    """Same seed ⇒ the same roster, rolled stats and weapons included (#261).

    Deliberately does NOT pin ``random.seed``: the point of the issue is that
    the battle's own seed alone must fix the roster. A pin here would hide the
    very regression these tests exist to catch.
    """

    def roster_fingerprint(self, battle):
        """Everything about a battle's generated roster that a seed must fix.

        Args:
            battle: The battle whose combatants to fingerprint.

        Returns:
            A list of one tuple per combatant, in seating order, carrying the
            display name, the rolled core attributes, the derived weapon and
            skills, and the full dice log — so a difference in any single roll
            shows up as a difference in the fingerprint.
        """
        fingerprint = []
        for row in battle.combatants.select_related("character").order_by("id"):
            character = row.character
            if character is None:
                continue
            fingerprint.append(
                (
                    row.display_name,
                    character.strength,
                    character.dexterity,
                    character.intelligence,
                    character.wisdom,
                    character.constitution,
                    character.charisma,
                    character.wealth_level,
                    tuple(character.equipment or ()),
                    tuple(sorted((character.skills or {}).items())),
                    character.dice_log,
                )
            )
        return fingerprint

    def test_same_seed_rolls_the_same_characters(self):
        user = self.create_dm()
        first = create_battle(user, name="Twin A", count=4, seed=GOLDEN_SEED)
        second = create_battle(user, name="Twin B", count=4, seed=GOLDEN_SEED)
        self.assertEqual(
            self.roster_fingerprint(first),
            self.roster_fingerprint(second),
        )

    def test_different_seeds_roll_different_characters(self):
        user = self.create_dm()
        first = create_battle(user, name="Fork A", count=4, seed=GOLDEN_SEED)
        second = create_battle(user, name="Fork B", count=4, seed=GOLDEN_SEED + 1)
        self.assertNotEqual(
            self.roster_fingerprint(first),
            self.roster_fingerprint(second),
        )

    def test_a_roster_is_stable_when_it_grows(self):
        """Adding a combatant leaves the earlier ones' rolls untouched.

        This is what per-character seed derivation buys over one shared
        roller: a GM who bumps the count from three to four gets the same
        first three fighters, not a wholly redrawn roster.
        """
        user = self.create_dm()
        smaller = create_battle(user, name="Grow A", count=3, seed=GOLDEN_SEED)
        larger = create_battle(user, name="Grow B", count=4, seed=GOLDEN_SEED)
        smaller_stats = [row[1:] for row in self.roster_fingerprint(smaller)]
        larger_stats = [row[1:] for row in self.roster_fingerprint(larger)]
        self.assertEqual(smaller_stats, larger_stats[: len(smaller_stats)])
