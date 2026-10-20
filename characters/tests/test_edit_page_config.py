"""The template -> JavaScript boundary on the character edit page (issue #63).

The page's ~1,100 lines of client logic used to be an inline <script>, with
Django values interpolated straight into JavaScript expressions. It now lives
in static/js/character-edit.js and reads one json_script block. That block is
the entire contract between the two halves: every key here is something the
extracted module dereferences by name on load, so a dropped or renamed key
breaks a feature silently, with an `undefined` URL and no error until a fetch
lands on the wrong endpoint.
"""

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from django.conf import settings

from characters.constants import CHARACTER_TEXT_LIMITS
from characters.models import Character, character_field_options
from characters.skill_rules import skill_rules
from characters.skills import (
    DEFAULT_RUN_YEARS,
    MAX_EXPERIENCE_YEARS_PER_RUN,
    aging_escalation_note,
    next_aging_change_after_age,
    survival_targets,
)
from characters.views.characters import (
    AGING_PREVIEW_HORIZON_STAGE,
    aging_preview_horizon_age,
    character_number_limits,
)
from common.test_helpers import BaseTestCase

CHARACTER_DEFAULTS = {
    "strength": 12,
    "dexterity": 12,
    "intelligence": 12,
    "wisdom": 12,
    "constitution": 12,
    "charisma": 12,
    "appearance_roll": "3",
    "height_roll": "3",
    "weight_roll": "3",
    "fatigue_roll": 7,
}

# Every key static/js/character-edit.js reads off the config object.
EXPECTED_KEYS = {
    "canSetItemModifiers",
    # The rows holding the weapon in hand and the shield in use (#804, #846).
    "weaponInHandIndex",
    "shieldInUseIndex",
    # A Game Master's allocate controls stay at no free points (#770), and
    # an idle shield's row says why (review of #804).
    "canGrantSkillPoints",
    "shieldIdleReason",
    "updateFieldUrl",
    "addYearsUrl",
    "allocateUrl",
    "renameSkillUrl",
    "currentAge",
    "currentYears",
    "defaultRunYears",
    "maxRunYears",
    "maxExperienceLogEntries",
    "survivalDice",
    "survivalTargets",
    "trackLabels",
    "agingByAge",
    "agingHorizonAge",
    "agingEscalationNote",
    "catalogUrl",
    "equipmentAddUrl",
    "equipmentUpdateUrl",
    "equipmentRemoveUrl",
    "equipmentBulkRemoveUrl",
    "equipmentTogglePackUrl",
    "initialEquipment",
    "packDropped",
    "markDeadUrl",
    # Each text field's limit, for the inputs the script builds (#928).
    "textLimits",
    # And each number input's bound (#938).
    "numberLimits",
    "reviveUrl",
    "rollFatigueUrl",
    "rollLiteracyUrl",
    "ruleLiteracyUrl",
    "dismissImportNoticeUrl",
    "setPoolsUrl",
    "canEdit",
    "setVisibilityUrl",
}


class CharacterEditConfigBlockTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user("player")
        self.character = Character.objects.create(
            user=self.user,
            name="Config Testwright",
            skills={"sword": 3},
            equipment=[{"item_id": "dagger", "quantity": 1}],
            free_skill_points=2,
            **CHARACTER_DEFAULTS,
        )
        self.login_as(self.user)
        self.response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        self.html = self.response.content.decode()

    def _config(self):
        match = re.search(
            r'<script id="character-edit-config" type="application/json">(.*?)</script>',
            self.html,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "the page has no #character-edit-config block")
        assert match is not None
        return json.loads(match.group(1))

    def test_config_block_carries_exactly_the_keys_the_module_reads(self):
        self.assertEqual(set(self._config()), EXPECTED_KEYS)

    def test_config_urls_point_at_this_character(self):
        config = self._config()
        pk = self.character.pk
        self.assertEqual(
            config["updateFieldUrl"], f"/tarmar/characters/{pk}/update-field/"
        )
        self.assertEqual(config["addYearsUrl"], f"/tarmar/characters/{pk}/add-years/")
        self.assertEqual(
            config["allocateUrl"], f"/tarmar/characters/{pk}/allocate-skill/"
        )
        self.assertEqual(
            config["renameSkillUrl"], f"/tarmar/characters/{pk}/rename-skill/"
        )
        self.assertEqual(
            config["equipmentAddUrl"], f"/tarmar/characters/{pk}/equipment/add/"
        )
        self.assertEqual(
            config["equipmentUpdateUrl"], f"/tarmar/characters/{pk}/equipment/update/"
        )
        self.assertEqual(
            config["equipmentRemoveUrl"], f"/tarmar/characters/{pk}/equipment/remove/"
        )
        self.assertEqual(
            config["equipmentBulkRemoveUrl"],
            f"/tarmar/characters/{pk}/equipment/bulk-remove/",
        )
        self.assertEqual(
            config["equipmentTogglePackUrl"],
            f"/tarmar/characters/{pk}/equipment/toggle-pack/",
        )
        self.assertEqual(config["markDeadUrl"], f"/tarmar/characters/{pk}/mark-dead/")
        self.assertEqual(config["textLimits"], CHARACTER_TEXT_LIMITS)
        self.assertEqual(config["numberLimits"], character_number_limits())
        self.assertEqual(config["reviveUrl"], f"/tarmar/characters/{pk}/revive/")
        self.assertEqual(
            config["rollLiteracyUrl"], f"/tarmar/characters/{pk}/roll-literacy/"
        )
        self.assertEqual(
            config["ruleLiteracyUrl"], f"/tarmar/characters/{pk}/rule-literacy/"
        )
        self.assertEqual(
            config["dismissImportNoticeUrl"],
            f"/tarmar/characters/{pk}/dismiss-import-notice/",
        )
        self.assertEqual(
            config["setVisibilityUrl"], f"/tarmar/characters/{pk}/set-visibility/"
        )
        self.assertEqual(config["setPoolsUrl"], f"/tarmar/characters/{pk}/set-pools/")
        # The catalog is shared, not per-character.
        self.assertEqual(config["catalogUrl"], "/tarmar/characters/equipment/catalog/")

    def test_config_carries_the_rules_tables_python_owns(self):
        """The aging and survival numbers are the loaded canon's (issue #70)."""
        config = self._config()
        self.assertEqual(config["survivalTargets"], survival_targets())
        self.assertEqual(config["survivalDice"], skill_rules().survival_roll.dice)
        self.assertEqual(config["agingHorizonAge"], aging_preview_horizon_age())
        self.assertEqual(
            config["agingEscalationNote"],
            aging_escalation_note(aging_preview_horizon_age()),
        )
        self.assertEqual(config["defaultRunYears"], DEFAULT_RUN_YEARS)
        self.assertEqual(config["maxRunYears"], MAX_EXPERIENCE_YEARS_PER_RUN)
        # Aging change points keyed by the age they land at, 3-letter attrs.
        # Every age at which the cumulative totals move, through the preview
        # horizon (issue #163) -- the canon's aging escalates by formula past
        # its table, so very old characters get real numbers too.
        expected = {}
        scan_age = 16
        while True:
            change = next_aging_change_after_age(scan_age, AGING_PREVIEW_HORIZON_STAGE)
            if change is None:
                break
            change_age, penalties = change
            expected[str(change_age)] = {
                attr[:3]: value for attr, value in penalties.items()
            }
            scan_age = change_age
        self.assertEqual(config["agingByAge"], expected)

    def test_config_aging_preview_covers_ages_past_the_tabulated_data(self):
        """Past age 68 aging is formula-driven, and still previewed (issue #163)."""
        config = self._config()
        # Age 72 is the first change point past the aging table's last column.
        self.assertIn("72", config["agingByAge"])
        self.assertEqual(
            config["agingByAge"]["72"],
            {"str": -3, "dex": -3, "con": -3, "int": -1, "wis": -1},
        )
        # In the table's order, as every tabulated stage is (issue #70): the
        # page iterates the object as it arrives.
        self.assertEqual(
            list(config["agingByAge"]["72"]), ["str", "dex", "int", "wis", "con"]
        )
        self.assertEqual(list(config["agingByAge"]["40"]), ["str", "dex", "con"])
        # Nothing is precomputed past the horizon.
        self.assertTrue(
            all(int(age) <= aging_preview_horizon_age() for age in config["agingByAge"])
        )

    def test_config_carries_the_characters_current_state(self):
        config = self._config()
        self.assertEqual(config["currentAge"], self.character.age)
        self.assertEqual(config["currentYears"], self.character.experience_years)
        self.assertEqual(config["initialEquipment"], self.character.equipment)
        self.assertEqual(config["packDropped"], self.character.pack_dropped)
        # The CSRF token left this block in issue #338: base.html carries it
        # once, in a meta tag, for every page.
        self.assertIn('<meta name="csrf-token" content="', self.html)
        # The owner opened this page, so it renders editable.
        self.assertIs(config["canEdit"], True)

    def test_config_block_precedes_the_script_that_reads_it(self):
        """character-edit.js parses the block at load, so order is load-bearing."""
        self.assertLess(
            self.html.index('id="character-edit-config"'),
            self.html.index("js/character-edit.js"),
            "the config block must render before character-edit.js",
        )

    def test_api_js_and_autosave_js_load_before_character_edit_js(self):
        """character-edit.js calls postWithCsrf and initAutosave as bare globals.

        api.js publishes the first from <head>; autosave.js the second from
        this template's extra_js block. Reverse either order and the page
        throws ReferenceError before a single handler binds.
        """
        self.assertLess(
            self.html.index("js/api.js"), self.html.index("js/character-edit.js")
        )
        self.assertLess(
            self.html.index("js/autosave.js"), self.html.index("js/character-edit.js")
        )

    def test_a_hostile_equipment_note_cannot_escape_the_config_block(self):
        """json_script escapes < > &, so stored markup stays data.

        This is the property that replaced hand-quoting values into a
        JavaScript string literal; a note like `</script><script>...` used to
        be the kind of thing that could close the block early.
        """
        self.character.equipment = [
            {"item_id": "custom", "name": "</script><script>alert(1)</script>"}
        ]
        self.character.save()
        html = self.client.get(
            f"/tarmar/characters/{self.character.pk}/"
        ).content.decode()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("\\u003C", html)


# The Attack Resolution panel's own block (issue #101). Deliberately separate
# from the one above: attack-panel.js is a separate module, and folding its two
# keys into the character-edit block would weaken that block's set-equality
# assertion into "the keys some module on this page reads".
ATTACK_PANEL_EXPECTED_KEYS = {"attackUrl", "maxCombatLogEntries"}


class AttackPanelConfigBlockTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user("attacker")
        self.character = Character.objects.create(
            user=self.user,
            name="Panel Testwright",
            skills={"sword": 3},
            equipment=[
                {"item_id": "saber", "quantity": 1},
                {"item_id": "backpack", "quantity": 1},
            ],
            **CHARACTER_DEFAULTS,
        )
        self.login_as(self.user)
        self.response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        self.html = self.response.content.decode()

    def _config(self):
        match = re.search(
            r'<script id="attack-panel-config" type="application/json">(.*?)</script>',
            self.html,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "the page has no #attack-panel-config block")
        assert match is not None
        return json.loads(match.group(1))

    def test_config_block_carries_exactly_the_keys_the_module_reads(self):
        self.assertEqual(set(self._config()), ATTACK_PANEL_EXPECTED_KEYS)

    def test_the_attack_url_points_at_this_character(self):
        self.assertEqual(
            self._config()["attackUrl"],
            f"/tarmar/characters/{self.character.pk}/attack/",
        )
        # The token comes from base.html's meta tag now, not this block.
        self.assertIn('<meta name="csrf-token" content="', self.html)

    def test_config_block_precedes_the_script_that_reads_it(self):
        self.assertLess(
            self.html.index('id="attack-panel-config"'),
            self.html.index("js/attack-panel.js"),
        )

    def test_api_js_loads_before_attack_panel_js(self):
        """attack-panel.js calls postWithCsrf and uiNotify as bare globals."""
        self.assertLess(
            self.html.index("js/api.js"), self.html.index("js/attack-panel.js")
        )

    def test_a_weapon_whose_damage_does_not_parse_is_not_offered(self):
        """Club, cestus, lasso, torch, bola: real class, no rollable damage.

        Offering one and then refusing it would be a trap, so the select omits
        it and a footnote names it — the player is told why rather than left
        hunting for a weapon they can see in their pack.
        """
        self.character.equipment = [
            {"item_id": "saber", "quantity": 1},
            {"item_id": "club", "quantity": 1},
            {"item_id": "bola", "quantity": 1},
        ]
        self.character.save()
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        self.assertEqual(
            [weapon["item_id"] for weapon in response.context["attack_weapons"]],
            ["saber"],
        )
        self.assertEqual(
            response.context["attack_gm_adjudicated_weapons"], ["Club", "Bola"]
        )
        self.assertContains(response, "damage varies, GM adjudicates")

    def test_no_footnote_when_every_weapon_is_resolvable(self):
        """The footnote is rendered hidden, so an equipment change can show it
        without a reload (issue #339, item 24)."""
        self.assertEqual(self.response.context["attack_gm_adjudicated_weapons"], [])
        self.assertContains(
            self.response,
            '<p class="attack-footnote" id="attack-gm-adjudicated" hidden>',
        )

    def test_the_footnote_shows_even_when_nothing_is_resolvable(self):
        """A character carrying only a club gets the explanation, not silence."""
        self.character.equipment = [{"item_id": "club", "quantity": 1}]
        self.character.save()
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        self.assertEqual(response.context["attack_weapons"], [])
        self.assertContains(response, "attack-no-weapons")
        self.assertContains(response, "attack-gm-adjudicated")

    def test_only_catalog_weapons_with_a_class_are_offered(self):
        """The select's options are server-rendered, so its values are trusted.

        A custom item and a non-weapon are both in this character's equipment;
        neither may appear, because the endpoint would refuse them.
        """
        self.character.equipment = [
            {"item_id": "saber", "quantity": 1},
            {"item_id": "backpack", "quantity": 1},
            {"item_id": "arrows_20", "quantity": 20},
            {"item_id": "improvised club", "quantity": 1, "category": "weapons"},
        ]
        self.character.save()
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        offered = response.context["attack_weapons"]
        self.assertEqual([weapon["item_id"] for weapon in offered], ["saber"])
        self.assertEqual(offered[0]["index"], 0)

    def test_the_option_value_is_the_equipment_index_not_a_position_in_the_list(self):
        """A weapon after a non-weapon still carries its own equipment index."""
        self.character.equipment = [
            {"item_id": "backpack", "quantity": 1},
            {"item_id": "saber", "quantity": 1},
        ]
        self.character.save()
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        self.assertEqual(response.context["attack_weapons"][0]["index"], 1)

    def test_the_panel_says_so_when_there_is_nothing_to_swing(self):
        """The form is still rendered, hidden, for a weapon added later to
        appear in without a reload (issue #339, item 24)."""
        self.character.equipment = [{"item_id": "backpack", "quantity": 1}]
        self.character.save()
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        self.assertEqual(response.context["attack_weapons"], [])
        self.assertContains(response, '<p class="no-weapons" id="attack-no-weapons">')
        self.assertContains(
            response, '<div class="attack-form" id="attack-form" hidden>'
        )
        self.assertNotContains(response, "reload the page")

    def test_with_a_weapon_the_note_is_hidden_and_the_form_shown(self):
        self.assertContains(self.response, '<div class="attack-form" id="attack-form">')
        self.assertContains(self.response, 'id="attack-no-weapons" hidden>')

    def test_the_combat_tiles_show_the_two_previously_export_only_numbers(self):
        """dex_modifier / dodge_modifier were computed and exported, never shown."""
        self.assertIn('id="dex-combat-mod"', self.html)
        self.assertIn('id="dodge-modifier"', self.html)


class SkillsListEmptyStateTest(BaseTestCase):
    """The page and character-edit.js say the same thing about no skills.

    They said different things (issue #338 item 3): the server-rendered page
    read "No skills yet. Roll experience years to gain skills." and the list
    character-edit.js rebuilt after a term read "No skills yet. Add an
    experience term to gain skills." Which sentence a reader met depended on
    whether they had reloaded since. The list is now rendered only by
    characters/_skills_list.html, for the page and for the add-years
    response, so the sentence lives there alone.
    """

    def test_the_sentence_lives_in_the_partial_and_nowhere_in_the_module(self):
        partial = (
            Path(settings.BASE_DIR)
            / "characters"
            / "templates"
            / "characters"
            / "_skills_list.html"
        ).read_text()
        module = (
            Path(settings.BASE_DIR) / "static" / "js" / "character-edit.js"
        ).read_text()

        match = re.search(r'<p class="no-skills">([^<]+)</p>', partial)
        assert match is not None, "_skills_list.html has no .no-skills paragraph"
        self.assertNotIn(match.group(1).strip(), module)
        self.assertNotIn("no-skills", module.replace(".no-skills", ""))


class EquipmentListEmptyStateTest(BaseTestCase):
    """The page and character-edit.js say the same thing about no equipment.

    The sentence was typed into both files (issue #338 item 20); the module
    now keeps it in NO_EQUIPMENT_TEXT, and this holds the two together the way
    SkillsListEmptyStateTest does for skills.
    """

    def test_the_template_and_the_module_agree_word_for_word(self):
        template = (
            Path(settings.BASE_DIR)
            / "characters"
            / "templates"
            / "characters"
            / "character_edit.html"
        ).read_text()
        module = (
            Path(settings.BASE_DIR) / "static" / "js" / "character-edit.js"
        ).read_text()

        match = re.search(r'<p class="no-equipment">([^<]+)</p>', template)
        assert match is not None, "character_edit.html has no .no-equipment paragraph"
        constant = re.search(r"const NO_EQUIPMENT_TEXT = '([^']+)';", module)
        assert constant is not None, "character-edit.js has no NO_EQUIPMENT_TEXT"

        self.assertEqual(match.group(1).strip(), constant.group(1))
        self.assertNotIn(constant.group(1), module.replace(constant.group(0), ""))


class EditableChoiceCellsFollowTheModelTest(BaseTestCase):
    """The sheet's selects list the model's choices, not a copy of them.

    character_edit.html used to type the options out (issue #338 item 5), so
    a choice added to Character reached the page only when somebody
    remembered this template. These tests read the model and check the page
    against it, so they move when the model does.
    """

    CHOICE_FIELDS = ("provenance_class", "location", "wealth_level")

    def setUp(self):
        super().setUp()
        self.user = self.create_user("chooser")
        self.character = Character.objects.create(
            user=self.user, name="Choice Testwright", **CHARACTER_DEFAULTS
        )
        # A Game Master sees every select; a player's provenance and wealth
        # are read-only (issue #749).
        self.login_as(self.create_dm("chooser-gm"))
        self.html = self.client.get(
            f"/tarmar/characters/{self.character.pk}/"
        ).content.decode()

    def _options(self, field):
        block = re.search(
            rf'data-field="{field}">\s*<select[^>]*>(.*?)</select>',
            self.html,
            re.DOTALL,
        )
        self.assertIsNotNone(block, f"no select rendered for {field}")
        assert block is not None
        return re.findall(
            r'<option value="([^"]*)"[^>]*>([^<]*)</option>', block.group(1)
        )

    def test_every_choice_field_offers_exactly_the_canons_rows(self):
        """The select lists the loaded canon's rows, the one statement of a
        field's options (issue #66)."""
        for field in self.CHOICE_FIELDS:
            with self.subTest(field=field):
                options = character_field_options(field)
                self.assertIsNotNone(options, f"{field} has no options")
                assert options is not None
                self.assertEqual(self._options(field), list(options.items()))

    def test_the_characters_own_value_is_the_selected_option(self):
        for field in self.CHOICE_FIELDS:
            with self.subTest(field=field):
                value = getattr(self.character, field)
                self.assertIn(
                    f'<option value="{value}" selected>',
                    self.html,
                    f"{field} does not preselect {value!r}",
                )

    def test_a_reader_who_cannot_edit_sees_the_label_and_no_select(self):
        other = self.create_user("onlooker")
        self.logout()
        self.login_as(other)
        self.character.is_public = True
        self.character.save()
        html = self.client.get(
            f"/tarmar/characters/{self.character.pk}/"
        ).content.decode()

        self.assertNotIn('data-field="location"', html)
        self.assertIn(self.character.location, html)


class _NumberInputs(HTMLParser):
    """Each number input's ``min`` and ``max``, keyed by its id or name."""

    def __init__(self) -> None:
        super().__init__()
        self.bounds: dict[str, tuple[str | None, str | None]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "input" and attributes.get("type") == "number":
            key = attributes.get("id") or attributes.get("name") or ""
            self.bounds[key] = (attributes.get("min"), attributes.get("max"))


class NumberInputBoundsTest(BaseTestCase):
    """The sheet's number inputs stop at the server's bounds (issue #938).

    The limits are patched to numbers no template would carry by chance, so
    an input that shows them read them from ``number_limits`` rather than
    from a number typed into the template.
    """

    ODD = {
        "equipment_quantity": 997,
        "equipment_weight": 1003,
        "equipment_modifier": 11,
        "armor_stops": 12,
        "armor_dex_penalty": 13,
        "check_difficulty": 41,
        "check_total_minimum": 3,
        "check_total_maximum": 14,
        "mana_grant": 101,
        "defender_dodge_minimum": 2,
        "defender_dodge_maximum": 15,
        "situational": 21,
    }

    def test_every_bounded_input_reads_the_map(self):
        game_master = self.create_dm(username="bounds-gm")
        character = Character.objects.create(
            user=game_master,
            name="Bounds Testwright",
            skills={"sword": 3},
            equipment=[{"item_id": "dagger", "quantity": 1}],
            **CHARACTER_DEFAULTS,
        )
        self.login_as(game_master)
        with mock.patch(
            "characters.views.characters.character_number_limits",
            return_value=self.ODD,
        ):
            page = self.client.get(f"/tarmar/characters/{character.pk}/")
        parser = _NumberInputs()
        parser.feed(page.content.decode())
        odd = self.ODD
        expected = {
            "skill-check-difficulty": ("0", odd["check_difficulty"]),
            "total": (odd["check_total_minimum"], odd["check_total_maximum"]),
            "amount": (-odd["mana_grant"], odd["mana_grant"]),
            "attack-stops": ("0", odd["armor_stops"]),
            "attack-dodge": (
                odd["defender_dodge_minimum"],
                odd["defender_dodge_maximum"],
            ),
            "attack-situational": (-odd["situational"], odd["situational"]),
            "custom-weight": ("0", odd["equipment_weight"]),
            "custom-stops": ("0", odd["armor_stops"]),
            "custom-dex-penalty": (-odd["armor_dex_penalty"], "0"),
        }
        for key, (minimum, maximum) in expected.items():
            with self.subTest(input=key):
                self.assertEqual(parser.bounds.get(key), (str(minimum), str(maximum)))
