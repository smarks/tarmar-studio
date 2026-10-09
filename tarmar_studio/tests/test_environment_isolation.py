"""The suite must not inherit the git or deploy variables of its caller.

The guard itself is the session-scoped fixture in the root ``conftest.py``;
these tests hold it to what it promises, including the failure it exists to
prevent (#725).
"""

import os
import subprocess
from pathlib import Path

import pytest

from conftest import (
    INHERITED_VARIABLE_PREFIXES,
    PRESERVED_VARIABLE_NAMES,
    remove_inherited_environment_variables,
)


def make_bare_repository(tmp_path: Path) -> Path:
    """Build a throwaway bare repository to stand in for the real checkout.

    Args:
        tmp_path: The test's temporary directory.

    Returns:
        The path of the bare repository.
    """
    bare_repository = tmp_path / "inherited.git"
    subprocess.run(
        ["git", "init", "--bare", "--quiet", str(bare_repository)],
        capture_output=True,
        check=True,
    )
    return bare_repository


def configured_value(repository: Path, key: str) -> str:
    """Read one config value written into a repository, empty when it is unset.

    ``--local`` matters: without it a missing key falls back to the developer's
    global config, and the identity there would hide a value the repository
    never had.

    Args:
        repository: The repository to read, given as ``--git-dir``.
        key: The config key, such as ``core.bare``.

    Returns:
        The stripped value, or the empty string when the key is not set.
    """
    reading = subprocess.run(
        ["git", "--git-dir", str(repository), "config", "--local", "--get", key],
        capture_output=True,
        text=True,
        check=False,
        env={name: value for name, value in os.environ.items() if name != "GIT_DIR"},
    )
    return reading.stdout.strip()


def test_no_inherited_git_or_deploy_variable_reaches_a_test() -> None:
    """Nothing a caller exported survives into the environment tests run in."""
    surviving_variable_names = [
        name
        for name in os.environ
        if name.startswith(INHERITED_VARIABLE_PREFIXES)
        and name not in PRESERVED_VARIABLE_NAMES
    ]
    assert surviving_variable_names == []


def test_an_inherited_git_dir_would_hijack_a_temporary_repository(
    tmp_path: Path,
) -> None:
    """Without the scrub, git ignores the directory it is given.

    This is the defect the guard prevents, run on purpose so the guard's
    tests fail if the mechanism ever stops working the way it did.
    """
    bare_repository = make_bare_repository(tmp_path)
    working_directory = tmp_path / "work"
    working_directory.mkdir()
    environment_with_git_dir = {**os.environ, "GIT_DIR": str(bare_repository)}

    subprocess.run(
        ["git", "init", "--quiet", str(working_directory)],
        capture_output=True,
        check=True,
        env=environment_with_git_dir,
    )
    subprocess.run(
        ["git", "-C", str(working_directory), "config", "user.name", "Hijack Test"],
        capture_output=True,
        check=True,
        env=environment_with_git_dir,
    )

    assert not (working_directory / ".git").exists()
    assert configured_value(bare_repository, "user.name") == "Hijack Test"


def test_scrubbing_keeps_git_init_inside_the_directory_it_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the variables removed, git builds and configures the temp repository."""
    bare_repository = make_bare_repository(tmp_path)
    working_directory = tmp_path / "work"
    working_directory.mkdir()
    monkeypatch.setenv("GIT_DIR", str(bare_repository))

    removed_variable_names = remove_inherited_environment_variables()

    assert "GIT_DIR" in removed_variable_names
    subprocess.run(
        ["git", "init", "--quiet", str(working_directory)],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(working_directory), "config", "user.name", "Local Test"],
        capture_output=True,
        check=True,
    )

    assert (working_directory / ".git").is_dir()
    assert configured_value(working_directory / ".git", "user.name") == "Local Test"
    assert configured_value(bare_repository, "core.bare") == "true"
    assert configured_value(bare_repository, "user.name") == ""


def test_scrubbing_removes_the_deploy_variables_a_re_executed_deploy_exports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """deploy.sh runs this suite with its own progress exported; it must not count."""
    monkeypatch.setenv("TARMAR_DEPLOY_PULLED", "1")
    monkeypatch.setenv("TARMAR_DEPLOY_LOCKED", "1")

    removed_variable_names = remove_inherited_environment_variables()

    assert {"TARMAR_DEPLOY_PULLED", "TARMAR_DEPLOY_LOCKED"} <= set(
        removed_variable_names
    )
    assert "TARMAR_DEPLOY_PULLED" not in os.environ
    assert "TARMAR_DEPLOY_LOCKED" not in os.environ


def test_scrubbing_keeps_the_canon_directory_a_run_was_pointed_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TARMAR_CANON_DIR is configuration: a run against an outside canon keeps it."""
    monkeypatch.setenv("TARMAR_CANON_DIR", "/srv/tarmar-canon")
    monkeypatch.setenv("TARMAR_DEPLOY_PULLED", "1")

    removed_variable_names = remove_inherited_environment_variables()

    assert "TARMAR_CANON_DIR" not in removed_variable_names
    assert os.environ["TARMAR_CANON_DIR"] == "/srv/tarmar-canon"
    assert "TARMAR_DEPLOY_PULLED" in removed_variable_names


def test_only_the_canon_directory_is_exempt_from_the_scrub() -> None:
    """The exemption is one named input; widening it has to be a visible change."""
    assert PRESERVED_VARIABLE_NAMES == frozenset({"TARMAR_CANON_DIR"})
