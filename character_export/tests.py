"""Tests for the character_export app."""

import json
import unittest

from django.test import TestCase

from accounts.models import User
from characters.models import Character
from common.test_helpers import BaseTestCase

from .export_utils import (
    generate_character_html,
    generate_character_json,
    generate_character_markdown,
)


def weasyprint_available():
    """Check if WeasyPrint and its system dependencies are available."""
    try:
        from weasyprint import HTML  # noqa: F401

        return True
    except (ImportError, OSError):
        return False


WEASYPRINT_AVAILABLE = weasyprint_available()


class GenerateCharacterMarkdownTest(TestCase):
    """Unit tests for generate_character_markdown function."""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="testpass")
        self.character = Character.objects.create(
            user=self.user,
            name="Test Hero",
            strength=16,
            dexterity=14,
            intelligence=12,
            wisdom=10,
            constitution=15,
            charisma=8,
            appearance_roll="3",
            height_roll="4",
            weight_roll="3",
            fatigue_roll=7,
            provenance_class="nobility",
            provenance_detail="Baron",
            location="City",
            wealth_level="Rich",
            starting_coin=500,
            age=25,
            total_xp=1000,
            experience_terms=2,
            skills={"sword": 3, "navigation": 1, "diplomacy": 2},
            free_skill_points=5,
            # Exactly what build_catalog_equipment_item stores: a catalog
            # item carries no name, weight or category of its own, they are
            # looked up. This fixture used to duplicate all three alongside
            # the item_id, which is why the export's equipment table could
            # print "Unknown | 0 lbs | -" for real catalog items and every
            # test here stayed green (issue #633).
            equipment=[
                {
                    "item_id": "rapier",
                    "quantity": 1,
                    "in_pack": False,
                    "notes": "",
                },
                {
                    "item_id": "rope_hemp_50ft",
                    "quantity": 1,
                    "in_pack": True,
                    "notes": "",
                },
            ],
            experience_log=[
                {
                    "track": "campaigner",
                    "xp_awarded": 4800,
                    "skills_gained": {"weapon": 2},
                },
                {
                    "track": "merchant",
                    "xp_awarded": 3000,
                    "skills_gained": {"barter": 1},
                },
            ],
            dice_log="STR: 4d6kh3 = [6,5,4,1] = 15\nDEX: 4d6kh3 = [5,5,3,2] = 13",
        )

    def test_includes_character_name_as_header(self):
        """Verify markdown starts with character name as H1."""
        md = generate_character_markdown(self.character)
        self.assertTrue(md.startswith("# Test Hero"))

    def test_includes_player_name(self):
        """Verify markdown includes the player's username."""
        md = generate_character_markdown(self.character)
        self.assertIn("**Player:** testuser", md)

    def test_includes_status(self):
        """Verify markdown includes character status."""
        md = generate_character_markdown(self.character)
        self.assertIn("**Status:**", md)

    def test_includes_attributes_section(self):
        """Verify markdown includes attributes table."""
        md = generate_character_markdown(self.character)
        self.assertIn("## Attributes", md)
        self.assertIn("| Strength | 16 |", md)
        self.assertIn("| Dexterity | 14 |", md)
        self.assertIn("| Intelligence | 12 |", md)
        self.assertIn("| Wisdom | 10 |", md)
        self.assertIn("| Constitution | 15 |", md)
        self.assertIn("| Charisma | 8 |", md)

    def test_includes_derived_statistics(self):
        """Verify markdown includes derived stats."""
        md = generate_character_markdown(self.character)
        self.assertIn("## Derived Statistics", md)
        self.assertIn("**Fatigue:**", md)
        self.assertIn("**Body:**", md)
        self.assertIn("**Age:** 25", md)
        self.assertIn("**Total XP:** 1000", md)

    def test_includes_injury_thresholds(self):
        """Verify markdown includes the injury threshold table (issue #130)."""
        md = generate_character_markdown(self.character)
        # Fatigue 60 (CON 15 + WIS 10 + INT 12 + max(DEX 14, STR 16) + roll 7),
        # Body 40.
        self.assertIn("### Injury Thresholds", md)
        self.assertIn("| As a pool falls | Fatigue | Body |", md)
        self.assertIn("| -1 to all rolls at or below | 30 | 20 |", md)
        self.assertIn("| Unconscious at | 0 | 0 |", md)
        self.assertIn("| Survival save each turn at or below | -30 | -20 |", md)
        self.assertIn("| Saves penalized at or below | -60 | -40 |", md)
        self.assertIn("3d6 <= 15 (CON)", md)

    def test_injury_thresholds_dash_when_there_is_no_minus_one_band(self):
        """Tiny pools have no -1 range, so that row prints dashes."""
        tiny = Character.objects.create(
            user=self.user,
            name="Tiny Pools",
            strength=1,
            dexterity=1,
            intelligence=1,
            wisdom=1,
            constitution=1,
            charisma=1,
            appearance_roll="3",
            height_roll="4",
            weight_roll="3",
            fatigue_roll=2,
        )
        md = generate_character_markdown(tiny)
        self.assertIn("| -1 to all rolls at or below | - | - |", md)

    def test_includes_physical_description(self):
        """Verify markdown includes physical description."""
        md = generate_character_markdown(self.character)
        self.assertIn("## Physical Description", md)
        self.assertIn("**Height:**", md)
        self.assertIn("**Weight:**", md)

    def test_includes_background(self):
        """Verify markdown includes background info."""
        md = generate_character_markdown(self.character)
        self.assertIn("## Background", md)
        self.assertIn("**Provenance Class:** Nobility", md)
        self.assertIn("**Provenance Detail:** Baron", md)
        self.assertIn("**Location:** City", md)
        self.assertIn("**Wealth Level:** Rich", md)
        self.assertIn("**Starting Coin:** 500", md)

    def test_includes_skills(self):
        """Verify markdown includes skills table."""
        md = generate_character_markdown(self.character)
        self.assertIn("## Skills", md)
        self.assertIn("| Diplomacy | 2 |", md)
        self.assertIn("| Navigation | 1 |", md)
        self.assertIn("| Sword | 3 |", md)
        self.assertIn("**Free Skill Points:** 5", md)

    def test_omits_free_spell_points_for_non_magic_character(self):
        """No Magic-track term means free_spell_points is 0; omit the line
        entirely rather than print a meaningless zero (issue #137)."""
        md = generate_character_markdown(self.character)
        self.assertNotIn("Free Spell Points", md)

    def test_includes_free_spell_points_for_magic_track_character(self):
        """A Magic-track character's spell-point pool shows in the export
        (issue #137: previously missing from EXPORTABLE_FIELDS entirely, so
        it never appeared here or round-tripped through import)."""
        self.character.free_spell_points = 4
        self.character.save()
        md = generate_character_markdown(self.character)
        self.assertIn("**Free Spell Points:** 4", md)

    def test_status_line_reflects_is_played(self):
        """The existing Status line is where is_played surfaces in the
        markdown export — Ready by default, Played once edited. No separate
        is_played line is needed (issue #156)."""
        md = generate_character_markdown(self.character)
        self.assertIn("**Status:** Ready", md)
        self.character.is_played = True
        self.character.save()
        md = generate_character_markdown(self.character)
        self.assertIn("**Status:** Played", md)

    def test_includes_equipment(self):
        """Verify markdown includes equipment list."""
        md = generate_character_markdown(self.character)
        self.assertIn("## Equipment & Encumbrance", md)
        self.assertIn("### Equipment List", md)
        self.assertIn("| Rapier |", md)
        self.assertIn("| Rope, hemp (50 ft) |", md)

    def test_a_catalog_item_exports_its_catalog_name_weight_and_category(self):
        """The table resolves the catalog, like the weight total above it (#633).

        A catalog entry stores only its item_id and how it is carried, so
        reading name/weight/category off the entry printed every one of them
        as "Unknown | 0 lbs | -" -- in the same document whose Total
        Equipment Weight was resolved correctly two sections earlier.
        """
        self.character.equipment = [
            {"item_id": "broadsword", "quantity": 2, "in_pack": False, "notes": ""}
        ]
        self.character.save()

        md = generate_character_markdown(self.character)
        item_rows = [
            line
            for line in md.splitlines()
            if line.startswith("|") and "Broadsword" in line
        ]

        self.assertEqual(len(item_rows), 1)
        # Broadsword is 5 lbs in the catalog; two of them are 10.
        self.assertEqual(
            item_rows[0], "| Broadsword | 2 | 10.0 lbs | No | No | weapons |"
        )
        self.assertNotIn("| Unknown |", md)

    def test_a_custom_item_exports_its_own_name_weight_and_category(self):
        """A hand-entered item has no catalog record, so its own values stand."""
        self.character.equipment = [
            {
                "item_id": "custom",
                "name": "Grandfather's Locket",
                "quantity": 1,
                "weight": 0.1,
                "category": "gear",
                "in_pack": True,
            }
        ]
        self.character.save()

        md = generate_character_markdown(self.character)

        self.assertIn("| Grandfather's Locket | 1 | 0.1 lbs | Yes | No | gear |", md)

    def test_includes_encumbrance_thresholds(self):
        """Verify markdown includes encumbrance thresholds."""
        md = generate_character_markdown(self.character)
        self.assertIn("### Encumbrance Thresholds", md)
        self.assertIn("| Unencumbered |", md)
        self.assertIn("| Light |", md)

    def test_includes_combat_modifiers(self):
        """Verify markdown includes combat modifiers."""
        md = generate_character_markdown(self.character)
        self.assertIn("### Combat Modifiers", md)
        self.assertIn("**Armor Stops:**", md)
        self.assertIn("**Weapon To-Hit Mod:**", md)

    def test_includes_experience_log(self):
        """Verify markdown includes experience log.

        The setUp fixture's entries predate #221's per-run ``years`` list,
        so the run heading has no age data to show and falls back to the
        track name alone (issue #235/#236 -- "### Term 1: Campaigner" was
        the old wording; there is no "Term" concept once experience is
        bought a year, or a run of years, at a time)."""
        md = generate_character_markdown(self.character)
        self.assertIn("## Experience Log", md)
        self.assertIn("### Campaigner", md)
        self.assertIn("### Merchant", md)
        self.assertIn("**XP Gained:** 4,800", md)

    def test_experience_log_run_heading_shows_age_range_for_multi_year_run(self):
        """A run heading reads "Track, Age N-M", built from the run's own
        first/last year ages -- the same shape the editor's history panel
        uses (issue #221/#235), not a fixed four-year term (issue #236)."""
        self.character.experience_log = [
            {
                "track": "merchant",
                "xp_awarded": 2250,
                "skills_gained": {"barter": 2, "navigation": 1},
                "years": [
                    {"year": 1, "age": 21, "skill": "Barter", "roll": 14},
                    {"year": 2, "age": 22, "skill": "Navigation", "roll": 8},
                    {"year": 3, "age": 23, "skill": "Barter", "roll": 14},
                ],
            }
        ]
        self.character.save()
        md = generate_character_markdown(self.character)
        self.assertIn("### Merchant, Age 21–23", md)

    def test_experience_log_run_heading_shows_single_age_for_one_year_run(self):
        """A one-year run's heading reads a single age, not a degenerate
        range (issue #236)."""
        self.character.experience_log = [
            {
                "track": "laborer",
                "xp_awarded": 500,
                "skills_gained": {"farming": 1},
                "years": [{"year": 4, "age": 24, "skill": "Farming", "roll": 5}],
            }
        ]
        self.character.save()
        md = generate_character_markdown(self.character)
        self.assertIn("### Laborer, Age 24", md)

    def test_experience_log_shows_per_year_survival_roll_lines(self):
        """Per-year lines carry the same survival-roll string the editor
        shows, built by the shared ``survival_roll`` formatter (issue
        #175), so export and editor never drift (issue #236)."""
        self.character.experience_log = [
            {
                "track": "merchant",
                "xp_awarded": 750,
                "skills_gained": {"barter": 1},
                "years": [
                    {
                        "year": 1,
                        "age": 21,
                        "skill": "Barter",
                        "roll": 14,
                        "survival_check": {
                            "dice": [4, 5, 6],
                            "modifier": 2,
                            "total": 17,
                            "target": 4,
                            "success": True,
                        },
                    }
                ],
            }
        ]
        self.character.save()
        md = generate_character_markdown(self.character)
        self.assertIn(
            "- Year 1: Barter (rolled 14) — survival 3d6 [4, 5, 6] + 2 = 17 "
            "vs 4: survived",
            md,
        )

    def test_experience_log_survival_roll_renders_legacy_2d6_truthfully(self):
        """A check recorded under the pre-#223 2d6 rule renders as 2d6, not
        relabelled 3d6 -- the dice count comes from the stored dice, same as
        the editor (issue #236)."""
        self.character.experience_log = [
            {
                "track": "laborer",
                "xp_awarded": 0,
                "skills_gained": {},
                "years": [
                    {
                        "year": 4,
                        "age": 24,
                        "skill": "Farming",
                        "roll": 5,
                        "survival_check": {
                            "dice": [1, 1],
                            "modifier": 0,
                            "total": 2,
                            "target": 2,
                            "success": False,
                            "auto_death": True,
                        },
                    }
                ],
            }
        ]
        self.character.save()
        md = generate_character_markdown(self.character)
        self.assertIn(
            "survival 2d6 [1, 1] + 0 = 2 vs 2 (snake eyes — always fatal): died",
            md,
        )

    def test_derived_statistics_shows_years_of_experience(self):
        """The old "Experience Terms: N" line is gone; years worked come
        from Character.experience_years, counted off the log's own year
        entries (issue #236, follow-up to #235's year-run rework)."""
        self.character.experience_log = [
            {
                "track": "merchant",
                "xp_awarded": 2250,
                "skills_gained": {},
                "years": [
                    {"year": 1, "age": 21, "skill": "Barter", "roll": 14},
                    {"year": 2, "age": 22, "skill": "Navigation", "roll": 8},
                    {"year": 3, "age": 23, "skill": "Barter", "roll": 14},
                ],
            }
        ]
        self.character.save()
        md = generate_character_markdown(self.character)
        self.assertIn("**Years of Experience:** 3", md)
        self.assertNotIn("Experience Terms", md)

    def test_includes_dice_log(self):
        """Verify markdown includes dice log in code block."""
        md = generate_character_markdown(self.character)
        self.assertIn("## Dice Log", md)
        self.assertIn("```", md)
        self.assertIn("STR: 4d6kh3", md)

    def test_includes_footer(self):
        """Verify markdown includes export footer."""
        md = generate_character_markdown(self.character)
        self.assertIn("---", md)
        self.assertIn("Exported on", md)
        self.assertIn("Tarmar Studio", md)

    def test_no_skills_message(self):
        """Verify message shown when character has no skills."""
        self.character.skills = {}
        self.character.save()
        md = generate_character_markdown(self.character)
        self.assertIn("*No skills allocated*", md)

    def test_deceased_character_shows_death_info(self):
        """Verify deceased character shows death cause."""
        self.character.is_alive = False
        self.character.death_cause = "Slain by dragon"
        self.character.save()
        md = generate_character_markdown(self.character)
        self.assertIn("## Death", md)
        self.assertIn("**Cause of Death:** Slain by dragon", md)

    def test_aging_penalties_shown(self):
        """Verify aging penalties are displayed.

        Driven by the character's age, which is what the penalties are
        reconciled from (issue #680). aging.md's age-68 column is STR -2 and
        DEX -2, so this sheet prints base, penalty and effective for each.
        """
        self.character.age = 68
        self.character.save()
        md = generate_character_markdown(self.character)
        self.assertIn("| Strength | 16 | 2 | 14 |", md)
        self.assertIn("| Dexterity | 14 | 2 | 12 |", md)

    def test_movement_restrictions_shown_when_encumbered(self):
        """Verify movement restrictions are shown when heavily encumbered."""
        # Add heavy equipment to cause encumbrance
        self.character.equipment = [
            {
                "item_id": "plate_armor",
                "name": "Plate Armor",
                "quantity": 1,
                "weight": 60,
                "in_pack": False,
                "equipped": True,
                "category": "armor",
            },
            {
                "item_id": "custom",
                "name": "Heavy Loot",
                "quantity": 1,
                "weight": 50,
                "in_pack": False,
                "category": "gear",
            },
        ]
        self.character.save()
        md = generate_character_markdown(self.character)
        self.assertIn("### Movement Restrictions", md)

    def test_includes_movement_modifier_and_gaits(self):
        """Verify the export shows the movement modifier and gait line (issue #143)."""
        md = generate_character_markdown(self.character)
        self.assertIn("**Movement Modifier:**", md)
        self.assertIn("- **Gaits (hexes):**", md)
        self.assertIn("Sprint", md)
        self.assertIn("Walk", md)

    def test_gait_line_shows_na_for_restricted_gaits(self):
        """A restricted gait (e.g. from heavy encumbrance) prints as n/a."""
        self.character.equipment = [
            {
                "item_id": "plate_armor",
                "name": "Plate Armor",
                "quantity": 1,
                "weight": 60,
                "in_pack": False,
                "equipped": True,
                "category": "armor",
            },
        ]
        self.character.save()
        md = generate_character_markdown(self.character)
        self.assertIn("Run n/a", md)
        self.assertIn("Sprint n/a", md)

    def test_experience_log_handles_string_entries(self):
        """Verify experience log handles simple string entries."""
        self.character.experience_log = [
            "Joined the guild",
            {"track": "merchant", "year": 1, "xp_gained": 100, "skills_gained": {}},
        ]
        self.character.save()
        md = generate_character_markdown(self.character)
        self.assertIn("- Joined the guild", md)

    def test_experience_log_entry_with_non_string_track_does_not_crash(self):
        """Cousin bug from issue #122: a non-string track used to crash export
        with AttributeError (no .replace()). Import now rejects this on the
        way in, but a legacy row (created before the fix, or written by some
        other path) must still export without crashing. No "years" list means
        no age data for the heading, so it falls back to the coerced track
        name alone (issue #236: "### Term 1: 42" was the old wording)."""
        self.character.experience_log = [{"track": 42, "xp_awarded": 100}]
        self.character.save()
        md = generate_character_markdown(self.character)
        self.assertIn("### 42", md)

    def test_experience_log_entry_with_non_numeric_xp_awarded_does_not_crash(self):
        """A non-numeric xp_awarded used to crash the ``{xp_gained:,}`` format
        spec with ValueError. Must degrade to a readable value instead."""
        self.character.experience_log = [{"track": "laborer", "xp_awarded": "a lot"}]
        self.character.save()
        md = generate_character_markdown(self.character)
        self.assertIn("**XP Gained:** 0", md)

    def test_experience_log_entry_with_invalid_skills_gained_does_not_crash(self):
        """A non-dict skills_gained used to crash on ``.items()``; a non-string
        skill name or non-numeric points used to crash on ``.replace()`` or the
        ``+{points}`` sign-formatted spec. All must degrade, not crash."""
        self.character.experience_log = [
            {
                "track": "laborer",
                "xp_awarded": 100,
                "skills_gained": {7: "many"},
            }
        ]
        self.character.save()
        md = generate_character_markdown(self.character)
        self.assertIn("### Laborer", md)
        self.assertIn("+0", md)

    def test_experience_log_entry_with_non_dict_skills_gained_does_not_crash(self):
        self.character.experience_log = [
            {"track": "laborer", "xp_awarded": 100, "skills_gained": ["sword"]}
        ]
        self.character.save()
        md = generate_character_markdown(self.character)
        self.assertIn("### Laborer", md)


class GenerateCharacterJsonTest(TestCase):
    """Unit tests for generate_character_json payload contents."""

    def setUp(self):
        self.user = User.objects.create_user(username="jsonuser", password="testpass")
        self.character = Character.objects.create(
            user=self.user,
            name="Json Hero",
            strength=12,
            dexterity=11,
            intelligence=10,
            wisdom=9,
            constitution=13,
            charisma=8,
            appearance_roll="3",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=7,
            is_played=True,
        )

    def test_payload_includes_is_played(self):
        """is_played must round-trip so an imported copy keeps its Played
        badge (issue #156: it was missing from EXPORTABLE_FIELDS and every
        import silently reset it to the default False)."""
        data = json.loads(generate_character_json(self.character))["data"]
        self.assertIn("is_played", data)
        self.assertIs(data["is_played"], True)


class GenerateCharacterHtmlTest(TestCase):
    """Unit tests for generate_character_html function."""

    def setUp(self):
        self.user = User.objects.create_user(username="htmluser", password="testpass")
        self.character = Character.objects.create(
            user=self.user,
            name="HTML Test Character",
            strength=10,
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
            appearance_roll="3",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=7,
        )

    def test_returns_valid_html_document(self):
        """Verify output is a complete HTML document."""
        html = generate_character_html(self.character)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("<html>", html)
        self.assertIn("</html>", html)
        self.assertIn("<head>", html)
        self.assertIn("<body>", html)

    def test_includes_character_name_in_title(self):
        """Verify HTML title contains character name."""
        html = generate_character_html(self.character)
        self.assertIn("<title>HTML Test Character - Character Sheet</title>", html)

    def test_includes_css_styles(self):
        """Verify HTML includes styling."""
        html = generate_character_html(self.character)
        self.assertIn("<style>", html)
        self.assertIn("font-family", html)

    def test_converts_markdown_tables_to_html(self):
        """Verify markdown tables are converted to HTML tables."""
        html = generate_character_html(self.character)
        self.assertIn("<table>", html)
        self.assertIn("<th>", html)
        self.assertIn("<td>", html)

    def test_includes_print_styles(self):
        """Verify HTML includes print media query."""
        html = generate_character_html(self.character)
        self.assertIn("@media print", html)

    def test_sanitizes_malicious_skill_and_name(self):
        """HTML-injecting skill names and character names are stripped (no XSS/SSRF)."""
        self.character.name = "Bob <script>alert(1)</script>"
        self.character.skills = {"<img src=x onerror=alert(1)>": 3}
        self.character.save()
        html = generate_character_html(self.character)
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertNotIn("onerror", html)


class ExportMarkdownViewTest(BaseTestCase):
    """E2E tests for the markdown export view."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="exportuser")
        self.other_user = self.create_user(username="otheruser")
        self.dm_user = self.create_user(username="dmuser")
        self.dm_user.is_dm = True
        self.dm_user.save()
        self.admin_user = self.create_user(username="adminuser")
        self.admin_user.is_role_admin = True
        self.admin_user.save()
        self.character = Character.objects.create(
            user=self.user,
            name="Exportable Hero",
            strength=12,
            dexterity=12,
            intelligence=12,
            wisdom=12,
            constitution=12,
            charisma=12,
            appearance_roll="3",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=7,
        )

    def test_requires_authentication(self):
        """Verify export requires login."""
        response = self.client.get(f"/tarmar/export/{self.character.id}/markdown/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/tarmar/accounts/login/", response.url)

    def test_returns_markdown_file(self):
        """Verify authenticated user can download markdown."""
        self.login_as(self.user)
        response = self.client.get(f"/tarmar/export/{self.character.id}/markdown/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/markdown")

    def test_filename_contains_character_name(self):
        """Verify downloaded file is named after character."""
        self.login_as(self.user)
        response = self.client.get(f"/tarmar/export/{self.character.id}/markdown/")
        self.assertIn("Exportable Hero.md", response["Content-Disposition"])

    def test_content_disposition_is_attachment(self):
        """Verify response triggers file download."""
        self.login_as(self.user)
        response = self.client.get(f"/tarmar/export/{self.character.id}/markdown/")
        self.assertIn("attachment", response["Content-Disposition"])

    def test_cannot_export_other_users_character(self):
        """Verify users cannot export characters they don't own."""
        self.login_as(self.other_user)
        response = self.client.get(f"/tarmar/export/{self.character.id}/markdown/")
        self.assertEqual(response.status_code, 404)

    def test_cannot_export_deleted_character(self):
        """Verify deleted characters cannot be exported."""
        self.character.is_deleted = True
        self.character.save()
        self.login_as(self.user)
        response = self.client.get(f"/tarmar/export/{self.character.id}/markdown/")
        self.assertEqual(response.status_code, 404)

    def test_nonexistent_character_returns_404(self):
        """Verify 404 for nonexistent character ID."""
        self.login_as(self.user)
        response = self.client.get("/tarmar/export/99999/markdown/")
        self.assertEqual(response.status_code, 404)

    def test_response_content_is_valid_markdown(self):
        """Verify response body contains valid markdown."""
        self.login_as(self.user)
        response = self.client.get(f"/tarmar/export/{self.character.id}/markdown/")
        content = response.content.decode("utf-8")
        self.assertIn("# Exportable Hero", content)
        self.assertIn("## Attributes", content)

    def test_special_characters_in_name_sanitized(self):
        """Verify special characters in filename are handled."""
        self.character.name = "Test/Hero:Special"
        self.character.save()
        self.login_as(self.user)
        response = self.client.get(f"/tarmar/export/{self.character.id}/markdown/")
        # Should not contain problematic characters in filename
        self.assertNotIn("/", response["Content-Disposition"].split("filename=")[1])
        self.assertNotIn(":", response["Content-Disposition"].split("filename=")[1])

    def test_dm_can_export_any_character(self):
        """Verify DMs can export characters they don't own."""
        self.login_as(self.dm_user)
        response = self.client.get(f"/tarmar/export/{self.character.id}/markdown/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/markdown")

    def test_admin_can_export_any_character(self):
        """Verify admins can export characters they don't own."""
        self.login_as(self.admin_user)
        response = self.client.get(f"/tarmar/export/{self.character.id}/markdown/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/markdown")


class ExportPdfViewTest(BaseTestCase):
    """E2E tests for the PDF export view."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="pdfuser")
        self.other_user = self.create_user(username="otherpdfuser")
        self.dm_user = self.create_user(username="pdfmuser")
        self.dm_user.is_dm = True
        self.dm_user.save()
        self.admin_user = self.create_user(username="pdfadminuser")
        self.admin_user.is_role_admin = True
        self.admin_user.save()
        self.character = Character.objects.create(
            user=self.user,
            name="PDF Hero",
            strength=14,
            dexterity=14,
            intelligence=14,
            wisdom=14,
            constitution=14,
            charisma=14,
            appearance_roll="3",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=7,
            skills={"archery": 2},
            equipment=[
                {
                    "item_id": "bow",
                    "name": "Longbow",
                    "quantity": 1,
                    "weight": 3,
                    "in_pack": False,
                    "equipped": True,
                    "category": "weapons",
                }
            ],
        )

    def test_requires_authentication(self):
        """Verify PDF export requires login."""
        response = self.client.get(f"/tarmar/export/{self.character.id}/pdf/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/tarmar/accounts/login/", response.url)

    @unittest.skipUnless(WEASYPRINT_AVAILABLE, "WeasyPrint not available")
    def test_returns_pdf_content_type(self):
        """Verify authenticated user receives PDF content type."""
        self.login_as(self.user)
        response = self.client.get(f"/tarmar/export/{self.character.id}/pdf/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    @unittest.skipUnless(WEASYPRINT_AVAILABLE, "WeasyPrint not available")
    def test_filename_contains_character_name(self):
        """Verify downloaded PDF is named after character."""
        self.login_as(self.user)
        response = self.client.get(f"/tarmar/export/{self.character.id}/pdf/")
        self.assertIn("PDF Hero.pdf", response["Content-Disposition"])

    @unittest.skipUnless(WEASYPRINT_AVAILABLE, "WeasyPrint not available")
    def test_content_disposition_is_attachment(self):
        """Verify PDF response triggers file download."""
        self.login_as(self.user)
        response = self.client.get(f"/tarmar/export/{self.character.id}/pdf/")
        self.assertIn("attachment", response["Content-Disposition"])

    def test_cannot_export_other_users_character(self):
        """Verify users cannot export PDF of characters they don't own."""
        self.login_as(self.other_user)
        response = self.client.get(f"/tarmar/export/{self.character.id}/pdf/")
        self.assertEqual(response.status_code, 404)

    def test_cannot_export_deleted_character(self):
        """Verify deleted characters cannot be exported as PDF."""
        self.character.is_deleted = True
        self.character.save()
        self.login_as(self.user)
        response = self.client.get(f"/tarmar/export/{self.character.id}/pdf/")
        self.assertEqual(response.status_code, 404)

    def test_nonexistent_character_returns_404(self):
        """Verify 404 for nonexistent character ID."""
        self.login_as(self.user)
        response = self.client.get("/tarmar/export/99999/pdf/")
        self.assertEqual(response.status_code, 404)

    @unittest.skipUnless(WEASYPRINT_AVAILABLE, "WeasyPrint not available")
    def test_response_content_is_valid_pdf(self):
        """Verify response body starts with PDF magic bytes."""
        self.login_as(self.user)
        response = self.client.get(f"/tarmar/export/{self.character.id}/pdf/")
        # PDF files start with %PDF
        self.assertTrue(response.content.startswith(b"%PDF"))

    @unittest.skipUnless(WEASYPRINT_AVAILABLE, "WeasyPrint not available")
    def test_pdf_has_reasonable_size(self):
        """Verify PDF has content (not empty)."""
        self.login_as(self.user)
        response = self.client.get(f"/tarmar/export/{self.character.id}/pdf/")
        # PDF should have reasonable size (at least 5KB for a character sheet)
        self.assertGreater(len(response.content), 5000)

    def test_dm_can_export_any_character_pdf(self):
        """Verify DMs can export PDF of characters they don't own."""
        self.login_as(self.dm_user)
        response = self.client.get(f"/tarmar/export/{self.character.id}/pdf/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_admin_can_export_any_character_pdf(self):
        """Verify admins can export PDF of characters they don't own."""
        self.login_as(self.admin_user)
        response = self.client.get(f"/tarmar/export/{self.character.id}/pdf/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")


class ExportUrlPatternsTest(BaseTestCase):
    """Tests for URL pattern configuration."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="urluser")
        self.character = Character.objects.create(
            user=self.user,
            name="URL Test Character",
            strength=10,
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
            appearance_roll="3",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=7,
        )

    def test_markdown_url_uses_character_id(self):
        """Verify markdown URL accepts character ID."""
        self.login_as(self.user)
        response = self.client.get(f"/tarmar/export/{self.character.id}/markdown/")
        self.assertEqual(response.status_code, 200)

    @unittest.skipUnless(WEASYPRINT_AVAILABLE, "WeasyPrint not available")
    def test_pdf_url_uses_character_id(self):
        """Verify PDF URL accepts character ID."""
        self.login_as(self.user)
        response = self.client.get(f"/tarmar/export/{self.character.id}/pdf/")
        self.assertEqual(response.status_code, 200)

    def test_invalid_character_id_format_returns_404(self):
        """Verify non-integer character ID returns 404."""
        self.login_as(self.user)
        response = self.client.get("/tarmar/export/abc/markdown/")
        self.assertEqual(response.status_code, 404)

    def test_reverse_url_resolution(self):
        """Verify URL names resolve correctly."""
        from django.urls import reverse

        md_url = reverse("character_export:export_markdown", kwargs={"character_id": 1})
        self.assertEqual(md_url, "/tarmar/export/1/markdown/")

        pdf_url = reverse("character_export:export_pdf", kwargs={"character_id": 1})
        self.assertEqual(pdf_url, "/tarmar/export/1/pdf/")


class SafeUrlFetcherTest(TestCase):
    """The PDF export url-fetcher must block local-file / SSRF resource reads."""

    def test_blocks_non_http_schemes(self):
        from character_export.views import _safe_url_fetcher

        for bad_url in (
            "file:///etc/passwd",
            "file://localhost/etc/hosts",
            "ftp://example.com/x",
            "data:text/plain,hi",
            "gopher://example.com/",
        ):
            with self.assertRaises(ValueError):
                _safe_url_fetcher(bad_url)

    def test_allows_http_schemes(self):
        # http(s) URLs must pass the scheme gate (delegating to WeasyPrint's
        # fetcher). Patch the lazily-imported default fetcher so no network /
        # weasyprint runtime is needed.
        from unittest.mock import patch

        from character_export.views import _safe_url_fetcher

        with patch("weasyprint.default_url_fetcher", return_value={"ok": True}) as m:
            result = _safe_url_fetcher("https://example.com/img.png")
        self.assertEqual(result, {"ok": True})
        m.assert_called_once()
