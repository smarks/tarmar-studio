"""``manage.py generate_ruin`` — scripted counterpart of the generator page.

Examples:
    manage.py generate_ruin
    manage.py generate_ruin --rooms 3 --seed 42
"""

from django.core.management.base import BaseCommand, CommandError

from ruins.generation import (
    DEFAULT_ROOMS,
    MAX_ROOMS,
    MIN_ROOMS,
    TableRoll,
    generate_ruin,
)


def describe(roll: TableRoll) -> str:
    """One printed line for a table roll: label, result, and the roll itself."""
    return f"{roll.table_title} {roll.result}  [{roll.die} → {roll.face}]"


class Command(BaseCommand):
    help = "Roll the Dungeons & Ruins tables and print a populated site"

    def add_arguments(self, parser):
        parser.add_argument(
            "--rooms",
            type=int,
            default=DEFAULT_ROOMS,
            help=f"Number of rooms to roll ({MIN_ROOMS}–{MAX_ROOMS})",
        )
        parser.add_argument("--seed", type=int, help="Seed for a reproducible ruin")

    def handle(self, *args, **options):
        try:
            ruin = generate_ruin(room_count=options["rooms"], seed=options["seed"])
        except ValueError as error:
            raise CommandError(str(error)) from error

        seed_note = f" (seed {ruin.seed})" if ruin.seed is not None else ""
        self.stdout.write(self.style.MIGRATE_HEADING(f"The Site{seed_note}"))
        for roll in ruin.site_rolls:
            self.stdout.write(f"  {describe(roll)}")
        for room in ruin.rooms:
            self.stdout.write(self.style.MIGRATE_HEADING(f"Room {room.number}"))
            self.stdout.write(f"  {describe(room.contents)}")
            self.stdout.write(f"  {describe(room.hazard)}")
        self.stdout.write(self.style.MIGRATE_HEADING("Treasure"))
        self.stdout.write(f"  {describe(ruin.treasure_type)}")
        self.stdout.write(f"  {describe(ruin.treasure_complication)}")
