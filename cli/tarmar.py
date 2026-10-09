"""Tarmar -- CLI for the AI Game Master and the battle simulator."""

import argparse
import os
import sys


def cmd_play(_args: argparse.Namespace) -> None:
    """Launch the RPG Game Master session."""
    from .gm import main as gm_main

    gm_main()


def cmd_simulate(args: argparse.Namespace) -> None:
    """Run a battle-royale simulation (issue #176).

    The simulator generates real Character rows, so it needs Django — the
    work lives in the ``simulate`` management command (one implementation for
    this subcommand, ``./run simulate``, and ``manage.py simulate``), and
    this wrapper only boots Django around it.
    """
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tarmar_studio.settings")
    import django

    django.setup()
    from django.core.management import call_command

    # Everything after `tarmar simulate` goes to the command untouched (issue
    # #337, item 13). This wrapper used to re-declare every option with its
    # own default of 8 characters where the command defaults to
    # DEFAULT_COMBATANTS, so the two drifted; the options now exist only on
    # the management command.
    call_command("simulate", *args.simulate_args)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="tarmar",
        description="Tarmar -- AI Game Master",
    )
    subparsers = parser.add_subparsers(dest="command")

    # tarmar play
    sub_play = subparsers.add_parser("play", help="Launch the RPG Game Master")
    sub_play.set_defaults(func=cmd_play)

    # tarmar simulate
    # The options are the management command's own: whatever follows
    # `tarmar simulate` is handed to it below. add_help=False lets
    # `tarmar simulate --help` reach the command and print its help.
    sub_simulate = subparsers.add_parser(
        "simulate", help="Run a battle-royale simulation", add_help=False
    )
    sub_simulate.set_defaults(func=cmd_simulate)

    # parse_known_args, not parse_args: argparse.REMAINDER on a subcommand does
    # not collect arguments that start with "--", which is every simulate
    # option. Only simulate accepts arguments this parser does not know.
    args, extra = parser.parse_known_args()
    if extra and args.command != "simulate":
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    args.simulate_args = extra

    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
