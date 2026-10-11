"""Searchable pickers (issue #864).

Spencer's ruling, 2026-10-09: "the nine pickers get a search box that queries
the server (name, owner) with results paged at PAGE_SIZE, newest first so a
just-made character is on page one; the plain selects become searchable
pickers."

The shared part (``common.pickers``: search, order, page, pin) is tested once
here; then each picker: its page renders the first page newest first, its
endpoint finds a row on any page by name and by owner, and the endpoint is
guarded as the page and the post behind it are.
"""

import re
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from battle.models import BattleDraft
from battle.tests.test_lobby import open_battle
from characters.models import Character
from common.pagination import PAGE_SIZE
from common.pickers import (
    PICKER_QUERY_MAX_LENGTH,
    PickerSource,
    clean_query,
    picker_page,
)
from common.test_helpers import BaseTestCase, create_character
from gm.models import GMSession
from parties.models import Party, PartyMembership

#: One page and then some, so a row can sit on page 2.
MORE_THAN_A_PAGE = PAGE_SIZE + 5

SOURCE = PickerSource(
    rows_template="battle/_claim_options.html",
    search_label="Search",
    placeholder="Search",
    search_fields=("name", "user__username"),
    order_by=("-created_at", "-pk"),
    noun="character",
    noun_plural="characters",
)


def make_characters(owner, count: int, prefix: str = "Soldier") -> list[Character]:
    """``count`` characters, each a minute newer than the last; oldest first."""
    start = timezone.now() - timedelta(days=1)
    made = []
    for number in range(count):
        character = create_character(owner, name=f"{prefix} {number:03d}")
        Character.objects.filter(pk=character.pk).update(
            created_at=start + timedelta(minutes=number)
        )
        made.append(character)
    return made


class PickerPageTest(BaseTestCase):
    """Search, order and page: what every picker shares."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("picker-owner")
        self.made = make_characters(self.owner, MORE_THAN_A_PAGE)

    def page(self, **kwargs):
        return picker_page(
            SOURCE,
            Character.objects.filter(user=self.owner),
            element_id="test-picker",
            url="/picker/",
            **kwargs,
        )

    def test_the_first_page_is_the_newest_page_size(self):
        first = self.page()
        self.assertEqual(len(first.rows), PAGE_SIZE)
        self.assertEqual(first.rows[0].pk, self.made[-1].pk)
        self.assertEqual(first.total, MORE_THAN_A_PAGE)
        self.assertEqual(first.search_id, "test-picker-search")

    def test_the_second_page_holds_the_rest_and_a_bad_page_is_the_nearest(self):
        second = self.page(page_number="2")
        self.assertEqual(
            [row.pk for row in second.rows],
            [character.pk for character in reversed(self.made[:5])],
        )
        self.assertEqual(self.page(page_number="nonsense").page.number, 1)
        self.assertEqual(self.page(page_number="99").page.number, 2)

    def test_a_query_matches_name_or_owner_on_every_page(self):
        oldest = self.made[0]
        found = self.page(query="  soldier 000 ")
        self.assertEqual([row.pk for row in found.rows], [oldest.pk])
        self.assertEqual(found.query, "soldier 000")
        self.assertEqual(self.page(query="PICKER-OWN").total, MORE_THAN_A_PAGE)
        self.assertEqual(self.page(query="nobody by this name").total, 0)

    def test_a_pinned_row_is_not_repeated_among_the_rows(self):
        newest = self.made[-1]
        pinned = self.page(pinned=[newest])
        self.assertEqual(list(pinned.pinned), [newest])
        self.assertNotIn(newest.pk, [row.pk for row in pinned.rows])

    def test_a_query_is_trimmed_and_capped(self):
        self.assertEqual(clean_query(None), "")
        self.assertEqual(
            len(clean_query("x" * (PICKER_QUERY_MAX_LENGTH + 50))),
            PICKER_QUERY_MAX_LENGTH,
        )


class SetupPickersTest(BaseTestCase):
    """The battle setup shelf and its party select."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.login_as(self.dm)
        self.draft = BattleDraft.objects.create(created_by=self.dm, name="Drafted")
        self.made = make_characters(self.dm, MORE_THAN_A_PAGE)

    def picker(self, kind: str, **query):
        return self.client.get(
            reverse("battle:setup-picker", args=[self.draft.pk, kind]), query
        )

    def test_the_page_shows_the_newest_page_with_its_search_box(self):
        just_made = create_character(self.dm, name="Just Made")
        page = self.client.get(reverse("battle:setup", args=[self.draft.pk]))
        content = page.content.decode()
        self.assertIn('id="character-filter"', content)
        self.assertIn("Just Made", content)
        self.assertNotIn(self.made[0].name, content)
        self.assertEqual(page.context["shelf_picker"].rows[0].pk, just_made.pk)
        self.assertIn(f"{MORE_THAN_A_PAGE + 1} characters, newest first", content)

    def test_a_search_reaches_a_character_on_page_two(self):
        oldest = self.made[0]
        response = self.picker("characters", q=oldest.name)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(oldest.name, content)
        self.assertIn(f'value="{oldest.pk}"', content)
        self.assertIn(reverse("battle:setup-add", args=[self.draft.pk]), content)

    def test_the_pager_turns_to_page_two(self):
        content = self.picker("characters", page="2").content.decode()
        self.assertIn(self.made[0].name, content)
        self.assertIn("Page 2 of 2", content)

    def test_parties_are_searched_by_owner_and_newest_first(self):
        owner = self.create_user("captain")
        Party.objects.create(name="Old Company", owner=owner)
        Party.objects.create(name="New Company", owner=self.dm)
        page = self.client.get(reverse("battle:setup", args=[self.draft.pk]))
        self.assertEqual(
            [party.name for party in page.context["party_picker"].rows],
            ["New Company", "Old Company"],
        )
        content = self.picker("parties", q="captain").content.decode()
        self.assertIn("Old Company", content)
        self.assertNotIn("New Company", content)
        self.assertIn('name="party_id"', content)

    def test_someone_elses_draft_and_an_unknown_picker_are_404(self):
        other = self.create_user("stranger")
        theirs = BattleDraft.objects.create(created_by=other, name="Theirs")
        response = self.client.get(
            reverse("battle:setup-picker", args=[theirs.pk, "characters"])
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.picker("beasts").status_code, 404)


class LobbyClaimPickerTest(BaseTestCase):
    """The lobby's "Bring one of your characters" select."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.player = self.create_player()
        self.battle = open_battle(self.dm, seats=2, count=1)
        self.login_as(self.player)

    def test_each_open_seat_has_its_own_search_over_the_players_own(self):
        made = make_characters(self.player, MORE_THAN_A_PAGE)
        create_character(self.dm, name="Not Yours")
        page = self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        content = page.content.decode()
        self.assertEqual(content.count('class="picker-search"'), 2)
        self.assertIn(made[-1].name, content)
        self.assertNotIn(made[0].name, content)

        response = self.client.get(
            reverse("battle:claim-picker", args=[self.battle.pk]), {"q": made[0].name}
        )
        self.assertContains(response, f'<option value="{made[0].pk}">')
        others = self.client.get(
            reverse("battle:claim-picker", args=[self.battle.pk]), {"q": "Not Yours"}
        )
        self.assertNotContains(others, "Not Yours</option>")


class PartyAddablePickerTest(BaseTestCase):
    """The party page's Add Members shelf."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("party-owner")
        self.party = Party.objects.create(name="The Wardens", owner=self.owner)
        self.made = make_characters(self.owner, MORE_THAN_A_PAGE)
        self.login_as(self.owner)

    def test_a_search_reaches_page_two_and_members_stay_out(self):
        member = self.made[1]
        PartyMembership.objects.create(party=self.party, character=member)
        url = reverse("parties:addable_picker", args=[self.party.pk])
        response = self.client.get(url, {"q": self.made[0].name})
        self.assertContains(response, self.made[0].name)
        self.assertContains(
            response, reverse("parties:add_member", args=[self.party.pk])
        )
        searched = self.client.get(url, {"q": member.name})
        self.assertNotContains(searched, f'value="{member.pk}"')
        self.assertContains(searched, "No characters match")

    def test_the_page_offers_the_newest_with_the_filter_box(self):
        page = self.client.get(reverse("parties:detail", args=[self.party.pk]))
        self.assertContains(page, 'id="party-character-filter"')
        self.assertContains(page, self.made[-1].name)
        self.assertNotContains(page, self.made[0].name)

    def test_a_reader_who_may_not_manage_is_refused(self):
        self.logout()
        self.login_as(self.create_user("bystander"))
        response = self.client.get(
            reverse("parties:addable_picker", args=[self.party.pk])
        )
        self.assertEqual(response.status_code, 403)


class HookPartyPickerTest(BaseTestCase):
    """The hook generator's party select."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.login_as(self.dm)
        self.parties = []
        start = timezone.now() - timedelta(days=1)
        for number in range(MORE_THAN_A_PAGE):
            party = Party.objects.create(name=f"Band {number:03d}", owner=self.dm)
            Party.objects.filter(pk=party.pk).update(
                created_at=start + timedelta(minutes=number)
            )
            self.parties.append(party)

    def test_the_chosen_party_stays_selected_off_the_first_page(self):
        oldest = self.parties[0]
        page = self.client.get(reverse("hooks:generate"), {"party": oldest.pk})
        self.assertContains(page, f'<option value="{oldest.pk}" selected>')
        self.assertContains(page, self.parties[-1].name)

    def test_a_search_keeps_the_chosen_party_chosen(self):
        oldest = self.parties[0]
        page = self.client.get(reverse("hooks:generate"), {"party": oldest.pk})
        picker = page.context["party_picker"]
        self.assertEqual(
            picker.url, f"{reverse('hooks:party_picker')}?party={oldest.pk}"
        )

        # What picker.js asks for: the picker's URL, plus what was typed.
        searched = self.client.get(picker.url + "&q=Band+054")

        self.assertContains(searched, f'<option value="{oldest.pk}" selected>')
        self.assertContains(searched, "Band 054")

    def test_a_search_reaches_page_two_for_a_game_master_only(self):
        url = reverse("hooks:party_picker")
        self.assertContains(self.client.get(url, {"q": "Band 000"}), "Band 000")
        self.logout()
        self.login_as(self.create_player())
        self.assertEqual(self.client.get(url).status_code, 403)


class GameMasterPickersTest(BaseTestCase):
    """The session rail, its menu, and the Characters tab's attach select."""

    def setUp(self):
        super().setUp()
        self.player = self.create_player()
        self.login_as(self.player)

    def test_the_rail_finds_an_old_session_and_keeps_the_open_one(self):
        start = timezone.now() - timedelta(days=2)
        sessions = []
        for number in range(MORE_THAN_A_PAGE):
            session = GMSession.objects.create(
                name=f"Night {number:03d}", user=self.player
            )
            GMSession.objects.filter(pk=session.pk).update(
                updated_at=start + timedelta(minutes=number)
            )
            sessions.append(session)
        oldest = sessions[0]

        page = self.client.get(reverse("gm:chat"), {"session": oldest.pk})
        content = page.content.decode()
        self.assertIn('id="session-list"', content)
        self.assertIn('id="mobile-session-select"', content)
        # The open session is listed (and marked) though it is the oldest.
        self.assertIn(f'data-id="{oldest.pk}"', content)
        self.assertIn(f'<option value="{oldest.pk}"', content)

        rail = self.client.get(
            reverse("gm:session_picker", args=["rail"]), {"q": "night 001"}
        )
        self.assertContains(rail, f'data-id="{sessions[1].pk}"')
        menu = self.client.get(reverse("gm:session_picker", args=["menu"]), {"page": 2})
        self.assertContains(menu, f'<option value="{sessions[1].pk}"')

    def test_another_players_sessions_are_neither_listed_nor_pinned(self):
        stranger = self.create_user("stranger")
        theirs = GMSession.objects.create(name="Not Yours", user=stranger)
        rail = self.client.get(reverse("gm:session_picker", args=["rail"]))
        self.assertNotContains(rail, "Not Yours")
        pinned = self.client.get(
            reverse("gm:session_picker", args=["rail"]), {"active": theirs.pk}
        )
        self.assertEqual(pinned.status_code, 404)
        self.assertEqual(
            self.client.get(reverse("gm:session_picker", args=["list"])).status_code,
            404,
        )

    def test_the_attach_picker_reaches_page_two_and_leaves_out_the_attached(self):
        made = make_characters(self.player, MORE_THAN_A_PAGE)
        session = GMSession.objects.create(name="Table", user=self.player)
        session.characters.add(made[1])
        url = reverse("gm:attach_picker")

        found = self.client.get(url, {"session": session.pk, "q": made[0].name})
        self.assertContains(
            found, f'<option value="{made[0].pk}">{made[0].name}</option>'
        )
        attached = self.client.get(url, {"session": session.pk, "q": made[1].name})
        self.assertContains(attached, "No character matches")

        page = self.client.get(reverse("gm:chat"), {"session": session.pk})
        self.assertEqual(page.context["attachable_characters"][0].pk, made[-1].pk)

    def test_the_attach_picker_refuses_someone_elses_session(self):
        theirs = GMSession.objects.create(name="Theirs", user=self.create_user("other"))
        response = self.client.get(reverse("gm:attach_picker"), {"session": theirs.pk})
        self.assertEqual(response.status_code, 404)


class DisbandedPartiesAreOfferedNowhereTest(BaseTestCase):
    """Issue #971: battle setup and the hook generator offered disbanded parties.

    A disbanded party is a soft delete; the party list leaves it out, and so
    must every picker that offers parties and every endpoint behind one. A
    disbanded party named by hand is refused with the reason.
    """

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.login_as(self.dm)
        self.live = Party.objects.create(name="Standing Company", owner=self.dm)
        self.disbanded = Party.objects.create(
            name="Broken Company", owner=self.dm, is_deleted=True
        )
        for party in (self.live, self.disbanded):
            PartyMembership.objects.create(
                party=party, character=create_character(self.dm, name=party.name)
            )
        self.draft = BattleDraft.objects.create(created_by=self.dm, name="Drafted")

    def test_battle_setup_offers_and_finds_only_the_live_party(self):
        page = self.client.get(reverse("battle:setup", args=[self.draft.pk]))
        self.assertEqual(
            [party.pk for party in page.context["party_picker"].rows], [self.live.pk]
        )
        searched = self.client.get(
            reverse("battle:setup-picker", args=[self.draft.pk, "parties"]),
            {"q": "Company"},
        )
        self.assertContains(searched, f'value="{self.live.pk}"')
        self.assertNotContains(searched, f'value="{self.disbanded.pk}"')

    def test_battle_setup_refuses_a_disbanded_party_posted_by_hand(self):
        response = self.client.post(
            reverse("battle:setup-import-party", args=[self.draft.pk]),
            {"party_id": self.disbanded.pk},
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Broken Company was disbanded", status_code=400)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.roster_json, [])

    def test_the_live_party_still_imports(self):
        response = self.client.post(
            reverse("battle:setup-import-party", args=[self.draft.pk]),
            {"party_id": self.live.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.teams_json, ["Standing Company"])

    def test_the_hook_generator_offers_and_finds_only_the_live_party(self):
        page = self.client.get(reverse("hooks:generate"))
        self.assertEqual(
            [party.pk for party in page.context["party_picker"].rows], [self.live.pk]
        )
        searched = self.client.get(reverse("hooks:party_picker"), {"q": "Company"})
        self.assertContains(searched, "Standing Company")
        self.assertNotContains(searched, "Broken Company")

    def test_the_hook_generator_refuses_a_disbanded_party_in_the_address(self):
        response = self.client.get(
            reverse("hooks:generate"), {"party": self.disbanded.pk, "seed": "9"}
        )
        self.assertContains(response, "Broken Company was disbanded")
        self.assertNotContains(response, 'id="hook-result"')
        # Nor is it pinned into the select as the party chosen.
        self.assertEqual(list(response.context["party_picker"].pinned), [])


def make_sessions(owner, count: int, prefix: str = "Night") -> list[GMSession]:
    """``count`` sessions, each played a minute after the last; oldest first."""
    start = timezone.now() - timedelta(days=2)
    made = []
    for number in range(count):
        session = GMSession.objects.create(name=f"{prefix} {number:03d}", user=owner)
        GMSession.objects.filter(pk=session.pk).update(
            updated_at=start + timedelta(minutes=number)
        )
        made.append(session)
    return made


class SheetSessionPickerTest(BaseTestCase):
    """Issue #973: the sheet's "Take <name> to" select listed every session."""

    def setUp(self):
        super().setUp()
        self.player = self.create_player()
        self.character = create_character(self.player, name="Wren")
        self.login_as(self.player)
        self.sessions = make_sessions(self.player, MORE_THAN_A_PAGE)

    def picker(self, **query):
        return self.client.get(
            reverse("gm:play_session_picker"),
            {"character": self.character.pk, **query},
        )

    def test_the_sheet_offers_the_most_recently_played_page_with_a_search(self):
        page = self.client.get(reverse("characters:edit", args=[self.character.pk]))
        content = page.content.decode()
        self.assertIn(f'id="play-with-gm-search-{self.character.pk}"', content)
        self.assertIn('<option value="">A new session</option>', content)
        self.assertIn(f'<option value="{self.sessions[-1].pk}">', content)
        self.assertNotIn(self.sessions[0].name, content)
        self.assertIn(
            f"{MORE_THAN_A_PAGE} sessions, most recently played first", content
        )
        newest_first = [session.pk for session in reversed(self.sessions)]
        select = re.search(
            rf'<select id="play-with-gm-session-{self.character.pk}".*?</select>',
            content,
            re.DOTALL,
        )
        assert select is not None
        offered = [
            int(pk) for pk in re.findall(r'<option value="(\d+)">', select.group(0))
        ]
        self.assertEqual(offered, newest_first[:PAGE_SIZE])

    def test_a_search_reaches_a_session_on_page_two_and_marks_a_holder(self):
        oldest = self.sessions[0]
        oldest.characters.add(self.character)
        found = self.picker(q=oldest.name)
        self.assertContains(
            found,
            f'<option value="{oldest.pk}">{oldest.name} (already playing here)</option>',
        )
        self.assertContains(found, '<option value="">A new session</option>')
        self.assertContains(found, f'id="play-with-gm-session-{self.character.pk}"')
        self.assertContains(self.picker(page="2"), "Page 2 of 2")

    def test_another_accounts_sessions_are_never_offered(self):
        stranger = self.create_user("stranger")
        GMSession.objects.create(name="Not Yours", user=stranger)
        self.assertNotContains(self.picker(q="Not Yours"), "Not Yours</option>")

    def test_the_endpoint_refuses_what_the_attach_refuses(self):
        """Guarded as gm_characters_attach: the same refusal, the same status."""
        theirs = create_character(self.create_user("other"), name="Theirs")
        fallen = create_character(self.player, name="Fallen", is_alive=False)
        for character, status in ((theirs, 404), (fallen, 400)):
            with self.subTest(character=character.name):
                response = self.client.get(
                    reverse("gm:play_session_picker"), {"character": character.pk}
                )
                self.assertEqual(response.status_code, status)
        missing = self.client.get(reverse("gm:play_session_picker"))
        self.assertEqual(missing.status_code, 400)

    def test_a_game_master_searches_their_own_sessions_for_a_players_character(self):
        game_master = self.create_dm("table-gm")
        mine = GMSession.objects.create(name="GM Table", user=game_master)
        self.logout()
        self.login_as(game_master)
        found = self.picker(q="table")
        self.assertContains(found, f'<option value="{mine.pk}">GM Table</option>')
        self.assertNotContains(found, "Night 000")


class OwnerFilterPickerTest(BaseTestCase):
    """Issue #973: the character list's owner filter listed every account."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.login_as(self.dm)
        start = timezone.now() - timedelta(days=3)
        self.accounts = []
        for number in range(MORE_THAN_A_PAGE):
            account = self.create_user(f"owner{number:03d}")
            type(account).objects.filter(pk=account.pk).update(
                date_joined=start + timedelta(minutes=number)
            )
            self.accounts.append(account)

    def test_the_list_offers_the_newest_accounts_with_a_search(self):
        page = self.client.get(reverse("characters:list"))
        content = page.content.decode()
        self.assertIn('id="owner-filter-search"', content)
        self.assertIn('id="user-filter"', content)
        self.assertIn(">owner" + f"{MORE_THAN_A_PAGE - 1:03d}</option>", content)
        self.assertNotIn(">owner000</option>", content)
        self.assertEqual(page.context["owner_picker"].rows[0], self.dm)

    def test_a_search_reaches_page_two_and_leaves_out_inactive_accounts(self):
        oldest = self.accounts[0]
        url = reverse("characters:owner_picker")
        self.assertContains(
            self.client.get(url, {"q": "owner000"}),
            f'<option value="{oldest.pk}">owner000</option>',
        )
        type(oldest).objects.filter(pk=oldest.pk).update(is_active=False)
        self.assertContains(
            self.client.get(url, {"q": "owner000"}), "No accounts match"
        )

    def test_the_owner_the_list_is_filtered_by_stays_chosen(self):
        oldest = self.accounts[0]
        page = self.client.get(reverse("characters:list"), {"user": oldest.pk})
        self.assertContains(page, f'<option value="{oldest.pk}" selected>')
        picker = page.context["owner_picker"]
        self.assertEqual(
            picker.url, f"{reverse('characters:owner_picker')}?user={oldest.pk}"
        )
        # What picker.js asks for: the picker's URL, plus what was typed.
        searched = self.client.get(picker.url + "&q=owner050")
        self.assertContains(searched, f'<option value="{oldest.pk}" selected>')
        self.assertContains(searched, ">owner050</option>")

    def test_a_player_is_refused_as_the_filter_refuses_them(self):
        self.logout()
        player = self.create_player()
        self.login_as(player)
        self.assertEqual(
            self.client.get(reverse("characters:owner_picker")).status_code, 403
        )
        page = self.client.get(reverse("characters:list"))
        self.assertNotContains(page, 'id="user-filter"')
