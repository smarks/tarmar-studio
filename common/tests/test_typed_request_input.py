"""Values a signed-in user can type answer 400, never 500 (issue #767).

Every JSON endpoint reads its body through ``common.helpers._parse_json_body``
and every number through ``whole_number`` or ``database_integer``. The
endpoint tests below send each exact input the 2026-09-28 rescan found
answering 500, plus the siblings the sweep for #767 turned up, and pin the
status each one must answer with instead.
"""

import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, SimpleTestCase
from django.urls import reverse

from battle.services import create_battle
from characters.models import Character
from common.helpers import (
    MAX_DATABASE_INTEGER,
    _parse_json_body,
    database_integer,
    parse_json_text,
    whole_number,
)
from common.test_helpers import BaseTestCase, mark_gate_passed
from gm.models import GMReviewRequest, GMSession

COMPLETE_ATTRIBUTES = {
    "strength": 12,
    "dexterity": 12,
    "intelligence": 12,
    "wisdom": 12,
    "constitution": 12,
    "charisma": 12,
    "appearance_roll": "3",
    "height_roll": "3",
    "weight_roll": "3",
    "fatigue_roll": 3,
}

DEEPLY_NESTED = ("[" * 100000).encode()
INVALID_UTF8 = b'{"field": "\xff"}'


class ParseJsonTextTest(SimpleTestCase):
    """The one ``json.loads`` for text a user sent: every refusal a ValueError."""

    def test_reads_a_json_object(self):
        self.assertEqual(parse_json_text(b'{"a": 1}'), {"a": 1})

    def test_malformed_json_is_a_value_error(self):
        with self.assertRaises(ValueError):
            parse_json_text(b"{not json")

    def test_invalid_utf8_is_a_value_error(self):
        with self.assertRaises(ValueError):
            parse_json_text(INVALID_UTF8)

    def test_deep_nesting_is_a_value_error_not_a_recursion_error(self):
        with self.assertRaises(ValueError) as caught:
            parse_json_text(DEEPLY_NESTED)
        self.assertNotIsInstance(caught.exception, RecursionError)

    def test_an_integer_past_the_digit_limit_is_a_value_error(self):
        with self.assertRaises(ValueError):
            parse_json_text(("1" * 5000).encode())


class ParseJsonBodyTest(SimpleTestCase):
    """``_parse_json_body`` answers 400 "Invalid JSON" to every bad body."""

    def body(self, raw: bytes):
        request = RequestFactory().post("/", data=raw, content_type="application/json")
        return _parse_json_body(request)

    def test_an_object_comes_back_with_no_error(self):
        self.assertEqual(self.body(b'{"field": "name"}'), ({"field": "name"}, None))

    def test_each_bad_body_is_a_400(self):
        for label, raw in [
            ("malformed", b"{"),
            ("invalid utf-8", INVALID_UTF8),
            ("deep nesting", DEEPLY_NESTED),
            ("not an object", b"[1, 2]"),
        ]:
            with self.subTest(label):
                data, error = self.body(raw)
                self.assertEqual(data, {})
                assert error is not None
                self.assertEqual(error.status_code, 400)
                self.assertEqual(json.loads(error.content), {"error": "Invalid JSON"})


class WholeNumberTest(SimpleTestCase):
    """``int()`` with OverflowError and TypeError folded into ValueError."""

    def test_keeps_what_int_accepts(self):
        self.assertEqual(whole_number(3), 3)
        self.assertEqual(whole_number("-4"), -4)
        self.assertEqual(whole_number(" 7 "), 7)
        self.assertEqual(whole_number(2.9), 2)

    def test_refuses_with_value_error_only(self):
        for label, value in [
            ("infinity (JSON 1e999)", float("inf")),
            ("negative infinity", float("-inf")),
            ("nan", float("nan")),
            ("list", ["x"]),
            ("dict", {"a": 1}),
            ("none", None),
            ("superscript two", "²"),
            ("text", "three"),
        ]:
            with self.subTest(label), self.assertRaises(ValueError):
                whole_number(value)


class DatabaseIntegerTest(SimpleTestCase):
    """A primary key or index off a request: an int or ASCII digits, in range."""

    def test_accepts_ints_and_ascii_digit_strings(self):
        self.assertEqual(database_integer(5), 5)
        self.assertEqual(database_integer("12"), 12)
        self.assertEqual(database_integer(0), 0)
        self.assertEqual(database_integer(MAX_DATABASE_INTEGER), MAX_DATABASE_INTEGER)

    def test_refuses_what_sqlite_cannot_hold_or_int_cannot_read(self):
        for label, value in [
            ("superscript two", "²"),
            ("arabic-indic three", "٣"),
            ("past sqlite's integer", MAX_DATABASE_INTEGER + 1),
            ("digits past sqlite's integer", "9" * 30),
            ("digits past python's limit", "9" * 5000),
            ("below sqlite's integer", -MAX_DATABASE_INTEGER - 2),
            ("bool", True),
            ("float", 1.0),
            ("infinity", float("inf")),
            ("list", ["1"]),
            ("signed text", "-3"),
            ("padded text", " 3"),
        ]:
            with self.subTest(label), self.assertRaises(ValueError):
                database_integer(value)


class TypedInputEndpointTest(BaseTestCase):
    """Each input the rescan found answering 500, sent exactly as it was."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user(username="player")
        self.dm = self.create_dm()
        self.client = Client(raise_request_exception=False)
        mark_gate_passed(self.client)
        self.login_as(self.player)
        self.character = Character.objects.create(
            user=self.player,
            name="Aldwin",
            equipment=[{"item_id": "dagger", "quantity": 1}],
            **COMPLETE_ATTRIBUTES,
        )
        self.base = f"/tarmar/characters/{self.character.pk}"

    def switch_to(self, user) -> None:
        """Log in as ``user``; logging a different user in flushes the session,
        so the passphrase gate's mark is planted again."""
        self.login_as(user)
        mark_gate_passed(self.client)

    def post(self, path: str, body: object):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        return self.client.post(path, raw, content_type="application/json")

    def assert_answers(self, cases, expected_status: int = 400):
        for label, path, body in cases:
            with self.subTest(label):
                response = self.post(path, body)
                self.assertEqual(response.status_code, expected_status, label)

    def test_update_field(self):
        self.assert_answers(
            [
                (
                    "update-field field=list",
                    f"{self.base}/update-field/",
                    {"field": ["x"], "value": 1},
                ),
                ("update-field bad utf8", f"{self.base}/update-field/", INVALID_UTF8),
                (
                    "update-field deep nesting",
                    f"{self.base}/update-field/",
                    DEEPLY_NESTED,
                ),
                # A sibling the sweep found: clean_field_value's int().
                (
                    "update-field strength 1e999",
                    f"{self.base}/update-field/",
                    b'{"field": "strength", "value": 1e999}',
                ),
                (
                    "update-field wealth list",
                    f"{self.base}/update-field/",
                    {"field": "wealth_level", "value": ["Rich"]},
                ),
            ]
        )
        self.character.refresh_from_db()
        self.assertEqual(self.character.strength, 12)

    def test_equipment(self):
        self.assert_answers(
            [
                (
                    "equipment add quantity 1e999",
                    f"{self.base}/equipment/add/",
                    b'{"item_id": "dagger", "quantity": 1e999}',
                ),
                (
                    "equipment update quantity 1e999",
                    f"{self.base}/equipment/update/",
                    b'{"index": 0, "quantity": 1e999}',
                ),
                (
                    "equipment update index 1e999",
                    f"{self.base}/equipment/update/",
                    b'{"index": 1e999, "notes": "x"}',
                ),
                (
                    "equipment update to_hit 1e999",
                    f"{self.base}/equipment/update/",
                    b'{"index": 0, "to_hit_mod": 1e999}',
                ),
                (
                    "bulk remove indices 1e999",
                    f"{self.base}/equipment/bulk-remove/",
                    b'{"indices": [1e999]}',
                ),
                (
                    "equipment add item_id list",
                    f"{self.base}/equipment/add/",
                    {"item_id": ["dagger"]},
                ),
            ]
        )
        self.character.refresh_from_db()
        self.assertEqual(
            self.character.equipment, [{"item_id": "dagger", "quantity": 1}]
        )

    def test_custom_armour_stops_as_a_game_master(self):
        """A custom item is a Game Master's to add (#752), so the probe's
        ``stops`` has to reach the parser through one. A ``stops`` or
        ``dex_penalty`` that is not a whole number has always been stored as
        0 rather than refused; infinity now takes that same path instead of
        raising."""
        self.switch_to(self.dm)
        response = self.post(
            f"{self.base}/equipment/add/",
            b'{"is_custom": true, "name": "Plate", "category": "armor",'
            b' "weight": 1, "stops": 1e999, "dex_penalty": -1e999}',
        )
        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        plate = self.character.equipment[-1]
        self.assertEqual(
            (plate["name"], plate["stops"], plate["dex_penalty"]), ("Plate", 0, 0)
        )

    def test_attack(self):
        self.assert_answers(
            [
                (
                    "attack shield list",
                    f"{self.base}/attack/",
                    {"weapon_index": 0, "shield_item_id": ["x"]},
                ),
                (
                    "attack armour tier list",
                    f"{self.base}/attack/",
                    {"weapon_index": 0, "armour_tier": ["x"]},
                ),
                (
                    "attack situational 1e999",
                    f"{self.base}/attack/",
                    b'{"weapon_index": 0, "situational": 1e999}',
                ),
                (
                    "attack defender_stops 1e999",
                    f"{self.base}/attack/",
                    b'{"weapon_index": 0, "defender_stops": 1e999}',
                ),
                (
                    "attack weapon_index 1e999",
                    f"{self.base}/attack/",
                    b'{"weapon_index": 1e999}',
                ),
            ]
        )

    def test_attack_ignores_a_skill_name_since_the_record_names_the_skill(self):
        """The rescan's ``skill_name: ["x"]`` case went away with #753: the
        weapon's skill is the record's to say, and the body's is not read."""
        response = self.post(
            f"{self.base}/attack/", {"weapon_index": 0, "skill_name": ["x"]}
        )
        self.assertEqual(response.status_code, 200)

    def test_bulk_delete(self):
        for label, ids in [
            ("bulk delete ids superscript", ["²"]),
            ("bulk delete id past sqlite's integer", ["9" * 30]),
            ("bulk delete int past sqlite's integer", [10**30]),
        ]:
            with self.subTest(label):
                response = self.post("/tarmar/characters/bulk-delete/", {"ids": ids})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"], "No character IDs provided")
        self.character.refresh_from_db()
        self.assertFalse(self.character.is_deleted)

    def test_bulk_delete_still_deletes_the_ids_it_can_read(self):
        response = self.post(
            "/tarmar/characters/bulk-delete/", {"ids": [str(self.character.pk), "²"]}
        )
        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        self.assertTrue(self.character.is_deleted)

    def test_mechanics_numbers(self):
        self.switch_to(self.dm)
        self.assert_answers(
            [
                (
                    "skill check difficulty 1e999",
                    f"{self.base}/skill-check/",
                    b'{"skill": "stealth", "difficulty": 1e999}',
                ),
                (
                    "learn spell points 1e999",
                    f"{self.base}/learn-spell/",
                    b'{"spell": "fire_missile", "points": 1e999}',
                ),
                (
                    "grant mana amount 1e999",
                    f"{self.base}/grant-mana/",
                    b'{"amount": 1e999}',
                ),
            ]
        )

    def test_character_import_file(self):
        for label, content in [
            ("import deep nesting", DEEPLY_NESTED),
            ("import bad utf8", INVALID_UTF8),
        ]:
            with self.subTest(label):
                upload = SimpleUploadedFile("c.json", content, "application/json")
                response = self.client.post(
                    reverse("characters:import"), {"file": upload}
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"], "File is not valid JSON")


class TypedInputBattleTest(BaseTestCase):
    """The battle act and rename bodies (PLAUSIBLE in the issue, now pinned)."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.client = Client(raise_request_exception=False)
        mark_gate_passed(self.client)
        self.login_as(self.dm)
        self.battle = create_battle(self.dm, name="Probe", count=2, seed=1)

    def post(self, name: str, body: bytes):
        return self.client.post(
            reverse(name, args=[self.battle.pk]),
            body,
            content_type="application/json",
        )

    def test_act(self):
        for label, body in [
            ("combatant_id 1e999", b'{"combatant_id": 1e999, "turn": 1}'),
            ("turn 1e999", b'{"combatant_id": 1, "turn": 1e999}'),
            (
                "target_id 1e999",
                b'{"combatant_id": 1, "turn": 1, "target_id": 1e999}',
            ),
            ("combatant_id list", b'{"combatant_id": [1], "turn": 1}'),
            ("bad utf8", INVALID_UTF8),
            ("deep nesting", DEEPLY_NESTED),
        ]:
            with self.subTest(label):
                self.assertEqual(self.post("battle:act", body).status_code, 400)

    def test_rename(self):
        for label, body in [
            ("combatant_id 1e999", b'{"combatant_id": 1e999, "name": "Bob"}'),
            ("name list", b'{"combatant_id": 1, "name": ["Bob"]}'),
        ]:
            with self.subTest(label):
                self.assertEqual(self.post("battle:rename", body).status_code, 400)


class TypedInputPartyAndReviewTest(BaseTestCase):
    """The party autosave's own ``json.loads`` and the reviews poll's ids."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.client = Client(raise_request_exception=False)
        mark_gate_passed(self.client)
        self.login_as(self.dm)

    def test_party_update_field(self):
        from parties.models import Party

        party = Party.objects.create(owner=self.dm, name="Company")
        path = reverse("parties:update_field", args=[party.pk])
        for label, body in [
            ("bad utf8", INVALID_UTF8),
            ("deep nesting", DEEPLY_NESTED),
        ]:
            with self.subTest(label):
                response = self.client.post(path, body, content_type="application/json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json(),
                    {"success": False, "error": "The request was not valid JSON"},
                )

    def test_review_statuses_leave_out_ids_that_are_not_numbers(self):
        session = GMSession.objects.create(user=self.dm, name="S")
        review = GMReviewRequest.objects.create(
            session=session, requested_by=self.dm, snapshot={}
        )
        for label, ids in [
            ("superscript", "²"),
            ("past sqlite's integer", "9" * 30),
            ("mixed", f"{review.pk},²,{'9' * 30}"),
        ]:
            with self.subTest(label):
                response = self.client.get(reverse("gm:review_statuses"), {"ids": ids})
                self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"reviews": {str(review.pk): review.status}})


class TypedInputSiblingTest(BaseTestCase):
    """Siblings the sweep for #767 found beyond the rescan's fourteen."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.client = Client(raise_request_exception=False)
        mark_gate_passed(self.client)
        self.login_as(self.dm)
        self.character = Character.objects.create(
            user=self.dm, name="Brannoc", **COMPLETE_ATTRIBUTES
        )
        self.base = f"/tarmar/characters/{self.character.pk}"

    def post(self, path: str, body: bytes):
        return self.client.post(path, body, content_type="application/json")

    def test_game_master_character_endpoints(self):
        for label, path, body in [
            (
                "custom item category list",
                f"{self.base}/equipment/add/",
                b'{"is_custom": true, "name": "Rock", "category": ["gear"]}',
            ),
            ("mark dead cause list", f"{self.base}/mark-dead/", b'{"cause": ["x"]}'),
        ]:
            with self.subTest(label):
                self.assertEqual(self.post(path, body).status_code, 400)
        self.character.refresh_from_db()
        self.assertTrue(self.character.is_alive)
        self.assertEqual(self.character.equipment, [])

    def test_import_file_attribute_of_infinity(self):
        content = b'{"name": "Infinite", "strength": 1e999}'
        upload = SimpleUploadedFile("c.json", content, "application/json")
        response = self.client.post(reverse("characters:import"), {"file": upload})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Character.objects.filter(name="Infinite").exists())

    def test_battle_setup_update_field_list(self):
        self.client.post(reverse("battle:setup-create"))
        from battle.models import BattleDraft

        draft = BattleDraft.objects.get(created_by=self.dm)
        response = self.post(
            reverse("battle:setup-update", args=[draft.pk]),
            b'{"field": ["name"], "value": "x"}',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Unknown setup field")

    def test_battle_act_with_an_id_past_sqlite_integer(self):
        battle = create_battle(self.dm, name="Probe", count=2, seed=1)
        body = json.dumps({"combatant_id": 10**30, "turn": 1}).encode()
        response = self.post(reverse("battle:act", args=[battle.pk]), body)
        self.assertEqual(response.status_code, 400)

    def test_gm_session_and_character_ids_of_infinity(self):
        session = GMSession.objects.create(user=self.dm, name="Table")
        for label, name, body in [
            ("stream session", "gm:stream", b'{"session_id": 1e999, "message": "hi"}'),
            ("save session", "gm:save", b'{"session_id": 1e999, "name": "x"}'),
            ("delete session", "gm:delete", b'{"session_id": 1e999}'),
            ("attach character", "gm:characters_attach", b'{"character_id": 1e999}'),
            (
                "detach character",
                "gm:characters_detach",
                f'{{"session_id": {session.pk}, "character_id": 1e999}}'.encode(),
            ),
            ("fight session", "gm:fight", b'{"session_id": 1e999, "foes": "wolf"}'),
            ("review session", "gm:reviews", b'{"session_id": 1e999}'),
        ]:
            with self.subTest(label):
                response = self.post(reverse(name), body)
                self.assertEqual(response.status_code, 400, label)

    def test_profile_update_field(self):
        path = reverse("profile_update_field")
        for label, body in [
            ("field list", b'{"field": ["is_dm"], "value": true}'),
            ("bad utf8", INVALID_UTF8),
            ("deep nesting", DEEPLY_NESTED),
        ]:
            with self.subTest(label):
                self.assertEqual(self.post(path, body).status_code, 400)
        self.dm.refresh_from_db()
        self.assertTrue(self.dm.is_dm)
