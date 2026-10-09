"""The cert runbook and nginx's config name the same certificate lineages.

Issue #665: the runbook described a state the repo had left three weeks
earlier. Its "Why" said ``nginx/tarmar.conf`` still pointed the
``tarmar.duckdns.org`` block at the ``pillars.duckdns.org`` lineage, which
#272 had already changed, and it ended by deleting that lineage — the rollback
target its own rollback section depended on — and closing two closed issues.
An operator following it would have reissued a certificate that existed and
edited two lines that already said what the step asked for.

Prose cannot be made to stay true by reading it, so the one fact that went
stale is checked here: every ``/etc/letsencrypt/live/<lineage>/`` the runbook
presents as current is one nginx actually loads, and every lineage nginx loads
is in the runbook.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUNBOOK = REPO_ROOT / "docs" / "cert-reissue-runbook.md"
NGINX_CONF = REPO_ROOT / "nginx" / "tarmar.conf"

LINEAGE_PATTERN = re.compile(r"/etc/letsencrypt/live/([^/\s`]+)/")


def lineages_in(path: Path) -> set[str]:
    """Every certbot lineage named in ``path``.

    Args:
        path: The file to read.

    Returns:
        The lineage directory names, e.g. ``{"origamisoftware.com"}``.
    """
    return set(LINEAGE_PATTERN.findall(path.read_text(encoding="utf-8")))


def test_the_runbook_names_the_lineages_nginx_loads() -> None:
    documented = lineages_in(RUNBOOK) - {"<name>"}
    assert documented == lineages_in(NGINX_CONF)


def test_the_runbook_says_the_reissue_was_carried_out() -> None:
    """So nobody reads its history as a job still waiting to be done."""
    assert "#272" in RUNBOOK.read_text(encoding="utf-8")


def test_the_runbook_does_not_close_issues_that_are_already_closed() -> None:
    """Its last step used to run `gh issue close 30` and `gh issue close 12`."""
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "gh issue close" not in text
