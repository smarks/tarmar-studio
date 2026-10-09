"""Template tag: the sign-in link that brings a visitor back to this page.

Pages a guest may open (the battle list, a setup, a lobby) offer "Sign in" and
must return the visitor to where they were once they have. Each of those links
was written out by hand as ``{% url 'login' %}?next={{ request.get_full_path|
urlencode }}`` (#338 item 17); this tag is that expression, defined once.
"""

from __future__ import annotations

from django import template
from django.template.defaultfilters import urlencode
from django.urls import reverse

register = template.Library()


@register.simple_tag(takes_context=True)
def sign_in_url(context: template.Context) -> str:
    """The login URL with ``next`` set to the current page, query string included.

    Args:
        context: The template context; its ``request`` supplies the path.

    Returns:
        ``/…/login/?next=<path>``, the path passed through the same
        ``urlencode`` filter the hand-written links used (``/`` left readable).
        Django escapes the result for the attribute it is written into.
    """
    login_url = reverse("login")
    request = context.get("request")
    if request is None:
        return login_url
    return f"{login_url}?next={urlencode(request.get_full_path())}"
