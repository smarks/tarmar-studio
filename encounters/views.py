"""Encounter generator page: gating, the roll flow, and URL-borne state
(issue #218 — promoted out of the bestiary app to stand on its own).

The page follows the house no-transient-UI rule the same way the beast
generator does, but without a saved row: the POST picks a seed and redirects
to a GET whose query string (terrain + seed) deterministically reproduces the
whole encounter, so a reload shows the same result.
"""

from urllib.parse import urlencode

from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import TemplateView

from bestiary.models import Beast
from common.breadcrumbs import BreadcrumbMixin
from common.mixins import DMToolsAccessMixin
from common.seeds import InvalidSeed, parse_seed, roll_seed

from .generation import MAIN_TABLES, SUBTABLES, TERRAINS, generate_encounter


class EncounterGeneratorView(BreadcrumbMixin, DMToolsAccessMixin, TemplateView):
    active_page = "encounters"
    breadcrumb_label = "Encounter generator"

    template_name = "encounters/generator.html"

    @staticmethod
    def _validated_terrain(value: str) -> str:
        if value not in TERRAINS:
            raise Http404(f"Unknown terrain: {value}")
        return value

    def get(self, request, *args, **kwargs):
        """Roll a seed for a terrain that arrived without one (#687).

        ``?terrain=forest`` alone used to 404 with a message naming an empty
        string, which is what a seeded link pasted into chat and trimmed
        looks like. Both ruin generators answer the same input by rolling a
        seed and redirecting to the fully specified URL, which is the
        no-dead-ends standard (#192) this module's docstring cites. A seed
        that *was* given and does not parse is still a 404: that URL names
        something, and it names something wrong.
        """
        terrain = request.GET.get("terrain", "")
        if terrain and not request.GET.get("seed", ""):
            query = urlencode(
                {
                    "terrain": self._validated_terrain(terrain),
                    "seed": roll_seed(),
                }
            )
            return redirect(f"{reverse('encounters:generator')}?{query}")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            terrains=TERRAINS,
            main_tables=MAIN_TABLES,
            subtables=SUBTABLES,
            result=None,
            beast=None,
            selected_terrain="",
        )
        terrain = self.request.GET.get("terrain", "")
        seed_text = self.request.GET.get("seed", "")
        if not terrain and not seed_text:
            return context
        terrain = self._validated_terrain(terrain)
        try:
            seed = parse_seed(seed_text)
        except InvalidSeed:
            seed = None
        if seed is None:
            raise Http404(f"Seed must be a number, got: {seed_text}")
        result = generate_encounter(terrain, seed=seed)
        beast = None
        if result.entry.beast_slug is not None:
            try:
                beast = Beast.objects.get(
                    slug=result.entry.beast_slug, is_generated=False
                )
            except Beast.DoesNotExist as missing:
                # A table row naming a slug the catalog no longer has is a
                # data fault, not a request fault, so it still fails — but
                # it fails saying which row drifted rather than as a bare
                # DoesNotExist on whichever seed happened to land on it
                # (#688). The drift guard in the tests is what stops it
                # reaching anybody.
                raise Beast.DoesNotExist(
                    f"encounter table row {result.entry.name!r} names beast "
                    f"slug {result.entry.beast_slug!r}, which is not in the "
                    f"catalog"
                ) from missing
        context.update(result=result, beast=beast, selected_terrain=terrain)
        return context

    def post(self, request):
        terrain = self._validated_terrain(request.POST.get("terrain", ""))
        seed = roll_seed()
        query = urlencode({"terrain": terrain, "seed": seed})
        return redirect(f"{reverse('encounters:generator')}?{query}")
