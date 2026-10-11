"""Issue #941: a player's own site-rolled height chain survives re-import.

Since #939 a player's import blanks a height or weight no player could
choose, since a file is not dice. A file carries no provenance, so the
importer asks the server instead: a chain the importer's own character
holds, with the dice-log line that rolled it in the file, is the site's
roll and is kept. Anything else is blanked as before, and the sheet now
offers the roll a blank height or weight lacked: a player's once, a Game
Master's any time.
"""

import json
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from character_export.export_utils import generate_character_json
from characters import services
from characters.dice import roll_physical_features
from characters.import_service import held_to_player, validate_import_payload
from characters.models import Character
from common.test_helpers import BaseTestCase, create_character

#: A chain the dice made, and the line the site writes for it.
CHAIN = "6.6.2"
CHAIN_LINE = "Height: Demon d6: 6 -> explode -> 6 -> explode -> 2 = 6.6.2"


#: What a player's import says when it keeps a chain, and when it does not.
KEPT = "Kept, since this file is an export of your own character"
NOT_THE_SOURCE = "no character of yours is the record this file came from"


class HeightChainProvenanceTest(BaseTestCase):
    """A chain is kept only on a faithful export of the importer's character."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("chain-player")
        self.rolled = create_character(
            self.player,
            name="Bran",
            height_roll=CHAIN,
            dice_log=f"Appearance: 1d6: 3\n{CHAIN_LINE}\nWeight: 1d6: 3",
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

    def test_a_players_own_export_keeps_the_chain_and_says_why(self):
        self.login_as(self.player)

        copy = self.import_file(self.exported(self.rolled))

        self.assertEqual(copy.height_roll, CHAIN)
        self.assertIn(
            f"{KEPT}, whose record holds it: height roll {CHAIN} (on your "
            "character Bran)",
            copy.import_notice,
        )
        self.assertNotIn(NOT_THE_SOURCE, copy.import_notice)
        # The copy records where each roll came from, as that field's roll:
        # the chain, and the single weight face Bran's log records rolling.
        # This asserted the height line alone, which left the rolled weight
        # with no record, open for the player to retype (#948).
        self.assertEqual(
            copy.dice_log,
            f"Height: kept from your character Bran on import = {CHAIN}\n"
            "Weight: kept from your character Bran on import = 3",
        )

    def test_a_rolled_single_face_stays_final_on_re_import(self):
        """A roll is final across a player's import of their own (#948).

        Coordinator's ruling under the standing rule; Spencer may overrule. A
        single face came in with no record of its roll and unlocked, while a
        chain came in locked.
        """
        single = create_character(
            self.player,
            name="Single",
            height_roll="4",
            weight_roll="2",
            dice_log="Height: Demon d6: 4 = 4\nWeight: 1d6: 2",
        )
        self.login_as(self.player)

        copy = self.import_file(self.exported(single))

        self.assertEqual((copy.height_roll, copy.weight_roll), ("4", "2"))
        for field_name in ("height_roll", "weight_roll"):
            with self.subTest(field_name):
                self.assertTrue(services.field_rolled(copy, field_name))
                self.assertFalse(
                    services.can_set_field(copy, field_name, is_game_master=False)
                )
        refused = self.post_json(
            reverse("characters:update_field", args=[copy.pk]),
            {"field": "height_roll", "value": "6"},
        )
        self.assertEqual(refused.status_code, 400)

    def test_a_chosen_single_face_stays_the_players_on_re_import(self):
        """No roll in the original's log: the copy's face is still a choice."""
        chosen = create_character(
            self.player, name="Chosen", height_roll="5", weight_roll="", dice_log=""
        )
        self.login_as(self.player)

        copy = self.import_file(self.exported(chosen))

        self.assertEqual(copy.height_roll, "5")
        self.assertFalse(services.field_rolled(copy, "height_roll"))
        self.assertTrue(
            services.can_set_field(copy, "height_roll", is_game_master=False)
        )

    def test_another_players_file_records_no_roll_for_a_single_face(self):
        single = create_character(
            self.player, name="Single", height_roll="4", dice_log="Height: 1d6: 4"
        )
        self.login_as(self.create_user("face-stranger"))

        copy = self.import_file(self.exported(single))

        self.assertEqual(copy.height_roll, "4")
        self.assertEqual(copy.dice_log, "")

    def test_an_edited_chain_is_blanked_and_the_notice_says_why(self):
        self.login_as(self.player)
        payload = self.exported(self.rolled)
        payload["data"]["height_roll"] = "6.6.6.5"

        copy = self.import_file(payload)

        self.assertEqual(copy.height_roll, "")
        self.assertIn(
            "height roll 6.6.6.5 (a chain comes only from the dice or a Game "
            f"Master, and {NOT_THE_SOURCE})",
            copy.import_notice,
        )
        self.assertNotIn(KEPT, copy.import_notice)

    def test_a_chain_copied_onto_another_file_is_blanked(self):
        """The chain and its forged dice-log line are Bran's; the file is not.

        A chain belongs to one character (the coordinator's ruling on the
        #941 review): the dice log in a file is the player's to edit, so
        pasting Bran's line into Other's file ties nothing.
        """
        self.login_as(self.player)
        other = create_character(self.player, name="Other", dice_log="Height: 1d6: 4")
        payload = self.exported(other)
        payload["data"]["height_roll"] = CHAIN
        payload["data"]["dice_log"] = self.rolled.dice_log

        copy = self.import_file(payload)

        self.assertEqual(copy.height_roll, "")
        self.assertIn(NOT_THE_SOURCE, copy.import_notice)

    def test_a_file_differing_from_its_source_anywhere_is_blanked(self):
        """Name, a site roll or the dice log: each one makes another character."""
        self.login_as(self.player)
        for field_name, value in (
            ("name", "Bran the Tall"),
            ("strength", self.rolled.strength + 1),
            ("fatigue_roll", self.rolled.fatigue_roll + 1),
            ("appearance_roll", "1"),
            ("weight_roll", "5"),
            ("dice_log", CHAIN_LINE),
        ):
            with self.subTest(field_name):
                payload = self.exported(self.rolled)
                payload["data"][field_name] = value

                copy = self.import_file(payload)

                self.assertEqual(copy.height_roll, "")

    def test_after_a_re_import_a_value_only_file_is_still_blanked(self):
        """The kept copy is no value-only source for a file that is not it."""
        self.login_as(self.player)
        self.import_file(self.exported(self.rolled))
        other = create_character(self.player, name="Other", dice_log="")
        payload = self.exported(other)
        payload["data"]["height_roll"] = CHAIN

        copy = self.import_file(payload)

        self.assertEqual(copy.height_roll, "")

    def test_a_kept_chain_cleared_is_not_rolled_again_by_a_player(self):
        self.login_as(self.player)
        copy = self.import_file(self.exported(self.rolled))
        copy.height_roll = ""
        copy.save()

        refused = self.client.post(reverse("characters:roll", args=[copy.pk, "height"]))

        self.assertEqual(refused.status_code, 400)
        self.assertIn("only a Game Master", refused.json()["error"])

    def test_another_players_file_is_blanked(self):
        stranger = self.create_user("chain-stranger")
        self.login_as(stranger)

        copy = self.import_file(self.exported(self.rolled))

        self.assertEqual(copy.height_roll, "")
        self.assertIn(NOT_THE_SOURCE, copy.import_notice)

    def test_a_game_master_set_chain_on_the_players_record_is_kept(self):
        """No dice-log line: a Game Master typed it; the file is its export."""
        ruled = create_character(
            self.player, name="Ruled", height_roll="1.1.4", dice_log=""
        )
        self.login_as(self.player)

        copy = self.import_file(self.exported(ruled))

        self.assertEqual(copy.height_roll, "1.1.4")

    def test_a_game_masters_import_is_unchanged(self):
        game_master = self.create_dm("chain-gm")
        payload = self.exported(self.rolled)
        payload["data"]["height_roll"] = "6.6.6.5"
        imported = validate_import_payload(payload)

        kept = held_to_player(imported, is_game_master=True, importer=game_master)

        self.assertIs(kept, imported)
        self.assertEqual(kept.fields["height_roll"], "6.6.6.5")

    def test_creation_and_the_sheet_write_the_lines_the_lookup_reads(self):
        """The dice log's label is the one prefix both sides use."""
        _stats, lines = roll_physical_features()
        dice_log = "\n".join(lines)
        for field_name in ("height_roll", "weight_roll"):
            with self.subTest(field_name):
                self.assertEqual(len(services.roll_lines(dice_log, field_name)), 1)


class BlankHeightRollTest(BaseTestCase):
    """The sheet rolls a blank height or weight: a player's once (#941)."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("roll-player")
        self.character = create_character(
            self.player, height_roll="", weight_roll="", dice_log=""
        )

    def roll(self, kind: str):
        return self.client.post(
            reverse("characters:roll", args=[self.character.pk, kind])
        )

    def test_a_player_rolls_a_blank_height_once(self):
        self.login_as(self.player)
        page = self.client.get(reverse("characters:edit", args=[self.character.pk]))
        self.assertRegex(page.content.decode(), r'id="roll-height-btn"[^>]*>Roll')
        self.assertNotRegex(page.content.decode(), r'id="roll-height-btn"[^>]*hidden')

        with patch("characters.dice.roll_d6", side_effect=[6, 6, 2]):
            response = self.roll("height")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()["can_roll"])
        self.character.refresh_from_db()
        self.assertEqual(self.character.height_roll, CHAIN)
        self.assertIn(CHAIN_LINE, self.character.dice_log)
        history = {
            entry["field"]: (entry["old"], entry["new"])
            for entry in self.character.history
        }
        self.assertEqual(history["height_roll"], ("", CHAIN))

        # Cleared, it is still rolled: no second roll for a player.
        self.character.height_roll = ""
        self.character.save()
        refused = self.roll("height")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("only a Game Master", refused.json()["error"])
        page = self.client.get(reverse("characters:edit", args=[self.character.pk]))
        self.assertRegex(page.content.decode(), r'id="roll-height-btn"[^>]*hidden')

    def test_a_player_may_not_roll_over_a_chosen_face(self):
        self.character.weight_roll = "4"
        self.character.save()
        self.login_as(self.player)

        refused = self.roll("weight")

        self.assertEqual(refused.status_code, 400)
        self.character.refresh_from_db()
        self.assertEqual(self.character.weight_roll, "4")

    def test_a_game_master_rolls_any_time_and_the_history_keeps_the_old(self):
        self.character.height_roll = "3"
        self.character.dice_log = "Height: Demon d6: 3 = 3"
        self.character.save()
        self.login_as(self.create_dm("roll-gm"))
        page = self.client.get(reverse("characters:edit", args=[self.character.pk]))
        self.assertRegex(
            page.content.decode(),
            r'id="roll-height-btn"[^>]* data-confirm="Roll height again\?',
        )

        with patch("characters.dice.roll_d6", return_value=4):
            first = self.roll("height")
            second = self.roll("weight")

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 200, second.content)
        self.character.refresh_from_db()
        self.assertEqual(
            (self.character.height_roll, self.character.weight_roll), ("4", "4")
        )
        history = {
            entry["field"]: (entry["old"], entry["new"])
            for entry in self.character.history
        }
        self.assertEqual(history["height_roll"], ("3", "4"))
        self.assertEqual(history["weight_roll"], ("", "4"))
