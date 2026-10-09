"""List migrations applied to the database that a checkout's code lacks.

``deploy.sh rollback`` runs this from the standby colour's release checkout,
with ``.env`` loaded, before it points nginx back at that colour (issue #575).
Any migration applied to the shared database but missing from the standby's
code means the standby's schema expectations are out of date, so the rollback
is refused. Exit status 0 means the standby knows every applied migration; 1
means it does not, and the missing ones are printed.

deploy.sh feeds the main checkout's copy to the standby's Python on stdin, so
a standby whose revision predates this script is still checked.
"""

import os
import sys
from collections.abc import Iterable


def missing_migrations(
    applied: Iterable[tuple[str, str]], on_disk: Iterable[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Applied migrations with no file in the checkout, sorted."""
    return sorted(set(applied) - set(on_disk))


def main() -> int:
    """Compare the database's applied migrations with this checkout's files."""
    sys.path.insert(0, os.getcwd())
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tarmar_studio.settings")
    import django

    django.setup()
    from django.db import connection
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(connection)
    missing = missing_migrations(loader.applied_migrations, loader.disk_migrations)
    for app_label, name in missing:
        print(f"  {app_label}.{name}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
