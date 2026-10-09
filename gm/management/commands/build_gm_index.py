"""``manage.py build_gm_index`` — embed the canon corpus for GM retrieval.

Chunks every markdown page under ``settings.GM_CORPUS_ROOT`` (``proposals/``
excluded), embeds the chunks through Ollama with ``settings.GM_EMBED_MODEL``,
and writes the index to ``settings.GM_INDEX_PATH`` (issue #325). Idempotent:
when the corpus and model recorded in the existing index still match, nothing
is embedded. ``deploy.sh`` runs this after ``migrate``; the offline CLI reads
the same index file.
"""

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from gm.retrieval import build_index


class Command(BaseCommand):
    help = "Chunk and embed the canon corpus into the GM retrieval index"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Rebuild even when the corpus and embedding model are unchanged",
        )

    def handle(self, *args: object, **options: object) -> None:
        try:
            result = build_index(
                corpus_root=settings.GM_CORPUS_ROOT,
                index_path=settings.GM_INDEX_PATH,
                embed_model=settings.GM_EMBED_MODEL,
                base_url=settings.OLLAMA_BASE_URL,
                force=bool(options["force"]),
            )
        except (OSError, ValueError, httpx.HTTPError) as error:
            # CommandError is how a management command exits non-zero with a
            # readable message; the cause travels with it for the traceback.
            raise CommandError(f"GM index build failed: {error}") from error
        status = "rebuilt" if result.rebuilt else "up to date"
        self.stdout.write(
            self.style.SUCCESS(
                f"GM index {status}: {result.chunk_count} chunks x "
                f"{result.dimensions} dims ({settings.GM_EMBED_MODEL}) "
                f"at {result.path}"
            )
        )
