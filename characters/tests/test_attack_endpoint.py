"""``POST /tarmar/characters/<pk>/attack/`` — the request layer (issue #101).

The arithmetic is covered by ``test_attack_service``; this file covers what
only an HTTP round trip can: who may call it, which bad inputs turn into a
clean 400 instead of a 500, that the resolved attack is persisted and trimmed,
and that the rate limit bites.

Resolution here is deterministic without a stub roller by construction, not by
patching: an unarmoured, unshielded, undodging target gives a Target Number a
skilled character clears on any face but a natural 1, so a hit is a hit
whatever the die does. The tests that need an exact face patch
``characters.dice.roll_d20`` — the one seam ``characters.dice`` rolls through.
"""

from unittest.mock import patch

from characters.models import MAX_COMBAT_LOG_ENTRIES, Character
from common.test_helpers import BaseTestCase

CHARACTER_DEFAULTS = {
    "strength": 14,
    "dexterity": 16,
    "intelligence": 12,
    "wisdom": 12,
    "constitution": 12,
    "charisma": 12,
    "appearance_roll": "3",
    "height_roll": "3",
    "weight_roll": "3",
    "fatigue_roll": 7,
}

VALID_ATTACK = {
    "weapon_index": 0,
    "armour_tier": "None",
    "defender_stops": 0,
    "shield_item_id": "",
    "defender_dodge": 0,
    "situational": 0,
}


class AttackEndpointTestBase(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user("owner")
        self.character = Character.objects.create(
            user=self.owner,
            name="Endpoint Duellist",
            skills={"weapon (saber)": 10},
            equipment=[
                {"item_id": "saber", "quantity": 1},
                {"item_id": "backpack", "quantity": 1},
                {"item_id": "homemade shiv", "quantity": 1, "category": "weapons"},
                {"item_id": "club", "quantity": 1},
            ],
            **CHARACTER_DEFAULTS,
        )
        self.url = f"/tarmar/characters/{self.character.pk}/attack/"

    def attack(self, **overrides):
        payload = dict(VALID_ATTACK)
        payload.update(overrides)
        return self.post_json(self.url, payload)


class AttackPermissionTest(AttackEndpointTestBase):
    def test_the_owner_may_resolve_an_attack(self):
        self.login_as(self.owner)
        self.assertEqual(self.attack().status_code, 200)

    def test_a_dm_may_resolve_someone_elses_attack(self):
        self.login_as(self.create_dm("dm"))
        self.assertEqual(self.attack().status_code, 200)

    def test_an_admin_may_resolve_someone_elses_attack(self):
        self.login_as(self.create_admin("admin"))
        self.assertEqual(self.attack().status_code, 200)

    def test_a_stranger_may_not(self):
        self.login_as(self.create_user("stranger"))
        self.assertEqual(self.attack().status_code, 403)
        self.character.refresh_from_db()
        self.assertEqual(self.character.combat_log, [])

    def test_an_anonymous_visitor_is_redirected_to_login(self):
        response = self.attack()
        self.assertEqual(response.status_code, 302)

    def test_get_is_not_allowed(self):
        self.login_as(self.owner)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_a_deleted_character_is_a_404(self):
        self.login_as(self.owner)
        self.character.is_deleted = True
        self.character.save()
        self.assertEqual(self.attack().status_code, 404)


class AttackValidationEndpointTest(AttackEndpointTestBase):
    def setUp(self):
        super().setUp()
        self.login_as(self.owner)

    def assert_rejected(self, **overrides):
        response = self.attack(**overrides)
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())
        return response.json()["error"]

    def test_a_missing_weapon_index_is_rejected(self):
        response = self.post_json(self.url, {"armour_tier": "None"})
        self.assertEqual(response.status_code, 400)

    def test_an_out_of_range_index_is_rejected(self):
        self.assert_rejected(weapon_index=99)

    def test_a_non_integer_index_is_rejected(self):
        self.assert_rejected(weapon_index="saber")

    def test_a_non_weapon_is_rejected(self):
        self.assertIn("not a weapon", self.assert_rejected(weapon_index=1))

    def test_a_custom_weapon_is_rejected_cleanly(self):
        self.assertIn("no combat class", self.assert_rejected(weapon_index=2))

    @patch("characters.dice.roll_d20", return_value=19)
    def test_a_gm_adjudicated_weapon_is_a_400_not_a_500(self, _roll):
        """Club has a class but "varies" for damage; a *landed* hit must not 500.

        The die is forced to a hit on purpose — the crash this guards was in the
        damage roll, which a miss never reaches, so a random die would pass this
        test most of the time by accident.
        """
        self.assertIn("GM-adjudicated", self.assert_rejected(weapon_index=3))

    def test_an_unknown_armour_tier_is_rejected(self):
        self.assertIn(
            "Unknown armour tier", self.assert_rejected(armour_tier="Mithril")
        )

    def test_a_skill_named_in_the_request_is_not_read(self):
        """Issue #753: the skill is the record's to decide, so naming another
        one in the request changes nothing about the swing."""
        payload = self.attack(skill_name="juggling").json()["attack"]
        self.assertEqual(payload["skill"]["name"], "weapon (saber)")

    def test_an_unknown_shield_is_rejected(self):
        self.assertIn(
            "Unknown shield", self.assert_rejected(shield_item_id="barrel_lid")
        )

    def test_an_out_of_range_dodge_is_rejected(self):
        self.assert_rejected(defender_dodge=25)

    def test_an_out_of_range_situational_is_rejected(self):
        self.assert_rejected(situational=99)

    def test_a_malformed_body_is_rejected(self):
        response = self.client.post(
            self.url, "not json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_no_rejected_request_writes_to_the_combat_log(self):
        self.assert_rejected(weapon_index=99)
        self.assert_rejected(armour_tier="Mithril")
        self.character.refresh_from_db()
        self.assertEqual(self.character.combat_log, [])


class AttackResolutionEndpointTest(AttackEndpointTestBase):
    def setUp(self):
        super().setUp()
        self.login_as(self.owner)

    def test_the_response_carries_the_whole_itemized_breakdown(self):
        payload = self.attack().json()["attack"]
        self.assertEqual(payload["weapon"]["name"], "Saber")
        self.assertEqual(payload["weapon"]["weapon_class"], "Striking")
        for section in ("target_number", "to_hit", "attack_roll", "damage"):
            self.assertIn(section, payload)
        self.assertEqual(
            payload["target_number"]["total"], payload["target_number"]["base"]
        )
        self.assertTrue(payload["dice_log"])

    @patch("characters.dice.roll_d20", return_value=13)
    def test_a_scripted_die_resolves_deterministically(self, _roll):
        payload = self.attack(armour_tier="Light").json()["attack"]
        self.assertEqual(payload["attack_roll"]["die"], 13)
        self.assertEqual(
            payload["attack_roll"]["total"], 13 + payload["to_hit"]["total"]
        )
        self.assertTrue(payload["attack_roll"]["hit"])

    @patch("characters.dice.roll_d20", return_value=1)
    @patch("characters.dice.roll_d6", return_value=4)
    def test_a_natural_one_returns_the_fumble_subtable(self, _d6, _d20):
        payload = self.attack().json()["attack"]
        self.assertTrue(payload["attack_roll"]["fumble"])
        self.assertEqual(payload["fumble"]["key"], "drop_weapon")
        self.assertEqual(payload["damage"]["multiplier"], 0)

    @patch("characters.dice.roll_d20", return_value=20)
    def test_a_natural_twenty_returns_a_confirmation(self, _roll):
        payload = self.attack().json()["attack"]
        # The confirm roll is the same patched 20, which auto-confirms.
        self.assertTrue(payload["confirm"]["hit"])
        self.assertTrue(payload["severe"])
        self.assertTrue(payload["bleeding"])
        self.assertEqual(payload["damage"]["multiplier"], 3)

    def test_the_shield_bonus_comes_from_the_catalog_not_the_request(self):
        payload = self.attack(shield_item_id="tower_shield").json()["attack"]
        self.assertEqual(payload["target_number"]["shield_bonus"], 3)

    def test_the_skill_level_is_derived_server_side(self):
        payload = self.attack().json()["attack"]
        self.assertGreater(payload["to_hit"]["skill_bonus"], 0)

    def test_an_untrained_swing_gets_no_skill_bonus(self):
        self.character.skills = {"knowledge": 10}
        self.character.save()
        payload = self.attack().json()["attack"]
        self.assertEqual(payload["to_hit"]["skill_bonus"], 0)


class AttackCombatLogPersistenceTest(AttackEndpointTestBase):
    def setUp(self):
        super().setUp()
        self.login_as(self.owner)

    @patch("characters.dice.roll_d20", return_value=15)
    def test_a_resolved_attack_is_appended_to_the_combat_log(self, _roll):
        self.attack()
        self.character.refresh_from_db()
        self.assertEqual(len(self.character.combat_log), 1)
        entry = self.character.combat_log[0]
        self.assertEqual(entry["weapon"], "Saber")
        self.assertEqual(entry["die"], 15)
        self.assertEqual(entry["by"], self.owner.username)
        self.assertTrue(entry["ts"])

    def test_the_log_is_trimmed_to_the_cap_keeping_the_newest(self):
        self.character.combat_log = [
            {"weapon": f"old {n}", "outcome": "miss"}
            for n in range(MAX_COMBAT_LOG_ENTRIES)
        ]
        self.character.save()
        self.attack()
        self.character.refresh_from_db()
        self.assertEqual(len(self.character.combat_log), MAX_COMBAT_LOG_ENTRIES)
        self.assertEqual(self.character.combat_log[0]["weapon"], "old 1")
        self.assertEqual(self.character.combat_log[-1]["weapon"], "Saber")

    def test_the_sheet_renders_the_log_newest_first(self):
        self.attack()
        self.attack(shield_item_id="small_shield")
        self.character.refresh_from_db()
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        rendered = response.context["combat_log_entries"]
        self.assertEqual(rendered, list(reversed(self.character.combat_log)))
        self.assertContains(response, "Recent attacks")

    def test_the_combat_log_is_not_exported(self):
        """It is per-table audit noise, excluded exactly as ``history`` is."""
        from character_export.export_utils import EXPORTABLE_FIELDS

        self.assertNotIn("combat_log", EXPORTABLE_FIELDS)
        self.assertNotIn("history", EXPORTABLE_FIELDS)


class AttackRateLimitTest(AttackEndpointTestBase):
    def setUp(self):
        super().setUp()
        self.login_as(self.owner)

    def test_the_endpoint_throttles_after_its_per_minute_allowance(self):
        for _ in range(60):
            self.assertEqual(self.attack().status_code, 200)
        self.assertEqual(self.attack().status_code, 429)
