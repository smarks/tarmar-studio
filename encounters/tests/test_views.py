"""Encounter generator page: gating, the roll flow, and URL-borne state.

The page follows the house no-transient-UI rule the same way the beast
generator does, but without a saved row: the POST picks a seed and redirects
to a GET whose query string (terrain + seed) deterministically reproduces the
whole encounter, so a reload shows the same result.
"""

from django.core.management import call_command
from django.urls import reverse

from bestiary.models import Beast
from common.test_helpers import BaseTestCase
from encounters.generation import generate_encounter
from encounters.tests.test_generation import every_entry


class EncounterViewTestCase(BaseTestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("loaddata", "beasts", verbosity=0)


class GatingTest(EncounterViewTestCase):
    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse("encounters:generator"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_player_is_refused(self):
        self.login_as(self.create_player())
        response = self.client.get(reverse("encounters:generator"))
        self.assertEqual(response.status_code, 403)


class GeneratorPageTest(EncounterViewTestCase):
    def test_bare_page_shows_the_form_and_no_result(self):
        self.login_dm()
        response = self.client.get(reverse("encounters:generator"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Roll encounter")
        self.assertIsNone(response.context["result"])

    def test_post_redirects_to_a_seeded_get(self):
        self.login_dm()
        response = self.client.post(
            reverse("encounters:generator"), {"terrain": "forest"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("terrain=forest", response.url)
        self.assertIn("seed=", response.url)

    def test_seeded_get_renders_the_deterministic_result(self):
        self.login_dm()
        expected = generate_encounter("mountains", seed=11)
        response = self.client.get(
            reverse("encounters:generator"), {"terrain": "mountains", "seed": "11"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["result"], expected)
        self.assertContains(response, expected.entry.name)

    def test_result_links_the_matched_beast(self):
        self.login_dm()
        # Plains roll of 20 -> special -> seed hunting is fragile; instead use
        # a seed whose entry carries a beast slug, found deterministically.
        seed = next(
            candidate
            for candidate in range(200)
            if generate_encounter("plains", seed=candidate).entry.beast_slug
        )
        result = generate_encounter("plains", seed=seed)
        response = self.client.get(
            reverse("encounters:generator"), {"terrain": "plains", "seed": str(seed)}
        )
        beast = response.context["beast"]
        self.assertIsNotNone(beast)
        self.assertEqual(beast.slug, result.entry.beast_slug)
        self.assertContains(response, beast.get_absolute_url())

    def test_entry_without_a_beast_renders_without_a_link(self):
        self.login_dm()
        seed = next(
            candidate
            for candidate in range(500)
            if generate_encounter("mountains", seed=candidate).entry.beast_slug is None
        )
        response = self.client.get(
            reverse("encounters:generator"), {"terrain": "mountains", "seed": str(seed)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["beast"])

    def test_a_terrain_with_no_seed_rolls_one_and_redirects(self):
        """A trimmed link is the ordinary way to arrive here: paste a seeded
        URL into chat, lose the seed, and the page used to answer a 404 whose
        message named an empty string. Both ruin generators roll and redirect
        instead, which is the no-dead-ends standard (#687)."""
        self.login_dm()
        response = self.client.get(
            reverse("encounters:generator"), {"terrain": "forest"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("terrain=forest", response.url)
        self.assertIn("seed=", response.url)
        # And the URL it names renders the encounter it promises.
        landed = self.client.get(response.url)
        self.assertEqual(landed.status_code, 200)
        self.assertIsNotNone(landed.context["result"])

    def test_an_empty_seed_is_the_same_as_none(self):
        self.login_dm()
        response = self.client.get(
            reverse("encounters:generator"), {"terrain": "desert", "seed": ""}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("seed=", response.url)

    def test_a_terrain_with_no_seed_is_still_checked_for_being_real(self):
        self.login_dm()
        response = self.client.get(
            reverse("encounters:generator"), {"terrain": "swamp"}
        )
        self.assertEqual(response.status_code, 404)

    def test_a_seed_with_no_terrain_is_still_a_404(self):
        """The 404 is for a URL that names something wrong, which this one
        does — it asks for an encounter in no terrain at all."""
        self.login_dm()
        response = self.client.get(reverse("encounters:generator"), {"seed": "11"})
        self.assertEqual(response.status_code, 404)

    def test_unknown_terrain_is_a_404(self):
        self.login_dm()
        response = self.client.get(
            reverse("encounters:generator"), {"terrain": "swamp", "seed": "1"}
        )
        self.assertEqual(response.status_code, 404)

    def test_non_numeric_seed_is_a_404(self):
        self.login_dm()
        response = self.client.get(
            reverse("encounters:generator"), {"terrain": "plains", "seed": "banana"}
        )
        self.assertEqual(response.status_code, 404)

    def test_post_with_unknown_terrain_is_a_404(self):
        self.login_dm()
        response = self.client.post(
            reverse("encounters:generator"), {"terrain": "swamp"}
        )
        self.assertEqual(response.status_code, 404)

    def test_catalog_links_to_the_generator(self):
        self.login_dm()
        response = self.client.get(reverse("bestiary:catalog"))
        self.assertContains(response, reverse("encounters:generator"))


class LegacyURLTest(EncounterViewTestCase):
    """The generator's old home under bestiary (issue #218) still resolves,
    permanently, with the query string intact — seeded result URLs are
    shared bookmarks and must keep reproducing the same encounter."""

    def test_old_bestiary_url_redirects_permanently_with_query_preserved(self):
        response = self.client.get(
            "/tarmar/gm/bestiary/encounters/", {"terrain": "forest", "seed": "11"}
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, "/tarmar/gm/encounters/?terrain=forest&seed=11")


class NavigationTest(EncounterViewTestCase):
    def test_base_nav_shows_the_generator_link_for_a_dm(self):
        self.login_dm()
        response = self.client.get(reverse("home"))
        self.assertContains(response, f'href="{reverse("encounters:generator")}"')


class TableSlugsResolveAgainstTheLoadedCatalogTest(EncounterViewTestCase):
    """Every table row's beast resolves the way the view resolves it (#688).

    ``encounters/tests/test_generation.py`` already guards the slugs against
    the fixture *file*, purely and without a database. What nothing covered
    was the view's own lookup — ``Beast.objects.get(slug=..., is_generated=
    False)`` against the loaded catalog — which ran for exactly one row:
    ``test_result_links_the_matched_beast`` picks a single seed by scanning
    ``range(200)``, out of tables holding a hundred-plus rows and thirty
    distinct slugs. A slug that drifted would surface as an unhandled
    exception on whichever terrain and seed happened to land on it.
    """

    def test_every_table_row_with_a_beast_resolves_through_the_views_query(self):
        unresolved = []
        for entry in every_entry():
            if entry.beast_slug is None:
                continue
            if not Beast.objects.filter(
                slug=entry.beast_slug, is_generated=False
            ).exists():
                unresolved.append(f"{entry.name} -> {entry.beast_slug}")
        self.assertEqual(
            unresolved,
            [],
            "these encounter-table rows name beast slugs the loaded catalog "
            "does not have; a roll landing on one is a 500",
        )

    def test_a_drifted_slug_says_which_row_drifted(self):
        """The lookup still fails — a table naming a beast the catalog does
        not have is a data fault, not a request fault — but it fails naming
        the row rather than as a bare DoesNotExist on some seed."""
        terrain, seed = self.a_seed_landing_on_a_beast()
        slug = generate_encounter(terrain, seed=seed).entry.beast_slug
        Beast.objects.filter(slug=slug).delete()
        self.login_dm()
        with self.assertRaises(Beast.DoesNotExist) as drift:
            self.client.get(
                reverse("encounters:generator"),
                {"terrain": terrain, "seed": str(seed)},
            )
        self.assertIn(slug, str(drift.exception))
        self.assertIn("not in the catalog", str(drift.exception))

    @staticmethod
    def a_seed_landing_on_a_beast() -> tuple[str, int]:
        for seed in range(200):
            if generate_encounter("forest", seed=seed).entry.beast_slug:
                return "forest", seed
        raise AssertionError("no forest seed in range(200) rolls a catalog beast")
