"""Beast — a creature from the Tarmar bestiary, in Tarmar-native terms.

Numeric stats and attack/ability mechanics derive from The Fantasy Trip
Bestiary (Steve Jackson Games) via the deterministic mapping in
``bestiary/translation.py``; ``source_page`` cites where each entry's numbers
come from. Every ``description`` is original prose written for this project —
never the book's text.
"""

from django.db import models
from django.urls import reverse
from django.utils import timezone

from characters.combat import ARMOUR_TIERS

# The book's table-of-contents sections, grouped into browsable families.
# Keys are stored in the DB; labels render in the catalog filter.
CATEGORY_CHOICES = [
    ("insects_vermin", "Insects & Vermin"),
    ("mammals", "Mammals"),
    ("birds", "Birds"),
    ("reptiles_amphibians", "Reptiles & Amphibians"),
    ("sea_creatures", "Sea Creatures"),
    ("dragons", "Dragons & Dragonkin"),
    ("elementals", "Elementals"),
    ("undead_spirits", "Undead & Spirits"),
    ("plants", "Plants"),
    ("constructs", "Constructs"),
    ("legendary", "Legendary"),
]

ARMOUR_TIER_CHOICES = [(tier, tier) for tier in ARMOUR_TIERS]


class Beast(models.Model):
    """One creature: catalog entry (from the fixture) or GM-generated variant.

    ``attacks`` is a list of ``{"name", "damage", "weapon_class"}`` dicts whose
    ``weapon_class`` values are keys of :data:`characters.combat.MATRIX`, so a
    beast's natural attacks resolve through the same §6 matrix as weapons.
    """

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, db_index=True)
    description = models.TextField()

    # Tarmar-native attributes. Beasts range far beyond the 3-18 humanoid
    # band (an elder dragon carries ST 100), so no attribute validators here.
    strength = models.PositiveSmallIntegerField()
    dexterity = models.PositiveSmallIntegerField()
    intelligence = models.PositiveSmallIntegerField()
    wisdom = models.PositiveSmallIntegerField()
    constitution = models.PositiveSmallIntegerField()
    charisma = models.PositiveSmallIntegerField()

    # Derived pools, stored (not recomputed) so a generated variant's bounded
    # variation stays exactly what the generator rolled.
    fatigue_pool = models.PositiveSmallIntegerField()
    body_pool = models.PositiveSmallIntegerField()

    armour_tier = models.CharField(
        max_length=10, choices=ARMOUR_TIER_CHOICES, default="None"
    )
    stops = models.PositiveSmallIntegerField(default=0)

    attacks = models.JSONField(default=list, blank=True)
    special_abilities = models.TextField(blank=True, default="")

    size_hexes = models.PositiveSmallIntegerField(default=1)
    movement = models.PositiveSmallIntegerField()

    source_page = models.PositiveSmallIntegerField(null=True, blank=True)

    is_generated = models.BooleanField(default=False, db_index=True)
    base_beast = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="variants",
    )
    # A default rather than auto_now_add: catalog rows arrive via ``loaddata``,
    # whose raw saves skip auto_now_add and trip the NOT NULL constraint.
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    def get_absolute_url(self):
        """This beast's own page — the one link every template needs."""
        return reverse("bestiary:detail", args=[self.slug])

    class Meta:
        ordering = ["category", "name"]

    def __str__(self) -> str:
        return self.name
