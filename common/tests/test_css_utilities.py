"""Shared CSS utilities stay defined once (#338 items 13, 14 and 27).

Each of these was once written in several sheets or fought over between them:
three copies of the screen-reader-only rule, a ``.delete-btn`` that meant a
solid button in base.css and an outlined one on the users list, and battle.css
blocks that existed only to outrank base.css's bare submit rule. These tests
keep the single definitions single.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
CSS_DIRECTORY = ROOT / "static" / "css"
TEMPLATE_ROOTS = [ROOT / "templates", *ROOT.glob("*/templates")]


def _without_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _sheets() -> dict[str, str]:
    return {
        sheet.name: _without_comments(sheet.read_text())
        for sheet in sorted(CSS_DIRECTORY.glob("*.css"))
    }


def _selectors(css: str) -> list[str]:
    return [
        " ".join(selector.split())
        for match in re.finditer(r"([^{}]+)\{", css)
        for selector in match.group(1).split(",")
        if not selector.strip().startswith("@")
    ]


class ScreenReaderOnlyUtilityTest(SimpleTestCase):
    """One .sr-only, in base.css, used everywhere."""

    def test_only_base_defines_a_screen_reader_only_utility(self):
        sheets = _sheets()
        self.assertIn(".sr-only", _selectors(sheets["base.css"]))
        for name, css in sheets.items():
            with self.subTest(sheet=name):
                self.assertNotIn(".visually-hidden", _selectors(css))
                if name != "base.css":
                    self.assertNotIn(".sr-only", _selectors(css))

    def test_no_template_uses_the_old_visually_hidden_class(self):
        templates = [
            template
            for template_root in TEMPLATE_ROOTS
            for template in template_root.rglob("*.html")
        ]
        self.assertGreater(len(templates), 20)
        offenders = [
            str(template.relative_to(ROOT))
            for template in templates
            if "visually-hidden" in template.read_text()
        ]
        self.assertEqual(offenders, [])


class DeleteButtonMeansOneThingTest(SimpleTestCase):
    """.delete-btn is the solid button; the outlined one has its own name."""

    def test_only_base_styles_the_delete_buttons(self):
        for name, css in _sheets().items():
            if name == "base.css":
                continue
            with self.subTest(sheet=name):
                self.assertEqual(
                    [
                        selector
                        for selector in _selectors(css)
                        if re.search(r"\.delete-btn(?![\w-])", selector)
                        or ".delete-btn-outline" in selector
                    ],
                    [],
                )

    def test_base_defines_both_delete_button_looks(self):
        base_selectors = _selectors(_sheets()["base.css"])
        self.assertIn(".delete-btn", base_selectors)
        self.assertIn(".delete-btn-outline", base_selectors)


class CharacterSheetRulesLiveInItsOwnSheetTest(SimpleTestCase):
    """The character editor's rules are in character-edit.css only (#338 item 10).

    base.css used to declare the sheet's containers and dice log, and
    character-edit.css re-declared most of them, so a change to one copy was
    silently overridden by the other.
    """

    EDITOR_ONLY = (
        ".character-sheet",
        ".stats-section h2",
        ".subtitle",
        ".dice-log",
        ".dice-log pre",
        ".inline-form",
        ".stats-table",
        ".stats-table th",
        ".rolled-stats",
        ".stats-grid",
        ".stat-group h3",
    )

    def test_base_declares_none_of_the_sheets_selectors(self):
        base_selectors = _selectors(_sheets()["base.css"])
        for selector in self.EDITOR_ONLY:
            with self.subTest(selector=selector):
                self.assertNotIn(selector, base_selectors)

    def test_the_editor_sheet_declares_its_own(self):
        editor_selectors = _selectors(_sheets()["character-edit.css"])
        for selector in (".character-sheet", ".subtitle", ".dice-log", ".dice-log pre"):
            with self.subTest(selector=selector):
                self.assertIn(selector, editor_selectors)

    def test_the_editor_sheet_has_no_bare_main_rule(self):
        self.assertNotIn("main", _selectors(_sheets()["character-edit.css"]))


class NoSheetSuppressesTheFocusRingTest(SimpleTestCase):
    """`outline: none` only where the rule paints its own indicator (#701, #737).

    #701 took the declaration out of base.css because it left a keyboard user
    nothing but a 1px border moving from grey to near-black, and installed a
    sitewide ``:focus-visible`` outline in its place. Seven copies survived in
    the page sheets, two of them on the Games Master page #701's complaint
    named -- one written into the ``:focus-visible`` rule itself, so the ring
    was removed from exactly the state it exists for.

    The rule these hold is the one #701 settled: a sheet may suppress the
    browser's ring only where it draws a focus indicator of its own, which in
    these sheets means a ``box-shadow`` ring. A border tint is not one.
    """

    def suppressions(self) -> list[tuple[str, str, str]]:
        """Every rule in every sheet that switches the outline off.

        Returns:
            Sheet name, selector and declaration block, one entry per rule.
        """
        found = []
        for name, css in _sheets().items():
            for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
                block = match.group(2)
                if not re.search(r"outline\s*:\s*(?:none|0)\b", block):
                    continue
                found.append((name, " ".join(match.group(1).split()), block))
        return found

    def test_every_suppression_draws_a_focus_indicator_of_its_own(self):
        for name, selector, block in self.suppressions():
            with self.subTest(sheet=name, selector=selector):
                self.assertIn(
                    "box-shadow",
                    block,
                    f"{name}: {selector} switches the focus ring off and puts "
                    "nothing visible in its place (#701, #737)",
                )

    def test_base_still_carries_the_sitewide_replacement(self):
        """The ring the suppressions above are measured against (#701)."""
        base = _sheets()["base.css"]
        rule = re.search(
            r"input:focus-visible[^{]*\{([^}]*)\}",
            base,
        )
        if rule is None:
            self.fail("base.css no longer carries the sitewide :focus-visible rule")

        self.assertRegex(rule.group(1), r"outline:\s*2px solid")

    def test_the_games_master_pages_choice_button_gets_its_own_ring(self):
        """A <button>, which base.css's input/select/textarea rule does not reach."""
        painted = [
            match.group(2)
            for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", _sheets()["chat.css"])
            if ".choice-btn:focus-visible" in _selectors(match.group(1) + "{")
            and "outline" in match.group(2)
        ]

        self.assertEqual(len(painted), 1, "one rule should settle the button's ring")
        self.assertRegex(painted[0], r"outline:\s*2px solid")
