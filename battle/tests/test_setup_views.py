"""The staged setup flow's views (#270/#271): auth matrix, the draft's
autosave and roster endpoints, the start compile, and the round-trip
continuation through the character editor."""

import json
from unittest import mock

from django.test import override_settings
from django.urls import reverse

from battle.adaptation import NO_MODELLED_ATTACK_NOTE
from battle.models import Battle, BattleDraft
from battle.persistence import CLASSIC_PROFILE, TARMAR_PROFILE
from battle.services import create_battle
from common.test_helpers import BaseTestCase, create_character

from .test_services import make_beast

#: A hostname the arena theme answers to, per this module's overrides. Not a
#: real deployment name: the mapping is deployment config (``THEME_HOSTS``),
#: and a test that hard-coded the production host would pin the wrong thing.
ARENA_HOST = "melee.example.com"


def make_draft(dm, **overrides):
    fields = {"name": "Drafted", **overrides}
    return BattleDraft.objects.create(created_by=dm, **fields)


class SetupAuthMatrixTest(BaseTestCase):
    """A draft is its creator's, and nobody else's.

    The DM-only half of this matrix went with issue #305 — anyone may open
    a battle now, guests included. What did not move is the own-draft rule,
    which is the part that actually protects anything: a draft names the
    characters somebody is mustering, and it is private to whoever opened
    it, DM or not.
    """

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.draft = make_draft(self.dm)

    def draft_urls(self):
        return [
            ("get", reverse("battle:setup", args=[self.draft.pk])),
            ("post", reverse("battle:setup-update", args=[self.draft.pk])),
            ("post", reverse("battle:setup-add", args=[self.draft.pk])),
            ("post", reverse("battle:setup-remove", args=[self.draft.pk])),
            ("post", reverse("battle:setup-start", args=[self.draft.pk])),
            ("post", reverse("battle:setup-discard", args=[self.draft.pk])),
            # The team endpoints (#275) sit behind the same own-draft
            # gate as the rest of the setup surface.
            ("post", reverse("battle:setup-import-party", args=[self.draft.pk])),
            ("post", reverse("battle:setup-team-add", args=[self.draft.pk])),
            ("post", reverse("battle:setup-team-remove", args=[self.draft.pk])),
            ("post", reverse("battle:setup-entry-team", args=[self.draft.pk])),
        ]

    def test_a_guest_may_open_a_draft_but_never_this_one(self):
        response = self.client.post(reverse("battle:setup-create"))
        self.assertEqual(response.status_code, 302)
        for method, url in self.draft_urls():
            response = getattr(self.client, method)(url)
            self.assertEqual(response.status_code, 404, url)

    def test_a_player_may_open_a_draft_but_never_this_one(self):
        player = self.create_player()
        self.login_as(player)
        response = self.client.post(reverse("battle:setup-create"))
        self.assertEqual(response.status_code, 302)
        for method, url in self.draft_urls():
            response = getattr(self.client, method)(url)
            self.assertEqual(response.status_code, 404, url)

    def test_another_dm_gets_no_special_read_of_a_draft(self):
        """Drafts are private to their creator, and a DM is no exception —
        the role check on this surface was ownership all along (#305)."""
        self.login_as(self.create_dm(username="nosydm"))
        for method, url in self.draft_urls():
            response = getattr(self.client, method)(url)
            self.assertEqual(response.status_code, 404, url)

    def test_another_dms_draft_is_a_404(self):
        other_dm = self.create_dm(username="otherdm")
        self.login_as(other_dm)
        for method, url in self.draft_urls():
            response = getattr(self.client, method)(url)
            self.assertEqual(response.status_code, 404, url)

    def test_the_creating_dm_gets_in(self):
        self.login_as(self.dm)
        response = self.client.get(reverse("battle:setup", args=[self.draft.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The muster")


class SetupCreateTest(BaseTestCase):
    def test_new_battle_opens_a_fresh_draft(self):
        dm = self.create_dm()
        self.login_as(dm)
        response = self.client.post(reverse("battle:setup-create"))
        draft = BattleDraft.objects.get(created_by=dm)
        self.assertRedirects(response, reverse("battle:setup", args=[draft.pk]))


class SetupPageTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.login_as(self.dm)
        self.draft = make_draft(self.dm)

    def setup_url(self):
        return reverse("battle:setup", args=[self.draft.pk])

    def test_page_offers_every_source_and_the_ledger(self):
        make_beast()
        response = self.client.get(self.setup_url())
        for expected in (
            "The terms",
            "The muster",
            "The march",
            "Fighters",
            "Saved characters",
            "Beasts",
            "Open seats",
            "Muster roll",
            "Test Wolf",
        ):
            self.assertContains(response, expected)

    def test_seatable_characters_are_listed_with_edit_and_seat(self):
        player = self.create_player()
        mine = create_character(self.dm, name="Morwen")
        create_character(player, name="Aldwin")
        create_character(player, name="Ghost", is_alive=False)
        response = self.client.get(self.setup_url())
        self.assertContains(response, "Morwen")
        self.assertContains(response, "Aldwin")
        self.assertNotContains(response, "Ghost")
        # The round trip: Edit links carry the way back to this setup.
        self.assertContains(
            response, f"{reverse('characters:edit', args=[mine.pk])}?next="
        )

    def test_the_empty_muster_explains_itself_and_blocks_the_march(self):
        response = self.client.get(self.setup_url())
        self.assertContains(response, "Nobody is mustered yet")
        self.assertContains(response, "disabled")
        self.assertContains(response, "Add at least 2")


class SetupUpdateTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.login_as(self.dm)
        self.draft = make_draft(self.dm, name="")

    def save_field(self, field, value):
        return self.client.post(
            reverse("battle:setup-update", args=[self.draft.pk]),
            {"field": field, "value": value},
            content_type="application/json",
        )

    def test_terms_fields_autosave_onto_the_draft(self):
        for field, value, stored in (
            ("name", "  Pit Fight  ", "Pit Fight"),
            ("seed", "42", 42),
            ("rules_profile", "classic-melee", "classic-melee"),
            ("auto_start", True, True),
        ):
            response = self.save_field(field, value)
            self.assertEqual(response.status_code, 200, field)
            self.draft.refresh_from_db()
            self.assertEqual(getattr(self.draft, field), stored, field)

    def test_blank_seed_clears_it(self):
        self.draft.seed = 9
        self.draft.save()
        self.save_field("seed", "")
        self.draft.refresh_from_db()
        self.assertIsNone(self.draft.seed)

    def test_bad_values_are_refused_with_a_reason(self):
        for field, value in (
            ("seed", "abc"),
            ("seed", "-1"),
            ("seed", str(2**31)),
            ("rules_profile", "calvinball"),
            ("name", "x" * 101),
        ):
            response = self.save_field(field, value)
            self.assertEqual(response.status_code, 400, (field, value))
            self.assertIn("error", response.json())

    def test_unknown_field_is_refused(self):
        response = self.save_field("status", "finished")
        self.assertEqual(response.status_code, 400)


class SetupRosterEndpointsTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.login_as(self.dm)
        self.draft = make_draft(self.dm)

    def add(self, **fields):
        return self.client.post(
            reverse("battle:setup-add", args=[self.draft.pk]), fields
        )

    def test_add_redirects_back_to_the_setup(self):
        response = self.add(kind="fighter", archetype="archer")
        self.assertRedirects(response, reverse("battle:setup", args=[self.draft.pk]))
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.roster_json[0]["archetype"], "archer")

    def test_added_character_appears_on_the_muster_roll(self):
        character = create_character(self.dm, name="Morwen")
        self.add(kind="character", character_id=str(character.pk))
        response = self.client.get(reverse("battle:setup", args=[self.draft.pk]))
        self.assertContains(response, "Morwen")
        self.assertContains(response, "snapshotted when the battle starts")

    def test_a_beast_with_a_modelled_attack_reads_plainly_on_the_muster(self):
        make_beast(name="Dire Wolf", slug="muster-dire-wolf", category="mammals")
        self.add(kind="beast", slug="muster-dire-wolf")
        response = self.client.get(reverse("battle:setup", args=[self.draft.pk]))
        self.assertContains(response, "Dire Wolf")
        self.assertNotContains(response, NO_MODELLED_ATTACK_NOTE)

    def test_a_beast_with_no_modelled_attack_says_so_on_the_muster(self):
        """The muster used to print only the category and the ST, so a DM
        adding a beast the catalog gives no rollable damage for had nothing
        on screen telling them it would not fight like its catalog page
        (#645)."""
        make_beast(
            name="Monstrous Snapping Turtle",
            slug="muster-snapper",
            category="reptiles_amphibians",
            strength=70,
            attacks=[],
        )
        self.add(kind="beast", slug="muster-snapper")
        response = self.client.get(reverse("battle:setup", args=[self.draft.pk]))
        self.assertContains(response, "Monstrous Snapping Turtle")
        self.assertContains(response, "ST 70")
        self.assertContains(response, NO_MODELLED_ATTACK_NOTE)

    def test_bad_entry_renders_the_reason_in_place(self):
        response = self.add(kind="fighter", archetype="dragon rider")
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Unknown archetype", status_code=400)

    def test_non_numeric_character_id_is_refused(self):
        response = self.add(kind="character", character_id="Morwen")
        self.assertEqual(response.status_code, 400)

    def test_remove_strikes_an_entry(self):
        self.add(kind="fighter", archetype="archer")
        self.add(kind="open_seat")
        response = self.client.post(
            reverse("battle:setup-remove", args=[self.draft.pk]), {"index": "0"}
        )
        self.assertRedirects(response, reverse("battle:setup", args=[self.draft.pk]))
        self.draft.refresh_from_db()
        self.assertEqual(
            [entry["kind"] for entry in self.draft.roster_json], ["open_seat"]
        )

    def test_remove_bad_index_is_refused(self):
        for bad in ("", "abc", "5"):
            response = self.client.post(
                reverse("battle:setup-remove", args=[self.draft.pk]),
                {"index": bad},
            )
            self.assertEqual(response.status_code, 400, bad)

    def test_stale_character_entry_is_flagged_and_blocks_the_march(self):
        character = create_character(self.dm, name="Doomed")
        self.add(kind="character", character_id=str(character.pk))
        self.add(kind="fighter", archetype="archer")
        character.is_deleted = True
        character.save()
        response = self.client.get(reverse("battle:setup", args=[self.draft.pk]))
        self.assertContains(response, "Unavailable character")
        self.assertContains(response, "Strike the unavailable entries first")


class SetupStartTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.login_as(self.dm)
        self.draft = make_draft(self.dm, name="")

    def add(self, **fields):
        self.client.post(reverse("battle:setup-add", args=[self.draft.pk]), fields)

    def start(self, **terms):
        return self.client.post(
            reverse("battle:setup-start", args=[self.draft.pk]), terms
        )

    def test_start_merges_the_terms_compiles_and_redirects(self):
        character = create_character(self.dm, name="Morwen")
        self.add(kind="fighter", archetype="archer")
        self.add(kind="character", character_id=str(character.pk))
        response = self.start(name="Pit Fight", seed="42", rules_profile="tarmar")
        battle = Battle.objects.get(name="Pit Fight")
        self.assertRedirects(response, reverse("battle:detail", args=[battle.pk]))
        self.assertEqual(battle.seed, 42)
        self.assertEqual(battle.combatants.count(), 2)
        self.assertTrue(battle.combatants.filter(character=character).exists())
        self.assertFalse(BattleDraft.objects.filter(pk=self.draft.pk).exists())

    def test_open_seats_start_a_lobby(self):
        self.add(kind="fighter", archetype="archer")
        self.add(kind="open_seat")
        self.start(name="Open Pit", auto_start="true")
        battle = Battle.objects.get(name="Open Pit")
        self.assertEqual(battle.status, Battle.STATUS_LOBBY)
        self.assertTrue(battle.auto_start)
        self.assertEqual(battle.combatants.filter(is_open_seat=True).count(), 1)

    def test_a_nameless_start_is_refused_and_the_draft_survives(self):
        self.add(kind="fighter", archetype="archer")
        self.add(kind="fighter", archetype="wizard")
        response = self.start(name="   ")
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "needs a name", status_code=400)
        self.assertTrue(BattleDraft.objects.filter(pk=self.draft.pk).exists())

    def test_a_too_small_muster_is_refused(self):
        self.add(kind="fighter", archetype="archer")
        response = self.start(name="Thin")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Battle.objects.filter(name="Thin").exists())

    def test_a_beast_gone_from_the_catalog_is_refused_at_the_start(self):
        """Validated at the add, but the catalog can change under a draft."""
        beast = make_beast()
        self.add(kind="fighter", archetype="archer")
        self.add(kind="beast", slug=beast.slug)
        beast.delete()
        response = self.start(name="Ghost Pit")
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response, "No catalog beast matches that request", status_code=400
        )
        self.assertTrue(BattleDraft.objects.filter(pk=self.draft.pk).exists())

    def test_unchecked_auto_start_clears_a_stale_true(self):
        self.draft.auto_start = True
        self.draft.save()
        self.add(kind="fighter", archetype="archer")
        self.add(kind="fighter", archetype="wizard")
        self.start(name="Sober")
        battle = Battle.objects.get(name="Sober")
        self.assertFalse(battle.auto_start)


class SetupDiscardTest(BaseTestCase):
    def test_discard_deletes_the_draft_and_returns_to_the_list(self):
        dm = self.create_dm()
        self.login_as(dm)
        draft = make_draft(dm)
        response = self.client.post(reverse("battle:setup-discard", args=[draft.pk]))
        self.assertRedirects(response, reverse("battle:list"))
        self.assertFalse(BattleDraft.objects.filter(pk=draft.pk).exists())


class BattleListSlimmingTest(BaseTestCase):
    def test_dm_list_offers_the_new_battle_button_and_drafts(self):
        dm = self.create_dm()
        self.login_as(dm)
        make_draft(dm, name="Half-built")
        response = self.client.get(reverse("battle:list"))
        self.assertContains(response, "New battle")
        self.assertContains(response, "Half-built")
        self.assertContains(response, "Resume")
        # The old dense creation form is gone from the list page.
        self.assertNotContains(response, "Archetype mix")
        self.assertNotContains(response, 'name="beast_count"')

    def test_player_list_offers_the_button_but_not_a_stranger_s_draft(self):
        """The New battle button is everyone's since #305; the drafts shelf
        is still only ever your own."""
        dm = self.create_dm()
        make_draft(dm, name="Private draft")
        player = self.create_player()
        self.login_as(player)
        response = self.client.get(reverse("battle:list"))
        self.assertContains(response, "New battle")
        self.assertNotContains(response, "Private draft")


class RoundTripContinuationTest(BaseTestCase):
    """The next= continuation through the character create/edit pages."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.login_as(self.dm)
        self.draft = make_draft(self.dm)
        self.setup_url = reverse("battle:setup", args=[self.draft.pk])

    def test_create_page_carries_next_through_its_forms(self):
        response = self.client.get(
            reverse("characters:create"), {"next": self.setup_url}
        )
        self.assertContains(response, "Back to the battle setup")
        self.assertContains(response, f'name="next" value="{self.setup_url}"')

    def test_created_character_lands_in_the_editor_with_the_way_back(self):
        response = self.client.post(
            reverse("characters:create"),
            {"method": "roll", "next": self.setup_url},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("?next=", response.url)
        followed = self.client.get(response.url)
        self.assertContains(followed, "Back to the battle setup")
        self.assertContains(followed, self.setup_url)

    def test_editor_shows_the_way_back_only_with_a_safe_next(self):
        character = create_character(self.dm, name="Morwen")
        edit_url = reverse("characters:edit", args=[character.pk])
        plain = self.client.get(edit_url)
        self.assertNotContains(plain, "Back to the battle setup")
        with_next = self.client.get(edit_url, {"next": self.setup_url})
        self.assertContains(with_next, "Back to the battle setup")

    def test_offsite_next_is_dropped(self):
        character = create_character(self.dm, name="Morwen")
        edit_url = reverse("characters:edit", args=[character.pk])
        for evil in ("https://evil.example/", "//evil.example/x", "javascript:1"):
            response = self.client.get(edit_url, {"next": evil})
            self.assertNotContains(response, "Back to the battle setup")

    def test_the_draft_survives_the_round_trip(self):
        self.client.post(
            reverse("battle:setup-add", args=[self.draft.pk]),
            {"kind": "fighter", "archetype": "archer"},
        )
        self.client.post(
            reverse("characters:create"),
            {"method": "roll", "next": self.setup_url},
        )
        response = self.client.get(self.setup_url)
        self.assertContains(response, "Archer")
        # And the new character is on the shelf, ready to seat.
        self.assertContains(response, self.dm.username)


@override_settings(
    ALLOWED_HOSTS=["testserver", ARENA_HOST],
    THEME_HOSTS={ARENA_HOST: "classic-arena"},
)
class FrontDoorDefaultProfileTest(BaseTestCase):
    """A new draft opens on the rules its front door stands for (#332).

    The door said Melee and the fight was six-phase Tarmar unless the
    visitor noticed the dropdown. Now the arena host seeds a classic draft
    and every other host seeds a Tarmar one -- a *default*, not a lock:
    both profiles stay pickable at both doors, and no battle that already
    exists changes because of the hostname it is read through.
    """

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.login_as(self.dm)

    def created_draft(self, **request_kwargs) -> BattleDraft:
        response = self.client.post(reverse("battle:setup-create"), **request_kwargs)
        self.assertEqual(response.status_code, 302)
        return BattleDraft.objects.get(
            pk=int(response.url.rstrip("/").rsplit("/", 1)[-1])
        )

    def test_arena_host_opens_a_classic_draft(self):
        draft = self.created_draft(HTTP_HOST=ARENA_HOST)
        self.assertEqual(draft.rules_profile, CLASSIC_PROFILE)

    def test_default_host_still_opens_a_tarmar_draft(self):
        draft = self.created_draft()
        self.assertEqual(draft.rules_profile, TARMAR_PROFILE)

    def test_arena_host_preselects_classic_in_the_dropdown(self):
        draft = self.created_draft(HTTP_HOST=ARENA_HOST)
        response = self.client.get(
            reverse("battle:setup", args=[draft.pk]), HTTP_HOST=ARENA_HOST
        )
        self.assertContains(
            response,
            f'<option value="{CLASSIC_PROFILE}" selected>Classic Melee</option>',
            html=True,
        )
        # The choice is still a choice: Tarmar is offered, unselected.
        self.assertContains(
            response,
            f'<option value="{TARMAR_PROFILE}">Tarmar</option>',
            html=True,
        )

    def test_the_dropdown_still_overrides_the_door(self):
        draft = self.created_draft(HTTP_HOST=ARENA_HOST)
        self.client.post(
            reverse("battle:setup-update", args=[draft.pk]),
            data=json.dumps({"field": "rules_profile", "value": TARMAR_PROFILE}),
            content_type="application/json",
            HTTP_HOST=ARENA_HOST,
        )
        draft.refresh_from_db()
        self.assertEqual(draft.rules_profile, TARMAR_PROFILE)

    def test_an_existing_tarmar_battle_is_tarmar_on_the_arena_host(self):
        """The theme seeds a draft; it never re-reads a battle's rules."""
        battle = create_battle(self.dm, name="Studio Bout", count=2, player_seats=1)
        self.assertEqual(battle.rules_profile, TARMAR_PROFILE)
        response = self.client.get(
            reverse("battle:detail", args=[battle.pk]), HTTP_HOST=ARENA_HOST
        )
        self.assertContains(response, "Fought under <strong>Tarmar</strong>")
        self.assertNotContains(response, "Classic Melee")


class SetupPageNamesTheDraftTest(BaseTestCase):
    """A resumed draft is called by its name, not "New battle" (issue #339, item 27)."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.login_as(self.dm)

    def test_a_named_draft_is_titled_and_trailed_by_its_name(self):
        draft = make_draft(self.dm, name="Siege of the Mill")

        response = self.client.get(reverse("battle:setup", args=[draft.pk]))

        self.assertContains(response, "<h1>Siege of the Mill</h1>", html=False)
        self.assertEqual(
            response.context["breadcrumb"][-1]["label"], "Siege of the Mill"
        )
        self.assertNotContains(response, "<h1>New battle</h1>")

    def test_an_unnamed_draft_is_still_a_new_battle(self):
        draft = make_draft(self.dm, name="")

        response = self.client.get(reverse("battle:setup", args=[draft.pk]))

        self.assertContains(response, "<h1>New battle</h1>")
        self.assertEqual(response.context["breadcrumb"][-1]["label"], "New battle")


class StaleAutosaveTest(BaseTestCase):
    """A blur autosave that lands after its draft is gone (#465).

    Starting a draft deletes it, and so does discarding one. An autosave sent
    just before either, and handled just after, must be refused as a JSON
    error the setup page's autosave can show, never a 500.
    """

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.login_as(self.dm)
        self.draft = make_draft(self.dm)
        # Kept apart from the instance: deleting a model instance clears its pk.
        self.draft_pk = self.draft.pk

    def post_update(self):
        return self.client.post(
            reverse("battle:setup-update", args=[self.draft_pk]),
            json.dumps({"field": "name", "value": "Renamed"}),
            content_type="application/json",
        )

    def assert_refused_as_gone(self, response):
        from battle.views import DRAFT_GONE_MESSAGE

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": DRAFT_GONE_MESSAGE})

    def test_an_autosave_after_the_draft_is_gone_is_a_json_404(self):
        self.draft.delete()
        self.assert_refused_as_gone(self.post_update())

    def test_a_draft_that_vanishes_between_load_and_save_is_a_json_404(self):
        from battle import views

        load_own_draft = views._own_draft_or_404

        def load_then_start_elsewhere(request, pk):
            draft = load_own_draft(request, pk)
            BattleDraft.objects.filter(pk=draft.pk).delete()
            return draft

        with mock.patch(
            "battle.views._own_draft_or_404", side_effect=load_then_start_elsewhere
        ):
            response = self.post_update()
        self.assert_refused_as_gone(response)
