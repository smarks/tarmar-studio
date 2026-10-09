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
