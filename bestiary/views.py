"""DM-gated bestiary pages: catalog browse/filter, beast detail, generation.

Generation is state-driven per the house UI rule: the POST saves the variant
and redirects to its detail page, which renders entirely from the DB — reload
and it is all still there. No transient UI.
"""

from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import DetailView, ListView, View

from common.breadcrumbs import BreadcrumbMixin, filtered, step, trail
from common.mixins import DMToolsAccessMixin

from .generation import DIFFICULTY_BANDS, generate_variant, pick_base
from .models import CATEGORY_CHOICES, Beast

#: Cards on one page of the bestiary (#674). Every card draws an inline SVG
#: plate, so an unpaginated catalog is 239 of them on a cold install — and
#: the generated-variant list below it grew by one for every variant any DM
#: had ever rolled, with no cap and no pruning anywhere in the app.
CATALOG_PAGE_SIZE = 24

#: The query parameter the generated-variant list pages on. It cannot be
#: ``page``: both lists are on one page and each has to be able to turn
#: without moving the other.
VARIANT_PAGE_PARAM = "variants"


class CatalogView(BreadcrumbMixin, DMToolsAccessMixin, ListView):
    active_page = "bestiary"
    breadcrumb_label = "Bestiary"

    """Browse the catalog, filterable by category; generated variants listed
    separately below so the canon catalog stays canon.

    Both lists are paged (#674). The catalog is ``ListView``'s own paging;
    the variants get a ``Paginator`` of their own on ``VARIANT_PAGE_PARAM``,
    newest first, because they are a second list on the same page and the
    two have to turn independently. Each pager's links carry the other
    list's position and the category filter, so turning one page never
    silently resets the rest of the page.
    """

    model = Beast
    template_name = "bestiary/catalog.html"
    context_object_name = "beasts"
    paginate_by = CATALOG_PAGE_SIZE

    def get_queryset(self):
        return self._filtered(Beast.objects.filter(is_generated=False))

    def _filtered(self, queryset):
        """Narrow ``queryset`` by the toolbar's category and name box.

        The catalog had a category filter and no text search at all, so
        finding "Cockatrice" among 239 beasts meant Ctrl-F or a long scroll --
        and a DM at the table wants one beast, quickly (issue #703). The name
        box is a case-insensitive substring, which is what someone half
        remembering a name needs.

        Args:
            queryset: Catalog beasts, or generated variants.

        Returns:
            The queryset with both filters applied.
        """
        category = self.request.GET.get("category")
        if category:
            queryset = queryset.filter(category=category)
        name = (self.request.GET.get("name") or "").strip()
        if name:
            queryset = queryset.filter(name__icontains=name)
        return queryset

    def generated_variants(self, category: str):
        """Every generated variant, newest first, filtered like the catalog.

        ``category`` is read off the request inside ``_filtered`` along with
        the name box, so the argument is kept only for the call site's
        readability.

        Newest first because a DM comes back to the page for the thing they
        just rolled; ``pk`` breaks ties, since ``created_at`` defaults to
        ``timezone.now`` and two variants rolled in one request share it.
        """
        variants = self._filtered(
            Beast.objects.filter(is_generated=True).select_related("base_beast")
        )
        return variants.order_by("-created_at", "-pk")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected = self.request.GET.get("category", "")
        context["categories"] = CATEGORY_CHOICES
        context["selected_category"] = selected
        context["name_filter"] = (self.request.GET.get("name") or "").strip()
        variant_page = Paginator(
            self.generated_variants(selected), CATALOG_PAGE_SIZE
        ).get_page(self.request.GET.get(VARIANT_PAGE_PARAM))
        context["generated_beasts"] = variant_page.object_list
        context["variant_page"] = variant_page
        context["variant_is_paginated"] = variant_page.has_other_pages()
        context["variant_page_param"] = VARIANT_PAGE_PARAM
        # Each pager's links keep the filter and the *other* list's page.
        context["catalog_query"] = self.other_list_query(
            selected,
            context["name_filter"],
            VARIANT_PAGE_PARAM,
            variant_page.number,
        )
        context["variant_query"] = self.other_list_query(
            selected, context["name_filter"], "page", context["page_obj"].number
        )
        context["difficulty_bands"] = list(DIFFICULTY_BANDS)
        return context

    @staticmethod
    def other_list_query(
        category: str, name: str, page_param: str, page_number: int
    ) -> str:
        """The query string a pager's links must carry besides its own page.

        Args:
            category: The selected category filter, or ``""`` for all.
            name: The name box's text, or ``""``.
            page_param: The *other* list's page parameter.
            page_number: The page that other list is currently showing.

        Returns:
            A urlencoded query string, without a leading ``?`` or ``&``.
        """
        params = {}
        if category:
            params["category"] = category
        if name:
            params["name"] = name
        if page_number > 1:
            params[page_param] = page_number
        return urlencode(params)


class BeastDetailView(BreadcrumbMixin, DMToolsAccessMixin, DetailView):
    active_page = "bestiary"
    model = Beast
    template_name = "bestiary/beast_detail.html"
    context_object_name = "beast"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        beast = context["beast"]
        # What "generate another" should ask for by default: this beast if
        # it is catalog stock, otherwise the base it was varied from.
        context["generate_name"] = (
            beast.base_beast.name if beast.base_beast else beast.name
        )
        context["generate_category"] = beast.category
        context["difficulty_bands"] = list(DIFFICULTY_BANDS)
        return context

    def get_breadcrumb(self, context):
        """Bestiary > Category > (base beast) > this beast.

        Issue #192 built this trail for the bestiary alone; #189 made the
        shape site-wide, so it is expressed in the shared helpers now and
        rendered by base.html like every other page's.
        """
        beast = context["beast"]
        steps = [
            step("Bestiary", reverse("bestiary:catalog")),
            step(
                beast.get_category_display(),
                filtered("bestiary:catalog", category=beast.category),
            ),
        ]
        if beast.base_beast:
            steps.append(
                step(beast.base_beast.name, beast.base_beast.get_absolute_url())
            )
        return trail(*steps, step(beast.name))


class GenerateBeastView(DMToolsAccessMixin, View):
    """POST name/category/difficulty; save a variant; render it immediately
    by redirecting to its (state-driven) detail page."""

    active_page = "bestiary"

    def post(self, request):
        name = request.POST.get("name", "").strip()
        category = request.POST.get("category", "").strip()
        difficulty = request.POST.get("difficulty", "").strip()
        try:
            base = pick_base(
                name=name or None,
                category=category or None,
                difficulty=difficulty or None,
            )
        except (Beast.DoesNotExist, ValueError):
            return redirect_with_error(request, name, category, difficulty)
        variant = generate_variant(base)
        return redirect("bestiary:detail", slug=variant.slug)


class DeleteGeneratedBeastView(DMToolsAccessMixin, View):
    """POST: delete one generated variant (issue #339, item 21).

    Only a variant: the lookup is restricted to ``is_generated=True``, so a
    canon catalog beast is a 404 here and can never be deleted from the
    site. Same DM/admin gate as every bestiary page. A battle combatant
    rolled from the variant keeps its own stored stats; its ``beast`` link
    is ``SET_NULL``, so no battle record goes with it.
    """

    active_page = "bestiary"

    def post(self, request, slug):
        beast = get_object_or_404(Beast, slug=slug, is_generated=True)
        beast.delete()
        return redirect("bestiary:catalog")


def redirect_with_error(request, name, category, difficulty):
    """Back to the catalog with a persistent, dismissible error banner.

    The error rides the query string so the redirected GET renders it
    server-side — state in the URL, not a flash of client-side script.
    """
    requested = name or category or difficulty or "beast"
    query = urlencode({"generate_error": f"No catalog beast matches “{requested}”."})
    return redirect(f"{reverse('bestiary:catalog')}?{query}")
