"""``manage.py generate_beast`` — scripted counterpart of the GM generate page.

Examples:
    manage.py generate_beast --name "Dire Wolf"
    manage.py generate_beast --category dragons
    manage.py generate_beast --difficulty deadly --seed 42
"""

import random

from django.core.management.base import BaseCommand, CommandError

from bestiary.generation import DIFFICULTY_BANDS, generate_variant, pick_base
from bestiary.models import CATEGORY_CHOICES, Beast


class Command(BaseCommand):
    help = "Generate a beast variant: exact by name, or random by category/difficulty"

    def add_arguments(self, parser):
        parser.add_argument("--name", help="Exact catalog beast name (or slug)")
        parser.add_argument(
            "--category", choices=[key for key, _label in CATEGORY_CHOICES]
        )
        parser.add_argument("--difficulty", choices=list(DIFFICULTY_BANDS))
        parser.add_argument("--seed", type=int, help="Seed for reproducible variants")

    def handle(self, *args, **options):
        rng = random.Random(options["seed"]) if options["seed"] is not None else None
        try:
            base = pick_base(
                name=options["name"],
                category=options["category"],
                difficulty=options["difficulty"],
                rng=rng,
            )
        except Beast.DoesNotExist as error:
            raise CommandError(str(error)) from error
        variant = generate_variant(base, rng=rng)
        self.stdout.write(
            self.style.SUCCESS(
                f"Generated {variant.name} (from {base.name}, p. {base.source_page}): "
                f"STR {variant.strength} DEX {variant.dexterity} "
                f"INT {variant.intelligence} WIS {variant.wisdom} "
                f"CON {variant.constitution} CHA {variant.charisma} | "
                f"fatigue {variant.fatigue_pool} body {variant.body_pool} | "
                f"{variant.armour_tier} armour ({variant.stops} stops) | "
                f"MA {variant.movement}, {variant.size_hexes} hex(es)"
            )
        )
