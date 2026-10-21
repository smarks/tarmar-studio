"""Issue #970: a rolled Origin is fixed for a player.

Spencer's ruling, 2026-10-10: "a rolled Origin is fixed for a player once
rolled, the same rule as #948: a player chooses OR rolls, and a roll is final;
a Game Master may change it, and a Game Master's change rerolls literacy
(#745)."

The one-roll record is the dice log's location-table line ("Location: d100=NN
-> City", ``services.roll_lines``), which only code writes: creation, the
Games Master's roster, the sheet's roll, and the line an import writes for an
Origin it kept from the importer's own rolled character.
"""

import json
import re
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from character_export.export_utils import generate_character_json
from characters import services
from characters.models import Character
from common.test_helpers import BaseTestCase, create_character

ROLLED_RURAL_LINE = "Location: d100=80 -> Rural"


def fixed() -> str:
    """The configured reason a rolled Origin is final (issue #85), read when
    asked so a test's own words apply."""
    return services.roll_fixed_reason("location")


def _origin_select(page: str) -> bool:
    """Whether the sheet renders the Origin as a select an editor can use."""
    return 'class="editable-field" data-field="location"' in page


def _locked_reason(page: str) -> str:
    """The persistent reason the sheet prints beside a locked Origin."""
    found = re.search(r'data-locked-field="location">([^<]*)<', page)
    return found.group(1) if found else ""


class RolledOriginFixedTest(BaseTestCase):
    """A player may not choose over a rolled Origin; a Game Master may."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("origin-fixed-player")
        self.character = create_character(
            self.player,
            location="Rural",
            skills={"survival": 1},
            origin_skill="survival",
            dice_log=ROLLED_RURAL_LINE,
        )

    def update(self, value: str):
        return self.post_json(
            reverse("characters:update_field", args=[self.character.pk]),
            {"field": "location", "value": value},
        )

    def edit_page(self, character: Character | None = None) -> str:
        target = self.character if character is None else character
        return self.client.get(
            reverse("characters:edit", args=[target.pk])
        ).content.decode()

    def assert_unchanged(self):
        self.character.refresh_from_db()
        self.assertEqual(self.character.location, "Rural")
        self.assertEqual(self.character.skills, {"survival": 1})

    def test_the_sheets_field_update_refuses_a_player(self):
        self.login_as(self.player)

        response = self.update("City")

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["error"], fixed())
        self.assert_unchanged()

    def test_the_chats_set_background_refuses_a_player(self):
        """The Games Master's set_background goes through change_field too."""
        with self.assertRaisesMessage(services.CharacterChangeError, fixed()):
            services.set_background(self.character.pk, {"location": "City"})
        self.assert_unchanged()

    def test_restating_the_rolled_origin_is_not_a_change(self):
        """set_background skips a value the record already holds, so a chat
        plan that names the rolled Origin is not refused."""
        services.set_background(self.character.pk, {"location": "Rural"})
        self.assert_unchanged()

    def test_a_player_may_not_roll_it_again(self):
        self.assertFalse(
            services.sheet_rolls_for(self.character, is_game_master=False)[
                "origin"
            ].available
        )
        with self.assertRaises(services.CharacterChangeError):
            services.roll_on_sheet(self.character.pk, "origin")
        self.assert_unchanged()

    def test_the_sheet_renders_a_rolled_origin_as_text_for_a_player(self):
        self.login_as(self.player)
        page = self.edit_page()
        self.assertFalse(_origin_select(page))
        self.assertEqual(_locked_reason(page), fixed())

    def test_the_literacy_note_is_not_printed_beside_a_rolled_origin(self):
        """Rolled and then fixed by literacy as well: one reason, the roll's,
        not the literacy select's note beside the text."""
        self.character.literacy_roll = 9
        self.character.literacy_rolled = True
        self.character.save()
        self.login_as(self.player)

        page = self.edit_page()

        self.assertEqual(_locked_reason(page), fixed())
        self.assertNotIn('id="origin-fixed-note"', page)

    def test_a_game_master_changes_it_and_literacy_is_rolled_again(self):
        """#745: a Game Master's Origin change rolls the literacy check again."""
        self.character.location = "Village"
        self.character.literacy_roll = 9
        self.character.literacy_rolled = True
        self.character.save()
        self.login_as(self.create_dm("origin-fixed-gm"))
        page = self.edit_page()
        self.assertTrue(_origin_select(page))
        self.assertEqual(_locked_reason(page), "")

        response = self.update("City")

        self.assertEqual(response.status_code, 200, response.content)
        self.character.refresh_from_db()
        self.assertEqual(self.character.location, "City")
        self.assertTrue(
            any(
                "literacy rerolled because Origin changed from Village to City"
                in str(entry)
                for entry in self.character.history
            ),
            self.character.history,
        )

    def test_a_chosen_origin_is_not_a_roll_and_stays_the_players(self):
        """Choosing writes no location-table line, so before literacy the
        choice stays open, as #741 left it."""
        chosen = create_character(
            self.player, name="Chooser", location="City", dice_log=""
        )
        for origin in ("Rural", "Village"):
            services.update_field(chosen.pk, "location", origin)
        chosen.refresh_from_db()
        self.assertEqual(chosen.location, "Village")
        self.assertFalse(services.field_rolled(chosen, "location"))
        self.login_as(self.player)
        self.assertTrue(_origin_select(self.edit_page(chosen)))

    def test_the_effects_line_is_not_a_roll(self):
        """Only the table's own line counts: "Location effects: ..." does not."""
        chosen = create_character(
            self.player,
            name="Effects",
            dice_log="Location effects: Survival 1 point, literacy 3d6 <= INT - 4",
        )
        self.assertFalse(services.field_rolled(chosen, "location"))


class OlderDiceLogsTest(BaseTestCase):
    """What a dice log written before #970 says about the Origin.

    The history of characters/ holds one writer of a location-table line,
    ``dice.roll_location_logged``, unchanged since 762446a (2026-01-10, when
    the Origin was added): "Location: d100=NN -> Origin". Before it there was
    no Origin. 72dde06 (2026-08-28) added the effects line, "Location effects:
    ...", which is no roll. Nothing in characters/ ever wrote another
    "Location: " line into a dice log; the "Location: City" lines in older
    gm/ tests were Games Master replies checked against the ledger, not dice.
    """

    def setUp(self):
        super().setUp()
        self.player = self.create_user("older-log-player")

    def test_a_creation_log_from_before_the_lock_counts_as_the_roll(self):
        """A character created before #970, whose player then chose another
        Origin over the roll (#754 allowed it), keeps the roll on record, so
        the Origin it holds is now fixed for the player."""
        older = create_character(
            self.player,
            location="City",
            dice_log=(
                "Strength: 3d6 = 4+4+4 = 12\n"
                "Provenance: d100=40 -> Commoner\n"
                "Location: d100=80 -> Rural\n"
                "Location effects: Survival 1 point, literacy 3d6 <= INT - 4\n"
                "Wealth: d100=50 -> Moderate"
            ),
        )

        self.assertEqual(
            services.roll_lines(older.dice_log, "location"),
            ["Location: d100=80 -> Rural"],
        )
        self.assertFalse(
            services.can_set_field(older, "location", is_game_master=False)
        )
        self.assertTrue(services.can_set_field(older, "location", is_game_master=True))

    def test_a_log_with_only_the_effects_line_is_no_roll(self):
        older = create_character(
            self.player,
            dice_log="Location effects: Street Smarts 1 point, literacy 3d6 <= INT",
        )

        self.assertEqual(services.roll_lines(older.dice_log, "location"), [])
        self.assertTrue(services.can_set_field(older, "location", is_game_master=False))

    def test_a_log_from_before_the_origin_existed_is_no_roll(self):
        older = create_character(
            self.player, dice_log="Strength: 3d6 = 4+4+4 = 12\nHeight: 1d6: 3"
        )

        self.assertFalse(services.field_rolled(older, "location"))


class RolledWhereverItIsRolledTest(BaseTestCase):
    """Creation, the sheet's roll and the chat's roster each fix the Origin."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("origin-roll-player")
        self.login_as(self.player)

    def test_the_creation_pages_origin_is_fixed(self):
        response = self.client.post(
            reverse("characters:create"), {"method": "roll", "name": "Rolled"}
        )

        self.assertEqual(response.status_code, 302)
        character = Character.objects.get(user=self.player, name="Rolled")
        self.assertTrue(services.field_rolled(character, "location"))
        page = self.client.get(response.url).content.decode()
        self.assertFalse(_origin_select(page))
        refused = self.post_json(
            reverse("characters:update_field", args=[character.pk]),
            {"field": "location", "value": "Consult GM"},
        )
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json()["error"], fixed())

    def test_a_sheet_roll_fixes_an_origin_nobody_recorded(self):
        character = create_character(
            self.player, location_recorded=False, skills={}, dice_log=""
        )
        page = self.client.get(
            reverse("characters:edit", args=[character.pk])
        ).content.decode()
        self.assertTrue(_origin_select(page))

        with patch("characters.dice.roll_d100", return_value=80):
            rolled = self.client.post(
                reverse("characters:roll", args=[character.pk, "origin"])
            )

        self.assertEqual(rolled.status_code, 200, rolled.content)
        page = self.client.get(
            reverse("characters:edit", args=[character.pk])
        ).content.decode()
        self.assertFalse(_origin_select(page))
        self.assertEqual(_locked_reason(page), fixed())

    def test_a_chat_started_characters_origin_is_fixed_for_its_player(self):
        """The roster rolls the background as creation does (#754), so the
        Origin it rolled is the roll a player keeps."""
        from gm.models import GMSession
        from gm.roster import start_character

        session = GMSession.objects.create(user=self.player, name="s")
        character = start_character(session, "Wren", name="Wren")

        self.assertTrue(services.field_rolled(character, "location"))
        self.assertFalse(
            services.can_set_field(character, "location", is_game_master=False)
        )


class RolledOriginOnReImportTest(BaseTestCase):
    """A roll is final across a player's import of their own character."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("origin-import-player")
        self.rolled = create_character(
            self.player,
            name="Bran",
            location="Rural",
            skills={"survival": 1},
            origin_skill="survival",
            dice_log=f"Appearance: 1d6: 3\n{ROLLED_RURAL_LINE}",
        )

    def import_file(self, payload: dict) -> Character:
        """Import ``payload`` through the upload form, as the signed-in user."""
        upload = SimpleUploadedFile(
            "character.json",
            json.dumps(payload).encode("utf-8"),
            content_type="application/json",
        )
        response = self.client.post(reverse("characters:import"), {"file": upload})
        self.assertEqual(response.status_code, 200, response.content.decode()[:400])
        return Character.objects.get(pk=response.json()["character_id"])

    def exported(self, character: Character) -> dict:
        return json.loads(generate_character_json(character))

    def test_a_players_own_export_keeps_the_origin_fixed(self):
        self.login_as(self.player)

        copy = self.import_file(self.exported(self.rolled))

        self.assertEqual(copy.location, "Rural")
        self.assertIn(
            "Location: kept from your character Bran on import = Rural",
            copy.dice_log,
        )
        self.assertTrue(services.field_rolled(copy, "location"))
        refused = self.post_json(
            reverse("characters:update_field", args=[copy.pk]),
            {"field": "location", "value": "City"},
        )
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json()["error"], fixed())

    def test_an_origin_edited_in_the_file_comes_in_as_a_choice(self):
        """The file names an Origin the character does not hold: no roll made
        it, so it arrives chosen, as a fresh character's choice would."""
        payload = self.exported(self.rolled)
        payload["data"]["location"] = "City"
        self.login_as(self.player)

        copy = self.import_file(payload)

        self.assertEqual(copy.location, "City")
        self.assertFalse(services.field_rolled(copy, "location"))
        self.assertTrue(services.can_set_field(copy, "location", is_game_master=False))

    def test_another_players_file_records_no_roll(self):
        other = self.create_user("origin-import-other")
        self.login_as(other)

        copy = self.import_file(self.exported(self.rolled))

        self.assertEqual(copy.location, "Rural")
        self.assertFalse(services.field_rolled(copy, "location"))

    def test_a_chosen_origin_stays_the_players_on_re_import(self):
        chosen = create_character(
            self.player, name="Chosen", location="City", dice_log=""
        )
        self.login_as(self.player)

        copy = self.import_file(self.exported(chosen))

        self.assertEqual(copy.location, "City")
        self.assertFalse(services.field_rolled(copy, "location"))
