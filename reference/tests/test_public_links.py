"""No public page links a commit, PR or issue in either repository (issue #956).

After the 2026-10-09 rename, ``smarks/tarmar-studio`` is a public snapshot
without the project's history, and ``smarks/tarmar-studio-private`` carries
that history where only its collaborators can open it. A link to a commit,
pull request or issue under either name is therefore broken or closed to a
player wherever a player reads it. Public pages name the issue or PR number
in plain text instead.

"Public" here is every file this repository serves or publishes to a reader:
the reference pages and the default canon, the player-facing guides in
``build/``, the README and the site's templates. Operator documents
(``docs/``, ``DEPLOY.md``) may link the private repository, and so may the
Tarmar canon, which lives in its own private repository and is not swept.
A link to either repository's root, or to a file in the public one, is fine.
"""

import re
from pathlib import Path

from reference.paths import REPO_ROOT

HISTORY_LINK = re.compile(
    r"github\.com/smarks/tarmar-studio(?:-private)?/"
    r"(?:commit|commits|pull|pulls|issues|compare)\b"
)

PUBLIC_GLOBS = (
    "reference/content/**/*.md",
    "reference/default_canon/**/*.md",
    "build/*.md",
    "README.md",
    "templates/**/*.html",
    "*/templates/**/*.html",
)


def public_files() -> list[Path]:
    """Every file in this repository that a player can read."""
    found = {path for pattern in PUBLIC_GLOBS for path in REPO_ROOT.glob(pattern)}
    return sorted(found)


def history_links(path: Path) -> list[str]:
    """``file:line`` for each line of ``path`` that links repository history."""
    text = path.read_text(encoding="utf-8")
    relative = path.relative_to(REPO_ROOT)
    return [
        f"{relative}:{number}"
        for number, line in enumerate(text.splitlines(), start=1)
        if HISTORY_LINK.search(line)
    ]


def test_the_pattern_catches_history_links_under_both_names():
    for link in (
        "https://github.com/smarks/tarmar-studio/commit/2be950e",
        "https://github.com/smarks/tarmar-studio-private/commit/2be950e",
        "https://github.com/smarks/tarmar-studio-private/pull/272",
        "https://github.com/smarks/tarmar-studio/issues/914",
        "https://github.com/smarks/tarmar-studio/compare/a...b",
    ):
        assert HISTORY_LINK.search(link), link


def test_the_pattern_allows_the_repository_root_and_public_files():
    for link in (
        "https://github.com/smarks/tarmar-studio",
        "https://github.com/smarks/tarmar-studio-private",
        "https://github.com/smarks/tarmar-studio/blob/main/README.md",
    ):
        assert not HISTORY_LINK.search(link), link


def test_the_sweep_covers_pages_guides_readme_and_templates():
    relatives = {path.relative_to(REPO_ROOT).as_posix() for path in public_files()}
    assert "reference/content/introduction.md" in relatives
    assert "build/tarmar_players_guide.md" in relatives
    assert "build/the_world_of_tarmar.md" in relatives
    assert "README.md" in relatives
    assert any(name.startswith("templates/") for name in relatives)
    assert not any(name.startswith("docs/") for name in relatives)


def test_no_public_file_links_repository_history():
    offenders = [hit for path in public_files() for hit in history_links(path)]
    assert offenders == [], (
        f"Public pages must name an issue or PR in plain text, not link it: {offenders}"
    )
