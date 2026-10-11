"""Context processors for common template variables."""

import subprocess
from functools import lru_cache

from django.conf import settings

from common.helpers import request_theme


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
    """Add the project's GitHub addresses to template context.

    ``repository_url`` is the public repository's root, shown to everyone.
    ``version_commit_url`` is the running commit in that same repository
    (issues #914, #997), and is set only for a Game Master or admin; everyone
    else sees the id as text. It is empty when the checkout has no commit to
    name.

    Args:
        request: The current request; its user decides ``version_commit_url``.

    Returns:
        The template variables ``repository_url``, ``version_commit_url`` and
        ``feedback_url``.
    """
    commit_sha = _get_git_info()["sha"]
    # ``getattr`` on the request too: the passphrase gate renders this footer
    # before the authentication middleware has set ``request.user``.
    request_user = getattr(request, "user", None)
    may_open_commit_page = bool(getattr(request_user, "can_manage_users", False))
    version_commit_url = ""
    if commit_sha and may_open_commit_page:
        version_commit_url = f"{settings.REPOSITORY_PUBLIC_URL}/commit/{commit_sha}"
    return {
        "repository_url": settings.REPOSITORY_PUBLIC_URL,
        "version_commit_url": version_commit_url,
        # "Leave a comment" opens the repository's issue forms (bug, feature
        # request, general comment).
        "feedback_url": f"{settings.REPOSITORY_PUBLIC_URL}/issues/new/choose",
    }


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
