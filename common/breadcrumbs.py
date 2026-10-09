"""The site-wide navigation trail (issue #189).

Every page below the Tarmar home offers a way back up, and the trail is the
one shape that does it: it names where you are, and every step above you is a
link. Pages build their own with :func:`trail`; class-based views get it for
free through :class:`BreadcrumbMixin`.

A trail is derived from the page's own subject, never from how the reader
arrived — so it is right on a direct link, after a redirect, and after a
form post, none of which a browser-history "back" would survive.
"""

from urllib.parse import urlencode

from django.urls import reverse

# Every trail starts here: the Tarmar home is the site's root for a
# signed-in reader (the landing page sits outside the app).
ROOT_LABEL = "Tarmar"


def step(label: str, url: str = "") -> dict[str, str]:
    """One step. An empty ``url`` marks the page you are on."""
    return {"label": label, "url": url}


def trail(*steps: dict[str, str], root: bool = True) -> list[dict[str, str]]:
    """A full trail, rooted at Tarmar home unless ``root`` says otherwise.

    The last step is the current page: it is rewritten to carry no URL, so a
    caller can pass a linked step without having to remember the convention.
    """
    resolved = list(steps)
    if root:
        resolved.insert(0, step(ROOT_LABEL, reverse("home")))
    if resolved:
        resolved[-1] = step(resolved[-1]["label"])
    return resolved


def filtered(url_name: str, **params: str) -> str:
    """A list-page URL carrying its filter, so a step returns you to the
    view you were reading rather than to the top of an unfiltered list."""
    query = urlencode({key: value for key, value in params.items() if value})
    base = reverse(url_name)
    return f"{base}?{query}" if query else base


class BreadcrumbMixin:
    """Adds ``breadcrumb`` to a class-based view's context.

    Set ``breadcrumb_label`` for a fixed page, or override
    :meth:`get_breadcrumb` when the trail depends on the object.
    """

    breadcrumb_label = ""

    def get_breadcrumb(self, context):
        if not self.breadcrumb_label:
            return []
        return trail(step(self.breadcrumb_label))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["breadcrumb"] = self.get_breadcrumb(context)
        return context
