"""What every paged list shares (issue #795).

Each ``ListView`` with a ``paginate_by`` turns its pages with
``templates/_pagination.html``; the page size and the query string its links
keep are stated here once, rather than in each view.
"""

from django.http import HttpRequest

#: Rows a paged list shows at once: the size every paginated ``ListView``
#: in the project uses.
PAGE_SIZE = 50


def query_without(request: HttpRequest, *parameters: str) -> str:
    """The request's query string less ``parameters``, for a pager's links.

    A pager turns its own page and must keep everything else the page was
    asked for: a filter, another list's page.

    Args:
        request: The request whose query string to keep.
        *parameters: The parameters to leave out.

    Returns:
        The rest of the query string, URL-encoded, or an empty string.
    """
    preserved = request.GET.copy()
    for parameter in parameters:
        preserved.pop(parameter, None)
    return preserved.urlencode()


def latest_window(
    count: int, before: int | None = None, size: int = PAGE_SIZE
) -> tuple[int, int]:
    """The rows a "latest first, show earlier" page shows, as ``[start, end)``.

    A conversation reads oldest first and opens at its newest end, so its
    pages are counted back from the end: the first page is the last ``size``
    rows, and each earlier page the ``size`` rows before the one shown
    (issue #972). ``before`` is clamped into the list, so a stale cursor
    shows what is there rather than failing.

    Args:
        count: How many rows the list holds.
        before: The first row already shown, whose predecessors this page
            holds; ``None`` for the latest page.
        size: Rows a page holds.

    Returns:
        ``(start, end)``: the page is rows ``start`` up to, not including,
        ``end``; ``start`` rows come before it.
    """
    end = count if before is None else max(0, min(before, count))
    return max(0, end - size), end
