"""Template access to the bestiary's silhouette plates (issue #193)."""

from django import template

from bestiary.art import plate_for

register = template.Library()


@register.inclusion_tag("bestiary/_beast_plate.html")
def beast_plate(beast, size="card"):
    """Render one beast's plate.

    Args:
        beast: The ``Beast`` to draw.
        size: ``"card"`` for the catalog grid, ``"detail"`` for the beast's
            own page — a class, not a pixel count, so the sheet keeps the
            sizes in one place.
    """
    return {"plate": plate_for(beast), "size": size}
