"""Hook generator view tests: DM gating, party matching, seed flow (#276)."""

from urllib.parse import parse_qs, urlparse

from django.urls import reverse
from django.utils.html import escape

from common.test_helpers import BaseTestCase, create_character
from parties.models import Party, PartyMembership
from ruins.hook_generation import generate_hook

LEVEL_TWO_XP = 2000
LEVEL_FIVE_XP = 16000


class HookViewTestCase(BaseTestCase):
    def build_party(self, owner, name="The Torchbearers", xp_values=(0,)):
        party = Party.objects.create(name=name, owner=owner)
        for index, xp in enumerate(xp_values):
            character = create_character(
                owner, name=f"{name} member {index}", total_xp=xp
            )
            PartyMembership.objects.create(
                party=party, character=character, added_by=owner
            )
        return party


class GatingTest(HookViewTestCase):
    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse("hooks:generate"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_player_is_refused(self):
        self.login_as(self.create_player())
        response = self.client.get(reverse("hooks:generate"))
        self.assertEqual(response.status_code, 403)

    def test_dm_sees_the_generator(self):
        self.login_dm()
        response = self.client.get(reverse("hooks:generate"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hook Generator")

    def test_admin_sees_the_generator(self):
        self.login_as(self.create_admin())
        response = self.client.get(reverse("hooks:generate"))
        self.assertEqual(response.status_code, 200)


class GenerateFlowTest(HookViewTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.login_dm()

    def test_bare_page_shows_the_form_and_no_result(self):
        response = self.client.get(reverse("hooks:generate"))
        self.assertContains(response, 'id="hook-form"')
        self.assertNotContains(response, 'id="hook-result"')

    def test_parties_are_offered_with_their_summaries(self):
        self.build_party(self.dm, xp_values=(0, LEVEL_TWO_XP))
        response = self.client.get(reverse("hooks:generate"))
        self.assertContains(response, "The Torchbearers")
        self.assertContains(response, "avg level 1.5")

    def test_generation_without_a_seed_redirects_to_a_seeded_url(self):
        response = self.client.get(reverse("hooks:generate"), {"level": "1"})
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response.url).query)
        self.assertEqual(query["level"], ["1"])
        seed = int(query["seed"][0])
        follow_up = self.client.get(response.url)
        self.assertContains(follow_up, f"seed {seed}")

    def test_party_generation_keeps_the_party_in_the_seeded_url(self):
        party = self.build_party(self.dm)
        response = self.client.get(reverse("hooks:generate"), {"party": str(party.pk)})
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response.url).query)
        self.assertEqual(query["party"], [str(party.pk)])
        self.assertIn("seed", query)

    def test_seeded_level_url_renders_the_exact_hook_with_the_roll(self):
        expected = generate_hook(level=1, seed=42)
        response = self.client.get(
            reverse("hooks:generate"), {"level": "1", "seed": "42"}
        )
        self.assertContains(response, 'id="hook-result"')
        self.assertContains(response, escape(expected.hook.title))
        self.assertContains(
            response, f"{expected.roll.die} &rarr; {expected.roll.face}"
        )
        self.assertContains(response, "Generate another")

    def test_party_average_selects_the_band(self):
        # Two members at levels 1 and 2: average 1.5 rounds up to level 2.
        party = self.build_party(self.dm, xp_values=(0, LEVEL_TWO_XP))
        expected = generate_hook(level=2, seed=9)
        response = self.client.get(
            reverse("hooks:generate"), {"party": str(party.pk), "seed": "9"}
        )
        self.assertContains(response, "rolls as <strong>level 2</strong>")
        self.assertContains(response, escape(expected.hook.title))
        self.assertContains(response, "average 1.5")

    def test_regional_hook_links_its_region_page(self):
        # Seed picked so level 2 rolls a hook with a region link.
        for seed in range(1, 60):
            if generate_hook(level=2, seed=seed).hook.region_links:
                expected = generate_hook(level=2, seed=seed)
                break
        response = self.client.get(
            reverse("hooks:generate"), {"level": "2", "seed": str(seed)}
        )
        for _, url in expected.hook.region_links:
            self.assertContains(response, f'href="{url}"')

    def test_outgrown_party_gets_scenario_seeds_not_a_dead_end(self):
        party = self.build_party(
            self.dm, name="The Greycloaks", xp_values=(LEVEL_FIVE_XP,)
        )
        response = self.client.get(
            reverse("hooks:generate"), {"party": str(party.pk), "seed": "1"}
        )
        self.assertContains(response, 'id="hook-outgrown"')
        self.assertContains(response, "Scenario Seeds")
        self.assertContains(response, 'id="hook-form"')
        self.assertNotContains(response, 'id="hook-result"')

    def test_empty_party_is_an_error_that_keeps_the_form(self):
        party = Party.objects.create(name="The Unmanned", owner=self.dm)
        response = self.client.get(reverse("hooks:generate"), {"party": str(party.pk)})
        self.assertContains(response, "has no members")
        self.assertContains(response, 'id="hook-form"')

    def test_unknown_party_is_an_error(self):
        response = self.client.get(reverse("hooks:generate"), {"party": "9999"})
        self.assertContains(response, "That party does not exist.")

    def test_non_numeric_seed_is_an_error_that_keeps_the_value(self):
        response = self.client.get(
            reverse("hooks:generate"), {"level": "1", "seed": "lucky"}
        )
        self.assertContains(response, "Seed must be a whole number")
        self.assertContains(response, 'value="lucky"')

    def test_non_numeric_level_is_an_error(self):
        response = self.client.get(reverse("hooks:generate"), {"level": "two"})
        self.assertContains(response, "Level must be a whole number")

    def test_blank_party_and_blank_level_is_an_error(self):
        response = self.client.get(
            reverse("hooks:generate"), {"party": "", "level": ""}
        )
        self.assertContains(response, "Pick a party or a level.")
