"""Template tag: the battle status labels, for battle.js (#338 item 20).

battle.js repaints the status chip from each advance and poll response, which
carries the status code ("awaiting"), not its label. It used to keep its own
STATUS_LABELS table restating Battle.STATUS_CHOICES, which would drift the day
a label changed. The page now hands it the model's labels:

    {% load battle_status %}{% battle_status_labels %}

renders ``<script id="battle-status-labels" type="application/json">`` with
``{"lobby": "Lobby", "awaiting": "Awaiting players", ...}``.
"""

from __future__ import annotations

from django import template
from django.utils.html import json_script
from django.utils.safestring import SafeString

from battle.models import Battle

register = template.Library()

STATUS_LABELS_ELEMENT_ID = "battle-status-labels"


@register.simple_tag
def battle_status_labels() -> SafeString:
    """The model's status labels as a JSON script element battle.js reads."""
    return json_script(dict(Battle.STATUS_CHOICES), STATUS_LABELS_ELEMENT_ID)
