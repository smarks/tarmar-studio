"""Which Django apps belong to this repository (shared by the migration tests).

The migration guards (``test_migration_safety`` and
``test_migration_dependencies``) both have to tell this repository's apps from
Django's and the installed packages'. They share this one rule so the two
cannot drift apart (#979).
"""

from pathlib import Path

from django.apps import apps

from reference.paths import REPO_ROOT


def is_this_repositorys_app(app_label: str) -> bool:
    """Whether ``app_label`` is one of this repository's own apps.

    By the app's own directory, a top-level package of the checkout. A test
    of where each migration's file lives is not enough: a worktree's
    ``.venv`` sits inside the checkout too, so Django's own ``auth`` and
    ``contenttypes`` migrations would count as this repository's.

    Args:
        app_label: The app's label, such as ``"gm"``.

    Returns:
        True when the app's directory is directly under the repository root.
    """
    return Path(apps.get_app_config(app_label).path).resolve().parent == REPO_ROOT
