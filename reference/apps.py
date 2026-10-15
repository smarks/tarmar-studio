import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


def watch_canon_files(sender: object, **kwargs: object) -> None:
    """Have ``runserver`` restart when the canon's import-time files change.

    ``nav.json``, ``maps.json`` and ``canon.json`` (issue #918), the GM's
    world digest under ``gm-prompt/`` (issue #919), ``magic-rules.json``
    (issue #960) and ``character-rules.json`` (issue #59) are read once per
    process, so an edit to one reaches the site only through a restart.
    Connected to ``autoreload_started``, whose sender is the reloader.

    Args:
        sender: Django's autoreloader.
        **kwargs: The signal's other arguments, unused.
    """
    from reference.paths import DM_RULES_DIR, WORLD_PROMPT_FILE

    sender.watch_dir(DM_RULES_DIR, "*.json")  # type: ignore[attr-defined]
    sender.watch_dir(WORLD_PROMPT_FILE.parent, "*.md")  # type: ignore[attr-defined]


class ReferenceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "reference"

    def ready(self) -> None:
        """Pre-warm the reference search index at app startup."""
        # Local import: AppConfig.ready is called before all imports settle, and
        # search_index pulls in django.conf settings which is fine post-setup.
        from django.utils.autoreload import autoreload_started

        from . import search_index

        autoreload_started.connect(watch_canon_files)

        try:
            search_index.build_index()
        except (
            Exception
        ):  # pragma: no cover - defensive against unforeseen bootstrap errors
            logger.exception("Reference search index pre-warm failed")
