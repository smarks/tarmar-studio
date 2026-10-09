"""``manage.py simulate`` — run a battle royale in the terminal.

This is the engine the web UI uses, character generation included, printing
the same event lines the battle page's log shows. It is a management command
(rather than a standalone script) because generation builds real Character
rows and therefore needs Django configured; the ``tarmar simulate``
subcommand and the ``./run simulate`` task both wrap this one implementation.
"""

from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from battle.models import Battle
from battle.services import (
    DEFAULT_COMBATANTS,
    MAX_COMBATANTS,
    MIN_COMBATANTS,
    advance_turn,
    create_battle,
)

# Owner of simulator-created battles and characters when run from the CLI.
SIMULATOR_USERNAME = "simulator"

# Hard stop so a stalemate of mutual misses cannot loop forever.
MAX_TURNS = 100


class Command(BaseCommand):
    help = "Simulate a battle royale, printing every roll and decision"

    def add_arguments(self, parser):
        parser.add_argument(
            "--n",
            type=int,
            default=DEFAULT_COMBATANTS,
            help=(
                f"Number of generated characters (characters + beasts must "
                f"total {MIN_COMBATANTS}-{MAX_COMBATANTS})"
            ),
        )
        parser.add_argument(
            "--seed", type=int, default=None, help="Deterministic battle seed"
        )
        parser.add_argument(
            "--step",
            action="store_true",
            help="Pause for Enter between turns",
        )
        parser.add_argument(
            "--beasts",
            type=int,
            default=0,
            help="Number of random beasts drafted from the bestiary catalog",
        )
        parser.add_argument(
            "--beast-category",
            default="",
            help="Restrict the random beast draft to one catalog category",
        )
        parser.add_argument(
            "--beast-difficulty",
            default="",
            help="Restrict the draft to a difficulty band (easy/moderate/hard/deadly)",
        )

    def handle(self, *args, **options):
        from bestiary.models import Beast

        count = options["n"]
        user, _created = User.objects.get_or_create(username=SIMULATOR_USERNAME)
        try:
            battle = create_battle(
                user,
                name=f"Simulation {Battle.objects.count() + 1}",
                count=count,
                seed=options["seed"],
                beast_count=options["beasts"],
                beast_category=options["beast_category"],
                beast_difficulty=options["beast_difficulty"],
            )
        except (ValueError, Beast.DoesNotExist) as error:
            raise CommandError(str(error)) from error
        total = len(battle.state_json["combatants"])
        self.stdout.write(
            f"{battle.name}: {total} combatants, seed {battle.seed}, "
            f"arena radius {battle.state_json['arena_radius']}"
        )
        for combatant in battle.state_json["combatants"]:
            size_note = (
                f", {combatant['size_hexes']} hexes"
                if combatant.get("size_hexes", 1) > 1
                else ""
            )
            self.stdout.write(
                f"  {combatant['name']} — {combatant['archetype']}, "
                f"{combatant['weapon']['name']}, "
                f"fatigue {combatant['fatigue']}, body {combatant['body']}"
                f"{size_note}"
            )

        while battle.status not in (
            Battle.STATUS_FINISHED,
            Battle.STATUS_STALEMATE,
        ):
            if battle.turn_number >= MAX_TURNS:
                self.stdout.write(f"Stopped after {MAX_TURNS} turns (still going)")
                break
            if options["step"] and battle.turn_number > 0:
                input("-- Enter for next turn --")  # pragma: no cover
            battle, events = advance_turn(battle.pk)
            for event in events:
                self.stdout.write(f"[{event.sequence:>4}] {event.message}")
        if battle.status == Battle.STATUS_FINISHED:
            ending = "finished"
        elif battle.status == Battle.STATUS_STALEMATE:
            ending = "ended in stalemate"
        else:
            # Only the turn cap leaves the loop with the battle still going
            # (issue #877): it used to say "finished" here too.
            ending = "stopped at the turn cap"
        self.stdout.write(f"{battle.name} {ending} after {battle.turn_number} turn(s)")
