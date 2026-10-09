"""Tests for the rollback migration check (issue #575)."""

import pytest
import rollback_migrations
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder


def test_missing_migrations_lists_applied_ones_the_checkout_lacks() -> None:
    applied = [("gm", "0012_new"), ("gm", "0011_old"), ("auth", "0001_initial")]
    on_disk = [("gm", "0011_old"), ("auth", "0001_initial")]
    assert rollback_migrations.missing_migrations(applied, on_disk) == [
        ("gm", "0012_new")
    ]


def test_missing_migrations_is_empty_when_the_checkout_is_ahead() -> None:
    applied = [("gm", "0011_old")]
    on_disk = [("gm", "0011_old"), ("gm", "0012_new")]
    assert rollback_migrations.missing_migrations(applied, on_disk) == []


@pytest.mark.django_db
def test_main_passes_on_this_checkout_and_refuses_an_unknown_migration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert rollback_migrations.main() == 0
    MigrationRecorder(connection).record_applied("gm", "9999_from_the_future")
    assert rollback_migrations.main() == 1
    assert "gm.9999_from_the_future" in capsys.readouterr().out
