"""Tests for inline help icons on the character edit page (issue #7)."""

from django.test import TestCase

from characters.help_text import help_text
from characters.models import Character
from characters.templatetags.character_help import help_for
from common.test_helpers import BaseTestCase


class HelpForTagTest(TestCase):
    def test_returns_empty_string_for_unknown_key(self):
        self.assertEqual(help_for("nonexistent-key"), "")

    def test_renders_disclosure_for_known_key(self):
        html = str(help_for("strength"))
        self.assertIn("<details", html)
        self.assertIn('class="help"', html)
        self.assertIn("<summary", html)
        self.assertIn(help_text()["strength"], html)

    def test_aria_label_is_humanized(self):
        html = str(help_for("armor_dex_penalty"))
        self.assertIn('aria-label="Help for armor dex penalty"', html)


class CharacterEditHelpIconsTest(BaseTestCase):
    """Help icons appear next to each game-specific label."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user("help-user")
        self.character = Character.objects.create(
            user=self.user,
            name="Help Test",
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

    def _get_page(self):
        self.login_as(self.user)
        return self.client.get(f"/tarmar/characters/{self.character.pk}/")

    def test_core_attribute_headers_have_help(self):
        body = self._get_page().content.decode()
        # Each attribute label is followed by its help disclosure body text.
        for key in (
            "strength",
            "dexterity",
            "intelligence",
            "wisdom",
            "constitution",
            "charisma",
        ):
            self.assertIn(help_text()[key], body, msg=f"missing help body for {key}")

    def test_derived_attributes_have_help(self):
        body = self._get_page().content.decode()
        self.assertIn(help_text()["fatigue"], body)
        self.assertIn(help_text()["body"], body)

    def test_injury_thresholds_have_help(self):
        body = self._get_page().content.decode()
        self.assertIn(help_text()["injury_thresholds"], body)
        self.assertIn(help_text()["survival_save"], body)

    def test_body_help_matches_the_injury_rules(self):
        """Zero Body is unconscious; dying starts at minus half (issue #130)."""
        body_help = help_text()["body"]
        self.assertIn("unconscious", body_help)
        self.assertNotIn("start dying", body_help)

    def test_provenance_class_help_makes_no_mechanical_claims(self):
        """Provenance stores social class/title only — nothing in the app
        wires it to starting equipment, coin, or roll modifiers (issue #145;
        wealth/coin come from the separate Wealth Level roll instead)."""
        provenance_help = help_text()["provenance_class"]
        self.assertNotIn("Affects starting equipment", provenance_help)
        self.assertNotIn("social-roll modifiers", provenance_help)
        self.assertIn("no starting equipment", provenance_help)

    def test_wealth_level_help_does_not_conflate_provenance(self):
        """Commoner is a Provenance class, not a Wealth level (issue #145)."""
        wealth_help = help_text()["wealth_level"]
        self.assertNotIn("Commoner", wealth_help)
        for tier in ("Subsistence", "Moderate", "Merchant", "Rich"):
            self.assertIn(tier, wealth_help)

    def test_armor_stops_help_matches_unconditional_shield_stacking(self):
        """Shields always count toward stops; there is no "actively blocking"
        state (issue #145, characters/models.py `_sum_armor_field`)."""
        armor_stops_help = help_text()["armor_stops"]
        self.assertNotIn("actively blocking", armor_stops_help)
        self.assertIn("shield", armor_stops_help.lower())

    def test_armor_dex_penalty_help_mentions_shields(self):
        """Shields contribute to the DEX penalty sum too, not just armor
        (issue #145, characters/models.py `_sum_armor_field`)."""
        self.assertIn("shield", help_text()["armor_dex_penalty"].lower())

    def test_weapon_to_hit_help_names_the_weapon_in_hand(self):
        """The tile reads the weapon in hand (issue #846).

        This used to assert the help said "every weapon" counted, which was
        #846 itself: the tile added up every weapon carried, a figure no
        swing gets. It still must not claim an "equipped weapon", a flag the
        sheet does not show under that name.
        """
        weapon_help = help_text()["weapon_to_hit"]
        self.assertNotIn("equipped weapon", weapon_help)
        self.assertNotIn("every weapon", weapon_help)
        self.assertIn("weapon in hand", weapon_help)

    def test_appearance_has_help(self):
        body = self._get_page().content.decode()
        self.assertIn(help_text()["appearance"], body)

    def test_skills_section_has_help(self):
        body = self._get_page().content.decode()
        self.assertIn(help_text()["experience_term"], body)
        self.assertIn(help_text()["free_skill_points"], body)
        self.assertIn(help_text()["total_xp"], body)

    def test_combat_stats_have_help(self):
        body = self._get_page().content.decode()
        self.assertIn(help_text()["armor_stops"], body)
        self.assertIn(help_text()["armor_dex_penalty"], body)
        self.assertIn(help_text()["weapon_to_hit"], body)
        self.assertIn(help_text()["encumbrance"], body)

    def test_help_uses_details_element_for_persistent_disclosure(self):
        body = self._get_page().content.decode()
        # Tooltip-style elements would be `<span title=...>`. The issue calls
        # for a click target with displayed help, hence <details>.
        self.assertIn('<details class="help">', body)


class AppearanceRulingTest(BaseTestCase):
    """A 1 or a 6 for Appearance sends the player to the GM (#685 item 4).

    attributes.md: "**Appearance** determines how attractive a character is.
    Roll 1d6: 1 is pretty, 6 is ugly. If you roll a 1 or 6, ask the GM to
    further qualify."

    The instruction was in help_text.py and nowhere else: nothing asserted it
    was there, nothing tied it back to attributes.md, and nothing checked the
    sheet shows it. test_dice.py covers the die, not the ruling.
    """

    def setUp(self):
        super().setUp()
        self.user = self.create_user("appearance-ruling")
        self.character = Character.objects.create(
            user=self.user,
            name="Pretty",
            strength=10,
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
            appearance_roll="1",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=7,
        )

    def test_an_appearance_roll_of_one_or_six_tells_the_player_to_ask_the_gm(self):
        """The rules page, the help text and the sheet all carry the ruling."""
        from pathlib import Path

        page = Path(
            "reference/content/public-rules/characters/attributes.md"
        ).read_text()
        self.assertIn("If you roll a 1 or 6, ask the GM to further qualify", page)

        self.assertIn("A 1 or 6 stands", help_text()["appearance"])
        self.assertIn("ask the GM to further qualify", help_text()["appearance"])

        self.login_as(self.user)
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        self.assertContains(response, "ask the GM to further qualify")

    def test_the_ruling_is_shown_whatever_the_roll(self):
        """It is field help, not a banner: the sheet states it from the start.

        A player who has not rolled yet needs to know what a 1 or a 6 will
        mean, and one who rolled a 3 needs to know why theirs stands. State,
        not an event (the no-transient-UI rule).
        """
        self.login_as(self.user)
        for roll in ("", "3", "6"):
            with self.subTest(appearance_roll=roll):
                self.character.appearance_roll = roll
                self.character.save()
                response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
                self.assertContains(response, "ask the GM to further qualify")
