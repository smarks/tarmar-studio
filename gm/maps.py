"""Game maps and the narration-to-map detector for the GM panel (issue #343).

Which maps exist is canon, not code (issue #918). The canon directory
(``reference.paths.DM_RULES_DIR``, which may live outside this repository,
issue #917) carries a ``maps.json`` manifest naming its regional chart, the
gazetteer directory behind it, any charts of parts of the region (the Tarmar
canon's three islands), an optional world map with the labels drawn on it,
and the place names that are also everyday words. ``MAP_AREAS`` lists the
maps from most to least specific, each with the place names its chart shows,
and ``detect_place`` scans a GM reply for those names so the panel can switch
to the map of wherever the party is. A canon with no manifest has no maps.

The regional place names are not typed anywhere. They are read at import
time from the gazetteer the manifest names: every page's first heading is a
place name on the regional chart, and the hub page beside the directory
(``<gazetteer>.md``) says, by its ``##`` sections, which part-of-region chart
each place is on. A new gazetteer page therefore reaches the detector without
a code change, and ``test_maps.py`` fails if the hub and the pages disagree.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import IntEnum
from functools import cache
from pathlib import Path
from typing import Any

from django.templatetags.static import static

from reference.paths import CANON_MAPS_FILE_NAME, DM_RULES_DIR

# The canon's map manifest, beside its dm-rules pages.
MAPS_MANIFEST_NAME = CANON_MAPS_FILE_NAME
MAPS_MANIFEST: Path = DM_RULES_DIR / MAPS_MANIFEST_NAME

_HEADING_PATTERN = re.compile(r"^#\s+(?P<title>.+?)\s*$", re.MULTILINE)
_SECTION_PATTERN = re.compile(r"^##\s+(?P<section>.+?)\s*$", re.MULTILINE)


class MapScope(IntEnum):
    """How much ground a map covers; a higher value is a more specific map.

    ``ISLAND`` is a chart of one part of the region: an island in the Tarmar
    canon, whatever the manifest's ``charts`` name in another.
    """

    WORLD = 1
    REGION = 2
    ISLAND = 3


@dataclass(frozen=True)
class MapArea:
    """One map image and the place names its chart shows."""

    slug: str
    title: str
    image: str
    place_names: tuple[str, ...]
    scope: MapScope

    @property
    def image_url(self) -> str:
        """The URL the page loads the map image from."""
        return static(f"images/{self.image}")


@dataclass(frozen=True)
class DetectedPlace:
    """A map chosen from narration, and the place name that chose it."""

    area: MapArea
    place: str

    def frame_payload(self) -> dict[str, str]:
        """The ``map`` entry of the stream's ``done`` frame."""
        return {
            "slug": self.area.slug,
            "title": self.area.title,
            "image_url": self.area.image_url,
            "place": self.place,
        }


@dataclass(frozen=True)
class ChartSpec:
    """A chart of part of the region, as the manifest declares it.

    Attributes:
        section: The hub's ``##`` heading whose links are this chart's places.
        slug: The chart's slug, unique among the canon's maps.
        image: The image path under ``static/images/``.
    """

    section: str
    slug: str
    image: str


@dataclass(frozen=True)
class WorldSpec:
    """The canon's world map, which has no gazetteer of its own.

    Attributes:
        slug: The map's slug.
        title: The title the picker shows.
        image: The image path under ``static/images/``.
        place_names: The labels drawn on the image.
    """

    slug: str
    title: str
    image: str
    place_names: tuple[str, ...]


@dataclass(frozen=True)
class MapsManifest:
    """What a canon's ``maps.json`` says about its maps.

    Attributes:
        region_slug: The regional chart's slug.
        region_title: The title the picker shows for it.
        region_image: The image path under ``static/images/``.
        gazetteer: The directory of place pages, one per place on the chart.
        charts: Charts of parts of the region, most specific maps first.
        world: The world map, or None when the canon has none.
        case_sensitive_place_names: Place names that are also everyday
            words, matched only as written.
    """

    region_slug: str
    region_title: str
    region_image: str
    gazetteer: Path
    charts: tuple[ChartSpec, ...]
    world: WorldSpec | None
    case_sensitive_place_names: frozenset[str]

    @property
    def hub(self) -> Path:
        """The hub page beside the gazetteer directory."""
        return self.gazetteer.with_suffix(".md")


def _field(container: dict, name: str, kind: type, path: Path, where: str) -> Any:
    """``container[name]``, which must be a ``kind``; ValueError names the field."""
    value = container.get(name)
    if not isinstance(value, kind):
        raise ValueError(
            f"{path}: {where}{name} must be a {kind.__name__}, got {value!r}"
        )
    return value


def _string_list(container: dict, name: str, path: Path, where: str) -> tuple[str, ...]:
    """``container[name]`` (default empty), which must be a list of strings."""
    value = container.get(name, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{path}: {where}{name} must be a list of strings")
    return tuple(value)


def load_maps_manifest(path: Path = MAPS_MANIFEST) -> MapsManifest | None:
    """Read a canon's map manifest, or None when the canon has no maps.

    The schema is documented in :mod:`reference.paths`.

    Args:
        path: The ``maps.json`` file; the gazetteer it names is relative to
            the directory holding it.

    Returns:
        The manifest, or None when ``path`` does not exist.

    Raises:
        ValueError: The file is not valid JSON, a field is missing or of the
            wrong type, or two maps share a slug; the message names the file
            and the field or slug.
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} is not valid JSON: {error}") from error
    if not isinstance(data, dict):
        raise ValueError(f"{path} must hold a JSON object")
    region = _field(data, "region", dict, path, "")
    charts = region.get("charts", [])
    if not isinstance(charts, list) or not all(
        isinstance(chart, dict) for chart in charts
    ):
        raise ValueError(f"{path}: region.charts must be a list of objects")
    world = data.get("world")
    if world is not None and not isinstance(world, dict):
        raise ValueError(f"{path}: world must be an object")
    manifest = MapsManifest(
        region_slug=_field(region, "slug", str, path, "region."),
        region_title=_field(region, "title", str, path, "region."),
        region_image=_field(region, "image", str, path, "region."),
        gazetteer=path.parent / _field(region, "gazetteer", str, path, "region."),
        charts=tuple(
            ChartSpec(
                section=_field(chart, "section", str, path, f"region.charts[{index}]."),
                slug=_field(chart, "slug", str, path, f"region.charts[{index}]."),
                image=_field(chart, "image", str, path, f"region.charts[{index}]."),
            )
            for index, chart in enumerate(charts)
        ),
        world=None
        if world is None
        else WorldSpec(
            slug=_field(world, "slug", str, path, "world."),
            title=_field(world, "title", str, path, "world."),
            image=_field(world, "image", str, path, "world."),
            place_names=_string_list(world, "place_names", path, "world."),
        ),
        case_sensitive_place_names=frozenset(
            _string_list(data, "case_sensitive_place_names", path, "")
        ),
    )
    slugs = [chart.slug for chart in manifest.charts] + [manifest.region_slug]
    if manifest.world is not None:
        slugs.append(manifest.world.slug)
    duplicates = sorted({slug for slug in slugs if slugs.count(slug) > 1})
    if duplicates:
        raise ValueError(f"{path}: map slugs must be unique, repeated: {duplicates}")
    return manifest


MANIFEST: MapsManifest | None = load_maps_manifest()

# The manifest's values under the names the panel and the tests read. With no
# manifest there is no gazetteer and every collection is empty.
GAZETTEER_DIR: Path | None = None if MANIFEST is None else MANIFEST.gazetteer
GAZETTEER_HUB: Path | None = None if MANIFEST is None else MANIFEST.hub

# Hub sections that name a part-of-region chart, keyed to that chart's slug.
# The hub's other sections hold places that appear on the regional chart but
# on no single part's chart.
HUB_ISLAND_SECTIONS: dict[str, str] = (
    {} if MANIFEST is None else {chart.section: chart.slug for chart in MANIFEST.charts}
)

# The labels drawn on the world map, when the canon has one.
WORLD_PLACE_NAMES: tuple[str, ...] = (
    () if MANIFEST is None or MANIFEST.world is None else MANIFEST.world.place_names
)

# Place names that are also everyday English words. These match only when
# written as the map writes them, so an ordinary use of the word in a reply
# does not drag the panel to a chart.
CASE_SENSITIVE_PLACE_NAMES: frozenset[str] = (
    frozenset() if MANIFEST is None else MANIFEST.case_sensitive_place_names
)


def place_name_from_title(title: str) -> str:
    """The place name in a gazetteer heading, without any epithet.

    A page titled "Greyhaven, the old harbor" names "Greyhaven": that is what
    the chart says and what a GM writes.
    """
    return title.split(",", 1)[0].strip()


def gazetteer_place_names(directory: Path) -> dict[str, str]:
    """Slug to place name for every gazetteer page in ``directory``."""
    names: dict[str, str] = {}
    for page in sorted(directory.glob("*.md")):
        heading = _HEADING_PATTERN.search(page.read_text(encoding="utf-8"))
        if heading is None:
            raise ValueError(f"{page} has no top-level heading to take a name from")
        names[page.stem] = place_name_from_title(heading.group("title"))
    return names


def hub_sections(hub: Path) -> dict[str, tuple[str, ...]]:
    """Section heading to the gazetteer slugs linked under it, in hub order.

    A link counts when it points into the directory the hub sits beside:
    ``/<directory name>/<slug>/``.
    """
    text = hub.read_text(encoding="utf-8")
    link_pattern = re.compile(rf"/{re.escape(hub.stem)}/(?P<slug>[a-z0-9-]+)/")
    sections: dict[str, tuple[str, ...]] = {}
    boundaries = list(_SECTION_PATTERN.finditer(text))
    for index, boundary in enumerate(boundaries):
        end = boundaries[index + 1].start() if index + 1 < len(boundaries) else None
        body = text[boundary.end() : end]
        slugs = tuple(dict.fromkeys(link_pattern.findall(body)))
        sections[boundary.group("section")] = slugs
    return sections


def _island_place_names(
    section: str,
    hub: Path,
    hub_slugs: dict[str, tuple[str, ...]],
    names: dict[str, str],
) -> tuple[str, ...]:
    slugs = hub_slugs.get(section)
    if not slugs:
        raise ValueError(f"{hub} has no '## {section}' section with links")
    missing = [slug for slug in slugs if slug not in names]
    if missing:
        raise ValueError(f"{hub} links places with no gazetteer page: {missing}")
    return tuple(names[slug] for slug in slugs)


def build_map_areas(
    directory: Path | None = GAZETTEER_DIR,
    hub: Path | None = GAZETTEER_HUB,
    manifest: MapsManifest | None = MANIFEST,
) -> tuple[MapArea, ...]:
    """The registry, most specific map first, read from the gazetteer.

    Args:
        directory: The gazetteer directory (default: the manifest's).
        hub: The hub page (default: the manifest's).
        manifest: The canon's map manifest; None means the canon has no maps.

    Returns:
        The part-of-region charts, then the regional chart, then the world
        map when there is one; empty when there is no manifest.
    """
    if manifest is None or directory is None or hub is None:
        return ()
    names = gazetteer_place_names(directory)
    sections = hub_sections(hub)
    charts = tuple(
        MapArea(
            slug=chart.slug,
            title=chart.section,
            image=chart.image,
            place_names=_island_place_names(chart.section, hub, sections, names),
            scope=MapScope.ISLAND,
        )
        for chart in manifest.charts
    )
    region = MapArea(
        slug=manifest.region_slug,
        title=manifest.region_title,
        image=manifest.region_image,
        place_names=tuple(names.values()),
        scope=MapScope.REGION,
    )
    if manifest.world is None:
        return (*charts, region)
    world = MapArea(
        slug=manifest.world.slug,
        title=manifest.world.title,
        image=manifest.world.image,
        place_names=manifest.world.place_names,
        scope=MapScope.WORLD,
    )
    return (*charts, region, world)


MAP_AREAS: tuple[MapArea, ...] = build_map_areas()
MAP_AREAS_BY_SLUG: dict[str, MapArea] = {area.slug: area for area in MAP_AREAS}


@cache
def _place_pattern(place: str, case_sensitive: bool) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(rf"(?<!\w){re.escape(place)}(?!\w)", flags)


def _first_mention(text: str, area: MapArea) -> tuple[int, str] | None:
    """The earliest place of ``area`` named in ``text``: (position, name).

    Where two names start at the same position (a town and the wood named
    after it, say), the longer one is the place actually written.
    """
    earliest: tuple[int, str] | None = None
    for place in area.place_names:
        pattern = _place_pattern(place, place in CASE_SENSITIVE_PLACE_NAMES)
        match = pattern.search(text)
        if match is None:
            continue
        candidate = (match.start(), place)
        if (
            earliest is None
            or candidate[0] < earliest[0]
            or (candidate[0] == earliest[0] and len(place) > len(earliest[1]))
        ):
            earliest = candidate
    return earliest


def detect_place(text: str) -> DetectedPlace | None:
    """The map a GM reply should show, and the place name that names it.

    The most specific map whose places the reply names wins: a chart of part
    of the region over the region, the region over the world. A reply naming
    places on more than one such chart gets the regional chart, which shows all of them; the
    place reported is the earliest one written.
    """
    mentions = [
        (area, mention)
        for area in MAP_AREAS
        if (mention := _first_mention(text, area)) is not None
    ]
    if not mentions:
        return None
    island_mentions = [
        (area, mention) for area, mention in mentions if area.scope is MapScope.ISLAND
    ]
    if len(island_mentions) == 1:
        area, (_position, place) = island_mentions[0]
        return DetectedPlace(area=area, place=place)
    if island_mentions:
        region = next(area for area in MAP_AREAS if area.scope is MapScope.REGION)
        _position, place = min(mention for _area, mention in island_mentions)
        return DetectedPlace(area=region, place=place)
    area, (_position, place) = mentions[0]
    return DetectedPlace(area=area, place=place)


def detect_area(text: str) -> MapArea | None:
    """The map a GM reply should show, or None when it names no place."""
    detected = detect_place(text)
    return None if detected is None else detected.area


def area_for_place(place_name: str) -> MapArea | None:
    """The most specific chart that shows ``place_name``, or None.

    ``place_name`` is a name out of this registry, as
    :attr:`gm.models.CampaignState.place_key` holds it, so the comparison is
    against whole names rather than a scan of prose: the party is at the
    place the record names, not at whichever place a reply mentioned first.
    """
    if not place_name:
        return None
    wanted = place_name.casefold()
    for area in MAP_AREAS:
        if any(place.casefold() == wanted for place in area.place_names):
            return area
    return None


@dataclass(frozen=True)
class PartyPlace:
    """Where the party is, and the chart that shows it (issue #579).

    Built from the campaign record rather than from narration. A place the
    gazetteer never named has no chart, and the panel says so plainly
    instead of leaving the last map up as though the party were still on it.

    Attributes:
        place: Where the party is, in the words the table used.
        area: The chart showing it, or None when the place is not mapped.
    """

    place: str
    area: MapArea | None

    @property
    def mapped(self) -> bool:
        """Whether any chart shows this place."""
        return self.area is not None

    def frame_payload(self) -> dict[str, object]:
        """The ``map`` entry of the stream's ``done`` frame.

        Carries ``party: True`` so the panel captions it as the party's own
        location, and ``mapped: False`` for a place no chart shows, which
        the panel reports rather than acting on.
        """
        payload: dict[str, object] = {
            "place": self.place,
            "party": True,
            "mapped": self.mapped,
        }
        if self.area is not None:
            payload.update(
                slug=self.area.slug,
                title=self.area.title,
                image_url=self.area.image_url,
            )
        return payload


def party_place(place: str, place_key: str) -> PartyPlace | None:
    """Where the party's record puts them, or None when nothing is recorded.

    Args:
        place: :attr:`gm.models.CampaignState.place`, the table's own words.
        place_key: :attr:`gm.models.CampaignState.place_key`, the gazetteer
            name when the place is mapped and ``""`` when it is not.

    Returns:
        The party's place and its chart, or None when no place is recorded.
    """
    if not place:
        return None
    return PartyPlace(place=place, area=area_for_place(place_key))
