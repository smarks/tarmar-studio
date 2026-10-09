"""Hook parser tests against the real markdown, plus the drift guard (#276).

The reference page is the single source of truth for the hooks; the drift
guard asserts the page still publishes hooks in exactly the level bands the
generator matches against, that every hook carries its band, region, and
index-table type, and that every published level is rollable — so an edit
there fails here instead of silently changing output.
"""

from django.test import SimpleTestCase

from reference.paths import page_markdown_file
from ruins.hook_generation import EXPECTED_HOOK_LEVELS, eligible_hooks
from ruins.hooks import HOOKS_MARKDOWN_PATH, load_hooks, parse_hooks

REFERENCE_URL_PREFIX = "/tarmar/reference/"


class DriftGuardTests(SimpleTestCase):
    def test_markdown_bands_union_to_exactly_the_expected_levels(self):
        hooks = parse_hooks(HOOKS_MARKDOWN_PATH.read_text(encoding="utf-8"))
        published: set[int] = set()
        for hook in hooks:
            published.update(hook.levels)
        self.assertEqual(
            published,
            set(EXPECTED_HOOK_LEVELS),
            "low-experience-hooks.md bands drifted from ruins.hook_generation",
        )

    def test_every_hook_is_complete(self):
        for hook in load_hooks():
            self.assertTrue(hook.title)
            self.assertTrue(hook.anchor, hook.title)
            self.assertTrue(hook.section, hook.title)
            self.assertTrue(hook.hook_type, hook.title)
            self.assertTrue(hook.levels, hook.title)
            self.assertTrue(hook.region_markdown, hook.title)
            self.assertTrue(hook.body_markdown, hook.title)

    def test_every_published_level_is_rollable(self):
        # A level with a single eligible hook would need a 1d1, which the
        # dice grammar refuses — the page must keep at least two hooks in
        # every band it publishes.
        for level in EXPECTED_HOOK_LEVELS:
            self.assertGreaterEqual(len(eligible_hooks(level)), 2, level)

    def test_regional_hooks_link_their_region_pages(self):
        # Every region link resolves to a page of the loaded canon, so a
        # renamed or deleted place page fails here rather than 404ing.
        linked = [hook for hook in load_hooks() if hook.region_links]
        self.assertGreaterEqual(len(linked), 6)
        for hook in linked:
            for label, url in hook.region_links:
                with self.subTest(hook=hook.title, label=label):
                    self.assertTrue(url.startswith(REFERENCE_URL_PREFIX), url)
                    page_path = url[len(REFERENCE_URL_PREFIX) :].split("#")[0]
                    markdown_file, _root = page_markdown_file(page_path.strip("/"))
                    self.assertTrue(markdown_file.is_file(), url)


SYNTHETIC = """\
# Sample Hooks

Prose introduction.

| Hook | Region | Type |
| ---- | ------ | ---- |
| [The First Job](#the-first-job) | Townsville | Job |
| [What's in the Cellar](#whats-in-the-cellar) | Anywhere | Mystery |

## Jobs

### The First Job

**Region:** [Townsville](/tarmar/reference/dm-rules/the-world/townsville/)
(a day's ride out)

**Levels:** 1–2

Somebody needs a thing done.

Nobody else will do it.

## Mysteries

### What's in the Cellar

**Region:** anywhere with a cellar

**Levels:** 1

There is a noise.
"""


class ParserTests(SimpleTestCase):
    def test_parses_titles_sections_anchors_and_types(self):
        first, second = parse_hooks(SYNTHETIC)
        self.assertEqual(first.title, "The First Job")
        self.assertEqual(first.anchor, "the-first-job")
        self.assertEqual(first.section, "Jobs")
        self.assertEqual(first.hook_type, "Job")
        self.assertEqual(second.title, "What's in the Cellar")
        self.assertEqual(second.anchor, "whats-in-the-cellar")
        self.assertEqual(second.section, "Mysteries")
        self.assertEqual(second.hook_type, "Mystery")

    def test_parses_bands_single_and_ranged(self):
        first, second = parse_hooks(SYNTHETIC)
        self.assertEqual(first.levels, (1, 2))
        self.assertEqual(first.band_label, "1–2")
        self.assertEqual(second.levels, (1,))
        self.assertEqual(second.band_label, "1")

    def test_region_keeps_markdown_continuation_and_extracts_links(self):
        first, second = parse_hooks(SYNTHETIC)
        self.assertIn("(a day's ride out)", first.region_markdown)
        self.assertEqual(
            first.region_links,
            (("Townsville", "/tarmar/reference/dm-rules/the-world/townsville/"),),
        )
        self.assertEqual(second.region_links, ())

    def test_body_keeps_paragraph_breaks(self):
        first, _ = parse_hooks(SYNTHETIC)
        self.assertEqual(
            first.body_markdown,
            "Somebody needs a thing done.\n\nNobody else will do it.",
        )

    def test_hook_without_band_is_refused(self):
        broken = SYNTHETIC.replace("**Levels:** 1–2\n\n", "")
        with self.assertRaisesMessage(ValueError, "no **Levels:** band"):
            parse_hooks(broken)

    def test_hook_without_region_is_refused(self):
        broken = SYNTHETIC.replace("**Region:** anywhere with a cellar\n\n", "")
        with self.assertRaisesMessage(ValueError, "no **Region:** line"):
            parse_hooks(broken)

    def test_unparseable_band_is_refused(self):
        broken = SYNTHETIC.replace("**Levels:** 1–2", "**Levels:** one to two")
        with self.assertRaisesMessage(ValueError, "unparseable band"):
            parse_hooks(broken)

    def test_inverted_band_is_refused(self):
        broken = SYNTHETIC.replace("**Levels:** 1–2", "**Levels:** 2–1")
        with self.assertRaisesMessage(ValueError, "inverted band"):
            parse_hooks(broken)

    def test_section_missing_from_index_is_refused(self):
        broken = SYNTHETIC.replace(
            "| [The First Job](#the-first-job) | Townsville | Job |\n", ""
        )
        with self.assertRaisesMessage(ValueError, "missing from the index table"):
            parse_hooks(broken)

    def test_index_row_without_a_section_is_refused(self):
        broken = SYNTHETIC + "\n"
        broken = broken.replace(
            "| [What's in the Cellar](#whats-in-the-cellar) | Anywhere | Mystery |",
            "| [What's in the Cellar](#whats-in-the-cellar) | Anywhere | Mystery |\n"
            "| [The Ghost Row](#the-ghost-row) | Nowhere | Mystery |",
        )
        with self.assertRaisesMessage(ValueError, "no section"):
            parse_hooks(broken)

    def test_duplicate_anchor_is_refused(self):
        broken = SYNTHETIC + (
            "\n### What's in the Cellar\n\n**Region:** elsewhere\n\n"
            "**Levels:** 1\n\nAnother noise.\n"
        )
        with self.assertRaisesMessage(ValueError, "Duplicate hook anchor"):
            parse_hooks(broken)
