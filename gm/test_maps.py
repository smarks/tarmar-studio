"""Tests for the GM panel's maps (issue #343).

Covers the registry read from the gazetteer, the narration detector, the
``map`` entry of the stream's ``done`` frame, the Map tab of the chat page,
and the standalone map page the tab pops out to (issue #351).

Which maps exist is canon (issue #918), so the tests come in three kinds:

- The detector, the frames and the pages run against a made-up map canon in
  ``gm/test_data/map_canon/`` (three part-of-region charts, a region, a world
  map, everyday-word place names, an epithet), patched in as the registry, so
  they prove the same rules whichever canon the app loaded.
- The loaded canon's registry and the default canon's are checked for shape:
  every image on disk, every gazetteer page on the regional chart, every hub
  link a page.
- What only the Tarmar canon has (its five maps and their place names) is
  pinned in :class:`TarmarCanonMapsTest`, skipped under another canon.
"""

import json
import shutil
import tempfile
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase

from common.test_helpers import (
    CANON_EXPECTATIONS,
    BaseTestCase,
    skip_unless_canon_expects,
)
from reference.paths import CANON_NAME, DEFAULT_CANON_DIR, TARMAR_CANON_NAME

from . import maps, views
from .maps import (
    CASE_SENSITIVE_PLACE_NAMES,
    GAZETTEER_DIR,
    GAZETTEER_HUB,
    HUB_ISLAND_SECTIONS,
    MAP_AREAS,
    MAP_AREAS_BY_SLUG,
    MAPS_MANIFEST_NAME,
    WORLD_PLACE_NAMES,
    DetectedPlace,
    MapArea,
    MapScope,
    MapsManifest,
    area_for_place,
    build_map_areas,
    detect_area,
    detect_place,
    gazetteer_place_names,
    hub_sections,
    load_maps_manifest,
    party_place,
    place_name_from_title,
)
from .models import CampaignState, GMSession
from .testing import gm_replies

STREAM_URL = "/tarmar/gm/stream/"
CHAT_URL = "/tarmar/gm/"
MAP_URL = "/tarmar/gm/map/"
IMAGES_DIR = Path(settings.BASE_DIR) / "static" / "images"

TEST_CANON_MANIFEST = Path(__file__).parent / "test_data" / "map_canon" / "maps.json"


def _required_manifest(path: Path) -> MapsManifest:
    """The manifest at ``path``, which the test data guarantees exists."""
    manifest = load_maps_manifest(path)
    assert manifest is not None, f"{path} is missing"
    return manifest


TEST_MANIFEST = _required_manifest(TEST_CANON_MANIFEST)
TEST_AREAS: tuple[MapArea, ...] = build_map_areas(
    TEST_MANIFEST.gazetteer, TEST_MANIFEST.hub, TEST_MANIFEST
)
TEST_AREAS_BY_SLUG: dict[str, MapArea] = {area.slug: area for area in TEST_AREAS}


class TestCanonMapsMixin:
    """Patch the made-up map canon in as the registry for one test.

    ``gm.views`` imported ``MAP_AREAS`` by name, so it is patched there too.
    """

    def setUp(self) -> None:
        super().setUp()  # type: ignore[misc]
        for target, name, value in (
            (maps, "MAP_AREAS", TEST_AREAS),
            (maps, "MAP_AREAS_BY_SLUG", TEST_AREAS_BY_SLUG),
            (
                maps,
                "CASE_SENSITIVE_PLACE_NAMES",
                TEST_MANIFEST.case_sensitive_place_names,
            ),
            (views, "MAP_AREAS", TEST_AREAS),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)  # type: ignore[attr-defined]


def _assert_registry_shape(
    test: SimpleTestCase, areas: tuple[MapArea, ...], manifest: MapsManifest
) -> None:
    """Check a canon's registry against its own gazetteer and manifest."""
    by_slug = {area.slug: area for area in areas}
    test.assertEqual(len(by_slug), len(areas), "map slugs must be unique")
    test.assertEqual(
        [area.scope for area in areas], sorted(area.scope for area in areas)[::-1]
    )
    regions = [area for area in areas if area.scope is MapScope.REGION]
    test.assertEqual(len(regions), 1)
    region = regions[0]

    names = gazetteer_place_names(manifest.gazetteer)
    pages = sorted(page.stem for page in manifest.gazetteer.glob("*.md"))
    test.assertTrue(pages, "the gazetteer has no pages")
    test.assertEqual(sorted(names), pages)
    test.assertEqual(sorted(region.place_names), sorted(names.values()))

    sections = hub_sections(manifest.hub)
    for section, slugs in sections.items():
        with test.subTest(section=section):
            test.assertTrue(slugs, f"'## {section}' links no places")
            test.assertTrue(set(slugs) <= set(names))
    chart_names = []
    for chart in manifest.charts:
        with test.subTest(chart=chart.slug):
            area = by_slug[chart.slug]
            test.assertEqual(area.scope, MapScope.ISLAND)
            test.assertEqual(area.title, chart.section)
            test.assertEqual(
                area.place_names,
                tuple(names[page_slug] for page_slug in sections[chart.section]),
            )
            test.assertTrue(set(area.place_names) <= set(region.place_names))
            chart_names.append(set(area.place_names))
    for index, first in enumerate(chart_names):
        for second in chart_names[index + 1 :]:
            test.assertEqual(first & second, set())

    if manifest.world is not None:
        test.assertEqual(
            by_slug[manifest.world.slug].place_names, manifest.world.place_names
        )
    every_name = {name for area in areas for name in area.place_names}
    test.assertTrue(manifest.case_sensitive_place_names <= every_name)


class LoadedCanonMapsTest(SimpleTestCase):
    """The loaded canon's registry, whichever canon that is."""

    def test_the_registry_is_read_from_the_canon_manifest(self):
        manifest = load_maps_manifest()
        assert manifest is not None, "the loaded canon has no maps.json"
        self.assertEqual(GAZETTEER_DIR, manifest.gazetteer)
        self.assertEqual(GAZETTEER_HUB, manifest.hub)
        self.assertTrue(manifest.hub.is_file())
        self.assertEqual(build_map_areas(), MAP_AREAS)
        self.assertEqual(
            HUB_ISLAND_SECTIONS,
            {chart.section: chart.slug for chart in manifest.charts},
        )
        self.assertEqual(
            WORLD_PLACE_NAMES,
            () if manifest.world is None else manifest.world.place_names,
        )
        self.assertEqual(
            CASE_SENSITIVE_PLACE_NAMES, manifest.case_sensitive_place_names
        )

    def test_the_registry_matches_its_gazetteer(self):
        manifest = load_maps_manifest()
        assert manifest is not None
        _assert_registry_shape(self, MAP_AREAS, manifest)

    def test_every_map_image_exists_and_has_a_static_url(self):
        self.assertTrue(MAP_AREAS)
        for area in MAP_AREAS:
            with self.subTest(area=area.slug):
                self.assertTrue((IMAGES_DIR / area.image).is_file())
                self.assertEqual(area.image_url, f"/static/images/{area.image}")
                self.assertTrue(area.place_names)
                self.assertEqual(MAP_AREAS_BY_SLUG[area.slug], area)

    def test_every_regional_place_is_detected_on_some_chart(self):
        for area in MAP_AREAS:
            for place in area.place_names:
                with self.subTest(place=place):
                    self.assertIsNotNone(detect_place(f"They reach {place} at dusk."))


class DefaultCanonMapsTest(SimpleTestCase):
    """The default canon's maps build and resolve, whatever canon is loaded."""

    def setUp(self):
        self.manifest = _required_manifest(DEFAULT_CANON_DIR / MAPS_MANIFEST_NAME)
        self.areas = build_map_areas(
            self.manifest.gazetteer, self.manifest.hub, self.manifest
        )

    def test_the_default_registry_matches_its_gazetteer(self):
        _assert_registry_shape(self, self.areas, self.manifest)

    def test_the_default_region_has_eight_to_twelve_places_and_an_image(self):
        region = next(area for area in self.areas if area.scope is MapScope.REGION)
        self.assertGreaterEqual(len(region.place_names), 8)
        self.assertLessEqual(len(region.place_names), 12)
        for area in self.areas:
            with self.subTest(area=area.slug):
                self.assertTrue((IMAGES_DIR / area.image).is_file())


@skipUnless(CANON_NAME == TARMAR_CANON_NAME, "pins the Tarmar canon's own maps")
class TarmarCanonMapsTest(SimpleTestCase):
    """The Tarmar canon's five maps and the places on them."""

    def test_registry_lists_the_five_maps_most_specific_first(self):
        self.assertEqual(
            [area.slug for area in MAP_AREAS],
            ["maja", "solberg", "old-qasha", "maja-solberg", "world"],
        )
        self.assertEqual(
            [area.scope for area in MAP_AREAS],
            [
                MapScope.ISLAND,
                MapScope.ISLAND,
                MapScope.ISLAND,
                MapScope.REGION,
                MapScope.WORLD,
            ],
        )
        self.assertEqual(
            [area.image for area in MAP_AREAS],
            [
                "maja-island.png",
                "solberg-island.png",
                "old-qasha-island.png",
                "maja-solberg-map.png",
                "world-map-overview.png",
            ],
        )

    def test_the_places_on_each_chart(self):
        region = MAP_AREAS_BY_SLUG["maja-solberg"]
        for place in ("Majahelm", "Eldfaxi", "Kingfort"):
            self.assertIn(place, region.place_names)
        self.assertIn("Majahelm", MAP_AREAS_BY_SLUG["maja"].place_names)
        self.assertIn("Seaside", MAP_AREAS_BY_SLUG["solberg"].place_names)
        self.assertIn("Mareld", MAP_AREAS_BY_SLUG["old-qasha"].place_names)

    @skip_unless_canon_expects("world_map_labels")
    def test_world_place_names_are_the_drawn_labels(self):
        # The labels come from the canon's tests/expectations.json: some are on
        # no public page, so this repository does not name them (#922).
        world = MAP_AREAS_BY_SLUG["world"]
        for label in CANON_EXPECTATIONS["world_map_labels"]:
            with self.subTest(label=label):
                self.assertIn(label, world.place_names)

    def test_its_everyday_word_places_match_only_as_written(self):
        self.assertIsNone(detect_area("the truth is, the oracle at the seaside lied"))
        self.assertIsNone(detect_area("She stared at her navel."))

    def test_a_reply_naming_the_capital_picks_its_island(self):
        detected = detect_place("The road climbs toward Majahelm by evening.")
        self.assertEqual(detected, DetectedPlace(MAP_AREAS_BY_SLUG["maja"], "Majahelm"))


class MapRegistryTest(SimpleTestCase):
    """The registry built from a gazetteer, on the made-up map canon."""

    def test_the_test_canon_registry_matches_its_gazetteer(self):
        _assert_registry_shape(self, TEST_AREAS, TEST_MANIFEST)
        self.assertEqual(
            [area.slug for area in TEST_AREAS],
            ["aster", "brightwater", "old-vey", "the-isles", "world"],
        )
        self.assertIn("Cinderpeak", TEST_AREAS_BY_SLUG["the-isles"].place_names)
        self.assertIn("Farhold", TEST_AREAS_BY_SLUG["the-isles"].place_names)

    def test_a_canon_without_a_manifest_has_no_maps(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        self.assertIsNone(load_maps_manifest(directory / MAPS_MANIFEST_NAME))
        self.assertEqual(build_map_areas(None, None, None), ())

    def test_a_manifest_missing_a_field_is_an_error(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        manifest = directory / MAPS_MANIFEST_NAME
        region = '"slug": "v", "title": "V", "image": "v.png", "gazetteer": "v"'
        for body, message in (
            ('{"region": {"slug": "vale"}}', "region.title must be a str"),
            ("{not json", "is not valid JSON"),
            ("[]", "must hold a JSON object"),
            (f'{{"region": {{{region}, "charts": {{}}}}}}', "region.charts"),
            (
                f'{{"region": {{{region}, "charts": [{{"section": "A"}}]}}}}',
                "region.charts[0].slug",
            ),
            (
                f'{{"region": {{{region}}}, "world": {{"slug": "w", "title": "W", '
                '"image": "w.png", "place_names": "Spine"}}',
                "world.place_names must be a list of strings",
            ),
            (
                f'{{"region": {{{region}}}, "case_sensitive_place_names": "Bell"}}',
                "case_sensitive_place_names must be a list of strings",
            ),
            (
                f'{{"region": {{{region}, "charts": [{{"section": "A", '
                '"slug": "v", "image": "a.png"}]}}',
                "map slugs must be unique, repeated: ['v']",
            ),
            (
                f'{{"region": {{{region}, "charts": ['
                '{"section": "A", "slug": "a", "image": "a.png"}, '
                '{"section": "B", "slug": "a", "image": "b.png"}]}}',
                "map slugs must be unique, repeated: ['a']",
            ),
        ):
            with self.subTest(body=body):
                manifest.write_text(body, encoding="utf-8")
                with self.assertRaisesMessage(ValueError, message):
                    load_maps_manifest(manifest)
                with self.assertRaisesMessage(ValueError, str(manifest)):
                    load_maps_manifest(manifest)

    def test_place_name_from_title_drops_the_epithet(self):
        self.assertEqual(
            place_name_from_title("Cinderpeak, the burning hill"), "Cinderpeak"
        )
        self.assertEqual(place_name_from_title("Farhold, the grim"), "Farhold")
        self.assertEqual(place_name_from_title("  Hesk Wood "), "Hesk Wood")

    def test_gazetteer_page_without_a_heading_is_an_error(self):
        directory, _hub = self._make_gazetteer({"nowhere.md": "*Draft.*\n"})
        with self.assertRaisesMessage(ValueError, "no top-level heading"):
            gazetteer_place_names(directory)

    def test_hub_without_a_chart_section_is_an_error(self):
        directory, hub = self._make_gazetteer({"aster.md": "# Aster\n"})
        hub.write_text("# Hub\n\n## Brightwater\n\n- [x](/vale/aster/)\n")
        with self.assertRaisesMessage(ValueError, "no '## Aster' section"):
            build_map_areas(directory, hub, self._manifest(directory))

    def test_hub_link_without_a_page_is_an_error(self):
        directory, hub = self._make_gazetteer({"aster.md": "# Aster\n"})
        hub.write_text(
            "# Hub\n\n## Aster\n\n- [x](/vale/aster/)\n- [y](/vale/atlantis/)\n"
        )
        with self.assertRaisesMessage(ValueError, "['atlantis']"):
            build_map_areas(directory, hub, self._manifest(directory))

    def test_hub_sections_keep_link_order_and_drop_repeats(self):
        _directory, hub = self._make_gazetteer({})
        hub.write_text(
            "# Hub\n\nIntro links [a](/vale/intro/) ignored.\n\n"
            "## First\n\n- [b](/vale/beta/) and [a](/vale/alpha/)\n"
            "- [b again](/vale/beta/)\n- [elsewhere](/other/delta/)\n\n"
            "## Second\n\n- [c](/vale/gamma/)\n"
        )
        self.assertEqual(
            hub_sections(hub),
            {"First": ("beta", "alpha"), "Second": ("gamma",)},
        )

    def _make_gazetteer(self, pages: dict[str, str]) -> tuple[Path, Path]:
        """A throwaway gazetteer ``vale/`` holding ``pages``, and its hub path."""
        parent = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        directory = parent / "vale"
        directory.mkdir()
        for name, page_text in pages.items():
            (directory / name).write_text(page_text, encoding="utf-8")
        return directory, parent / "vale.md"

    def _manifest(self, directory: Path) -> MapsManifest:
        """A manifest with one chart, ``Aster``, over ``directory``."""
        return MapsManifest(
            region_slug="vale",
            region_title="Vale",
            region_image="vale.png",
            gazetteer=directory,
            charts=(maps.ChartSpec(section="Aster", slug="aster", image="aster.png"),),
            world=None,
            case_sensitive_place_names=frozenset(),
        )


class DetectPlaceTest(TestCanonMapsMixin, SimpleTestCase):
    """What a GM reply's place names pick."""

    def test_no_place_named_is_none(self):
        self.assertIsNone(detect_place("You wake in a nameless inn and eat bread."))
        self.assertIsNone(detect_area(""))

    def test_a_chart_place_picks_that_chart(self):
        detected = detect_place("The road climbs toward Asterhold by evening.")
        self.assertEqual(
            detected, DetectedPlace(TEST_AREAS_BY_SLUG["aster"], "Asterhold")
        )
        self.assertEqual(
            detect_area("Ash drifts from Cinderpeak."),
            TEST_AREAS_BY_SLUG["brightwater"],
        )
        self.assertEqual(
            detect_area("Mardle's fishers watch you land."),
            TEST_AREAS_BY_SLUG["old-vey"],
        )

    def test_chart_beats_region_beats_world(self):
        text = "Across The Shards, past Vey's Tree, you reach Tollby."
        detected = detect_place(text)
        assert detected is not None
        self.assertEqual(detected.area.slug, "brightwater")
        self.assertEqual(detected.place, "Tollby")

        region_only = "You sail the straits by Vey's Tree, out of The Shards."
        detected = detect_place(region_only)
        assert detected is not None
        self.assertEqual(detected.area.slug, "the-isles")
        self.assertEqual(detected.place, "Vey's Tree")

        world_only = "Traders speak of the Old Kingdom and the Far Steppe."
        detected = detect_place(world_only)
        assert detected is not None
        self.assertEqual(detected.area.slug, "world")
        self.assertEqual(detected.place, "Old Kingdom")

    def test_two_charts_named_picks_the_regional_chart(self):
        detected = detect_place("From Strand on Brightwater you can see Aster's peaks.")
        assert detected is not None
        self.assertEqual(detected.area.slug, "the-isles")
        self.assertEqual(detected.place, "Strand")

    def test_match_is_case_insensitive_and_whole_word(self):
        self.assertEqual(
            detect_area("you arrive at ASTERHOLD"), TEST_AREAS_BY_SLUG["aster"]
        )
        self.assertEqual(detect_area("Skell, at last."), TEST_AREAS_BY_SLUG["aster"])
        self.assertIsNone(detect_area("An asterholder? No such thing."))
        self.assertIsNone(detect_area("The skelling birds wheel overhead."))
        # "Aster" inside "Asterhold" is not a mention of Aster; Asterhold is.
        detected = detect_place("Asterhold rises.")
        assert detected is not None
        self.assertEqual(detected.place, "Asterhold")

    def test_common_words_are_not_places_unless_capitalised(self):
        self.assertIsNone(detect_area("The bell rang down on the strand."))
        self.assertIsNone(detect_area("She felt it in her spine."))
        self.assertEqual(
            detect_area("You reach Bell by noon."), TEST_AREAS_BY_SLUG["aster"]
        )
        self.assertEqual(
            detect_area("Strand is quiet."), TEST_AREAS_BY_SLUG["brightwater"]
        )

    def test_the_earliest_named_place_is_reported(self):
        detected = detect_place("Past Corve and then Lamport, you rest.")
        assert detected is not None
        self.assertEqual(detected.place, "Corve")

    def test_the_longer_name_wins_at_the_same_position(self):
        detected = detect_place("Hesk Wood closes around you.")
        assert detected is not None
        self.assertEqual(detected.place, "Hesk Wood")
        detected = detect_place("Hesk is a small town.")
        assert detected is not None
        self.assertEqual(detected.place, "Hesk")

    def test_frame_payload_shape(self):
        detected = detect_place("Welcome to Asterhold.")
        assert detected is not None
        self.assertEqual(
            detected.frame_payload(),
            {
                "slug": "aster",
                "title": "Aster",
                "image_url": "/static/images/test-canon/aster-chart.png",
                "place": "Asterhold",
            },
        )


class MapStreamFrameTest(TestCanonMapsMixin, BaseTestCase):
    """The ``map`` entry of the ``done`` frame."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)

    def _done_frame(self, response) -> dict:
        frames = [
            json.loads(line[len("data: ") :])
            for line in b"".join(response.streaming_content).decode().splitlines()
            if line.startswith("data: ")
        ]
        done_frames = [frame for frame in frames if frame.get("done")]
        self.assertEqual(len(done_frames), 1)
        return done_frames[0]

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_done_frame_carries_the_map_a_reply_names(self, mock_stream):
        mock_stream.side_effect = gm_replies(["You ride ", "into Asterhold."])
        response = self.client.post(
            STREAM_URL, json.dumps({"message": "Go"}), content_type="application/json"
        )
        frame = self._done_frame(response)
        self.assertEqual(
            frame["map"],
            {
                "slug": "aster",
                "title": "Aster",
                "image_url": "/static/images/test-canon/aster-chart.png",
                "place": "Asterhold",
            },
        )

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_done_frame_has_no_map_when_no_place_is_named(self, mock_stream):
        mock_stream.side_effect = gm_replies(["You wake in a ditch."])
        response = self.client.post(
            STREAM_URL, json.dumps({"message": "Go"}), content_type="application/json"
        )
        self.assertNotIn("map", self._done_frame(response))

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_map_is_detected_from_the_reply_without_its_thinking(self, mock_stream):
        mock_stream.side_effect = gm_replies(
            ["<think>maybe Strand?</think>", "The road to Mardle is long."]
        )
        response = self.client.post(
            STREAM_URL, json.dumps({"message": "Go"}), content_type="application/json"
        )
        self.assertEqual(self._done_frame(response)["map"]["slug"], "old-vey")


class PartyPlaceTest(TestCanonMapsMixin, SimpleTestCase):
    """Where the party's record puts them, and which chart shows it (#579)."""

    def test_a_gazetteer_name_finds_its_most_specific_chart(self):
        found = {
            place: area_for_place(place) for place in ("Lamport", "Strand", "Spine")
        }

        self.assertEqual(
            {place: area.slug if area else None for place, area in found.items()},
            {"Lamport": "aster", "Strand": "brightwater", "Spine": "world"},
        )

    def test_a_name_no_chart_shows_has_no_chart(self):
        self.assertIsNone(area_for_place("Grandma's cellar"))
        self.assertIsNone(area_for_place(""))

    def test_a_recorded_mapped_place_carries_its_chart(self):
        where = party_place("the well beneath Lamport", "Lamport")

        assert where is not None
        self.assertTrue(where.mapped)
        self.assertEqual(
            where.frame_payload(),
            {
                "place": "the well beneath Lamport",
                "party": True,
                "mapped": True,
                "slug": "aster",
                "title": "Aster",
                "image_url": "/static/images/test-canon/aster-chart.png",
            },
        )

    def test_an_unmapped_place_is_reported_as_one(self):
        where = party_place("Grandma's cellar", "")

        assert where is not None
        self.assertFalse(where.mapped)
        self.assertEqual(
            where.frame_payload(),
            {"place": "Grandma's cellar", "party": True, "mapped": False},
        )

    def test_nothing_recorded_is_no_place_at_all(self):
        self.assertIsNone(party_place("", ""))


class PartyMapFrameTest(TestCanonMapsMixin, BaseTestCase):
    """The Map tab follows the party's record, not the prose (issue #579).

    ``place_key`` was recorded from the first turn this issue shipped and
    nothing read it, so a reply that mentioned a place in passing dragged the
    tab there while the party sat somewhere else entirely.
    """

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)
        self.session = GMSession.objects.create(user=self.user, name="the well")

    def _done_frame(self, response) -> dict:
        frames = [
            json.loads(line[len("data: ") :])
            for line in b"".join(response.streaming_content).decode().splitlines()
            if line.startswith("data: ")
        ]
        done_frames = [frame for frame in frames if frame.get("done")]
        self.assertEqual(len(done_frames), 1)
        return done_frames[0]

    def _turn(self, reply: str) -> dict:
        """Play one turn on this session and return its ``done`` frame.

        The stream is drained inside the patch: a streaming response runs its
        generator when it is read, not when it is returned.
        """
        with patch("gm.turn_engine.model.ollama_client.stream_chat") as mock_stream:
            mock_stream.side_effect = gm_replies([reply])
            response = self.client.post(
                STREAM_URL,
                json.dumps({"session_id": self.session.pk, "message": "Go on"}),
                content_type="application/json",
            )
            return self._done_frame(response)

    def test_the_record_beats_a_place_the_reply_only_mentioned(self):
        CampaignState.objects.create(
            session=self.session, place="the well beneath Lamport", place_key="Lamport"
        )

        frame = self._turn("Asterhold is four days north, the ferryman says.")

        self.assertEqual(frame["map"]["slug"], "aster")
        self.assertEqual(frame["map"]["place"], "the well beneath Lamport")
        self.assertTrue(frame["map"]["party"])

    def test_an_unmapped_party_is_reported_rather_than_moved(self):
        CampaignState.objects.create(session=self.session, place="Grandma's cellar")

        frame = self._turn("Strand is a long way off now.")

        self.assertEqual(
            frame["map"],
            {"place": "Grandma's cellar", "party": True, "mapped": False},
        )

    def test_a_session_with_no_place_recorded_still_reads_the_reply(self):
        frame = self._turn("You ride into Asterhold.")

        self.assertEqual(frame["map"]["slug"], "aster")
        self.assertNotIn("party", frame["map"])

    def test_the_page_opens_where_the_party_is(self):
        CampaignState.objects.create(
            session=self.session, place="the harbour at Lamport", place_key="Lamport"
        )

        response = self.client.get(f"{CHAT_URL}?session={self.session.pk}")

        self.assertEqual(
            response.context["map_config"]["partyMap"],
            {
                "place": "the harbour at Lamport",
                "party": True,
                "mapped": True,
                "slug": "aster",
                "title": "Aster",
                "image_url": "/static/images/test-canon/aster-chart.png",
            },
        )


class MapTabTest(TestCanonMapsMixin, BaseTestCase):
    """The Map tab of the chat page."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)

    def test_map_tab_lists_every_map(self):
        response = self.client.get(CHAT_URL)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('data-panel-tab="map"', content)
        self.assertIn('id="gm-panel-map"', content)
        self.assertIn('id="gm-map-follow"', content)
        # The picker names the regional chart as the map to open on (#918).
        self.assertIn(
            '<select id="gm-map-select" data-default-slug="the-isles">', content
        )
        for area in TEST_AREAS:
            self.assertIn(
                f'<option value="{area.slug}" data-title="{area.title}" '
                f'data-image-url="{area.image_url}">',
                content.replace("&amp;", "&"),
            )
        self.assertEqual(
            response.context["map_config"],
            {"sessionId": None, "partyMap": None, "storyMap": None},
        )
        self.assertIn("js/gm-panel-map.js", content)

    def test_story_map_is_the_latest_reply_naming_a_place(self):
        session = GMSession.objects.create(name="Quest", user=self.user)
        session.history = [
            {"role": "user", "content": "Where am I?"},
            {"role": "assistant", "content": "You stand in Asterhold."},
            {"role": "user", "content": "Sail to Strand."},
            {"role": "assistant", "content": "The wind favours you; Strand nears."},
            {"role": "user", "content": "Rest."},
            {"role": "assistant", "content": "You sleep soundly."},
        ]
        session.save()
        response = self.client.get(f"{CHAT_URL}?session={session.pk}")
        self.assertEqual(
            response.context["map_config"],
            {
                "sessionId": session.pk,
                "partyMap": None,
                "storyMap": {
                    "slug": "brightwater",
                    "title": "Brightwater",
                    "image_url": "/static/images/test-canon/brightwater-chart.png",
                    "place": "Strand",
                },
            },
        )

    def test_story_map_ignores_the_player_naming_a_place(self):
        session = GMSession.objects.create(name="Quest", user=self.user)
        session.history = [
            {"role": "user", "content": "Take me to Asterhold."},
            {"role": "assistant", "content": "Not yet."},
        ]
        session.save()
        response = self.client.get(f"{CHAT_URL}?session={session.pk}")
        self.assertIsNone(response.context["map_config"]["storyMap"])


class MapPageTest(TestCanonMapsMixin, BaseTestCase):
    """The map on its own page (issue #351), where the Map pane pops out to."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)

    def test_the_page_renders_the_panel_partial_from_the_registry(self):
        response = self.client.get(MAP_URL)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # The same section the pane holds, from the same registry -- no
        # second copy of the markup and no second list of maps.
        self.assertIn('id="gm-panel-map"', content)
        self.assertIn('data-map-standalone="true"', content)
        self.assertIn("js/gm-panel-map.js", content)
        self.assertTrue(response.context["map_standalone"])
        for area in TEST_AREAS:
            self.assertIn(
                f'<option value="{area.slug}" data-title="{area.title}" '
                f'data-image-url="{area.image_url}">',
                content.replace("&amp;", "&"),
            )

    def test_standing_alone_it_is_neither_hidden_nor_a_tabpanel(self):
        content = self.client.get(MAP_URL).content.decode()
        section = content[content.index('id="gm-panel-map"') :]
        section = section[: section.index(">")]
        self.assertNotIn("hidden", section)
        self.assertNotIn("aria-labelledby", section)
        self.assertNotIn("data-popout-url", section)

    def test_the_pane_on_the_chat_page_points_at_this_page(self):
        content = self.client.get(CHAT_URL).content.decode()
        self.assertIn(f'data-popout-url="{MAP_URL}"', content)

    def test_a_session_gives_the_page_the_story_it_follows(self):
        session = GMSession.objects.create(name="Quest", user=self.user)
        session.history = [
            {"role": "user", "content": "Where am I?"},
            {"role": "assistant", "content": "Strand's roofs come into view."},
        ]
        session.save()
        response = self.client.get(f"{MAP_URL}?session={session.pk}")
        self.assertEqual(response.context["map_config"]["sessionId"], session.pk)
        self.assertEqual(
            response.context["map_config"]["storyMap"]["slug"], "brightwater"
        )
        self.assertContains(response, f'href="{CHAT_URL}?session={session.pk}"')

    def test_a_session_that_is_not_the_viewers_is_no_session_at_all(self):
        other = self.create_user(username="other")
        session = GMSession.objects.create(name="Theirs", user=other)
        response = self.client.get(f"{MAP_URL}?session={session.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["map_config"]["sessionId"])

    def test_a_junk_session_still_renders_the_maps(self):
        response = self.client.get(f"{MAP_URL}?session=not-a-number")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["map_config"],
            {"sessionId": None, "partyMap": None, "storyMap": None},
        )

    def test_the_page_requires_login(self):
        self.logout()
        response = self.client.get(MAP_URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])
