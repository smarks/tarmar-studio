"""Print a GM session's whole transcript (issue #389).

The same document the chat page's Transcript button downloads, for reading a
session over ssh without a browser or the owner's login::

    uv run python manage.py gm_transcript 28
    uv run python manage.py gm_transcript 28 --format json
"""

import json

from django.core.management.base import BaseCommand, CommandError, CommandParser

from gm.models import GMSession
from gm.transcript import render_transcript_text, transcript_payload


class Command(BaseCommand):
    help = "Print a GM session's full transcript, hidden entries and turn records included."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("session_id", type=int)
        parser.add_argument("--format", choices=("text", "json"), default="text")

    def handle(self, *args: object, **options: object) -> None:
        session_id = options["session_id"]
        try:
            session = GMSession.objects.get(pk=session_id)
        except GMSession.DoesNotExist as error:
            raise CommandError(f"No GM session {session_id}") from error
        if options["format"] == "json":
            self.stdout.write(
                json.dumps(
                    transcript_payload(session),
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            )
        else:
            self.stdout.write(render_transcript_text(session), ending="")
