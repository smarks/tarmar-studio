"""The battle list is paged like the other lists (issue #795).

It rendered every battle ever made. It shows ``common.pagination.PAGE_SIZE``
rows and the shared pager now, and the pager's links keep the status filter.
The pickers #795 also names are not paged here: a picker whose rows sit on
other pages cannot be chosen from by typing or selecting, which is a
regression for the DM it was meant to help, so they wait on a search that
reaches every row (filed as a follow-up).
"""

from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from battle.services import create_battle
from common.pagination import PAGE_SIZE, query_without
from common.test_helpers import BaseTestCase

ONE_PAGE_MORE = PAGE_SIZE + 1


class QueryWithoutTest(SimpleTestCase):
    def request(self, query: str):
        return RequestFactory().get(f"/list/?{query}")

    def test_query_without_drops_only_the_named_parameters(self):
        self.assertEqual(
            query_without(self.request("page=2&show=lobby"), "page"), "show=lobby"
        )


class BattleListPagesTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.player = self.create_player()

    def assert_paged(self, response, label: str, param: str) -> None:
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'aria-label="{label}"')
        self.assertContains(response, f"{param}=2")

    def test_the_battle_list_is_paged_and_keeps_its_filter(self):
        for number in range(ONE_PAGE_MORE):
            create_battle(self.dm, name=f"Pit {number:02d}", count=2, seed=number)
        self.login_as(self.dm)
        response = self.client.get(reverse("battle:list"), {"show": "running"})
        self.assert_paged(response, "Battle list pages", "page")
        self.assertContains(response, "?show=running&amp;page=2")
        self.assertEqual(len(response.context["battles"]), PAGE_SIZE)

    def test_the_newest_battle_is_first_on_page_one(self):
        """The Count annotations drop Meta.ordering, so the list is ordered
        in the view; without it the newest of 55 landed on page 2."""
        for number in range(55):
            create_battle(self.dm, name=f"Pit {number:02d}", count=2, seed=number)
        self.login_as(self.dm)
        first_page = self.client.get(reverse("battle:list"))
        names = [battle.name for battle in first_page.context["battles"]]
        self.assertEqual(names[0], "Pit 54")
        self.assertEqual(len(names), PAGE_SIZE)
        second_page = self.client.get(reverse("battle:list"), {"page": 2})
        rest = [battle.name for battle in second_page.context["battles"]]
        self.assertEqual(rest, [f"Pit {number:02d}" for number in range(4, -1, -1)])

    def test_a_short_battle_list_shows_no_pager(self):
        create_battle(self.dm, name="Lone", count=2, seed=1)
        self.login_as(self.dm)
        response = self.client.get(reverse("battle:list"))
        self.assertNotContains(response, 'class="pagination"')
