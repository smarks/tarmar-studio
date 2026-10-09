"""The site-wide navigation trail (issue #189)."""

from django.urls import reverse

from common.breadcrumbs import filtered, step, trail
from common.test_helpers import BaseTestCase, create_character


class TrailTest(BaseTestCase):
    def test_a_trail_is_rooted_at_the_tarmar_home(self):
        steps = trail(step("Characters", reverse("characters:list")))
        self.assertEqual([entry["label"] for entry in steps], ["Tarmar", "Characters"])
        self.assertEqual(steps[0]["url"], reverse("home"))

    def test_the_last_step_never_links_to_itself(self):
        steps = trail(
            step("Characters", reverse("characters:list")),
            step("Mira", "/somewhere/"),
        )
        self.assertEqual(steps[-1], {"label": "Mira", "url": ""})
        # The step above it keeps its link.
        self.assertEqual(steps[1]["url"], reverse("characters:list"))

    def test_a_rootless_trail_omits_the_home_step(self):
        steps = trail(step("Gate"), root=False)
        self.assertEqual([entry["label"] for entry in steps], ["Gate"])

    def test_an_empty_rootless_trail_stays_empty(self):
        self.assertEqual(trail(root=False), [])

    def test_filtered_carries_the_filter_and_drops_blanks(self):
        self.assertEqual(
            filtered("characters:list", show_deleted="1", user=""),
            f"{reverse('characters:list')}?show_deleted=1",
        )
        self.assertEqual(filtered("characters:list"), reverse("characters:list"))


class RenderedTrailTest(BaseTestCase):
    """base.html renders the trail for any view that supplies one."""

    def test_a_page_with_a_trail_renders_it_as_navigation(self):
        self.login_as(self.create_player())
        response = self.client.get(reverse("characters:list"))
        self.assertContains(response, 'class="breadcrumb"')
        self.assertContains(response, 'aria-label="Breadcrumb"')
        self.assertContains(response, 'aria-current="page"')

    def test_the_home_page_is_the_root_and_carries_no_trail(self):
        self.login_as(self.create_player())
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, 'class="breadcrumb"')


class PageTrailTest(BaseTestCase):
    """Every page below home names where it sits (issue #189)."""

    def assertTrail(self, url, expected_labels):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        self.assertEqual(
            [entry["label"] for entry in response.context["breadcrumb"]],
            expected_labels,
            url,
        )

    def test_player_pages(self):
        self.login_as(self.create_player())
        self.assertTrail(reverse("characters:list"), ["Tarmar", "Characters"])
        self.assertTrail(
            reverse("characters:create"), ["Tarmar", "Characters", "New character"]
        )
        self.assertTrail(reverse("profile"), ["Tarmar", "Profile"])
        self.assertTrail(reverse("about"), ["Tarmar", "About"])
        self.assertTrail(reverse("reference:index"), ["Tarmar", "Reference"])

    def test_dm_pages(self):
        self.login_as(self.create_dm())
        self.assertTrail(reverse("users_list"), ["Tarmar", "Users"])
        self.assertTrail(reverse("battle:list"), ["Tarmar", "Battles"])
        self.assertTrail(reverse("ruins:generate"), ["Tarmar", "Ruin generator"])

    def test_a_character_sheet_trails_back_to_the_list(self):
        player = self.create_player()
        self.login_as(player)
        character = create_character(player, name="Mira Quickhand")
        response = self.client.get(reverse("characters:edit", args=[character.pk]))
        steps = response.context["breadcrumb"]
        self.assertEqual(
            [entry["label"] for entry in steps],
            ["Tarmar", "Characters", "Mira Quickhand"],
        )
        self.assertEqual(steps[1]["url"], reverse("characters:list"))

    def test_a_battle_trails_back_to_the_battle_list(self):
        dm = self.create_dm()
        self.login_as(dm)
        from battle.services import create_battle

        battle = create_battle(dm, name="Ashfall", count=2, seed=4)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        steps = response.context["breadcrumb"]
        self.assertEqual(
            [entry["label"] for entry in steps], ["Tarmar", "Battles", "Ashfall"]
        )
        self.assertEqual(steps[1]["url"], reverse("battle:list"))

    def test_deleted_characters_trails_through_the_character_list(self):
        dm = self.create_dm()
        self.login_as(dm)
        response = self.client.get(reverse("characters:deleted"))
        self.assertEqual(
            [entry["label"] for entry in response.context["breadcrumb"]],
            ["Tarmar", "Characters", "Deleted"],
        )
