"""A guest plays the arena, and stops exactly where a guest should (#305).

Three things are being pinned here, in order of how much they matter.

**Security first.** Opening the arena to the anonymous internet is a read
widening, and two ways it could have gone wrong are covered in their own
classes: a battle must not become a way to read somebody's private
character (``GuestReadBoundaryTest``, guarding issue #273's rule), and an
anonymous visitor must never be mistaken for the holder of an unclaimed
seat (``AnonymousIsNobodyTest`` — the ``None == None`` shape that
``battle.access.Actor`` exists to prevent).

**Then the limits**, because a limit that is not enforced server-side is
decoration: what a guest cannot do is refused by the endpoint, and said on
the page at the control it applies to.

**Then the journey**, end to end: land, muster, start, fight, and — if the
visitor takes the offer — sign in and find the battle still there.
"""

from django.contrib.auth.models import AnonymousUser
from django.test import Client
from django.urls import reverse

from battle.access import Actor, listable_battles, readable_battles
from battle.limits import (
    GUEST_BATTLES_PER_WINDOW,
    GUEST_LIVE_BATTLE_CAP,
    charge_guest_creation,
)
from battle.models import Battle, BattleCombatant, BattleDraft
from battle.services import claim_seat, create_battle, seatable_characters
from characters.models import Character, characters_visible_to
from common.guest import GUEST_KEY_SESSION_FIELD
from common.test_helpers import TEST_PASSWORD, BaseTestCase, create_character


class GuestClientMixin(BaseTestCase):
    """A client that has answered no passphrase and holds no account."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def guest_key(self, client=None):
        return (client or self.client).session.get(GUEST_KEY_SESSION_FIELD, "")

    def open_draft(self, client=None):
        client = client or self.client
        response = client.post(reverse("battle:setup-create"))
        self.assertEqual(response.status_code, 302)
        return BattleDraft.objects.get(pk=response.url.rstrip("/").split("/")[-1])

    def muster_two_fighters(self, draft, client=None):
        client = client or self.client
        for archetype in ("armoured fighter", "archer"):
            response = client.post(
                reverse("battle:setup-add", args=[draft.pk]),
                {"kind": "fighter", "archetype": archetype},
            )
            self.assertEqual(response.status_code, 302)

    def start_draft_as_guest(self, name="Guest Bout", client=None):
        client = client or self.client
        draft = self.open_draft(client)
        self.muster_two_fighters(draft, client)
        response = client.post(
            reverse("battle:setup-start", args=[draft.pk]), {"name": name}
        )
        self.assertEqual(response.status_code, 302, response.content[:400])
        return Battle.objects.get(name=name)


# ------------------------------------------------------------- security


class GuestReadBoundaryTest(GuestClientMixin):
    """The read widening stops where issue #273 does.

    The arena page prints a combatant's strength, dexterity, weapon, armour
    and wounds. So "any signed-in user may watch any battle" cannot simply
    become "anyone may", or a private character's sheet would be readable
    to the internet by way of a battle it was seated in.
    """

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.owner = self.create_user("keeper")

    def battle_with(self, character):
        return create_battle(
            self.dm,
            name=f"With {character.name}",
            count=1,
            character_ids=(character.pk,),
            seed=3,
        )

    def test_a_battle_holding_a_private_character_is_invisible_to_a_guest(self):
        private = create_character(self.owner, name="Hidden Hero")
        battle = self.battle_with(private)
        # The page sends a signed-out guest to sign in (issue #339, item 32);
        # nothing of the battle is rendered, and its JSON state is a 404.
        detail = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertEqual(detail.status_code, 302)
        self.assertIn(reverse("login"), detail["Location"])
        self.assertNotContains(detail, private.name, status_code=302)
        state = self.client.get(reverse("battle:state", args=[battle.pk]))
        self.assertEqual(state.status_code, 404)
        self.assertNotIn(battle, readable_battles(Actor(user=None, guest_key="k")))

    def test_a_battle_of_public_characters_only_is_readable_by_a_guest(self):
        public = create_character(self.owner, name="Famous Hero", is_public=True)
        second = create_character(self.owner, name="Famous Friend", is_public=True)
        battle = create_battle(
            self.dm,
            name="All Public",
            count=0,
            character_ids=(public.pk, second.pk),
            seed=4,
        )
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertEqual(response.status_code, 200)

    def test_a_guests_own_battle_is_readable_because_it_holds_no_characters(self):
        battle = self.start_draft_as_guest()
        self.assertFalse(
            BattleCombatant.objects.filter(
                battle=battle, character__isnull=False
            ).exists()
        )
        stranger = Client()
        self.assertEqual(
            stranger.get(reverse("battle:detail", args=[battle.pk])).status_code, 200
        )

    def test_characters_visible_to_an_anonymous_viewer_is_empty(self):
        create_character(self.owner, name="Famous Hero", is_public=True)
        self.assertEqual(list(characters_visible_to(AnonymousUser())), [])
        self.assertEqual(list(seatable_characters(None)), [])


class AnonymousIsNobodyTest(GuestClientMixin):
    """An anonymous visitor holds nothing, and must never appear to.

    ``seat.player_id == user.pk`` is ``None == None`` when an anonymous
    viewer looks at an unclaimed seat. Every ownership question therefore
    goes through ``battle.access.Actor``; these are the assertions that
    would fail if it ever stopped.
    """

    def test_a_keyless_guest_owns_no_battle_and_holds_no_seat(self):
        dm = self.create_dm()
        battle = create_battle(dm, name="Not Yours", count=2, seed=5, player_seats=1)
        seat = battle.combatants.get(is_open_seat=True)
        nobody = Actor(user=None, guest_key="")
        self.assertFalse(nobody.owns(battle))
        self.assertFalse(nobody.holds_seat(seat))
        self.assertEqual(list(BattleCombatant.objects.filter(nobody.seat_filter())), [])
        self.assertEqual(list(listable_battles(nobody)), [])

    def test_a_guest_key_matches_only_its_own_rows(self):
        battle = self.start_draft_as_guest()
        mine = Actor(user=None, guest_key=self.guest_key())
        theirs = Actor(user=None, guest_key="someone-else")
        self.assertTrue(mine.owns(battle))
        self.assertFalse(theirs.owns(battle))

    def test_one_guest_cannot_run_another_guests_battle(self):
        battle = self.start_draft_as_guest()
        other = Client()
        response = other.post(reverse("battle:advance", args=[battle.pk]))
        self.assertEqual(response.status_code, 403)
        battle.refresh_from_db()
        self.assertEqual(battle.turn_number, 0)

    def test_one_guest_cannot_act_for_another_guests_seat(self):
        battle = self.start_draft_as_guest()
        seat = battle.combatants.first()
        other = Client()
        response = other.post(
            reverse("battle:seat-ready", args=[battle.pk, seat.pk]), {"ready": "1"}
        )
        self.assertEqual(response.status_code, 400)


# --------------------------------------------------------------- limits


class GuestCannotSaveOrChooseTest(GuestClientMixin):
    """Spencer's three prohibitions, refused at the server."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("keeper")
        self.public_character = create_character(
            self.owner, name="Famous Hero", is_public=True
        )

    def test_a_guests_battle_saves_no_characters(self):
        before = Character.objects.count()
        battle = self.start_draft_as_guest()
        self.assertEqual(Character.objects.count(), before)
        self.assertEqual(battle.combatants.count(), 2)
        self.assertTrue(
            all(row.character_id is None for row in battle.combatants.all())
        )

    def test_a_guest_cannot_seat_a_saved_character_through_the_page(self):
        draft = self.open_draft()
        response = self.client.post(
            reverse("battle:setup-add", args=[draft.pk]),
            {"kind": "character", "character_id": self.public_character.pk},
        )
        self.assertEqual(response.status_code, 403)
        draft.refresh_from_db()
        self.assertEqual(draft.roster_json, [])

    def test_the_setup_page_says_why_rather_than_hiding_the_control(self):
        draft = self.open_draft()
        response = self.client.get(reverse("battle:setup", args=[draft.pk]))
        self.assertContains(response, "Seat a saved character")
        self.assertContains(response, "As a guest you fight with generated fighters")
        self.assertContains(response, "guest-limit-characters")
        self.assertContains(response, "Import as a team")
        self.assertContains(response, "Parties are saved teams")
        # The picker itself is offered to nobody it would not work for.
        self.assertNotContains(response, self.public_character.name)

    def test_a_guest_cannot_import_a_party(self):
        from parties.models import Party

        party = Party.objects.create(name="The Company", owner=self.owner)
        draft = self.open_draft()
        response = self.client.post(
            reverse("battle:setup-import-party", args=[draft.pk]),
            {"party_id": party.pk},
        )
        self.assertEqual(response.status_code, 403)
        draft.refresh_from_db()
        self.assertEqual(draft.teams_json, [])

    def test_a_guest_may_still_name_a_team_that_lasts_one_battle(self):
        """The prohibition is on teams that *persist between battles* — a
        party. A team named on this muster dies with the battle, so it is
        ordinary arena play and stays open."""
        draft = self.open_draft()
        response = self.client.post(
            reverse("battle:setup-team-add", args=[draft.pk]), {"team_name": "Reds"}
        )
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.teams_json, ["Reds"])

    def test_the_service_refuses_a_saved_character_even_without_the_view(self):
        with self.assertRaises(ValueError) as caught:
            create_battle(
                None,
                guest_key="k",
                name="Sneaky",
                count=1,
                character_ids=(self.public_character.pk,),
                seed=7,
            )
        self.assertIn("Sign in", str(caught.exception))

    def test_a_guest_claiming_a_seat_may_only_generate(self):
        dm = self.create_dm()
        battle = create_battle(dm, name="Open", count=1, seed=8, player_seats=1)
        seat = battle.combatants.get(is_open_seat=True)
        with self.assertRaises(ValueError) as caught:
            claim_seat(
                battle.pk,
                seat.pk,
                None,
                guest_key="k",
                character_id=self.public_character.pk,
            )
        self.assertIn("Sign in", str(caught.exception))


class GuestAbuseBoundsTest(GuestClientMixin):
    """Public battle creation is an unauthenticated write, and is bounded."""

    def test_the_live_cap_stops_the_next_battle_and_says_so(self):
        for index in range(GUEST_LIVE_BATTLE_CAP):
            self.start_draft_as_guest(name=f"Bout {index}")
        response = self.client.post(reverse("battle:setup-create"))
        self.assertEqual(response.status_code, 429)
        self.assertIn("battles going as a guest", response.json()["error"])

    def test_a_finished_battle_stops_counting_against_the_cap(self):
        battles = [
            self.start_draft_as_guest(name=f"Bout {index}")
            for index in range(GUEST_LIVE_BATTLE_CAP)
        ]
        battles[0].status = Battle.STATUS_FINISHED
        battles[0].save(update_fields=["status"])
        self.assertEqual(
            self.client.post(reverse("battle:setup-create")).status_code, 302
        )

    def test_the_rate_limit_stops_a_burst_from_one_address(self):
        response = self.client.post(reverse("battle:setup-create"))
        self.assertEqual(response.status_code, 302)
        # Spend the rest of the window without accumulating live battles.
        for _ in range(GUEST_BATTLES_PER_WINDOW):
            charge_guest_creation(response.wsgi_request)
        BattleDraft.objects.all().delete()
        blocked = self.client.post(reverse("battle:setup-create"))
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("as many as guests get", blocked.json()["error"])

    def test_the_list_page_disables_the_button_and_gives_the_reason(self):
        for index in range(GUEST_LIVE_BATTLE_CAP):
            self.start_draft_as_guest(name=f"Bout {index}")
        response = self.client.get(reverse("battle:list"))
        self.assertContains(response, "battles going as a guest")
        self.assertContains(response, "disabled")

    def test_a_signed_in_player_is_not_rate_limited_as_a_guest(self):
        self.login_as(self.create_player())
        for _ in range(GUEST_LIVE_BATTLE_CAP + 2):
            self.assertEqual(
                self.client.post(reverse("battle:setup-create")).status_code, 302
            )


# --------------------------------------------------------------- journey


class GuestJourneyTest(GuestClientMixin):
    """Land, muster, start, fight — with no account and no passphrase."""

    def test_a_guest_reaches_the_arena_and_is_told_where_they_stand(self):
        response = self.client.get(reverse("battle:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You are playing as a guest")
        self.assertContains(response, "Nothing here is saved to an account")
        self.assertContains(response, reverse("login"))

    def test_browsing_the_list_issues_no_guest_key(self):
        """A key is minted where ownership begins, not where looking does."""
        self.client.get(reverse("battle:list"))
        self.assertEqual(self.guest_key(), "")

    def test_a_guest_starts_a_battle_and_runs_it(self):
        battle = self.start_draft_as_guest()
        self.assertIsNone(battle.created_by_id)
        self.assertEqual(battle.guest_key, self.guest_key())
        detail = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'id="advance-turn"')
        advanced = self.client.post(reverse("battle:advance", args=[battle.pk]))
        self.assertEqual(advanced.status_code, 200)
        battle.refresh_from_db()
        self.assertEqual(battle.turn_number, 1)
        self.assertTrue(battle.events.exists())

    def test_a_guest_may_restart_and_rename_in_their_own_battle(self):
        battle = self.start_draft_as_guest()
        self.client.post(reverse("battle:advance", args=[battle.pk]))
        row = battle.combatants.first()
        renamed = self.client.post(
            reverse("battle:rename", args=[battle.pk]),
            {"combatant_id": row.pk, "name": "Torin Blackbriar"},
            content_type="application/json",
        )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(
            self.client.post(reverse("battle:restart", args=[battle.pk])).status_code,
            200,
        )

    def test_a_guest_takes_a_seat_and_plays_the_figure_themselves(self):
        draft = self.open_draft()
        self.muster_two_fighters(draft)
        self.client.post(
            reverse("battle:setup-add", args=[draft.pk]), {"kind": "open_seat"}
        )
        self.client.post(
            reverse("battle:setup-start", args=[draft.pk]), {"name": "Seated Bout"}
        )
        battle = Battle.objects.get(name="Seated Bout")
        self.assertEqual(battle.status, Battle.STATUS_LOBBY)
        seat = battle.combatants.get(is_open_seat=True)
        lobby = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertContains(lobby, "Sign in")
        self.assertContains(lobby, "generate a fighter below")
        claimed = self.client.post(
            reverse("battle:seat-claim", args=[battle.pk, seat.pk]),
            {"archetype": "archer"},
        )
        self.assertEqual(claimed.status_code, 302)
        seat.refresh_from_db()
        self.assertIsNone(seat.player_id)
        self.assertIsNone(seat.character_id)
        self.assertEqual(seat.guest_key, self.guest_key())
        self.assertTrue(seat.snapshot_json)
        # The claimed seat is no longer on offer to the next passer-by.
        stranger = Client()
        offered = stranger.get(reverse("battle:detail", args=[battle.pk]))
        self.assertNotContains(offered, "Claim this seat")
        # Ready, then start: the guest's own seat survives the lobby close.
        self.client.post(
            reverse("battle:seat-ready", args=[battle.pk, seat.pk]), {"ready": "1"}
        )
        self.client.post(reverse("battle:start", args=[battle.pk]))
        battle.refresh_from_db()
        self.assertEqual(battle.status, Battle.STATUS_SETUP)
        self.assertTrue(battle.combatants.filter(pk=seat.pk).exists())

    def test_an_unclaimed_seat_still_leaves_the_roster_at_the_start(self):
        draft = self.open_draft()
        self.muster_two_fighters(draft)
        self.client.post(
            reverse("battle:setup-add", args=[draft.pk]), {"kind": "open_seat"}
        )
        self.client.post(
            reverse("battle:setup-start", args=[draft.pk]), {"name": "Empty Seat"}
        )
        battle = Battle.objects.get(name="Empty Seat")
        self.client.post(reverse("battle:start", args=[battle.pk]))
        self.assertFalse(battle.combatants.filter(is_open_seat=True).exists())


class GuestListScopeTest(GuestClientMixin):
    """What a viewer is offered is narrower than what they may open."""

    def test_a_guest_is_offered_their_own_battles_only(self):
        mine = self.start_draft_as_guest(name="Mine")
        other_client = Client()
        theirs = self.start_draft_as_guest(name="Theirs", client=other_client)
        dm_battle = create_battle(self.create_dm(), name="DM Pit", count=2, seed=2)
        response = self.client.get(reverse("battle:list"))
        self.assertContains(response, mine.name)
        self.assertNotContains(response, theirs.name)
        self.assertNotContains(response, dm_battle.name)

    def test_a_signed_in_player_is_not_offered_a_guests_battle_but_may_open_it(self):
        guest_battle = self.start_draft_as_guest(name="Anon Bout")
        player_client = Client()
        player = self.create_player()
        player_client.force_login(player)
        listing = player_client.get(reverse("battle:list"))
        self.assertNotContains(listing, guest_battle.name)
        opened = player_client.get(reverse("battle:detail", args=[guest_battle.pk]))
        self.assertEqual(opened.status_code, 200)

    def test_a_dm_is_offered_everything_including_guest_battles(self):
        guest_battle = self.start_draft_as_guest(name="Anon Bout")
        dm_client = Client()
        dm_client.force_login(self.create_dm())
        listing = dm_client.get(reverse("battle:list"))
        self.assertContains(listing, guest_battle.name)


class GuestAdoptionTest(GuestClientMixin):
    """Registering keeps the battle you were already playing."""

    def test_registering_adopts_the_battle_the_seat_and_the_draft(self):
        battle = self.start_draft_as_guest(name="Kept Bout")
        draft = self.open_draft()
        self.assertNotEqual(self.guest_key(), "")
        response = self.client.post(
            reverse("register"),
            {
                "username": "newcomer",
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 302)
        battle.refresh_from_db()
        draft.refresh_from_db()
        self.assertEqual(battle.created_by.username, "newcomer")
        self.assertEqual(battle.guest_key, "")
        self.assertEqual(draft.created_by.username, "newcomer")
        self.assertNotIn(GUEST_KEY_SESSION_FIELD, self.client.session)
        # And it is now on their list, where a guest battle would not be.
        listing = self.client.get(reverse("battle:list"))
        self.assertContains(listing, "Kept Bout")
        self.assertNotContains(listing, "You are playing as a guest")

    def test_signing_in_adopts_a_guest_seat(self):
        dm = self.create_dm()
        battle = create_battle(dm, name="Public Lobby", count=1, seed=9, player_seats=1)
        seat = battle.combatants.get(is_open_seat=True)
        claim_seat(battle.pk, seat.pk, None, guest_key="visitor", archetype="archer")
        session = self.client.session
        session[GUEST_KEY_SESSION_FIELD] = "visitor"
        session.save()
        player = self.create_player()
        self.client.post(
            reverse("login"),
            {"username": player.username, "password": TEST_PASSWORD},
        )
        seat.refresh_from_db()
        self.assertEqual(seat.player_id, player.pk)
        self.assertEqual(seat.guest_key, "")

    def test_adoption_never_gives_one_person_two_seats_in_a_battle(self):
        dm = self.create_dm()
        battle = create_battle(dm, name="Two Seats", count=1, seed=10, player_seats=2)
        first, second = list(battle.combatants.filter(is_open_seat=True))
        player = self.create_player()
        claim_seat(battle.pk, first.pk, player, archetype="archer")
        claim_seat(battle.pk, second.pk, None, guest_key="visitor", archetype="wizard")
        session = self.client.session
        session[GUEST_KEY_SESSION_FIELD] = "visitor"
        session.save()
        self.client.post(
            reverse("login"),
            {"username": player.username, "password": TEST_PASSWORD},
        )
        second.refresh_from_db()
        self.assertIsNone(second.player_id)
        self.assertEqual(second.guest_key, "visitor")

    def test_adoption_saves_no_characters(self):
        before = Character.objects.count()
        self.start_draft_as_guest(name="Kept Bout")
        self.client.post(
            reverse("register"),
            {
                "username": "newcomer",
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD,
            },
        )
        self.assertEqual(Character.objects.count(), before)


class ActorEdgeTest(BaseTestCase):
    """The small no-op paths, asserted rather than assumed."""

    def test_holds_seat_is_false_for_no_seat_at_all(self):
        player = self.create_player()
        self.assertFalse(Actor(user=player, guest_key="").holds_seat(None))
        self.assertFalse(Actor(user=None, guest_key="k").holds_seat(None))

    def test_adopting_with_no_guest_key_moves_nothing(self):
        from battle.adoption import adopt_guest_rows

        counts = adopt_guest_rows(self.create_player(), "")
        self.assertEqual(counts, {"battles": 0, "drafts": 0, "seats": 0})

    def test_a_login_without_a_request_is_ignored(self):
        """``user_logged_in`` carries ``request=None`` when something signs a
        user in outside a request (a management command, a test helper)."""
        from battle.adoption import adopt_on_login

        self.assertIsNone(
            adopt_on_login(sender=None, request=None, user=self.create_player())
        )

    def test_signing_in_with_no_guest_battles_is_a_no_op(self):
        player = self.create_player()
        response = self.client.post(
            reverse("login"),
            {"username": player.username, "password": TEST_PASSWORD},
        )
        self.assertEqual(response.status_code, 302)
