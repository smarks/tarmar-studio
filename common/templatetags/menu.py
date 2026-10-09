"""Template tag: one entry in the site menu (#338 item 28).

base.html wrote every menu entry by hand as
``<a href="{% url … %}" class="submenu{% if active_page == '…' %} disabled{% endif %}">``,
sixteen times across two theme branches. ``{% menu_link %}`` is that markup,
defined once:

    {% menu_link 'battle:list' 'Battles' page='battles' %}
    {% menu_link 'characters:edit' 'Character Sheet' pk=character.pk page='character_editor' %}
    {% menu_link 'profile' label page='profile' submenu=False %}

``page`` names the ``active_page`` value that marks this entry as the page you
are on; the entry then carries ``disabled``, which base.css greys out. An entry
without ``page`` is never marked. ``submenu=False`` is the unindented entry the
account links use, which has no class at all unless it is the current page.
"""

from __future__ import annotations

from django import template
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString

register = template.Library()


@register.simple_tag(takes_context=True)
def menu_link(
    context: template.Context,
    view_name: str,
    label: str,
    pk: int | str | None = None,
    page: str | None = None,
    submenu: bool = True,
) -> SafeString:
    """Render one menu anchor in the markup base.html's menu uses.

    Args:
        context: The template context; ``active_page`` is read from it.
        view_name: The URL name to link to.
        label: The visible text, escaped.
        pk: A primary key for URLs that take one.
        page: The ``active_page`` value that marks this entry as current.
        submenu: True for an indented entry (``class="submenu"``).

    Returns:
        The anchor element, safe to insert.
    """
    url = reverse(view_name, args=[pk] if pk is not None else None)
    is_current = page is not None and context.get("active_page") == page
    classes = " ".join(
        name
        for name, applies in (("submenu", submenu), ("disabled", is_current))
        if applies
    )
    if classes:
        return format_html('<a href="{}" class="{}">{}</a>', url, classes, label)
    return format_html('<a href="{}">{}</a>', url, label)
