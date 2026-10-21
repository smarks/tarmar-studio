"""View tests: DM gating, the seed-in-URL flow, rendering, and error states."""

from urllib.parse import parse_qs, urlparse

from django.urls import reverse
from django.utils.html import escape

from common.refusal_words import refusal_words as common_words
from common.test_helpers import BaseTestCase, sentence_parts
from ruins.generation import MAX_ROOMS, generate_ruin


class RuinsViewTestCase(BaseTestCase):
    pass


class GatingTest(RuinsViewTestCase):
    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse("ruins:generate"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_player_is_refused(self):
        self.login_as(self.create_player())
        response = self.client.get(reverse("ruins:generate"))
        self.assertEqual(response.status_code, 403)

    def test_dm_sees_the_generator(self):
        self.login_dm()
        response = self.client.get(reverse("ruins:generate"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ruin Generator")

    def test_admin_sees_the_generator(self):
        self.login_as(self.create_admin())
        response = self.client.get(reverse("ruins:generate"))
        self.assertEqual(response.status_code, 200)


class GenerateFlowTest(RuinsViewTestCase):
    def setUp(self):
        super().setUp()
        self.login_dm()

    def test_bare_page_shows_the_form_and_no_result(self):
        response = self.client.get(reverse("ruins:generate"))
        self.assertContains(response, 'id="ruin-form"')
        self.assertNotContains(response, 'id="ruin-result"')

    def test_generation_without_a_seed_redirects_to_a_seeded_url(self):
        response = self.client.get(reverse("ruins:generate"), {"rooms": "3"})
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response.url).query)
        self.assertEqual(query["rooms"], ["3"])
        seed = int(query["seed"][0])
        follow_up = self.client.get(response.url)
        self.assertContains(follow_up, f"seed {seed}")

    def test_seeded_url_renders_the_exact_ruin_with_every_roll(self):
        expected = generate_ruin(room_count=3, seed=42)
        response = self.client.get(
            reverse("ruins:generate"), {"rooms": "3", "seed": "42"}
        )
        self.assertContains(response, 'id="ruin-result"')
        self.assertContains(response, "seed 42")
        self.assertContains(response, "Room 3")
        for roll in expected.all_rolls:
            self.assertContains(response, roll.result)
            self.assertContains(response, f"{roll.die} &rarr; {roll.face}")

    def test_seeded_url_is_deterministic_across_requests(self):
        # Compare the rendered result section only: the surrounding chrome
        # carries a per-request CSRF token (the logout form).
        def result_section(response) -> str:
            content = response.content.decode()
            return content[content.index('id="ruin-result"') :]

        url = reverse("ruins:generate")
        first = self.client.get(url, {"rooms": "2", "seed": "7"})
        second = self.client.get(url, {"rooms": "2", "seed": "7"})
        self.assertEqual(result_section(first), result_section(second))

    def test_result_page_keeps_the_form_for_generate_another(self):
        response = self.client.get(
            reverse("ruins:generate"), {"rooms": "3", "seed": "42"}
        )
        self.assertContains(response, 'id="ruin-form"')
        self.assertContains(response, 'value="3"')
        self.assertContains(response, "Generate another")

    def test_non_numeric_room_count_renders_an_error_not_a_dead_end(self):
        response = self.client.get(
            reverse("ruins:generate"), {"rooms": "many", "seed": "1"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "notice notice-error")
        self.assertContains(response, "whole number")
        self.assertContains(response, 'id="ruin-form"')

    def test_out_of_bounds_room_count_renders_an_error(self):
        response = self.client.get(
            reverse("ruins:generate"), {"rooms": str(MAX_ROOMS + 1), "seed": "1"}
        )
        self.assertContains(response, "notice notice-error")
        self.assertNotContains(response, 'id="ruin-result"')

    def test_non_numeric_seed_renders_an_error_and_keeps_the_value(self):
        response = self.client.get(
            reverse("ruins:generate"), {"rooms": "3", "seed": "lucky"}
        )
        self.assertContains(
            response, escape(sentence_parts(common_words().seeds.not_whole_number)[0])
        )
        self.assertContains(response, 'value="lucky"')


class ReferencePageLinkTest(RuinsViewTestCase):
    def test_dungeons_ruins_reference_page_links_to_the_generator(self):
        self.login_dm()
        response = self.client.get(
            reverse("reference:view", args=["dm-rules/gm-tools/dungeons-ruins"])
        )
        self.assertContains(response, reverse("ruins:generate"))

    def test_dm_nav_links_to_the_generator(self):
        self.login_as(self.create_admin())
        response = self.client.get(reverse("home"))
        self.assertContains(response, reverse("ruins:generate"))
