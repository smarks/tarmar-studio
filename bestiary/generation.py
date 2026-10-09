"""GM beast generation: catalog picks and bounded stat variants.

A generated beast is a saved ``Beast`` row flagged ``is_generated=True`` whose
``base_beast`` points at the catalog entry it was varied from. Stats vary
within ± :data:`VARIATION` of the base (each stat independently), pools are
re-derived from the varied stats through the same translation formulas, and
the derived armour tier is re-read from the varied stops.
"""

import random

from django.db.models import Q

from characters.constants import CHARACTER_ATTRIBUTE_FIELDS

from .models import Beast
from .translation import derive_pools, tier_for_stops

# Bound on per-stat variation for generated variants.
VARIATION = 0.15

# Difficulty bands by base-beast strength: a quick GM-facing knob that maps
# onto the catalog's natural ST spread.
DIFFICULTY_BANDS: dict[str, tuple[int, int | None]] = {
    "easy": (1, 9),
    "moderate": (10, 24),
    "hard": (25, 59),
    "deadly": (60, None),
}

# A beast carries the same six attributes a character does.
_VARIED_STATS = (
    *CHARACTER_ATTRIBUTE_FIELDS,
    "movement",
    "stops",
)


def _varied(value: int, rng: random.Random) -> int:
    """``value`` shifted by a uniform ±VARIATION factor, floored at its band.

    Never drops a positive stat to zero, and never moves a zero (a rooted
    plant's movement stays 0).
    """
    if value == 0:
        return 0
    low = round(value * (1 - VARIATION))
    high = round(value * (1 + VARIATION))
    return max(1, rng.randint(min(low, high), max(low, high)))


def catalog_queryset():
    """The browsable catalog: fixture beasts only, never generated variants."""
    return Beast.objects.filter(is_generated=False)


def pick_base(
    *,
    name: str | None = None,
    category: str | None = None,
    difficulty: str | None = None,
    rng: random.Random | None = None,
) -> Beast:
    """Choose the catalog beast to vary: exact name, or random within filters.

    Raises:
        Beast.DoesNotExist: if no catalog beast matches the request.
        ValueError: if ``difficulty`` is not a known band.
    """
    rng = rng or random.Random()
    queryset = catalog_queryset()
    if name:
        return queryset.get(Q(name__iexact=name) | Q(slug=name))
    if category:
        queryset = queryset.filter(category=category)
    if difficulty:
        if difficulty not in DIFFICULTY_BANDS:
            raise ValueError(f"Unknown difficulty band: {difficulty}")
        minimum, maximum = DIFFICULTY_BANDS[difficulty]
        queryset = queryset.filter(strength__gte=minimum)
        if maximum is not None:
            queryset = queryset.filter(strength__lte=maximum)
    identifiers = list(queryset.values_list("pk", flat=True))
    if not identifiers:
        raise Beast.DoesNotExist("No catalog beast matches the request")
    return queryset.get(pk=rng.choice(identifiers))


def _unique_slug(base_slug: str) -> str:
    suffix = 1
    while True:
        candidate = f"{base_slug}-variant-{suffix}"
        if not Beast.objects.filter(slug=candidate).exists():
            return candidate
        suffix += 1


def generate_variant(base: Beast, rng: random.Random | None = None) -> Beast:
    """Save and return a ±15% variant of ``base``, flagged as generated."""
    rng = rng or random.Random()
    varied = {stat: _varied(getattr(base, stat), rng) for stat in _VARIED_STATS}
    fatigue_pool, body_pool = derive_pools(
        varied["strength"],
        varied["dexterity"],
        varied["intelligence"],
        varied["wisdom"],
        varied["constitution"],
    )
    armour_tier = tier_for_stops(varied["stops"])
    variant_number = base.variants.count() + 1
    return Beast.objects.create(
        name=f"{base.name} (variant {variant_number})",
        slug=_unique_slug(base.slug),
        category=base.category,
        description=base.description,
        fatigue_pool=fatigue_pool,
        body_pool=body_pool,
        armour_tier=armour_tier,
        attacks=base.attacks,
        special_abilities=base.special_abilities,
        size_hexes=base.size_hexes,
        source_page=base.source_page,
        is_generated=True,
        base_beast=base,
        **varied,
    )
