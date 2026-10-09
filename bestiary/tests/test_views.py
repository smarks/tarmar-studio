"""View tests: DM gating, catalog browse/filter, generation flow."""

from django.core.management import call_command
from django.urls import reverse

from bestiary.models import Beast
from bestiary.views import CATALOG_PAGE_SIZE, VARIANT_PAGE_PARAM
from common.test_helpers import BaseTestCase


class BestiaryViewTestCase(BaseTestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("loaddata", "beasts", verbosity=0)

    def catalog_beast(self, **filters) -> Beast:
        """The first catalog beast matching ``filters``.

        Fails the test if the fixture has none, rather than handing back a
        ``Beast | None`` for every caller to unwrap (issue #198).
        """
        beast = Beast.objects.filter(is_generated=False, **filters).first()
        if beast is None:
            self.fail(f"the beast fixture has nothing matching {filters}")
        return beast


class GatingTest(BestiaryViewTestCase):
    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse("bestiary:catalog"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_player_is_refused(self):
        # An authenticated non-DM gets PermissionDenied (AccessMixin raises
        # rather than redirecting a user who is already signed in).
        self.login_as(self.create_player())
        response = self.client.get(reverse("bestiary:catalog"))
        self.assertEqual(response.status_code, 403)

    def test_dm_sees_catalog(self):
        self.login_dm()
        response = self.client.get(reverse("bestiary:catalog"))
        self.assertEqual(response.status_code, 200)

    def test_admin_sees_catalog(self):
        self.login_as(self.create_admin())
        response = self.client.get(reverse("bestiary:catalog"))
        self.assertEqual(response.status_code, 200)

    def test_detail_and_generate_are_gated_too(self):
        beast = Beast.objects.filter(is_generated=False).earliest("pk")
        detail = self.client.get(reverse("bestiary:detail", args=[beast.slug]))
        self.assertEqual(detail.status_code, 302)
        generate = self.client.post(reverse("bestiary:generate"), {"name": beast.name})
        self.assertEqual(generate.status_code, 302)
        self.assertIn("login", generate.url)


class CatalogTest(BestiaryViewTestCase):
    def setUp(self):
        super().setUp()
        self.login_dm()

    def test_catalog_lists_beasts_with_stats_and_description(self):
        """The catalog is paged since #674, so a named beast is looked for
        where a reader would look for it — under its category — rather than
        on whichever page it happened to fall on."""
        response = self.client.get(reverse("bestiary:catalog"), {"category": "mammals"})
        self.assertContains(response, "Dire Wolf")
        self.assertContains(response, "TFT Bestiary p. 37")
        wolf = Beast.objects.get(slug="dire-wolf")
        self.assertContains(response, wolf.description)

    def test_the_unfiltered_catalog_opens_on_its_first_page(self):
        response = self.client.get(reverse("bestiary:catalog"))
        first = Beast.objects.filter(is_generated=False)[0]
        self.assertContains(response, first.name)
        self.assertEqual(len(response.context["beasts"]), CATALOG_PAGE_SIZE)

    def test_category_filter(self):
        response = self.client.get(reverse("bestiary:catalog"), {"category": "dragons"})
        self.assertContains(response, "Adult Dragon")
        self.assertNotContains(response, "Dire Wolf")

    def test_generated_variants_listed_separately(self):
        base = Beast.objects.get(slug="dire-wolf")
        self.client.post(reverse("bestiary:generate"), {"name": base.name})
        response = self.client.get(reverse("bestiary:catalog"))
        self.assertContains(response, "Dire Wolf (variant 1)")

    def test_error_banner_renders_from_query_state(self):
        response = self.client.get(
            reverse("bestiary:catalog"), {"generate_error": "No such beast."}
        )
        self.assertContains(response, "No such beast.")
        self.assertContains(response, "notice notice-error")
        # It can be dismissed: a link back to the plain catalog (issue #339, item 33).
        self.assertContains(
            response, f'class="notice-dismiss" href="{reverse("bestiary:catalog")}"'
        )


class DetailTest(BestiaryViewTestCase):
    def setUp(self):
        super().setUp()
        self.login_dm()

    def test_detail_shows_statblock_attacks_and_citation(self):
        response = self.client.get(reverse("bestiary:detail", args=["adult-dragon"]))
        self.assertContains(response, "Adult Dragon")
        self.assertContains(response, "beast-statblock")
        self.assertContains(response, "Heavy Striking")
        self.assertContains(response, "The Fantasy Trip Bestiary, p. 54")


class GenerateTest(BestiaryViewTestCase):
    def setUp(self):
        super().setUp()
        self.login_dm()

    def test_generate_by_exact_name_redirects_to_saved_variant(self):
        response = self.client.post(reverse("bestiary:generate"), {"name": "Dire Wolf"})
        self.assertEqual(response.status_code, 302)
        variant = Beast.objects.get(is_generated=True)
        self.assertEqual(response.url, reverse("bestiary:detail", args=[variant.slug]))
        detail = self.client.get(response.url)
        self.assertContains(detail, variant.name)
        self.assertContains(detail, "generated variant")

    def test_generate_random_from_category(self):
        response = self.client.post(
            reverse("bestiary:generate"), {"category": "elementals"}
        )
        variant = Beast.objects.get(is_generated=True)
        self.assertEqual(variant.category, "elementals")
        self.assertEqual(response.url, reverse("bestiary:detail", args=[variant.slug]))

    def test_generate_random_by_difficulty(self):
        self.client.post(reverse("bestiary:generate"), {"difficulty": "deadly"})
        variant = Beast.objects.get(is_generated=True)
        self.assertGreaterEqual(variant.base_beast.strength, 60)

    def test_unknown_name_redirects_with_error_state(self):
        response = self.client.post(
            reverse("bestiary:generate"), {"name": "Nonexistent Horror"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("generate_error", response.url)
        self.assertEqual(Beast.objects.filter(is_generated=True).count(), 0)

    def test_unknown_difficulty_redirects_with_error_state(self):
        response = self.client.post(
            reverse("bestiary:generate"), {"difficulty": "impossible"}
        )
        self.assertIn("generate_error", response.url)


class CatalogPopulationTest(BaseTestCase):
    """The data migration must populate the catalog with no loaddata step."""

    def test_migration_populated_the_catalog(self):
        # No fixture loading here on purpose: a fresh (migrated) database must
        # already hold the full catalog, or production deploys empty (#183).
        self.assertEqual(Beast.objects.filter(is_generated=False).count(), 239)


class NavigationLinkTest(BestiaryViewTestCase):
    def test_dm_nav_links_to_bestiary(self):
        self.login_dm()
        response = self.client.get(reverse("bestiary:catalog"))
        self.assertContains(response, reverse("bestiary:catalog"))
        self.assertContains(response, ">Bestiary</a>")


class BreadcrumbTest(BestiaryViewTestCase):
    """A beast detail page says where it sits and how to get back (issue #192)."""

    def setUp(self):
        super().setUp()
        self.login_dm()

    def test_catalog_beast_trails_back_through_its_category(self):
        beast = self.catalog_beast(category="dragons")
        response = self.client.get(beast.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        steps = response.context["breadcrumb"]
        # Rooted at the Tarmar home since #189 made the trail site-wide.
        self.assertEqual(
            [step["label"] for step in steps],
            ["Tarmar", "Bestiary", beast.get_category_display(), beast.name],
        )
        self.assertEqual(steps[1]["url"], reverse("bestiary:catalog"))
        # The category step returns to the catalog already filtered.
        self.assertEqual(
            steps[2]["url"], f"{reverse('bestiary:catalog')}?category=dragons"
        )
        # The page you are on is not a link.
        self.assertEqual(steps[-1]["url"], "")

    def test_a_variant_trails_back_through_the_beast_it_came_from(self):
        base = self.catalog_beast(category="dragons")
        variant = self.client.post(reverse("bestiary:generate"), {"name": base.name})
        response = self.client.get(variant.url)

        steps = response.context["breadcrumb"]
        self.assertEqual(
            [step["label"] for step in steps],
            [
                "Tarmar",
                "Bestiary",
                base.get_category_display(),
                base.name,
                response.context["beast"].name,
            ],
        )
        self.assertEqual(steps[3]["url"], base.get_absolute_url())

    def test_the_trail_is_rendered_as_navigation(self):
        beast = self.catalog_beast()
        response = self.client.get(beast.get_absolute_url())
        self.assertContains(response, 'class="breadcrumb"')
        self.assertContains(response, reverse("bestiary:catalog"))


class GenerateAnotherTest(BestiaryViewTestCase):
    """From any beast page you can generate again without going back (#192)."""

    def setUp(self):
        super().setUp()
        self.login_dm()

    def test_a_catalog_beast_offers_a_variant_of_itself(self):
        beast = self.catalog_beast()
        response = self.client.get(beast.get_absolute_url())
        self.assertContains(response, "generate-panel")
        self.assertContains(response, f'value="{beast.name}"')

    def test_a_variant_offers_another_of_the_same_base(self):
        base = self.catalog_beast(category="dragons")
        created = self.client.post(reverse("bestiary:generate"), {"name": base.name})
        response = self.client.get(created.url)

        self.assertContains(response, "generate-panel")
        # Same parameters: another variant of the same base beast.
        self.assertContains(response, f'value="{base.name}"')
        # Tweaked parameters: a random beast from this category instead.
        self.assertContains(response, 'value="dragons"')
        self.assertContains(response, "difficulty")

    def test_generating_another_variant_lands_on_a_new_beast(self):
        base = self.catalog_beast(category="dragons")
        first = self.client.post(reverse("bestiary:generate"), {"name": base.name})
        second = self.client.post(reverse("bestiary:generate"), {"name": base.name})

        self.assertNotEqual(first.url, second.url)
        self.assertEqual(Beast.objects.filter(base_beast=base).count(), 2)

    def test_generation_context_carries_the_category_and_bands(self):
        beast = self.catalog_beast()
        response = self.client.get(beast.get_absolute_url())
        self.assertEqual(response.context["generate_category"], beast.category)
        self.assertIn("difficulty_bands", response.context)


class BeastUrlTest(BestiaryViewTestCase):
    def test_get_absolute_url_is_the_detail_page(self):
        beast = self.catalog_beast()
        self.assertEqual(
            beast.get_absolute_url(), reverse("bestiary:detail", args=[beast.slug])
        )


class PlateRenderingTest(BestiaryViewTestCase):
    """The pages actually draw the silhouette art (issue #193)."""

    def setUp(self):
        super().setUp()
        self.login_dm()

    def test_a_beast_page_draws_its_plate(self):
        beast = Beast.objects.filter(is_generated=False).first()
        assert beast is not None
        response = self.client.get(beast.get_absolute_url())
        # Inline SVG, not an <img>: no extra request, and it scales.
        self.assertContains(response, '<svg class="beast-plate beast-plate-detail"')
        # Described for anyone who cannot see it.
        self.assertContains(response, f"{beast.name}, a ")
        self.assertContains(response, "silhouette")

    def test_the_catalog_draws_a_plate_on_every_card(self):
        response = self.client.get(reverse("bestiary:catalog"))
        cards = response.content.decode().count('class="beast-card"')
        plates = response.content.decode().count("beast-plate-card")
        self.assertGreater(cards, 0)
        self.assertEqual(plates, cards)

    def test_a_generated_variant_gets_a_plate_too(self):
        base = Beast.objects.filter(is_generated=False, category="dragons").first()
        assert base is not None
        created = self.client.post(reverse("bestiary:generate"), {"name": base.name})
        response = self.client.get(created.url)
        self.assertContains(response, "beast-plate-detail")


class DeleteGeneratedBeastTest(BestiaryViewTestCase):
    """Issue #339, item 21: generated variants can be deleted; canon cannot."""

    def generated_variant(self) -> Beast:
        from bestiary.generation import generate_variant

        return generate_variant(self.catalog_beast())

    def delete_url(self, beast: Beast) -> str:
        return reverse("bestiary:delete", args=[beast.slug])

    def test_dm_deletes_a_generated_variant(self):
        variant = self.generated_variant()
        self.login_dm()
        response = self.client.post(self.delete_url(variant))
        self.assertRedirects(response, reverse("bestiary:catalog"))
        self.assertFalse(Beast.objects.filter(pk=variant.pk).exists())

    def test_admin_may_delete_too(self):
        variant = self.generated_variant()
        self.login_as(self.create_admin())
        self.client.post(self.delete_url(variant))
        self.assertFalse(Beast.objects.filter(pk=variant.pk).exists())

    def test_a_canon_beast_is_a_404_and_stays(self):
        canon = self.catalog_beast()
        self.login_dm()
        self.assertEqual(self.client.post(self.delete_url(canon)).status_code, 404)
        self.assertTrue(Beast.objects.filter(pk=canon.pk).exists())

    def test_players_and_anonymous_are_refused(self):
        variant = self.generated_variant()
        response = self.client.post(self.delete_url(variant))
        self.assertEqual(response.status_code, 302)
        self.login_as(self.create_player())
        self.assertEqual(self.client.post(self.delete_url(variant)).status_code, 403)
        self.assertTrue(Beast.objects.filter(pk=variant.pk).exists())

    def test_get_does_not_delete(self):
        variant = self.generated_variant()
        self.login_dm()
        self.assertEqual(self.client.get(self.delete_url(variant)).status_code, 405)
        self.assertTrue(Beast.objects.filter(pk=variant.pk).exists())

    def test_delete_is_offered_with_a_confirmation_on_variants_only(self):
        variant = self.generated_variant()
        canon = self.catalog_beast()
        self.login_dm()
        detail = self.client.get(variant.get_absolute_url())
        self.assertContains(detail, f'action="{self.delete_url(variant)}"')
        self.assertContains(
            detail, f'data-confirm="Delete the generated variant {variant.name}?'
        )
        canon_page = self.client.get(canon.get_absolute_url())
        self.assertNotContains(canon_page, "/delete/")
        catalog = self.client.get(reverse("bestiary:catalog"))
        self.assertContains(catalog, f'action="{self.delete_url(variant)}"')
        self.assertNotContains(catalog, f'action="{self.delete_url(canon)}"')


class CatalogPagingTest(BaseTestCase):
    """Neither list on the bestiary page is unbounded (#674).

    ``CatalogView`` had no ``paginate_by``, so all 239 catalog beasts
    rendered at once — each card drawing an inline SVG plate — and beneath
    them an *unsliced* queryset of every generated variant ever rolled,
    which nothing in the app prunes: generating only ever adds rows, and
    deleting is one at a time by hand. Neither had a bound and the default
    view was everything.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("loaddata", "beasts", verbosity=0)

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.login_as(self.dm)

    def make_variants(self, count: int) -> None:
        base = Beast.objects.get(slug="dire-wolf")
        Beast.objects.bulk_create(
            Beast(
                name=f"Dire Wolf (variant {number})",
                slug=f"dire-wolf-variant-{number}",
                category=base.category,
                description=base.description,
                strength=base.strength,
                dexterity=base.dexterity,
                intelligence=base.intelligence,
                wisdom=base.wisdom,
                constitution=base.constitution,
                charisma=base.charisma,
                fatigue_pool=base.fatigue_pool,
                body_pool=base.body_pool,
                movement=base.movement,
                is_generated=True,
                base_beast=base,
            )
            for number in range(count)
        )

    def test_the_catalog_is_paged_and_says_so(self):
        response = self.client.get(reverse("bestiary:catalog"))
        self.assertEqual(len(response.context["beasts"]), CATALOG_PAGE_SIZE)
        self.assertTrue(response.context["is_paginated"])
        self.assertContains(response, 'rel="next"')

    def test_every_catalog_beast_is_reachable_by_turning_pages(self):
        total = Beast.objects.filter(is_generated=False).count()
        pages = response = None
        seen = 0
        page_number = 1
        while True:
            response = self.client.get(
                reverse("bestiary:catalog"), {"page": page_number}
            )
            seen += len(response.context["beasts"])
            pages = response.context["page_obj"].paginator.num_pages
            if page_number >= pages:
                break
            page_number += 1
        self.assertEqual(seen, total)

    def test_the_generated_variants_are_paged_on_their_own_parameter(self):
        self.make_variants(CATALOG_PAGE_SIZE + 5)
        response = self.client.get(reverse("bestiary:catalog"))
        self.assertEqual(len(response.context["generated_beasts"]), CATALOG_PAGE_SIZE)
        self.assertTrue(response.context["variant_is_paginated"])
        self.assertContains(response, f"{VARIANT_PAGE_PARAM}=2")

        second = self.client.get(reverse("bestiary:catalog"), {VARIANT_PAGE_PARAM: 2})
        self.assertEqual(len(second.context["generated_beasts"]), 5)
        # The catalog above it did not move.
        self.assertEqual(second.context["page_obj"].number, 1)

    def test_the_variants_are_newest_first(self):
        self.make_variants(3)
        newest = Beast.objects.filter(is_generated=True).order_by("-pk").first()
        response = self.client.get(reverse("bestiary:catalog"))
        self.assertEqual(response.context["generated_beasts"][0], newest)

    def test_each_pager_keeps_the_filter_and_the_other_lists_page(self):
        """Turning one list's page must not silently reset the category or
        send the other list back to the top."""
        self.make_variants(CATALOG_PAGE_SIZE + 5)
        response = self.client.get(
            reverse("bestiary:catalog"),
            {"category": "mammals", "page": 2, VARIANT_PAGE_PARAM: 2},
        )
        self.assertIn("category=mammals", response.context["catalog_query"])
        self.assertIn(f"{VARIANT_PAGE_PARAM}=2", response.context["catalog_query"])
        self.assertIn("category=mammals", response.context["variant_query"])
        self.assertIn("page=2", response.context["variant_query"])

    def test_a_filtered_catalog_short_enough_to_fit_shows_no_pager(self):
        response = self.client.get(
            reverse("bestiary:catalog"), {"category": "constructs"}
        )
        self.assertFalse(response.context["is_paginated"])
        self.assertNotContains(response, 'class="pagination"')
