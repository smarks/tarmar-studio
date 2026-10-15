"""The literacy check: its row on the sheet, and rolling it (issues #740, #741).

The Location section printed an origin's literacy check once, from the origin
the page loaded with, so changing the Origin left the old check on show until
a reload (#740). And there was no way to roll the check or keep the result
(#741): a player rolled by hand and nothing recorded whether the character
could read.
"""

import json
import re
from pathlib import Path
from unittest.mock import patch

from django.conf import settings

from character_export.export_utils import (
    generate_character_json,
    generate_character_markdown,
)
from characters import services
from characters.dice import DICE_IN_TEXT
from characters.import_service import CharacterImportError, validate_import_payload
from characters.models import Character
from characters.services import CharacterChangeError
from characters.views.characters import LITERACY_RESPONSE_KEYS
from common.test_helpers import BaseTestCase, create_character
from gm.character_sheet import CharacterSheet
from gm.models import GMSession

#: A Village character with INT 10 passes on 8 or less (10 − 2).
VILLAGE_THRESHOLD = 8


def rolled(total: int):
    """Patch the service's literacy roll to come up ``total``."""
    return patch(
        "characters.services.roll_literacy_logged",
        return_value=(total, f"3d6: [{total - 2}, 1, 1] = {total}"),
    )


class LiteracyReadingTest(BaseTestCase):
    """The stored roll read against the current origin and INT."""

    def setUp(self):
        super().setUp()
        self.character = create_character(self.create_user("reader-owner"))

    def test_the_threshold_is_int_less_the_origin_penalty(self):
        for location, threshold in (("City", 10), ("Village", 8), ("Rural", 6)):
            with self.subTest(location=location):
                self.character.location = location
                self.assertEqual(self.character.literacy_threshold, threshold)

    def test_consult_gm_and_an_unrecorded_int_have_no_threshold(self):
        self.character.location = "Consult GM"
        self.assertIsNone(self.character.literacy_threshold)
        self.character.location = "City"
        self.character.intelligence = None
        self.assertIsNone(self.character.literacy_threshold)

    def test_the_row_reads_not_rolled_until_it_is(self):
        self.assertIsNone(self.character.is_literate)
        self.assertEqual(self.character.literacy_result, "Not rolled")
        self.assertEqual(self.character.literacy_summary, "3d6 ≤ INT − 2, Not rolled")

    def test_a_roll_at_the_threshold_passes_and_one_over_fails(self):
        self.character.literacy_roll = VILLAGE_THRESHOLD
        self.assertTrue(self.character.is_literate)
        self.assertEqual(self.character.literacy_result, "Literate (rolled 8 ≤ 10 − 2)")
        self.character.literacy_roll = VILLAGE_THRESHOLD + 1
        self.assertFalse(self.character.is_literate)
        self.assertEqual(
            self.character.literacy_result, "Illiterate (rolled 9 > 10 − 2)"
        )

    def test_a_check_with_no_penalty_prints_int_alone(self):
        self.character.location = "City"
        self.character.literacy_roll = 9
        self.assertEqual(self.character.literacy_result, "Literate (rolled 9 ≤ 10)")
        self.character.literacy_roll = 11
        self.assertEqual(self.character.literacy_result, "Illiterate (rolled 11 > 10)")

    def test_a_roll_with_no_check_to_read_it_against_reads_as_the_roll(self):
        self.character.literacy_roll = 9
        self.character.location = "Consult GM"
        self.assertIsNone(self.character.is_literate)
        self.assertEqual(self.character.literacy_result, "Rolled 9")


class LiteracyServiceTest(BaseTestCase):
    """``services.roll_literacy``: code owns the dice, a player rolls once."""

    def setUp(self):
        super().setUp()
        self.character = create_character(self.create_user("literacy-owner"))

    def test_a_roll_within_the_threshold_is_literate(self):
        with rolled(VILLAGE_THRESHOLD):
            result = services.roll_literacy(self.character.pk)

        self.character.refresh_from_db()
        self.assertEqual(self.character.literacy_roll, VILLAGE_THRESHOLD)
        self.assertTrue(self.character.is_literate)
        self.assertIn("8 ≤ 8, literate", result.note)

    def test_a_roll_over_the_threshold_is_illiterate(self):
        with rolled(VILLAGE_THRESHOLD + 1):
            result = services.roll_literacy(self.character.pk)

        self.character.refresh_from_db()
        self.assertFalse(self.character.is_literate)
        self.assertIn("9 > 8, illiterate", result.note)

    def test_the_note_carries_the_dice_and_the_history_keeps_it(self):
        result = services.roll_literacy(self.character.pk)

        found = DICE_IN_TEXT.findall(result.note)
        self.assertEqual(len(found), 1, result.note)
        expression, faces, total = found[0]
        self.assertEqual(expression, "3d6")
        self.assertEqual(sum(int(face) for face in faces.split(",")), int(total))
        self.character.refresh_from_db()
        self.assertEqual(self.character.literacy_roll, int(total))
        self.assertEqual(self.character.history[-1]["field"], "literacy_roll")

    def test_consult_gm_publishes_no_modifier_so_there_is_nothing_to_roll(self):
        self.character.location = "Consult GM"
        self.character.save()

        for is_game_master in (False, True):
            with self.subTest(is_game_master=is_game_master):
                with self.assertRaisesMessage(CharacterChangeError, "Consult GM"):
                    services.roll_literacy(
                        self.character.pk, is_game_master=is_game_master
                    )
        self.character.refresh_from_db()
        self.assertIsNone(self.character.literacy_roll)
        self.assertFalse(
            services.can_roll_literacy(self.character, is_game_master=True)
        )

    def test_a_player_rolls_once_and_a_game_master_may_roll_again(self):
        with rolled(5):
            services.roll_literacy(self.character.pk)
        self.character.refresh_from_db()
        self.assertFalse(
            services.can_roll_literacy(self.character, is_game_master=False)
        )
        self.assertTrue(services.can_roll_literacy(self.character, is_game_master=True))

        with self.assertRaisesMessage(CharacterChangeError, "only a Game Master"):
            services.roll_literacy(self.character.pk)
        with rolled(15):
            services.roll_literacy(self.character.pk, is_game_master=True)

        self.character.refresh_from_db()
        self.assertEqual(self.character.literacy_roll, 15)

    def test_an_unrecorded_int_still_takes_the_roll_and_reads_it_later(self):
        self.character.intelligence = None
        self.character.save()

        with rolled(7):
            result = services.roll_literacy(self.character.pk)

        self.assertIn("INT is not recorded yet", result.note)
        self.character.refresh_from_db()
        self.assertIsNone(self.character.is_literate)
        self.character.intelligence = 12
        self.assertTrue(self.character.is_literate)


class OriginChangeAfterTheRollTest(BaseTestCase):
    """Spencer's rulings on #741: once literacy is rolled a player may not
    change the Origin, and a Game Master who does has literacy rolled again
    against the new origin -- no roll is read against an origin it was not
    rolled for."""

    def setUp(self):
        super().setUp()
        self.character = create_character(self.create_user("origin-owner"))

    def roll(self, total: int) -> None:
        with rolled(total):
            services.roll_literacy(self.character.pk)

    def test_a_player_may_change_the_origin_before_any_roll(self):
        self.assertTrue(
            services.can_change_origin(self.character, is_game_master=False)
        )
        services.update_field(self.character.pk, "location", "Rural")

        self.character.refresh_from_db()
        self.assertEqual(self.character.location, "Rural")
        self.assertIsNone(self.character.literacy_roll, "nothing is rolled")

    def test_a_player_may_not_change_the_origin_once_it_is_rolled(self):
        self.roll(VILLAGE_THRESHOLD)
        self.character.refresh_from_db()
        self.assertFalse(
            services.can_change_origin(self.character, is_game_master=False)
        )

        with self.assertRaisesMessage(
            CharacterChangeError, services.ORIGIN_FIXED_REASON
        ):
            services.update_field(self.character.pk, "location", "City")

        self.character.refresh_from_db()
        self.assertEqual(
            (self.character.location, self.character.literacy_roll),
            ("Village", VILLAGE_THRESHOLD),
        )

    def test_the_lock_is_on_the_origin_alone(self):
        self.roll(VILLAGE_THRESHOLD)

        # Another field the player may set, and a Game Master's INT (#748).
        services.update_field(self.character.pk, "height_roll", "4")
        services.update_field(
            self.character.pk, "intelligence", 11, is_game_master=True
        )

        self.character.refresh_from_db()
        self.assertEqual(self.character.literacy_roll, VILLAGE_THRESHOLD)
        self.assertEqual(self.character.literacy_result, "Literate (rolled 8 ≤ 11 − 2)")

    def test_a_game_master_changing_the_origin_rolls_literacy_again(self):
        self.roll(VILLAGE_THRESHOLD)
        self.character.refresh_from_db()
        self.assertTrue(services.can_change_origin(self.character, is_game_master=True))

        with rolled(12):
            result = services.update_field(
                self.character.pk, "location", "City", is_game_master=True
            )

        self.character.refresh_from_db()
        self.assertEqual(self.character.location, "City")
        self.assertEqual(self.character.literacy_roll, 12)
        self.assertFalse(self.character.is_literate, "12 > 10")
        self.assertEqual(self.character.literacy_result, "Illiterate (rolled 12 > 10)")
        last = self.character.history[-1]
        self.assertEqual(last["field"], "literacy_roll")
        self.assertIn(
            "literacy rerolled because Origin changed from Village to City",
            result.note,
        )
        self.assertIn("rerolled because Origin changed from Village to City", str(last))
        self.assertIn("12 > 10, illiterate", result.note)

    def test_a_new_origin_of_consult_gm_clears_the_roll(self):
        self.roll(VILLAGE_THRESHOLD)

        result = services.update_field(
            self.character.pk, "location", "Consult GM", is_game_master=True
        )

        self.character.refresh_from_db()
        self.assertIsNone(self.character.literacy_roll)
        self.assertEqual(self.character.location_literacy_check, "Consult GM")
        self.assertEqual(
            self.character.literacy_result, "Roll cleared; the Game Master decides"
        )
        self.assertIn(
            "literacy roll 8 cleared because Origin changed from Village to "
            "Consult GM, which publishes no check",
            result.note,
        )

    def test_the_same_origin_again_rolls_nothing(self):
        self.roll(VILLAGE_THRESHOLD)

        with rolled(17):
            services.update_field(
                self.character.pk, "location", "Village", is_game_master=True
            )

        self.character.refresh_from_db()
        self.assertEqual(self.character.literacy_roll, VILLAGE_THRESHOLD)

    def test_the_background_path_refuses_a_player_once_it_is_rolled(self):
        """set_background is the Games Master's plan's way in; a player's
        session reached it and moved the origin under the roll."""
        self.roll(VILLAGE_THRESHOLD + 1)

        with self.assertRaisesMessage(
            CharacterChangeError, services.ORIGIN_FIXED_REASON
        ):
            services.set_background(self.character.pk, {"location": "City"})

        self.character.refresh_from_db()
        self.assertEqual(self.character.location, "Village")
        self.assertFalse(self.character.is_literate)

    def test_a_refused_background_change_saves_nothing(self):
        """The check this file used to make beside the Origin lock: a change
        whose later field is refused leaves the earlier one unsaved too. Since
        #749 a player's wealth is the refused field."""
        with self.assertRaises(CharacterChangeError):
            services.set_background(
                self.character.pk, {"location": "City", "wealth_level": "Rich"}
            )

        self.character.refresh_from_db()
        self.assertEqual(
            (self.character.location, self.character.wealth_level),
            ("Village", "Moderate"),
            "nothing in the refused change is saved",
        )

    def test_the_background_path_rerolls_for_a_game_master(self):
        self.roll(VILLAGE_THRESHOLD)

        with rolled(4):
            result = services.set_background(
                self.character.pk, {"location": "Rural"}, is_game_master=True
            )

        self.character.refresh_from_db()
        self.assertEqual(self.character.literacy_roll, 4)
        self.assertEqual(self.character.literacy_result, "Literate (rolled 4 ≤ 10 − 4)")
        self.assertIn(
            "literacy rerolled because Origin changed from Village to Rural",
            result.note,
        )
        self.assertEqual(self.character.history[-1]["field"], "literacy_roll")

    def test_a_cleared_roll_keeps_the_player_held_to_it(self):
        """Clearing the roll for Consult GM must not hand the player a fresh
        origin and a second roll."""
        self.roll(VILLAGE_THRESHOLD + 1)
        services.update_field(
            self.character.pk, "location", "Consult GM", is_game_master=True
        )
        self.character.refresh_from_db()

        self.assertIsNone(self.character.literacy_roll)
        self.assertTrue(self.character.literacy_rolled)
        self.assertEqual(
            self.character.literacy_result, "Roll cleared; the Game Master decides"
        )
        self.assertFalse(
            services.can_change_origin(self.character, is_game_master=False)
        )
        with self.assertRaisesMessage(
            CharacterChangeError, services.ORIGIN_FIXED_REASON
        ):
            services.update_field(self.character.pk, "location", "City")

        # Even an origin with a check leaves the player without a second roll.
        self.character.location = "City"
        self.character.save()
        self.assertFalse(
            services.can_roll_literacy(self.character, is_game_master=False)
        )
        with self.assertRaisesMessage(CharacterChangeError, "only a Game Master"):
            services.roll_literacy(self.character.pk)

    def test_a_flag_with_no_roll_away_from_consult_gm_reads_not_rolled(self):
        """An import can carry the flag and no roll at any origin; only a
        Consult GM origin says the Game Master cleared it. The lock holds
        either way."""
        self.character.location = "City"
        self.character.literacy_rolled = True
        self.character.save()

        self.assertEqual(self.character.literacy_result, "Not rolled")
        self.assertFalse(
            services.can_change_origin(self.character, is_game_master=False)
        )
        self.assertFalse(
            services.can_roll_literacy(self.character, is_game_master=False)
        )

    def test_no_stored_roll_to_clear_writes_no_literacy_line(self):
        """Rolled once but holding no roll (an import can arrive so): moving
        to Consult GM has nothing to clear and says nothing about it."""
        self.character.literacy_rolled = True
        self.character.save()

        result = services.update_field(
            self.character.pk, "location", "Consult GM", is_game_master=True
        )

        self.assertEqual(result.note, "location set to Consult GM")

    def test_a_game_master_moving_off_consult_gm_rolls_it_again(self):
        self.roll(VILLAGE_THRESHOLD)
        services.update_field(
            self.character.pk, "location", "Consult GM", is_game_master=True
        )

        with rolled(10):
            result = services.update_field(
                self.character.pk, "location", "City", is_game_master=True
            )

        self.character.refresh_from_db()
        self.assertEqual(self.character.literacy_roll, 10)
        self.assertEqual(self.character.literacy_result, "Literate (rolled 10 ≤ 10)")
        self.assertIn("from Consult GM to City", result.note)

    def test_an_import_carrying_a_roll_keeps_the_lock(self):
        payload = json.loads(generate_character_json(self.character))
        payload["data"]["literacy_roll"] = 9
        payload["data"].pop("literacy_rolled")

        imported = validate_import_payload(payload)

        self.assertTrue(imported.fields["literacy_rolled"])


class LiteracyResponseKeysTest(BaseTestCase):
    """The view and the page read the literacy row from one list of keys."""

    def test_the_javascript_declares_the_same_keys(self):
        source = Path(settings.BASE_DIR, "static/js/character-edit.js").read_text()
        match = re.search(
            r"const LITERACY_RESPONSE_KEYS = \[(.*?)\];", source, re.DOTALL
        )
        self.assertIsNotNone(match, "LITERACY_RESPONSE_KEYS is not declared")
        assert match is not None
        declared = tuple(re.findall(r"'([a-z_]+)'", match.group(1)))
        self.assertEqual(declared, LITERACY_RESPONSE_KEYS)


class LiteracyEndpointsTest(BaseTestCase):
    """The editor's field update, the roll endpoint and the row they paint."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("literacy-page-owner")
        self.login_as(self.owner)
        self.character = create_character(self.owner, name="Letters")
        self.edit_url = f"/tarmar/characters/{self.character.pk}/"
        self.roll_url = f"/tarmar/characters/{self.character.pk}/roll-literacy/"
        self.update_url = f"/tarmar/characters/{self.character.pk}/update-field/"

    def page(self) -> str:
        return self.client.get(self.edit_url).content.decode()

    def test_changing_the_origin_answers_with_the_new_check(self):
        """Issue #740: the row had nothing to repaint from."""
        response = self.post_json(
            self.update_url, {"field": "location", "value": "Rural"}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(
            {key: data[key] for key in LITERACY_RESPONSE_KEYS},
            {
                "location_literacy_check": "3d6 ≤ INT − 4",
                "location_literacy_modifier": 4,
                "literacy_roll": None,
                "is_literate": None,
                "literacy_result": "Not rolled",
                "can_roll_literacy": True,
                "can_change_origin": True,
                "literacy_ruling": None,
                "can_rule_literacy": False,
            },
        )

    def test_consult_gm_answers_with_no_modifier_and_no_control(self):
        data = self.post_json(
            self.update_url, {"field": "location", "value": "Consult GM"}
        ).json()

        self.assertEqual(data["location_literacy_check"], "Consult GM")
        self.assertIsNone(data["location_literacy_modifier"])
        self.assertFalse(data["can_roll_literacy"])

    def test_changing_int_rereads_a_stored_roll(self):
        self.character.literacy_roll = 9
        self.character.save()
        # INT is a Game Master's to type (issue #748).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("reread-gm"))

        data = self.post_json(
            self.update_url, {"field": "intelligence", "value": 11}
        ).json()

        self.assertEqual(data["literacy_roll"], 9)
        self.assertTrue(data["is_literate"], "9 ≤ 11 − 2")
        self.assertEqual(data["literacy_result"], "Literate (rolled 9 ≤ 11 − 2)")

    def test_a_field_the_row_does_not_read_carries_none_of_its_keys(self):
        data = self.post_json(
            self.update_url, {"field": "wealth_level", "value": "Merchant"}
        ).json()

        for key in LITERACY_RESPONSE_KEYS:
            self.assertNotIn(key, data)

    def test_the_row_renders_with_the_ids_the_page_repaints(self):
        page = self.page()

        self.assertIn('id="literacy-check">3d6 ≤ INT − 2</span>', page)
        self.assertIn('id="literacy-result">Not rolled</span>', page)
        self.assertRegex(page, r'<button[^>]*id="roll-literacy-btn"[^>]*>')
        self.assertNotRegex(page, r'<button[^>]*id="roll-literacy-btn"[^>]*hidden')

    def test_a_player_rolls_once_and_the_control_then_hides(self):
        with rolled(VILLAGE_THRESHOLD):
            first = self.post_json(self.roll_url, {})

        self.assertEqual(first.status_code, 200)
        data = first.json()
        self.assertEqual(
            {key: data[key] for key in LITERACY_RESPONSE_KEYS},
            {
                "location_literacy_check": "3d6 ≤ INT − 2",
                "location_literacy_modifier": 2,
                "literacy_roll": VILLAGE_THRESHOLD,
                "is_literate": True,
                "literacy_result": "Literate (rolled 8 ≤ 10 − 2)",
                "can_roll_literacy": False,
                "can_change_origin": False,
                "literacy_ruling": None,
                "can_rule_literacy": False,
            },
        )
        self.assertIn("literate", data["note"])

        second = self.post_json(self.roll_url, {})
        self.assertEqual(second.status_code, 400)
        self.assertIn("only a Game Master", second.json()["error"])
        page = self.page()
        self.assertIn('id="literacy-result">Literate (rolled 8 ≤ 10 − 2)</span>', page)
        self.assertRegex(page, r'<button[^>]*id="roll-literacy-btn"[^>]*hidden')

    def test_a_player_may_change_the_origin_before_rolling(self):
        response = self.post_json(
            self.update_url, {"field": "location", "value": "City"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["can_change_origin"])
        page = self.page()
        self.assertNotRegex(page, r'<select aria-label="Origin"[^>]*disabled')
        self.assertRegex(page, r'<span[^>]*id="origin-fixed-note" hidden>')

    def test_a_rolled_players_origin_is_refused_and_rendered_locked(self):
        with rolled(VILLAGE_THRESHOLD):
            self.post_json(self.roll_url, {})

        response = self.post_json(
            self.update_url, {"field": "location", "value": "City"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], services.ORIGIN_FIXED_REASON)
        self.character.refresh_from_db()
        self.assertEqual(self.character.location, "Village")
        page = self.page()
        self.assertRegex(
            page, r'<select aria-label="Origin" class="autosave" disabled>'
        )
        self.assertRegex(page, r'<span class="formula" id="origin-fixed-note">')
        self.assertIn("The Origin is fixed once literacy is rolled", page)

    def test_a_game_master_changing_a_rolled_origin_gets_the_new_roll_back(self):
        self.character.literacy_roll = VILLAGE_THRESHOLD
        self.character.literacy_rolled = True
        self.character.save()
        self.logout()
        self.login_as(self.create_dm("origin-dm"))
        page = self.page()
        self.assertNotRegex(page, r'<select aria-label="Origin"[^>]*disabled')

        with rolled(12):
            response = self.post_json(
                self.update_url, {"field": "location", "value": "City"}
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["literacy_roll"], 12)
        self.assertFalse(data["is_literate"])
        self.assertEqual(data["literacy_result"], "Illiterate (rolled 12 > 10)")
        self.assertTrue(data["can_change_origin"])

    def test_a_game_masters_origin_change_writes_one_audit_line(self):
        self.character.literacy_roll = VILLAGE_THRESHOLD
        self.character.literacy_rolled = True
        self.character.save()
        self.logout()
        self.login_as(self.create_dm("origin-audit-dm"))

        with (
            rolled(12),
            self.assertLogs("origami.audit", level="INFO") as captured,
        ):
            self.post_json(self.update_url, {"field": "location", "value": "City"})

        self.assertEqual(len(captured.output), 1, captured.output)
        (line,) = captured.output
        self.assertIn("CHARACTER_EDIT", line)
        self.assertIn("rerolled because Origin changed from Village to City", line)

    def test_a_game_master_may_roll_it_again(self):
        self.logout()
        self.login_as(self.create_dm("literacy-dm"))
        self.post_json(self.roll_url, {})

        again = self.post_json(self.roll_url, {})

        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.json()["can_roll_literacy"])

    def test_consult_gm_refuses_the_roll_and_hides_the_control(self):
        self.character.location = "Consult GM"
        self.character.save()

        response = self.post_json(self.roll_url, {})

        self.assertEqual(response.status_code, 400)
        self.assertIn("Consult GM", response.json()["error"])
        self.assertRegex(self.page(), r'<button[^>]*id="roll-literacy-btn"[^>]*hidden')

    def test_a_stranger_cannot_roll_it(self):
        self.logout()
        self.login_as(self.create_user("literacy-stranger"))

        response = self.post_json(self.roll_url, {})

        self.assertEqual(response.status_code, 403)
        self.character.refresh_from_db()
        self.assertIsNone(self.character.literacy_roll)

    def test_a_reader_sees_the_row_but_no_control(self):
        self.character.is_public = True
        self.character.literacy_roll = 14
        self.character.save()
        self.logout()
        self.login_as(self.create_user("literacy-reader"))

        page = self.page()

        self.assertIn(
            # The template escapes the comparison, as it should.
            'id="literacy-result">Illiterate (rolled 14 &gt; 10 − 2)</span>',
            page,
        )
        self.assertNotIn('id="roll-literacy-btn"', page)


class LiteracyExportTest(BaseTestCase):
    """The roll is a record: exported, imported, and on the GM's sheet."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user("literacy-export-owner")
        self.character = create_character(self.user, name="Scribe", literacy_roll=6)

    def test_the_markdown_export_states_the_check_and_the_outcome(self):
        markdown = generate_character_markdown(self.character)

        self.assertIn(
            "- **Literacy:** 3d6 ≤ INT − 2, Literate (rolled 6 ≤ 10 − 2)", markdown
        )

    def test_the_roll_round_trips_through_the_json_export(self):
        payload = json.loads(generate_character_json(self.character))

        self.assertEqual(payload["data"]["literacy_roll"], 6)
        imported = validate_import_payload(payload)
        self.assertEqual(imported.fields["literacy_roll"], 6)

    def test_an_unrolled_character_still_imports(self):
        self.character.literacy_roll = None
        payload = json.loads(generate_character_json(self.character))

        from characters.import_service import save_imported_character

        saved = save_imported_character(self.user, validate_import_payload(payload))
        self.assertIsNone(saved.literacy_roll)

    def test_an_import_cannot_state_a_roll_three_dice_cannot_make(self):
        from characters.import_service import save_imported_character

        payload = json.loads(generate_character_json(self.character))
        for impossible in (2, 19):
            with self.subTest(value=impossible):
                payload["data"]["literacy_roll"] = impossible
                with self.assertRaises(CharacterImportError):
                    save_imported_character(self.user, validate_import_payload(payload))
        self.assertEqual(Character.objects.filter(name="Scribe").count(), 1)

    def test_the_game_masters_sheet_says_whether_the_character_can_read(self):
        sheet = CharacterSheet.from_character(self.character)
        self.assertEqual(sheet.literacy, "3d6 ≤ INT − 2, Literate (rolled 6 ≤ 10 − 2)")

        session = GMSession.objects.create(name="Reading", user=self.user)
        session.characters.add(self.character)
        self.login_as(self.user)
        page = self.client.get(f"/tarmar/gm/?session={session.pk}").content.decode()
        self.assertIn(
            "<dt>Literacy</dt><dd>3d6 ≤ INT − 2, Literate (rolled 6 ≤ 10 − 2)</dd>",
            page,
        )


class LiteracyRulingTest(BaseTestCase):
    """A Game Master's ruling on literacy at a Consult GM origin (issue #743).

    "Consult GM" publishes no literacy check, so there is nothing to roll and
    the table decides. The decision is a record, kept apart from a roll and
    shown with where it came from.
    """

    def setUp(self):
        super().setUp()
        self.character = create_character(
            self.create_user("ruling-owner"), location="Consult GM"
        )

    def rule(self, literate: bool | None, *, is_game_master: bool = True):
        return services.rule_literacy(
            self.character.pk, literate, is_game_master=is_game_master
        )

    def test_a_game_master_rules_a_character_literate(self):
        result = self.rule(True)

        self.character.refresh_from_db()
        self.assertIs(self.character.literacy_ruling, True)
        self.assertIsNone(self.character.literacy_roll, "a ruling is not a roll")
        self.assertIs(self.character.is_literate, True)
        self.assertEqual(
            self.character.literacy_result, "Literate (Game Master's ruling)"
        )
        self.assertEqual(
            self.character.literacy_summary,
            "Consult GM, Literate (Game Master's ruling)",
        )
        self.assertIn("ruled literate by the Game Master", result.note)
        self.assertEqual(self.character.history[-1]["field"], "literacy_ruling")

    def test_a_game_master_rules_a_character_illiterate_and_may_withdraw_it(self):
        self.rule(False)
        self.character.refresh_from_db()
        self.assertIs(self.character.is_literate, False)
        self.assertEqual(
            self.character.literacy_result, "Illiterate (Game Master's ruling)"
        )

        self.rule(None)

        self.character.refresh_from_db()
        self.assertIsNone(self.character.literacy_ruling)
        self.assertIsNone(self.character.is_literate)

    def test_a_player_cannot_rule(self):
        self.assertFalse(
            services.can_rule_literacy(self.character, is_game_master=False)
        )
        with self.assertRaisesMessage(
            CharacterChangeError, services.LITERACY_RULING_GAME_MASTER_ONLY
        ):
            self.rule(True, is_game_master=False)

        self.character.refresh_from_db()
        self.assertIsNone(self.character.literacy_ruling)

    def test_an_origin_with_a_check_is_rolled_not_ruled(self):
        self.character.location = "Village"
        self.character.save()
        self.assertFalse(
            services.can_rule_literacy(self.character, is_game_master=True)
        )

        with self.assertRaisesMessage(CharacterChangeError, "has a literacy check"):
            self.rule(True)

    def test_a_ruling_fixes_a_players_origin(self):
        """A player must not escape a ruling by moving to an origin they can
        roll at."""
        self.rule(False)
        self.character.refresh_from_db()
        self.assertFalse(
            services.can_change_origin(self.character, is_game_master=False)
        )

        with self.assertRaisesMessage(
            CharacterChangeError, services.ORIGIN_FIXED_REASON
        ):
            services.update_field(self.character.pk, "location", "City")

    def test_a_game_master_moving_off_consult_gm_rolls_where_the_ruling_was(self):
        """Spencer's ruling on #741: a Game Master's Origin change rolls the
        check again and the player's lock survives. Withdrawing the ruling
        and rolling nothing unlocked the Origin: the player moved to City and
        rolled for themselves."""
        self.rule(True)

        with rolled(9):
            result = services.update_field(
                self.character.pk, "location", "Rural", is_game_master=True
            )

        self.character.refresh_from_db()
        self.assertIsNone(self.character.literacy_ruling)
        self.assertEqual(
            (self.character.literacy_roll, self.character.literacy_rolled), (9, True)
        )
        self.assertEqual(
            self.character.literacy_result, "Illiterate (rolled 9 > 10 − 4)"
        )
        self.assertFalse(
            services.can_change_origin(self.character, is_game_master=False)
        )
        self.assertIn(
            "the Game Master's ruling (literate) withdrawn because Origin changed "
            "from Consult GM to Rural, which has a check to roll",
            result.note,
        )
        # The Rural origin's skill is granted with the move (#754).
        self.assertEqual(
            [line["field"] for line in self.character.history[-4:]],
            ["location", "skills", "literacy_ruling", "literacy_roll"],
        )

    def test_a_ruling_imported_at_an_origin_with_a_check_is_dropped(self):
        """A ruling belongs to Consult GM; a file putting one on a City
        character would read as literate with no roll and lock the Origin."""
        payload = json.loads(generate_character_json(self.character))
        payload["data"].update(location="City", literacy_ruling=True)

        imported = validate_import_payload(payload)

        self.assertNotIn("literacy_ruling", imported.fields)

    def test_the_ruling_is_exported_and_shown_on_the_game_masters_sheet(self):
        self.rule(True)
        self.character.refresh_from_db()

        markdown = generate_character_markdown(self.character)
        self.assertIn(
            "- **Literacy:** Consult GM, Literate (Game Master's ruling)", markdown
        )
        payload = json.loads(generate_character_json(self.character))
        self.assertIs(payload["data"]["literacy_ruling"], True)
        self.assertEqual(
            CharacterSheet.from_character(self.character).literacy,
            "Consult GM, Literate (Game Master's ruling)",
        )


class LiteracyRulingEndpointTest(BaseTestCase):
    """The editor's ruling control, for a Game Master at Consult GM (#743)."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("ruling-page-owner")
        self.character = create_character(
            self.owner, name="Unlettered", location="Consult GM"
        )
        self.edit_url = f"/tarmar/characters/{self.character.pk}/"
        self.rule_url = f"/tarmar/characters/{self.character.pk}/rule-literacy/"

    def test_a_game_master_rules_and_gets_the_row_back(self):
        self.login_as(self.create_dm("ruling-dm"))

        response = self.post_json(self.rule_url, {"literate": False})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(
            {key: data[key] for key in LITERACY_RESPONSE_KEYS},
            {
                "location_literacy_check": "Consult GM",
                "location_literacy_modifier": None,
                "literacy_roll": None,
                "is_literate": False,
                "literacy_result": "Illiterate (Game Master's ruling)",
                "can_roll_literacy": False,
                "can_change_origin": True,
                "literacy_ruling": False,
                "can_rule_literacy": True,
            },
        )
        page = self.client.get(self.edit_url).content.decode()
        self.assertIn('id="literacy-ruling-select"', page)
        self.assertRegex(page, r'<option value="illiterate" selected>')

    def test_the_ruling_must_be_true_false_or_null(self):
        self.login_as(self.create_dm("ruling-dm-typo"))

        response = self.post_json(self.rule_url, {"literate": "yes"})

        self.assertEqual(response.status_code, 400)
        self.character.refresh_from_db()
        self.assertIsNone(self.character.literacy_ruling)

    def test_the_owner_who_is_a_player_is_refused_and_has_no_control(self):
        self.login_as(self.owner)

        response = self.post_json(self.rule_url, {"literate": True})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["error"], services.LITERACY_RULING_GAME_MASTER_ONLY
        )
        self.character.refresh_from_db()
        self.assertIsNone(self.character.literacy_ruling)
        self.assertNotIn(
            'id="literacy-ruling-select"',
            self.client.get(self.edit_url).content.decode(),
        )

    def test_the_control_is_hidden_away_from_consult_gm(self):
        self.character.location = "Village"
        self.character.save()
        self.login_as(self.create_dm("ruling-dm-village"))

        page = self.client.get(self.edit_url).content.decode()

        self.assertRegex(page, r'<span[^>]*id="literacy-ruling-control"[^>]*hidden')
