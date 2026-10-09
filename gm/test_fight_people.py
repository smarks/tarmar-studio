"""A foe named as a person fights as a person (issue #787).

``/fight a bandit`` built "bandit — beast — Kick" with 51 Fatigue: the
bestiary holds no people, so the foe was a stand-in varied from a catalog
beast, with a beast's attack and no weapon, and nothing on the board said
so. A person is now a generated fighter of the archetype its calling
suggests, named on the board as asked for; a stand-in beast says it is one.

Coordinator's ruling under the standing rule; Spencer may overrule.
"""

from django.test import SimpleTestCase

from battle.generation import ARCHETYPES
from battle.models import Battle
from battle.services import restart_battle
from bestiary.models import Beast
from characters.models import Character
from common.test_helpers import BaseTestCase, create_character

from .fight import FOE_TEAM, FoeRequest, resolve_foes, start_fight
from .foe_people import PERSON_ARCHETYPES, person_foe
from .models import GMSession


def person_archetype(name: str) -> str | None:
    """The archetype a foe named ``name`` is built as, or None for a creature."""
    found = person_foe(name)
    return found.archetype if found is not None else None


class PersonFoeTest(SimpleTestCase):
    """Which names are people, and what each fights as."""

    def test_callings_map_to_archetypes_generation_can_build(self):
        self.assertTrue(set(PERSON_ARCHETYPES.values()) <= set(ARCHETYPES))

    def test_a_person_is_read_from_the_head_noun_singular_or_plural(self):
        self.assertEqual(person_archetype("bandit"), "unarmoured duelist")
        self.assertEqual(person_archetype("scarred bandits"), "unarmoured duelist")
        self.assertEqual(person_archetype("City Guardsmen"), "armoured fighter")
        self.assertEqual(person_archetype("men-at-arms"), "armoured fighter")
        self.assertEqual(person_archetype("thieves"), "unarmoured duelist")
        self.assertEqual(person_archetype("hedge witches"), "wizard")
        self.assertEqual(person_archetype("crossbowmen"), "archer")
        self.assertEqual(person_archetype("armed people"), "brawler")
        self.assertEqual(person_archetype("spies"), "unarmoured duelist")

    def test_a_calling_after_a_creature_word_is_not_a_person(self):
        # Review of #787: "ant soldier" was a human fighter named "Ant
        # soldier", and "skeleton warrior" one named "Skeleton warrior". A
        # calling makes a person only after plain descriptions.
        for name in (
            "ant soldier",
            "giant ant soldiers",
            "ant scout",
            "skeleton warrior",
            "zombie soldier",
            "undead knight",
            "lizard men",
        ):
            with self.subTest(name=name):
                self.assertIsNone(person_foe(name))

    def test_plain_descriptions_and_callings_keep_a_person(self):
        for name, label in (
            ("the guard", "Guard"),
            ("city guard", "City guard"),
            ("two drunken thugs", "Drunken thug"),
            ("bandit captain", "Bandit captain"),
        ):
            with self.subTest(name=name):
                found = person_foe(name)
                self.assertEqual(getattr(found, "label", None), label)

    def test_a_creature_is_not_a_person(self):
        self.assertIsNone(person_archetype("guard dog"))
        self.assertIsNone(person_archetype("gray wolves"))
        self.assertIsNone(person_archetype("shadow hound"))
        self.assertIsNone(person_archetype(""))

    def test_the_label_is_the_name_asked_for_with_its_calling_singular(self):
        labels = {
            name: getattr(person_foe(name), "label", None)
            for name in ("scarred bandits", "men at arms", "Cultist")
        }
        self.assertEqual(
            labels,
            {
                "scarred bandits": "Scarred bandit",
                "men at arms": "Man-at-arms",
                "Cultist": "Cultist",
            },
        )


class PersonResolutionTest(BaseTestCase):
    """People are resolved before any loose catalog match."""

    def test_a_bandit_is_a_person_not_a_stand_in_beast(self):
        beasts_before = Beast.objects.count()
        (foe,) = resolve_foes([FoeRequest("bandits", 2)])
        self.assertIsNone(foe.beast)
        self.assertEqual(foe.archetype, "unarmoured duelist")
        self.assertEqual(foe.count, 2)
        self.assertEqual(foe.name, "Bandit")
        # No stand-in beast row was generated for it.
        self.assertEqual(Beast.objects.count(), beasts_before)

    def test_a_guard_is_a_person_though_the_catalog_has_a_guard_dog(self):
        (foe,) = resolve_foes([FoeRequest("guard", 1)])
        self.assertIsNone(foe.beast)
        self.assertEqual(foe.archetype, "armoured fighter")

    def test_ants_named_by_their_caste_are_the_catalog_ants_as_on_main(self):
        # What main matched them to, before and after this change.
        for name, beast_name in (
            ("ant soldier", "Titanic Ant Soldier"),
            ("giant ant soldiers", "Black Giant Ant Soldier"),
            ("ant scout", "Titanic Ant Scout"),
        ):
            with self.subTest(name=name):
                (foe,) = resolve_foes([FoeRequest(name, 1)])
                assert foe.beast is not None
                self.assertEqual(foe.beast.name, beast_name)
                self.assertEqual(foe.archetype, "")

    def test_undead_and_lizard_men_get_a_stand_in_beast_as_on_main(self):
        for name in (
            "skeleton warrior",
            "zombie soldier",
            "undead knight",
            "lizard men",
        ):
            with self.subTest(name=name):
                (foe,) = resolve_foes([FoeRequest(name, 1)])
                assert foe.beast is not None
                self.assertTrue(foe.beast.is_generated)
                self.assertEqual(foe.beast.name, name)
                self.assertIsNotNone(foe.generated_from)
                self.assertEqual(foe.archetype, "")

    def test_a_catalog_name_given_exactly_stays_the_catalog_beast(self):
        (foe,) = resolve_foes([FoeRequest("Headless Man", 1)])
        assert foe.beast is not None
        self.assertEqual(foe.beast.slug, Beast.objects.get(name="Headless Man").slug)
        self.assertEqual(foe.archetype, "")

    def test_a_slug_from_the_model_is_the_catalog_beast(self):
        (foe,) = resolve_foes([FoeRequest("guard", 1, beast_slug="gray-wolf")])
        assert foe.beast is not None
        self.assertEqual(foe.beast.slug, "gray-wolf")


class PeopleOnTheBoardTest(BaseTestCase):
    """A Game Master's ``/fight`` with people and a stand-in beast in it."""

    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm()
        self.session = GMSession.objects.create(
            name="The Toll Bridge", user=self.game_master
        )
        self.aldwin = create_character(self.game_master, name="Aldwin")
        self.session.characters.add(self.aldwin)

    def test_people_are_generated_fighters_named_as_asked(self):
        characters_before = Character.objects.count()
        started = start_fight(
            self.session, foes_text="two bandits, a gray wolf", seed=7
        )
        battle = started.battle
        people = list(
            battle.combatants.filter(
                beast__isnull=True, character__isnull=True
            ).order_by("pk")
        )
        self.assertEqual([row.display_name for row in people], ["Bandit", "Bandit (2)"])
        for row in people:
            self.assertEqual(row.team, FOE_TEAM)
            self.assertIsNone(row.player)
            self.assertFalse(row.is_saved_character)
        # Rolled, not saved: no row joins the Game Master's characters.
        self.assertEqual(Character.objects.count(), characters_before)
        figures = {figure["name"]: figure for figure in battle.state_json["combatants"]}
        for name in ("Bandit", "Bandit (2)"):
            figure = figures[name]
            self.assertEqual(figure["archetype"], "unarmoured duelist")
            self.assertFalse(figure["is_beast"])
            # A catalog weapon, not a beast's kick.
            self.assertIn(figure["weapon"]["item_id"], ("saber", "rapier", "dagger"))
        self.assertTrue(figures["Gray Wolf"]["is_beast"])

    def test_the_game_master_is_told_they_are_people(self):
        start_fight(self.session, foes_text="a cultist", seed=7)
        self.session.refresh_from_db()
        hidden, visible = self.session.history
        self.assertIn("Cultist (a person: unarmoured duelist)", hidden["content"])
        self.assertIn(
            "against Cultist (a person: unarmoured duelist)", visible["content"]
        )

    def test_the_names_survive_a_restart(self):
        started = start_fight(self.session, foes_text="a bandit", seed=7)
        battle = restart_battle(started.battle.pk)
        names = {figure["name"] for figure in battle.state_json["combatants"]}
        self.assertIn("Bandit", names)

    def test_a_stand_in_beast_says_so_on_its_roster_card(self):
        started = start_fight(self.session, foes_text="shadow hound", seed=7)
        (foe,) = started.foes
        assert foe.generated_from is not None
        battle = Battle.objects.get(pk=started.battle.pk)
        for field in ("state_json", "initial_state_json"):
            figure = next(
                figure
                for figure in getattr(battle, field)["combatants"]
                if figure["name"] == "shadow hound"
            )
            self.assertEqual(
                figure["archetype"],
                f"stand-in beast (from {foe.generated_from.name})",
            )
