"""``characters.services``: the one place a character changes (issue #571)."""

from unittest.mock import patch

from django.core.exceptions import PermissionDenied

from characters import services
from characters.dice import DICE_IN_TEXT
from characters.models import Character
from characters.services import CharacterChangeError
from characters.skills import normalize_skill_name
from common.test_helpers import BaseTestCase, create_character

SURVIVE = patch("characters.skills.roll_survival_dice", return_value=(6, 6, 6, 18))


class FatigueRollTest(BaseTestCase):
    """The stated Fatigue roll is held to what 2d6 can produce (issue #653)."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("fatigue-roll-owner")
        self.character = create_character(self.owner, name="Roller")

    def test_a_stated_roll_inside_the_band_is_recorded(self):
        for stated in range(2, 13):
            with self.subTest(value=stated):
                note = services.apply_fatigue_roll(self.character, stated)
                self.assertEqual(self.character.fatigue_roll, stated)
                self.assertIn(str(stated), note)

    def test_a_stated_roll_two_dice_cannot_produce_is_refused(self):
        """apply_fatigue_roll did no range check at all, so the Games Master's
        stated-value path set Fatigue and Body to anything it was told."""
        self.character.fatigue_roll = 7
        for stated in (-1, 0, 1, 13, 5000):
            with self.subTest(value=stated):
                with self.assertRaises(CharacterChangeError) as refusal:
                    services.apply_fatigue_roll(self.character, stated)
                self.assertIn("2d6", str(refusal.exception))
                self.assertEqual(self.character.fatigue_roll, 7)


class ChangingTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user("services-owner")
        self.character = create_character(self.owner, name="Wren")

    def test_a_change_is_saved_with_its_history_line(self):
        with services.changing(self.character.pk, actor=self.owner) as change:
            change.character.age = 30
            change.note("age", 16, 30)

        self.character.refresh_from_db()
        self.assertEqual(self.character.age, 30)
        self.assertTrue(self.character.is_played)
        entry = self.character.history[-1]
        self.assertEqual(
            (entry["field"], entry["old"], entry["new"], entry["by"]),
            ("age", "16", "30", "services-owner"),
        )

    def test_a_change_without_an_actor_is_the_systems(self):
        with services.changing(self.character.pk) as change:
            change.note("age", 21, 22)

        self.character.refresh_from_db()
        self.assertEqual(self.character.history[-1]["by"], services.UNATTRIBUTED)
        self.assertFalse(self.character.is_played)

    def test_a_refusal_inside_the_block_leaves_the_row_as_it_was(self):
        with self.assertRaises(CharacterChangeError):
            with services.changing(self.character.pk, actor=self.owner) as change:
                change.character.age = 99
                raise CharacterChangeError("no")

        self.character.refresh_from_db()
        self.assertNotEqual(self.character.age, 99)

    def test_a_discarded_change_saves_nothing(self):
        with services.changing(self.character.pk, actor=self.owner) as change:
            change.character.age = 99
            change.discard()

        self.character.refresh_from_db()
        self.assertNotEqual(self.character.age, 99)

    def test_authorize_refuses_before_anything_changes(self):
        def refuse(_character: Character) -> None:
            raise PermissionDenied

        with self.assertRaises(PermissionDenied):
            services.rename(self.character.pk, "Other", authorize=refuse)

        self.character.refresh_from_db()
        self.assertEqual(self.character.name, "Wren")

    def test_a_deleted_character_is_not_found(self):
        Character.objects.filter(pk=self.character.pk).update(is_deleted=True)

        with self.assertRaises(Character.DoesNotExist):
            services.rename(self.character.pk, "Other")


class LockedCallsTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user("services-calls")
        self.character = create_character(self.owner, name="Wren")

    def test_a_rename_keeps_the_row_id(self):
        result = services.rename(self.character.pk, "Twain", actor=self.owner)

        self.assertEqual(result.character.pk, self.character.pk)
        self.assertEqual(Character.objects.get(pk=self.character.pk).name, "Twain")
        self.assertEqual(result.note, "named Twain")

    def test_set_attribute_holds_a_player_to_the_limits(self):
        with self.assertRaisesMessage(CharacterChangeError, "for a player"):
            services.set_attribute(self.character.pk, "strength", 25)

    def test_set_attribute_lets_a_game_master_past_them(self):
        result = services.set_attribute(
            self.character.pk, "strength", 25, is_game_master=True
        )

        self.assertEqual(result.character.strength, 25)
        self.assertIn("the Game Master's call", result.note)

    def test_set_attribute_all_records_six(self):
        result = services.set_attribute(self.character.pk, "all", 12)

        character = result.character
        self.assertEqual(
            {character.strength, character.dexterity, character.charisma}, {12}
        )

    def test_an_unknown_attribute_is_refused(self):
        with self.assertRaisesMessage(CharacterChangeError, "not a core attribute"):
            services.set_attribute(self.character.pk, "luck", 12)

    def test_an_impossible_score_is_refused_for_everyone(self):
        with self.assertRaisesMessage(CharacterChangeError, "the most a character"):
            services.set_attribute(
                self.character.pk, "strength", 99, is_game_master=True
            )

    @patch("characters.services.roll_expression", return_value=([3, 4], 7))
    def test_the_server_rolls_fatigue(self, _roll):
        # The character has a roll; only a Game Master rolls again (#748
        # made one roll the default for every caller).
        result = services.roll_fatigue(self.character.pk, is_game_master=True)

        self.assertEqual(result.character.fatigue_roll, 7)
        self.assertEqual(result.note, "Fatigue roll (2d6) rolled 3 and 4 = 7")

    def test_a_stated_fatigue_roll_is_recorded(self):
        result = services.roll_fatigue(self.character.pk, value=9, is_game_master=True)

        self.assertEqual(result.character.fatigue_roll, 9)

    def test_revive_brings_a_dead_character_back(self):
        Character.objects.filter(pk=self.character.pk).update(
            is_alive=False, death_cause="fell"
        )

        result = services.revive(self.character.pk)

        self.assertTrue(result.character.is_alive)
        self.assertEqual(result.note, "brought back to life (had died: fell)")

    def test_revive_refuses_the_living(self):
        with self.assertRaisesMessage(CharacterChangeError, "is alive"):
            services.revive(self.character.pk)

    def test_revive_without_a_cause(self):
        Character.objects.filter(pk=self.character.pk).update(is_alive=False)

        self.assertEqual(
            services.revive(self.character.pk).note, "brought back to life"
        )

    def test_add_equipment_stacks_a_named_item(self):
        services.add_equipment(self.character.pk, "Rope", 1)
        result = services.add_equipment(self.character.pk, "rope", 2)

        self.assertEqual(result.character.equipment, [{"name": "Rope", "quantity": 3}])
        self.assertEqual(result.note, "now carrying 3 × Rope")

    def test_add_equipment_refuses_nothing(self):
        with self.assertRaises(CharacterChangeError):
            services.add_equipment(self.character.pk, "Rope", 0)

    def test_allocate_skill_points_spends_free_points(self):
        Character.objects.filter(pk=self.character.pk).update(free_skill_points=3)

        result = services.allocate_skill_points(self.character.pk, "sword", 2)

        self.assertEqual(result.character.free_skill_points, 1)
        self.assertEqual(result.character.skills[normalize_skill_name("sword")], 2)

    def test_a_player_cannot_overspend(self):
        with self.assertRaisesMessage(CharacterChangeError, "free skill point(s)"):
            services.allocate_skill_points(self.character.pk, "sword", 2)

    def test_a_game_master_overspend_is_granted(self):
        result = services.allocate_skill_points(
            self.character.pk, "sword", 2, is_game_master=True
        )

        self.assertEqual(result.granted, 2)
        self.assertIn("granted by the Game Master", result.note)

    def test_a_skill_purchase_is_at_least_one_point(self):
        with self.assertRaises(CharacterChangeError):
            services.allocate_skill_points(self.character.pk, "sword", 0)

    def test_a_skill_cannot_pass_the_storage_ceiling(self):
        Character.objects.filter(pk=self.character.pk).update(
            free_skill_points=5,
            skills={normalize_skill_name("sword"): services.MAX_SKILL_POINTS},
        )

        with self.assertRaisesMessage(CharacterChangeError, "maximum storable"):
            services.allocate_skill_points(self.character.pk, "sword", 1)

    @SURVIVE
    def test_add_experience_years_plays_the_run(self, _survive):
        result = services.add_experience_years(
            self.character.pk, "campaigner", 2, actor=self.owner
        )

        self.assertIsNotNone(result.run_result)
        self.assertEqual(result.character.experience_years, 2)
        self.assertIn("played 2 of 2 year(s) on the campaigner track", result.note)

    def test_years_are_refused_for_the_dead(self):
        Character.objects.filter(pk=self.character.pk).update(is_alive=False)

        with self.assertRaisesMessage(CharacterChangeError, "Wren is dead"):
            services.add_experience_years(self.character.pk, "campaigner", 1)
        with self.assertRaisesMessage(CharacterChangeError, "bring them back"):
            services.add_experience_years(
                self.character.pk, "campaigner", 1, is_game_master=True
            )

    def test_years_need_attributes(self):
        character = Character(name="", strength=None)

        with self.assertRaises(CharacterChangeError) as refused:
            services.apply_experience_years(character, "campaigner", 1)

        self.assertEqual(
            refused.exception.reason, services.REFUSAL_ATTRIBUTES_NOT_RECORDED
        )

    def test_years_out_of_bounds_and_unknown_tracks_are_refused(self):
        with self.assertRaisesMessage(CharacterChangeError, "Years must be"):
            services.add_experience_years(self.character.pk, "campaigner", 0)
        with self.assertRaisesMessage(CharacterChangeError, "Unknown track"):
            services.add_experience_years(self.character.pk, "dancer", 1)

    def test_update_field_applies_the_derived_recompute(self):
        result = services.update_field(
            self.character.pk,
            "wealth_level",
            "Rich",
            actor=self.owner,
            is_game_master=True,
        )

        self.assertEqual(result.character.history[-1]["field"], "wealth_level")

    def test_set_background_records_each_field(self):
        result = services.set_background(
            self.character.pk,
            {"location": "City", "wealth_level": "Rich"},
            is_game_master=True,
        )

        self.assertEqual(result.character.location, "City")
        # The City origin's skill is granted with the move (#754).
        fields = [entry["field"] for entry in result.character.history[-3:]]
        self.assertEqual(fields, ["location", "skills", "wealth_level"])


class CreationTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user("services-create")
        template = create_character(self.owner, name="Template")
        self.fields = {
            field: getattr(template, field)
            for field in (
                "strength",
                "dexterity",
                "intelligence",
                "wisdom",
                "constitution",
                "charisma",
                "appearance_roll",
                "height_roll",
                "weight_roll",
                "fatigue_roll",
            )
        }

    def test_create_character_saves_the_row(self):
        character = services.create_character(self.owner, "Wren", self.fields)

        self.assertEqual(Character.objects.get(pk=character.pk).name, "Wren")

    def test_create_character_runs_the_validator_first(self):
        def refuse(_character: Character) -> None:
            raise CharacterChangeError("bad")

        with self.assertRaises(CharacterChangeError):
            services.create_character(self.owner, "Wren", self.fields, validate=refuse)
        self.assertFalse(Character.objects.filter(name="Wren").exists())


class RecordAndRollAttributesTest(BaseTestCase):
    """The attribute changes the Games Master's plans make (issue #590)."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user()
        self.character = create_character(self.owner, name="Wren")

    def test_stated_scores_are_recorded_all_or_none(self):
        before = self.character.strength
        with self.assertRaises(CharacterChangeError):
            services.record_attributes(
                self.character.pk, {"strength": 12, "dexterity": 40}
            )
        self.character.refresh_from_db()
        self.assertEqual(self.character.strength, before)

        result = services.record_attributes(
            self.character.pk, {"strength": 12, "dexterity": 9}
        )
        self.assertEqual(
            result.note, "strength recorded as 12; dexterity recorded as 9"
        )

    def test_a_player_rolls_attributes_only_while_none_are_recorded(self):
        with self.assertRaisesMessage(CharacterChangeError, "already recorded"):
            services.roll_attributes(self.character.pk)
        result = services.roll_attributes(self.character.pk, is_game_master=True)
        self.assertTrue(
            result.note.startswith("rolled the six attributes: strength 3d6: [")
        )

    def test_the_spend_note_names_both_figures_and_what_is_left(self):
        """The GM read "2 pts" as points remaining and told a player 0 (#356).

        The skill's own points sat beside the words "skill point(s)", and how
        many free points were left was not said at all, so the figure the
        next turn needs had to be worked out rather than read.
        """
        self.character.free_skill_points = 2
        self.character.save(update_fields=["free_skill_points"])

        note, _granted = services.apply_skill_points(self.character, "Sword", 1)

        self.assertIn("sword is now", note.casefold())
        self.assertIn("1 free skill point left", note)
        self.assertNotIn("pts)", note)

    def test_the_spend_note_says_points_when_more_than_one_is_left(self):
        self.character.free_skill_points = 3
        self.character.save(update_fields=["free_skill_points"])

        note, _granted = services.apply_skill_points(self.character, "Sword", 1)

        self.assertIn("2 free skill points left", note)

    def test_rolled_attribute_note_shape(self):
        """The dice in the note stay machine-readable (issue #604).

        ``scripts/gm_behaviour_check.py`` reads the dice the server rolled out
        of this text, because ``roll_attributes`` reports its rolling in a
        procedure frame rather than a ``gm_roll`` frame. When the wording here
        changes, this test fails in a second instead of the harness quietly
        losing sight of every creation roll, which is what happened before.
        """
        result = services.roll_attributes(self.character.pk, is_game_master=True)
        found = DICE_IN_TEXT.findall(result.note)
        self.assertEqual(len(found), 6, f"six rolls readable in {result.note!r}")
        for expression, faces, total in found:
            self.assertEqual(expression, "3d6")
            values = [int(face) for face in faces.split(",")]
            self.assertEqual(len(values), 3)
            self.assertEqual(sum(values), int(total))

    def test_a_player_rolls_fatigue_once_when_asked_to_hold_them_to_it(self):
        with self.assertRaisesMessage(CharacterChangeError, "Fatigue roll is already"):
            services.roll_fatigue(self.character.pk, once_for_players=True)
        services.roll_fatigue(
            self.character.pk, once_for_players=True, is_game_master=True
        )
