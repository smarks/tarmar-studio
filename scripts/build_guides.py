#!/usr/bin/env python
"""Generate the consolidated Tarmar guides from the live content tree.

Source of truth:
  - reference/content/*.md  — the per-page markdown served by the Django app.
  - reference/nav.py         — REFERENCE_NAV: structure, order, the dm_only
                               player/GM boundary, and `guides` membership.
  - the loaded canon (reference.paths.DM_RULES_DIR) — the DM Rules pages, and
    its author/cosmology-notes.md, the private author cosmology (not a page).

Outputs, generated and never hand-edited (issue #921 split them by audience):
  - build/tarmar_players_guide.md and build/the_world_of_tarmar.md, in this
    repository: player-facing, built from public pages only, leak-checked.
  - <canon>/guides/tarmar_gms_guide.md and
    <canon>/guides/tarmar_cosmology_notes_private.md, in the canon's own
    private repository: built only when the loaded canon is the Tarmar canon.
    Against the default canon they are skipped with a notice, since they
    would carry the Tarmar secrets this repository no longer holds.

The script never modifies anything under reference/ or the canon's pages.
It runs an esoteric/exoteric leak check on the player-facing guides and exits
non-zero if any deep-secret term leaks into them, or if a player-facing guide
would include a page from the canon. The terms come from the Tarmar canon's
author/forbidden-terms.txt, read only when that canon is loaded; this
repository names none of them.

Run from anywhere: `python scripts/build_guides.py`; `--check` verifies
instead of writing. Set TARMAR_CANON_DIR to the Tarmar canon checkout to
build or check its guides as well.
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Run from anywhere: the repo root has to go on sys.path before this file can
# import from it, so REPO_ROOT is derived here rather than read from
# reference.paths -- the same bootstrap tarmar_studio/settings.py does. Every
# path *under* reference/ comes from the shared module below.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from reference.paths import (  # noqa: E402
    CANON_AUTHOR_DIR_NAME,
    CANON_GUIDES_DIR_NAME,
    CANON_NAME,
    CANON_URL_PREFIX,
    DM_RULES_DIR,
    TARMAR_CANON_NAME,
    page_markdown_file,
)

# The player-facing guides, committed in this repository.
BUILD_DIR = REPO_ROOT / "build"
# The Game Master's guide and the private notes, committed in the canon's
# repository beside the pages they are built from.
CANON_GUIDES_DIR = DM_RULES_DIR / CANON_GUIDES_DIR_NAME
COSMOLOGY_SOURCE = DM_RULES_DIR / CANON_AUTHOR_DIR_NAME / "cosmology-notes.md"

GENERATED_HEADER_NAV = (
    "<!-- GENERATED FILE — do not edit. Edit the source under "
    "reference/content/ (+ reference/nav.py) and run "
    "scripts/build_guides.py -->"
)
GENERATED_HEADER_COSMOLOGY = (
    "<!-- GENERATED FILE — do not edit. Edit the canon's "
    "author/cosmology-notes.md and run tarmar-studio's "
    "scripts/build_guides.py -->"
)

# The deep-secret terms the player-facing guides are checked against. The
# list is itself a summary of the secrets, so it lives in the Tarmar canon
# (issue #921), not here; the format is described at the top of that file.
FORBIDDEN_TERMS_FILE = DM_RULES_DIR / CANON_AUTHOR_DIR_NAME / "forbidden-terms.txt"
ALLOW_PREFIX = "allow:"
MATCH_CASE_PREFIX = "match-case:"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LeakRules:
    """What the leak check looks for, read from the canon's term list.

    Attributes:
        forbidden: (term, pattern) pairs; any match is a leak.
        allowed: Phrases inside which a forbidden term is not a leak.
    """

    forbidden: tuple[tuple[str, re.Pattern[str]], ...] = ()
    allowed: tuple[re.Pattern[str], ...] = ()


def _phrase_pattern(phrase: str, flags: int) -> re.Pattern[str]:
    """A pattern for ``phrase`` as a whole word or phrase."""
    return re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)", flags)


def parse_forbidden_terms(text: str) -> LeakRules:
    """Parse a forbidden-terms list (one entry per line, ``#`` comments).

    Args:
        text: The file's contents.

    Returns:
        The rules: a plain line is a term matched ignoring case,
        ``match-case: term`` one matched as written, and ``allow: phrase`` a
        phrase whose occurrences of a term do not count.
    """
    forbidden: list[tuple[str, re.Pattern[str]]] = []
    allowed: list[re.Pattern[str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(ALLOW_PREFIX):
            phrase = line[len(ALLOW_PREFIX) :].strip()
            allowed.append(_phrase_pattern(phrase, re.IGNORECASE))
        elif line.startswith(MATCH_CASE_PREFIX):
            term = line[len(MATCH_CASE_PREFIX) :].strip()
            forbidden.append((term, _phrase_pattern(term, 0)))
        else:
            forbidden.append((line, _phrase_pattern(line, re.IGNORECASE)))
    return LeakRules(forbidden=tuple(forbidden), allowed=tuple(allowed))


def load_forbidden_terms(path: Path = FORBIDDEN_TERMS_FILE) -> LeakRules:
    """The canon's leak rules, or none when it has no term list.

    Args:
        path: The canon's ``author/forbidden-terms.txt``.

    Returns:
        The parsed rules; empty (and logged at info) when the file is absent.
    """
    if not path.is_file():
        logger.info("No forbidden-terms list at %s: the leak check has no terms", path)
        return LeakRules()
    return parse_forbidden_terms(path.read_text(encoding="utf-8"))


IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
SITE_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(/[^)]*\)")
DRAFT_NOTE_PATTERN = re.compile(r"^\*Draft\b.*\*\s*$")
HEADING_PATTERN = re.compile(r"^(#{1,6})(\s)")


@dataclass
class GuideSpec:
    """Configuration for one generated guide."""

    key: str
    title: str
    blurb: str
    output_name: str
    leak_check: bool
    # Built from the canon's secrets: written into the canon's guides/ and
    # only when the Tarmar canon is loaded. A player-facing guide is not.
    private: bool = False


@dataclass
class Page:
    """A content page collected from the nav tree."""

    path: str  # slug path, e.g. "public-rules/combat/turn-sequence"
    title: str
    guides: list[str]
    dm_only: bool


@dataclass
class BuildResult:
    """Outcome of generating one guide, for the summary report."""

    spec: GuideSpec
    page_titles: list[str] = field(default_factory=list)
    word_count: int = 0
    leaks: list[str] = field(default_factory=list)


NAV_GUIDES: list[GuideSpec] = [
    GuideSpec(
        key="players_guide",
        title="Tarmar — Player's Guide",
        blurb=(
            "The complete player-facing guide to Tarmar — everything a player may "
            "read. Nothing here reveals the world's deep secrets; those live in the "
            "Game Master's materials."
        ),
        output_name="tarmar_players_guide.md",
        leak_check=True,
    ),
    GuideSpec(
        key="the_world_of_tarmar",
        title="The World of Tarmar",
        blurb=(
            "A guide to the world of Tarmar — its lands first, then the deeper "
            "account of its history and powers as the educated understand them."
        ),
        output_name="the_world_of_tarmar.md",
        leak_check=True,
    ),
    GuideSpec(
        key="gms_guide",
        title="Tarmar — Game Master's Guide",
        blurb=(
            "The complete Game Master's guide to Tarmar — for the GM only. Contains "
            "the hidden mechanics, regional detail, generators, scenario seeds, and "
            "reference material that players should not read. For the deep cosmology "
            "and design reasoning, see the separate Author's Bible and Canon files."
        ),
        output_name="tarmar_gms_guide.md",
        leak_check=False,
        private=True,
    ),
]


def collect_pages(
    nav: list[dict],
    prefix: str = "",
    inherited: list[str] | None = None,
    accumulator: list[Page] | None = None,
) -> list[Page]:
    """Walk REFERENCE_NAV depth-first, resolving paths and guide inheritance.

    A node's `guides` overrides; otherwise it inherits the nearest ancestor's.
    Pre-order traversal preserves nav order for sorting within each guide.
    """
    if accumulator is None:
        accumulator = []
    for item in nav:
        slug = item["slug"]
        path = f"{prefix}/{slug}" if prefix else slug
        guides = item.get("guides", inherited if inherited is not None else [])
        accumulator.append(
            Page(
                path=path,
                title=item["title"],
                guides=list(guides),
                dm_only=bool(item.get("dm_only", False)),
            )
        )
        children = item.get("children")
        if children:
            collect_pages(children, path, guides, accumulator)
    return accumulator


def demote_headings(markdown: str) -> str:
    """Add one level to every ATX heading so a page nests under the guide.

    Headings inside fenced code blocks are left untouched; level is capped at 6.
    """
    lines = markdown.splitlines()
    out: list[str] = []
    in_fence = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            out.append(line)
            continue
        if not in_fence:
            match = HEADING_PATTERN.match(line)
            if match:
                hashes = match.group(1)
                if len(hashes) < 6:
                    line = "#" + line
        out.append(line)
    return "\n".join(out)


def clean_links(markdown: str) -> str:
    """Strip site-relative links and image refs for a standalone document.

    `![alt](/path)` -> `*(image: alt)*`; `[text](/path)` -> `text`. External
    (http/https/mailto) links and `*Draft — …*` notes are handled too.
    """
    text = IMAGE_PATTERN.sub(
        lambda m: f"*(image: {m.group(1)})*" if m.group(1) else "*(image)*",
        markdown,
    )
    text = SITE_LINK_PATTERN.sub(r"\1", text)
    kept = [
        line for line in text.splitlines() if not DRAFT_NOTE_PATTERN.match(line.strip())
    ]
    return "\n".join(kept)


def find_leaks(markdown: str, rules: LeakRules) -> list[str]:
    """Return the forbidden terms present in player-facing text.

    Occurrences inside an allowed phrase are removed before the search, so a
    term that only ever appears inside one is not reported.
    """
    text = markdown
    for allowed in rules.allowed:
        text = allowed.sub(" ", text)
    return [term for term, pattern in rules.forbidden if pattern.search(text)]


def anchor_for(path: str) -> str:
    """Stable, unique anchor id derived from a page's slug path."""
    return "page-" + path.replace("/", "-")


def render_page_section(page: Page, body: str) -> str:
    """Demote, clean, and anchor a single page for inclusion in a guide."""
    rendered = clean_links(demote_headings(body)).strip()
    return f'<a id="{anchor_for(page.path)}"></a>\n\n{rendered}\n'


def build_nav_guide(
    spec: GuideSpec, pages: list[Page], leak_rules: LeakRules | None = None
) -> tuple[str, BuildResult]:
    """Assemble one nav-driven guide document and its build result."""
    selected = [p for p in pages if spec.key in p.guides]
    if not spec.private:
        canon_pages = [
            page.path
            for page in selected
            if page.path.split("/", 1)[0] == CANON_URL_PREFIX and "/" in page.path
        ]
        if canon_pages:
            raise ValueError(
                f"{spec.output_name} is player-facing and committed in this "
                f"repository, but the nav puts canon pages in it: {canon_pages}"
            )
    result = BuildResult(spec=spec)
    sections: list[str] = []
    toc: list[str] = []
    for page in selected:
        source, _content_root = page_markdown_file(page.path)
        if not source.exists():
            raise FileNotFoundError(
                f"nav references {page.path!r} but {source} is missing"
            )
        body = source.read_text(encoding="utf-8")
        sections.append(render_page_section(page, body))
        toc.append(f"- [{page.title}](#{anchor_for(page.path)})")
        result.page_titles.append(page.title)

    document = (
        "\n".join(
            [
                GENERATED_HEADER_NAV,
                "",
                f"# {spec.title}",
                "",
                f"*{spec.blurb}*",
                "",
                "## Table of Contents",
                "",
                *toc,
                "",
                "---",
                "",
                "\n---\n\n".join(sections),
            ]
        ).rstrip()
        + "\n"
    )

    result.word_count = len(document.split())
    if spec.leak_check:
        result.leaks = find_leaks(document, leak_rules or LeakRules())
    return document, result


def build_cosmology_guide() -> tuple[str, BuildResult]:
    """Generate the private cosmology guide from the canon's author notes."""
    spec = GuideSpec(
        key="cosmology",
        title="Notes on Tarmar Cosmology",
        blurb="",
        output_name="tarmar_cosmology_notes_private.md",
        leak_check=False,
        private=True,
    )
    if not COSMOLOGY_SOURCE.exists():
        raise FileNotFoundError(f"missing cosmology source: {COSMOLOGY_SOURCE}")
    body = clean_links(COSMOLOGY_SOURCE.read_text(encoding="utf-8")).strip()
    document = f"{GENERATED_HEADER_COSMOLOGY}\n\n{body}\n"
    result = BuildResult(spec=spec)
    result.page_titles = ["(author/cosmology-notes.md)"]
    result.word_count = len(document.split())
    return document, result


def builds_private_guides(canon_name: str = CANON_NAME) -> bool:
    """Whether the loaded canon is one the private guides are built from."""
    return canon_name == TARMAR_CANON_NAME


def output_path_for(spec: GuideSpec) -> Path:
    """Where a guide is written: the canon's guides/ if private, else build/."""
    return (CANON_GUIDES_DIR if spec.private else BUILD_DIR) / spec.output_name


def generate_all(
    include_private: bool | None = None, leak_rules: LeakRules | None = None
) -> list[tuple[Path, str, BuildResult]]:
    """Render every guide in memory. Returns (output_path, content, result).

    Args:
        include_private: Whether to render the Game Master's guide and the
            cosmology notes too (default: when the Tarmar canon is loaded).
        leak_rules: The player-facing guides' leak check (default: the
            canon's term list when the Tarmar canon is loaded, else none).
    """
    from reference.nav import REFERENCE_NAV

    if include_private is None:
        include_private = builds_private_guides()
    if leak_rules is None:
        leak_rules = load_forbidden_terms() if include_private else LeakRules()
    pages = collect_pages(REFERENCE_NAV)
    rendered: list[tuple[Path, str, BuildResult]] = []
    for spec in NAV_GUIDES:
        if spec.private and not include_private:
            continue
        document, result = build_nav_guide(spec, pages, leak_rules)
        rendered.append((output_path_for(spec), document, result))
    if include_private:
        cosmology_doc, cosmology_result = build_cosmology_guide()
        rendered.append(
            (output_path_for(cosmology_result.spec), cosmology_doc, cosmology_result)
        )
    return rendered


def main(check_only: bool = False) -> int:
    """Generate the guides (or verify them with check_only=True).

    Returns non-zero if any leak check fails, or — in check mode — if a
    committed guide (build/, and the canon's guides/ under the Tarmar canon)
    is out of date relative to the source.
    """
    include_private = builds_private_guides()
    leak_rules = load_forbidden_terms() if include_private else LeakRules()
    rendered = generate_all(include_private, leak_rules)
    if not check_only:
        for output_path, _content, _result in rendered:
            output_path.parent.mkdir(exist_ok=True)

    print("Tarmar guide build" + (" (check)" if check_only else ""))
    print("=" * 60)
    if not include_private:
        print(
            f"Canon loaded: {CANON_NAME} ({DM_RULES_DIR}). The Game Master's "
            "guide and the cosmology notes are built only against the Tarmar "
            "canon, into its guides/: skipped. Set TARMAR_CANON_DIR to the "
            "Tarmar canon checkout to build them."
        )
    failed = False
    stale: list[str] = []
    for output_path, content, result in rendered:
        if check_only:
            existing = (
                output_path.read_text(encoding="utf-8")
                if output_path.exists()
                else None
            )
            if existing != content:
                stale.append(str(output_path))
        else:
            output_path.write_text(content, encoding="utf-8")

        print(f"\n{output_path}  ({result.word_count} words)")
        for title in result.page_titles:
            print(f"    • {title}")
        if result.spec.leak_check:
            if result.leaks:
                failed = True
                print(f"    LEAK CHECK: FAIL — forbidden terms: {result.leaks}")
            elif leak_rules.forbidden:
                print(f"    LEAK CHECK: PASS ({len(leak_rules.forbidden)} terms)")
            else:
                print("    LEAK CHECK: no forbidden-terms list loaded")

    print("\n" + "=" * 60)
    if failed:
        print("BUILD FAILED: deep secrets leaked into player-facing output.")
        return 1
    if check_only and stale:
        print(
            "BUILD OUT OF DATE: "
            + ", ".join(stale)
            + "\nRun: python scripts/build_guides.py"
        )
        return 1
    print("CHECK OK" if check_only else "BUILD OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(check_only="--check" in sys.argv[1:]))
