"""``manage.py generate_hook`` — scripted counterpart of the hook generator page.

Examples:
    manage.py generate_hook --band 1
    manage.py generate_hook --band 2 --seed 42
    manage.py generate_hook --party "The Torchbearers"
    manage.py generate_hook --party 3 --seed 7
"""

from django.core.management.base import BaseCommand, CommandError

from ruins.hook_generation import (
    NoHooksForLevel,
    generate_hook,
    matched_level,
)


class Command(BaseCommand):
    help = "Roll one adventure hook, matched to a party's experience or a band"

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument(
            "--party",
            help="Party to match: its id or its exact name",
        )
        target.add_argument(
            "--band",
            type=int,
            help="Experience level band to roll in, without a party",
        )
        parser.add_argument("--seed", type=int, help="Seed for a reproducible hook")

    def handle(self, *args, **options):
        if options["party"] is not None:
            level = self.level_for_party(options["party"])
        else:
            level = options["band"]

        try:
            generated = generate_hook(level=level, seed=options["seed"])
        except NoHooksForLevel as outgrown:
            raise CommandError(
                f"{outgrown} This party has outgrown local trouble — "
                "see Scenario Seeds (dm-rules/scenario-seeds)."
            ) from outgrown

        seed_note = f" (seed {generated.seed})" if generated.seed is not None else ""
        self.stdout.write(
            self.style.MIGRATE_HEADING(f"Level {generated.level} hook{seed_note}")
        )
        roll = generated.roll
        self.stdout.write(
            f"  {generated.eligible_count} eligible hooks  [{roll.die} → {roll.face}]"
        )
        hook = generated.hook
        self.stdout.write(self.style.MIGRATE_HEADING(hook.title))
        self.stdout.write(
            f"  {hook.section} · {hook.hook_type} · Levels {hook.band_label}"
        )
        self.stdout.write(f"  Region: {hook.region_markdown}")
        self.stdout.write("")
        self.stdout.write(hook.body_markdown)

    def level_for_party(self, party_argument: str) -> int:
        """Resolve --party (id or exact name) to the level it rolls as."""
        from parties.models import Party

        try:
            if party_argument.isdigit():
                party = Party.objects.get(pk=int(party_argument))
            else:
                party = Party.objects.get(name=party_argument)
        except Party.DoesNotExist as missing:
            raise CommandError(f"No party matches “{party_argument}”.") from missing
        summary = party.experience_summary
        if summary is None:
            raise CommandError(
                f"{party.name} has no members to match hooks against — "
                "seat a character first, or use --band."
            )
        level = matched_level(summary["average_level"])
        self.stdout.write(
            f"{party.name}: {summary['count']} member(s), levels "
            f"{summary['min_level']}–{summary['max_level']}, average "
            f"{summary['average_level']:.1f} → rolls as level {level}"
        )
        return level
