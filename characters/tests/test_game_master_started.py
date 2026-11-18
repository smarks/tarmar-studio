"""A skill only a Game Master starts (tarmar-studio #208).

True Magic comes by invitation, and the invitation is the Arcane skill. The
canon lists the skills a player never starts under ``game_master_started``
(``skill-rules.json``), which on the public pages is Arcane alone. Players are
held to it on every path that can write the skill, and a Game Master may
override on each:

* a free point: the sheet's allocate endpoint, held by
  ``services.apply_skill_points`` (the Games Master chat's
  ``spend_skill_points`` is held by the same function, and tested in
  ``gm/test_game_master_authority.py``);
* a rename into the skill;
* an import, and the Games Master chat's character extraction, which writes a
  file the importer reads;
* the lists that give a skill and ask nobody: a track's table, the Origin's
  grant, the background roll and the Origin skill a player's import grants.
  One system check holds those lists (``characters.E006``).

The refusals' words are configuration, so the tests run under the fixed words
(``characters.tests.fixed_refusals``).
"""

from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import patch

from django.core.checks import Error, run_checks
from django.test import SimpleTestCase
from django.urls import reverse

from accounts.models import User
from characters import services
from characters.checks import (
    GAME_MASTER_STARTED_CHECK_ID,
    check_game_master_started_skills,
    granted_game_master_started_skills,
)
from characters.equipment_rules import EquipmentCatalogError
from characters.import_service import (
    game_master_kept_skills,
    held_to_player,
    validate_import_payload,
)
from characters.refusal_words import refusal_words
from characters.rules import CharacterRules, CharacterRulesError
from characters.skill_rules import SkillRules, SkillRulesError, SkillTrackRules
from characters.skills import ARCANE_SKILL_NAME, format_skill_level
from characters.tests.fixed_refusals import with_fixed_refusals
from characters.tests.rules_fixtures import (
    DEFAULT_CHARACTER_RULES,
    DEFAULT_SKILL_RULES,
    seated_character_rules,
    seated_skill_rules,
)
from characters.tests.test_skill_rules import INVENTED_SKILL_RULES
from characters.views.characters import skill_name_suggestions
from common.test_helpers import BaseTestCase, create_character
from gm.character_schema import extraction_to_import_payload

if TYPE_CHECKING:
    # The test client's response type exists only in django-stubs.
    from django.test.client import _MonkeyPatchedWSGIResponse as TestResponse

#: Arcane as a canon's track table or Origin row would write it.
ARCANE_AS_WRITTEN = "Arcane"

#: Invented rules that leave two glossary skills a record never stores by
#: that name alone to a Game Master: Weapon, held as the weapon's name, and
#: Maker Skill, held with its craft.
WEAPON_AND_MAKER_SKILL_BY_INVITATION = replace(
    INVENTED_SKILL_RULES, game_master_started=("weapon", "maker skill")
)


def refused_to_start(skill: str) -> str:
    """What a player who does not hold ``skill`` is told, in the loaded words."""
    return refusal_words().services.game_master_started_skill.format(skill=skill)


def rules_whose_track_grants(track_key: str, *written: str) -> SkillRules:
    """The default skill rules with one track's whole table given over to
    ``written``, entry after entry."""
    tracks = DEFAULT_SKILL_RULES.tracks.tracks
    table = tuple(written[face % len(written)] for face in range(20))
    return replace(
        DEFAULT_SKILL_RULES,
        tracks=SkillTrackRules(
            tracks={**tracks, track_key: replace(tracks[track_key], skills=table)}
        ),
    )


def rules_whose_origins_grant(written: str) -> CharacterRules:
    """The default character rules with every Origin that grants a skill
    granting ``written`` instead."""
    location = DEFAULT_CHARACTER_RULES.location
    return replace(
        DEFAULT_CHARACTER_RULES,
        location=replace(
            location,
            rows=tuple(
                replace(row, skills=(written,)) if row.skills else row
                for row in location.rows
            ),
        ),
    )


@with_fixed_refusals
class AllocationRuleTest(SimpleTestCase):
    """``services.allocation_refusal``: the rule itself."""

    def test_a_player_may_not_start_arcane(self):
        self.assertEqual(
            refused_to_start(ARCANE_SKILL_NAME),
            "services.game_master_started_skill arcane",
        )
        for asked in ("arcane", "Arcane", "Arcane II"):
            with self.subTest(asked=asked):
                self.assertEqual(
                    services.allocation_refusal({"sword": 3}, asked),
                    refused_to_start(ARCANE_SKILL_NAME),
                )

    def test_a_player_who_holds_arcane_may_raise_it(self):
        self.assertIsNone(services.allocation_refusal({"arcane": 2}, "Arcane"))

    def test_the_skills_are_the_canons_not_arcane_by_name(self):
        # The invented rules leave Sorcery and Fate to a Game Master and say
        # nothing of Arcane, which they do not name at all.
        with seated_skill_rules(INVENTED_SKILL_RULES):
            for skill in ("sorcery", "fate"):
                with self.subTest(skill=skill):
                    self.assertEqual(
                        services.allocation_refusal({}, skill), refused_to_start(skill)
                    )
                    self.assertIsNone(services.allocation_refusal({skill: 1}, skill))
            self.assertIsNone(services.allocation_refusal({}, "rope"))
            self.assertEqual(
                services.allocation_refusal({}, "arcane"),
                services.ALLOCATION_REFUSED_REASON,
            )

    def test_a_canon_that_lists_none_refuses_none(self):
        with seated_skill_rules(replace(DEFAULT_SKILL_RULES, game_master_started=())):
            self.assertIsNone(services.allocation_refusal({}, "arcane"))

    def test_a_weapon_or_a_chosen_part_is_held_by_the_skill_it_falls_under(self):
        # The list names glossary skills. A record stores a Weapon skill as
        # its weapon and a Maker Skill with its craft, so the rule is asked
        # of the skill each falls under, never of the stored name.
        with seated_skill_rules(WEAPON_AND_MAKER_SKILL_BY_INVITATION):
            for asked, stored in (
                ("Broadsword", "broadsword"),
                ("Weapon (Broadsword)", "weapon (broadsword)"),
                ("Weapon", "weapon"),
                ("Maker Skill (Smith)", "maker skill (smith)"),
            ):
                with self.subTest(asked=asked):
                    self.assertEqual(
                        services.allocation_refusal({"rope": 2}, asked),
                        refused_to_start(stored),
                    )
                    self.assertIsNone(services.allocation_refusal({stored: 1}, asked))
            self.assertIsNone(services.allocation_refusal({}, "rope"))


@with_fixed_refusals
class SheetAllocationTest(BaseTestCase):
    """The sheet's allocate endpoint, for a player and for a Game Master."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("uninvited-player")
        self.game_master = self.create_dm("inviting-gm")
        self.character = create_character(
            self.owner, free_skill_points=4, skills={"sword": 2}
        )
        self.url = reverse("characters:allocate_skill", args=[self.character.pk])

    def allocate(self, user: User, skill: str = "Arcane") -> "TestResponse":
        self.logout()
        self.login_as(user)
        return self.post_json(self.url, {"skill": skill})

    def test_a_player_is_refused_and_spends_nothing(self):
        response = self.allocate(self.owner)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], refused_to_start(ARCANE_SKILL_NAME))
        self.character.refresh_from_db()
        self.assertEqual(self.character.skills, {"sword": 2})
        self.assertEqual(self.character.free_skill_points, 4)

    def test_a_game_master_starts_it_and_the_history_says_so(self):
        response = self.allocate(self.game_master)
        self.assertEqual(response.status_code, 200, response.content)
        self.character.refresh_from_db()
        self.assertEqual(self.character.skills, {"sword": 2, "arcane": 1})
        self.assertIn(
            f"arcane {services.GAME_MASTER_STARTED_SKILL_NOTE}",
            self.character.history[-1]["new"],
        )
        self.assertEqual(self.character.history[-1]["by"], self.game_master.username)

    def test_the_player_then_raises_it_with_their_own_points_and_no_note(self):
        self.assertEqual(self.allocate(self.game_master).status_code, 200)
        response = self.allocate(self.owner)
        self.assertEqual(response.status_code, 200, response.content)
        self.character.refresh_from_db()
        # The Game Master's point and the player's: two points buy level I at
        # Arcane's cost, and each came off the free points.
        self.assertEqual(self.character.skills["arcane"], 2)
        self.assertEqual(self.character.free_skill_points, 2)
        self.assertEqual(response.json()["points"], 2)
        self.assertEqual(format_skill_level(2, ARCANE_SKILL_NAME), "I")
        self.assertNotIn(
            services.GAME_MASTER_STARTED_SKILL_NOTE, self.character.history[-1]["new"]
        )

    def test_a_game_master_with_no_free_point_grants_the_first(self):
        self.character.free_skill_points = 0
        self.character.save(update_fields=["free_skill_points"])
        response = self.allocate(self.game_master)
        self.assertEqual(response.status_code, 200, response.content)
        self.character.refresh_from_db()
        self.assertEqual(self.character.skills["arcane"], 1)
        note = self.character.history[-1]["new"]
        self.assertIn("1 past the points earned, granted by the Game Master", note)
        self.assertIn(services.GAME_MASTER_STARTED_SKILL_NOTE, note)


class TypeAheadTest(BaseTestCase):
    """The add-skill input suggests Arcane only to a character who holds it."""

    def test_arcane_is_suggested_only_once_held(self):
        owner = self.create_user("suggested-to")
        without = create_character(owner, skills={"sword": 2})
        holder = create_character(owner, skills={"sword": 2, "arcane": 1})
        self.assertNotIn(ARCANE_SKILL_NAME, skill_name_suggestions(without))
        self.assertIn(ARCANE_SKILL_NAME, skill_name_suggestions(holder))

    def test_the_page_lists_it_only_once_held(self):
        owner = self.create_user("suggested-on-the-page")
        without = create_character(owner, free_skill_points=1)
        holder = create_character(owner, free_skill_points=1, skills={"arcane": 1})
        self.login_as(owner)
        # As the datalist writes a suggestion: the name in title case, and no
        # text (the skill check's own list writes its options otherwise).
        suggestion = '<option value="Arcane"></option>'
        without_page = self.client.get(reverse("characters:edit", args=[without.pk]))
        self.assertContains(without_page, '<option value="Perception"></option>')
        self.assertNotContains(without_page, suggestion)
        self.assertContains(
            self.client.get(reverse("characters:edit", args=[holder.pk])), suggestion
        )


class RenameTest(BaseTestCase):
    """A rename into Arcane moves points into it, which a player may not do."""

    def setUp(self):
        super().setUp()
        self.character = create_character(
            self.create_user("renaming-player"), skills={"perception": 6}
        )

    def test_a_player_may_not_rename_a_skill_into_arcane(self):
        with self.assertRaises(services.CharacterChangeError) as refused:
            services.apply_skill_rename(
                self.character, "perception", "arcane", is_game_master=False
            )
        self.assertEqual(str(refused.exception), services.RENAME_REFUSED_REASON)
        self.assertEqual(self.character.skills, {"perception": 6})

    def test_the_sheets_rename_endpoint_refuses_it_too(self):
        self.login_as(self.character.user)
        response = self.post_json(
            reverse("characters:rename_skill", args=[self.character.pk]),
            {"old_name": "perception", "new_name": "Arcane"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], services.RENAME_REFUSED_REASON)
        self.character.refresh_from_db()
        self.assertEqual(self.character.skills, {"perception": 6})

    def test_a_game_master_may_and_it_is_noted_as_his_call(self):
        recorded, points = services.apply_skill_rename(
            self.character, "perception", "arcane", is_game_master=True
        )
        self.assertEqual(self.character.skills, {"arcane": 6})
        self.assertEqual(points, 6)
        self.assertEqual(
            recorded,
            "arcane (points moved from another skill: "
            f"{refusal_words().common.the_game_masters_call})",
        )


class GrantingListsCheckTest(SimpleTestCase):
    """``characters.E006``: no track's table and no Origin's row names a skill
    only a Game Master starts."""

    def messages_for(
        self,
        skills: SkillRules = DEFAULT_SKILL_RULES,
        characters: CharacterRules = DEFAULT_CHARACTER_RULES,
    ) -> list[str]:
        """The E006 messages for a canon with these rules."""
        with (
            patch("characters.checks.skill_rules", return_value=skills),
            patch("characters.checks.character_rules", return_value=characters),
        ):
            return self.messages_for_refused()

    def test_the_loaded_canon_passes(self):
        self.assertEqual(check_game_master_started_skills(), [])
        self.assertEqual(
            granted_game_master_started_skills(
                DEFAULT_SKILL_RULES, DEFAULT_CHARACTER_RULES
            ),
            [],
        )

    def test_a_track_table_that_names_one_is_refused(self):
        # As written on the table, and with a level numeral: the grant reads
        # either as the stored skill.
        for written in (ARCANE_AS_WRITTEN, "arcane", "Arcane II"):
            with self.subTest(written=written):
                (message,) = self.messages_for(
                    skills=rules_whose_track_grants("magic", written, "Coins")
                )
                self.assertIn(
                    f"skill-rules.json tracks.magic.skills names {written!r}", message
                )
                self.assertIn("game_master_started leaves each to a Game", message)

    def test_a_weapon_on_a_table_is_refused_when_weapon_is_such_a_skill(self):
        # The table writes the weapon and the list names Weapon: the grant is
        # read as the skill it falls under.
        weapon_by_invitation = replace(
            rules_whose_track_grants("magic", "Broadsword", "Coins"),
            game_master_started=("weapon",),
        )
        (message,) = self.messages_for(skills=weapon_by_invitation)
        self.assertIn(
            "skill-rules.json tracks.magic.skills names 'Broadsword'", message
        )
        self.assertNotIn("names 'Coins'", message)

    def test_a_tracks_money_skill_that_names_one_is_refused(self):
        tracks = DEFAULT_SKILL_RULES.tracks.tracks
        paid_by_arcane = replace(
            DEFAULT_SKILL_RULES,
            tracks=SkillTrackRules(
                tracks={
                    **tracks,
                    "magic": replace(tracks["magic"], money_skill="arcane"),
                }
            ),
        )
        (message,) = self.messages_for(skills=paid_by_arcane)
        self.assertIn(
            "skill-rules.json tracks.magic.money_skill names 'arcane'", message
        )

    def test_an_origin_row_that_grants_one_is_refused(self):
        (message,) = self.messages_for(
            characters=rules_whose_origins_grant(ARCANE_AS_WRITTEN)
        )
        for row in DEFAULT_CHARACTER_RULES.location.rows:
            with self.subTest(row=row.label):
                names_the_row = (
                    f"character-rules.json location row {row.label!r} grants 'Arcane'"
                )
                self.assertEqual(names_the_row in message, bool(row.skills))

    def test_every_place_is_named_in_one_message(self):
        (message,) = self.messages_for(
            skills=rules_whose_track_grants("craft", ARCANE_AS_WRITTEN),
            characters=rules_whose_origins_grant(ARCANE_AS_WRITTEN),
        )
        self.assertIn("tracks.craft.skills names 'Arcane'", message)
        self.assertIn("location row 'City' grants 'Arcane'", message)

    def test_the_skills_are_the_canons(self):
        # Under rules that leave no skill to a Game Master, Arcane on a table
        # is any other skill.
        open_to_all = replace(
            rules_whose_track_grants("magic", ARCANE_AS_WRITTEN), game_master_started=()
        )
        self.assertEqual(self.messages_for(skills=open_to_all), [])

    def test_it_is_a_registered_error_with_a_hint(self):
        with patch(
            "characters.checks.skill_rules",
            return_value=rules_whose_track_grants("magic", ARCANE_AS_WRITTEN),
        ):
            messages = [
                message
                for message in run_checks()
                if message.id == GAME_MASTER_STARTED_CHECK_ID
            ]
        (message,) = messages
        self.assertIsInstance(message, Error)
        self.assertIn("game_master_started in skill-rules.json", message.hint or "")

    def test_a_refused_file_is_left_to_its_own_check(self):
        for reader, refusal in (
            ("skill_rules", SkillRulesError("skill-rules.json: is missing")),
            (
                "character_rules",
                CharacterRulesError("character-rules.json: is missing"),
            ),
            (
                "is_game_master_started",
                EquipmentCatalogError("equipment.json: is missing"),
            ),
        ):
            with (
                self.subTest(reader=reader),
                patch(f"characters.checks.{reader}", side_effect=refusal) as read,
            ):
                self.assertEqual(self.messages_for_refused(), [])
                read.assert_called()

    def messages_for_refused(self) -> list[str]:
        """The E006 messages with the loaders as the test has patched them."""
        return [
            str(message.msg)
            for message in check_game_master_started_skills()
            if message.id == GAME_MASTER_STARTED_CHECK_ID
        ]


class UngatedPathsReadTheCheckedListsTest(BaseTestCase):
    """Why the check is the gate: each of the four paths gives a character
    whatever its list names and asks nobody, so a canon that put Arcane on a
    list would hand it to a player. Each path is run under such a canon, and
    the check names the list it read."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("handed-arcane")

    def assert_the_check_names(
        self,
        expected: str,
        *,
        skills: SkillRules = DEFAULT_SKILL_RULES,
        characters: CharacterRules = DEFAULT_CHARACTER_RULES,
    ) -> None:
        granted = granted_game_master_started_skills(skills, characters)
        self.assertTrue(any(expected in place for place in granted), granted)

    def test_a_year_of_prior_experience(self):
        skills = rules_whose_track_grants("magic", ARCANE_AS_WRITTEN)
        character = create_character(self.owner)
        with (
            seated_skill_rules(skills),
            patch("characters.skills.roll_survival_dice", return_value=(6, 6, 6, 18)),
        ):
            services.apply_experience_years(character, "magic", 1)
        self.assertIn(ARCANE_SKILL_NAME, character.skills)
        self.assert_the_check_names("tracks.magic.skills", skills=skills)

    def test_the_origins_grant(self):
        characters = rules_whose_origins_grant(ARCANE_AS_WRITTEN)
        character = create_character(self.owner, location="City", skills={})
        with seated_character_rules(characters):
            services.apply_origin_skill(character, None)
        self.assertEqual(character.skills, {ARCANE_SKILL_NAME: 1})
        self.assert_the_check_names("location row 'City'", characters=characters)

    def test_the_background_roll(self):
        characters = rules_whose_origins_grant(ARCANE_AS_WRITTEN)
        character = create_character(
            self.owner,
            skills={},
            location_recorded=False,
            provenance_recorded=False,
            wealth_recorded=False,
        )
        # A face that lands on an Origin with a grant: City is the first row.
        with (
            seated_character_rules(characters),
            patch("characters.dice.roll_d100", return_value=1),
        ):
            changed = services.roll_background(character.pk, is_game_master=False)
        self.assertEqual(changed.character.location, "City")
        self.assertEqual(changed.character.skills, {ARCANE_SKILL_NAME: 1})
        self.assert_the_check_names("location row 'City'", characters=characters)

    def test_an_origin_chosen_in_the_chat(self):
        # The Games Master chat's set_background, for a character whose
        # Origin nobody has rolled or chosen: naming it grants its skill.
        characters = rules_whose_origins_grant(ARCANE_AS_WRITTEN)
        character = create_character(self.owner, skills={}, location_recorded=False)
        with seated_character_rules(characters):
            changed = services.set_background(
                character.pk, {"location": "Rural"}, is_game_master=False
            )
        self.assertEqual(changed.character.skills, {ARCANE_SKILL_NAME: 1})
        self.assert_the_check_names("location row 'Rural'", characters=characters)

    def test_the_origin_skill_a_players_import_grants(self):
        characters = rules_whose_origins_grant(ARCANE_AS_WRITTEN)
        payload = {
            "_format": "tarmar-character",
            "data": {"name": "Handed Arcane", "location": "Rural"},
        }
        with seated_character_rules(characters):
            held = held_to_player(
                validate_import_payload(payload), is_game_master=False
            )
        self.assertEqual(held.fields["skills"], {ARCANE_SKILL_NAME: 1})
        self.assert_the_check_names("location row 'Rural'", characters=characters)


@with_fixed_refusals
class ImportAndExtractionTest(BaseTestCase):
    """A file is not an invitation: a player's import drops Arcane and says
    so, and a Game Master's keeps it and records that he did. The Games
    Master chat's character extraction writes such a file."""

    #: What the model's extraction answers with: skills as a list.
    EXTRACTED = {
        "name": "Extracted Mage",
        "location": "Rural",
        "skills": [
            {"name": "Arcane", "points": 6},
            {"name": "Perception", "points": 3},
        ],
    }

    def test_the_line_names_each_such_skill_and_no_other(self):
        self.assertEqual(
            game_master_kept_skills({"sword": 21, "arcane": 6}),
            ["import_service.game_master_kept_skill 6 arcane"],
        )
        self.assertEqual(game_master_kept_skills({"sword": 21}), [])
        with seated_skill_rules(INVENTED_SKILL_RULES):
            self.assertEqual(
                game_master_kept_skills({"fate": 1, "arcane": 6, "sorcery": 2}),
                [
                    "import_service.game_master_kept_skill 1 fate",
                    "import_service.game_master_kept_skill 2 sorcery",
                ],
            )

    def test_the_line_names_a_weapon_or_a_chosen_part_under_such_a_skill(self):
        kept = [
            "import_service.game_master_kept_skill 3 broadsword",
            "import_service.game_master_kept_skill 1 maker skill (smith)",
        ]
        skills = {"broadsword": 3, "rope": 2, "maker skill (smith)": 1}
        payload = {
            "_format": "tarmar-character",
            "data": {"name": "Kept By A Game Master", "skills": skills},
        }
        with seated_skill_rules(WEAPON_AND_MAKER_SKILL_BY_INVITATION):
            self.assertEqual(game_master_kept_skills(skills), kept)
            held = held_to_player(validate_import_payload(payload), is_game_master=True)
        self.assertEqual(held.fields["skills"], skills)
        self.assertEqual(held.game_master_kept, kept)

    def test_a_players_extraction_drops_arcane_and_the_notice_names_it(self):
        imported = validate_import_payload(extraction_to_import_payload(self.EXTRACTED))
        self.assertEqual(imported.fields["skills"], {"arcane": 6, "perception": 3})
        held = held_to_player(imported, is_game_master=False)
        self.assertEqual(held.fields["skills"], {"survival": 1})
        self.assertIn(
            "the skills (arcane 6, perception 3)", held.fields["import_notice"]
        )
        self.assertEqual(held.game_master_kept, [])

    def test_a_game_masters_extraction_keeps_arcane_and_names_it_as_his(self):
        imported = validate_import_payload(extraction_to_import_payload(self.EXTRACTED))
        held = held_to_player(imported, is_game_master=True)
        self.assertEqual(held.fields["skills"], {"arcane": 6, "perception": 3})
        self.assertEqual(
            held.game_master_kept, ["import_service.game_master_kept_skill 6 arcane"]
        )
