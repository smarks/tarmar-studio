"""Issue #18: a caster's school of magic is rolled, chosen, and gates spells.

schools-of-magic.md heads its lists "Common Schools (0-70%)" and "Less Common
Schools (71-100%)". The canon's ``schools`` block carries them; the d100 roll
lands on a row, the player chooses one of its schools, and a player learns a
new spell only from that school. A Game Master may roll again, set any school
and teach any spell, each recorded.

Most tests seat an invented table (``battle.tests.magic_fixtures``), so they
hold under any canon; the sheet test at the end reads the loaded canon's.
"""

import json
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.urls import reverse

from battle.magic_rules import magic_schools
from battle.tests.magic_fixtures import seated_schools
from character_export.export_utils import (
    generate_character_json,
    generate_character_markdown,
)
from characters.magic import (
    choose_school,
    learn_spell,
    schools_to_choose,
    spells_open_to,
)
from characters.models import CHARACTER_FIELDS, Character
from characters.services import (
    CharacterChangeError,
    apply_school_roll,
    can_roll_school,
    roll_on_sheet,
    sheet_rolls_for,
)
from common.test_helpers import BaseTestCase, create_character

#: Where the module-default roller draws its faces (characters.dice's note).
RANDINT = "characters.dice.random.randint"


class SchoolTestCase(BaseTestCase):
    """A player's would-be caster, under the invented school table."""

    def setUp(self):
        super().setUp()
        self.enterContext(seated_schools())
        self.owner = self.create_user("caster")
        self.character = create_character(
            self.owner, name="Caster", free_skill_points=4
        )

    def roll(self, face: int, *, is_game_master: bool = False) -> str:
        with patch(RANDINT, return_value=face):
            return apply_school_roll(self.character, is_game_master=is_game_master)


class SchoolRollTest(SchoolTestCase):
    def test_the_roll_records_the_d100_and_leaves_the_choice(self):
        note = self.roll(23)
        self.assertEqual(self.character.magic_school_roll, 23)
        self.assertEqual(self.character.magic_school, "")
        self.assertIn("d100=23 -> Usual (Elemental, Mending)", note)
        self.assertIn("choose one of them", note)
        self.assertIn(
            "School of magic: d100=23 -> Usual (Elemental, Mending)",
            self.character.dice_log,
        )

    def test_a_row_naming_one_school_gives_it_outright(self):
        note = self.roll(41)
        self.assertEqual(self.character.magic_school, "Protection")
        self.assertIn("the school is Protection", note)

    def test_a_player_rolls_once(self):
        self.roll(23)
        self.assertFalse(can_roll_school(self.character, is_game_master=False))
        with self.assertRaises(CharacterChangeError):
            self.roll(41)
        self.assertEqual(self.character.magic_school_roll, 23)

    def test_a_player_may_not_roll_over_a_school_a_game_master_set(self):
        choose_school(self.character, "Control", is_game_master=True)
        self.assertFalse(can_roll_school(self.character, is_game_master=False))
        with self.assertRaises(CharacterChangeError) as refused:
            self.roll(23)
        self.assertIn("A Game Master set", str(refused.exception))
        self.assertEqual(self.character.magic_school, "Control")

    def test_a_game_master_rolls_again_and_keeps_a_school_still_listed(self):
        self.roll(23)
        choose_school(self.character, "Mending", is_game_master=False)
        note = self.roll(5, is_game_master=True)
        self.assertEqual(
            (self.character.magic_school_roll, self.character.magic_school),
            (5, "Mending"),
        )
        self.assertIn("the Mending school stands", note)

    def test_a_game_master_roll_clears_a_school_the_new_row_does_not_name(self):
        self.roll(23)
        choose_school(self.character, "Mending", is_game_master=False)
        note = self.roll(77, is_game_master=True)
        self.assertEqual(self.character.magic_school, "")
        self.assertIn("the Mending school, not on this list, is cleared", note)

    def test_a_canon_with_no_table_has_nothing_to_roll(self):
        with seated_schools(None):
            self.assertFalse(can_roll_school(self.character, is_game_master=True))
            with self.assertRaises(CharacterChangeError):
                self.roll(23, is_game_master=True)


class ChooseSchoolTest(SchoolTestCase):
    def test_a_player_chooses_from_the_rolled_row_once(self):
        self.assertEqual(schools_to_choose(self.character, is_game_master=False), ())
        with self.assertRaises(CharacterChangeError) as refused:
            choose_school(self.character, "Elemental", is_game_master=False)
        self.assertIn("Roll the school of magic first", str(refused.exception))
        self.roll(90)
        self.assertEqual(
            schools_to_choose(self.character, is_game_master=False),
            ("Passage", "Control", "Sky"),
        )
        with self.assertRaises(CharacterChangeError) as refused:
            choose_school(self.character, "Elemental", is_game_master=False)
        self.assertIn("opens Passage, Control, Sky", str(refused.exception))
        line = choose_school(self.character, "Control", is_game_master=False)
        self.assertEqual(line, "school of magic unchosen → Control")
        self.assertEqual(schools_to_choose(self.character, is_game_master=False), ())
        with self.assertRaises(CharacterChangeError):
            choose_school(self.character, "Passage", is_game_master=False)
        self.assertEqual(self.character.magic_school, "Control")

    def test_a_game_master_sets_any_school_and_it_is_recorded(self):
        self.roll(90)
        line = choose_school(self.character, "Elemental", is_game_master=True)
        self.assertEqual(self.character.magic_school, "Elemental")
        self.assertIn("outside the Rarer schools the roll opened", line)
        unrolled = create_character(self.owner, name="Unrolled")
        line = choose_school(unrolled, "Mending", is_game_master=True)
        self.assertIn("with no school roll", line)
        self.assertNotIn("Mending", schools_to_choose(unrolled, is_game_master=True))

    def test_a_school_the_table_does_not_name_is_refused_for_everyone(self):
        with self.assertRaises(CharacterChangeError):
            choose_school(self.character, "Necromancy", is_game_master=True)
        choose_school(self.character, "Sky", is_game_master=True)
        with self.assertRaises(CharacterChangeError):
            choose_school(self.character, "Sky", is_game_master=True)


class SchoolGatesSpellsTest(SchoolTestCase):
    def test_a_player_without_a_school_learns_no_new_spell(self):
        self.assertEqual(spells_open_to(self.character, is_game_master=False), [])
        with self.assertRaises(CharacterChangeError) as refused:
            learn_spell(self.character, "fire_missile", is_game_master=False)
        self.assertIn("Roll and choose a school of magic first", str(refused.exception))
        self.assertEqual(self.character.free_skill_points, 4)

    def test_a_player_learns_only_their_schools_spells(self):
        self.roll(23)
        choose_school(self.character, "Mending", is_game_master=False)
        self.assertEqual(
            [
                spell.key
                for spell in spells_open_to(self.character, is_game_master=False)
            ],
            ["heal"],
        )
        with self.assertRaises(CharacterChangeError) as refused:
            learn_spell(self.character, "fire_missile", is_game_master=False)
        self.assertIn(
            "Fire Missile is of the Elemental school and this character's is Mending",
            str(refused.exception),
        )
        learn_spell(self.character, "heal", is_game_master=False)
        self.assertEqual(self.character.spells, {"heal": 1})

    def test_a_spell_already_held_may_be_raised_whatever_its_school(self):
        self.roll(23)
        choose_school(self.character, "Mending", is_game_master=False)
        line = learn_spell(self.character, "fire_missile", is_game_master=True)
        self.assertIn("(Game Master, outside the caster's school: Mending)", line)
        learn_spell(self.character, "fire_missile", is_game_master=False)
        self.assertEqual(self.character.spells, {"fire_missile": 2})
        raised = learn_spell(self.character, "fire_missile", is_game_master=True)
        self.assertNotIn("outside the caster's school", raised)

    def test_a_game_master_teaches_any_spell(self):
        self.assertEqual(
            len(spells_open_to(self.character, is_game_master=True)),
            len(spells_open_to(Character(), is_game_master=True)),
        )
        line = learn_spell(self.character, "wound", is_game_master=True)
        self.assertIn("outside the caster's school: unchosen", line)

    def test_a_canon_with_no_table_gates_nothing(self):
        with seated_schools(None):
            learn_spell(self.character, "fire_missile", is_game_master=False)
            self.assertEqual(self.character.spells, {"fire_missile": 1})
            with self.assertRaises(CharacterChangeError):
                choose_school(self.character, "Elemental", is_game_master=True)


class SchoolEndpointsTest(SchoolTestCase):
    def test_a_player_rolls_and_chooses_once_on_the_sheet(self):
        roll_url = reverse("characters:roll", args=[self.character.pk, "magic_school"])
        choose_url = reverse("characters:choose_school", args=[self.character.pk])
        self.login_as(self.owner)
        with patch(RANDINT, return_value=23):
            rolled = self.post_json(roll_url, {})
        self.assertEqual(rolled.status_code, 200, rolled.content)
        self.assertFalse(rolled.json()["can_roll"])
        self.assertEqual(self.post_json(roll_url, {}).status_code, 400)
        self.assertEqual(
            self.post_json(choose_url, {"school": "Control"}).status_code, 400
        )
        self.assertEqual(
            self.post_json(choose_url, {"school": "Elemental"}).status_code, 200
        )
        self.assertEqual(
            self.post_json(choose_url, {"school": "Mending"}).status_code, 400
        )
        self.character.refresh_from_db()
        self.assertEqual(
            (self.character.magic_school_roll, self.character.magic_school),
            (23, "Elemental"),
        )
        fields = [entry["field"] for entry in self.character.history]
        self.assertEqual(fields.count("magic_school_roll"), 1)
        # The roll moved only the roll; the choice is the school's one line.
        self.assertEqual(fields.count("magic_school"), 1)

    def test_a_game_master_rolls_again_through_the_sheet_roll(self):
        self.roll(23)
        self.character.save()
        self.assertTrue(
            sheet_rolls_for(self.character, is_game_master=True)["magic_school"].confirm
        )
        with patch(RANDINT, return_value=50):
            roll_on_sheet(self.character.pk, "magic_school", is_game_master=True)
        self.character.refresh_from_db()
        self.assertEqual(
            (self.character.magic_school_roll, self.character.magic_school),
            (50, "Protection"),
        )

    def test_a_field_update_sets_neither(self):
        self.login_as(self.create_dm())
        url = reverse("characters:update_field", args=[self.character.pk])
        for field_name, value in (("magic_school", "Sky"), ("magic_school_roll", 5)):
            with self.subTest(field_name):
                response = self.post_json(url, {"field": field_name, "value": value})
                self.assertEqual(response.status_code, 400)
                # Not a field the sheet's editor takes from anyone, a Game
                # Master included: the roll and the choice are the only paths.
                self.assertNotIn(field_name, CHARACTER_FIELDS)
                self.assertEqual(response.json()["error"], "Invalid field")
        self.character.refresh_from_db()
        self.assertEqual(
            (self.character.magic_school_roll, self.character.magic_school),
            (None, ""),
        )


class SchoolRecordTest(BaseTestCase):
    """The record: its bounds, the export, and who an import keeps it for."""

    def test_the_record_holds_only_what_the_table_and_the_dice_give(self):
        character = create_character(self.create_user("bounds"), name="Bounds")
        with seated_schools():
            for field_name, value in (
                ("magic_school", "Necromancy"),
                ("magic_school_roll", 0),
                ("magic_school_roll", 101),
            ):
                with self.subTest(field_name=field_name, value=value):
                    setattr(character, field_name, value)
                    with self.assertRaises(ValidationError):
                        character.full_clean()
                    character.refresh_from_db()
            with seated_schools(None):
                character.magic_school = "Elemental"
                with self.assertRaises(ValidationError):
                    character.full_clean()


class LoadedTableTestCase(BaseTestCase):
    """A test of the loaded canon's own school table, skipped under a canon
    whose ``schools`` block is null, which has no table to roll on."""

    def setUp(self):
        super().setUp()
        table = magic_schools()
        if table is None:
            self.skipTest(
                "the loaded canon's schools block is null: it gates no spell by "
                "school, so there is no table to roll on"
            )
        self.table = table


class SchoolRollEdgesTest(LoadedTableTestCase):
    """Each row's lowest and highest face land on it: on both canons today,
    1 and 70 on the Common schools, 71 and 100 on the Less Common."""

    def test_each_band_edge_lands_on_its_row(self):
        character = create_character(self.create_user("edges"), name="Edges")
        lowest = 1
        for row in self.table.rows:
            for face in (lowest, row.up_to):
                with self.subTest(row=row.label, face=face):
                    character.magic_school_roll = None
                    character.magic_school = ""
                    with patch(RANDINT, return_value=face):
                        note = apply_school_roll(character, is_game_master=False)
                    self.assertEqual(character.magic_school_roll, face)
                    self.assertIn(
                        f"d100={face} -> {row.label} ({', '.join(row.schools)})",
                        note,
                    )
                    self.assertEqual(
                        schools_to_choose(character, is_game_master=False),
                        row.schools if len(row.schools) > 1 else (),
                    )
            lowest = row.up_to + 1


class SchoolExportImportTest(LoadedTableTestCase):
    def test_the_export_carries_the_school_and_an_import_keeps_it_for_a_dm(self):
        table = self.table
        school = table.rows[0].schools[0]
        source = create_character(
            self.create_user("exporter"),
            name="Exported Caster",
            magic_school_roll=table.rows[0].up_to,
            magic_school=school,
        )
        data = json.loads(generate_character_json(source))["data"]
        self.assertEqual(
            (data["magic_school_roll"], data["magic_school"]),
            (table.rows[0].up_to, school),
        )
        self.assertIn(
            f"**School of Magic:** {school}", generate_character_markdown(source)
        )
        payload = generate_character_json(source).encode()
        from django.core.files.uploadedfile import SimpleUploadedFile

        imported = {}
        for label, user in (
            ("player", self.create_user("importing_player")),
            ("dm", self.create_dm()),
        ):
            self.login_as(user)
            upload = SimpleUploadedFile("c.json", payload, "application/json")
            response = self.client.post(reverse("characters:import"), {"file": upload})
            self.assertEqual(response.status_code, 200, response.content)
            imported[label] = Character.objects.get(pk=response.json()["character_id"])
            self.logout()
        self.assertEqual(
            (imported["player"].magic_school_roll, imported["player"].magic_school),
            (None, ""),
        )
        self.assertEqual(
            (imported["dm"].magic_school_roll, imported["dm"].magic_school),
            (table.rows[0].up_to, school),
        )


class SchoolOnTheSheetTest(LoadedTableTestCase):
    """The sheet offers the loaded canon's roll, its row's schools, and only
    the school's spells to learn."""

    def test_the_sheet_walks_a_player_through_roll_choice_and_spells(self):
        table = self.table
        owner = self.create_user("sheet_caster")
        character = create_character(owner, name="Sheet Caster", free_skill_points=2)
        url = reverse("characters:edit", args=[character.pk])
        self.login_as(owner)
        page = self.client.get(url)
        self.assertContains(page, 'id="roll-magic-school-btn"')
        self.assertContains(page, 'id="learn-spell-note"')
        self.assertNotContains(page, 'id="choose-school-select"')
        self.assertNotContains(page, 'id="learn-spell-select"')

        row = table.rows[0]
        character.magic_school_roll = row.up_to
        character.save()
        page = self.client.get(url)
        self.assertNotContains(page, 'id="roll-magic-school-btn"')
        for school in row.schools:
            self.assertContains(page, f'<option value="{school}">{school}</option>')
        self.assertContains(page, f"(d100 {row.up_to}: {row.label}")

        school = next(
            school
            for school in row.schools
            if any(
                spell.school == school
                for spell in spells_open_to(Character(), is_game_master=True)
            )
        )
        character.magic_school = school
        character.save()
        page = self.client.get(url)
        self.assertContains(page, f'<strong id="magic-school">{school}</strong>')
        self.assertNotContains(page, 'id="choose-school-select"')
        offered = spells_open_to(character, is_game_master=False)
        self.assertTrue(offered)
        for spell in spells_open_to(Character(), is_game_master=True):
            option = (
                f'<option value="{spell.key}">{spell.name} '
                f"({spell.school} {spell.level})</option>"
            )
            if spell in offered:
                self.assertContains(page, option)
            else:
                self.assertNotContains(page, option)
