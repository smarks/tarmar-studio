"""Template tag: render a small `?` disclosure with help text for a game term."""

from django import template
from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe

from battle.services import unarmed_line
from characters.help_text import help_text

register = template.Library()


@register.simple_tag
def help_for(key: str) -> SafeString:
    """Render the inline help disclosure for `key`.

    Returns an empty string when no entry exists, so callers can drop the tag
    next to any labelled element without conditionals. Help text is treated as
    trusted authored content (lives in characters/help_text.py, never reaches
    user input), so the body isn't HTML-escaped — future entries can include
    links or emphasis if needed.
    """
    text = help_text().get(key)
    if not text:
        return SafeString("")
    label = escape(key.replace("_", " "))
    return mark_safe(
        f'<details class="help"><summary aria-label="Help for {label}" '
        f'title="Show help">?</summary>'
        f'<div class="help-body">{text}</div></details>'
    )


@register.simple_tag
def unarmed_for(name: str) -> str:
    """That ``name`` carries no weapon and would fight bare-handed.

    The sheet's gear step (issue #844), in the configured words the session's
    Battle tab uses (``[services] unarmed_one``, issue #136). Escaped by the
    template, as any string a simple tag returns is.
    """
    return unarmed_line([name])
