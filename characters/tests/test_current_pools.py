"""Current Fatigue and Body on the character's record (issues #750, #751).

The defaults in ``common.test_helpers`` give Fatigue 59 (CON 15 + WIS 13 +
INT 10 + STR 14 + roll 7) and Body 40 (2/3 of 59, rounded up), so the
injury table's rows sit at: Fatigue -1 at 29, Body -1 at 20, -2 from 5,
unconscious at 0, survival saves from -30 (Fatigue) and -20 (Body),
penalized past -59 and -40.
"""

import json

from character_export.export_utils import (
    EXPORTABLE_FIELDS,
    generate_character_json,
    generate_character_markdown,
)
from characters import services
from characters.import_service import (
    PLAYER_KEPT_FIELDS,
    held_to_player,
    save_imported_character,
    validate_import_payload,
)
from characters.services import CharacterChangeError, FightReport
from common.test_helpers import BaseTestCase, create_character, mark_gate_passed


class CurrentPoolsModelTest(BaseTestCase):
    """What the record says about a character's pools now."""

    def setUp(self):
        super().setUp()
        self.character = create_character(self.create_user())

    def condition_at(self, fatigue: int, body: int):
        maximum_fatigue, maximum_body = self.character.pools()
        self.character.fatigue_damage = maximum_fatigue - fatigue
        self.character.body_damage = maximum_body - body
        condition = self.character.condition
        assert condition is not None
        return condition

    def test_a_new_character_is_unhurt(self):
        self.assertEqual(self.character.current_fatigue, 59)
        self.assertEqual(self.character.current_body, 40)
        condition = self.character.condition
        assert condition is not None
        self.assertTrue(condition.unhurt)
        self.assertEqual(condition.summary, "Fatigue 59/59, Body 40/40 (unhurt)")

    def test_damage_lowers_the_current_pool_and_not_the_maximum(self):
        self.character.fatigue_damage = 12
        self.assertEqual(self.character.fatigue, 59)
        self.assertEqual(self.character.current_fatigue, 47)
        self.assertEqual(self.condition_at(47, 40).state, "hurt, no penalty yet")

    def test_the_bands_follow_the_injury_table(self):
        self.assertEqual(self.condition_at(29, 40).roll_penalty, -1)
        self.assertEqual(self.condition_at(29, 40).state, "-1 to all rolls")
        self.assertEqual(self.condition_at(30, 40).roll_penalty, 0)
        self.assertEqual(self.condition_at(59, 5).state, "-2 to all rolls")
        self.assertEqual(self.condition_at(1, 40).roll_penalty, -2)

    def test_either_pool_at_zero_is_unconscious(self):
        for fatigue, body in ((0, 40), (59, 0), (-3, 12)):
            condition = self.condition_at(fatigue, body)
            self.assertFalse(condition.conscious)
            self.assertEqual(condition.state, "unconscious")
            self.assertEqual(condition.roll_penalty, 0)

    def test_survival_saves_start_at_minus_half_and_the_worse_pool_governs(self):
        self.assertIsNone(self.condition_at(-29, 40).survival_save_penalty)
        condition = self.condition_at(-30, 40)
        self.assertEqual(condition.survival_save_penalty, 0)
        self.assertEqual(
            condition.state, "unconscious; survival save each turn (3d6 ≤ CON 15)"
        )
        # Body 45 past its -40 line is -5; Fatigue at -30 is unpenalized.
        condition = self.condition_at(-30, -45)
        self.assertEqual(condition.survival_save_penalty, -5)
        self.assertEqual(
            condition.state, "unconscious; survival save each turn (3d6 ≤ CON 15 -5)"
        )

    def test_the_dead_read_dead_whatever_their_pools(self):
        self.character.is_alive = False
        condition = self.condition_at(-40, -45)
        self.assertEqual(condition.state, "dead")
        self.assertIsNone(condition.survival_save_penalty)
        self.assertFalse(condition.conscious)

    def test_an_incomplete_character_has_no_condition(self):
        self.character.fatigue_roll = None
        self.assertIsNone(self.character.current_fatigue)
        self.assertIsNone(self.character.condition)


class RecordFightTest(BaseTestCase):
    """``services.record_fight``: the write-back contract's receiving side."""

    def setUp(self):
        super().setUp()
        self.character = create_character(self.create_user(), name="Aldwin")

    def report(self, **fields) -> FightReport:
        return FightReport(
            character_id=self.character.pk, fight="Wolf Road", turn=3, **fields
        )

    def test_a_turns_losses_go_on_the_record_with_no_history_line(self):
        result = services.record_fight(self.report(fatigue_lost=30, body_lost=2))
        assert result is not None
        self.assertEqual(result.note, "")
        self.character.refresh_from_db()
        self.assertEqual(self.character.current_fatigue, 29)
        self.assertEqual(self.character.current_body, 38)
        # One line per fight, not per turn (the history keeps fifty).
        self.assertEqual(self.character.history, [])
        # A fight is not the owner playing the character.
        self.assertFalse(self.character.is_played)

    def test_the_end_of_the_fight_writes_one_line_from_where_it_started(self):
        services.record_fight(self.report(fatigue_lost=20))
        result = services.record_fight(
            self.report(
                fatigue_lost=10,
                body_lost=2,
                closes=services.FIGHT_ENDED,
                started_fatigue=59,
                started_body=40,
            )
        )
        assert result is not None
        self.character.refresh_from_db()
        entry = self.character.history[-1]
        self.assertEqual(entry["field"], "current pools")
        self.assertEqual(entry["old"], "Fatigue 59/59, Body 40/40")
        self.assertEqual(
            entry["new"],
            "Fatigue 29/59, Body 38/40 (-1 to all rolls), "
            "at the end of Wolf Road, turn 3",
        )
        self.assertEqual(entry["by"], "system")
        self.assertEqual(len(self.character.history), 1)

    def test_a_fight_that_ends_where_it_began_writes_no_line(self):
        self.assertIsNone(
            services.record_fight(
                self.report(
                    closes=services.FIGHT_ENDED, started_fatigue=59, started_body=40
                )
            )
        )
        self.character.refresh_from_db()
        self.assertEqual(self.character.history, [])

    def test_losses_accumulate_and_a_restart_never_passes_full(self):
        services.record_fight(self.report(fatigue_lost=10))
        services.record_fight(self.report(fatigue_lost=5))
        self.character.refresh_from_db()
        self.assertEqual(self.character.fatigue_damage, 15)
        services.record_fight(
            self.report(fatigue_lost=-40, closes=services.FIGHT_RESTARTED)
        )
        self.character.refresh_from_db()
        self.assertEqual(self.character.fatigue_damage, 0)
        entry = self.character.history[-1]
        self.assertEqual(entry["old"], "Fatigue 44/59, Body 40/40")
        self.assertIn("Wolf Road restarted", entry["new"])

    def test_a_death_is_recorded_as_mark_dead_records_it(self):
        services.record_fight(
            self.report(body_lost=70, died=True, started_fatigue=59, started_body=40)
        )
        self.character.refresh_from_db()
        self.assertFalse(self.character.is_alive)
        self.assertEqual(self.character.death_cause, "Killed in battle — Wolf Road")
        fields = [entry["field"] for entry in self.character.history]
        self.assertEqual(fields, ["current pools", "is_alive"])
        self.assertIn("(dead), killed in Wolf Road", self.character.history[0]["new"])
        self.assertEqual(
            (self.character.history[-1]["old"], self.character.history[-1]["new"]),
            ("True", "False"),
        )

    def test_the_dead_take_nothing_and_are_never_revived(self):
        services.record_fight(self.report(died=True))
        self.character.refresh_from_db()
        history = list(self.character.history)
        self.assertIsNone(services.record_fight(self.report(fatigue_lost=-5)))
        self.assertIsNone(
            services.record_fight(self.report(fatigue_lost=9, closes="ended"))
        )
        self.character.refresh_from_db()
        self.assertFalse(self.character.is_alive)
        self.assertEqual(self.character.fatigue_damage, 0)
        self.assertEqual(self.character.history, history)

    def test_spent_mana_stays_spent_and_a_restart_hands_it_back(self):
        self.character.free_spell_points = 6
        self.character.save()
        services.record_fight(self.report(mana_spent=4))
        self.character.refresh_from_db()
        self.assertEqual(
            (self.character.mana_spent, self.character.current_mana), (4, 2)
        )
        services.record_fight(
            self.report(mana_spent=-4, closes=services.FIGHT_RESTARTED)
        )
        self.character.refresh_from_db()
        self.assertEqual(self.character.mana_spent, 0)
        services.record_fight(self.report(mana_spent=3))
        result = services.record_fight(
            self.report(
                closes=services.FIGHT_ENDED,
                started_fatigue=59,
                started_body=40,
                started_mana=6,
            )
        )
        assert result is not None
        self.assertIn("Mana 3/6", result.note)
        self.character.refresh_from_db()
        self.assertEqual(
            self.character.history[-1]["old"], "Fatigue 59/59, Body 40/40; Mana 6/6"
        )

    def test_a_report_that_changes_nothing_writes_nothing(self):
        self.assertIsNone(services.record_fight(self.report()))
        self.character.refresh_from_db()
        self.assertEqual(self.character.history, [])


class SetCurrentPoolsTest(BaseTestCase):
    """A Game Master sets current pools; a player may not (issue #751)."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("owner")
        self.dm = self.create_dm()
        self.character = create_character(self.owner, name="Aldwin")
        self.url = f"/tarmar/characters/{self.character.pk}/set-pools/"

    def test_the_service_refuses_a_player_and_takes_a_game_master(self):
        with self.assertRaises(CharacterChangeError) as caught:
            services.set_current_pools(self.character.pk, fatigue=10)
        self.assertEqual(str(caught.exception), services.POOLS_GAME_MASTER_ONLY)
        result = services.set_current_pools(
            self.character.pk, fatigue=10, is_game_master=True, actor=self.dm
        )
        self.assertEqual(result.character.current_fatigue, 10)
        self.assertEqual(result.character.current_body, 40)
        entry = result.character.history[-1]
        self.assertEqual(
            (entry["field"], entry["old"], entry["by"]),
            ("current pools", "Fatigue 59/59, Body 40/40", self.dm.username),
        )

    def test_the_rules_hold_for_a_game_master_too(self):
        cases: tuple[tuple[dict[str, int], str], ...] = (
            ({"fatigue": 60}, "above its maximum of 59"),
            ({"body": 41}, "above its maximum of 40"),
            ({}, "Give a current Fatigue, Body or Mana"),
            ({"body": -40000}, "further down than a record holds"),
        )
        for values, message in cases:
            with self.subTest(values=values):
                with self.assertRaises(CharacterChangeError) as caught:
                    services.set_current_pools(
                        self.character.pk,
                        fatigue=values.get("fatigue"),
                        body=values.get("body"),
                        is_game_master=True,
                    )
                self.assertIn(message, str(caught.exception))
        incomplete = create_character(self.owner, fatigue_roll=None)
        with self.assertRaises(CharacterChangeError):
            services.set_current_pools(incomplete.pk, fatigue=1, is_game_master=True)

    def test_a_game_master_restores_mana_within_the_pool(self):
        self.character.free_spell_points = 5
        self.character.mana_spent = 5
        self.character.save()
        result = services.set_current_pools(
            self.character.pk, mana=3, is_game_master=True
        )
        self.assertEqual(result.character.current_mana, 3)
        self.assertIn("Mana 3/5", result.note)
        self.login_as(self.dm)
        response = self.post_json(self.url, {"mana": 2})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["current_mana"], 2)
        self.assertIn("Mana 0/5", result.character.history[-1]["old"])
        for value, message in ((6, "above its maximum of 5"), (-1, "below 0")):
            with self.subTest(value=value):
                with self.assertRaises(CharacterChangeError) as caught:
                    services.set_current_pools(
                        self.character.pk, mana=value, is_game_master=True
                    )
                self.assertIn(message, str(caught.exception))
        with self.assertRaises(CharacterChangeError):
            services.set_current_pools(self.character.pk, mana=1)
        # Mana alone may be set before the attributes are known.
        incomplete = create_character(
            self.owner, fatigue_roll=None, free_spell_points=2, mana_spent=2
        )
        result = services.set_current_pools(incomplete.pk, mana=2, is_game_master=True)
        self.assertEqual(result.character.current_mana, 2)

    def test_the_owner_is_refused_at_the_endpoint(self):
        self.login_as(self.owner)
        response = self.post_json(self.url, {"fatigue": 59, "body": 40})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], services.POOLS_GAME_MASTER_ONLY)
        self.character.refresh_from_db()
        self.assertEqual(self.character.history, [])

    def test_someone_elses_player_is_refused_before_the_rules(self):
        self.login_as(self.create_user("stranger"))
        response = self.post_json(self.url, {"fatigue": 1})
        self.assertEqual(response.status_code, 403)

    def test_a_game_master_sets_them_and_gets_the_readout_back(self):
        self.login_as(self.dm)
        response = self.post_json(self.url, {"fatigue": "-3", "body": ""})
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertIn("Fatigue -3/59, Body 40/40 (unconscious)", data["note"])
        self.assertIn('id="current-fatigue"', data["injury_thresholds_html"])
        self.assertIn('value="-3"', data["injury_thresholds_html"])
        self.assertIn("Unconscious", data["injury_thresholds_html"])
        self.character.refresh_from_db()
        self.assertEqual(self.character.fatigue_damage, 62)
        self.assertEqual(self.character.body_damage, 0)

    def test_a_body_that_is_not_json_is_refused(self):
        self.login_as(self.dm)
        response = self.client.post(
            self.url, "not json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_a_value_that_is_not_a_number_is_refused(self):
        self.login_as(self.dm)
        response = self.post_json(self.url, {"fatigue": "lots"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Fatigue must be a whole number")


class SheetShowsCurrentPoolsTest(BaseTestCase):
    """The sheet prints the pools now, with inputs for a Game Master only."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("owner")
        self.character = create_character(
            self.owner, name="Aldwin", fatigue_damage=30, body_damage=0
        )
        self.url = f"/tarmar/characters/{self.character.pk}/"

    def test_the_owner_reads_them(self):
        self.login_as(self.owner)
        response = self.client.get(self.url)
        self.assertContains(response, '<strong id="current-fatigue">29</strong> / 59')
        self.assertContains(response, '<strong id="current-body">40</strong> / 40')
        self.assertContains(response, "-1 to all rolls")
        self.assertNotContains(response, 'class="current-pool-input"')
        # The line that said the sheet does not track damage is gone with the gap.
        self.assertNotContains(response, "Damage is not tracked here")

    def test_a_caster_reads_their_mana(self):
        Character = type(self.character)
        Character.objects.filter(pk=self.character.pk).update(
            free_spell_points=4, mana_spent=1
        )
        self.login_as(self.owner)
        response = self.client.get(self.url)
        self.assertContains(response, '<strong id="current-mana">3</strong> / 4')
        self.login_as(self.create_dm())
        mark_gate_passed(self.client)
        response = self.client.get(self.url)
        self.assertContains(response, 'id="current-mana" data-pool="mana"')

    def test_a_game_master_gets_inputs(self):
        self.login_as(self.create_dm())
        response = self.client.get(self.url)
        self.assertContains(response, 'id="current-fatigue" data-pool="fatigue"')
        self.assertContains(response, 'value="29" max="59"')
        self.assertContains(response, 'id="current-body" data-pool="body"')


class ExportCarriesCurrentPoolsTest(BaseTestCase):
    """The export shows the pools now, and a round trip keeps the wounds."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.character = create_character(
            self.user, name="Aldwin", fatigue_damage=30, body_damage=5
        )

    def test_markdown_prints_them(self):
        markdown = generate_character_markdown(self.character)
        self.assertIn(
            "- **Now:** Fatigue 29/59, Body 35/40 (-1 to all rolls)\n", markdown
        )
        self.character.free_spell_points = 5
        self.character.mana_spent = 2
        self.assertIn(
            "- **Now:** Fatigue 29/59, Body 35/40 (-1 to all rolls); Mana 3/5",
            generate_character_markdown(self.character),
        )
        self.assertNotIn("current damage is not tracked", markdown)

    def test_json_round_trip_keeps_the_wounds(self):
        self.assertIn("fatigue_damage", EXPORTABLE_FIELDS)
        self.assertIn("body_damage", EXPORTABLE_FIELDS)
        self.assertIn("mana_spent", EXPORTABLE_FIELDS)
        payload = json.loads(generate_character_json(self.character))
        self.assertEqual(payload["data"]["fatigue_damage"], 30)
        copy = save_imported_character(self.user, validate_import_payload(payload))
        self.assertEqual((copy.fatigue_damage, copy.body_damage), (30, 5))

    def test_a_players_import_arrives_unhurt_and_a_game_masters_keeps_it(self):
        # The pool columns are the Game Master's (FIELD_RULES), so the
        # player-import allowlist built from that table drops them (#744).
        for name in ("fatigue_damage", "body_damage", "mana_spent"):
            self.assertNotIn(name, PLAYER_KEPT_FIELDS)
        self.character.mana_spent = 2
        self.character.save()
        imported = validate_import_payload(
            json.loads(generate_character_json(self.character))
        )
        players = held_to_player(imported, is_game_master=False).fields
        self.assertEqual(
            (players["fatigue_damage"], players["body_damage"], players["mana_spent"]),
            (0, 0, 0),
        )
        game_masters = held_to_player(imported, is_game_master=True).fields
        self.assertEqual(
            (
                game_masters["fatigue_damage"],
                game_masters["body_damage"],
                game_masters["mana_spent"],
            ),
            (30, 5, 2),
        )

    def test_import_holds_the_damage_to_the_column(self):
        payload = json.loads(generate_character_json(self.character))
        payload["data"]["fatigue_damage"] = -1
        with self.assertRaises(Exception) as caught:
            validate_import_payload(payload)
        self.assertIn("positive", str(caught.exception))


class PoolFieldRulesTest(BaseTestCase):
    """Who may set the pool columns, in the sheet's one table (#748, #751)."""

    def test_they_are_game_master_values_and_never_a_field_edit(self):
        from characters.models import CHARACTER_FIELDS

        for name in ("fatigue_damage", "body_damage", "mana_spent"):
            with self.subTest(name=name):
                rule = services.FIELD_RULES[name]
                self.assertIs(rule.author, services.FieldAuthor.GAME_MASTER)
                self.assertEqual(rule.reason, services.POOLS_GAME_MASTER_ONLY)
                self.assertNotIn(name, CHARACTER_FIELDS)
                self.assertNotIn(name, services.update_fields_for(is_game_master=True))
