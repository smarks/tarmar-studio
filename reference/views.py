import logging
import re

from django.shortcuts import render
from django.urls import reverse
from django.views.generic import TemplateView

from common.breadcrumbs import BreadcrumbMixin, step, trail
from common.mixins import ActivePageMixin
from reference.paths import page_markdown_file
from reference.rendering import render_reference_markdown
from reference.tables import annotate_tables

logger = logging.getLogger(__name__)


class ReferenceView(BreadcrumbMixin, ActivePageMixin, TemplateView):
    template_name = "reference/reference.html"
    active_page = "reference"

    def get_context_data(self, **kwargs):
        context = self._page_context(super().get_context_data(**kwargs))
        # The trail names the page being read (issue #339, item 28). It is set
        # here, once the page is known: BreadcrumbMixin builds its trail before
        # this view has looked the page up, so it could only ever say
        # "Reference".
        context["breadcrumb"] = self._page_breadcrumb(context)
        return context

    def _page_breadcrumb(self, context):
        """Reference alone on the overview; Reference › the page everywhere else."""
        if self.kwargs.get("path") is None:
            return trail(step("Reference"))
        return trail(
            step("Reference", reverse("reference:index")),
            step(context["current_title"]),
        )

    def _page_context(self, context):
        """The page's navigation, title and rendered content, added to ``context``."""

        # Import navigation config
        from .nav import REFERENCE_NAV, collect_dm_only_paths, process_nav_items

        # Get the path from URL
        path = self.kwargs.get("path", "overview")

        # Filter nav for DM-only sections
        user = self.request.user
        is_dm = user.is_authenticated and (user.is_dm or user.is_role_admin)

        # Process nav items once - get both filtered nav and title lookup
        nav_items, title_map = process_nav_items(REFERENCE_NAV, is_dm)
        context["nav"] = nav_items

        # Check if trying to access DM-only content without permission. Gated by
        # the dm-rules/ path convention OR an explicit dm_only nav flag (so a
        # sensitive page placed outside dm-rules/ is still protected, not just
        # hidden from the sidebar).
        is_dm_only = path.startswith("dm-rules") or path in collect_dm_only_paths(
            REFERENCE_NAV
        )
        if is_dm_only and not is_dm:
            context["content"] = (
                "<p>You do not have permission to view this content.</p>"
            )
            context["current_title"] = "Access Denied"
            context["current_path"] = path
            return context

        # Read and render markdown file. The dm-rules/ prefix maps to the canon
        # directory, which may live outside CONTENT_DIR (issue #917).
        # Path traversal protection: page_markdown_file refuses any ".."
        # segment, and the resolved file (symlinks followed) must stay inside
        # the root its prefix mapped to (the canon or the content directory).
        try:
            unresolved_file, content_root = page_markdown_file(path)
        except ValueError:
            md_file = None
        else:
            md_file = unresolved_file.resolve()
            if not md_file.is_relative_to(content_root.resolve()):
                md_file = None
        if md_file is None:
            logger.warning("Rejected reference path-traversal attempt: path=%r", path)
            context["content"] = "<p>Content not found.</p>"
            context["current_title"] = "Not Found"
            context["current_path"] = path
            return context

        # Default-deny: only serve pages explicitly published in REFERENCE_NAV
        # (title_map holds every nav path visible to this user). A markdown file
        # that exists under content/ but isn't in the nav — e.g. an unreferenced
        # draft — must not be readable by guessing its slug.
        if path not in title_map:
            context["content"] = "<p>Content not found.</p>"
            context["current_title"] = "Not Found"
            context["current_path"] = path
            return context

        if md_file.exists():
            try:
                cleaned = render_reference_markdown(md_file.read_text())
                # Tag tables with their column count and each cell with its
                # column name, so the stylesheet can restack a too-wide table
                # into labelled blocks rather than hiding columns behind a
                # scrollbar. Applied after sanitization since these attributes
                # are ours, not user content.
                context["content"] = annotate_tables(cleaned)
            except Exception:
                logger.exception("Error rendering reference content: %s", path)
                context["content"] = "<p>Error rendering content.</p>"
        else:
            context["content"] = "<p>Content not found.</p>"

        context["current_title"] = title_map.get(path, "Reference")
        context["current_path"] = path

        return context


def _extract_snippet(content, query, context_chars=100):
    """Extract a snippet around the first match with context."""
    # Remove markdown formatting for cleaner snippets
    clean = re.sub(r"[#*_`\[\]]", "", content)
    clean = re.sub(r"\n+", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()

    idx = clean.lower().find(query.lower())
    if idx == -1:
        # Query matched title but not content - show beginning
        return clean[:200] + "..." if len(clean) > 200 else clean

    start = max(0, idx - context_chars)
    end = min(len(clean), idx + len(query) + context_chars)

    snippet = clean[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(clean):
        snippet = snippet + "..."

    return snippet


def _search_content(query, is_dm):
    """Search all markdown files for query. Returns list of {path, title, snippet}.

    Reads from the in-memory index built at app startup (see
    `reference.search_index`) instead of walking the filesystem per request.
    DM-only filtering happens here so the index is shared across users.
    """
    from .nav import REFERENCE_NAV, collect_dm_only_paths
    from .search_index import get_index, get_raw_content

    results = []
    query_lower = query.lower()
    # Same DM-only gate as ReferenceView.get_context_data: the dm-rules/ path
    # convention OR an explicit dm_only nav flag, so a sensitive page placed
    # outside dm-rules/ is hidden from players in search too, not just
    # protected on direct access (issue #166).
    dm_only_paths = collect_dm_only_paths(REFERENCE_NAV)

    for path_str, (title, normalized_content) in get_index().items():
        is_dm_only = path_str.startswith("dm-rules") or path_str in dm_only_paths
        if is_dm_only and not is_dm:
            continue

        if query_lower not in title.lower() and query_lower not in normalized_content:
            continue

        raw = get_raw_content(path_str) or ""
        results.append(
            {
                "path": path_str,
                "title": title,
                "snippet": _extract_snippet(raw, query),
            }
        )

    # Path breaks ties between equal titles ("Runaway", "Magic"), so their
    # order is stated here rather than inherited from the filesystem walk.
    results.sort(key=lambda result: (result["title"].lower(), result["path"]))
    return results


def _search_breadcrumb():
    """Tarmar > Reference > Search — both render branches share it."""
    return trail(
        step("Reference", reverse("reference:index")),
        step("Search"),
    )


def search(request):
    """Search reference content."""
    from .nav import REFERENCE_NAV, process_nav_items

    query = request.GET.get("q", "").strip()

    # Filter nav for sidebar
    user = request.user
    is_dm = user.is_authenticated and (user.is_dm or user.is_role_admin)
    nav_items, _ = process_nav_items(REFERENCE_NAV, is_dm)

    if not query or len(query) < 2:
        return render(
            request,
            "reference/search.html",
            {
                "results": [],
                "query": query,
                "nav": nav_items,
                "breadcrumb": _search_breadcrumb(),
                "message": "Enter at least 2 characters to search." if query else None,
            },
        )

    results = _search_content(query, is_dm)
    return render(
        request,
        "reference/search.html",
        {
            "results": results,
            "query": query,
            "nav": nav_items,
            "breadcrumb": _search_breadcrumb(),
        },
    )
