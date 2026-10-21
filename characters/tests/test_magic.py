"""Issues #774 and #755: the spells a character knows, the Mana Pool, and
seating a player's own caster into a battle with both.
"""

import json
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.urls import reverse

from battle.services import claim_seat, create_battle
from character_export.export_utils import (
    generate_character_json,
    generate_character_markdown,
)
from characters.import_service import held_to_player, validate_import_payload
from characters.magic import (
    battle_loadout,
    current_mana,
    grant_mana,
    learn_spell,
)
from characters.models import Character, validate_spells
from characters.rules import character_rules
from characters.services import CharacterChangeError
from common.test_helpers import CHARACTER_DEFAULTS, BaseTestCase, create_character

MAGIC_PAGE = Path(settings.BASE_DIR) / "reference/content/public-rules/magic.md"


class LearnSpellTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user("caster")
        # An Elemental caster: a player learns a new spell only from their
        # school (issue #18; characters/tests/test_magic_school.py).
        self.character = create_character(
            self.owner,
            name="Caster",
            free_skill_points=3,
            free_spell_points=4,
            magic_school_roll=12,
            magic_school="Elemental",
        )

    def test_a_skill_point_learns_a_spell_at_mastery_one(self):
        note = learn_spell(self.character, "fire_missile", is_game_master=False)
        self.assertEqual(self.character.spells, {"fire_missile": 1})
        self.assertEqual(self.character.free_skill_points, 2)
        self.assertIn("Fire Missile", note)
        self.assertEqual(battle_loadout(self.character).spells, ("fire_missile",))

    def test_a_player_without_free_points_is_refused(self):
        self.character.free_skill_points = 0
        with self.assertRaises(CharacterChangeError):
            learn_spell(self.character, "fire_missile", is_game_master=False)

    def test_a_game_master_teaching_spends_none_of_the_points(self):
        """Ruling on the #774 review: a Game Master's teaching is a grant."""
        learn_spell(self.character, "heal", 3, is_game_master=True)
        self.assertEqual(self.character.spells, {"heal": 3})
        self.assertEqual(self.character.free_skill_points, 3)

    def test_an_unknown_spell_is_refused_here_and_on_import(self):
        with self.assertRaises(CharacterChangeError):
            learn_spell(self.character, "wish", is_game_master=True)
        for bad in ({"wish": 1}, {"heal": -1}, {"heal": "1"}, []):
            with self.subTest(bad), self.assertRaises(ValidationError):
                validate_spells(bad)


class ManaTest(BaseTestCase):
    def test_only_a_game_master_grants_mana(self):
        character = create_character(self.create_user("pool"), name="Pool")
        with self.assertRaises(CharacterChangeError):
            grant_mana(character, 5, is_game_master=False)
        grant_mana(character, 5, is_game_master=True)
        self.assertEqual(character.free_spell_points, 5)
        with self.assertRaises(CharacterChangeError):
            grant_mana(character, -6, is_game_master=True)

    def test_the_pool_is_never_lowered_below_the_mana_spent(self):
        """Issue #940: Mana below 0 is refused here as by the pools setting."""
        character = create_character(
            self.create_user("drained"),
            name="Drained",
            free_spell_points=6,
            mana_spent=4,
        )
        with self.assertRaises(CharacterChangeError) as refused:
            grant_mana(character, -3, is_game_master=True)
        self.assertEqual(
            str(refused.exception),
            "The Mana Pool holds 6 with 4 spent; it cannot go to 3, below the "
            "mana spent",
        )
        self.assertEqual(character.free_spell_points, 6)
        grant_mana(character, -2, is_game_master=True)
        self.assertEqual(character.free_spell_points, 4)

    def test_current_mana_is_the_pool_less_what_fights_spent(self):
        """The pools batch keeps ``mana_spent``; until it lands, none is spent."""
        character = create_character(
            self.create_user("spent"), name="Spent", free_spell_points=5
        )
        self.assertEqual(current_mana(character), 5)
        character.mana_spent = 3  # the pools batch's field, set in memory here
        self.assertEqual(current_mana(character), 2)
        character.mana_spent = 9
        self.assertEqual(current_mana(character), 0)
        # The seat is handed the maximum; snapshot_character takes the spent
        # mana off (pools batch), so the loadout must not do it too.
        self.assertEqual(battle_loadout(character).mana, 5)

    def test_the_magic_page_states_the_amendment(self):
        """Ruling on #774: skill points do not convert to mana unless a page
        says so; the page now says they do not, and that a GM may grant it."""
        text = " ".join(MAGIC_PAGE.read_text().split())
        self.assertNotIn("invests skill points to raise it", text)
        self.assertIn("ordinary skill points do not convert into mana", text)
        self.assertIn("a Game Master may grant mana", text)

    def test_the_endpoints_hold_players_to_the_rules(self):
        owner = self.create_user("learner")
        character = create_character(
            owner,
            name="Learner",
            free_skill_points=1,
            magic_school_roll=12,
            magic_school="Elemental",
        )
        learn_url = reverse("characters:learn_spell", args=[character.pk])
        mana_url = reverse("characters:grant_mana", args=[character.pk])
        self.login_as(owner)
        self.assertEqual(self.post_json(mana_url, {"amount": 3}).status_code, 400)
        self.assertEqual(
            self.post_json(learn_url, {"spell": "fire_missile"}).status_code, 200
        )
        self.assertEqual(
            self.post_json(learn_url, {"spell": "fire_missile"}).status_code, 400
        )
        self.logout()
        self.login_as(self.create_dm())
        self.assertEqual(self.post_json(mana_url, {"amount": 3}).json()["mana"], 3)
        character.refresh_from_db()
        fields = [entry["field"] for entry in character.history]
        self.assertEqual(fields.count("spells"), 1)
        self.assertIn("free_spell_points", fields)


class SeatedCasterTest(BaseTestCase):
    """#755: a saved caster is seated with its spells, so it can cast."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.caster = create_character(
            self.dm,
            name="Own Caster",
            free_spell_points=6,
            spells={"fire_missile": 1, "heal": 3, "shield": 0},
        )

    def seated(self, battle):
        for combatant in battle.state_json["combatants"]:
            if combatant["name"] == "Own Caster":
                return combatant
        raise AssertionError("the caster was not seated")

    def test_a_picked_character_carries_its_spells_and_mana(self):
        battle = create_battle(
            self.dm, name="Casting", count=1, character_ids=(self.caster.pk,), seed=3
        )
        combatant = self.seated(battle)
        self.assertEqual(sorted(combatant["spells"]), ["fire_missile", "heal"])
        self.assertEqual((combatant["mana"], combatant["max_mana"]), (6, 6))

    def test_a_lobby_claim_carries_them_too(self):
        battle = create_battle(self.dm, name="Lobby", count=1, player_seats=1, seed=3)
        seat = battle.combatants.get(is_open_seat=True)
        claim_seat(battle.pk, seat.pk, self.dm, character_id=self.caster.pk)
        seat.refresh_from_db()
        self.assertEqual(sorted(seat.snapshot_json["spells"]), ["fire_missile", "heal"])
        self.assertEqual(seat.snapshot_json["mana"], 6)


class SheetAndExportTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user("reader")
        self.character = create_character(
            self.owner, name="Reader", free_spell_points=2, spells={"heal": 3}
        )

    def test_the_sheet_lists_the_spells_and_the_pool(self):
        self.login_as(self.owner)
        page = self.client.get(reverse("characters:edit", args=[self.character.pk]))
        self.assertContains(page, '<strong id="mana-pool">2</strong>')
        self.assertContains(page, 'data-spell="heal"')
        self.assertNotContains(page, "Raise the Mana Pool")

    def test_the_export_carries_them(self):
        data = json.loads(generate_character_json(self.character))["data"]
        self.assertEqual(data["spells"], {"heal": 3})
        self.assertIn("coin_adjustment", data)
        markdown = generate_character_markdown(self.character)
        self.assertIn("## Spells", markdown)
        self.assertIn("| Heal | Mending |", markdown)


class ImportHoldsPlayersTest(BaseTestCase):
    """A player's import drops the Mana Pool, the spells and the purse's
    movements; a Game Master's keeps them (the #757 review, the #744 rule)."""

    def payload(self):
        source = create_character(
            self.create_user("exporter"),
            name="Exported Caster",
            free_spell_points=9,
            spells={"heal": 3},
            coin_adjustment=5000,
        )
        return generate_character_json(source).encode()

    def import_as(self, user):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.login_as(user)
        upload = SimpleUploadedFile("c.json", self.payload(), "application/json")
        response = self.client.post(reverse("characters:import"), {"file": upload})
        self.assertEqual(response.status_code, 200, response.content)
        return Character.objects.get(pk=response.json()["character_id"])

    def test_a_player_imports_none_of_them(self):
        imported = self.import_as(self.create_user("importing_player"))
        self.assertEqual(
            (imported.free_spell_points, imported.spells, imported.coin_adjustment),
            (0, {}, 0),
        )

    def test_a_game_master_imports_them_as_written(self):
        imported = self.import_as(self.create_dm())
        self.assertEqual(
            (imported.free_spell_points, imported.spells, imported.coin_adjustment),
            (9, {"heal": 3}, 5000),
        )

    def test_the_sites_own_export_brings_none_of_them_for_a_player(self):
        """A file this site wrote is still someone's character as it once
        stood, not the importer's: it grants a player nothing (coordinator's
        ruling on the #744 review)."""
        imported = self.import_as(self.create_user("own_export_player"))
        self.assertEqual(
            (imported.free_spell_points, imported.spells, imported.coin_adjustment),
            (0, {}, 0),
        )


class ImportHoldsCoinAndGearTest(BaseTestCase):
    """The #757 second review: a player's file carried 2,000,000 starting
    coin and 500,000 earned, and its chainmail came in free."""

    def file(self):
        return {
            "_format": "tarmar-character",
            "_version": 1,
            "data": {
                **CHARACTER_DEFAULTS,
                "name": "Rich File",
                "wealth_level": "Moderate",
                "starting_coin": 2_000_000,
                "money_earned": 500_000,
                "equipment": [
                    {"item_id": "broadsword", "quantity": 1, "to_hit_mod": 3},
                    {"item_id": "chainmail", "quantity": 1},
                    {"item_id": "custom", "name": "Dragonhide", "quantity": 1},
                ],
            },
        }

    def import_as(self, user):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.login_as(user)
        upload = SimpleUploadedFile(
            "c.json", json.dumps(self.file()).encode(), "application/json"
        )
        response = self.client.post(reverse("characters:import"), {"file": upload})
        self.assertEqual(response.status_code, 200, response.content)
        return response, Character.objects.get(pk=response.json()["character_id"])

    def test_a_player_gets_a_fresh_purse_and_buys_nothing_yet(self):
        """The wealth arrives unrolled, and gear bought before the wealth
        roll can leave the purse below zero once the roll sets the coin (an
        80 gp sword, then Subsistence: -70). So nothing is bought; every
        item is named as left out (coordinator's ruling on the #744 review).
        """
        response, imported = self.import_as(self.create_user("filer"))
        self.assertEqual((imported.starting_coin, imported.money_earned), (100, 0))
        self.assertEqual((imported.equipment, imported.coin_adjustment), ([], 0))
        self.assertEqual(imported.purse, 100)
        left_out = response.json()["left_out"]
        self.assertEqual(len(left_out), 3)
        self.assertIn(
            "Broadsword (bought on the sheet once wealth is rolled)", left_out[0]
        )
        self.assertIn("Chainmail", left_out[1])
        self.assertIn("Dragonhide (a custom item", left_out[2])
        self.assertIn("import left out", imported.history[-1]["new"])

    def test_the_wealth_is_rolled_on_the_sheet_not_by_import(self):
        """Wealth is a Game Master's field (#749): a player's file stating a
        level neither sets it nor rolls coin for it (the #744 seam review).
        The character arrives with its wealth unrolled and the unrolled coin,
        and nothing written to its dice log."""
        for level in ("Subsistence", "Merchant", "Rich"):
            with self.subTest(level):
                file = self.file()
                file["data"]["wealth_level"] = level
                file["data"]["equipment"] = []
                imported = held_to_player(
                    validate_import_payload(file), is_game_master=False
                )
                self.assertEqual(
                    imported.fields["wealth_level"], character_rules().defaults.wealth
                )
                self.assertEqual(
                    imported.fields["starting_coin"],
                    character_rules().default_starting_coin,
                )
                self.assertFalse(imported.fields["wealth_recorded"])
                self.assertEqual(imported.fields["dice_log"], "")

    def test_a_rich_file_buys_nothing_either(self):
        file = self.file()
        file["data"]["wealth_level"] = "Rich"
        imported = held_to_player(validate_import_payload(file), is_game_master=False)
        self.assertEqual(imported.fields["equipment"], [])
        self.assertEqual(len(imported.left_out), 3)

    def test_a_game_master_imports_it_as_written(self):
        response, imported = self.import_as(self.create_dm())
        self.assertEqual(imported.starting_coin, 2_000_000)
        self.assertEqual(len(imported.equipment), 3)
        self.assertEqual(response.json()["left_out"], [])
