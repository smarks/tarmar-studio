"""A dead character's record takes no change the rules govern (issue #932).

Adding years was refused for a dead character and the sheet hid the other
spending controls, but the server checked nothing else: a hand-made request
still spent free points on a spell or a skill for the dead. The rule, from
#403/#404's ruling on years, now holds on every path: a player and a Game
Master alike are refused until a Game Master brings the character back, and
the refusal says so. The guard is in ``services.changing``; these tests hold
every service and every sheet endpoint to it, and fail when a new one is
added without a decision about the dead.
"""

import ast
import inspect
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from django.conf import settings
from django.urls import URLPattern, reverse
from tarmar_engine.spells import SPELLS

from characters import services
from characters.models import Character
from characters.services import CharacterChangeError, DeadCharacterWrite
from characters.urls import urlpatterns
from common.test_helpers import BaseTestCase, create_character

#: The words every refusal of the dead carries.
DEAD_WORDS = "is dead; a Game Master must bring them back to life first"

#: A dead character as these tests make it: points, skills, gear, mana and
#: coin to spend, so nothing but the death stands between a request and a
#: change.
DEAD_VALUES: dict[str, Any] = {
    "name": "Wren",
    "is_alive": False,
    "death_cause": "Fell from the tower",
    "free_skill_points": 5,
    "free_spell_points": 3,
    "skills": {"stealth": 1, "climbing": 1},
    "equipment": [
        {"item_id": "dagger", "name": "Dagger", "quantity": 1, "category": "weapon"}
    ],
    "starting_coin": 100,
    "body_damage": 2,
    "location": "Village",
}


def snapshot(character_id: int) -> dict[str, Any]:
    """Every stored value of the row, to show a refusal wrote nothing."""
    return Character.objects.filter(pk=character_id).values().get()


Call = Callable[[int, bool], object]

#: Every service that writes a saved character, called for a dead one; each
#: must refuse it with the shared words, a Game Master as much as a player.
REFUSED_SERVICES: dict[str, Call] = {
    "set_current_pools": lambda pk, gm: services.set_current_pools(
        pk, fatigue=1, is_game_master=gm
    ),
    "set_attribute": lambda pk, gm: services.set_attribute(
        pk, "strength", 12, is_game_master=gm
    ),
    "roll_fatigue": lambda pk, gm: services.roll_fatigue(pk, is_game_master=gm),
    "roll_on_sheet": lambda pk, gm: services.roll_on_sheet(
        pk, "appearance", is_game_master=gm
    ),
    "roll_background": lambda pk, gm: services.roll_background(pk, is_game_master=gm),
    "roll_literacy": lambda pk, gm: services.roll_literacy(pk, is_game_master=gm),
    "rule_literacy": lambda pk, gm: services.rule_literacy(pk, True, is_game_master=gm),
    "roll_survival_save": lambda pk, gm: services.roll_survival_save(
        pk, is_game_master=gm
    ),
    "record_attributes": lambda pk, gm: services.record_attributes(
        pk, {"strength": 12}, is_game_master=gm
    ),
    "roll_attributes": lambda pk, gm: services.roll_attributes(pk, is_game_master=gm),
    "award_xp": lambda pk, gm: services.award_xp(pk, 100, is_game_master=gm),
    "add_equipment": lambda pk, gm: services.add_equipment(pk, "rope", 1),
    "allocate_skill_points": lambda pk, gm: services.allocate_skill_points(
        pk, "stealth", 1, is_game_master=gm
    ),
    "add_experience_years": lambda pk, gm: services.add_experience_years(
        pk, "merchant", 1, is_game_master=gm
    ),
    "update_field": lambda pk, gm: services.update_field(
        pk, "wealth_level", "Rich", is_game_master=gm
    ),
    "set_background": lambda pk, gm: services.set_background(
        pk, {"wealth_level": "Rich"}, is_game_master=gm
    ),
}

#: The services that may touch the dead, and why (``DeadCharacterWrite``).
#: ``changing`` itself is the guard, and is tested on its own.
EXEMPT_SERVICES: frozenset[str] = frozenset(
    {"revive", "mark_dead", "rename", "dismiss_import_notice", "changing"}
)


#: Every ``dead=`` argument outside the tests: (module, top-level function,
#: the value passed). A new exemption from the guard changes this list, so it
#: is a decision someone reviews, not a keyword slipped into a call.
PINNED_EXEMPTIONS: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("characters/services.py", "record_fight", "DeadCharacterWrite.FIGHT"),
        # Passes on what its callers name.
        ("characters/services.py", "_single", "dead"),
        (
            "characters/services.py",
            "revive",
            "DeadCharacterWrite.DEATH_AND_REVIVAL",
        ),
        (
            "characters/services.py",
            "mark_dead",
            "DeadCharacterWrite.DEATH_AND_REVIVAL",
        ),
        (
            "characters/services.py",
            "dismiss_import_notice",
            "DeadCharacterWrite.NO_RULE",
        ),
        ("characters/services.py", "rename", "DeadCharacterWrite.NO_RULE"),
        (
            "characters/services.py",
            "update_field",
            "DeadCharacterWrite.NO_RULE if field_name in DEAD_EDITABLE_FIELDS "
            "else None",
        ),
        (
            "characters/views/characters.py",
            "character_mark_dead",
            "services.DeadCharacterWrite.DEATH_AND_REVIVAL",
        ),
        (
            "characters/views/characters.py",
            "character_revive",
            "services.DeadCharacterWrite.DEATH_AND_REVIVAL",
        ),
        ("gm/withdraw.py", "withdraw", "DeadCharacterWrite.SESSION"),
    }
)


def _source_files() -> Iterator[Path]:
    """The project's own modules, not its tests, migrations or packages."""
    root = Path(settings.BASE_DIR)
    for path in root.rglob("*.py"):
        parts = path.relative_to(root).parts
        if (
            parts[0].startswith(".")
            or parts[0] in {"node_modules", "scripts"}
            or "migrations" in parts
            or "tests" in parts
            or path.name.startswith("test")
        ):
            continue
        yield path


def dead_arguments() -> set[tuple[str, str, str]]:
    """Every ``dead=`` keyword in a call, where it is and what it passes."""
    root = Path(settings.BASE_DIR)
    found = set()
    for path in _source_files():
        tree = ast.parse(path.read_text())
        for top in tree.body:
            for node in ast.walk(top):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg == "dead":
                        found.add(
                            (
                                path.relative_to(root).as_posix(),
                                getattr(top, "name", "<module>"),
                                ast.unparse(keyword.value),
                            )
                        )
    return found


class EveryExemptionIsPinnedTest(BaseTestCase):
    def test_the_exemptions_are_the_reviewed_list(self) -> None:
        self.assertEqual(dead_arguments(), PINNED_EXEMPTIONS)

    def test_only_a_named_reason_lifts_the_guard(self) -> None:
        character = create_character(self.create_user("wren-owner"), **DEAD_VALUES)
        for not_a_reason in ("fight", True, 1):
            with self.subTest(dead=not_a_reason):
                with self.assertRaisesMessage(CharacterChangeError, DEAD_WORDS):
                    with services.changing(character.pk, dead=not_a_reason):  # type: ignore[arg-type]
                        pass


class EveryServiceRefusesTheDeadTest(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.create_user("wren-owner")
        self.character = create_character(self.owner, **DEAD_VALUES)

    def test_every_write_service_is_decided(self) -> None:
        """A new service taking a character id must be added to a table."""
        writers = {
            name
            for name, function in inspect.getmembers(services, inspect.isfunction)
            if function.__module__ == services.__name__
            and not name.startswith("_")
            and next(iter(inspect.signature(function).parameters), "") == "character_id"
        }
        self.assertEqual(writers, set(REFUSED_SERVICES) | EXEMPT_SERVICES)

    def test_each_refuses_a_player_and_a_game_master_and_writes_nothing(
        self,
    ) -> None:
        before = snapshot(self.character.pk)
        for name, call in REFUSED_SERVICES.items():
            for is_game_master in (False, True):
                with self.subTest(service=name, is_game_master=is_game_master):
                    with self.assertRaisesMessage(CharacterChangeError, DEAD_WORDS):
                        call(self.character.pk, is_game_master)
                    self.assertEqual(snapshot(self.character.pk), before)

    def test_the_refusal_names_its_kind(self) -> None:
        with self.assertRaises(CharacterChangeError) as refused:
            services.allocate_skill_points(self.character.pk, "stealth")
        self.assertEqual(refused.exception.reason, services.REFUSAL_DEAD)
        self.assertEqual(str(refused.exception), f"Wren {DEAD_WORDS}")

    def test_changing_refuses_the_dead_unless_told_why(self) -> None:
        with self.assertRaisesMessage(CharacterChangeError, DEAD_WORDS):
            with services.changing(self.character.pk) as change:
                change.character.free_skill_points = 0
        with services.changing(
            self.character.pk, dead=DeadCharacterWrite.NO_RULE
        ) as change:
            change.note("note", "", "written")
        self.character.refresh_from_db()
        self.assertEqual(self.character.history[-1]["new"], "written")

    def test_a_game_master_revives_and_then_the_rules_apply_again(self) -> None:
        services.revive(self.character.pk)
        result = services.allocate_skill_points(self.character.pk, "stealth")
        self.assertEqual(result.character.skills["stealth"], 2)

    def test_marking_the_dead_dead_again_keeps_its_own_refusal(self) -> None:
        with self.assertRaisesMessage(CharacterChangeError, "already dead"):
            services.mark_dead(self.character.pk, "again", is_game_master=True)

    def test_the_name_and_the_import_notice_need_no_revival(self) -> None:
        services.rename(self.character.pk, "Wren the Fallen")
        services.update_field(self.character.pk, "name", "Wren the Lost")
        Character.objects.filter(pk=self.character.pk).update(import_notice="left out")
        services.dismiss_import_notice(self.character.pk)
        self.character.refresh_from_db()
        self.assertEqual(self.character.name, "Wren the Lost")
        self.assertEqual(self.character.import_notice, "")
        self.assertFalse(self.character.is_alive)

    def test_a_fight_report_for_the_dead_is_taken_as_nothing(self) -> None:
        """Not refused: a refusal would roll back the board's committed turn."""
        before = snapshot(self.character.pk)
        report = services.FightReport(
            character_id=self.character.pk, fight="Skirmish", turn=3, body_lost=1
        )
        self.assertIsNone(services.record_fight(report))
        self.assertEqual(snapshot(self.character.pk), before)

    def test_a_years_run_priced_in_memory_is_refused_too(self) -> None:
        with self.assertRaisesMessage(CharacterChangeError, DEAD_WORDS):
            services.apply_experience_years(
                self.character, "merchant", 1, is_game_master=True
            )


#: Every sheet endpoint that writes a character, with a body that would
#: otherwise reach the record. ``kwargs`` beyond ``pk`` go to ``reverse``.
REFUSED_ENDPOINTS: dict[str, dict[str, Any]] = {
    "roll_fatigue": {},
    "roll_literacy": {},
    "roll": {"kwargs": {"kind": "appearance"}},
    "rule_literacy": {"body": {"literate": True}},
    "set_pools": {"body": {"fatigue": 1}},
    "update_field": {"body": {"field": "wealth_level", "value": "Rich"}},
    "add_years": {"body": {"track": "merchant", "years": 1}},
    "allocate_skill": {"body": {"skill": "stealth"}},
    "rename_skill": {"body": {"old_name": "stealth", "new_name": "hiding"}},
    "record_weapon_skill": {"body": {"skill": "Weapon (dagger)", "item_id": "dagger"}},
    "skill_check": {"body": {"skill": "stealth"}},
    "roll_survival_save": {},
    "move_coin": {"body": {"amount": "5"}},
    "learn_spell": {"body": {"spell": next(iter(SPELLS))}},
    "grant_mana": {"body": {"amount": 2}},
    "choose_school": {"body": {"school": next(iter(SPELLS.values())).school}},
    "equipment_add": {"body": {"item_id": "rope", "quantity": 1}},
    "equipment_update": {"body": {"index": 0, "quantity": 2}},
    "equipment_remove": {"body": {"index": 0}},
    "equipment_bulk_remove": {"body": {"indices": [0]}},
    "equipment_toggle_pack": {},
}

#: Writes that may touch the dead, each tested below, and the one read.
EXEMPT_ENDPOINTS: frozenset[str] = frozenset(
    {
        # A Game Master's own (mark-dead refuses the already dead itself).
        "mark_dead",
        "revive",
        # Removing a character, and its sharing: no rule governs either.
        "delete",
        "restore",
        "set_visibility",
        "dismiss_import_notice",
        # The attack calculator refuses the dead with its own words (#840)
        # and writes only the combat log.
        "attack",
        # The sheet itself, a read.
        "edit",
    }
)


class EverySheetEndpointRefusesTheDeadTest(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.create_user("wren-owner")
        self.game_master = self.create_dm("table-gm")
        self.character = create_character(self.owner, **DEAD_VALUES)

    def url(self, name: str, kwargs: dict[str, Any] | None = None) -> str:
        return reverse(
            f"characters:{name}", kwargs={"pk": self.character.pk, **(kwargs or {})}
        )

    def test_every_character_endpoint_is_decided(self) -> None:
        """A new endpoint on a character must be added to a table."""
        on_a_character = {
            pattern.name
            for pattern in urlpatterns
            if isinstance(pattern, URLPattern) and "<int:pk>" in str(pattern.pattern)
        }
        self.assertEqual(on_a_character, set(REFUSED_ENDPOINTS) | EXEMPT_ENDPOINTS)

    def test_each_refuses_a_player_and_a_game_master_in_words(self) -> None:
        before = snapshot(self.character.pk)
        for user in (self.owner, self.game_master):
            self.logout()
            self.login_as(user)
            for name, request in REFUSED_ENDPOINTS.items():
                with self.subTest(endpoint=name, user=user.username):
                    response = self.post_json(
                        self.url(name, request.get("kwargs")), request.get("body", {})
                    )
                    self.assertEqual(response.status_code, 400, response.content)
                    # The sheet prints a 400's error where the control is, as
                    # it prints every other refusal.
                    self.assertEqual(response.json()["error"], f"Wren {DEAD_WORDS}")
                    self.assertEqual(snapshot(self.character.pk), before)

    def test_the_name_stays_editable(self) -> None:
        self.login_as(self.owner)
        response = self.post_json(
            self.url("update_field"), {"field": "name", "value": "Wren the Lost"}
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.character.refresh_from_db()
        self.assertEqual(self.character.name, "Wren the Lost")

    def test_a_game_master_revives_and_a_player_may_not(self) -> None:
        self.login_as(self.owner)
        self.assertEqual(self.post_json(self.url("revive"), {}).status_code, 403)
        self.logout()
        self.login_as(self.game_master)
        self.assertEqual(self.post_json(self.url("revive"), {}).status_code, 200)
        self.character.refresh_from_db()
        self.assertTrue(self.character.is_alive)

    def test_mark_dead_answers_the_dead_with_its_own_refusal(self) -> None:
        self.login_as(self.game_master)
        response = self.post_json(self.url("mark_dead"), {"cause": "Again"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("already dead", response.json()["error"])

    def test_the_attack_calculator_refuses_the_dead(self) -> None:
        self.login_as(self.owner)
        response = self.post_json(self.url("attack"), {"weapon_index": 0})
        self.assertEqual(response.status_code, 400)
        self.assertIn("is dead", response.json()["error"])

    def test_sharing_dismissing_and_removing_need_no_revival(self) -> None:
        self.login_as(self.owner)
        Character.objects.filter(pk=self.character.pk).update(import_notice="left out")
        for name, body in (
            ("set_visibility", {"is_public": True}),
            ("dismiss_import_notice", {}),
        ):
            with self.subTest(endpoint=name):
                response = self.post_json(self.url(name), body)
                self.assertEqual(response.status_code, 200, response.content)
        self.assertLess(self.client.post(self.url("delete")).status_code, 400)
        self.assertLess(self.client.post(self.url("restore")).status_code, 400)
        self.character.refresh_from_db()
        self.assertTrue(self.character.is_public)
        self.assertEqual(self.character.import_notice, "")
        self.assertFalse(self.character.is_deleted)
