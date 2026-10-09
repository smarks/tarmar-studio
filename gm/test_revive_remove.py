"""A Game Master can bring a character back or remove one (issue #403).

Session 29: a twin died in her fourth magic year, and the Game Master asked
the GM to "restore the two dead characters". Nothing could, so the GM built
new characters beside the dead one and the roster tangled. Spencer's rule for
the fix: the GM enforces the rules exactly for players, and a human with the
Game Master role may override them. These tests hold both halves: the two
actions do what they say, and a player's session cannot run them whatever the
model plans. Since issue #571 the characters are records; since #590 the
changes are plan actions.
"""

from unittest.mock import patch

from characters.models import Character
from common.test_helpers import BaseTestCase

from . import roster
from .engine import DM_SYSTEM_PROMPT, SYSTEM_PROMPT
from .models import GMSession
from .testing import run_actions

SURVIVE = patch("characters.skills.roll_survival_dice", return_value=(6, 6, 6, 18))
# Three years survived, then a natural 3 in the fourth: session 29's sister.
DIE_IN_YEAR_FOUR = [(6, 6, 6, 18)] * 3 + [(1, 1, 1, 3)]
FIRST_SKILL = patch("characters.skills.roll_d20", return_value=1)


def _everyone(session: GMSession) -> dict[str, dict]:
    return {entry.identity: entry.values for entry in roster.roster(session)}


def _id(session: GMSession, name: str) -> int:
    """The id of the character named or labelled ``name``, ignoring case."""
    (character_id,) = [
        entry.character.pk
        for entry in roster.roster(session)
        if name.casefold() in (entry.character.name.casefold(), entry.label.casefold())
    ]
    return character_id


def twins_with_a_dead_sister(user) -> GMSession:
    """Session 29's roster: the brother lived, the sister died in year four."""
    session = GMSession.objects.create(user=user, name="Twins")
    with FIRST_SKILL:
        run_actions(
            session,
            {
                "action": "record_attributes",
                "new_character": {"label": "Twin Male"},
                "all": 18,
            },
            {
                "action": "record_attributes",
                "new_character": {"label": "Twin Female"},
                "all": 18,
            },
            is_game_master=True,
        )
        with SURVIVE:
            run_actions(
                session,
                {
                    "action": "play_years",
                    "character_id": _id(session, "Twin Male"),
                    "track": "underworld",
                    "years": 5,
                },
            )
        with patch(
            "characters.skills.roll_survival_dice", side_effect=DIE_IN_YEAR_FOUR
        ):
            run_actions(
                session,
                {
                    "action": "play_years",
                    "character_id": _id(session, "Twin Female"),
                    "track": "magic",
                    "years": 5,
                },
            )
    return session


class PromptTest(BaseTestCase):
    def test_only_the_game_masters_prompt_teaches_them(self):
        for phrase in ("brought back to life", "taken out of this session"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, DM_SYSTEM_PROMPT)
                self.assertNotIn(phrase, SYSTEM_PROMPT)


class ReviveTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.session = twins_with_a_dead_sister(self.create_admin())

    def test_a_dead_twin_comes_back_with_her_years_and_how_she_died(self):
        self.assertFalse(_everyone(self.session)["Twin Female"]["is_alive"])

        (result,) = run_actions(
            self.session,
            {
                "action": "revive_character",
                "character_id": _id(self.session, "Twin Female"),
            },
        )

        self.assertTrue(result.ran, result.error)
        sister = _everyone(self.session)["Twin Female"]
        self.assertTrue(sister["is_alive"])
        self.assertEqual(sister["death_cause"], "")
        self.assertEqual(len(sister["experience_log"][0]["years"]), 4)
        self.assertEqual(
            result.procedure["lines"],
            [
                "⚙️ Twin Female: brought back to life (had died: Failed survival check as a magic at age 20)"
            ],
        )

    def test_the_living_are_not_brought_back(self):
        (result,) = run_actions(
            self.session,
            {
                "action": "revive_character",
                "character_id": _id(self.session, "Twin Male"),
            },
        )

        self.assertFalse(result.ran)
        self.assertEqual(result.error, "is alive; there is nothing to bring back")


class RemoveTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.session = twins_with_a_dead_sister(self.create_admin())

    def test_removing_a_character_leaves_the_other(self):
        sister = _id(self.session, "Twin Female")
        # A chat-built character's Fatigue is rolled as it is made (#859), so
        # the fixture's sister is complete; this test pinned the older design,
        # in which she was still waiting for the roll. An incomplete
        # character is still not kept, so she is made one here.
        Character.objects.filter(pk=sister).update(fatigue_roll=None)

        (result,) = run_actions(
            self.session, {"action": "remove_character", "character_id": sister}
        )

        self.assertEqual(
            result.note, "Twin Female: removed Twin Female from this session"
        )
        self.assertEqual(set(_everyone(self.session)), {"Twin Male"})
        # She was still incomplete (no Fatigue roll), so she is not kept.
        self.assertTrue(Character.objects.get(pk=sister).is_deleted)

    def test_removing_a_complete_character_only_detaches_them(self):
        brother = _id(self.session, "twin male")
        Character.objects.filter(pk=brother).update(fatigue_roll=7)

        run_actions(
            self.session, {"action": "remove_character", "character_id": brother}
        )

        self.assertEqual(set(_everyone(self.session)), {"Twin Female"})
        self.assertFalse(Character.objects.get(pk=brother).is_deleted)

    def test_removing_the_last_character_leaves_nobody(self):
        # A Game Master's account: the executor reads the role off the
        # account on each action (second review of #837), so a player's
        # account can no longer be handed Game Master authority by the caller.
        session = GMSession.objects.create(user=self.create_dm("kael"), name="s")
        run_actions(
            session,
            {
                "action": "record_attributes",
                "new_character": {"name": "Kael"},
                "all": 18,
            },
            is_game_master=True,
        )

        run_actions(
            session,
            {"action": "remove_character", "character_id": _id(session, "Kael")},
            is_game_master=True,
        )

        self.assertEqual(roster.roster(session), [])


class GameMasterOnlyTest(BaseTestCase):
    """The server checks the account; the conversation cannot grant it."""

    def test_a_players_session_cannot_do_either_whatever_the_model_plans(self):
        # The twins are built by a Game Master (their stated 18s are his to
        # record), and the account is then a player's. It used to be a
        # player's account handed Game Master authority for the build, which
        # the executor no longer accepts (second review of #837).
        owner = self.create_dm()
        session = twins_with_a_dead_sister(owner)
        owner.is_dm = False
        owner.save(update_fields=["is_dm"])
        before = _everyone(session)

        for action, name in (
            ("revive_character", "Twin Female"),
            ("remove_character", "Twin Male"),
        ):
            with self.subTest(action=action):
                (result,) = run_actions(
                    session, {"action": action, "character_id": _id(session, name)}
                )
                self.assertFalse(result.ran)
                self.assertEqual(
                    result.error,
                    "only a Game Master can bring a character back or remove one",
                )

        self.assertEqual(_everyone(session), before)
        self.assertFalse(_everyone(session)["Twin Female"]["is_alive"])


class LoneLabelledCharacterPaneTest(BaseTestCase):
    """The Characters pane names a lone character by its label (issue #403)."""

    def test_the_twin_left_after_a_removal_is_named(self):
        admin = self.create_admin()
        self.login_as(admin)
        session = twins_with_a_dead_sister(admin)
        run_actions(
            session,
            {"action": "remove_character", "character_id": _id(session, "Twin Female")},
        )

        response = self.client.get(f"/tarmar/gm/?session={session.pk}")

        self.assertEqual(
            [heading for heading, _ledger, _row in response.context["panel_drafts"]],
            ["Twin Male"],
        )
        self.assertContains(response, ">Twin Male</a></h4>")
        self.assertNotContains(response, "(unnamed)")
