"""Context processors for common template variables."""

import subprocess
from functools import lru_cache

from common.helpers import request_theme

# The project's GitHub repository; the footer links the running commit under it.
REPO_URL = "https://github.com/smarks/tarmar-studio"
# Where the home page sends players to leave a comment. A separate public
# repository's issues, because the code repository is private and its issues
# page is a 404 to anyone who is not a collaborator.
FEEDBACK_URL = "https://github.com/smarks/tarmar-feedback/issues"


@lru_cache(maxsize=1)
def _get_git_info() -> dict[str, str]:
    """Get git commit info, cached for the lifetime of the process."""
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        date = subprocess.check_output(
            ["git", "log", "-1", "--format=%ci"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        # Format: "2025-01-17 12:34:56 -0800" -> "2025-01-17"
        if date:
            date = date.split()[0]
        return {"sha": sha, "date": date}
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {"sha": "", "date": ""}


def git_info(request) -> dict[str, str]:
    """Add git commit info to template context."""
    info = _get_git_info()
    return {
        "git_sha": info["sha"],
        "git_sha_short": info["sha"][:7] if info["sha"] else "",
        "git_date": info["date"],
    }


def project_links(request) -> dict[str, str]:
    """Add the project's GitHub addresses to template context."""
    return {"repo_url": REPO_URL, "feedback_url": FEEDBACK_URL}


def theme(request) -> dict[str, object]:
    """Add the presentation theme the request's hostname selects.

    A context processor rather than a mixin or a middleware attribute: every
    page inherits base.html, so every page needs this, and a processor is the
    one place that is true without each view remembering to say so.
    """
    return {"theme": request_theme(request)}


def canon(request) -> dict[str, str]:
    """Add the loaded canon's label (issue #918) to template context.

    A processor rather than view context, so every template that includes
    ``_canon_line.html`` names the canon instead of printing a bare "World:".
    """
    from reference.paths import CANON_LABEL

    return {"canon_label": CANON_LABEL}
