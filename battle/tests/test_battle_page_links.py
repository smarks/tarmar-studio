"""The started battle page's list link says where it goes (issue #339, item 30).

The controller's secondary actions carried a link labelled "New battle" that
went to the battle list, beside viewers' identical "All battles" link. It is
"All battles" for everyone now; the list is where a new battle starts.
"""

from django.urls import reverse

from battle.services import create_battle
from common.test_helpers import BaseTestCase


class StartedBattleLinksTest(BaseTestCase):
    def test_the_controller_is_offered_all_battles_not_new_battle(self):
        dm = self.create_dm()
        battle = create_battle(dm, name="Links", count=2, seed=21)
        self.login_as(dm)

        response = self.client.get(reverse("battle:detail", args=[battle.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, ">All battles</a>")
        self.assertNotContains(response, ">New battle</a>")
