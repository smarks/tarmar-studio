"""The background a character is created with, and the skill its Origin grants (#754).

location.md: "Roll percentile dice or choose", and "City: Street smarts ...
Village: Street smarts OR survival (choose) ... Rural: Survival".
provenance.md and wealth.md are percentile tables too, and Spencer's ruling on
#749 holds a player to one roll of each, with a Game Master free to set or
roll again.

Two defects, both confirmed by the 2026-09-28 rescan:

* The Origin's skill was granted at creation only, and never followed the
  Origin when it changed: City to Rural kept Street Smarts and added no
  Survival (scratchpad/rescan/paths/test_paths.py,
  ``test_origin_change_leaves_the_old_origins_skill``).
* Nothing but the creation page rolled the background, so a character started
  anywhere else held model defaults nobody rolled. ``roll_background`` rolls
  it, through the same table functions and in the same dice-log lines.
"""

import importlib
import json
from unittest.mock import patch

from django.apps import apps
from django.urls import reverse

from character_export.export_utils import generate_character_json
from characters import services
from characters.models import Character
from characters.rules import character_rules
from characters.services import (
    BACKGROUND_ALREADY_ROLLED,
    ORIGIN_FIXED_REASON,
    CharacterChangeError,
)
from common.test_helpers import BaseTestCase, create_character
from gm.models import GMSession, SessionCharacter

MIGRATION = importlib.import_module("characters.migrations.0021_location_recorded")

#: d100 faces for provenance (class, detail), origin and wealth, in the order
#: the creation page rolls them: Commoner, Laborer-range detail, Rural,
#: Moderate.
RURAL_MODERATE_COMMONER = [40, 3, 80, 50]


def d100(*faces: int):
    """Patch every percentile roll to come up ``faces``, in order."""
    return patch("characters.dice.roll_d100", side_effect=list(faces))


def unrolled(user, **overrides) -> Character:
    """A character nobody rolled a background for, as a session once made."""
    fields = {
        "provenance_recorded": False,
        "wealth_recorded": False,
        "location_recorded": False,
        "skills": {},
        **overrides,
    }
    return create_character(user, **fields)


def granted(user, location: str, skills: dict, origin_skill: str, **overrides):
    """A character whose Origin's grant is on the record, held in ``origin_skill``."""
    return create_character(
        user,
        location=location,
        skills=skills,
        origin_skill=origin_skill,
        **overrides,
    )


class OriginSkillFollowsTheOriginTest(BaseTestCase):
    """The Origin's point moves with the Origin, wherever the Origin is set."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("origin-player")

    def move(self, character: Character, origin: str, *, is_game_master=False):
        services.update_field(
            character.pk, "location", origin, is_game_master=is_game_master
        )
        character.refresh_from_db()
        return character

    def test_city_to_rural_swaps_street_smarts_for_survival(self):
        """The rescan's reproduction, asserted the right way round."""
        character = granted(self.player, "City", {"street smarts": 1}, "street smarts")

        self.move(character, "Rural")

        self.assertEqual(
            character.skills, {"survival": character_rules().location.skill_points}
        )
        self.assertEqual(character.origin_skill, "survival")

    def test_rural_to_city_swaps_survival_for_street_smarts(self):
        character = granted(self.player, "Rural", {"survival": 1}, "survival")

        self.move(character, "City")

        self.assertEqual(
            character.skills, {"street smarts": character_rules().location.skill_points}
        )

    def test_points_bought_past_the_grant_stay_and_nothing_leaks(self):
        """Street Smarts 2 is the City's point and one bought. Only the grant
        moves; going back and forth neither loses the bought point nor makes
        one (review of #754: the 1-point guess kept 2 and added Survival, a
        point from nowhere, on every trip)."""
        character = granted(self.player, "City", {"street smarts": 2}, "street smarts")

        self.move(character, "Rural")
        self.assertEqual(character.skills, {"street smarts": 1, "survival": 1})
        self.move(character, "City")
        self.assertEqual(character.skills, {"street smarts": 2})
        self.move(character, "Rural")
        self.assertEqual(character.skills, {"street smarts": 1, "survival": 1})

    def test_a_bought_point_in_the_new_origins_skill_is_added_to(self):
        """A City character who bought Survival 1 keeps it and gets the Rural
        point on top; the bought point is never taken for the grant."""
        character = granted(
            self.player,
            "City",
            {"street smarts": 1, "survival": 1},
            "street smarts",
        )

        self.move(character, "Rural")
        self.assertEqual(character.skills, {"survival": 2})
        self.move(character, "City")
        self.assertEqual(character.skills, {"street smarts": 1, "survival": 1})

    def test_a_village_with_both_skills_loses_no_point(self):
        """The review's reproduction: Street Smarts (the grant) and Survival
        (bought) at 1 each, moved to Rural, lost the bought point."""
        character = granted(
            self.player,
            "Village",
            {"street smarts": 1, "survival": 1},
            "street smarts",
        )

        self.move(character, "Rural")

        self.assertEqual(character.skills, {"survival": 2})

    def test_city_to_village_keeps_street_smarts_and_adds_nothing(self):
        character = granted(self.player, "City", {"street smarts": 1}, "street smarts")

        self.move(character, "Village")

        self.assertEqual(character.skills, {"street smarts": 1})
        self.assertEqual(character.origin_skill, "street smarts")

    def test_a_village_survival_is_the_rural_grant_already(self):
        """Village grants Street Smarts OR Survival; one who took Survival
        and moves to Rural holds the Rural grant and gets no second point."""
        character = granted(self.player, "Village", {"survival": 1}, "survival")

        self.move(character, "Rural")

        self.assertEqual(character.skills, {"survival": 1})

    def test_a_village_survival_moves_to_street_smarts_in_the_city(self):
        character = granted(self.player, "Village", {"survival": 1}, "survival")

        self.move(character, "City")

        self.assertEqual(character.skills, {"street smarts": 1})

    def test_consult_gm_takes_the_grant_back_and_gives_none(self):
        character = granted(
            self.player, "Rural", {"survival": 1, "sword": 2}, "survival"
        )

        self.move(character, "Consult GM", is_game_master=True)
        self.assertEqual(character.skills, {"sword": 2})
        self.assertEqual(character.origin_skill, "")
        self.move(character, "City", is_game_master=True)
        self.assertEqual(character.skills, {"sword": 2, "street smarts": 1})

    def test_a_skill_that_was_never_the_grant_is_left_alone(self):
        """No grant on the record (a character from before #754 that holds no
        grant skill): nothing is taken, and the new Origin's point is given."""
        character = granted(self.player, "City", {"sword": 1}, "")

        self.move(character, "Rural")

        self.assertEqual(character.skills, {"sword": 1, "survival": 1})

    def test_the_move_is_in_the_history(self):
        character = granted(self.player, "City", {"street smarts": 1}, "street smarts")

        self.move(character, "Rural")

        skill_lines = [
            entry["new"] for entry in character.history if entry["field"] == "skills"
        ]
        self.assertEqual(len(skill_lines), 2, character.history)
        self.assertIn("street smarts 1 taken back", skill_lines[0])
        self.assertIn("survival 1 granted by the Rural origin", skill_lines[1])

    def test_a_game_master_moving_a_rolled_origin_moves_the_skill_and_rerolls(self):
        """set_background, the chat's path: the skill follows, and literacy is
        rolled again against the new Origin as #741 has it."""
        character = granted(
            self.player,
            "Village",
            {"street smarts": 1},
            "street smarts",
            literacy_roll=9,
            literacy_rolled=True,
        )

        with patch(
            "characters.services.roll_literacy_logged",
            return_value=(5, "3d6: [3, 1, 1] = 5"),
        ):
            result = services.set_background(
                character.pk, {"location": "Rural"}, is_game_master=True
            )

        character.refresh_from_db()
        self.assertEqual(character.skills, {"survival": 1})
        self.assertEqual(character.literacy_roll, 5)
        self.assertIn("survival 1 granted by the Rural origin", result.note)
        self.assertIn("literacy rerolled because Origin changed", result.note)

    def test_the_editor_moves_the_skill_and_returns_the_list(self):
        """The sheet showed the old Origin's skill until a reload (the #740
        class): the response carries the list as the page renders it."""
        character = granted(self.player, "City", {"street smarts": 1}, "street smarts")
        self.login_as(self.player)

        response = self.post_json(
            reverse("characters:update_field", args=[character.pk]),
            {"field": "location", "value": "Rural"},
        )

        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertIn("Survival", data["skills_html"])
        self.assertNotIn("Street Smarts", data["skills_html"])
        self.assertEqual(data["total_allocated_points"], 1)
        self.assertTrue(data["location_recorded"])
        character.refresh_from_db()
        self.assertEqual(character.skills, {"survival": 1})

    def test_choosing_an_origin_nobody_rolled_grants_its_skill(self):
        """A Village nobody chose or rolled carries no grant; choosing it,
        even the same Village, is the first time the Origin is set."""
        character = unrolled(self.player, location="Village")

        self.move(character, "Village")

        self.assertTrue(character.location_recorded)
        self.assertEqual(character.skills, {"street smarts": 1})
        self.assertEqual(character.origin_skill, "street smarts")

    def test_renaming_the_granted_skill_moves_the_grant_with_it(self):
        """location.md's Village choice is made by renaming the granted
        skill; the grant is then held in Survival."""
        character = granted(
            self.player, "Village", {"street smarts": 1}, "street smarts"
        )
        self.login_as(self.player)

        response = self.post_json(
            reverse("characters:rename_skill", args=[character.pk]),
            {"old_name": "Street Smarts", "new_name": "Survival"},
        )

        self.assertEqual(response.status_code, 200, response.content)
        character.refresh_from_db()
        self.assertEqual(character.origin_skill, "survival")
        self.move(character, "City")
        self.assertEqual(character.skills, {"street smarts": 1})


class BackgroundRollTest(BaseTestCase):
    """``roll_background``: a player rolls what nobody rolled, once."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("background-player")

    def test_a_player_rolls_an_unrolled_background_in_the_creation_format(self):
        character = unrolled(self.player, dice_log="Appearance: 1d6: 3")

        with d100(*RURAL_MODERATE_COMMONER):
            services.roll_background(character.pk)

        character.refresh_from_db()
        self.assertEqual(
            (character.provenance_class, character.location, character.wealth_level),
            ("commoner", "Rural", "Moderate"),
        )
        self.assertTrue(character.provenance_recorded)
        self.assertTrue(character.location_recorded)
        self.assertTrue(character.wealth_recorded)
        self.assertEqual(character.starting_coin, 100)
        self.assertEqual(
            character.skills, {"survival": character_rules().location.skill_points}
        )
        log = character.dice_log.splitlines()
        # The lines the creation page writes, in its order.
        self.assertEqual(log[0], "Appearance: 1d6: 3")
        self.assertTrue(log[1].startswith("Provenance: d100=40 -> Commoner"), log)
        self.assertEqual(log[2], "Location: d100=80 -> Rural")
        self.assertTrue(log[3].startswith("Location effects: Survival 1 point"), log)
        self.assertEqual(log[4], "Wealth: d100=50 -> Moderate, starting coin = 100")

    def test_a_player_rolls_once(self):
        character = unrolled(self.player)
        with d100(*RURAL_MODERATE_COMMONER):
            services.roll_background(character.pk)

        with self.assertRaisesMessage(CharacterChangeError, BACKGROUND_ALREADY_ROLLED):
            services.roll_background(character.pk)

    def test_a_player_rolls_only_what_is_not_recorded(self):
        character = unrolled(
            self.player,
            provenance_recorded=True,
            location_recorded=True,
            provenance_class="merchant",
            provenance_detail="Trader",
            location="City",
            skills={"street smarts": 1},
        )

        with d100(95, 25):
            result = services.roll_background(character.pk)

        character.refresh_from_db()
        self.assertEqual(
            (character.provenance_class, character.location), ("merchant", "City")
        )
        self.assertEqual(character.wealth_level, "Merchant")
        self.assertIn("rolled wealth", result.note)
        self.assertNotIn("provenance", result.note)

    def test_a_player_cannot_roll_an_origin_fixed_by_literacy(self):
        """The literacy check was rolled against this Origin (#741), so the
        player's roll leaves it; the rest is theirs to roll."""
        character = unrolled(self.player, literacy_roll=9, literacy_rolled=True)

        with d100(40, 3, 50):
            services.roll_background(character.pk)

        character.refresh_from_db()
        self.assertEqual(character.location, "Village")
        self.assertFalse(character.location_recorded)
        self.assertTrue(character.provenance_recorded and character.wealth_recorded)
        with self.assertRaisesMessage(CharacterChangeError, ORIGIN_FIXED_REASON):
            services.roll_background(character.pk)

    def test_a_game_master_rolls_it_all_again_and_the_history_keeps_it(self):
        character = create_character(
            self.player,
            provenance_class="merchant",
            provenance_detail="Trader",
            location="City",
            skills={"street smarts": 1},
            origin_skill="street smarts",
            wealth_level="Subsistence",
            starting_coin=10,
            intelligence=12,
            literacy_roll=9,
            literacy_rolled=True,
        )

        with (
            d100(*RURAL_MODERATE_COMMONER),
            patch(
                "characters.services.roll_literacy_logged",
                return_value=(6, "3d6: [4, 1, 1] = 6"),
            ),
        ):
            services.roll_background(character.pk, is_game_master=True)

        character.refresh_from_db()
        self.assertEqual(
            (character.provenance_class, character.location, character.wealth_level),
            ("commoner", "Rural", "Moderate"),
        )
        self.assertEqual(character.skills, {"survival": 1})
        self.assertEqual(character.literacy_roll, 6)
        changed = {entry["field"]: entry["old"] for entry in character.history}
        self.assertEqual(changed["provenance_class"], "merchant")
        self.assertEqual(changed["location"], "City")
        self.assertEqual(changed["wealth_level"], "Subsistence")

    def test_a_player_may_not_choose_over_a_rolled_origin(self):
        """A rolled Origin is final for a player (Spencer's ruling on #970).

        This asserted that a player whose Origin was rolled could choose
        another and the grant followed, which was the bug: "choose or roll"
        read as "roll, and choose instead if the roll was bad". The grant
        following a chosen Origin is pinned by OriginSkillFollowsTheOriginTest.
        """
        character = unrolled(self.player)
        with d100(*RURAL_MODERATE_COMMONER):
            services.roll_background(character.pk)

        with self.assertRaisesMessage(
            CharacterChangeError, services.roll_fixed_reason("location")
        ):
            services.update_field(character.pk, "location", "City")

        character.refresh_from_db()
        self.assertEqual(character.location, "Rural")
        self.assertEqual(character.skills, {"survival": 1})


class OriginRollLogTest(BaseTestCase):
    """The dice log says what the Origin roll granted, not what it might."""

    def test_a_reroll_onto_the_same_origin_logs_the_grant_it_kept(self):
        """The review's reproduction: a Village character holding the grant
        in Survival rerolled Village, and the log claimed a new Street Smarts
        point that was never given."""
        character = granted(
            self.create_user("log-player"), "Village", {"survival": 1}, "survival"
        )

        with d100(50):
            services.roll_on_sheet(character.pk, "origin", is_game_master=True)

        character.refresh_from_db()
        self.assertEqual(character.skills, {"survival": 1})
        self.assertEqual(
            character.dice_log.splitlines()[-1],
            "Location effects: survival 1 point kept from before, Literacy check "
            "INT - 2",
        )

    def test_a_first_roll_logs_the_creation_pages_effects_line(self):
        character = unrolled(self.create_user("log-player"))

        with d100(10):
            services.roll_on_sheet(character.pk, "origin")

        character.refresh_from_db()
        self.assertEqual(
            character.dice_log.splitlines()[-2:],
            [
                "Location: d100=10 -> City",
                "Location effects: Street Smarts 1 point, Literacy check INT",
            ],
        )


class SheetOriginRollTest(BaseTestCase):
    """The sheet offers the Origin roll location.md gives (issue #858)."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("sheet-origin-player")

    def offer(self, character: Character, *, is_game_master: bool = False):
        return services.sheet_rolls_for(character, is_game_master=is_game_master)[
            "origin"
        ]

    def test_a_player_rolls_an_origin_nobody_rolled_once(self):
        character = unrolled(self.player)
        self.assertTrue(self.offer(character).available)

        with d100(80):
            services.roll_on_sheet(character.pk, "origin")

        character.refresh_from_db()
        self.assertEqual(
            (character.location, character.skills), ("Rural", {"survival": 1})
        )
        self.assertFalse(self.offer(character).available)
        with self.assertRaises(CharacterChangeError):
            services.roll_on_sheet(character.pk, "origin")

    def test_a_game_master_rolls_again_after_a_confirm(self):
        character = granted(self.player, "City", {"street smarts": 1}, "street smarts")

        offer = self.offer(character, is_game_master=True)

        self.assertTrue(offer.available)
        self.assertIn("Roll the origin again?", offer.confirm)

    def test_an_origin_fixed_by_literacy_is_not_offered_to_a_player(self):
        character = unrolled(self.player, literacy_roll=9, literacy_rolled=True)

        self.assertFalse(self.offer(character).available)

    def test_the_sheet_shows_the_roll_and_marks_an_unrolled_origin(self):
        character = unrolled(self.player)
        self.login_as(self.player)

        page = self.client.get(reverse("characters:edit", args=[character.pk]))

        self.assertContains(page, 'id="roll-origin-btn"')
        self.assertContains(page, 'id="origin-not-rolled">Not rolled or chosen yet')

    def test_a_recorded_origin_is_not_marked_and_not_offered(self):
        character = granted(self.player, "City", {"street smarts": 1}, "street smarts")
        self.login_as(self.player)

        page = self.client.get(reverse("characters:edit", args=[character.pk]))

        self.assertNotContains(page, 'id="roll-origin-btn"')
        self.assertContains(page, 'id="origin-not-rolled" hidden')


class LiteracyWaitsForTheOriginTest(BaseTestCase):
    """A player rolls literacy against an Origin somebody rolled or chose.

    The review's reproduction (row 837): a player rolled literacy while the
    Origin was unrecorded; the roll fixed it, the Origin roll disappeared,
    roll_background skipped it, and the character kept the model's Village,
    no grant and "(not rolled)" for good. The coordinator's ruling: for a
    player, literacy waits for the Origin; a Game Master is not held to it.
    """

    def setUp(self):
        super().setUp()
        self.player = self.create_user("literacy-origin-player")

    def test_a_player_is_refused_and_the_button_hidden(self):
        character = unrolled(self.player, intelligence=12)

        self.assertFalse(services.can_roll_literacy(character, is_game_master=False))
        with self.assertRaisesMessage(
            CharacterChangeError, services.LITERACY_BEFORE_ORIGIN_REASON
        ):
            services.roll_literacy(character.pk)
        self.login_as(self.player)
        page = self.client.get(reverse("characters:edit", args=[character.pk]))
        self.assertContains(
            page, 'id="roll-literacy-btn" class="button secondary" hidden'
        )

    def test_choosing_the_origin_offers_the_roll(self):
        character = unrolled(self.player, intelligence=12)
        self.login_as(self.player)

        response = self.post_json(
            reverse("characters:update_field", args=[character.pk]),
            {"field": "location", "value": "City"},
        )

        self.assertTrue(response.json()["can_roll_literacy"])
        services.roll_literacy(character.pk)
        character.refresh_from_db()
        self.assertTrue(character.literacy_rolled)

    def test_a_game_master_is_not_held_to_it(self):
        character = unrolled(self.player, intelligence=12)

        self.assertTrue(services.can_roll_literacy(character, is_game_master=True))
        services.roll_literacy(character.pk, is_game_master=True)

        character.refresh_from_db()
        self.assertTrue(character.literacy_rolled)


class GameMasterLiteracyAdoptsTheOriginTest(BaseTestCase):
    """A Game Master's literacy roll on an Origin nobody rolled or chose.

    The roll fixes the Origin for the player (can_change_origin), so it makes
    that Origin the record and grants its skill, as migration 0021 does for a
    check already rolled; the player is never left waiting on an Origin roll
    they can no longer make.
    """

    def test_the_village_is_recorded_and_granted(self):
        character = unrolled(self.create_user("adopt-player"), intelligence=12)

        result = services.roll_literacy(character.pk, is_game_master=True)

        character.refresh_from_db()
        self.assertTrue(character.location_recorded)
        self.assertEqual(character.skills, {"street smarts": 1})
        self.assertEqual(character.origin_skill, "street smarts")
        self.assertIn("the Village origin recorded", result.note)
        self.assertEqual(character.background_summary.count("not rolled"), 2)

    def test_a_recorded_origin_is_left_as_it_is(self):
        character = granted(
            self.create_user("adopt-player"),
            "Rural",
            {"survival": 1},
            "survival",
            intelligence=12,
        )

        result = services.roll_literacy(character.pk, is_game_master=True)

        character.refresh_from_db()
        self.assertEqual(character.skills, {"survival": 1})
        self.assertNotIn("origin recorded", result.note)


class ImportedGrantTest(BaseTestCase):
    """A Game Master's verbatim import of a file written before #754."""

    def test_a_file_without_origin_skill_has_its_grant_read_off_its_skills(self):
        """The review's reproduction: imported with no grant on the record,
        a City to Rural and back left Street Smarts 3 from 2."""
        from characters.import_service import (
            save_imported_character,
            validate_import_payload,
        )

        game_master = self.create_dm("import-gm")
        source = create_character(
            game_master, location="City", skills={"street smarts": 2}
        )
        payload = json.loads(generate_character_json(source))
        payload["data"].pop("origin_skill")

        character = save_imported_character(
            game_master, validate_import_payload(payload)
        )

        self.assertEqual(character.origin_skill, "street smarts")
        services.update_field(character.pk, "location", "Rural", is_game_master=True)
        services.update_field(character.pk, "location", "City", is_game_master=True)
        character.refresh_from_db()
        self.assertEqual(character.skills, {"street smarts": 2})

    def test_imported_skill_names_are_stored_normalised(self):
        """A file's "Street Smarts" is stored as "street smarts", so the grant
        is found and moved; names that meet add their points."""
        from characters.import_service import validate_import_payload

        game_master = self.create_dm("import-gm")
        payload = json.loads(
            generate_character_json(create_character(game_master, location="City"))
        )
        payload["data"].pop("origin_skill")
        payload["data"]["skills"] = {"Street Smarts": 1, "Sword": 2, "sword": 1}

        imported = validate_import_payload(payload)

        self.assertEqual(imported.fields["skills"], {"street smarts": 1, "sword": 3})
        self.assertEqual(imported.fields["origin_skill"], "street smarts")

    def test_held_origin_skill_follows_the_migrations_rule(self):
        self.assertEqual(
            services.held_origin_skill("Village", {"Survival": 2, "sword": 1}),
            "survival",
        )
        self.assertEqual(services.held_origin_skill("City", {"survival": 1}), "")
        self.assertEqual(services.held_origin_skill("Consult GM", {"survival": 1}), "")
        self.assertEqual(services.held_origin_skill("City", None), "")


class BackgroundSummaryTest(BaseTestCase):
    """One line naming the background and what of it is not rolled yet."""

    def test_a_recorded_background(self):
        character = create_character(
            self.create_user("summary-player"),
            location="Rural",
            provenance_class="merchant",
            provenance_detail="Trader",
            wealth_level="Rich",
            starting_coin=None,
        )

        self.assertEqual(
            character.background_summary,
            "origin Rural; provenance Merchant (Trader); wealth Rich, Consult GM "
            "for starting coin",
        )

    def test_an_unrolled_background_says_so(self):
        character = unrolled(self.create_user("summary-player"))

        self.assertEqual(
            character.background_summary,
            "origin Village (not rolled); provenance Commoner (Laborer) (not "
            "rolled); wealth Moderate, 100 starting coin (not rolled)",
        )


class LocationRecordedMigrationTest(BaseTestCase):
    """What ``0021`` records for existing characters."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("migrated-origin")
        self.session = GMSession.objects.create(user=self.player, name="s")

    def session_built(self, **fields) -> Character:
        character = create_character(self.player, **fields)
        SessionCharacter.objects.create(session=self.session, character=character)
        return character

    def migrate(self) -> dict[int, Character]:
        MIGRATION.record_origins(apps, None)
        return {character.pk: character for character in Character.objects.all()}

    def test_a_default_village_nobody_rolled_or_chose_is_unrecorded(self):
        chat_built = self.session_built(location="Village")

        after = self.migrate()[chat_built.pk]

        self.assertFalse(after.location_recorded)
        self.assertEqual((after.skills, after.origin_skill), ({}, ""))

    def test_only_session_built_characters_are_marked(self):
        """A creation-page character, a player's import and a fixture keep
        their Origin; the first two record the grant they hold."""
        created = create_character(
            self.player,
            location="Village",
            skills={"street smarts": 1},
            dice_log="Location: d100=50 -> Village",
        )
        imported = create_character(
            self.player, location="Village", skills={"street smarts": 1}
        )
        fixture = create_character(self.player, location="Village")

        after = self.migrate()

        for character in (created, imported, fixture):
            self.assertTrue(after[character.pk].location_recorded)
        self.assertEqual(after[created.pk].origin_skill, "street smarts")
        self.assertEqual(after[imported.pk].origin_skill, "street smarts")
        self.assertEqual(after[fixture.pk].origin_skill, "")

    def test_a_village_literacy_was_rolled_against_is_kept_and_granted(self):
        """Marking it unrecorded left its player stuck: the Origin fixed by
        literacy, the roll refused, and no Village skill (review of #754)."""
        rolled = self.session_built(
            location="Village", literacy_roll=9, literacy_rolled=True
        )
        ruled = self.session_built(location="Village", literacy_ruling=True)

        after = self.migrate()

        for character in (rolled, ruled):
            migrated = after[character.pk]
            self.assertTrue(migrated.location_recorded)
            self.assertEqual(migrated.skills, {"street smarts": 1})
            self.assertEqual(migrated.origin_skill, "street smarts")
            self.assertIn(
                "street smarts 1 granted by the Village origin",
                migrated.history[-1]["new"],
            )

    def test_a_changed_origin_that_was_never_granted_is_granted(self):
        """The review's row 830: a chat-built Wren moved to Rural before #754,
        recorded, and never given Survival."""
        moved = self.session_built(
            location="Rural",
            skills={"sword": 2},
            history=[
                {
                    "ts": "t",
                    "field": "location",
                    "old": "Village",
                    "new": "Rural",
                    "by": "gm",
                }
            ],
        )

        migrated = self.migrate()[moved.pk]

        self.assertTrue(migrated.location_recorded)
        self.assertEqual(migrated.skills, {"sword": 2, "survival": 1})
        self.assertEqual(migrated.origin_skill, "survival")

    def test_a_held_grant_is_recorded_as_the_grant(self):
        village_survival = create_character(
            self.player,
            location="Village",
            skills={"survival": 3},
            dice_log="Location: d100=50 -> Village",
        )

        migrated = self.migrate()[village_survival.pk]

        self.assertEqual(migrated.origin_skill, "survival")
        self.assertEqual(migrated.skills, {"survival": 3})
