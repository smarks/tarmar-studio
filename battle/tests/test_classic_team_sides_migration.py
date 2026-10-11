"""The data migration that prefixes stored classic team sides (#944).

Before #930 a teamed classic figure's side was its team's bare name, which a
team named like a row id shares with that row's teamless figure. These build
battles in that old form and prove the migration moves them to the prefixed
one, with the allies the rows say, and back again.
"""

from importlib import import_module

from django.apps import apps as installed_apps
from django.db import transaction

from battle.models import Battle, BattleCombatant
from battle.persistence import CLASSIC_PROFILE
from battle.profiles import for_battle
from battle.services import create_battle
from common.test_helpers import BaseTestCase

MIGRATION = import_module("battle.migrations.0017_classic_team_sides_prefixed")

SNAPSHOT_FIELDS = ("state_json", "initial_state_json")


def sides(battle: Battle, field: str = "state_json") -> dict[str, str]:
    """A stored snapshot's figure sides, by uid."""
    battle.refresh_from_db()
    return {
        figure["uid"]: figure["side"] for figure in getattr(battle, field)["figures"]
    }


class ClassicTeamSidesMigrationTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()

    def old_form_battle(self, team_named_after: str) -> Battle:
        """A classic battle as a pre-#930 save holds it, with a row-named team.

        Two members of one team and one teamless figure; the team is renamed
        after the loner's row (``"loner"``) or its own first member's row
        (``"member"``), and every stored side is spelled the old way: the
        team's bare name, or the uid.
        """
        battle = create_battle(
            self.dm,
            name="Old save",
            count=3,
            seed=5,
            rules_profile=CLASSIC_PROFILE,
            fighter_teams=("Red", "Red", ""),
        )
        loner = battle.combatants.get(team="")
        first_member = battle.combatants.filter(team="Red").order_by("pk").first()
        assert first_member is not None
        named_after = loner if team_named_after == "loner" else first_member
        team = str(named_after.pk)
        battle.combatants.filter(team="Red").update(team=team)
        return self.spelled_the_old_way(battle)

    def spelled_the_old_way(self, battle: Battle) -> Battle:
        """``battle`` with every stored side its team's bare name, or its uid."""
        teams = dict(battle.combatants.values_list("pk", "team"))
        for field in SNAPSHOT_FIELDS:
            snapshot = getattr(battle, field)
            for figure in snapshot["figures"]:
                figure["side"] = teams[int(figure["uid"])] or figure["uid"]
        Battle.objects.filter(pk=battle.pk).update(
            state_json=battle.state_json,
            initial_state_json=battle.initial_state_json,
        )
        return Battle.objects.get(pk=battle.pk)

    def enemies(self, battle: Battle, uid: str) -> set[str]:
        from tarmar_engine.classic.state import GameState

        battle.refresh_from_db()
        state = for_battle(battle).load(battle.state_json)
        assert isinstance(state, GameState)
        by_uid = {figure.uid: figure for figure in state.figures}
        return {figure.uid for figure in state.enemies_of(by_uid[uid])}

    def test_a_team_named_like_the_loner_stops_being_its_ally(self):
        battle = self.old_form_battle(team_named_after="loner")
        loner = str(battle.combatants.get(team="").pk)
        members = {str(row.pk) for row in battle.combatants.exclude(team="")}
        # The old form's fault: the team's side is the loner's uid.
        self.assertEqual(self.enemies(battle, loner), set())

        MIGRATION.forwards(installed_apps, None)

        for field in SNAPSHOT_FIELDS:
            self.assertEqual(
                sides(battle, field),
                {loner: loner} | {member: f"team:{loner}" for member in members},
            )
        self.assertEqual(self.enemies(battle, loner), members)
        profile = for_battle(battle)
        state = profile.load(battle.state_json)
        self.assertEqual(
            {figure.uid: profile.actor_team(figure) for figure in state.figures},
            {loner: ""} | {member: loner for member in members},
        )

        MIGRATION.backwards(installed_apps, None)

        for field in SNAPSHOT_FIELDS:
            self.assertEqual(
                sides(battle, field),
                {loner: loner} | {member: loner for member in members},
            )

    def test_a_team_named_like_its_own_member_is_read_as_that_team(self):
        battle = self.old_form_battle(team_named_after="member")
        member = battle.combatants.exclude(team="").order_by("pk").first()
        assert member is not None
        team = member.team
        self.assertEqual(sides(battle)[team], team)

        MIGRATION.forwards(installed_apps, None)

        battle.refresh_from_db()
        profile = for_battle(battle)
        state = profile.load(battle.state_json)
        figure = next(one for one in state.figures if one.uid == team)
        self.assertEqual(figure.side, f"team:{team}")
        self.assertEqual(profile.actor_team(figure), team)
        self.assertEqual(sides(battle, "initial_state_json")[team], f"team:{team}")

        MIGRATION.backwards(installed_apps, None)
        self.assertEqual(sides(battle)[team], team)

    def test_running_it_twice_changes_nothing_more(self):
        battle = self.old_form_battle(team_named_after="loner")
        MIGRATION.forwards(installed_apps, None)
        once = {field: sides(battle, field) for field in SNAPSHOT_FIELDS}
        MIGRATION.forwards(installed_apps, None)
        self.assertEqual(
            {field: sides(battle, field) for field in SNAPSHOT_FIELDS}, once
        )

    def assert_refused_and_rolled_back(self, *battles: Battle) -> None:
        """The migration raises, and atomically leaves every battle as it was."""
        before = [sides(battle) for battle in battles]
        with (
            self.assertRaises(MIGRATION.UnreadableSideError),
            transaction.atomic(),
        ):
            MIGRATION.forwards(installed_apps, None)
        self.assertEqual([sides(battle) for battle in battles], before)

    def test_a_teamed_figure_without_its_row_is_refused(self):
        # A battle the migration can rewrite comes first, so the rollback of
        # its rewrite is part of what is proved.
        rewritable = self.old_form_battle(team_named_after="member")
        battle = self.old_form_battle(team_named_after="loner")
        gone = battle.combatants.exclude(team="").order_by("pk").first()
        assert gone is not None
        BattleCombatant.objects.filter(pk=gone.pk).delete()

        self.assert_refused_and_rolled_back(rewritable, battle)

    def test_a_teamless_figure_without_its_row_is_kept_and_named(self):
        battle = self.old_form_battle(team_named_after="loner")
        gone = battle.combatants.get(team="")
        uid = str(gone.pk)
        BattleCombatant.objects.filter(pk=gone.pk).delete()

        with self.assertLogs(MIGRATION.__name__, level="WARNING") as logged:
            MIGRATION.forwards(installed_apps, None)

        self.assertEqual(sides(battle)[uid], uid)
        self.assertTrue(any("no BattleCombatant row" in line for line in logged.output))
        self.assertTrue(
            all(
                side.startswith("team:")
                for key, side in sides(battle).items()
                if key != uid
            )
        )

    def test_a_bare_team_side_its_row_disagrees_with_is_refused(self):
        battle = self.old_form_battle(team_named_after="loner")
        moved = battle.combatants.exclude(team="").order_by("pk").first()
        assert moved is not None
        BattleCombatant.objects.filter(pk=moved.pk).update(team="Blue")

        self.assert_refused_and_rolled_back(battle)

    def test_a_uid_side_whose_row_was_given_a_team_is_kept_and_named(self):
        battle = self.old_form_battle(team_named_after="loner")
        edited = battle.combatants.get(team="")
        uid = str(edited.pk)
        BattleCombatant.objects.filter(pk=edited.pk).update(team="Blue")

        with self.assertLogs(MIGRATION.__name__, level="WARNING") as logged:
            MIGRATION.forwards(installed_apps, None)

        self.assertEqual(sides(battle)[uid], uid)
        self.assertTrue(any("'Blue'" in line for line in logged.output))

    def test_a_team_named_with_the_prefix_is_prefixed_again(self):
        battle = create_battle(
            self.dm,
            name="Prefixed name",
            count=3,
            seed=5,
            rules_profile=CLASSIC_PROFILE,
            fighter_teams=("team:Red", "team:Red", "Red"),
        )
        battle = self.spelled_the_old_way(battle)
        named = {str(row.pk) for row in battle.combatants.filter(team="team:Red")}
        plain = str(battle.combatants.get(team="Red").pk)
        self.assertEqual(self.enemies(battle, plain), named)

        MIGRATION.forwards(installed_apps, None)

        for field in SNAPSHOT_FIELDS:
            self.assertEqual(
                sides(battle, field),
                {plain: "team:Red"} | {uid: "team:team:Red" for uid in named},
            )
        self.assertEqual(self.enemies(battle, plain), named)
        profile = for_battle(battle)
        state = profile.load(battle.state_json)
        self.assertEqual(
            {figure.uid: profile.actor_team(figure) for figure in state.figures},
            {plain: "Red"} | {uid: "team:Red" for uid in named},
        )
        MIGRATION.forwards(installed_apps, None)
        self.assertEqual(sides(battle)[plain], "team:Red")

        MIGRATION.backwards(installed_apps, None)

        for field in SNAPSHOT_FIELDS:
            self.assertEqual(
                sides(battle, field),
                {plain: "Red"} | {uid: "team:Red" for uid in named},
            )

    def test_a_tarmar_battle_is_not_touched(self):
        battle = create_battle(
            self.dm, name="Tarmar", count=2, seed=3, fighter_teams=("Red", "")
        )
        before = {field: getattr(battle, field) for field in SNAPSHOT_FIELDS}
        MIGRATION.forwards(installed_apps, None)
        battle.refresh_from_db()
        self.assertEqual(
            {field: getattr(battle, field) for field in SNAPSHOT_FIELDS}, before
        )
