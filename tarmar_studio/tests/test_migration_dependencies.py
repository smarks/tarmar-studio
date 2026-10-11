"""Every data migration runs when Django applies only what it depends on (#974).

A full ``migrate`` on a fresh database applies every app, and the order it
happens to pick can hide a missing dependency: ``gm.0007`` filtered on
``user__is_role_admin`` while depending only on ``accounts.0001``, and it
worked until a targeted ``migrate battle 0017`` on a fresh Postgres database
ran it before ``accounts.0002`` added the column.

So each of this repository's own migrations that reads or writes rows (a
``RunPython`` or ``RunSQL``; Django's and installed packages' migrations are
not counted) is applied here on its own fresh SQLite database, which
receives its dependency closure and nothing else, and then unapplied again.
A read of a model or field the closure does not provide fails at once:
``apps.get_model`` raises ``LookupError`` for an app outside it, and a lookup
on a missing field raises ``FieldError`` while the query is built, before any
row is needed.

What an empty database cannot show is a field read as an attribute of a row,
since there are no rows. The migrations that read the user model's role flags
that way are pinned by name below (``ROLE_FLAG_READERS``).

The data functions query through the model manager, which follows the
database routers rather than the schema editor's connection, so every read
and write is routed to the fresh database for the length of the run.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from django.db import connections, migrations
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import override_settings

from tarmar_studio.tests.repository_apps import is_this_repositorys_app

#: The connection alias each targeted run uses.
FRESH_ALIAS = "fresh_for_migration_dependencies"

Node = tuple[str, str]


class EverythingToTheFreshDatabase:
    """Route every model read and write to the fresh database."""

    def db_for_read(self, model: type, **hints: object) -> str:
        """The fresh database, for every model."""
        return FRESH_ALIAS

    def db_for_write(self, model: type, **hints: object) -> str:
        """The fresh database, for every model."""
        return FRESH_ALIAS


ROUTER_PATH = f"{__name__}.{EverythingToTheFreshDatabase.__name__}"


def touches_rows(migration: migrations.Migration) -> bool:
    """Whether ``migration`` runs code or SQL against the rows."""
    return any(
        isinstance(operation, migrations.RunPython | migrations.RunSQL)
        for operation in migration.operations
    )


def local_data_migrations() -> list[Node]:
    """This repository's migrations that read or write rows, in name order."""
    loader = MigrationLoader(None, ignore_no_migrations=True)
    return sorted(
        node
        for node, migration in loader.disk_migrations.items()
        if is_this_repositorys_app(node[0]) and touches_rows(migration)
    )


@pytest.fixture
def fresh_database(tmp_path: Path, django_db_blocker) -> Iterator[str]:
    """An empty SQLite database under ``FRESH_ALIAS``, removed afterwards."""
    settings_dict = dict(connections["default"].settings_dict)
    settings_dict["ENGINE"] = "django.db.backends.sqlite3"
    settings_dict["NAME"] = str(tmp_path / "fresh.sqlite3")
    settings_dict["OPTIONS"] = {}
    settings_dict["TEST"] = {"NAME": settings_dict["NAME"]}
    connections.settings[FRESH_ALIAS] = settings_dict
    try:
        with django_db_blocker.unblock():
            yield FRESH_ALIAS
    finally:
        connections[FRESH_ALIAS].close()
        del connections[FRESH_ALIAS]
        del connections.settings[FRESH_ALIAS]


def previous_in_app(executor: MigrationExecutor, node: Node) -> Node | tuple[str, None]:
    """The target that unapplies ``node`` alone: its parent in the same app."""
    parents = [
        parent
        for parent in executor.loader.graph.node_map[node].parents
        if parent.key[0] == node[0]
    ]
    return parents[0].key if parents else (node[0], None)


def apply_then_unapply(alias: str, node: Node) -> None:
    """Apply ``node`` with its dependencies only, then unapply ``node``."""
    connection = connections[alias]
    with override_settings(DATABASE_ROUTERS=[ROUTER_PATH]):
        executor = MigrationExecutor(connection)
        executor.migrate([node])
        executor = MigrationExecutor(connection)
        applied = executor.loader.applied_migrations
        assert node in applied, f"{node} was not applied"
        executor.migrate([previous_in_app(executor, node)])
        executor = MigrationExecutor(connection)
        assert node not in executor.loader.applied_migrations


def test_the_sweep_holds_this_repositorys_data_migrations_only() -> None:
    found = local_data_migrations()
    assert ("gm", "0007_gmsession_mode") in found
    assert {app_label for app_label, _ in found} <= {
        "accounts",
        "battle",
        "bestiary",
        "characters",
        "common",
        "gm",
    }


@pytest.mark.parametrize("node", local_data_migrations(), ids="{0[0]}.{0[1]}".format)
def test_data_migration_runs_on_its_dependencies_alone(
    fresh_database: str, node: Node
) -> None:
    apply_then_unapply(fresh_database, node)


#: Migrations whose data functions read the user model's role flags, which
#: no lookup would catch on an empty table (``battle.0015`` reads them as
#: attributes of each session's owner).
ROLE_FLAG_READERS = [
    ("gm", "0007_gmsession_mode"),
    ("battle", "0015_session_fights_at_deploy"),
]

ROLE_FLAGS_MIGRATION = ("accounts", "0002_user_is_dm_user_is_player_user_is_role_admin")


@pytest.mark.parametrize("node", ROLE_FLAG_READERS, ids="{0[0]}.{0[1]}".format)
def test_role_flag_readers_name_the_accounts_migration_that_adds_them(
    node: Node,
) -> None:
    """The instance #974 found and its sibling, pinned by name."""
    loader = MigrationLoader(None, ignore_no_migrations=True)
    assert ROLE_FLAGS_MIGRATION in loader.graph.nodes[node].dependencies
