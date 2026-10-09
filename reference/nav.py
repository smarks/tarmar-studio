# Reference navigation structure
# Edit this file to add, remove or reorder the public sections. The DM Rules
# section's pages come from the loaded canon's nav.json (see below).
# Each item is a dict with:
#   - title: Display name
#   - slug: URL slug and filename (without .md)
#   - children: (optional) list of child items
#   - dm_only: (optional) True if only visible to DMs
#   - guides: (optional) list of generated guides this page belongs to, any of
#     "players_guide", "the_world_of_tarmar", "gms_guide". Children inherit a
#     parent's guides unless they declare their own. Consumed by
#     scripts/build_guides.py; does not affect the live site.


import json
from pathlib import Path

from reference.paths import CANON_NAV_FILE_NAME, DM_RULES_DIR


def _build_nav_path(prefix: str, slug: str) -> str:
    """Build a navigation path from prefix and slug."""
    return f"{prefix}/{slug}" if prefix else slug


def process_nav_items(
    items: list[dict], is_dm: bool, prefix: str = ""
) -> tuple[list[dict], dict[str, str]]:
    """Filter nav items by DM permission and attach computed paths.

    Returns (filtered_items, path_to_title_map). This is the single source of
    truth for "which content/*.md paths does the nav publish" — the reference
    view, the search index, and anything else gating on nav membership should
    call this (or `all_nav_paths` below) rather than re-deriving it.
    """
    result: list[dict] = []
    titles: dict[str, str] = {}
    for item in items:
        if item.get("dm_only") and not is_dm:
            continue
        item_path = _build_nav_path(prefix, item["slug"])
        filtered_item = item.copy()
        filtered_item["path"] = item_path
        titles[item_path] = item["title"]
        if "children" in filtered_item:
            child_items, child_titles = process_nav_items(
                filtered_item["children"], is_dm, item_path
            )
            filtered_item["children"] = child_items
            titles.update(child_titles)
        result.append(filtered_item)
    return result, titles


def collect_dm_only_paths(
    items: list[dict], prefix: str = "", parent_dm_only: bool = False
) -> set[str]:
    """Full slug-paths that are DM-only (flagged on the item or an ancestor)."""
    paths: set[str] = set()
    for item in items:
        item_path = _build_nav_path(prefix, item["slug"])
        dm_only = parent_dm_only or bool(item.get("dm_only"))
        if dm_only:
            paths.add(item_path)
        if "children" in item:
            paths |= collect_dm_only_paths(item["children"], item_path, dm_only)
    return paths


def all_nav_paths(items: list[dict]) -> set[str]:
    """Every path published in the nav, DM-only or not.

    This is the maximal set the view will ever serve to *any* user (a DM sees
    exactly this; a player sees this minus `collect_dm_only_paths`). Callers
    that build shared, cross-user state — like the search index, which is
    built once at startup and reused for every request — should allowlist
    against this set and keep any DM/player split at query time.
    """
    _, title_map = process_nav_items(items, is_dm=True)
    return set(title_map)


# The DM Rules section's pages are the canon's own (issue #918): the canon
# directory carries them as ``nav.json``, a list of nav items in the format
# above, so the Tarmar canon and the default canon each publish their own tree
# under the unchanged ``dm-rules/`` prefix. A canon with no ``nav.json``
# publishes the landing page alone.
CANON_NAV_FILE = DM_RULES_DIR / CANON_NAV_FILE_NAME


def _validate_nav_items(items: object, nav_file: Path, where: str) -> list[dict]:
    """Check nav items have the form above; ValueError names file and item."""
    if not isinstance(items, list):
        raise ValueError(f"{nav_file}: {where or 'the file'} must be a JSON list")
    for index, item in enumerate(items):
        place = f"{where}[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{nav_file}: {place} must be an object")
        for field in ("title", "slug"):
            if not isinstance(item.get(field), str) or not item[field]:
                raise ValueError(f"{nav_file}: {place}.{field} must be a string")
        if not isinstance(item.get("dm_only", False), bool):
            raise ValueError(f"{nav_file}: {place}.dm_only must be true or false")
        guides = item.get("guides", [])
        if not isinstance(guides, list) or not all(
            isinstance(guide, str) for guide in guides
        ):
            raise ValueError(f"{nav_file}: {place}.guides must be a list of strings")
        if "children" in item:
            _validate_nav_items(item["children"], nav_file, f"{place}.children")
    return items


def _load_canon_nav(nav_file: Path = CANON_NAV_FILE) -> list[dict]:
    """The DM Rules children the canon publishes, from its ``nav.json``.

    The schema is documented in :mod:`reference.paths`.

    Args:
        nav_file: The canon's nav file.

    Returns:
        The nav items, or an empty list when the canon has no nav file.

    Raises:
        ValueError: The file is not valid JSON or an item is malformed; the
            message names the file and the item.
    """
    if not nav_file.is_file():
        return []
    try:
        items = json.loads(nav_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{nav_file} is not valid JSON: {error}") from error
    return _validate_nav_items(items, nav_file, "")


REFERENCE_NAV = [
    {"title": "Get Started", "slug": "get-started", "guides": ["players_guide"]},
    {
        "title": "Your First Session",
        "slug": "first-session",
        "guides": ["players_guide"],
    },
    {"title": "Overview", "slug": "overview", "guides": ["players_guide"]},
    {"title": "Introduction", "slug": "introduction", "guides": ["players_guide"]},
    {
        "title": "The World of Tarmar",
        "slug": "the-world-of-tarmar",
        "guides": ["players_guide", "the_world_of_tarmar"],
        "children": [
            # Children belong to the Player's Guide only; the world page's
            # Part Two already covers these topics for the world guide.
            {
                "title": "Inhabitants",
                "slug": "inhabitants",
                "guides": ["players_guide"],
            },
            {
                "title": "The God King Emperor",
                "slug": "god-king-emperor",
                "guides": ["players_guide"],
            },
            {"title": "The Dragons", "slug": "dragons", "guides": ["players_guide"]},
            {
                "title": "The Wayfarers",
                "slug": "wayfarers",
                "guides": ["players_guide"],
            },
            {"title": "The Pillars", "slug": "pillars", "guides": ["players_guide"]},
            {
                "title": "Ancient Ruins",
                "slug": "ancient-ruins",
                "guides": ["players_guide"],
            },
            {"title": "Magic", "slug": "magic", "guides": ["players_guide"]},
        ],
    },
    {
        "title": "Public Rules",
        "slug": "public-rules",
        "guides": ["players_guide"],
        "children": [
            {
                "title": "Characters",
                "slug": "characters",
                "children": [
                    {"title": "Attributes", "slug": "attributes"},
                    {"title": "Derived Pools", "slug": "derived-pools"},
                    {
                        "title": "Injury Thresholds & Death",
                        "slug": "injury-thresholds-death",
                    },
                    {"title": "Provenance", "slug": "provenance"},
                    {"title": "Location", "slug": "location"},
                    {"title": "Wealth", "slug": "wealth"},
                    {
                        "title": "Prior Experience",
                        "slug": "prior-experience",
                        "children": [
                            {"title": "Aging", "slug": "aging"},
                            {"title": "Survivability", "slug": "survivability"},
                            {"title": "Skill Tracks", "slug": "skill-tracks"},
                            {"title": "Skills Glossary", "slug": "skills-glossary"},
                        ],
                    },
                ],
            },
            {
                "title": "Magic",
                "slug": "magic",
                "children": [
                    {"title": "Casting Spells", "slug": "casting-spells"},
                    {"title": "Spell Mastery", "slug": "spell-mastery"},
                    {"title": "Push (Boosting)", "slug": "push-boosting"},
                    {"title": "Runaway", "slug": "runaway"},
                    {"title": "Counterspell", "slug": "counterspell"},
                    {"title": "Schools of Magic", "slug": "schools-of-magic"},
                    {
                        "title": "Spell Descriptions",
                        "slug": "spell-descriptions",
                    },
                ],
            },
            {
                "title": "Combat",
                "slug": "combat",
                "children": [
                    {"title": "Turn Sequence", "slug": "turn-sequence"},
                    {
                        "title": "Action Options",
                        "slug": "action-options",
                        "children": [
                            {"title": "Movement", "slug": "movement"},
                            {"title": "Attack Rolls", "slug": "attack-rolls"},
                            {"title": "DEX Adjustments", "slug": "dex-adjustments"},
                            {
                                "title": "Special Combat Situations",
                                "slug": "special-combat-situations",
                            },
                            {
                                "title": "Hand-to-Hand & Grappling",
                                "slug": "hand-to-hand-and-grappling",
                            },
                        ],
                    },
                ],
            },
            {
                "title": "Equipment",
                "slug": "equipment",
                "children": [
                    {"title": "Weapons", "slug": "weapons"},
                    {"title": "Armor and Shields", "slug": "armor-and-shields"},
                    {"title": "Gear", "slug": "gear"},
                ],
            },
        ],
    },
    {
        "title": "DM Rules",
        "slug": "dm-rules",
        "dm_only": True,
        "guides": ["gms_guide"],
        "children": _load_canon_nav(),
    },
]
