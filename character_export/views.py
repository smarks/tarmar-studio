"""Views for exporting character data."""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from characters.models import Character, characters_visible_to
from common.audit import audit_logger

from .export_utils import (
    generate_character_html,
    generate_character_json,
    generate_character_markdown,
)


def _safe_filename(name: str, suffix: str) -> str:
    """Build a filesystem-safe filename from a character name."""
    safe_name = "".join(c for c in name if c.isalnum() or c in (" ", "-", "_")).strip()
    return f"{safe_name}{suffix}"


#: The only schemes a PDF export may fetch. The character sheet is
#: self-contained, so a crafted resource reference must not read local files
#: (``file://``) or reach other schemes (SSRF/LFI).
PDF_ALLOWED_PROTOCOLS = frozenset({"http", "https"})


def _safe_url_fetcher():
    """WeasyPrint's own URL fetcher, limited to http(s).

    WeasyPrint 70 takes a :class:`weasyprint.urls.URLFetcher`, not a function,
    and the fetcher refuses any other scheme itself (issue #1003). Imported
    lazily so test collection does not require pango and glib.
    """
    from weasyprint.urls import URLFetcher

    return URLFetcher(allowed_protocols=PDF_ALLOWED_PROTOCOLS)


def _visible_character_or_404(request, character_id) -> Character:
    """The character to export, under the app's read rule, or 404.

    Exports are reads, so they take the read scope (characters_visible_to:
    your own, any public one, everything for a DM/admin -- never a
    soft-deleted one), not the edit check they used before public characters
    existed (issue #273). The two were equivalent then; now a public
    character's sheet, markdown, JSON, and PDF are readable by any
    registered user, while editing it stays owner/DM-only.
    """
    return get_object_or_404(characters_visible_to(request.user), id=character_id)


def _incomplete_refusal(character: Character) -> HttpResponse | None:
    """The answer to exporting a character the rules cannot use yet (#571).

    An export is a finished sheet, and importing one back must give a
    character that can play, so a character still missing attributes or its
    Fatigue roll is refused, saying what it needs.
    """
    if character.is_complete:
        return None
    return HttpResponse(
        f"{character.name} cannot be exported yet: "
        f"{', '.join(character.missing_parts)}.",
        status=409,
        content_type="text/plain; charset=utf-8",
    )


def _log_export(request, character: Character, fmt: str) -> None:
    """Record a character export. Always logged so exports are auditable."""
    audit_logger.info(
        "CHARACTER_EXPORT: user=%s format=%s character=%s (pk=%s) owner=%s",
        request.user.username,
        fmt,
        character.name,
        character.pk,
        character.user.username,
    )


@login_required
def export_markdown(request, character_id):
    """Export a character as a markdown file."""
    character = _visible_character_or_404(request, character_id)
    refused = _incomplete_refusal(character)
    if refused is not None:
        return refused

    _log_export(request, character, "markdown")

    markdown_content = generate_character_markdown(character)
    filename = _safe_filename(character.name, ".md")

    response = HttpResponse(markdown_content, content_type="text/markdown")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def export_json(request, character_id):
    """Export a character as a JSON file (round-trippable via import)."""
    character = _visible_character_or_404(request, character_id)
    refused = _incomplete_refusal(character)
    if refused is not None:
        return refused

    _log_export(request, character, "json")

    body = generate_character_json(character)
    filename = _safe_filename(character.name, ".json")
    response = HttpResponse(body, content_type="application/json")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def export_pdf(request, character_id):
    """Export a character as a PDF file."""
    character = _visible_character_or_404(request, character_id)
    refused = _incomplete_refusal(character)
    if refused is not None:
        return refused

    _log_export(request, character, "pdf")

    html_content = generate_character_html(character)

    # Use WeasyPrint to convert HTML to PDF. Imported lazily so test collection
    # does not require pango/glib to be installed.
    from weasyprint import HTML

    pdf_bytes = HTML(string=html_content, url_fetcher=_safe_url_fetcher()).write_pdf()
    filename = _safe_filename(character.name, ".pdf")

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
