"""Searchable pickers: a search box that asks the server, a page at a time.

Issue #864. The pickers rendered every row they could offer, and paging them
the way the lists were paged in #795 broke them: a filter that only hid rows
on screen could not find a character on page 2, and a ``<select>`` showed only
its page's options. Spencer's ruling, 2026-10-09: "the nine pickers get a
search box that queries the server (name, owner) with results paged at
PAGE_SIZE, newest first so a just-made character is on page one; the plain
selects become searchable pickers."

One component serves every picker:

* this module, which searches a queryset by its source's fields, orders it,
  pages it at :data:`common.pagination.PAGE_SIZE`, and renders a page;
* ``templates/_picker.html``, the search box and the body around it;
* ``templates/_picker_body.html``, the page's rows (each source's own rows
  template, a list of forms or a ``<select>``), the count and the pager;
* ``static/js/picker.js``, which asks the picker's endpoint for the body
  that matches what was typed, or for another page, and puts it in place.

The page's first load and every search render the same body template, so
what a search shows is what a reload with that query would show. Each app
keeps the rows a viewer may pick (the queryset) and the endpoint that guards
them; this module never decides who may pick what.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from django.core.paginator import Page, Paginator
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.template.loader import render_to_string

from common.pagination import PAGE_SIZE

#: The longest query a picker searches by; the rest of a longer one is ignored.
PICKER_QUERY_MAX_LENGTH = 100

#: Why a picker endpoint answers 404 for a kind it does not serve. Django's 404
#: page never shows it, with DEBUG off (issue #136): it is for whoever reads the
#: debug page, not a player.
NO_SUCH_PICKER = "No such picker"

#: The query string parameters a picker's endpoint reads.
QUERY_PARAMETER = "q"
PAGE_PARAMETER = "page"


@dataclass(frozen=True)
class PickerSource:
    """What one kind of picker searches, in what order, and how it draws rows.

    Attributes:
        rows_template: Renders ``picker.rows`` (and ``picker.pinned``) with the
            page's own context: a ``<ul>`` of forms, or a ``<select>``.
        search_label: The search box's accessible name.
        placeholder: The search box's placeholder.
        search_fields: The fields a query matches, case-insensitively, any of
            them: the name, and the owner's username.
        order_by: The order of the results; newest first, ending in a unique
            key so a row stays on one page (issue #950).
        noun: What one row is, for the count ("character").
        noun_plural: The same, for more than one ("characters").
        order_note: How the count line names the order.
    """

    rows_template: str
    search_label: str
    placeholder: str
    search_fields: tuple[str, ...]
    order_by: tuple[str, ...]
    noun: str
    noun_plural: str
    order_note: str = "newest first"


@dataclass(frozen=True)
class PickerPage:
    """One page of a picker's results, as the page and its endpoint render it.

    Attributes:
        source: The kind of picker.
        element_id: The picker's DOM id; the search box is ``search_id``.
        search_id: The search box's DOM id.
        url: The endpoint that renders the body for a query and a page.
        query: What the results match; empty for every row.
        page: The page of results.
        pinned: Rows the page must show whether or not they are on this
            page, such as the option a form has already chosen; never
            repeated among ``rows``.
    """

    source: PickerSource
    element_id: str
    search_id: str
    url: str
    query: str
    page: Page
    pinned: Sequence[Any] = field(default_factory=tuple)

    @property
    def rows(self) -> list[Any]:
        """The page's rows, less any that are pinned."""
        pinned_keys = {row.pk for row in self.pinned}
        return [row for row in self.page.object_list if row.pk not in pinned_keys]

    @property
    def total(self) -> int:
        """How many rows match the query, on every page."""
        return self.page.paginator.count

    @property
    def noun(self) -> str:
        """The source's noun, made plural for any count but one."""
        return self.source.noun if self.total == 1 else self.source.noun_plural


def clean_query(raw: object) -> str:
    """A query as a picker searches by it: text, trimmed, at most the limit."""
    return str(raw or "").strip()[:PICKER_QUERY_MAX_LENGTH]


def search(queryset: QuerySet, source: PickerSource, query: str) -> QuerySet:
    """The rows of ``queryset`` that match ``query``, in the source's order.

    Args:
        queryset: The rows the viewer may pick.
        source: The kind of picker.
        query: The cleaned query; empty matches every row.

    Returns:
        The matching rows, ordered.
    """
    if query:
        matches = Q()
        for field_name in source.search_fields:
            matches |= Q(**{f"{field_name}__icontains": query})
        queryset = queryset.filter(matches)
    return queryset.order_by(*source.order_by)


def picker_page(
    source: PickerSource,
    queryset: QuerySet,
    *,
    element_id: str,
    url: str,
    search_id: str = "",
    query: str = "",
    page_number: int | str = 1,
    pinned: Sequence[Any] = (),
) -> PickerPage:
    """Search, order and page ``queryset`` for one picker.

    Args:
        source: The kind of picker.
        queryset: The rows the viewer may pick.
        element_id: The picker's DOM id.
        url: The picker's endpoint.
        search_id: The search box's DOM id; ``<element_id>-search`` if empty.
        query: What to match.
        page_number: The page asked for; anything that is not a page number
            is the nearest page (``Paginator.get_page``).
        pinned: Rows to show whatever the page.

    Returns:
        The page.
    """
    cleaned = clean_query(query)
    page = Paginator(search(queryset, source, cleaned), PAGE_SIZE).get_page(page_number)
    return PickerPage(
        source=source,
        element_id=element_id,
        search_id=search_id or f"{element_id}-search",
        url=url,
        query=cleaned,
        page=page,
        pinned=tuple(pinned),
    )


def asked_for(request: HttpRequest) -> tuple[str, str]:
    """The query and the page a picker's endpoint was asked for."""
    return (
        request.GET.get(QUERY_PARAMETER, ""),
        request.GET.get(PAGE_PARAMETER, "1"),
    )


def picker_body_response(
    request: HttpRequest, picker: PickerPage, context: Mapping[str, Any]
) -> HttpResponse:
    """The picker's body for this page, as ``picker.js`` puts it in place.

    Args:
        request: The endpoint's request (the rows' forms need its CSRF token).
        picker: The page to render.
        context: What the source's rows template reads besides ``picker``,
            the same values the full page gives it.

    Returns:
        The rendered ``_picker_body.html``.
    """
    html = render_to_string(
        "_picker_body.html", {**context, "picker": picker}, request=request
    )
    return HttpResponse(html)
