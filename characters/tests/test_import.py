"""End-to-end tests for character export → import round-trip (issue #26)."""

import json

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from character_export.export_utils import (
    EXPORTABLE_FIELDS,
    generate_character_json,
)
from characters.constants import CORE_ATTRIBUTE_FIELDS, STARTING_AGE
from characters.dice import location_effects
from characters.import_service import (
    IMPORT_NOTICE_KEY,
    PLAYER_KEPT_FIELDS,
    fresh_player_fields,
    player_import_notice,
)
from characters.models import NEEDS_ATTRIBUTES, NEEDS_FATIGUE_ROLL, Character
from common.test_helpers import BaseTestCase


def _build_payload(name="Imported Hero", overrides=None):
    payload = {
        "_format": "tarmar-character",
        "_version": 1,
        "data": {
            "name": name,
            "strength": 12,
            "dexterity": 11,
            "intelligence": 14,
            "wisdom": 10,
            "constitution": 13,
            "charisma": 9,
            "appearance_roll": "3",
            "height_roll": "4",
            "weight_roll": "3",
            "fatigue_roll": 7,
            "provenance_class": "commoner",
            "provenance_detail": "Laborer",
            "location": "Village",
            "wealth_level": "Moderate",
            "starting_coin": 100,
            "skills": {"sword": 3},
            "free_skill_points": 0,
            "free_spell_points": 0,
            "is_played": False,
            "equipment": [{"item_id": "dagger", "quantity": 1}],
            "pack_dropped": False,
            "total_xp": 1500,
            "age": 20,
            "experience_terms": 1,
            "experience_log": [],
            "money_earned": 200,
            "aging_str_penalty": 0,
            "aging_dex_penalty": 0,
            "aging_int_penalty": 0,
            "aging_wis_penalty": 0,
            "aging_con_penalty": 0,
            "is_alive": True,
            "death_cause": "",
        },
    }
    if overrides:
        payload["data"].update(overrides)
    return payload


class CharacterImportTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        # Each test starts with a clean rate-limit counter; the import endpoint
        # is capped at 10/60s and these tests share one user, so without this the
        # later tests would 429 instead of exercising their validation path.
        from django.core.cache import cache

        cache.clear()
        self.user = self.create_user("import-user")

    def _post_import(self, body_bytes):
        upload = SimpleUploadedFile(
            "character.json", body_bytes, content_type="application/json"
        )
        return self.client.post("/tarmar/characters/import/", {"file": upload})

    # ---- happy paths ----

    def _import_as(self, importer) -> Character:
        """Import the standard payload as ``importer``; the saved character."""
        self.login_as(importer)
        body = json.dumps(_build_payload()).encode("utf-8")
        response = self._post_import(body)

        self.assertEqual(response.status_code, 200, msg=response.content.decode()[:400])
        data = response.json()
        self.assertTrue(data["success"])
        # The name the payload asked for, reported back unchanged: imports do
        # not rename (issue #571), and the response no longer carries an
        # always-false "renamed" flag saying so (issue #658).
        self.assertEqual(data["name"], "Imported Hero")

        character = Character.objects.get(pk=data["character_id"])
        self.assertEqual(character.user, importer)
        self.assertEqual(character.name, "Imported Hero")
        return character

    def test_import_creates_character_owned_by_importer(self):
        """A player's import is theirs, and arrives with its dice unrolled,
        its skills a new character's and nothing bought (issue #744)."""
        character = self._import_as(self.user)
        self.assertIsNone(character.strength)
        self.assertEqual(character.skills, location_effects("Village")[0])
        self.assertEqual(character.equipment, [])

    def test_a_game_masters_import_creates_character_owned_by_importer(self):
        """A Game Master's import is his, kept verbatim (issue #744)."""
        character = self._import_as(self.create_dm("import-dm"))
        self.assertEqual(character.strength, 12)
        self.assertEqual(character.equipment, [{"item_id": "dagger", "quantity": 1}])
        self.assertEqual(character.skills, {"sword": 3})

    def test_round_trip_export_then_import(self):
        # Build a real character, export it, re-import.
        source = Character.objects.create(
            user=self.user,
            name="Round Trip",
            strength=10,
            dexterity=11,
            intelligence=12,
            wisdom=13,
            constitution=14,
            charisma=15,
            appearance_roll="3",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=7,
            skills={"axe": 6},
            equipment=[{"item_id": "broadsword", "quantity": 1}],
            total_xp=2500,
            free_spell_points=3,
            is_played=True,
        )
        body = generate_character_json(source).encode("utf-8")

        # By role (issue #744): a Game Master's import keeps the earned
        # values; a player's starts them where a new character's start, even
        # from this site's own export, and is unplayed.
        for importer, earned, played in (
            (self.create_dm("importer-dm"), ({"axe": 6}, 2500, 3), True),
            (
                self.create_user("importer-user"),
                (location_effects("Village")[0], 0, 0),
                False,
            ),
        ):
            with self.subTest(importer=importer.username):
                # A fresh login flushes the session, gate pass and all.
                self.logout()
                self.login_as(importer)
                response = self._post_import(body)

                self.assertEqual(response.status_code, 200)
                data = response.json()
                imported = Character.objects.get(pk=data["character_id"])
                self.assertEqual(imported.user, importer)
                self.assertEqual(imported.name, "Round Trip")
                # issue #137: free_spell_points predated EXPORTABLE_FIELDS and
                # used to silently reset to 0 on every export/import round trip.
                self.assertEqual(
                    (imported.skills, imported.total_xp, imported.free_spell_points),
                    earned,
                )
                # issue #156: is_played (migration 0002) was likewise missing
                # from EXPORTABLE_FIELDS, so an edited character's Played badge
                # silently reverted to Ready on every round trip.
                self.assertEqual(imported.is_played, played)

    def test_round_trip_keeps_an_item_stored_before_custom_items_kept_a_cost(self):
        """Review of #927: the chat used to store custom items as name and quantity."""
        old_shape = [{"name": "Vane's key", "quantity": 2}]
        source = Character.objects.create(
            user=self.user,
            name="Old Shape",
            strength=10,
            dexterity=11,
            intelligence=12,
            wisdom=13,
            constitution=14,
            charisma=15,
            appearance_roll="3",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=7,
            equipment=old_shape,
        )
        self.logout()
        self.login_as(self.create_dm("old-shape-dm"))

        response = self._post_import(generate_character_json(source).encode("utf-8"))

        self.assertEqual(response.status_code, 200, response.content)
        imported = Character.objects.get(pk=response.json()["character_id"])
        self.assertEqual(imported.equipment, old_shape)

    def test_round_trip_export_then_import_with_the_free_text_rolls_cleared(self):
        """Issue #637: an empty Appearance box saved but would not re-import.

        appearance_roll, height_roll, weight_roll and provenance_detail are
        free-text boxes on the sheet that autosave, and "" is what three of
        them default to, so a character can perfectly well hold one. They
        carried no blank=True, and the importer's full_clean refused them --
        the write path saving what the import path would not accept.
        """
        source = Character.objects.create(
            user=self.user,
            name="Blank Rolls",
            strength=10,
            dexterity=11,
            intelligence=12,
            wisdom=13,
            constitution=14,
            charisma=15,
            appearance_roll="",
            height_roll="",
            weight_roll="",
            provenance_detail="",
            fatigue_roll=7,
        )
        body = generate_character_json(source).encode("utf-8")

        # A Game Master's import round-trips the blanks as they are. A
        # player's is accepted too; the provenance detail is a Game Master's
        # field, so it arrives at a new character's (issue #744).
        for importer, detail in (
            (self.create_dm("blank-rolls-dm"), ""),
            (self.create_user("blank-rolls-importer"), "Laborer"),
        ):
            with self.subTest(importer=importer.username):
                # A fresh login flushes the session, gate pass and all.
                self.logout()
                self.login_as(importer)
                response = self._post_import(body)

                self.assertEqual(
                    response.status_code, 200, msg=response.content.decode()[:400]
                )
                imported = Character.objects.get(pk=response.json()["character_id"])
                self.assertEqual(imported.appearance_roll, "")
                self.assertEqual(imported.height_roll, "")
                self.assertEqual(imported.weight_roll, "")
                self.assertEqual(imported.provenance_detail, detail)

    def test_round_trip_survives_a_skill_past_the_old_fifteen_point_cap(self):
        """Issue #146: experience terms add points uncapped, so a long career
        could produce an export its own importer rejected. It must round-trip."""
        source = Character.objects.create(
            user=self.user,
            name="Long Career",
            strength=10,
            dexterity=11,
            intelligence=12,
            wisdom=13,
            constitution=14,
            charisma=15,
            appearance_roll="3",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=7,
            total_xp=2500,
            # 21 points is level VI on the ordinary ladder; 6 Arcane points is
            # level II on the doubled one (issue #154).
            skills={"sword": 21, "arcane": 6},
        )
        self.assertEqual(source.highest_skill_level, 6)
        body = generate_character_json(source).encode("utf-8")

        # By role (issue #744): the long career survives a Game Master's
        # import; a player's is still accepted, with a new character's skills.
        for importer, skills in (
            (self.create_dm("long-career-dm"), {"sword": 21, "arcane": 6}),
            (self.create_user("long-career-importer"), location_effects("Village")[0]),
        ):
            with self.subTest(importer=importer.username):
                # A fresh login flushes the session, gate pass and all.
                self.logout()
                self.login_as(importer)
                response = self._post_import(body)

                self.assertEqual(
                    response.status_code, 200, msg=response.content.decode()[:400]
                )
                imported = Character.objects.get(pk=response.json()["character_id"])
                self.assertEqual(imported.skills, skills)

    def test_round_trip_export_then_import_zero_total_xp(self):
        """Issue #164: total_xp defaults to 0 for a fresh character, but
        CHARACTER_INT_FIELDS validation used to enforce a minimum of 1 on
        import ("Value must be positive"), so a legitimately exported fresh
        character could never be re-imported. Every exportable field must
        round-trip byte-equal."""
        source = Character.objects.create(
            user=self.user,
            name="Fresh Recruit",
            strength=10,
            dexterity=11,
            intelligence=12,
            wisdom=13,
            constitution=14,
            charisma=15,
            appearance_roll="3",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=7,
            total_xp=0,
        )
        self.assertEqual(source.total_xp, 0)
        body = generate_character_json(source).encode("utf-8")

        # By role (issue #744): a Game Master's import round-trips every
        # field; a player's keeps what a player chooses and holds a fresh
        # character's value for the rest.
        stated = json.loads(body)["data"]
        for importer, cleared in (
            (self.create_dm("fresh-recruit-dm"), {}),
            (
                self.create_user("fresh-recruit-importer"),
                {**fresh_player_fields(stated), "equipment": []},
            ),
        ):
            with self.subTest(importer=importer.username):
                # A fresh login flushes the session, gate pass and all.
                self.logout()
                self.login_as(importer)
                response = self._post_import(body)

                self.assertEqual(
                    response.status_code, 200, msg=response.content.decode()[:400]
                )
                imported = Character.objects.get(pk=response.json()["character_id"])
                for field in EXPORTABLE_FIELDS:
                    expected = (
                        cleared[field] if field in cleared else getattr(source, field)
                    )
                    self.assertEqual(
                        getattr(imported, field),
                        expected,
                        f"{field} did not round-trip byte-equal",
                    )

    def test_a_taken_name_is_kept_not_suffixed(self):
        """Names need not be unique (issue #571): identity is the record id."""
        Character.objects.create(
            user=self.user,
            name="Imported Hero",
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
        self.login_as(self.user)
        body = json.dumps(_build_payload()).encode("utf-8")
        response = self._post_import(body)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["name"], "Imported Hero")
        self.assertNotIn("renamed", data)
        self.assertEqual(
            Character.objects.filter(user=self.user, name="Imported Hero").count(), 2
        )

    def test_rejects_missing_file(self):
        self.login_as(self.user)
        response = self.client.post("/tarmar/characters/import/", {})
        self.assertEqual(response.status_code, 400)

    def test_rejects_non_json_payload(self):
        self.login_as(self.user)
        response = self._post_import(b"not json at all")
        self.assertEqual(response.status_code, 400)
        self.assertIn("JSON", response.json()["error"])

    def test_rejects_wrong_format_marker(self):
        self.login_as(self.user)
        body = json.dumps({"_format": "something-else", "data": {}}).encode("utf-8")
        response = self._post_import(body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Tarmar character export", response.json()["error"])

    def test_rejects_out_of_range_skill_points(self):
        """Skill levels are uncapped (issue #146), but the storage sanity
        ceiling that allocation enforces still applies to imported files."""
        from characters.skills import MAX_SKILL_POINTS

        over = MAX_SKILL_POINTS + 1
        self.login_as(self.user)
        body = json.dumps(_build_payload(overrides={"skills": {"sword": over}})).encode(
            "utf-8"
        )
        response = self._post_import(body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("invalid points", response.json()["error"])
        self.assertFalse(Character.objects.filter(skills={"sword": over}).exists())

    def test_rejects_negative_skill_points(self):
        """Removing the level cap must not let negative totals through."""
        self.login_as(self.user)
        body = json.dumps(_build_payload(overrides={"skills": {"sword": -1}})).encode(
            "utf-8"
        )
        response = self._post_import(body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("invalid points", response.json()["error"])

    def test_import_accepts_total_xp_past_smallint_limit(self):
        """Issue #136: total_xp is a 32-bit PositiveIntegerField, so import
        must accept the same range the update-field endpoint does -- level
        10 requires 512,000 XP, well past the smallint cap of 32767.

        By role (issue #744): accepted from anyone, kept for a Game Master,
        started at a new character's 0 for a player."""
        self._accepted_by_role("total_xp", 512000, player_gets=0)

    def test_import_accepts_starting_coin_past_smallint_limit(self):
        self._accepted_by_role("starting_coin", 40000, player_gets=100)

    def _accepted_by_role(self, field: str, value: int, *, player_gets: int) -> None:
        body = json.dumps(_build_payload(overrides={field: value})).encode("utf-8")
        for importer, kept in (
            (self.create_dm(f"{field}-dm"), value),
            (self.user, player_gets),
        ):
            with self.subTest(importer=importer.username):
                # A fresh login flushes the session, gate pass and all.
                self.logout()
                self.login_as(importer)
                response = self._post_import(body)
                self.assertEqual(
                    response.status_code, 200, msg=response.content.decode()[:400]
                )
                character = Character.objects.get(pk=response.json()["character_id"])
                self.assertEqual(getattr(character, field), kept)

    def test_import_rejects_a_fatigue_roll_two_dice_cannot_produce(self):
        """Import is held to the same 2d6 band as update-field (issue #653).

        It used to accept anything up to the smallint ceiling, so a payload
        saying 30000 produced a character with Fatigue 30040.
        """
        self.login_as(self.user)
        for rejected_value in (0, 13, 30000, 32768):
            with self.subTest(value=rejected_value):
                body = json.dumps(
                    _build_payload(overrides={"fatigue_roll": rejected_value})
                ).encode("utf-8")
                response = self._post_import(body)
                self.assertEqual(response.status_code, 400)

    def test_rejects_negative_free_spell_points(self):
        """free_spell_points is a PositiveSmallIntegerField like
        free_skill_points; a negative value must fail full_clean, not save
        (issue #137). A Game Master's import, since a player's drops the
        field altogether."""
        self.login_as(self.create_dm("negative-spell-dm"))
        body = json.dumps(_build_payload(overrides={"free_spell_points": -1})).encode(
            "utf-8"
        )
        response = self._post_import(body)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Character.objects.filter(name="Imported Hero").exists())

    def test_rejects_non_boolean_is_played(self):
        """is_played is a BooleanField; a value full_clean can't coerce to a
        boolean must fail validation, not save (issue #156)."""
        self.login_as(self.user)
        body = json.dumps(_build_payload(overrides={"is_played": "banana"})).encode(
            "utf-8"
        )
        response = self._post_import(body)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Character.objects.filter(name="Imported Hero").exists())

    def test_rejects_payload_without_data_object(self):
        self.login_as(self.user)
        body = json.dumps({"_format": "tarmar-character"}).encode("utf-8")
        response = self._post_import(body)
        self.assertEqual(response.status_code, 400)

    def test_rejects_payload_with_emoji_name(self):
        self.login_as(self.user)
        body = json.dumps(_build_payload(name="Hero \U0001f600")).encode("utf-8")
        response = self._post_import(body)
        self.assertEqual(response.status_code, 400)
        # The sheet's words since the import asks the sheet's rule (#786
        # review); this pinned the import's own copy of the rule's wording.
        self.assertIn("Name may contain letters", response.json()["error"])

    def test_rejects_empty_name(self):
        self.login_as(self.user)
        body = json.dumps(_build_payload(name="   ")).encode("utf-8")
        response = self._post_import(body)
        self.assertEqual(response.status_code, 400)
        # As above: the sheet's words, "Name cannot be empty".
        self.assertIn("Name cannot be empty", response.json()["error"])

    def test_a_refused_name_reads_as_the_sheet_says_it(self):
        """One name rule, one set of words: the import asks the sheet's
        ``Character.clean_field_value`` (#786 review) rather than keeping a
        copy that had drifted into its own messages."""
        self.login_as(self.user)
        for name in ("   ", "x" * 101, "Hero \U0001f600", "Wren<b>"):
            with self.subTest(name=name):
                with self.assertRaises(ValidationError) as refusal:
                    Character.clean_field_value("name", name)
                response = self._post_import(
                    json.dumps(_build_payload(name=name)).encode("utf-8")
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json()["error"], refusal.exception.messages[0]
                )

    def test_rejects_too_many_skills(self):
        self.login_as(self.user)
        # Above the MAX_SKILL_ENTRIES cap (200) on the JSONField validator.
        too_many = {f"skill_{idx}": 1 for idx in range(250)}
        body = json.dumps(_build_payload(overrides={"skills": too_many})).encode(
            "utf-8"
        )
        response = self._post_import(body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Too many skills", response.json()["error"])

    def test_rejects_dead_character_without_cause(self):
        # Death invariant: is_alive=False requires a non-empty death_cause.
        self.login_as(self.user)
        body = json.dumps(
            _build_payload(overrides={"is_alive": False, "death_cause": ""})
        ).encode("utf-8")
        response = self._post_import(body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("death cause", response.json()["error"].lower())

    def test_accepts_dead_character_with_cause(self):
        # By role (issue #744): a Game Master's import keeps the death; a
        # player's is a new character, alive.
        body = json.dumps(
            _build_payload(
                overrides={"is_alive": False, "death_cause": "Slain by a troll"}
            )
        ).encode("utf-8")
        for importer, alive in ((self.create_dm("troll-dm"), False), (self.user, True)):
            with self.subTest(importer=importer.username):
                self.logout()
                self.login_as(importer)
                response = self._post_import(body)
                self.assertEqual(
                    response.status_code, 200, msg=response.content.decode()[:400]
                )
                self.assertEqual(
                    Character.objects.get(pk=response.json()["character_id"]).is_alive,
                    alive,
                )

    def test_rejects_equipment_with_out_of_range_modifier(self):
        # Import must not bypass the add/update modifier bounds.
        self.login_as(self.user)
        bad_equipment = [{"item_id": "blade", "quantity": 1, "to_hit_mod": 999}]
        body = json.dumps(
            _build_payload(overrides={"equipment": bad_equipment})
        ).encode("utf-8")
        response = self._post_import(body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("to_hit_mod", response.json()["error"])

    def test_rejects_equipment_with_invalid_quantity(self):
        self.login_as(self.user)
        bad_equipment = [{"item_id": "dagger", "quantity": 0}]
        body = json.dumps(
            _build_payload(overrides={"equipment": bad_equipment})
        ).encode("utf-8")
        response = self._post_import(body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("quantity", response.json()["error"])

    def test_rejects_equipment_name_with_control_characters(self):
        # The add endpoint strips control characters out of names; import must
        # not be a way to smuggle them into stored equipment (issue #120).
        self.login_as(self.user)
        bad_equipment = [{"item_id": "custom", "name": "Sword\u0000", "quantity": 1}]
        body = json.dumps(
            _build_payload(overrides={"equipment": bad_equipment})
        ).encode("utf-8")
        response = self._post_import(body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("control characters", response.json()["error"])

    def test_accepts_equipment_name_with_quotes(self):
        # Quotes stay legal -- the page escapes them -- so import keeps them.
        # By role (issue #744): a custom item survives a Game Master's import;
        # a player's import is still accepted and drops the custom item.
        equipment = [{"item_id": "custom", "name": "Bob's 6\" dagger", "quantity": 1}]
        body = json.dumps(_build_payload(overrides={"equipment": equipment})).encode(
            "utf-8"
        )
        for importer, names in (
            (self.create_dm("quotes-dm"), ["Bob's 6\" dagger"]),
            (self.user, []),
        ):
            with self.subTest(importer=importer.username):
                # A fresh login flushes the session, gate pass and all.
                self.logout()
                self.login_as(importer)
                response = self._post_import(body)
                self.assertEqual(
                    response.status_code, 200, msg=response.content.decode()[:400]
                )
                character = Character.objects.get(pk=response.json()["character_id"])
                self.assertEqual([item["name"] for item in character.equipment], names)

    def test_rejects_equipment_notes_over_length_cap(self):
        # The add/update endpoints cap notes; import must too.
        self.login_as(self.user)
        bad_equipment = [{"item_id": "dagger", "quantity": 1, "notes": "x" * 201}]
        body = json.dumps(
            _build_payload(overrides={"equipment": bad_equipment})
        ).encode("utf-8")
        response = self._post_import(body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("notes", response.json()["error"])

    def test_rejects_skill_name_with_control_characters(self):
        self.login_as(self.user)
        body = json.dumps(
            _build_payload(overrides={"skills": {"sword\u0000play": 3}})
        ).encode("utf-8")
        response = self._post_import(body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("control characters", response.json()["error"])

    def test_rejects_non_object_experience_log_entries(self):
        self.login_as(self.user)
        body = json.dumps(
            _build_payload(overrides={"experience_log": ["not-an-object"]})
        ).encode("utf-8")
        response = self._post_import(body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("experience log", response.json()["error"].lower())

    # ---- issue #122: wrong-typed containers must be rejected, not skipped ----

    def test_rejects_wrong_typed_skills(self):
        self.login_as(self.user)
        body = json.dumps(
            _build_payload(overrides={"skills": ["not", "a", "dict"]})
        ).encode("utf-8")
        response = self._post_import(body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("skills", response.json()["error"].lower())
        self.assertFalse(Character.objects.filter(user=self.user).exists())

    def test_rejects_wrong_typed_equipment(self):
        self.login_as(self.user)
        body = json.dumps(
            _build_payload(overrides={"equipment": {"also": "wrong"}})
        ).encode("utf-8")
        response = self._post_import(body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("equipment", response.json()["error"].lower())
        self.assertFalse(Character.objects.filter(user=self.user).exists())

    def test_rejects_wrong_typed_experience_log(self):
        self.login_as(self.user)
        body = json.dumps(_build_payload(overrides={"experience_log": "wrong"})).encode(
            "utf-8"
        )
        response = self._post_import(body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("experience log", response.json()["error"].lower())
        self.assertFalse(Character.objects.filter(user=self.user).exists())

    def test_rejects_experience_log_entry_with_non_string_track(self):
        self.login_as(self.user)
        body = json.dumps(
            _build_payload(overrides={"experience_log": [{"term": 1, "track": 42}]})
        ).encode("utf-8")
        response = self._post_import(body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("track", response.json()["error"].lower())
        self.assertFalse(Character.objects.filter(user=self.user).exists())

    def test_rejects_experience_log_entry_with_non_numeric_xp_awarded(self):
        self.login_as(self.user)
        body = json.dumps(
            _build_payload(
                overrides={
                    "experience_log": [{"track": "laborer", "xp_awarded": "lots"}]
                }
            )
        ).encode("utf-8")
        response = self._post_import(body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("xp_awarded", response.json()["error"])
        self.assertFalse(Character.objects.filter(user=self.user).exists())

    def test_rejects_experience_log_entry_with_invalid_skills_gained(self):
        self.login_as(self.user)
        body = json.dumps(
            _build_payload(
                overrides={
                    "experience_log": [{"track": "laborer", "skills_gained": ["sword"]}]
                }
            )
        ).encode("utf-8")
        response = self._post_import(body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("skills_gained", response.json()["error"])
        self.assertFalse(Character.objects.filter(user=self.user).exists())

    def test_full_repro_from_issue_no_longer_bricks_character(self):
        """The exact repro from issue #122: wrong types on all three fields at
        once used to return {"success": true}, save the corrupt row, and then
        permanently 500 the character edit page. It must now be rejected."""
        self.login_as(self.user)
        body = json.dumps(
            _build_payload(
                overrides={
                    "skills": ["not", "a", "dict"],
                    "equipment": {"also": "wrong"},
                    "experience_log": "wrong",
                }
            )
        ).encode("utf-8")
        response = self._post_import(body)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json().get("success", False))
        self.assertFalse(
            Character.objects.filter(user=self.user, name="Imported Hero").exists()
        )

    def test_requires_login(self):
        # Logged-out user redirects (login_required).
        body = json.dumps(_build_payload()).encode("utf-8")
        response = self._post_import(body)
        self.assertIn(response.status_code, (302, 403))


class CharacterExportJsonTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user("json-export-user")
        self.character = Character.objects.create(
            user=self.user,
            name="JSON Test",
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

    def test_json_export_returns_round_trippable_payload(self):
        self.login_as(self.user)
        response = self.client.get(f"/tarmar/export/{self.character.pk}/json/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        payload = json.loads(response.content)
        self.assertEqual(payload["_format"], "tarmar-character")
        self.assertEqual(payload["data"]["name"], "JSON Test")
        self.assertIn("attachment", response["Content-Disposition"])


class ImportServiceTest(BaseTestCase):
    """``import_service`` called directly, as the upload view calls it."""

    def setUp(self):
        super().setUp()
        from characters import import_service

        self.import_service = import_service
        self.game_master = self.create_dm("service-dm")

    def _save(self, overrides):
        return self.import_service.save_imported_character(
            self.game_master,
            self.import_service.validate_import_payload(
                _build_payload(overrides=overrides)
            ),
        )

    def test_rejections_save_nothing(self):
        with self.assertRaises(self.import_service.CharacterImportError) as raised:
            self._save({"is_alive": False, "death_cause": ""})
        self.assertIn("death cause", str(raised.exception))

        with self.assertRaises(self.import_service.CharacterImportError) as raised:
            self._save({"provenance_class": "royalty"})
        self.assertIn("Import failed", str(raised.exception))
        self.assertFalse(Character.objects.exists())

    def test_validate_rejects_a_non_object(self):
        with self.assertRaises(self.import_service.CharacterImportError):
            self.import_service.validate_import_payload(["not", "an", "object"])


class PlayerImportArrivesUnrolledTest(BaseTestCase):
    """Issue #744: a file is not dice. A player could export, edit the numbers
    and import a character with every roll at its best, any XP, skills, coin
    or age, equipment modifiers, or a dead character alive. Spencer's house
    rule holds players to the rules and lets a Game Master override, so a
    player's import keeps only what a player sets on the sheet, the rest
    arrives unrolled or at a fresh character's value, and the sheet says what
    was dropped and why; a Game Master's is verbatim."""

    # What the file states past the standard payload: every kind of value a
    # player may not carry in, beside some they may.
    STATED = {
        "fatigue_roll": 12,
        "location": "Village",
        # A player's people is theirs to state, and kept (#810).
        "people": "Kanu",
        "literacy_roll": 3,
        "literacy_rolled": True,
        "free_skill_points": 5,
        "free_spell_points": 2,
        "starting_coin": 40000,
        "dice_log": "STR: 3d6 = 18",
        "experience_log": [{"track": "campaigner", "xp_awarded": 1500}],
        "equipment": [
            {
                "item_id": "dagger",
                "quantity": 1,
                "to_hit_mod": 3,
                "paid": 2,
                "notes": "Gran's",
            },
            {"item_id": "custom", "name": "Vorpal Blade", "quantity": 1},
        ],
        "provenance_class": "nobility",
        "provenance_detail": "Minor noble house",
        "wealth_level": "Merchant",
        "appearance_roll": "1",
        "is_played": True,
    }

    def setUp(self):
        super().setUp()
        from django.core.cache import cache

        cache.clear()

    def _import(self, importer, overrides=None):
        self.login_as(importer)
        upload = SimpleUploadedFile(
            "character.json",
            json.dumps(
                _build_payload(overrides={**self.STATED, **(overrides or {})})
            ).encode("utf-8"),
            content_type="application/json",
        )
        response = self.client.post("/tarmar/characters/import/", {"file": upload})
        self.assertEqual(response.status_code, 200, msg=response.content.decode()[:400])
        return response.json(), Character.objects.get(
            pk=response.json()["character_id"]
        )

    def test_a_player_keeps_only_what_a_player_sets_on_the_sheet(self):
        stated = _build_payload(overrides=self.STATED)["data"]
        _data, character = self._import(self.create_user("kept-player"))

        for name in PLAYER_KEPT_FIELDS - {"equipment"}:
            with self.subTest(kept=name):
                self.assertEqual(getattr(character, name), stated[name])
        # Nothing carried comes in: the wealth is unrolled, so nothing is
        # bought yet, and a custom item is a Game Master's to add.
        self.assertEqual(character.equipment, [])
        self.assertEqual(character.purse, 100)

    def test_everything_else_arrives_as_a_fresh_character_has_it(self):
        _data, character = self._import(self.create_user("fresh-player"))

        self.assertEqual(
            [getattr(character, name) for name, _short in CORE_ATTRIBUTE_FIELDS],
            [None] * 6,
        )
        self.assertIsNone(character.fatigue_roll)
        self.assertIsNone(character.literacy_roll)
        self.assertFalse(character.literacy_rolled, "the player rolls it here")
        self.assertEqual(
            character.missing_parts, [NEEDS_ATTRIBUTES, NEEDS_FATIGUE_ROLL]
        )
        self.assertEqual(character.literacy_result, "Not rolled")
        self.assertEqual(
            (
                character.total_xp,
                character.level,
                character.age,
                character.experience_terms,
                character.experience_log,
                character.money_earned,
                character.free_skill_points,
                character.free_spell_points,
            ),
            (0, 1, STARTING_AGE, 0, [], 0, 0, 0),
        )
        # The skill a new Village character is granted; a new character's
        # background, appearance and coin, with nothing rolled for them.
        self.assertEqual(character.skills, location_effects("Village")[0])
        self.assertEqual(
            (
                character.provenance_class,
                character.provenance_detail,
                character.wealth_level,
                character.starting_coin,
                character.appearance_roll,
                character.is_played,
            ),
            ("commoner", "Laborer", "Moderate", 100, "", False),
        )
        # Unrolled, so the sheet offers the player their background rolls.
        self.assertEqual(
            (character.provenance_recorded, character.wealth_recorded),
            (False, False),
        )
        # The dice log records only dice this site rolled, and none were.
        self.assertEqual(character.dice_log, "")

    def test_the_notice_names_each_value_dropped_and_says_why(self):
        data, character = self._import(self.create_user("notice-player"))

        notice = character.import_notice
        self.assertTrue(
            notice.startswith(
                "Imported by a player, so this is a new, living character: what "
                "the file said was rolled, earned or ruled was not kept: STR 12; "
                "DEX 11; INT 14; WIS 10; CON 13; CHR 9; "
            ),
            notice,
        )
        for named in (
            "the Fatigue roll 12",
            "the literacy roll 3",
            "the record that literacy was rolled",
            "provenance class nobility",
            "provenance detail Minor noble house",
            "wealth level Merchant",
            "starting coin 40000",
            "appearance roll 1",
            "that the character has been played",
            "the skills (sword 3)",
            "free skill points 5",
            "free spell points 2",
            "1500 XP",
            "age 20",
            "experience terms 1",
            "the experience log (1 entries)",
            "money earned 200",
            "1 × Dagger (bought on the sheet once wealth is rolled)",
            "Vorpal Blade (a custom item is a Game Master's to add)",
        ):
            with self.subTest(named=named):
                self.assertIn(named, notice)
        self.assertTrue(notice.endswith("and experience is earned here."))
        self.assertEqual(data[IMPORT_NOTICE_KEY], notice)

    def test_aging_and_a_ruling_are_named_too(self):
        stated = {
            "location": "Consult GM",
            "aging_str_penalty": -2,
            "literacy_ruling": True,
        }
        fresh = fresh_player_fields(stated)

        notice = player_import_notice(stated, fresh)

        self.assertIn("the aging penalty on STR (-2)", notice)
        self.assertIn("the Game Master's literacy ruling (literate)", notice)
        self.assertEqual(fresh["skills"], {}, "Consult GM grants no skill")

    def _post(self, importer, body: bytes):
        self.logout()
        self.login_as(importer)
        response = self.client.post(
            "/tarmar/characters/import/",
            {
                "file": SimpleUploadedFile(
                    "character.json", body, content_type="application/json"
                )
            },
        )
        self.assertEqual(response.status_code, 200, msg=response.content.decode()[:400])
        return Character.objects.get(pk=response.json()["character_id"])

    def _dead_export(self, owner) -> bytes:
        fallen = Character.objects.create(
            user=owner,
            name="Fallen",
            strength=12,
            dexterity=12,
            intelligence=12,
            wisdom=12,
            constitution=12,
            charisma=12,
            fatigue_roll=7,
            total_xp=3000,
            is_alive=False,
            death_cause="Failed a survival roll",
        )
        return generate_character_json(fallen).encode("utf-8")

    def test_the_sites_own_export_is_still_a_new_character_for_a_player(self):
        """A file this site wrote is someone's character as it once stood,
        not the importer's as it stands: a copy of a dead character, or of
        one from before a bad roll, would undo the death or the roll. So it
        arrives as any player's import does, new and alive, and the notice
        says so (coordinator's ruling on the #744 review, for Spencer)."""
        player = self.create_user("own-export-player")

        character = self._post(player, self._dead_export(player))

        self.assertTrue(character.is_alive)
        self.assertEqual(character.death_cause, "")
        self.assertEqual((character.strength, character.total_xp), (None, 0))
        self.assertIn("a new, living character", character.import_notice)
        self.assertIn("the character's death", character.import_notice)
        self.assertIn("3000 XP", character.import_notice)
        # The dead character on the site stays dead.
        self.assertFalse(
            Character.objects.get(user=player, name="Fallen", is_alive=False).is_alive
        )

    def test_the_notice_names_no_value_that_was_already_a_new_characters(self):
        """A real export writes the purse's movements as the string "0.00",
        which is not equal to a new character's ``Decimal(0)`` until it is
        read as the field reads it; the notice named "coin adjustment 0.00"
        as dropped on every such import."""
        player = self.create_user("zero-coin-player")

        character = self._post(player, self._dead_export(player))

        self.assertNotIn("coin adjustment", character.import_notice)
        # No skills in the file, so none dropped, though the Origin grants one.
        self.assertNotIn("the skills", character.import_notice)
        self.assertIn("STR 12", character.import_notice)

    def test_another_players_public_character_is_not_copied(self):
        """The reviewer's case: a public character's export, STR 22, 512,000
        XP and a custom item, imported by someone else."""
        owner = self.create_user("public-owner")
        strong = Character.objects.create(
            user=owner,
            name="Borrowed",
            strength=22,
            dexterity=12,
            intelligence=12,
            wisdom=12,
            constitution=12,
            charisma=12,
            fatigue_roll=7,
            total_xp=512000,
            equipment=[{"item_id": "custom", "name": "Blade of Plenty", "quantity": 1}],
            is_public=True,
        )

        character = self._post(
            self.create_user("copying-player"),
            generate_character_json(strong).encode("utf-8"),
        )

        self.assertEqual(
            (character.strength, character.total_xp, character.equipment),
            (None, 0, []),
        )
        self.assertIn("STR 22", character.import_notice)
        self.assertIn("Blade of Plenty", character.import_notice)

    def test_a_players_import_can_be_exported_and_imported_again(self):
        """A player's import holds unmade rolls, and its own export must come
        back, for either role (the nulls used to be refused before the role
        was even read)."""
        player = self.create_user("again-player")
        _data, first = self._import(player)
        again = generate_character_json(first).encode("utf-8")

        for importer in (player, self.create_dm("again-dm")):
            with self.subTest(importer=importer.username):
                character = self._post(importer, again)
                self.assertIsNone(character.strength)
                self.assertEqual(
                    character.missing_parts, [NEEDS_ATTRIBUTES, NEEDS_FATIGUE_ROLL]
                )

    def test_a_game_masters_ruling_in_the_file_is_dropped(self):
        _data, character = self._import(
            self.create_user("ruled-player"),
            {"location": "Consult GM", "literacy_roll": None, "literacy_ruling": True},
        )

        self.assertIsNone(character.literacy_ruling)
        self.assertIn(
            "the Game Master's literacy ruling (literate)", character.import_notice
        )

    def test_a_file_stating_nothing_dropped_leaves_no_notice(self):
        """Only what was dropped is named, so a file that states nothing a
        player may not carry in has nothing to say."""
        stated = {"name": "Blank", "location": "City", "wealth_level": "Moderate"}
        fresh = fresh_player_fields(stated)

        self.assertEqual(player_import_notice({**stated, **fresh}, fresh), "")

    def test_the_player_rolls_what_the_import_dropped_on_the_sheet_once(self):
        """The paths a player has after an import (#748, #749): the sheet's
        roll controls for the attributes, the background and appearance, and
        the Fatigue and literacy rolls -- each once, the second refused."""
        player = self.create_user("rolling-player")
        _data, character = self._import(player)
        base = f"/tarmar/characters/{character.pk}"

        for path in (
            "roll/attributes",
            "roll/provenance",
            "roll/wealth",
            "roll/appearance",
            "roll-fatigue",
            "roll-literacy",
        ):
            with self.subTest(roll=path):
                self.assertEqual(self.post_json(f"{base}/{path}/", {}).status_code, 200)
                self.assertEqual(self.post_json(f"{base}/{path}/", {}).status_code, 400)

        character.refresh_from_db()
        self.assertTrue(character.is_complete)
        self.assertTrue(character.provenance_recorded and character.wealth_recorded)
        self.assertIsNotNone(character.literacy_roll)

    def test_the_player_may_buy_the_dropped_attributes_instead(self):
        """Or the 65-point buy, typed into the sheet, with nothing rolled."""
        player = self.create_user("buying-player")
        _data, character = self._import(player)
        update = f"/tarmar/characters/{character.pk}/update-field/"

        for name, score in zip(
            (name for name, _short in CORE_ATTRIBUTE_FIELDS),
            (15, 12, 10, 10, 10, 8),
            strict=True,
        ):
            with self.subTest(attribute=name):
                response = self.post_json(update, {"field": name, "value": score})
                self.assertEqual(response.status_code, 200, response.content)

        character.refresh_from_db()
        self.assertEqual(character.strength, 15)
        self.assertEqual(
            self.post_json(update, {"field": "strength", "value": 18}).status_code, 400
        )

    def test_a_value_no_dice_can_make_is_still_refused(self):
        """Cleared or not, the file is held to the bounds: an impossible
        roll is a bad file, not a value to drop quietly."""
        self.login_as(self.create_user("impossible-player"))
        upload = SimpleUploadedFile(
            "character.json",
            json.dumps(_build_payload(overrides={"literacy_roll": 19})).encode("utf-8"),
            content_type="application/json",
        )

        response = self.client.post("/tarmar/characters/import/", {"file": upload})

        self.assertEqual(response.status_code, 400)

    def test_a_game_masters_import_is_verbatim(self):
        stated = _build_payload(overrides={**self.STATED, "strength": 22})["data"]
        data, character = self._import(self.create_dm("verbatim-dm"), {"strength": 22})

        for name in EXPORTABLE_FIELDS:
            with self.subTest(field=name):
                self.assertEqual(
                    getattr(character, name), stated.get(name, getattr(character, name))
                )
        self.assertEqual(character.equipment, self.STATED["equipment"])
        self.assertEqual(character.import_notice, "")
        self.assertEqual(data[IMPORT_NOTICE_KEY], "")

    def test_a_game_masters_ruling_survives_his_import(self):
        _data, character = self._import(
            self.create_dm("ruling-dm"),
            {"location": "Consult GM", "literacy_roll": None, "literacy_ruling": False},
        )

        self.assertIs(character.literacy_ruling, False)

    def test_the_notice_is_not_part_of_the_export(self):
        _data, character = self._import(self.create_user("export-player"))

        payload = json.loads(generate_character_json(character))

        self.assertNotIn("import_notice", payload["data"])


class ImportNoticeOnTheSheetTest(BaseTestCase):
    """The notice stays on the sheet until somebody dismisses it (#744)."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("notice-owner")
        self.character = Character.objects.create(
            user=self.owner,
            name="Dropped",
            import_notice="Imported by a player, so the rolled values were not kept.",
        )
        self.edit_url = f"/tarmar/characters/{self.character.pk}/"
        self.dismiss_url = (
            f"/tarmar/characters/{self.character.pk}/dismiss-import-notice/"
        )

    def test_the_sheet_shows_it_with_a_way_to_dismiss_it(self):
        self.login_as(self.owner)

        page = self.client.get(self.edit_url).content.decode()

        self.assertIn('id="import-notice"', page)
        self.assertIn("Imported by a player, so the rolled values were not kept.", page)
        self.assertIn('id="dismiss-import-notice-btn"', page)

    def test_dismissing_it_clears_it_for_good(self):
        self.login_as(self.owner)

        response = self.post_json(self.dismiss_url, {})

        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        self.assertEqual(self.character.import_notice, "")
        self.assertFalse(self.character.is_played, "reading a notice is not play")
        self.assertNotIn(
            'id="import-notice"', self.client.get(self.edit_url).content.decode()
        )

    def test_a_stranger_cannot_dismiss_it(self):
        self.login_as(self.create_user("notice-stranger"))

        response = self.post_json(self.dismiss_url, {})

        self.assertEqual(response.status_code, 403)
        self.character.refresh_from_db()
        self.assertNotEqual(self.character.import_notice, "")
