"""Two overlapping requests on one battle setup (issue #832).

"Begin the battle" pressed twice, or retried after a slow first request,
loaded the draft twice before either request deleted it, so both built a
battle. The muster changes were read-modify-write on the draft's JSON with
nothing held, so two quick adds could lose one. Each test hands the service
two instances loaded before either change, which is the interleaving two
requests produce.
"""

from unittest import mock

from django.urls import reverse

from battle.models import Battle, BattleDraft
from battle.services import (
    add_draft_entry,
    add_draft_team,
    import_party_into_draft,
    remove_draft_entry,
    remove_draft_team,
    set_entry_team,
    start_draft,
)
from common.test_helpers import BaseTestCase, create_character
from parties.models import Party, PartyMembership

TWO_ARCHERS = [
    {"kind": "fighter", "archetype": "archer"},
    {"kind": "fighter", "archetype": "archer"},
]


class DraftRaceTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()

    def two_loads(self, **fields) -> tuple[BattleDraft, BattleDraft]:
        """One draft, loaded twice, as two overlapping requests would."""
        draft = BattleDraft.objects.create(created_by=self.dm, **fields)
        return (
            BattleDraft.objects.get(pk=draft.pk),
            BattleDraft.objects.get(pk=draft.pk),
        )

    def test_two_overlapping_starts_make_one_battle(self):
        first, second = self.two_loads(name="Twice", roster_json=TWO_ARCHERS)
        before = Battle.objects.count()
        start_draft(first)
        with self.assertRaises(BattleDraft.DoesNotExist):
            start_draft(second)
        self.assertEqual(Battle.objects.count(), before + 1)

    def test_start_reads_the_draft_as_it_stands_now(self):
        """A muster change that landed after this request loaded the draft
        is in the battle it builds."""
        first, second = self.two_loads(name="Late", roster_json=TWO_ARCHERS)
        add_draft_entry(second, self.dm, kind="fighter", archetype="wizard")
        battle = start_draft(first)
        self.assertEqual(battle.combatants.count(), 3)

    def test_two_overlapping_adds_keep_both(self):
        first, second = self.two_loads(name="Adds")
        add_draft_entry(first, self.dm, kind="fighter", archetype="archer")
        add_draft_entry(second, self.dm, kind="fighter", archetype="wizard")
        stored = BattleDraft.objects.get(pk=first.pk).roster_json
        self.assertEqual([entry["archetype"] for entry in stored], ["archer", "wizard"])

    def test_a_removal_strikes_the_entry_at_that_position_now(self):
        first, second = self.two_loads(name="Strike", roster_json=TWO_ARCHERS)
        add_draft_entry(first, self.dm, kind="fighter", archetype="wizard")
        remove_draft_entry(second, 2)
        stored = BattleDraft.objects.get(pk=first.pk).roster_json
        self.assertEqual([entry["archetype"] for entry in stored], ["archer", "archer"])

    def test_teams_named_at_once_both_stand(self):
        first, second = self.two_loads(name="Sides")
        self.assertEqual(add_draft_team(first, "Red"), "Red")
        self.assertEqual(add_draft_team(second, "Red"), "Red (2)")
        stored = BattleDraft.objects.get(pk=first.pk)
        self.assertEqual(stored.teams_json, ["Red", "Red (2)"])

    def test_a_team_move_keeps_an_entry_added_meanwhile(self):
        first, second = self.two_loads(name="Move", roster_json=TWO_ARCHERS)
        add_draft_team(first, "Red")
        add_draft_entry(first, self.dm, kind="fighter", archetype="wizard")
        set_entry_team(second, 0, "Red")
        stored = BattleDraft.objects.get(pk=first.pk).roster_json
        self.assertEqual(len(stored), 3)
        self.assertEqual(stored[0]["team"], "Red")

    def test_a_disband_keeps_an_entry_added_meanwhile(self):
        first, second = self.two_loads(name="Disband", teams_json=["Red"])
        add_draft_entry(first, self.dm, kind="fighter", archetype="archer", team="Red")
        remove_draft_team(second, "Red")
        stored = BattleDraft.objects.get(pk=first.pk)
        self.assertEqual(stored.teams_json, [])
        self.assertEqual(
            stored.roster_json, [{"kind": "fighter", "archetype": "archer"}]
        )

    def test_a_party_import_keeps_an_entry_added_meanwhile(self):
        member = create_character(self.dm, name="Aldwin")
        party = Party.objects.create(owner=self.dm, name="Company")
        PartyMembership.objects.create(party=party, character=member, added_by=self.dm)
        first, second = self.two_loads(name="Import")
        add_draft_entry(first, self.dm, kind="fighter", archetype="archer")
        import_party_into_draft(second, self.dm, party.pk)
        stored = BattleDraft.objects.get(pk=first.pk)
        self.assertEqual(stored.teams_json, ["Company"])
        self.assertEqual(
            [entry["kind"] for entry in stored.roster_json], ["fighter", "character"]
        )

    def test_a_change_to_a_draft_that_is_gone_is_refused(self):
        first, second = self.two_loads(name="Gone", roster_json=TWO_ARCHERS)
        start_draft(first)
        for label, change in [
            ("add", lambda: add_draft_entry(second, self.dm, kind="open_seat")),
            ("remove", lambda: remove_draft_entry(second, 0)),
            ("team", lambda: add_draft_team(second, "Red")),
        ]:
            with self.subTest(label), self.assertRaises(BattleDraft.DoesNotExist):
                change()


class DraftRaceViewTest(BaseTestCase):
    """The page's side: a second press finds the draft gone and says so."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.login_as(self.dm)
        self.draft = BattleDraft.objects.create(
            created_by=self.dm, name="Once", roster_json=TWO_ARCHERS
        )

    def test_a_press_that_loaded_the_draft_before_it_went_goes_to_the_list(self):
        """The overlapping press: it loaded the draft, then the other press
        started it. It used to build a second battle, or answer 500 when its
        terms save found no row. It goes to the battle list, where the one
        battle the other press built is listed first."""
        stale = BattleDraft.objects.get(pk=self.draft.pk)
        start_draft(BattleDraft.objects.get(pk=self.draft.pk))
        before = Battle.objects.count()
        with mock.patch("battle.views._own_draft_or_404", return_value=stale):
            response = self.client.post(
                reverse("battle:setup-start", args=[self.draft.pk]), {"name": "Once"}
            )
        self.assertRedirects(response, reverse("battle:list"))
        self.assertEqual(Battle.objects.count(), before)

    def test_a_press_whose_start_found_the_draft_gone_goes_to_the_list(self):
        """The same press, with the draft going between the terms save and
        the start itself."""
        stale = BattleDraft.objects.get(pk=self.draft.pk)
        before = Battle.objects.count()
        with (
            mock.patch("battle.views._own_draft_or_404", return_value=stale),
            mock.patch("battle.views._apply_draft_terms", return_value=None),
        ):
            BattleDraft.objects.filter(pk=self.draft.pk).delete()
            response = self.client.post(
                reverse("battle:setup-start", args=[self.draft.pk]), {"name": "Once"}
            )
        self.assertRedirects(response, reverse("battle:list"))
        self.assertEqual(Battle.objects.count(), before)

    def test_a_muster_change_that_loaded_the_draft_before_it_went_is_a_404(self):
        member = create_character(self.dm, name="Aldwin")
        party = Party.objects.create(owner=self.dm, name="Company")
        PartyMembership.objects.create(party=party, character=member, added_by=self.dm)
        stale = BattleDraft.objects.get(pk=self.draft.pk)
        start_draft(BattleDraft.objects.get(pk=self.draft.pk))
        for label, name, body in [
            ("add", "battle:setup-add", {"kind": "open_seat"}),
            ("remove", "battle:setup-remove", {"index": "0"}),
            ("team add", "battle:setup-team-add", {"name": "Red"}),
            ("team remove", "battle:setup-team-remove", {"name": "Red"}),
            ("entry team", "battle:setup-entry-team", {"index": "0", "team": ""}),
            (
                "import party",
                "battle:setup-import-party",
                {"party_id": str(party.pk)},
            ),
        ]:
            with (
                self.subTest(label),
                mock.patch("battle.views._own_draft_or_404", return_value=stale),
            ):
                response = self.client.post(reverse(name, args=[self.draft.pk]), body)
                self.assertEqual(response.status_code, 404, label)

    def test_a_second_press_after_the_first_finished_is_a_404(self):
        """Unchanged from before #832: a start for a draft that is not there
        when the request arrives is the 404 any missing draft is."""
        path = reverse("battle:setup-start", args=[self.draft.pk])
        first = self.client.post(path, {"name": "Once"})
        self.assertEqual(first.status_code, 302)
        second = self.client.post(path, {"name": "Once"})
        self.assertEqual(second.status_code, 404)
        self.assertEqual(Battle.objects.filter(name="Once").count(), 1)
