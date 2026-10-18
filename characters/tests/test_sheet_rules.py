"""The sheet's rules batch of 2026-09-29: renames, XP, grants, literacy,
aged scores, the modifier table, prior experience in play, chosen skill
parts, the Consult GM rulings and a character's people.

Issues #768, #769, #770, #772, #773, #801, #803, #806, #808, #810.
"""

from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ValidationError
from django.urls import reverse

from battle.models import Battle, BattleCombatant
from characters import services
from characters.models import Character
from characters.services import (
    FIELD_RULES,
    CharacterChangeError,
    FieldAuthor,
    apply_experience_years,
    apply_skill_rename,
    apply_xp_change,
    character_in_play,
    record_entry_into_play,
)
from characters.skill_checks import governing_attribute, skill_check
from characters.skills import (
    calculate_attribute_modifier,
    calculate_skill_points_from_xp,
    glossary_skill,
    specialised_skill_for_granted,
    split_specialised_skill,
)
from characters.tests.rules_fixtures import seated_skill_rules
from common.test_helpers import BaseTestCase, create_character
from gm import roster
from gm.models import GMSession
from gm.turn_record import TurnRecorder

PUBLIC_RULES = Path(settings.BASE_DIR) / "reference/content/public-rules/characters"
GLOSSARY = PUBLIC_RULES / "prior-experience/skills-glossary.md"


def flat(text: str) -> str:
    """Markdown with its line breaks folded, so a phrase can span them."""
    return " ".join(text.split())


class RenameTest(BaseTestCase):
    """#768: a rename never changes which rule a skill falls under."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("rename-player")
        self.character = create_character(
            self.player,
            location="Village",
            origin_skill="street smarts",
            skills={"street smarts": 1, "coins": 15, "maker skill": 3, "knowledge": 6},
        )
        self.url = reverse("characters:rename_skill", args=[self.character.pk])

    def rename(self, old_name: str, new_name: str):
        return self.post_json(self.url, {"old_name": old_name, "new_name": new_name})

    def test_a_player_cannot_move_points_to_another_skill(self):
        self.login_as(self.player)
        for old_name, new_name in (
            ("street smarts", "broadsword"),  # the rescan's evidence
            ("coins", "Knowledge Lore"),
            ("coins", "Arcane"),  # into the double cost
            ("maker skill", "knowledge (history)"),  # another glossary skill
        ):
            with self.subTest(new_name):
                response = self.rename(old_name, new_name)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("only a Game Master", response.json()["error"])
        self.character.refresh_from_db()
        self.assertEqual(self.character.skills["coins"], 15)
        self.assertEqual(self.character.skills["street smarts"], 1)

    def test_a_player_names_the_maker_skills_craft_once(self):
        self.login_as(self.player)
        self.assertEqual(
            self.rename("maker skill", "Maker Skill (Smith)").status_code, 200
        )
        self.character.refresh_from_db()
        self.assertEqual(self.character.skills["maker skill (smith)"], 3)
        # Moving the chosen craft afterwards is the Game Master's (as #753).
        moved = self.rename("maker skill (smith)", "Maker Skill (Tailor)")
        self.assertEqual(moved.status_code, 400)

    def test_focusing_knowledge_is_the_game_masters(self):
        # skills glossary: "the GM may let you focus a field".
        self.login_as(self.player)
        self.assertEqual(
            self.rename("knowledge", "Knowledge (History)").status_code, 400
        )
        self.logout()
        self.login_as(self.create_dm("focus-gm"))
        self.assertEqual(
            self.rename("knowledge", "Knowledge (History)").status_code, 200
        )

    def test_the_village_choice_joins_a_survival_already_held(self):
        Character.objects.filter(pk=self.character.pk).update(
            skills={"street smarts": 1, "survival": 2}
        )
        self.login_as(self.player)
        response = self.rename("street smarts", "survival")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("skills_html", response.json())
        self.character.refresh_from_db()
        self.assertEqual(self.character.skills, {"survival": 3})
        self.assertEqual(self.character.origin_skill, "survival")

    def test_a_village_player_chooses_survival_for_the_origin_point(self):
        self.login_as(self.player)
        response = self.rename("Street Smarts", "Survival")
        self.assertEqual(response.status_code, 200, response.content)
        self.character.refresh_from_db()
        self.assertEqual(self.character.skills["survival"], 1)
        self.assertEqual(self.character.origin_skill, "survival")

    def test_the_village_choice_moves_the_origin_point_and_nothing_else(self):
        Character.objects.filter(pk=self.character.pk).update(
            skills={"street smarts": 4}
        )
        self.login_as(self.player)
        self.assertEqual(self.rename("street smarts", "survival").status_code, 400)
        self.character.refresh_from_db()
        self.assertEqual(self.character.skills, {"street smarts": 4})

    def test_the_village_choice_is_only_a_villagers(self):
        Character.objects.filter(pk=self.character.pk).update(location="City")
        self.login_as(self.player)
        self.assertEqual(self.rename("street smarts", "survival").status_code, 400)

    def test_a_game_master_moves_points_and_the_history_says_so(self):
        self.login_as(self.create_dm("rename-gm"))
        response = self.rename("coins", "broadsword")
        self.assertEqual(response.status_code, 200, response.content)
        self.character.refresh_from_db()
        self.assertEqual(self.character.skills["broadsword"], 15)
        self.assertIn("the Game Master's call", self.character.history[-1]["new"])

    def test_the_service_holds_the_same_rule(self):
        character = Character(skills={"coins": 3}, location="City")
        with self.assertRaises(CharacterChangeError):
            apply_skill_rename(character, "coins", "arcane", is_game_master=False)
        recorded, points = apply_skill_rename(
            character, "coins", "arcane", is_game_master=True
        )
        self.assertEqual((points, character.skills), (3, {"arcane": 3}))
        self.assertIn("Game Master", recorded)


class XpGrantTest(BaseTestCase):
    """#769: raising XP grants what the table earns, through the free points."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("xp-player")
        self.character = create_character(self.player, total_xp=0, free_skill_points=0)
        self.game_master = self.create_dm("xp-gm")

    def update(self, value: int):
        return services.update_field(
            self.character.pk,
            "total_xp",
            value,
            actor=self.game_master,
            is_game_master=True,
        )

    def test_the_rescans_three_thousand_xp_earns_two_points(self):
        # prior-experience.md: "at 3,000 XP you would have earned 2 skill points"
        result = self.update(3000)
        self.assertEqual(result.character.free_skill_points, 2)
        self.assertEqual(result.character.level, 2)
        self.assertIn("+2 free skill point(s)", result.note)
        self.assertEqual(result.character.history[-1]["field"], "free_skill_points")

    def test_level_seven_earns_the_tables_twelve(self):
        result = self.update(64000)
        self.assertEqual(result.character.level, 7)
        self.assertEqual(
            result.character.free_skill_points, calculate_skill_points_from_xp(64000)
        )

    def test_the_sheet_endpoint_grants_them(self):
        self.login_as(self.game_master)
        response = self.post_json(
            reverse("characters:update_field", args=[self.character.pk]),
            {"field": "total_xp", "value": 3000},
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.character.refresh_from_db()
        self.assertEqual(self.character.free_skill_points, 2)

    def test_a_player_still_cannot_set_xp(self):
        with self.assertRaises(CharacterChangeError):
            services.update_field(
                self.character.pk, "total_xp", 3000, actor=self.player
            )

    def test_lowering_xp_takes_back_unspent_points_only(self):
        self.update(3000)
        Character.objects.filter(pk=self.character.pk).update(
            free_skill_points=1, skills={"coins": 1}
        )
        result = self.update(0)
        self.assertEqual(result.character.free_skill_points, 0)
        self.assertEqual(result.character.skills, {"coins": 1})
        self.assertIn("1 already spent stay where they are", result.note)

    def test_lowering_then_raising_never_makes_points(self):
        character = Character(total_xp=0, free_skill_points=0)
        apply_xp_change(character, 30000)
        earned = character.free_skill_points
        character.free_skill_points = 0  # all spent
        apply_xp_change(character, 3000)
        apply_xp_change(character, 30000)
        self.assertEqual(character.free_skill_points, 0)
        self.assertEqual(character.skill_points_owed, 0)
        self.assertEqual(earned, calculate_skill_points_from_xp(30000))

    def test_lowering_within_one_bracket(self):
        character = Character(total_xp=0, free_skill_points=0)
        apply_xp_change(character, 7000)  # 2 + 1 + 1
        apply_xp_change(character, 4100)  # 2 + 1 + 0
        self.assertEqual(character.free_skill_points, 3)

    def test_the_history_stores_the_number(self):
        result = self.update(3000)
        line = result.character.history[-1]
        self.assertEqual((line["old"], line["new"]), ("0", "2"))

    def test_the_sheet_response_carries_the_points(self):
        self.login_as(self.game_master)
        response = self.post_json(
            reverse("characters:update_field", args=[self.character.pk]),
            {"field": "total_xp", "value": 3000},
        )
        data = response.json()
        self.assertEqual(data["free_skill_points"], 2)
        self.assertIn("skills_html", data)

    def test_the_run_path_and_the_edit_path_agree(self):
        character = Character(total_xp=1500, free_skill_points=0)
        apply_xp_change(character, 5000)
        self.assertEqual(
            character.free_skill_points,
            calculate_skill_points_from_xp(5000) - calculate_skill_points_from_xp(1500),
        )


class GameMasterGrantOnTheSheetTest(BaseTestCase):
    """#770: the sheet's allocate grants a Game Master a point past those earned."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("grant-player")
        self.character = create_character(self.player, free_skill_points=0)
        self.url = reverse("characters:allocate_skill", args=[self.character.pk])

    def test_a_game_master_grants_a_point_on_the_sheet(self):
        self.login_as(self.create_dm("grant-gm"))
        response = self.post_json(self.url, {"skill": "survival"})
        self.assertEqual(response.status_code, 200, response.content)
        self.character.refresh_from_db()
        self.assertEqual(self.character.skills.get("survival"), 1)
        self.assertEqual(self.character.free_skill_points, 0)
        self.assertIn("granted by the Game Master", self.character.history[-1]["new"])

    def test_a_player_is_still_refused(self):
        self.login_as(self.player)
        response = self.post_json(self.url, {"skill": "survival"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("No free skill points", response.json()["error"])


class LiteracyRulingTest(BaseTestCase):
    """#772: the check decides whether a character reads; the skill, how well."""

    def test_the_pages_say_the_ruling(self):
        location = flat((PUBLIC_RULES / "location.md").read_text())
        glossary = flat(GLOSSARY.read_text())
        self.assertIn("This is the check to be literate at all", location)
        self.assertIn("separate from the [Literacy skill]", location)
        # The page's own line stays; the house ruling stands beside it.
        self.assertIn("Without it, you cannot read at all", glossary)
        self.assertIn("House ruling (provisional) on Literacy", glossary)
        self.assertIn("*whether* you can read at all is the literacy check", glossary)
        self.assertIn("Spencer may overrule", glossary)

    def test_the_sheet_reads_the_check_not_the_skill(self):
        player = self.create_user("reader")
        literate = create_character(
            player, location="City", intelligence=12, literacy_roll=9, skills={}
        )
        self.assertTrue(literate.is_literate)
        illiterate = create_character(
            player,
            location="City",
            intelligence=8,
            literacy_roll=15,
            skills={"literacy": 6},
        )
        self.assertFalse(illiterate.is_literate)


class AgedScoresTest(BaseTestCase):
    """#773: aged scores in play, base scores for what creation decides."""

    def setUp(self):
        super().setUp()
        # aging.md: at 68, STR, DEX and CON -2, INT and WIS -1.
        self.character = create_character(
            self.create_user("aged"),
            strength=14,
            dexterity=14,
            intelligence=12,
            wisdom=12,
            constitution=14,
            charisma=10,
            fatigue_roll=7,
            weight_roll="3",
            location="City",
            age=68,
        )

    def test_each_rules_property_reads_the_score_the_ruling_names(self):
        character = self.character
        self.assertEqual(
            (character.effective_strength, character.effective_intelligence), (12, 11)
        )
        # Effective: the pools, Physical, the survival save, encumbrance, movement.
        self.assertEqual(character.fatigue, 12 + 11 + 11 + 12 + 7)
        self.assertEqual(character.physical, (12 + 12 + 12) // 2)
        thresholds = character.injury_thresholds
        encumbrance = character.encumbrance_thresholds
        assert thresholds is not None and encumbrance is not None
        self.assertEqual(thresholds["fatigue"]["survival_save_target"], 12)
        self.assertEqual(
            encumbrance,
            {"unencumbered": 12, "light": 18, "medium": 24, "heavy": 30},
        )
        self.assertEqual(character.movement_modifier, 0)
        # Base: the literacy check and weight, decided when the character is made.
        self.assertEqual(character.literacy_threshold, 12)
        self.assertEqual(character.weight_lbs, (10 + 14 // 2) * 14)

    def test_aging_md_states_the_ruling(self):
        aging = flat((PUBLIC_RULES / "prior-experience/aging.md").read_text())
        self.assertIn("Everywhere the attribute is used in play", aging)
        self.assertIn("which reads the base score", aging)
        self.assertIn("House ruling (provisional)", aging)


class ModifierTableTest(BaseTestCase):
    """#806: the table stops at 3 and 18, and so does the modifier."""

    def test_scores_past_the_table_take_the_nearest_row(self):
        self.enterContext(seated_skill_rules())  # the default canon's table
        self.assertEqual(
            [calculate_attribute_modifier(score) for score in (0, 1, 2, 3)], [-5] * 4
        )
        self.assertEqual(
            [calculate_attribute_modifier(score) for score in (18, 19, 25, 30)],
            [5] * 4,
        )

    def test_attributes_md_says_so(self):
        attributes = flat((PUBLIC_RULES / "attributes.md").read_text())
        self.assertIn(
            "a score below 3 takes −5 and a score above 18 takes +5", attributes
        )
        self.assertIn("House ruling (provisional)", attributes)


class PriorExperienceInPlayTest(BaseTestCase):
    """#803: a player adds prior-experience years before play only."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("years-player")
        self.character = create_character(self.player)

    def attached_and_played(self) -> GMSession:
        session = GMSession.objects.create(user=self.player, name="s")
        session.characters.add(self.character)
        record_entry_into_play(session_id=session.pk)
        self.character.refresh_from_db()
        return session

    def test_a_fresh_character_is_not_in_play(self):
        self.assertFalse(character_in_play(self.character))
        self.assertFalse(character_in_play(Character()))
        # is_played is not the signal: any owner's edit sets it.
        Character.objects.filter(pk=self.character.pk).update(is_played=True)
        self.character.refresh_from_db()
        self.assertFalse(character_in_play(self.character))

    def test_a_battle_start_puts_its_complete_seats_in_play(self):
        battle = Battle.objects.create(
            name="b", seed=1, created_by=self.create_dm("years-gm")
        )
        BattleCombatant.objects.create(
            battle=battle, character=self.character, display_name="x"
        )
        waiting = create_character(self.player, fatigue_roll=None)
        BattleCombatant.objects.create(
            battle=battle, character=waiting, display_name="y"
        )
        self.character.refresh_from_db()
        # A seat alone (a lobby claim) is not play; the start is.
        self.assertFalse(character_in_play(self.character))
        record_entry_into_play(battle_id=battle.pk)
        self.character.refresh_from_db()
        waiting.refresh_from_db()
        self.assertTrue(character_in_play(self.character))
        self.assertFalse(character_in_play(waiting))

    def test_a_battle_created_with_the_character_starts_it_in_play(self):
        from battle.services import create_battle

        game_master = self.create_dm("battle-gm")
        create_battle(
            game_master, name="fight", count=1, character_ids=(self.character.pk,)
        )
        self.character.refresh_from_db()
        self.assertTrue(character_in_play(self.character))

    def test_detaching_never_takes_it_out_of_play(self):
        session = self.attached_and_played()
        self.assertTrue(character_in_play(self.character))
        session.characters.remove(self.character)
        self.character.refresh_from_db()
        self.assertTrue(character_in_play(self.character))

    def test_a_character_still_being_built_is_not(self):
        session = GMSession.objects.create(user=self.player, name="built")
        built = roster.start_character(session, "the brother", name="Built")
        for _ in range(3):
            record_entry_into_play(session_id=session.pk)
        built.refresh_from_db()
        self.assertFalse(character_in_play(built))
        # Once complete, the next completed turn puts it in play.
        Character.objects.filter(pk=built.pk).update(
            strength=10,
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
            fatigue_roll=7,
        )
        record_entry_into_play(session_id=session.pk)
        built.refresh_from_db()
        self.assertTrue(character_in_play(built))

    def test_a_finished_turn_marks_it_and_a_failed_one_does_not(self):
        session = GMSession.objects.create(user=self.player, name="s")
        session.characters.add(self.character)

        def recorder() -> TurnRecorder:
            return TurnRecorder(
                user=self.player,
                session_id=session.pk,
                command="",
                mode="play",
                is_game_master=False,
                user_message="on we go",
                history_start=0,
                system_prompt="",
                model="test",
                options={},
            )

        recorder().finish("error", error="boom")
        recorder().finish("ok")  # narration or a question: never play
        self.character.refresh_from_db()
        self.assertFalse(character_in_play(self.character))
        recorder().finish("ok", procedures=[{"kind": "move_party", "ran": True}])
        self.character.refresh_from_db()
        self.assertTrue(character_in_play(self.character))

    def test_years_are_creation_and_moving_the_party_is_play(self):
        """The coordinator's ruling on #803: build over three turns, years on
        the fourth, and only the fifth turn, which moves the party, is play."""
        session = GMSession.objects.create(user=self.player, name="chat")
        built = roster.start_character(session, "the brother", name="Built")

        def turn(*kinds: str) -> None:
            TurnRecorder(
                user=self.player,
                session_id=session.pk,
                command="",
                mode="play",
                is_game_master=False,
                user_message="go on",
                history_start=0,
                system_prompt="",
                model="test",
                options={},
            ).finish("ok", procedures=[{"kind": kind, "ran": True} for kind in kinds])
            built.refresh_from_db()

        services.record_attributes(built.pk, {"all": 12}, actor=self.player)
        turn("record_attributes")
        # The Fatigue roll is made as a chat-built character is made (#859);
        # this turn is the one that reports it. The service call that stood
        # here rolled it a second time for a player, which #859 refuses.
        turn("roll_fatigue")
        turn("roll_background", "spend_skill_points")
        turn()  # "Which track should I take?": a question, not play
        # The year survives (#945): this test is about when play begins, and a
        # live 3d6 kills one year in 216, after which the last call below is
        # refused as dead (#932) before it is ever asked about play.
        with patch("characters.skills.roll_survival_dice", return_value=(6, 6, 6, 18)):
            services.add_experience_years(built.pk, "merchant", 1, actor=self.player)
        turn("play_years")
        self.assertFalse(character_in_play(built))
        # A refused campaign action is not play either.
        TurnRecorder(
            user=self.player,
            session_id=session.pk,
            command="",
            mode="aide",
            is_game_master=False,
            user_message="go on",
            history_start=0,
            system_prompt="",
            model="test",
            options={},
        ).finish("ok", procedures=[{"kind": "move_party", "ran": False}])
        built.refresh_from_db()
        self.assertFalse(character_in_play(built))

        turn("move_party")
        self.assertTrue(character_in_play(built))
        with self.assertRaisesMessage(CharacterChangeError, "entered play"):
            services.add_experience_years(built.pk, "merchant", 1, actor=self.player)

    def test_years_and_a_campaign_action_in_one_turn_apply_the_years_first(self):
        session = GMSession.objects.create(user=self.player, name="both")
        session.characters.add(self.character)
        services.add_experience_years(
            self.character.pk, "merchant", 1, actor=self.player
        )
        TurnRecorder(
            user=self.player,
            session_id=session.pk,
            command="",
            mode="aide",
            is_game_master=False,
            user_message="years, then off we go",
            history_start=0,
            system_prompt="",
            model="test",
            options={},
        ).finish(
            "ok",
            procedures=[
                {"kind": "play_years", "ran": True},
                {"kind": "advance_time", "ran": True},
            ],
        )
        self.character.refresh_from_db()
        self.assertEqual(self.character.experience_years, 1)
        self.assertTrue(character_in_play(self.character))

    def test_the_sheet_refuses_a_player_and_lets_a_game_master(self):
        self.attached_and_played()
        url = reverse("characters:add_years", args=[self.character.pk])
        self.login_as(self.player)
        refused = self.post_json(url, {"track": "merchant", "years": 1})
        self.assertEqual(refused.status_code, 400)
        self.assertIn("entered play", refused.json()["error"])
        self.logout()
        self.login_as(self.create_dm("years-gm-two"))
        allowed = self.post_json(url, {"track": "merchant", "years": 1})
        self.assertEqual(allowed.status_code, 200, allowed.content)
        self.character.refresh_from_db()
        self.assertIn("the Game Master's call", self.character.history[-1]["new"])

    def test_the_chat_path_holds_the_player_too(self):
        self.attached_and_played()
        with self.assertRaisesMessage(CharacterChangeError, "entered play"):
            services.add_experience_years(self.character.pk, "merchant", 1)
        character = Character.objects.get(pk=self.character.pk)
        note, _run = apply_experience_years(
            character, "merchant", 1, is_game_master=True
        )
        self.assertIn("the Game Master's call", note)


class ChosenPartTest(BaseTestCase):
    """#808: Maker Skill's craft and Knowledge's field live in the name."""

    def test_a_chosen_part_is_the_same_glossary_skill(self):
        self.assertEqual(glossary_skill("maker skill (smith)"), "maker skill")
        self.assertEqual(glossary_skill("knowledge (history)"), "knowledge")
        self.assertEqual(glossary_skill("weapon (broadsword)"), "weapon")
        self.assertEqual(governing_attribute("maker skill (smith)"), "intelligence")

    def test_a_chosen_part_may_hold_parentheses_of_its_own(self):
        # Issue #52: the normaliser keeps balanced parentheses, so the split
        # reads the chosen part the same way instead of calling it another skill.
        self.assertEqual(
            split_specialised_skill("knowledge (history (old))"),
            ("knowledge", "history (old)"),
        )
        self.assertEqual(glossary_skill("maker skill (smith (bronze))"), "maker skill")
        self.assertEqual(
            split_specialised_skill("knowledge (a) (b)"), ("knowledge (a) (b)", "")
        )
        for name in (
            "knowledge",
            "knowledge ()",
            "knowledge (a",
            "(knowledge)",
            "lore (history)",
            "knowledge(history)",
        ):
            with self.subTest(name=name):
                self.assertEqual(split_specialised_skill(name), (name, ""))

    def test_a_player_may_start_a_maker_skill_craft_with_parentheses(self):
        # Issue #52: the craft is read whole, so the Maker Skill's player
        # choice still covers it.
        character = create_character(
            self.create_user("nested-smith"), free_skill_points=1
        )
        services.apply_skill_points(
            character, "Maker Skill (Smith (Bronze))", 1, is_game_master=False
        )
        self.assertEqual(character.skills, {"maker skill (smith (bronze))": 1})

    def test_a_knowledge_field_with_parentheses_is_the_game_masters(self):
        player_character = create_character(
            self.create_user("nested-scholar"), free_skill_points=1
        )
        with self.assertRaisesMessage(
            services.CharacterChangeError, services.KNOWLEDGE_FIELD_REFUSED_REASON
        ):
            services.apply_skill_points(
                player_character,
                "Knowledge (History (Old))",
                1,
                is_game_master=False,
            )
        self.assertEqual(player_character.skills, {})

        master_character = create_character(
            self.create_user("nested-scholar-gm"), free_skill_points=1
        )
        services.apply_skill_points(
            master_character, "Knowledge (History (Old))", 1, is_game_master=True
        )
        self.assertEqual(master_character.skills, {"knowledge (history (old))": 1})

    def test_a_rolled_maker_skill_goes_to_the_chosen_craft(self):
        self.assertEqual(
            specialised_skill_for_granted({"maker skill (smith)": 3}, "maker skill"),
            "maker skill (smith)",
        )
        self.assertEqual(
            specialised_skill_for_granted({}, "maker skill"), "maker skill"
        )
        character = create_character(
            self.create_user("smith"), skills={"maker skill (smith)": 3}
        )
        character.apply_experience_run(
            {
                "years": [{"age": 17}],
                "xp_awarded": 0,
                "money_earned": 0,
                "skills_gained": {"maker skill": 1},
                "alive": True,
            },
            "craft",
            0,
        )
        self.assertEqual(character.skills, {"maker skill (smith)": 4})

    def test_a_check_on_the_bare_name_reads_the_chosen_craft(self):
        character = Character(
            skills={"maker skill (smith)": 6},
            strength=10,
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
        )
        self.assertEqual(skill_check(character, "maker skill").level, 3)

    def test_the_sheet_allocates_the_bare_name_to_the_chosen_craft(self):
        player = self.create_user("sheet-allocator")
        character = create_character(
            player, skills={"maker skill (smith)": 3}, free_skill_points=1
        )
        self.login_as(player)
        response = self.post_json(
            reverse("characters:allocate_skill", args=[character.pk]),
            {"skill": "Maker Skill"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual((data["skill"], data["points"]), ("maker skill (smith)", 4))
        self.assertIn('data-skill="maker skill (smith)"', data["row_html"])

    def test_a_game_masters_rows_keep_the_allocate_control_at_no_points(self):
        character = create_character(
            self.create_user("zero-points"), skills={"survival": 1}, free_skill_points=0
        )
        self.login_as(self.create_dm("zero-gm"))
        page = self.client.get(reverse("characters:edit", args=[character.pk]))
        self.assertContains(page, 'class="allocate-btn')
        self.logout()
        self.login_as(character.user)
        page = self.client.get(reverse("characters:edit", args=[character.pk]))
        self.assertNotContains(page, 'class="allocate-btn')

    def test_allocating_the_bare_name_goes_to_the_chosen_craft(self):
        character = create_character(
            self.create_user("allocator"),
            skills={"maker skill (smith)": 3},
            free_skill_points=1,
        )
        result = services.allocate_skill_points(character.pk, "Maker Skill")
        self.assertEqual(result.character.skills, {"maker skill (smith)": 4})

    def test_the_glossary_states_the_focus_ruling(self):
        glossary = flat(GLOSSARY.read_text())
        self.assertIn(
            "House ruling (provisional) on a focused Knowledge field", glossary
        )
        self.assertIn("is **+1** to checks in that field", glossary)

    def test_a_focused_knowledge_field_adds_one(self):
        # "the GM may let you focus a field for a larger bonus in it": +1
        # (coordinator's ruling under the standing rule).
        scores = dict(
            strength=10,
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
        )
        focused = skill_check(
            Character(skills={"knowledge (history)": 6}, **scores),
            "knowledge (history)",
            stated_total=7,
            is_game_master=True,
        )
        broad = skill_check(
            Character(skills={"knowledge": 6}, **scores),
            "knowledge",
            stated_total=7,
            is_game_master=True,
        )
        self.assertEqual(focused.total, broad.total + 1)
        self.assertIn("+1 focus", focused.line)

    def test_the_craft_track_pays_on_the_chosen_craft(self):
        character = Character(skills={"maker skill (smith)": 6})
        self.assertEqual(character.maker_skill_level, 3)


class ConsultGameMasterTest(BaseTestCase):
    """#801: the Game Master's word at a Consult GM row, with its provenance."""

    def test_the_rulings_are_the_game_masters(self):
        self.assertIs(FIELD_RULES["origin_ruling"].author, FieldAuthor.GAME_MASTER)
        self.assertIs(FIELD_RULES["is_dragonborn"].author, FieldAuthor.GAME_MASTER)
        self.assertIs(FIELD_RULES["people"].author, FieldAuthor.PLAYER)

    def test_the_summary_says_whose_word_it_is(self):
        character = Character(
            location="Consult GM",
            origin_ruling="raised in a Kanu enclave",
            wealth_level="Rich",
            starting_coin=900,
        )
        summary = character.background_summary
        self.assertIn(
            "origin Consult GM (raised in a Kanu enclave, the Game Master's ruling)",
            summary,
        )
        self.assertIn("900 starting coin (Game Master's figure)", summary)

    def test_moving_the_origin_withdraws_the_ruling(self):
        character = create_character(
            self.create_user("consult"),
            location="Consult GM",
            origin_ruling="the far isles",
        )
        result = services.update_field(
            character.pk,
            "location",
            "City",
            actor=self.create_dm("consult-gm"),
            is_game_master=True,
        )
        self.assertEqual(result.character.origin_ruling, "")
        self.assertIn("origin ruling (the far isles) withdrawn", result.note)


class OriginRollDropsTheRulingTest(BaseTestCase):
    def test_a_game_masters_origin_roll_withdraws_it(self):
        character = create_character(
            self.create_user("roller"),
            location="Consult GM",
            origin_ruling="the far isles",
        )
        game_master = self.create_dm("roller-gm")
        for _attempt in range(20):  # a roll of 100 is Consult GM again
            result = services.roll_on_sheet(
                character.pk, "origin", actor=game_master, is_game_master=True
            )
            if result.character.location != "Consult GM":
                break
        self.assertEqual(result.character.origin_ruling, "")


class PeopleTest(BaseTestCase):
    """#810: a people from the rules' choices, and the Game Master's secret."""

    def test_the_choices_are_the_playable_peoples(self):
        self.assertEqual(Character.clean_field_value("people", "Kanu"), "Kanu")
        with self.assertRaises(ValidationError):
            Character.clean_field_value("people", "Moonfolk")
        self.assertEqual(Character().people, "Human")

    def test_the_secret_is_a_yes_or_no(self):
        self.assertIs(Character.clean_field_value("is_dragonborn", "true"), True)
        self.assertIs(Character.clean_field_value("is_dragonborn", False), False)
        self.assertIs(Character.clean_field_value("is_dragonborn", "false"), False)
        self.assertIs(Character.clean_field_value("is_dragonborn", "False"), False)
        with self.assertRaises(ValidationError):
            Character.clean_field_value("is_dragonborn", "maybe")

    def test_a_player_cannot_set_it_and_is_not_told_what_it_is(self):
        self.assertFalse(
            services.can_set_field(Character(), "is_dragonborn", is_game_master=False)
        )
        self.assertNotIn("Dragonborn", services.field_refusal("is_dragonborn"))

    def test_the_secret_never_reaches_the_history_or_the_players_sheet(self):
        player = self.create_user("secret-player")
        character = create_character(player)
        services.update_field(
            character.pk,
            "is_dragonborn",
            True,
            actor=self.create_dm("secret-gm"),
            is_game_master=True,
        )
        character.refresh_from_db()
        self.assertTrue(character.is_dragonborn)
        self.assertNotIn("dragonborn", str(character.history).lower())
        self.login_as(player)
        page = self.client.get(reverse("characters:edit", args=[character.pk]))
        self.assertNotIn("dragonborn", page.content.decode().lower())

    def test_a_player_sets_their_people(self):
        player = self.create_user("people-player")
        character = create_character(player)
        result = services.update_field(character.pk, "people", "Kanu", actor=player)
        self.assertEqual(result.character.people, "Kanu")
